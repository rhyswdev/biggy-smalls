"""The API's own reads of served data, at a chat's `now` (docs/decisions/ui-build.md sections 6.4 and 7.5): the
incident card, what a call showed, the evidence drawer and the tools' argument schemas.

They go through the same toolbox the runs use, built once per `now` and never written to: the store's
`visible` view (nothing known at or after `now`), `render_native` (no field whose served value is not yet
true) and the tools themselves. Nothing here opens `_internal/` or `scenario/`; the store's loader refuses
both. Nothing here reaches a model either.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from typing import Any

from smalls.agent.graph import iso
from smalls.agent.record import RunRecord
from smalls.agent.runner import Toolbox
from smalls.contracts.handles import handle_class
from smalls.tools import registry
from smalls.tools.registry import ToolContext
from smalls.tools.windows import iso as iso_ms

HEADER = re.compile(r"^(From|Time):\s*(.+)$")
AUTHOR = re.compile(r"^(?P<name>[^(]+?)\s*(?:\((?P<role>[^)]*)\))?$")
FIELDS = {"number": "number", "short_description": "short_description", "priority": "priority", "state": "state", "impact": "impact",
          "urgency": "urgency", "service": "business_service", "group": "assignment_group", "ci": "cmdb_ci", "assignee": "assigned_to"}
INACTIVE = ("resolved", "closed", "canceled", "cancelled")


def note_of(trigger: str) -> dict[str, Any]:
    """The commander's note: its header (`From: <name> (<role>)`, `Time: <time>`) and its text."""
    head, text, lines = {}, [], trigger.strip().splitlines()
    for n, line in enumerate(lines):
        m = HEADER.match(line.strip())
        if m and not text:
            head[m[1].lower()] = m[2].strip()
            continue
        text = lines[n:]
        break
    who = AUTHOR.match(head.get("from", "")) if head.get("from") else None
    return {"author": who["name"].strip() if who else None, "role": (who["role"] or "").strip() or None if who else None,
            "time": head.get("time"), "text": "\n".join(text).strip()}


def _result(native: str) -> dict[str, Any]:
    try:
        body = json.loads(native)
    except ValueError:
        return {}
    return body.get("result", body) if isinstance(body, dict) else {}


