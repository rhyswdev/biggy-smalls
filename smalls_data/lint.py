"""Invariants from docs/PLAN.md section 9. Entry point for `make lint`.

Runs against every bundle under bundles/ that has a scenario file. Each check appends errors (fail the
build) or notes (reported). Invariant 6 (answer key) and 10 (leak test through the tools) arrive with
steps 7 and 6; they are reported as not yet checkable."""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from smalls.contracts.manifest import ManifestRow
from smalls_data import lint_schema as ls
from smalls_data import prompt_lint
from smalls_data import shape
from smalls_data.parity import Parity
from smalls_data.gen.core import BUNDLES, SCENARIO, Clock, Curve

UTC = ZoneInfo("UTC")


def t_of(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class Lint:
    def __init__(self, scenario_file: Path, bundle_root: Path = BUNDLES):
        self.sc = yaml.safe_load(scenario_file.read_text())
        self.facts = yaml.safe_load((SCENARIO / "facts.yaml").read_text())
        self.c = Clock(self.sc["meta"]["date"])
        self.b = bundle_root / self.sc["meta"]["bundle"]
        self.rows = [ManifestRow.from_json(x) for x in (self.b / "_internal/manifest.jsonl").read_text().splitlines()]
        self.errors: list[str] = []
        self.notes: list[str] = []
        self.warnings: list[str] = []
        self._files: dict[str, str] = {}
        self._json: dict[str, object] = {}

    def err(self, inv: int, msg: str) -> None:
        self.errors.append(f"[{inv}] {msg}")

    # -- served content --------------------------------------------------------------------------

    def served(self) -> list[Path]:
        return sorted(p for p in self.b.rglob("*") if p.is_file() and "_internal" not in p.parts)

    def text(self, rel: str) -> str:
        if rel not in self._files:
            self._files[rel] = (self.b / rel).read_text()
        return self._files[rel]

    def js(self, rel: str):
        if rel not in self._json:
            self._json[rel] = json.loads(self.text(rel))
        return self._json[rel]

    def record_text(self, row: ManifestRow) -> str:
        if isinstance(row.locator, int):
            lines = self.text(row.path).split("\n")
            starts = self.starts(row.path)
            i = starts.index(row.locator) if row.locator in starts else None
            end = starts[i + 1] if i is not None and i + 1 < len(starts) else row.locator
            if row.path.endswith(".md"):
                return self.text(row.path)
            return "\n".join(lines[row.locator - 1:end - 1] if end > row.locator else [lines[row.locator - 1]])
        if row.locator == "/":
            return self.text(row.path)
        node = self.js(row.path)
        for part in row.locator.strip("/").split("/"):
            node = node[int(part)] if isinstance(node, list) else node[part]
        return json.dumps(node, ensure_ascii=False)

    def starts(self, path: str) -> list[int]:
        key = "__starts__" + path
        if key not in self._json:
            self._json[key] = sorted({r.locator for r in self.rows if r.path == path and isinstance(r.locator, int)})
        return self._json[key]

    def visible(self, row: ManifestRow, now: datetime) -> bool:
        if t_of(row.t_known) >= now:
            return False
        if row.valid_from and t_of(row.valid_from) > now:
            return False
        if row.valid_to and now >= t_of(row.valid_to):
            return False
        return True

    # -- invariants ------------------------------------------------------------------------------

    def inv1_connections(self) -> None:
        conn = self.sc["model"]["connections"]
        usable = conn["usable"]
        expect = {"14:29": 45, "14:30": 121, "14:31": 161, "14:32": 186, "14:33": 197}
        for r in conn["rows"]:
            total = sum(r["gateway"]) + r["order"] + r["recon"] + r["other"]
            if total > usable:
                self.err(1, f"connections at {r['at']} total {total} > {usable}")
            if str(r["at"]) in expect and total != expect[str(r["at"])]:
                self.err(1, f"connections at {r['at']} total {total}, PLAN 1.3 says {expect[str(r['at'])]}")
            if any(g > self.facts["parameters"]["payments_gateway"]["hikari"]["maximumPoolSize"] for g in r["gateway"]):
                self.err(1, f"a gateway pool above its maximum at {r['at']}")
        # The Datadog series and the housekeeper lines agree with the budget.
        p = self.b / "datadog/metrics/postgresql.connections.by_db.json"
        if p.exists():
            d = json.loads(p.read_text())
            per_t = defaultdict(float)
            for s in d["series"]:
                for ts, v in s["pointlist"]:
                    if v is not None:
                        per_t[ts] += v
            rows = [(self.c.t(r["at"]), r) for r in conn["rows"]]
            for ts, v in per_t.items():
                t = datetime.fromtimestamp(ts / 1000, UTC)
                row = [r for at, r in rows if at <= t][-1]
                if v != sum(row["gateway"]) + row["order"] + row["recon"] + row["other"]:
                    self.err(1, f"postgresql.connections at {t:%H:%M} sums to {v}, budget says otherwise")
        stats = re.compile(r"^(\S+ \S+) DEBUG .*After cleanup  stats \(total=(\d+), active=(\d+), idle=(\d+), waiting=(\d+)\)")
        for f in sorted((self.b / "splunk/raw/payments_app").glob("*.log")):
            for line in f.read_text().split("\n"):
                m = stats.match(line)
                if m and int(m.group(2)) > 35:
                    self.err(1, f"{f.name}: pool total above 35: {line[:80]}")
                if m and int(m.group(3)) + int(m.group(4)) != int(m.group(2)):
                    self.err(1, f"{f.name}: active+idle != total: {line[:100]}")

    def inv2_little(self) -> None:
        m = self.sc["model"]
        rate = Curve(m["authorization_rate_per_s"], self.c)
        slow = Curve(m["slow_share"], self.c)
        rows = [(self.c.t(r["at"]), r) for r in m["connections"]["rows"]]
        for hhmm in m["little_law_checks"]:
            t = self.c.t(hhmm)
            demand = rate(t) * (1.05 + 36 * slow(t))
            row = [r for at, r in rows if at <= t][-1]
            have = sum(row["gateway"])
            capped = any(g >= 35 for g in row["gateway"]) or (have + row["order"] + row["recon"] + row["other"]) >= m["connections"]["usable"]
            if abs(have - demand) / demand > 0.2 and not (capped and have <= demand):
                self.err(2, f"Little's law at {hhmm}: demand {demand:.0f} vs gateway connections {have}")

    def inv3_sequences(self) -> None:
        def check(name, items):
            items = sorted(items)
            nums = [n for _, n in items]
            if nums != sorted(nums):
                self.err(3, f"{name} numbers out of time order: {nums}")
        probs = {json.loads(p.read_text())["displayId"]: json.loads(p.read_text())["startTime"]
                 for p in (self.b / "dynatrace/problems").glob("*.json")}
        if probs:
            check("Dynatrace display ids", [(t, d) for d, t in probs.items()])
        zone = ZoneInfo(self.facts["zones"]["servicenow"]["zone"])
        for table in ("incident", "change_request"):
            p = self.b / f"servicenow/{table}.json"
            if p.exists():
                check(f"ServiceNow {table}", [(datetime.strptime(x["sys_created_on"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=zone), x["number"])
                                               for x in json.loads(p.read_text())["result"]])
        jobs = defaultdict(list)
        for p in (self.b / "jenkins/jobs").glob("*/builds/*.json"):
            d = json.loads(p.read_text())
            jobs[p.parts[-3]].append((d["timestamp"], d["number"]))
        for job, items in jobs.items():
            check(f"Jenkins {job}", items)
        p = self.b / "datadog/events.jsonl"
        if p.exists():
            ev = [json.loads(x)["event"] for x in p.read_text().splitlines()]
            check("Datadog event ids", [(e["date_happened"], e["id"]) for e in ev])

    def inv4_splunk_raw(self) -> None:
        integ = yaml.safe_load((self.b / "integration.yaml").read_text())["sources"]
        for p in sorted((self.b / "splunk/alerts").glob("*.json")):
            body = json.loads(p.read_text())
            raw, host, st = body["result"]["_raw"], body["result"]["host"], body["result"]["sourcetype"]
            folders = [f for f, c in integ.items() if c.get("sourcetype") == st]
            files = [f for folder in folders for f in (self.b / folder).glob("*")
                     if integ[folder]["host"] != "file_stem" and integ[folder]["host"] == host or f.stem == host]
            if not any(raw in f.read_text().split("\n") for f in files):
                self.err(4, f"{p.name}: _raw is not a line of {host}'s {st} log")

    def checkpoints(self) -> list[tuple[str, datetime]]:
        return [(c["id"], self.c.t(c["at"])) for c in self.sc["checkpoints"]]

    def inv5_visibility(self) -> None:
        ids = {}
        def walk(node):
            if isinstance(node, dict):
                if "id" in node and node.get("sig_id"):
                    ids[str(node["id"])] = node["sig_id"]
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
        walk({k: v for k, v in self.sc.items() if k != "timeline"})
        for ch in yaml.safe_load((SCENARIO / "content/slack/fixed.yaml").read_text())["channels"].values():
            for m in ch:
                if m.get("sig_id"):
                    ids[m["id"]] = m["sig_id"]
        by_sig = defaultdict(list)
        for r in self.rows:
            if r.sig_id:
                by_sig[r.sig_id].append(r)
        cps = self.checkpoints()
        for row in self.sc.get("timeline", []):
            vis = str(row.get("visible", ""))
            if vis not in {c for c, _ in cps}:
                continue
            for ref in row.get("refs", []):
                sig = ids.get(str(ref))
                if not sig or sig not in by_sig:
                    continue
                first = min(t_of(r.t_known) for r in by_sig[sig])
                got = next((c for c, at in cps if first < at), "after")
                if got != vis:
                    self.err(5, f"{sig} first visible at {got}, timeline says {vis} ({row['row']})")
        for cid, at in cps:
            for r in self.rows:
                if r.kind == "signal" and r.sig_id and at - timedelta(seconds=60) < t_of(r.t_known) < at:
                    self.err(5, f"{r.sig_id} lands within 60 s before checkpoint {cid}")

    def inv6_answer_key(self) -> None:
        """Every sig_id and class a checkpoint's facts use resolves to a manifest row visible at it; entities
        are facts.yaml keys; change ids are served records."""
        p = self.b / "_internal/answer_key.yaml"
        if not p.exists():
            self.err(6, "_internal/answer_key.yaml missing")
            return
        key = yaml.safe_load(p.read_text())
        cps = dict(self.checkpoints())
        by_cp = {cid: [r for r in self.rows if self.visible(r, at)] for cid, at in cps.items()}
        for cp, spec in key["checkpoints"].items():
            cid = cp.replace(":", "")
            vis = by_cp[cid]
            sigs = {r.sig_id for r in vis if r.sig_id}
            classes = {r.class_ for r in vis}
            for f in spec.get("required_facts", []) + spec.get("supporting_facts", []):
                for ref in key["facts"][f]["any_of"]:
                    ok = ref[6:] in classes if ref.startswith("class:") else ref in sigs
                    if not ok:
                        self.err(6, f"{cp} {f}: {ref} has no manifest row visible at {cp}")
            ents = set(self.facts["entities"])
            named = [spec.get("leading", {}).get("entity"), (spec.get("trigger") or {}).get("entity")] + spec.get("leading_any", []) \
                + spec.get("contributing_any", []) + [t["match"]["entity"] for t in spec.get("traps", {}).values()]
            for e in filter(None, named):
                if e not in ents:
                    self.err(6, f"{cp}: entity {e!r} is not a facts.yaml entity")
            handles = {r.handle for r in vis}
            for ch in spec.get("leading", {}).get("change_any", []):
                if ch.startswith("CHG") and f"snow:{ch}" not in handles:
                    self.err(6, f"{cp}: change {ch} is not visible")
                if "#" in ch and f"jk:{ch}" not in handles:
                    self.err(6, f"{cp}: build {ch} is not visible")

    def inv7_tokens(self) -> None:
        for p in self.served():
            rel = p.relative_to(self.b).as_posix()
            txt = p.read_text(errors="replace")
            if re.search(r"\bsig\.[a-z]", txt) or "sig_id" in txt:
                self.err(7, f"{rel} contains a sig_id")
            if re.search(r"\btrap\b", txt, re.I):
                self.err(7, f"{rel} contains the string 'trap'")
            if re.search(r'"kind":\s*"(signal|noise)"', txt):
                self.err(7, f"{rel} contains a scenario kind")
        day0 = self.c.t("00:00")
        # Authoring labels: a record written before the incident day cannot call itself the trap.
        label = re.compile(r"\b(stale|decoy|red herring|misleading)\b", re.I)
        for r in self.rows:
            if t_of(r.t_utc) < day0:
                m = label.search(self.record_text(r))
                if m:
                    self.err(7, f"{r.handle} ({r.path}), dated before the incident day, contains {m.group(0)!r}")
        for cid, at in self.checkpoints():
            toks = self.sc["future_tokens"].get(cid, [])
            for r in self.rows:
                # Records written before the bundle day (knowledge pages, closed incidents) cannot hold a
                # token from the day's future; the rule targets the day's evidence (DATA.md).
                if not self.visible(r, at) or t_of(r.t_utc) < day0:
                    continue
                txt = self.record_text(r)
                for tok in toks:
                    if tok in txt:
                        self.err(7, f"checkpoint {cid}: visible record {r.handle} ({r.path}) contains future token {tok!r}")

    # Where each source's time field sits in its first served record (for checking `format`).
    def time_samples(self) -> dict[str, list[str]]:
        b, out = self.b, {}
        def lines(folder, n):
            return [f.read_text().split("\n", 1)[0][:n] for f in sorted((b / folder).glob("*")) if f.is_file()]
        out["splunk/raw/payments_app"] = lines("splunk/raw/payments_app", 23)
        out["splunk/raw/postgres"] = lines("splunk/raw/postgres", 27)
        out["splunk/raw/checkout_api"] = [json.loads(x)["@timestamp"] for x in lines("splunk/raw/checkout_api", 10 ** 6)]
        out["splunk/raw/k8s_events"] = [json.loads(x)["metadata"]["creationTimestamp"] for x in lines("splunk/raw/k8s_events", 10 ** 6)]
        for t in ("incident", "change_request", "sys_journal_field"):
            p = b / f"servicenow/{t}.json"
            if p.exists():
                out.setdefault("servicenow", []).extend(x["sys_created_on"] for x in json.loads(p.read_text())["result"])
        out["paycore"] = [json.loads(p.read_text())["page"]["updated_at"] for p in (b / "paycore").glob("*.json")]
        if (b / "splunk/raw/nginx").exists():
            out["splunk/raw/nginx"] = [re.search(r"\[[^\]]+\]", x).group(0) for x in lines("splunk/raw/nginx", 10 ** 6)]
        if (b / "splunk/raw/f5").exists():
            out["splunk/raw/f5"] = [x[:15] for x in lines("splunk/raw/f5", 15)]
        if (b / "pagerduty/log_entries.json").exists():
            out["pagerduty"] = [x["created_at"] for x in json.loads((b / "pagerduty/log_entries.json").read_text())["log_entries"]]
        p = b / "splunk/saved_searches.json"
        if p.exists():
            out["splunk/saved_searches.json"] = [x["updated"] for x in json.loads(p.read_text())]
        return out

    def inv8_clocks(self) -> None:
        integ = yaml.safe_load((self.b / "integration.yaml").read_text())["sources"]
        # Every served `format` is a strptime pattern that parses its source's time field (engineer M5).
        samples = self.time_samples()
        for src, cfg in integ.items():
            if "format" not in cfg:
                continue
            if src not in samples:
                self.notes.append(f"[8] {src}: no time sample to check its format against")
                continue
            for x in samples[src]:
                try:
                    datetime.strptime(x, cfg["format"])
                except ValueError:
                    self.err(8, f"{src}: {x!r} does not parse with served format {cfg['format']!r}")
                    break
        zone = ZoneInfo(integ["servicenow"]["zone"])
        p = self.b / "servicenow/incident.json"
        hist = {h["number"]: h for h in self.sc["history"]}
        if p.exists():
            for x in json.loads(p.read_text())["result"]:
                h = hist.get(x["number"])
                if h:
                    got = datetime.strptime(x["opened_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=zone)
                    if got != self.c.t(h["opened"]):
                        self.err(8, f"{x['number']} opened_at re-derives to {got.astimezone(UTC)}, YAML {h['opened']}")
        p = self.b / "servicenow/change_request.json"
        chg = {c["number"]: c for c in self.sc["changes"]}
        if p.exists():
            for x in json.loads(p.read_text())["result"]:
                c = chg[x["number"]]
                if c["actual"][0]:
                    got = datetime.strptime(x["work_start"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=zone)
                    if got != self.c.t(c["actual"][0]):
                        self.err(8, f"{x['number']} work_start re-derives to {got.astimezone(UTC)}, YAML {c['actual'][0]}")
        # Every manifest time re-derives from the served line for zone-less logs.
        pat = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),(\d{3}) ")
        for r in self.rows:
            if r.path.startswith("splunk/raw/payments_app/"):
                m = pat.match(self.record_text(r))
                tz = ZoneInfo(integ["splunk/raw/payments_app"]["zone"])
                got = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz) + timedelta(milliseconds=int(m.group(2)))
                if got != t_of(r.t_utc):
                    self.err(8, f"{r.handle}: line time {got} != manifest {r.t_utc}")
                    break

    def inv9_aliases(self) -> None:
        present = {p.name for p in self.b.iterdir() if p.is_dir() and p.name != "_internal"} | {"triggers"}
        texts = defaultdict(str)
        for p in self.served():
            texts[p.relative_to(self.b).parts[0]] += p.read_text(errors="replace") + "\n"
        missing = []
        for ent, e in self.facts["entities"].items():
            for src, aliases in e["aliases"].items():
                if src not in present:
                    continue
                for a in aliases:
                    if a not in texts[src]:
                        missing.append(f"{ent}/{src}: {a!r}")
        if missing:
            self.notes.append(f"[9] {len(missing)} aliases not yet used in their source (fill in later steps): " + "; ".join(missing))

    # Epochs stand alone: a digit run inside a hex id or a compound id (`_at_<epoch>_`) is not read as one.
    EPOCH_S = re.compile(r"(?<![\w.])(1[6-9]\d{8})(?:\.\d+)?(?![\w])")
    EPOCH_MS = re.compile(r"(?<![\w.])(1[6-9]\d{11})(?![\w])")
    # Slack thread summaries hold their 15:30 export values; read_chat recomputes them at now (PLAN 3.4).
    SLACK_THREAD_SUMMARY = ("latest_reply", "reply_count", "reply_users", "reply_users_count")
    # Scheduled times, known in advance: an on-call shift's end.
    SCHEDULED = {"pagerduty/oncalls.json": ("end",)}
    ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})")

    def times_in(self, txt: str) -> list[datetime]:
        out = [datetime.fromtimestamp(int(m.group(1)), UTC) for m in self.EPOCH_S.finditer(txt)]
        out += [datetime.fromtimestamp(int(m.group(1)) / 1000, UTC) for m in self.EPOCH_MS.finditer(txt)]
        out += [datetime.fromisoformat(m.group(0).replace("Z", "+00:00")) for m in self.ISO.finditer(txt)]
        return out

    def inv10_served(self) -> None:
        """The part of the leak test a lint can check on served files (data fix 2): every file the loader
        reads sits under a listed source; at each checkpoint, no record version visible there carries an epoch
        or ISO time after the checkpoint. The tool-output half arrives with the tool layer (step 6a)."""
        integ = yaml.safe_load((self.b / "integration.yaml").read_text())
        srcs = list(integ["sources"])
        def listed(rel: str) -> bool:
            return any(rel == s or rel.startswith(s + "/") for s in srcs)
        by_path = defaultdict(list)
        for r in self.rows:
            by_path[r.path].append(r)
        for p in self.served():
            rel = p.relative_to(self.b).as_posix()
            if rel == "integration.yaml" or rel.startswith("triggers/"):
                continue          # configuration; harness input, never a source
            if not listed(rel):
                self.err(10, f"{rel} is in the bundle but under no integration.yaml source")
            elif rel not in by_path:
                # Reference data with no record rows (Slack users and channels) is visible at every checkpoint.
                first = self.checkpoints()[0][1]
                late = [t for t in self.times_in(self.text(rel)) if t >= first]
                if late:
                    self.err(10, f"{rel} (no manifest rows) carries {max(late):%H:%M:%S} UTC")
                for tok in self.sc["future_tokens"].get(self.checkpoints()[0][0], []):
                    if tok in self.text(rel):
                        self.err(10, f"{rel} (no manifest rows) contains future token {tok!r}")
        for cid, at in self.checkpoints():
            for r in self.rows:
                if not self.visible(r, at):
                    continue
                txt = self.record_text(r)
                if r.path.startswith("slack/"):
                    d = json.loads(txt)
                    txt = json.dumps({k: v for k, v in d.items() if k not in self.SLACK_THREAD_SUMMARY})
                elif r.path in self.SCHEDULED:
                    d = json.loads(txt)
                    txt = json.dumps({k: v for k, v in d.items() if k not in self.SCHEDULED[r.path]})
                late = [t for t in self.times_in(txt) if t >= at]
                if late:
                    self.err(10, f"checkpoint {cid}: visible {r.handle} ({r.path} {r.locator}) carries {max(late):%H:%M:%S} UTC")

    def inv11_schema(self) -> None:
        rep = ls.Report()
        b = self.b
        def jf(rel):
            p = b / rel
            return json.loads(p.read_text()) if p.exists() else None
        for i, m in enumerate(jf("datadog/monitors.json") or []):
            rep.extend(ls.validate_json("datadog", "Monitor", m, f"monitors[{i}]"))
        p = b / "datadog/events.jsonl"
        if p.exists():
            for i, line in enumerate(p.read_text().splitlines()):
                rep.extend(ls.validate_json("datadog", "Event", json.loads(line)["event"], f"events:{i + 1}"))
        for p in sorted((b / "datadog/metrics").glob("*.json")):
            rep.extend(ls.validate_json("datadog", "MetricsQueryResponse", json.loads(p.read_text()), p.name))
        for i, s in enumerate(jf("datadog/synthetics.json") or []):
            rep.extend(ls.validate_json("datadog", "SyntheticsTestDetails", s, f"synthetics[{i}]"))
        for p in sorted((b / "dynatrace/problems").glob("*.json")):
            rep.extend(ls.dynatrace_problem(json.loads(p.read_text()), p.name))
        for table in ("incident", "change_request", "sys_journal_field", "sys_audit", "cmdb_ci", "cmdb_rel_ci", "sys_user", "sys_user_group"):
            for i, rec in enumerate((jf(f"servicenow/{table}.json") or {}).get("result", [])):
                rep.extend(ls.servicenow_record(table, rec, f"{table}[{i}]"))
        for p in sorted((b / "servicenow/major_incident").glob("*.json")):
            rep.extend(ls.servicenow_record("incident", json.loads(p.read_text())["result"], p.name))
        for p in sorted((b / "jenkins/jobs").glob("*/builds/*.json")):
            rep.extend(ls.jenkins_build(json.loads(p.read_text()), p.relative_to(b).as_posix()))
        for p in sorted((b / "slack").glob("*/*.json")):
            for i, m in enumerate(json.loads(p.read_text())):
                rep.extend(ls.validate_json("slack", "objs_message", m, f"{p.relative_to(b)}[{i}]"))
        p = b / "splunk/raw/k8s_events/events.jsonl"
        if p.exists():
            for i, line in enumerate(p.read_text().splitlines()):
                rep.extend(ls.validate_json("kubernetes", "Event", json.loads(line), f"k8s:{i + 1}"))
        for p in sorted((b / "splunk/raw/checkout_api").glob("*.jsonl")):
            for i, line in enumerate(p.read_text().splitlines()):
                rep.extend(ls.checkout_log(json.loads(line), f"{p.name}:{i + 1}"))
        for p in sorted((b / "splunk/alerts").glob("*.json")):
            rep.extend(ls.splunk_webhook(json.loads(p.read_text()), p.name))
        for i, s in enumerate(jf("splunk/saved_searches.json") or []):
            rep.extend(ls.splunk_saved_search(s, f"saved_searches[{i}]"))
        for i, a in enumerate((jf("solarwinds/alerts.json") or {}).get("results", [])):
            rep.extend(ls.solarwinds_alert(a, f"solarwinds[{i}]"))
        for p in sorted((b / "pagerduty/incidents").glob("*.json")):
            rep.extend(ls.validate_json("pagerduty", "Incident", json.loads(p.read_text())["incident"], p.name))
        # Log entries are checked against their subtype (AssignLogEntry carries `assignees`, NotifyLogEntry `user`).
        sub = {"assign_log_entry": "AssignLogEntry", "escalate_log_entry": "EscalateLogEntry", "notify_log_entry": "NotifyLogEntry"}
        for i, x in enumerate((jf("pagerduty/log_entries.json") or {}).get("log_entries", [])):
            rep.extend(ls.validate_json("pagerduty", sub.get(x["type"], "LogEntry"), x, f"log_entries[{i}]"))
        for name, key, schema in (("services", "services", "Service"),
                                  ("escalation_policies", "escalation_policies", "EscalationPolicy"), ("oncalls", "oncalls", "Oncall")):
            for i, x in enumerate((jf(f"pagerduty/{name}.json") or {}).get(key, [])):
                rep.extend(ls.validate_json("pagerduty", schema, x, f"{name}[{i}]"))
        for p in sorted((b / "paycore").glob("*.json")):
            rep.extend(ls.statuspage_summary(json.loads(p.read_text()), p.name))
        for e in rep.errors:
            self.err(11, e)
        if rep.warnings:
            self.notes.append(f"[11] {len(rep.warnings)} deliberate deviations kept (e.g. {rep.warnings[0]})")
        self.notes.append(f"[11] schema-checked {rep.checked} records")

    def inv12_strings(self) -> None:
        pinned = ls.exact_strings()
        corpus = "\n".join(p.read_text() for p in (self.b / "splunk/raw").rglob("*.log"))
        for key, s in pinned.items():
            if key == "hikari.before_cleanup":
                continue
            if s not in corpus:
                self.err(12, f"pinned string {key!r} not rendered verbatim: {s!r}")
        f5 = self.b / "splunk/raw/f5/ltm.log"
        if f5.exists():
            text = f5.read_text()
            for key, tpl in ls._fields("f5", "ltm")["strings"].items():
                if key.startswith("member_") and tpl.split("{")[0] not in text:
                    self.err(12, f"F5 {key} not rendered: {tpl.split('{')[0]!r}")
            if not all(re.search(r"monitor status (down|up)\. \[ ", ln) for ln in text.splitlines()):
                self.err(12, "an F5 line does not follow the pinned member status template")
        if "After cleanup stats" in corpus:
            self.err(12, "housekeeper line with one space between 'cleanup' and 'stats'")

    def inv13_budget(self) -> None:
        bud = self.sc["budget"]
        counts = json.loads((self.b / "_internal/counts.json").read_text())
        logs = sum(v for k, v in counts.items() if k.startswith("log_lines:"))
        recs = sum(v for k, v in counts.items() if k.startswith("records:"))
        sources = len([p for p in self.b.iterdir() if p.is_dir() and p.name not in ("_internal", "triggers")])
        if logs > bud["ceilings"]["log_lines"]:
            self.err(13, f"{logs} log lines > {bud['ceilings']['log_lines']}")
        if recs > bud["ceilings"]["structured_records"]:
            self.err(13, f"{recs} structured records > {bud['ceilings']['structured_records']}")
        if sources > bud["ceilings"]["vendor_sources"]:
            self.err(13, f"{sources} vendor sources > {bud['ceilings']['vendor_sources']}")
        for src, target in bud["log_line_targets"].items():
            n = counts.get(f"log_lines:{src}")
            if n is None:
                self.notes.append(f"[13] {src}: not in this bundle (target {target})")
            elif abs(n - target) / target > 0.2:
                self.err(13, f"{src}: {n} lines, more than 20% from target {target}")
        self.notes.append(f"[13] {logs} log lines / {bud['ceilings']['log_lines']}, {recs} structured records / "
                          f"{bud['ceilings']['structured_records']} (full-bundle target {bud['structured_record_target']}), "
                          f"{sources} vendor sources / {bud['ceilings']['vendor_sources']}")
        inventory = {n["class"] for n in self.sc["noise"]}
        unnamed = sorted({r.class_ for r in self.rows if r.kind == "noise"} - inventory)
        if unnamed:
            self.err(13, f"noise classes missing from the scenario noise inventory: {unnamed}")

    def inv14_parity(self) -> None:
        """Every manifest handle and t_known re-derives from served bytes (smalls_data/parity.py)."""
        problems = Parity(self.b).run()
        for p in problems[:20]:
            self.err(14, p)
        if len(problems) > 20:
            self.err(14, f"... {len(problems) - 20} more parity problems")

    def inv15_sampling(self) -> None:
        """Sampling keeps the shape: the served `sampling:` block is the scenario's, every served log record
        matches the class that claims it, rates match the population, and the shape holds (smalls_data/shape.py)."""
        scen = self.sc["sampling"]
        served = yaml.safe_load((self.b / "integration.yaml").read_text()).get("sampling", {})
        gen_of: dict[str, tuple[str, dict]] = {}
        for folder, spec in scen.items():
            for c in spec["classes"]:
                for g in c["gen"]:
                    gen_of[g] = (folder, c)
            if (self.b / folder).exists():
                want = {k: v for k, v in spec.items() if k != "classes"}
                want["classes"] = [{k: v for k, v in c.items() if k != "gen"} for c in spec["classes"]]
                if served.get(folder) != want:
                    self.err(15, f"integration.yaml sampling for {folder} differs from the scenario")
        # Every served raw-log record's first line falls in the class that claims its generator class.
        pats = {f: [(c["name"], re.compile(c["match"])) for c in spec["classes"]] for f, spec in served.items()}
        bad = 0
        for r in self.rows:
            folder = "/".join(r.path.split("/")[:3])
            if folder not in pats or not isinstance(r.locator, int):
                continue
            first = self.text(r.path).split("\n")[r.locator - 1]
            if folder.endswith("postgres") and "LOG:  connection received: " in first:
                continue          # belongs to the session of the line after it
            got = next((n for n, rx in pats[folder] if rx.search(first)), None)
            want = gen_of.get(r.class_, (None, {}))[1].get("name")
            if got != want:
                bad += 1
                if bad <= 5:
                    self.err(15, f"{r.handle} ({r.class_}) matches sampling class {got}, expected {want}: {first[:90]}")
        # Rates and shape against the population the estate wrote.
        popf = self.b / "_internal/population.json"
        if not popf.exists():
            self.err(15, "_internal/population.json missing; run make data")
            return
        pop = json.loads(popf.read_text())
        kept: dict[str, Counter] = defaultdict(Counter)
        for r in self.rows:
            if r.class_ in gen_of and r.path.startswith("splunk/raw/"):
                kept[r.class_][r.t_utc[11:16]] += 1
        t0 = self.c.t("14:20")
        window = [(t0 + timedelta(minutes=i)).strftime("%H:%M") for i in range(50)]
        report = []
        for cls, (folder, spec) in sorted(gen_of.items()):
            if cls not in pop:
                continue
            n, k = int(spec["keep_1_in"]), pop[cls]["per_unit"]
            pm, km = pop[cls]["by_minute"], kept[cls]
            if n == 1:
                if round(sum(pm.values())) != sum(km.values()):
                    self.err(15, f"{cls}: kept {sum(km.values())} of {sum(pm.values()):.0f} at a rate of all")
                continue
            if not shape.within_band(sum(km.values()), sum(pm.values()), n, k):
                self.err(15, f"{cls}: kept {sum(km.values())} of {sum(pm.values()):.0f}, not 1 in {n}")
            tens = defaultdict(lambda: [0.0, 0.0])
            for m, v in pm.items():
                tens[m[:-1]][0] += v
            for m, v in km.items():
                tens[m[:-1]][1] += v
            for b, (p, q) in sorted(tens.items()):
                if not shape.within_band(q, p, n, k):
                    self.err(15, f"{cls}: {b}0 to {b}9 kept {q:.0f} of {p:.0f}, not 1 in {n}: the rate changes in the window")
            P = [pm.get(m, 0) for m in window]
            if len(set(P)) < 2:
                continue
            w = shape.shape_width(P, n, k)
            if w is None:
                report.append(f"{cls} 1/{n}: too sparse for a shape test")
                continue
            rho = shape.spearman(shape.buckets([km.get(m, 0) for m in window], w), shape.buckets(P, w))
            report.append(f"{cls} 1/{n}: rho {rho:.2f} at {w} min")
            if rho is None or rho <= shape.THRESHOLD:
                self.err(15, f"{cls}: Spearman {rho} at {w}-minute buckets, 14:20 to 15:10, not above {shape.THRESHOLD}")
        self.notes.append("[15] " + "; ".join(report))

    def inv16_prompt_text(self) -> None:
        """No bundle string in prompt text on the agent side (the tag profiles; the agent's prompts later)."""
        strings = prompt_lint.bundle_strings(self.facts)
        texts = prompt_lint.prompt_texts()
        for hit in prompt_lint.find(texts, strings):
            self.err(16, hit)
        self.notes.append(f"[16] {len(texts)} prompt texts checked against {len(strings)} bundle strings")

    def inv17_ui_text(self) -> None:
        """No bundle string in the UI's code: the API and the web app (docs/decisions/ui-build.md section 7.6)."""
        from smalls_data import ui_lint

        strings = prompt_lint.bundle_strings(self.facts)
        texts = ui_lint.ui_texts()
        for hit in ui_lint.find(texts, strings):
            self.err(17, hit)
        self.notes.append(f"[17] {len(texts)} UI source files checked against {len(strings)} bundle strings")

    def run(self) -> None:
        for f in (self.inv1_connections, self.inv2_little, self.inv3_sequences, self.inv4_splunk_raw, self.inv5_visibility, self.inv6_answer_key,
                  self.inv7_tokens, self.inv8_clocks, self.inv10_served, self.inv9_aliases, self.inv11_schema, self.inv12_strings, self.inv13_budget,
                  self.inv14_parity, self.inv15_sampling, self.inv16_prompt_text, self.inv17_ui_text):
            f()
        self.notes.append("[10] served files checked; the store half is `make leak-test`; the tool-output half arrives with the tools (step 6a-tools)")


def main() -> int:
    bundles = [s for s in SCENARIO.glob("*.yaml") if s.name != "facts.yaml"]
    failed = False
    for s in bundles:
        L = Lint(s)
        if not L.b.exists():
            print(f"lint: {L.b.name} not built; run make data")
            failed = True
            continue
        L.run()
        print(f"lint: {L.b.name}: {len(L.errors)} errors")
        for n in L.notes:
            print("  note", n)
        for e in L.errors[:80]:
            print("  FAIL", e)
        if len(L.errors) > 80:
            print(f"  ... {len(L.errors) - 80} more")
        failed |= bool(L.errors)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
