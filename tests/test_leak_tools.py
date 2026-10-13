"""The tool-output half of the leak test (docs/decisions/tools-agent-build.md section 6.6; build row B5).
At each core checkpoint, with tags off and with tags on, nothing any built tool prints carries a time
after `now` or a token from the checkpoint's future, every tool stays inside its caps, and every visible
record can be reached from some result. `make leak-test` runs this file after the store half.

The scanner, the checkpoints, the future tokens and the exemptions are the store half's: this file uses
what `tests/test_leak.py` already loads.

What fails: a time later than `now`; a time equal to `now` anywhere but the end of the window; a future
token; a printed `valid_to`. What is exempt: a scheduled time, by field, and each is printed with the word
`planned` (a change request's planned start and end, an on-call shift's end).

The call set grows with the registry: a tool that is built and has no entry here fails the test.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache

import pytest

import golden
import parity
import test_leak
from eval.retrieval import run as retrieval
from smalls.contracts.envelope import Envelope
from smalls.tools import inventory, registry, windows

CHECKPOINTS = list(golden.CHECKPOINTS)
ARMS = [(c, tags) for c in CHECKPOINTS for tags in (False, True)]
UTC = test_leak.UTC
WIDEST = "-30d"
PLANNED = re.compile(r"planned (?:start|end) \S+(?: \(native [^)]*\))?|^.*\(planned\)$", re.M)


def list_calls(ctx) -> dict[str, list[dict]]:
    """The calls that list records, per tool: the widest window with no filter, and each way of counting."""
    calls = {
        "search_logs": [{"since": WIDEST, "limit": 50}, *({"since": WIDEST, "group_by": by} for by in ("minute", "host", "level", "template"))],
        "list_alerts": [{"since": WIDEST}, {"since": WIDEST, "state": "active"}],
        "list_changes": [{"since": WIDEST}, {"since": WIDEST, "until": "now"}],
        "read_chat": [{"since": WIDEST}, *({"since": WIDEST, "channel": c} for c in channels(ctx))],
        "search_knowledge": [{"query": q["query"]} for q in retrieval.load_queries()],     # the retrieval-check queries
        "get_record": [],       # every handle in the store, below
    }
    if ctx.tags:
        calls["search_logs"] += [{"since": WIDEST, "group_by": "signal"}, {"since": WIDEST, "signal": "untagged", "limit": 50}]
        calls["list_alerts"] += [{"since": WIDEST, "signal": "untagged"}]
        calls["list_changes"] += [{"since": WIDEST, "change_kind": "untagged"}]
        calls["read_chat"] += [{"since": WIDEST, "act": "untagged"}]
    return calls


def channels(ctx) -> list[str]:
    return [c for (c,) in ctx.store.conn.execute("SELECT DISTINCT dataset FROM visible WHERE kind = 'message'")]


@lru_cache(maxsize=None)
def record_facts(checkpoint: str, tags: bool) -> dict[str, tuple[str, int]]:
    """Each handle's source and event time, for the zone a row is scanned in and the old-record exemption."""
    ctx = golden.ctx(checkpoint, tags)
    return {h: (s, t) for h, s, t in ctx.store.conn.execute("SELECT handle, source, t_event FROM record")}


def scan(env: Envelope, ctx, checkpoint: str, what: str) -> None:
    """Fail on a time after now, a now that is not the window's end, a future token, or a printed store column."""
    now = ctx.store.now
    facts = record_facts(checkpoint, ctx.tags)
    tokens = test_leak.SCENARIO["future_tokens"][checkpoint]
    day0 = parity.ms(f"{golden.DAY}T00:00:00Z")
    outside = env.text
    latest = 0
    for row in env.rows:
        source, event = facts.get(row.handle, (None, day0))
        zone = ctx.store.integration.zone(source) if source else UTC
        kept = PLANNED.sub("", row.text)
        latest = max(latest, max(test_leak.times_in(kept, zone), default=0))
        outside = outside.replace(row.text, "")
        if event >= day0:       # a record written before the bundle's day cannot hold a token from the day's future
            assert not [tok for tok in tokens if tok in row.text], (what, row.handle)
        assert row.t_known is None or parity.ms(row.t_known) < now, (what, row.handle)
    rest = "\n".join([PLANNED.sub("", outside), env.native_query or "", json.dumps(env.window), json.dumps(dict(env.meta), default=str)])
    latest = max(latest, max(test_leak.times_in(rest, UTC), default=0))
    assert latest <= now, (what, windows.iso(latest))
    if latest == now:           # now itself may appear only as the end of the window
        assert env.window is not None and env.window[1] == windows.iso(now), what
    assert not [tok for tok in tokens if tok in rest], what
    assert "valid_to" not in env.text and "overall_state" not in env.text, what


