"""A fixture registry for offline agent tests (docs/decisions/tools-agent-build.md section 7.4): a made-up
incident that has nothing to do with the dataset. A nightly export job fails because its disk fills up,
after a release stopped cleaning temporary files.

Each tool here sits on the real argument model and returns the real envelope, so the graph, the gate, the
validator and the run record see exactly what the real tools will give them. Rows appear between two
values of `now` (02:12 and 02:20), which is what the carry-forward tests use.

`registry()` and `inventory(ctx)` are what the runner's `module_toolbox` expects.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from smalls.contracts.envelope import Envelope, Row
from smalls.contracts.handles import is_handle, log_handle, query_handle, slack_handle
from smalls.tools import errors, schemas
from smalls.tools.registry import CAPS, ToolContext, ToolSpec

UTC = timezone.utc
EARLY = datetime(2026, 3, 2, 2, 12, tzinfo=UTC)
NOW = datetime(2026, 3, 2, 2, 20, tzinfo=UTC)
TRIGGER = ("The nightly report-export job failed at 02:10 UTC. Someone in the channel thinks the disk is dying. "
           "What happened, and what should we do before tonight's run?")

LOG_LINES = {
    "worker-01": """\
2026-03-02T01:00:04.101Z INFO  [deploy] report-exporter version=2.4.1 installed (was 2.4.0)
2026-03-02T01:30:00.002Z INFO  [health] GET /healthz 200 3ms
2026-03-02T02:00:01.120Z INFO  [scheduler] job=report-export run=57 started
2026-03-02T02:00:01.455Z INFO  [exporter] writing temp files to /var/spool/report-exporter/tmp
2026-03-02T02:03:12.870Z WARN  [disk] /var/spool usage=88% free=5.9GiB
2026-03-02T02:06:40.311Z WARN  [disk] /var/spool usage=95% free=2.5GiB
2026-03-02T02:08:55.902Z WARN  [disk] /var/spool usage=99% free=0.4GiB
2026-03-02T02:09:31.004Z ERROR [exporter] write failed: [Errno 28] No space left on device: '/var/spool/report-exporter/tmp/part-0412.csv'
2026-03-02T02:09:31.010Z ERROR [exporter] write failed: [Errno 28] No space left on device: '/var/spool/report-exporter/tmp/part-0413.csv'
2026-03-02T02:10:02.551Z ERROR [scheduler] job=report-export run=57 exited code=1 after 601s
2026-03-02T02:10:02.560Z WARN  [exporter] temp cleanup skipped: cleanup_on_exit=false
2026-03-02T02:15:00.002Z INFO  [health] GET /healthz 200 2ms
2026-03-02T02:31:10.000Z INFO  [ops] /var/spool/report-exporter/tmp purged by on-call, usage=41%
""",
    "worker-02": """\