class Reader:
    """Reads at one `now`, through one toolbox, one call at a time."""

    def __init__(self, toolbox: Toolbox, now: datetime):
        self.toolbox, self.now = toolbox, now
        self.ctx: ToolContext = toolbox.context(now, "api-reader")
        self.lock = threading.Lock()

    @property
    def store(self):
        return self.ctx.store

    def call(self, tool: str, args: dict[str, Any]):
        with self.lock:
            return registry.call(self.toolbox.read_tools[tool], args, self.ctx)

    def rows(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self.lock:
            cur = self.store.conn.execute(sql, params)
            names = [c[0] for c in cur.description]
            return [dict(zip(names, r)) for r in cur.fetchall()]

    # -- the incident card -----------------------------------------------------------------------------------

    def incident(self, trigger: str, named: str | None = None) -> dict[str, Any]:
        """The incident the trigger belongs to, as it stands at `now`. Found from served data: the incident on
        which the trigger's author last wrote a work note, else the one they last updated, else the open
        incident of highest priority most recently opened. `named` (a config value) overrides all three."""
        from smalls.tools.store import render_native

        note = note_of(trigger)
        if self.store is None:
            return {"incident": None, "note": note, "found_by": "no served incident data"}
        incidents = self.rows("SELECT handle, native, flags, t_event, t_known FROM visible WHERE kind = 'incident'")
        for row in incidents:
            row["fields"] = _result(render_native(row["native"], row["flags"]))
        by_number = {r["fields"].get("number"): r for r in incidents}
        chosen, found_by = None, None
        if named and named in by_number:
            chosen, found_by = by_number[named], "named in config"
        user = self._user(note["author"]) if note["author"] else None
        if chosen is None and user:
            notes = self.rows("SELECT parent, t_known, native, flags FROM visible WHERE kind = 'note' AND parent IS NOT NULL ORDER BY t_known DESC")
            mine = next((n for n in notes if _result(render_native(n["native"], n["flags"])).get("sys_created_by") == user), None)
            if mine and any(r["handle"] == mine["parent"] for r in incidents):
                chosen, found_by = next(r for r in incidents if r["handle"] == mine["parent"]), "the trigger's author's latest work note"
        if chosen is None and user:
            touched = sorted((r for r in incidents if r["fields"].get("sys_updated_by") == user), key=lambda r: r["t_known"], reverse=True)
            if touched:
                chosen, found_by = touched[0], "the incident the trigger's author last updated"
        if chosen is None:
            open_ = [r for r in incidents if str(r["fields"].get("state", "")).lower() not in INACTIVE]
            open_.sort(key=lambda r: (str(r["fields"].get("priority", "9")), -r["t_event"]))
            if open_:
                chosen, found_by = open_[0], "the open incident of highest priority"
        if chosen is None:
            return {"incident": None, "note": note, "found_by": "no visible incident"}
        f = chosen["fields"]
        card = {key: f.get(field) for key, field in FIELDS.items()}
        card.update(handle=chosen["handle"], opened=iso_ms(chosen["t_event"]), updated=iso_ms(chosen["t_known"]),
                    priority_code=str(f.get("priority", "")).split(" ", 1)[0] or None)
        return {"incident": card, "note": note, "found_by": found_by}

    def _user(self, display: str) -> str | None:
        """A person's user name, from the served user records, by the name the trigger gives."""
        from smalls.tools.store import render_native

        for row in self.rows("SELECT native, flags FROM visible WHERE kind = 'user'"):
            f = _result(render_native(row["native"], row["flags"]))
            if str(f.get("name", "")).strip().lower() == display.strip().lower():
                return f.get("user_name")
        return None

    # -- the service desk: open incidents, import and the ticket view (v4 plan 5.2, 5.5) -------------------

    @property
    def connected(self) -> bool:
        """Whether the ServiceNow source is loaded, so the screens can say it is connected."""
        return self.store is not None and "servicenow" in self.store.integration.sources

    def _incident_rows(self) -> list[dict[str, Any]]:
        from smalls.tools.store import render_native

        if not self.connected:
            return []
        rows = self.rows("SELECT handle, native, flags, t_event, t_known FROM visible WHERE kind = 'incident' AND source = 'servicenow'")
        for row in rows:
            row["fields"] = _result(render_native(row["native"], row["flags"]))
        return rows

    @staticmethod
    def card(row: dict[str, Any]) -> dict[str, Any]:
        """An incident as the header and the lists show it: its own fields at `now`, and the two facts read off
        them. A major incident is what `major_incident_state` says when the ticket carries it, else priority 1."""
        f = row["fields"]
        card = {key: f.get(field) for key, field in FIELDS.items()}
        code = str(f.get("priority", "")).split(" ", 1)[0] or None
        state = str(f.get("major_incident_state") or "").strip().lower()
        card.update(handle=row["handle"], opened=iso_ms(row["t_event"]), updated=iso_ms(row["t_known"]), priority_code=code,
                    major=state == "accepted" if state else code == "1",
                    active=str(f.get("state", "")).lower() not in INACTIVE)
        return card

    def find_incident(self, number: str) -> dict[str, Any] | None:
        """The ServiceNow incident with this number visible at `now`, as a card, or None."""
        wanted = number.strip().upper()
        row = next((r for r in self._incident_rows() if str(r["fields"].get("number", "")).upper() == wanted), None)
        return self.card(row) if row else None

    def open_incidents(self) -> list[dict[str, Any]]:
        """The open P1 and P2 incidents visible at `now`, highest priority first, then the latest opened."""
        cards = [self.card(r) for r in self._incident_rows()]
        cards = [c for c in cards if c["active"] and c["priority_code"] in ("1", "2")]
        return sorted(cards, key=lambda c: (c["priority_code"], -(datetime.fromisoformat(c["opened"].replace("Z", "+00:00")).timestamp())))

    def work_notes(self, handle: str) -> list[dict[str, Any]]:
        """An incident's work notes at `now`, newest first: who wrote each (by name, from the served user records),
        when in UTC and in the ticket source's own zone, and the text."""
        from zoneinfo import ZoneInfo

        from smalls.tools.store import render_native

        zone = ZoneInfo(self.store.integration.zone("servicenow").key)
        names = {}
        for row in self.rows("SELECT native, flags FROM visible WHERE kind = 'user' AND source = 'servicenow'"):
            f = _result(render_native(row["native"], row["flags"]))
            if f.get("user_name"):
                names[f["user_name"]] = f.get("name") or f["user_name"]
        out = []
        for row in self.rows("SELECT handle, native, flags, t_event, t_known FROM visible WHERE kind = 'note' AND source = 'servicenow' "
                             "AND parent = ? ORDER BY t_known DESC", (handle,)):
            f = _result(render_native(row["native"], row["flags"]))
            if f.get("element") not in (None, "work_notes"):
                continue
            ms = row["t_event"] or row["t_known"]
            by = f.get("sys_created_by")
            out.append({"handle": row["handle"], "author": names.get(by, by), "user_name": by, "utc": iso_ms(ms),
                        "local": datetime.fromtimestamp(ms / 1000, zone).strftime("%H:%M:%S"), "text": str(f.get("value") or "").strip()})
        return out

    def ticket(self, number: str) -> dict[str, Any] | None:
        """The ticket view: the incident's fields as ServiceNow names them, its work notes newest first, and the
        source's zone, which is the only place a screen shows a local time."""
        from zoneinfo import ZoneInfo

        card = self.find_incident(number)
        if card is None:
            return None
        row = next(r for r in self._incident_rows() if r["handle"] == card["handle"])
        f = row["fields"]
        zone = self.store.integration.zone("servicenow").key
        opened_ms = row["t_event"]
        return {"incident": card, "zone": zone,
                "fields": {"state": f.get("state"), "priority": f.get("priority"), "impact": f.get("impact"), "urgency": f.get("urgency"),
                           "business_service": f.get("business_service"), "cmdb_ci": f.get("cmdb_ci"),
                           "assignment_group": f.get("assignment_group"), "assigned_to": f.get("assigned_to")},
                "opened": {"utc": iso_ms(opened_ms), "local": datetime.fromtimestamp(opened_ms / 1000, ZoneInfo(zone)).strftime("%H:%M:%S")},
                "notes": self.work_notes(card["handle"])}

    # -- the incident overview's inputs (v4 plan 5.2) --------------------------------------------------------

    def _fields(self, where: str, params: tuple = ()) -> list[dict[str, Any]]:
        from smalls.tools.store import render_native

        out = []
        for row in self.rows(f"SELECT handle, native, flags, t_event, t_known FROM visible WHERE {where}", params):
            body = render_native(row["native"], row["flags"])
            f = _result(body)
            if not f:
                try:
                    f = json.loads(body)
                except ValueError:
                    f = {}
            out.append({"handle": row["handle"], "t_event": row["t_event"], "t_known": row["t_known"], "fields": f if isinstance(f, dict) else {}})
        return out

    def overview_inputs(self, number: str) -> dict[str, Any] | None:
        """What the overview is written from, all served data at `now`: the ticket, its work notes, the changes the
        notes name or the ticket links, the CMDB around its CI and business service, who is on call for its
        groups, the people it names, and the chat channels the notes name."""
        ticket = self.ticket(number)
        if ticket is None:
            return None
        f, notes = ticket["fields"], ticket["notes"]
        text = " ".join(n["text"] for n in notes)
        raw = next(r for r in self._incident_rows() if r["handle"] == ticket["incident"]["handle"])["fields"]
        named = set(re.findall(r"\bCHG\d{4,}\b", text)) | ({raw.get("rfc")} if raw.get("rfc") else set())
        changes = [{k: c["fields"].get(k) for k in ("number", "short_description", "state", "start_date", "end_date", "cmdb_ci", "assignment_group", "assigned_to")}
                   for c in self._fields("kind = 'change' AND source = 'servicenow'") if c["fields"].get("number") in named]
        rels = [r["fields"] for r in self._fields("source = 'servicenow' AND dataset = 'cmdb_rel_ci'")]
        cis = {c["fields"].get("name"): c["fields"] for c in self._fields("source = 'servicenow' AND dataset = 'cmdb_ci'")}
        near = {x for x in (f.get("cmdb_ci"), f.get("business_service")) if x}
        for _ in range(2):          # two steps out from the ticket's CI and business service, both directions
            near |= {r.get("child") for r in rels if r.get("parent") in near} | {r.get("parent") for r in rels if r.get("child") in near}
        near.discard(None)
        cmdb = {"relationships": [{k: r.get(k) for k in ("parent", "type", "child")} for r in rels if r.get("parent") in near and r.get("child") in near],
                "items": [{k: cis[n].get(k) for k in ("name", "sys_class_name", "short_description", "operational_status", "support_group")} for n in sorted(near) if n in cis]}
        users = {u["fields"].get("name"): u["fields"] for u in self._fields("kind = 'user' AND source = 'servicenow'") if u["fields"].get("name")}
        channels = sorted({c for c in re.findall(r"#([a-z0-9][a-z0-9_-]{1,79})", text)
                           if self.rows("SELECT 1 AS x FROM visible WHERE source = 'slack' AND dataset = ? LIMIT 1", (c,))})
        groups = {f.get("assignment_group"), cis.get(f.get("cmdb_ci") or "", {}).get("support_group")} - {None, ""}
        return {"ticket": ticket, "changes": changes, "cmdb": cmdb, "oncall": self.oncall(groups), "people": self._named(text, users, notes),
                "channels": channels, "support_group": cis.get(f.get("cmdb_ci") or "", {}).get("support_group")}

    def oncall(self, groups: set[str]) -> list[dict[str, Any]]:
        """Who is on call now for these groups' escalation policies (a policy named as the group), by level."""
        out = []
        for row in self._fields("source = 'pagerduty' AND dataset = 'oncalls'"):
            f = row["fields"]
            policy = (f.get("escalation_policy") or {}).get("summary")
            if policy in groups:
                out.append({"policy": policy, "level": f.get("escalation_level"), "name": (f.get("user") or {}).get("summary"),
                            "schedule": (f.get("schedule") or {}).get("summary")})
        return sorted(out, key=lambda o: (o["policy"] or "", o["level"] or 0))

    @staticmethod
    def _named(text: str, users: dict[str, dict[str, Any]], notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The people the work notes name or are written by, as the served user records know them: a full name, or
        a first name only one person has."""
        firsts: dict[str, list[str]] = {}
        for name, u in users.items():
            firsts.setdefault(str(u.get("first_name") or name.split(" ")[0]), []).append(name)
        found = {n["author"] for n in notes if n.get("author") in users}
        for name in users:
            if re.search(rf"\b{re.escape(name)}\b", text):
                found.add(name)
        for first, names in firsts.items():
            if len(names) == 1 and re.search(rf"\b{re.escape(first)}\b", text):
                found.add(names[0])
        return [{"name": n, "title": users[n].get("title"), "department": users[n].get("department"), "user_name": users[n].get("user_name"),
                 "phone": users[n].get("phone") or users[n].get("mobile_phone") or None} for n in sorted(found)]

    # -- the evidence drawer ---------------------------------------------------------------------------------

    def record(self, handle: str) -> dict[str, Any] | None:
        """A record as it stands at `now`, the way the drawer shows it, or None when nothing visible has the handle."""
        from smalls.tools.store import render_native

        if self.store is None:
            return None
        found = self.rows("SELECT handle, source, dataset, kind, t_event, t_known, t_native, tz_rule, native, flags, parent, path, version, "
                          "title FROM visible WHERE handle = ? ORDER BY version DESC LIMIT 1", (handle,))
        if not found:
            return None
        r = found[0]
        children = [c["handle"] for c in self.rows("SELECT DISTINCT handle FROM visible WHERE parent = ? ORDER BY t_known", (handle,))]
        native = render_native(r["native"], r["flags"])
        try:
            native, fmt = json.dumps(json.loads(native), indent=2, ensure_ascii=False), "json"
        except ValueError:
            fmt = "text"
        flags = json.loads(r["flags"] or "{}")
        zone = self.store.integration.zone(r["source"]).key if r["source"] in self.store.integration.sources else "UTC"
        now_ms = self.store.now
        # A record can be known before the time it is about: a change scheduled for later. That time is a plan,
        # and is shown as one, never as when something happened.
        happened = bool(r["t_event"]) and r["t_event"] < now_ms
        return {"handle": handle, "source": r["source"], "dataset": r["dataset"], "kind": r["kind"], "title": r["title"],
                "event_utc": iso_ms(r["t_event"]) if happened else None,
                "planned_utc": iso_ms(r["t_event"]) if r["t_event"] and not happened else None,
                "known_utc": iso_ms(r["t_known"]) if r["t_known"] else None,
                "vendor_time": r["t_native"], "zone": zone, "path": r["path"], "version": r["version"], "native": native, "format": fmt,
                "withheld": list(flags.get("withheld") or []), "parent": r["parent"], "children": children}

    def schemas(self) -> list[dict[str, Any]]:
        """Each read tool as the model sees it, without its `rationale`: what the Edit form offers."""
        out = []
        for spec in self.toolbox.read_tools.values():
            d = registry.definition(spec)
            params = dict(d["parameters"])
            params["properties"] = {k: v for k, v in params.get("properties", {}).items() if k != "rationale"}
            params["required"] = [k for k in params.get("required", []) if k != "rationale"]
            out.append({"name": d["name"], "description": d["description"], "parameters": params})
        return out


def call_view(record: RunRecord, events: list[dict[str, Any]], call_id: str) -> dict[str, Any] | None:
    """What one call showed: its arguments, native query, window and status, from the run record's calls (or,
    mid-run, from its events), and the rows it showed with their handles, from the evidence file."""
    call = next((c for c in record.read_jsonl("calls.jsonl") if c.get("call_id") == call_id), None)
    if call is None:
        proposed = next((e["data"] for e in events if e["type"] == "tool_call_proposed" and e["data"].get("call_id") == call_id), None)
        result = next((e["data"] for e in events if e["type"] == "tool_result" and e["data"].get("call_id") == call_id), None)
        if proposed is None and result is None:
            return None
        call = {**(proposed or {}), **(result or {})}
    rows = [{"handle": r["handle"], "t_known": r.get("t_known"), "text": r["text"]}
            for r in record.read_jsonl("evidence.jsonl") if r.get("call_id") == call_id]
    keep = ("call_id", "round", "by", "tool", "kind", "args", "edited_args", "rationale", "status", "shown", "referenced", "native_query",
            "window", "chars", "repeat_of")
    return {**{k: call.get(k) for k in keep}, "rows": rows}


def citing(report: dict[str, Any] | None, handle: str) -> list[dict[str, str]]:
    """The claims of a report that cite a handle: each hypothesis, timeline row, action and do-not row."""
    if not report:
        return []
    out = []
    for h in report.get("hypotheses") or []:
        for name in ("support", "against"):
            if handle in (h.get(name) or []):
                out.append({"claim": f"h:{h['id']}", "as": name, "text": h.get("statement", "")})
    for n, row in enumerate(report.get("timeline") or [], 1):
        if handle in (row.get("evidence") or []):
            out.append({"claim": f"t:{n}", "as": "evidence", "text": row.get("event", "")})
    for row in report.get("actions") or []:
        if handle in (row.get("evidence") or []):
            out.append({"claim": f"a:{row.get('rank')}", "as": "evidence", "text": row.get("action", "")})
    for n, row in enumerate(report.get("do_not") or [], 1):
        if handle in (row.get("evidence") or []):
            out.append({"claim": f"d:{n}", "as": "evidence", "text": row.get("action", "")})
    if handle in str(report.get("summary") or ""):
        out.append({"claim": "summary", "as": "cited", "text": report.get("summary", "")})
    return out


def evidence(reader: Reader, records: list[RunRecord], report: dict[str, Any] | None, handle: str) -> dict[str, Any] | None:
    """The drawer for a handle: the native record at `now` when it is one, what the runs were shown of it, the
    native query and window of the call that showed it, related records, and the claims that cite it."""
    shown, calls, notes = [], {}, []
    for record in records:
        shown += [{**r, "run_id": record.run_id} for r in record.read_jsonl("evidence.jsonl") if r.get("handle") == handle]
        calls.update({(record.run_id, c["call_id"]): c for c in record.read_jsonl("calls.jsonl")})
        notes += [n for n in record.read_jsonl("notes.jsonl") if n.get("handle") == handle]
    stored = handle_class(handle) in ("record", "chat") and not handle.startswith("ic:note/")      # a chat message is a record too
    native = reader.record(handle) if stored else None
    if native is None and reader.store is None and stored and "get_record" in reader.toolbox.read_tools:
        env = reader.call("get_record", {"handle": handle})        # a registry with no store: the tool's own answer
        if env.status == "ok":
            native = {"handle": handle, "source": None, "kind": None, "native": env.text, "format": "text", "parent": None,
                      "children": list(env.referenced), "withheld": [], "event_utc": None,
                      "known_utc": env.rows[0].t_known if env.rows else None, "vendor_time": None, "zone": None}
    if native is None and not shown and not notes:
        return None
    via = [calls.get((s["run_id"], s["call_id"])) for s in shown]
    first = next((c for c in via if c), None)
    referenced_by = sorted({c["call_id"] for c in calls.values() if handle in (c.get("referenced") or [])})
    return {"handle": handle, "class": handle_class(handle), "record": native,
            "shown": [{"run_id": s["run_id"], "call_id": s["call_id"], "t_known": s.get("t_known"), "text": s["text"]} for s in shown],
            "note": notes[-1] if notes else None,
            "query": {"tool": first.get("tool"), "native_query": first.get("native_query"), "window": first.get("window"),
                      "ends_at_now": bool(first.get("window") and first["window"][1] == iso(reader.now))} if first else None,
            "referenced_by": referenced_by, "claims": citing(report, handle)}