def check_caps(env: Envelope, spec, what: str) -> None:
    assert len(env.text) // 4 <= spec.cap_tokens, what
    assert spec.cap_rows is None or len(env.rows) <= spec.cap_rows, what
    assert all(row.text in env.text and row.text.startswith(row.handle) for row in env.rows), what
    if env.meta["truncated"]:
        assert "[showing " in env.text and " not shown. " in env.text, what          # a truncated result carries its footer


def test_every_built_tool_has_a_call_set():
    for tags in (False, True):
        ctx = golden.ctx(golden.SECOND, tags)
        built = registry.registry(tags=tags)
        assert set(built) == set(list_calls(ctx)), "a tool was built without a call set in the leak test"
        assert len(built) >= 5


@pytest.mark.parametrize("checkpoint,tags", ARMS)
def test_no_list_result_leaks_and_every_tool_stays_inside_its_caps(checkpoint, tags):
    ctx = golden.ctx(checkpoint, tags)
    built = registry.registry(tags=tags)
    for name, calls in list_calls(ctx).items():
        for args in calls:
            env = golden.run(name, ctx, **args)
            what = f"{name} {json.dumps(args)}"
            assert env.status == "ok", (what, env.text[:200])
            scan(env, ctx, checkpoint, what)
            check_caps(env, built[name], what)
            assert tags or "[tag " not in env.text, what
    listed = inventory.build(ctx)
    scan(listed, ctx, checkpoint, "inventory")
    assert len(listed.text) // 4 <= 2000 and (tags or "tag" not in listed.text)


@pytest.mark.parametrize("checkpoint,tags", ARMS)
def test_get_record_on_every_handle_in_the_store(checkpoint, tags):
    """A visible handle returns its record, with nothing from after now. A handle not yet visible returns
    byte for byte what an unknown handle returns."""
    ctx = golden.ctx(checkpoint, tags)
    spec = registry.registry(tags=tags)["get_record"]
    visible = dict(ctx.store.conn.execute("SELECT handle, version FROM visible"))
    unknown = golden.run("get_record", ctx, handle="conf:NO-SUCH-PAGE")
    assert unknown.status == "error"
    for handle in record_facts(checkpoint, tags):
        env = golden.run("get_record", ctx, handle=handle)
        if handle not in visible:
            assert env.status == "error" and env.text.replace(handle, "H") == unknown.text.replace("conf:NO-SUCH-PAGE", "H"), handle
            assert env.rows == () and env.referenced == ()
            continue
        assert env.status == "ok" and env.shown == (handle,), handle
        parts = [env] + [golden.run("get_record", ctx, handle=handle, part=p) for p in range(2, env.meta["total"] + 1)]
        for part in parts:
            scan(part, ctx, checkpoint, f"get_record {handle}")
            check_caps(part, spec, f"get_record {handle}")
        versioned = re.search(r"; version (\d+)", env.text.split("\n")[0])
        assert versioned is None or int(versioned.group(1)) == visible[handle], handle     # the version valid at now
        assert "members" not in env.text or not handle.startswith("slack:"), handle


# -- coverage ----------------------------------------------------------------------------------------------

# What no call lists yet, by source and dataset, with the row of the build plan that will list it. This
# shrinks as tools land, and what stays must have a reason of its own.
NOT_LISTED_YET = {
    ("servicenow", "incident"): "itsm_query, row D7", ("servicenow", "sys_journal_field"): "itsm_query, row D7",
    ("servicenow", "sys_audit"): "an incident's children once itsm_query lists incidents, row D7",
    ("servicenow", "cmdb_ci"): "get_topology, row D7", ("servicenow", "cmdb_rel_ci"): "get_topology, row D7",
    ("servicenow", "sys_user"): "itsm_query, row D7", ("servicenow", "sys_user_group"): "itsm_query, row D7",
    ("dynatrace", "entities"): "get_topology, row D7",
    ("pagerduty", "services"): "get_oncall, row D7", ("pagerduty", "escalation_policies"): "get_oncall, row D7",
    ("pagerduty", "oncalls"): "get_oncall, row D7",
    (None, "status"): "check_status_page, row D3",
}


def is_pending(source: str, dataset: str) -> bool:
    return (source, dataset) in NOT_LISTED_YET or (source, None) in NOT_LISTED_YET or (None, dataset) in NOT_LISTED_YET


