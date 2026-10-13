"""Lint invariant 14: every manifest handle and `t_known` re-derives from the served bytes.

Ported from the step 6 panel's prototype. It reads only what a tool may read (the files under
integration.yaml `sources:` and integration.yaml itself) and mints handles only through
smalls.contracts.handles, never through the generator. Each source has one rule for `t_known`:

    raw logs              the record's first-line time, as matched by break_only_before (nginx: the
                          bracketed time; F5 syslog: plus integration.yaml syslog_year; k8s events:
                          lastTimestamp, the version served)
    splunk/alerts         the receipt time in the file name
    saved searches        `updated`
    datadog               events `date_happened`; monitors `created`; metric points bucket start + interval
    dynatrace             open version `startTime`, closed version `endTime`; entities `firstSeenTms`
    servicenow            `sys_created_on` in the integration user's zone; live incident versions `sys_updated_on`
    pagerduty             services `created_at`; on-call `start`; log entries `created_at`; incident versions
                          `updated_at`; escalation policies carry no time
    jenkins               building version `timestamp`; finished version `timestamp + duration`
    slack                 `ts`
    confluence            the "Last updated" date, at 12:00 UTC
    paycore               `page.updated_at`
    solarwinds            `TriggeredDateTime` (server zone, seven-digit fraction cut to six)

Records with no time in their bytes (Datadog synthetics) must be known before the earliest log line.
The manifest is used only to compare.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from smalls.contracts import handles as H

UTC = timezone.utc


def z(dt: datetime) -> str:
    dt = dt.astimezone(UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def ms(v: int) -> str:
    return z(datetime.fromtimestamp(v / 1000, UTC))


class Parity:
    def __init__(self, bundle: Path):
        self.b = bundle
        self.integ = yaml.safe_load((bundle / "integration.yaml").read_text())
        self.src = self.integ["sources"]
        self.served: dict[tuple[str, str], tuple[str, str | None]] = {}   # (path, locator) -> (handle, t_known)

    def put(self, rel: str, loc, handle: str, t_known: str | None) -> None:
        self.served[(rel, str(loc))] = (handle, t_known)

    def zone(self, src: str) -> ZoneInfo:
        return ZoneInfo(self.src[src]["zone"])

    def parse(self, src: str, s: str) -> str:
        return z(datetime.strptime(s, self.src[src]["format"]).replace(tzinfo=self.zone(src)) if "%z" not in self.src[src]["format"]
                 else datetime.strptime(s, self.src[src]["format"]))

    def files(self, folder: str, pattern: str = "*"):
        for f in sorted((self.b / folder).rglob(pattern)):
            if f.is_file():
                yield f.relative_to(self.b).as_posix(), f

    # -- per-source rules ------------------------------------------------------------------------------

    def logs(self) -> None:
        for folder, cfg in self.src.items():
            if not folder.startswith("splunk/raw/"):
                continue
            brk = re.compile(cfg["break_only_before"])
            for rel, f in self.files(folder):
                lines = f.read_text().split("\n")
                if lines and lines[-1] == "":
                    lines.pop()
                recs: list[list] = []
                for i, ln in enumerate(lines, 1):
                    if brk.search(ln) or not recs:
                        recs.append([i, [ln]])
                    else:
                        recs[-1][1].append(ln)
                seen: dict[str, int] = {}
                for i, ls in recs:
                    text = "\n".join(ls)
                    seen[text] = seen.get(text, 0) + 1
                    if folder.endswith("k8s_events"):
                        self.put(rel, i, H.k8s_handle(rel, text), self.parse(folder, json.loads(text)["lastTimestamp"]))
                        continue
                    if folder.endswith("checkout_api"):
                        t = self.parse(folder, json.loads(text)["@timestamp"])
                    elif folder.endswith("nginx"):
                        t = self.parse(folder, re.search(r"\[[^\]]+\]", ls[0]).group(0))
                    elif "%Y" not in cfg["format"] and "%y" not in cfg["format"]:     # BSD syslog: no year
                        stamp = brk.match(ls[0]).group(0).strip()
                        t = z(datetime.strptime(f"{self.integ['syslog_year']} {stamp}", "%Y " + cfg["format"]).replace(tzinfo=self.zone(folder)))
                    else:      # the record-break pattern matches exactly the line's timestamp
                        t = self.parse(folder, brk.match(ls[0]).group(0).strip())
                    self.put(rel, i, H.log_handle(rel, text, seen[text]), t)

    def splunk(self) -> None:
        for rel, f in self.files("splunk/alerts", "*.json"):
            received = datetime.strptime(f.name.split("-", 1)[0], "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=UTC)
            self.put(rel, "/", H.splunk_alert_handle(rel, f.read_text()), z(received))
        rel = "splunk/saved_searches.json"
        for i, s in enumerate(json.loads((self.b / rel).read_text())):
            self.put(rel, f"/{i}", H.splunk_search_handle(s["name"]), self.parse(rel, s["updated"]))

    def datadog(self) -> None:
        for i, ln in enumerate((self.b / "datadog/events.jsonl").read_text().splitlines(), 1):
            ev = json.loads(ln)["event"]
            self.put("datadog/events.jsonl", i, H.dd_event_handle(ev["id"]), z(datetime.fromtimestamp(ev["date_happened"], UTC)))
        for i, m in enumerate(json.loads((self.b / "datadog/monitors.json").read_text())):
            self.put("datadog/monitors.json", f"/{i}", H.dd_monitor_handle(m["id"]), z(datetime.fromisoformat(m["created"])))
        for i, s in enumerate(json.loads((self.b / "datadog/synthetics.json").read_text())):
            self.put("datadog/synthetics.json", f"/{i}", H.dd_synthetic_handle(s["public_id"]), None)
        for rel, f in self.files("datadog/metrics", "*.json"):
            d = json.loads(f.read_text())
            for si, s in enumerate(d["series"]):
                for pi, (t, _) in enumerate(s["pointlist"]):
                    self.put(rel, f"/series/{si}/pointlist/{pi}", H.metric_handle(d["query"]), ms(t + s["interval"] * 1000))

    def dynatrace(self) -> None:
        for rel, f in self.files("dynatrace/problems", "*.json"):
            p = json.loads(f.read_text())
            self.put(rel, "/", H.dt_problem_handle(p["displayId"]), ms(p["startTime"] if p["status"] == "OPEN" else p["endTime"]))
        for i, e in enumerate(json.loads((self.b / "dynatrace/entities.json").read_text())["entities"]):
            self.put("dynatrace/entities.json", f"/entities/{i}", H.dt_entity_handle(e["entityId"]), ms(e["firstSeenTms"]))

    def servicenow(self) -> None:
        for table, mint in (("incident", lambda r: H.snow_handle(r["number"])), ("change_request", lambda r: H.snow_handle(r["number"])),
                            ("sys_journal_field", lambda r: H.journal_handle(r["sys_id"])), ("sys_audit", lambda r: H.audit_handle(r["sys_id"])),
                            ("cmdb_ci", lambda r: H.ci_handle(r["name"])), ("cmdb_rel_ci", lambda r: H.rel_handle(r["sys_id"])),
                            ("sys_user", lambda r: H.user_handle(r["user_name"])), ("sys_user_group", lambda r: H.group_handle(r["sys_id"]))):
            p = self.b / f"servicenow/{table}.json"
            if p.exists():
                for i, r in enumerate(json.loads(p.read_text())["result"]):
                    self.put(f"servicenow/{table}.json", f"/result/{i}", mint(r), self.parse("servicenow", r["sys_created_on"]))
        # The live incident's versions: each known when it was saved.
        for rel, f in self.files("servicenow/major_incident", "*.json"):
            r = json.loads(f.read_text())["result"]
            self.put(rel, "/", H.snow_handle(r["number"]), self.parse("servicenow", r["sys_updated_on"]))

    def pagerduty(self) -> None:
        if not (self.b / "pagerduty").exists():
            return
        for i, x in enumerate(json.loads((self.b / "pagerduty/services.json").read_text())["services"]):
            self.put("pagerduty/services.json", f"/services/{i}", H.pd_service_handle(x["id"]), self.parse("pagerduty", x["created_at"]))
        for i, x in enumerate(json.loads((self.b / "pagerduty/escalation_policies.json").read_text())["escalation_policies"]):
            self.put("pagerduty/escalation_policies.json", f"/escalation_policies/{i}", H.pd_policy_handle(x["id"]), None)
        for i, x in enumerate(json.loads((self.b / "pagerduty/oncalls.json").read_text())["oncalls"]):
            self.put("pagerduty/oncalls.json", f"/oncalls/{i}",
                     H.pd_oncall_handle(x["escalation_policy"]["id"], x["escalation_level"], x["user"]["id"]), self.parse("pagerduty", x["start"]))
        for i, x in enumerate(json.loads((self.b / "pagerduty/log_entries.json").read_text())["log_entries"]):
            self.put("pagerduty/log_entries.json", f"/log_entries/{i}", H.pd_log_handle(x["id"]), self.parse("pagerduty", x["created_at"]))
        for rel, f in self.files("pagerduty/incidents", "*.json"):
            x = json.loads(f.read_text())["incident"]
            self.put(rel, "/", H.pd_incident_handle(x["incident_number"]), self.parse("pagerduty", x["updated_at"]))

    def jenkins(self) -> None:
        for rel, f in self.files("jenkins/jobs", "*.json"):
            job = f.parts[-3]
            b = json.loads(f.read_text())
            known = ms(b["timestamp"] + (0 if b["building"] else b["duration"]))
            self.put(rel, "/", H.jenkins_build_handle(job, b["number"]), known)
            for ci, it in enumerate(b["changeSet"]["items"]):
                self.put(rel, f"/changeSet/items/{ci}", H.jenkins_commit_handle(job, b["number"], it["commitId"]), known)

    def slack(self) -> None:
        for rel, f in self.files("slack", "*/*.json"):
            ch = f.parts[-2]
            for i, m in enumerate(json.loads(f.read_text())):
                self.put(rel, f"/{i}", H.slack_handle(ch, m["ts"], m["text"]), z(datetime.fromtimestamp(float(m["ts"]), UTC)))

    def confluence(self) -> None:
        for rel, f in self.files("confluence", "*.md"):
            d = re.search(r"Last updated (\d{1,2} \w{3} \d{4})", f.read_text()).group(1)
            self.put(rel, 1, H.conf_handle_for_path(rel), z(datetime.strptime(d, "%d %b %Y").replace(hour=12, tzinfo=UTC)))

    def solarwinds(self) -> None:
        p = self.b / "solarwinds/alerts.json"
        if p.exists():
            for i, a in enumerate(json.loads(p.read_text())["results"]):
                t = datetime.strptime(a["TriggeredDateTime"][:26], "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=self.zone("solarwinds"))
                self.put("solarwinds/alerts.json", f"/results/{i}", H.solarwinds_handle(a["AlertActiveID"]), z(t))

    def paycore(self) -> None:
        for rel, f in self.files("paycore", "status.v*.json"):
            v = re.search(r"v(\d+)", f.name).group(1)
            self.put(rel, "/", H.psp_handle("paycore", v), self.parse("paycore", json.loads(f.read_text())["page"]["updated_at"]))

    # -- compare ---------------------------------------------------------------------------------------

    def run(self) -> list[str]:
        for rule in (self.logs, self.splunk, self.datadog, self.dynatrace, self.servicenow, self.pagerduty, self.jenkins, self.slack,
                     self.confluence, self.paycore, self.solarwinds):
            rule()
        rows = [json.loads(x) for x in (self.b / "_internal/manifest.jsonl").read_text().splitlines()]
        problems = []
        first = min(t for (path, _), (_, t) in self.served.items() if path.startswith("splunk/raw/"))
        keys = set()
        for r in rows:
            key = (r["path"], str(r["locator"]))
            keys.add(key)
            if key not in self.served:
                problems.append(f"{r['handle']}: no served record at {key}")
                continue
            h, t = self.served[key]
            if h != r["handle"]:
                problems.append(f"{key}: handle {r['handle']} does not re-derive (served bytes give {h})")
            if t is None:
                if r["t_known"] >= first:
                    problems.append(f"{r['handle']}: no time in its bytes, so t_known must precede the earliest log line")
            elif t != r["t_known"]:
                problems.append(f"{r['handle']} ({r['path']} {r['locator']}): t_known {r['t_known']} does not re-derive (served bytes give {t})")
        for key in self.served:
            if key not in keys:
                problems.append(f"served record {key} has no manifest row")
        return problems