2026-03-02T01:30:00.010Z INFO  [health] GET /healthz 200 4ms
2026-03-02T02:00:00.011Z INFO  [scheduler] job=invoice-digest run=903 started
2026-03-02T02:04:12.400Z INFO  [scheduler] job=invoice-digest run=903 finished code=0 after 252s
2026-03-02T02:05:41.220Z WARN  [gc] pause 412ms (young)
2026-03-02T02:15:00.009Z INFO  [health] GET /healthz 200 3ms
""",
}


@dataclass(frozen=True)
class Item:
    handle: str
    t: datetime             # when it became known
    text: str
    host: str = ""
    level: str = ""
    children: tuple[str, ...] = ()


def t(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def iso(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _logs() -> list[Item]:
    out = []
    for host, text in LOG_LINES.items():
        for raw in text.splitlines():
            stamp, level = raw.split()[:2]
            out.append(Item(log_handle(f"logs/{host}.log", raw), t(stamp), raw, host, level))
    return sorted(out, key=lambda i: i.t)


LOGS = _logs()
COMMIT = "jk:report-export#57/0a1b2c3d"
RECORDS = {r.handle: r for r in [
    Item("jk:report-export#57", t("2026-03-02T02:10:03Z"),
         json.dumps({"number": 57, "result": "FAILURE", "builtOn": "worker-01", "parameters": {"VERSION": "2.4.1"},
                     "console_tail": "No space left on device"}, indent=1), children=(COMMIT,)),
    Item("jk:report-export#56", t("2026-03-01T02:06:10Z"),
         json.dumps({"number": 56, "result": "SUCCESS", "builtOn": "worker-01", "parameters": {"VERSION": "2.4.0"}}, indent=1)),
    Item(COMMIT, t("2026-03-01T16:40:00Z"), json.dumps({"msg": "exporter: keep temp files for debugging", "paths": ["exporter/run.py"]}, indent=1)),
    Item("conf:RB-DISK-SPACE", t("2025-11-20T09:00:00Z"),
         "Runbook: disk space on batch workers\n1. Check /var/spool usage. 2. If report-exporter temp files are present and no job "
         "is running, purge /var/spool/report-exporter/tmp. 3. If a release changed temp handling, roll back the release and open "
         "a ticket with the owning team (Reporting). Never purge while a job is running."),
]}
ALERTS = [
    Item("dd:ev1000000001", t("2026-03-02T02:06:45Z"), "datadog  mon101  [Warn] Disk usage high on /var/spool  host:worker-01  "
         "first 02:06:45Z  last 02:08:57Z  state alert  transitions 2", children=("dd:ev1000000003",)),
    Item("dd:ev1000000002", t("2026-03-02T02:05:42Z"), "datadog  mon102  [Warn] GC pause over 400 ms  host:worker-02  "
         "first 02:05:42Z  last 02:05:42Z  state ok  transitions 2"),
    Item("dd:ev1000000004", t("2026-03-02T02:16:00Z"), "datadog  mon103  [Triggered] Export job failed  job:report-export  "
         "first 02:16:00Z  last 02:16:00Z  state alert  transitions 1"),
]
CHANGES = [
    Item("jk:report-export#56", t("2026-03-01T02:00:01Z"), "build  report-export#56  VERSION 2.4.0  SUCCESS  start 2026-03-01T02:00:01Z"),
    Item("jk:report-exporter-deploy#12", t("2026-03-02T01:00:04Z"), "build  report-exporter-deploy#12  VERSION 2.4.1  SUCCESS  start 2026-03-02T01:00:00Z"),
    Item("jk:report-export#57", t("2026-03-02T02:10:03Z"), "build  report-export#57  VERSION 2.4.1  FAILURE  start 2026-03-02T02:00:01Z",
         children=(COMMIT,)),
]
CHAT = [Item(slack_handle("ops", ts, text), t(when), f"{who}: {text}") for when, ts, who, text in [
    ("2026-03-02T02:11:00Z", "1772417460.000100", "dana", "report-export just failed again, is the disk on worker-01 dying?"),
    ("2026-03-02T02:13:30Z", "1772417610.000200", "lee", "smart shows nothing wrong on that disk"),
    ("2026-03-02T02:17:05Z", "1772417825.000300", "dana", "we shipped 2.4.1 last night fwiw"),
]]


def _window(args, now: datetime) -> tuple[datetime, datetime, bool]:
    def resolve(spec: str | None, default: datetime) -> datetime:
        if not spec or spec == "now":
            return default
        m = re.fullmatch(r"-(\d+)([smhd])", spec.strip())
        if m:
            return now - timedelta(**{{"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}[m[2]]: int(m[1])})
        return t(spec)

    since, until = resolve(args.since, now), resolve(args.until, now)
    return since, min(until, now), until > now


def _rows(items: list[Item], now: datetime, since: datetime, until: datetime, text: str | None = None) -> list[Item]:
    return [i for i in items if i.t < now and since <= i.t < until and (not text or text.lower() in i.text.lower())]


def _listing(items: list[Item], window, clamped: bool, native: str, what: str) -> Envelope:
    span = (iso(window[0]), iso(window[1]))
    if not items:
        return Envelope(f"0 rows. Widen the window to see more {what}.", native_query=native, window=span,
                        meta={"empty": True, "clamped": clamped})
    lines = [f"{i.handle}  {iso(i.t)}  {i.text}" for i in items]
    return Envelope("\n".join([*lines, f"[{len(items)} {what}]"]), rows=[Row(i.handle, line, iso(i.t)) for i, line in zip(items, lines)],
                    referenced=[c for i in items for c in i.children], native_query=native, window=span,
                    meta={"total": len(items), "returned": len(items), "clamped": clamped})


def search_logs(args: schemas.SearchLogsArgs, ctx: ToolContext) -> Envelope:
    since, until, clamped = _window(args, ctx.now)
    hits = [i for i in _rows(LOGS, ctx.now, since, until) if (not args.host or i.host == args.host)
            and all(term.lower() in i.text.lower() for term in args.terms)]
    native = f'search index=app {" ".join(json.dumps(x) for x in args.terms)} earliest="{iso(since)}" latest="{iso(until)}"'
    span = (iso(since), iso(until))
    if not hits:
        return Envelope("0 rows. Drop a term, remove host=, or widen the window.", native_query=native, window=span,
                        meta={"empty": True, "clamped": clamped})
    if args.group_by:
        key = {"minute": lambda i: i.t.strftime("%H:%M"), "host": lambda i: i.host, "level": lambda i: i.level}[args.group_by]
        counts: dict[str, int] = {}
        for i in hits:
            counts[key(i)] = counts.get(key(i), 0) + 1
        handle = query_handle(f"search_logs|{args.model_dump_json()}", iso(ctx.now))
        table = "\n".join([f"{handle}  count by {args.group_by}, {len(hits)} records"] + [f"{k}\t{v}" for k, v in sorted(counts.items())])
        return Envelope(table, rows=[Row(handle, table)], native_query=native + f" | stats count by {args.group_by}", window=span,
                        meta={"total": len(hits), "returned": len(counts), "clamped": clamped})
    shown = hits[: args.limit]
    lines = [f"{i.handle}  {iso(i.t)}  {i.host}  {i.text[:300]}" for i in shown]
    footer = f"[showing {len(shown)} of {len(hits)}]"
    return Envelope("\n".join([*lines, footer]), rows=[Row(i.handle, line, iso(i.t)) for i, line in zip(shown, lines)],
                    native_query=native, window=span,
                    meta={"total": len(hits), "returned": len(shown), "truncated": len(hits) > len(shown), "clamped": clamped})


def get_record(args: schemas.GetRecordArgs, ctx: ToolContext) -> Envelope:
    handle = args.handle.strip()
    if not is_handle(handle):
        return errors.malformed_handle(handle)
    item = RECORDS.get(handle) or next((i for i in [*LOGS, *ALERTS, *CHAT] if i.handle == handle), None)
    if item is None or item.t >= ctx.now:       # not yet visible reads exactly like unknown
        return errors.not_in_bundle(handle)
    text = f"{handle}  (as of {iso(ctx.now)})\n{item.text}"
    if item.children:
        text += "\nchildren: " + ", ".join(item.children)
    return Envelope(text, rows=[Row(handle, text, iso(item.t))], referenced=item.children, meta={"total": 1, "returned": 1})


def list_alerts(args: schemas.ListAlertsArgs, ctx: ToolContext) -> Envelope:
    since, until, clamped = _window(args, ctx.now)
    return _listing(_rows(ALERTS, ctx.now, since, until, args.text), (since, until), clamped, "alerts", "alert streams")


def list_changes(args: schemas.ListChangesArgs, ctx: ToolContext) -> Envelope:
    since, until, clamped = _window(args, ctx.now)
    return _listing(_rows(CHANGES, ctx.now, since, until, args.text), (since, until), clamped, "changes", "changes")


def read_chat(args: schemas.ReadChatArgs, ctx: ToolContext) -> Envelope:
    since, until, clamped = _window(args, ctx.now)
    return _listing(_rows(CHAT, ctx.now, since, until, args.text), (since, until), clamped, "in:#ops", "messages")


FUNCTIONS = {"list_alerts": list_alerts, "get_record": get_record, "search_logs": search_logs, "list_changes": list_changes,
             "read_chat": read_chat}


def registry() -> dict[str, ToolSpec]:
    """The fixture tools, in the real registry's order, on the real argument models."""
    models = schemas.args_models(tags=False)
    return {name: ToolSpec(name, "read", models[name], FUNCTIONS[name], *CAPS[name]) for name in models if name in FUNCTIONS}


def inventory(ctx: ToolContext) -> Envelope:
    """What exists, from visible rows only."""
    hosts = sorted({i.host for i in LOGS if i.t < ctx.now})
    records = sorted(h for h, r in RECORDS.items() if r.t < ctx.now)
    first = min(i.t for i in LOGS if i.t < ctx.now)
    text = (f"Logs: sourcetype app; hosts {', '.join(hosts)}; coverage {iso(first)} to now.\n"
            f"Chat channels: ops.\nRecords: {', '.join(records)}.")
    return Envelope(text, referenced=records)