def every_log_record(ctx) -> set[str]:
    """Page through the logs by narrowing the window, as the footer of a truncated result says to."""
    seen: set[str] = set()
    first, last = ctx.store.conn.execute("SELECT min(t_known), max(t_known) FROM visible WHERE kind = 'log'").fetchone()
    cursor = first
    while cursor <= last:
        env = golden.run("search_logs", ctx, since=windows.iso(cursor), limit=50)
        if not env.rows:
            break
        seen |= set(env.shown)
        newest = parity.ms(env.rows[-1].t_known)
        if newest > cursor:
            cursor = newest
            continue
        for host in [h for (h,) in ctx.store.conn.execute("SELECT DISTINCT host FROM visible WHERE kind = 'log' AND t_known = ?", (cursor,))]:
            seen |= set(golden.run("search_logs", ctx, since=windows.iso(cursor), until=windows.iso(cursor + 1), host=host, limit=50).shown)
        cursor += 1
    return seen


@pytest.mark.parametrize("checkpoint,tags", ARMS)
def test_every_visible_record_can_be_reached_from_some_result(checkpoint, tags):
    """No record is ingested and then unreachable: its handle is in the rows or the referenced handles of a
    list call, or of `get_record` on a handle reached that way."""
    ctx = golden.ctx(checkpoint, tags)
    reached = every_log_record(ctx)
    for name, calls in list_calls(ctx).items():
        for args in calls:
            env = golden.run(name, ctx, **args)
            reached |= set(env.shown) | set(env.referenced)
    for env in (golden.run("read_chat", ctx, since=WIDEST, until=windows.iso(parity.ms(row.t_known) + 1))
                for row in golden.run("read_chat", ctx, since=WIDEST).rows[:1]):
        reached |= set(env.shown) | set(env.referenced)         # the messages a cut dropped, by narrowing the window
    reached |= set(inventory.build(ctx).referenced)
    frontier = set(reached)
    while frontier:             # follow pointers: a record's children and parent
        found: set[str] = set()
        for handle in frontier:
            env = golden.run("get_record", ctx, handle=handle)
            if env.status == "ok":
                found |= set(env.referenced)
        frontier = found - reached
        reached |= found
    rows = ctx.store.conn.execute("SELECT handle, source, dataset, kind FROM visible").fetchall()
    missing = [(h, s, d) for h, s, d, _ in rows if h not in reached and not is_pending(s, d)]
    assert missing == []
    pending = {(s, d) for h, s, d, _ in rows if h not in reached}
    assert len({h for h, *_ in rows} & reached) > 2000 and len(pending) <= len(NOT_LISTED_YET)
    # An incident's audit rows are among its children, and a synthetic test is referenced from its monitor's row.
    synthetics = [h for h, s, d, _ in rows if d == "synthetics"]
    assert synthetics and set(synthetics) <= reached


# -- the defects the bundle carries on purpose -------------------------------------------------------------


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_the_defects_survive_the_tools(checkpoint):
    ctx = golden.ctx(checkpoint)
    alerts = golden.run("list_alerts", ctx, since=WIDEST)
    # A duplicate delivery is one row with two handles, and both resolve.
    doubled = [row for row in alerts.rows if "  deliveries 2" in row.text]
    assert len(doubled) == 1
    second = re.search(r"  later (\S+)", doubled[0].text).group(1)
    assert golden.run("get_record", ctx, handle=doubled[0].handle).status == golden.run("get_record", ctx, handle=second).status == "ok"
    # An alert that fires and resolves keeps both transitions.
    recovered = [row for row in alerts.rows if "state Recovered" in row.text]
    assert recovered and all(int(re.search(r"transitions (\d+)", row.text).group(1)) >= 2 and "  later " in row.text for row in recovered)
    # A log line the forwarder cut is flagged where it is listed and where it is fetched.
    cut = ctx.store.conn.execute("SELECT handle, host, t_known FROM visible WHERE kind = 'log' AND json_extract(flags, '$.truncated')").fetchall()
    assert cut
    for handle, host, known in cut:
        listed = golden.run("search_logs", ctx, host=host, since=windows.iso(known), until=windows.iso(known + 1))
        assert "[cut short at the source]" in next(row.text for row in listed.rows if row.handle == handle)
        assert "[this record was cut short at the source]" in golden.run("get_record", ctx, handle=handle).text
    # Hidden empty fields are counted.
    change = ctx.store.conn.execute("SELECT handle FROM visible WHERE kind = 'change'").fetchone()[0]
    assert re.search(r"\[\d+ empty fields hidden\]", golden.run("get_record", ctx, handle=change).text)
