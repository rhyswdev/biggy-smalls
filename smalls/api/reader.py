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
