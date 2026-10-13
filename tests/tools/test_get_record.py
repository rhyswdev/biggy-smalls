"""`get_record` (build row B4; section 6.4, K4, K8 and ruling G7 of docs/decisions/tools-agent-build.md):
each versioned source returns the version the manifest says is valid at each checkpoint, and for every
visible record the parts joined hold every non-empty field it may show, in full."""
import json
import re

import pytest

import golden
import parity
import test_leak
from smalls.contracts.handles import ic_note_handle, metric_handle, query_handle
from smalls.tools import get_record, store, windows

CHECKPOINTS = list(golden.CHECKPOINTS)
EMPTY = ("", None, [], {})


def leaves(value) -> list[str]:
    """Every non-empty scalar in a parsed record, as text, one entry per line of it."""
    if isinstance(value, dict):
        return [x for v in value.values() for x in leaves(v)]
    if isinstance(value, list):
        return [x for v in value for x in leaves(v)]
    if value in EMPTY:
        return []
    text = json.dumps(value) if isinstance(value, bool) else str(value)
    return [line.strip() for line in text.split("\n") if line.strip()]


def all_parts(ctx, handle: str) -> str:
    """Every part of a record, joined."""
    first = golden.run("get_record", ctx, handle=handle)
    assert first.status == "ok", (handle, first.text)
    texts = [first.text]
    for part in range(2, first.meta["total"] + 1):
        texts.append(golden.run("get_record", ctx, handle=handle, part=part).text)
    return "\n".join(texts)


def visible_rows(ctx) -> list[dict]:
    cur = ctx.store.conn.execute("SELECT * FROM visible")
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur]


def shown_leaves(ctx, row: dict) -> list[str]:
    native = store.render_native(row["native"], row["flags"])
    try:
        parsed = json.loads(native)
    except ValueError:
        return [line.strip() for line in native.split("\n") if line.strip()]
    if not isinstance(parsed, (dict, list)):
        return [line.strip() for line in native.split("\n") if line.strip()]
    return leaves(get_record._as_of_now(parsed, ctx.store.now))


@pytest.mark.parametrize("tags", [False, True])
@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_every_visible_record_is_returned_whole(checkpoint, tags):
    """G7: nothing a record holds is unreachable."""
    ctx = golden.ctx(checkpoint, tags=tags)
    rows = visible_rows(ctx)
    assert len(rows) > 2000
    for row in rows:
        text = all_parts(ctx, row["handle"])
        lines = {line.strip() for line in text.split("\n")}
        joined = " ".join(lines)
        for leaf in shown_leaves(ctx, row):
            assert leaf in lines or leaf in joined, (row["handle"], leaf[:80])
        assert text.startswith(row["handle"] + "  ")


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_each_versioned_source_returns_the_version_the_manifest_says_is_valid(checkpoint):
    ctx = golden.ctx(checkpoint)
    now = ctx.store.now
    by_handle: dict[str, list[dict]] = {}
    for row in golden.manifest():
        if row["valid_from"] or row["valid_to"]:
            by_handle.setdefault(row["handle"], []).append(row)
    sources = set()
    for handle, versions in by_handle.items():
        valid = [v for v in versions if v["path"].endswith(".json") and parity.visible_at(v, now)]
        if not valid or str(valid[0]["locator"]) != "/":
            continue                                                               # a commit rides with its build's versions
        assert len(valid) == 1
        text = all_parts(ctx, handle)
        served = json.loads(golden.served(valid[0]["path"]))
        withheld = {"status"} if valid[0]["path"].startswith("pagerduty/services") else set()
        for leaf in leaves(get_record._as_of_now(served, now)):
            assert leaf in text or leaf in withheld, (handle, valid[0]["path"], leaf[:60])
        version = re.search(r"; version (\d+)", text.split("\n")[0])
        others = [v for v in versions if v["path"] != valid[0]["path"] and v["path"].endswith(".json")]
        if others:
            assert version is not None
        sources.add(valid[0]["path"].split("/")[0])
        later = [v for v in others if parity.ms(v["t_known"]) >= now]
        for v in later:                                                            # nothing of a version not yet known
            newer = set(leaves(json.loads(golden.served(v["path"])))) - set(leaves(served))
            times = [leaf for leaf in newer if test_leak.times_in(leaf, test_leak.UTC) and max(test_leak.times_in(leaf, test_leak.UTC)) >= now]
            assert not [leaf for leaf in times if leaf in text], (handle, v["path"])
    assert {"dynatrace", "jenkins", "pagerduty", "servicenow"} <= sources


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_a_handle_that_is_not_yet_visible_reads_byte_for_byte_like_an_unknown_one(checkpoint):
    ctx = golden.ctx(checkpoint)
    every = {h for (h,) in ctx.store.conn.execute("SELECT DISTINCT handle FROM record")}
    hidden = every - {h for (h,) in ctx.store.conn.execute("SELECT handle FROM visible")}
    assert len(hidden) > 100
    unknown = golden.run("get_record", ctx, handle="conf:NO-SUCH-PAGE")
    assert unknown.status == "error" and unknown.text == "conf:NO-SUCH-PAGE: not in this bundle." and unknown.rows == ()
    for handle in hidden:
        env = golden.run("get_record", ctx, handle=handle)
        assert env.text.replace(handle, "H") == unknown.text.replace("conf:NO-SUCH-PAGE", "H") and env.as_dict() | {"text": ""} == unknown.as_dict() | {"text": ""}


def test_k8_handles_that_hold_no_record():
    ctx = golden.ctx()
    assert golden.run("get_record", ctx, handle="INC0048811").text.startswith("INC0048811: not a handle.")
    assert golden.run("get_record", ctx, handle="").meta["error"] == "malformed_handle"
    for handle, words in ((query_handle("x", "y"), "Re-run the search that printed it"),
                          (metric_handle("avg:system.load.1{*}", "1400", "1452"), "Call query_metrics with the same metric, scope and window"),
                          (ic_note_handle("run", "t", "note"), "The note's text is in the result that printed it")):
        env = golden.run("get_record", ctx, handle=handle)
        assert env.status == "error" and env.meta["error"] == "minted" and words in env.text


def test_a_record_larger_than_the_cap_comes_in_parts_and_nothing_is_dropped(monkeypatch):
    ctx = golden.ctx()
    handle, native = ctx.store.conn.execute("SELECT handle, native FROM visible ORDER BY length(native) DESC LIMIT 1").fetchone()
    whole = golden.run("get_record", ctx, handle=handle)
    assert whole.meta["total"] == 1 and "part 1 of" not in whole.text
    monkeypatch.setattr(get_record, "CAP_TOKENS", 300)
    first = golden.run("get_record", ctx, handle=handle)
    n = first.meta["total"]
    assert n > 3 and f"part 1 of {n}" in first.text.split("\n")[0] and first.text.split("\n")[-1].startswith(f"[part 1 of {n}: get_record with part=2")
    parts = [first] + [golden.run("get_record", ctx, handle=handle, part=p) for p in range(2, n + 1)]
    body = []
    for i, part in enumerate(parts, 1):
        assert part.status == "ok" and len(part.text) // 4 <= 300 and part.shown == (handle,) and part.text.startswith(handle + "  ")
        assert f"part {i} of {n}" in part.text.split("\n")[0]
        lines = part.text.split("\n")[1:]
        body += lines[:-1] if i < n else lines
    assert "\n".join(body) == "\n".join(whole.text.split("\n")[1:])                 # the parts joined are the whole record
    assert "[part " not in parts[-1].text.split("\n")[-1]
    past = golden.run("get_record", ctx, handle=handle, part=n + 1)
    assert past.status == "error" and past.text == f"{handle} has {n} parts; part {n + 1} does not exist."
    assert golden.run("get_record", ctx, handle=handle, part=0).meta["error"] == "arguments"


def test_a_single_line_longer_than_a_part_is_split_not_dropped():
    line = "x" * 950 + "END"
    pieces = get_record.split(["head", line, "tail"], 300)
    assert "".join(chunk for piece in pieces for chunk in piece if chunk not in ("head", "tail")) == line
    assert all(sum(len(c) + 1 for c in piece) <= 301 for piece in pieces) and pieces[0][0] == "head" and pieces[-1][-1] == "tail"


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_empty_fields_are_hidden_and_counted(checkpoint):
    ctx = golden.ctx(checkpoint)

    def empties(value) -> int:
        if isinstance(value, dict):
            return sum(empties(v) for v in value.values())
        if isinstance(value, list):
            return sum(empties(v) for v in value) if value else 1
        return 1 if value in EMPTY else 0

    checked = 0
    for row in visible_rows(ctx):
        if row["kind"] not in ("incident", "change", "ci", "user", "alert_def", "build"):
            continue
        parsed = json.loads(store.render_native(row["native"], row["flags"]))
        want = empties(get_record._as_of_now(parsed, ctx.store.now))
        text = golden.run("get_record", ctx, handle=row["handle"]).text
        found = re.search(r"\[(\d+) empty fields hidden\]", text)
        assert (int(found.group(1)) if found else 0) == want, row["handle"]
        checked += want > 0
    assert checked > 20


# -- values derived at now (K4) ----------------------------------------------------------------------------


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_a_monitors_state_is_derived_from_its_visible_transitions(checkpoint):
    ctx = golden.ctx(checkpoint)
    monitors = [h for (h,) in ctx.store.conn.execute("SELECT handle FROM visible WHERE kind = 'alert_def' AND json_extract(flags, '$.withheld') IS NOT NULL")]
    assert monitors
    for handle in monitors:
        text = golden.run("get_record", ctx, handle=handle).text
        events = ctx.store.conn.execute("SELECT stream_key, status, sev, t_known FROM visible WHERE kind = 'alert' AND parent = ? ORDER BY t_known, rid", (handle,)).fetchall()
        last = {}
        for key, status, sev, known in events:
            last[key] = (status, sev, known)
        assert "overall_state" not in text
        if not last:
            assert "derived at now: no transition seen, so no state is claimed" in text
            continue
        worst = min(last.values(), key=lambda v: v[1] if v[1] is not None else 99)
        assert f"derived at now: state {worst[0]} (the worst of {len(last)} group" in text
        for key, (status, _, known) in last.items():
            assert f"derived at now: group {key}: {status} since {windows.iso(known)}" in text
    quiet = golden.ctx("12:00")
    silent = [h for h in monitors if not quiet.store.conn.execute("SELECT count(*) FROM visible WHERE parent = ?", (h,)).fetchone()[0]]
    assert silent and "no transition seen" in golden.run("get_record", quiet, handle=silent[0]).text


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_a_paging_services_status_is_derived_from_the_incident_versions_valid_at_now(checkpoint):
    ctx = golden.ctx(checkpoint)
    services = ctx.store.conn.execute("SELECT handle, native_id, native FROM visible WHERE kind = 'config' AND json_extract(flags, '$.withheld') IS NOT NULL").fetchall()
    assert services
    differs = 0
    for handle, service_id, native in services:
        open_states = [s for (s,) in ctx.store.conn.execute("SELECT status FROM visible WHERE kind = 'incident' AND json_extract(attrs, '$.service_id') = ?", (service_id,))]
        want = "critical" if "triggered" in open_states else "warning" if "acknowledged" in open_states else "active"
        text = golden.run("get_record", ctx, handle=handle).text
        assert f"derived at now: status {want} (" in text
        assert not re.search(r"^status: ", text, re.M)                              # the served status is withheld
        differs += json.loads(native)["status"] != want
    assert differs > 0                                                              # the served status would have been wrong


def test_a_thread_summary_and_a_network_monitor_alert_are_derived_at_now():
    ctx = golden.ctx()
    root, n = ctx.store.conn.execute("SELECT parent, count(*) FROM visible WHERE kind = 'message' AND parent IS NOT NULL GROUP BY 1 ORDER BY 2 DESC").fetchone()
    env = golden.run("get_record", ctx, handle=root)
    assert f"derived at now: thread has {n} replies by " in env.text and "reply_count" not in env.text
    replies = [h for (h,) in ctx.store.conn.execute("SELECT handle FROM visible WHERE parent = ?", (root,))]
    assert set(replies) == set(env.referenced) and "children (message): " in env.text
    handle, triggered, ended = ctx.store.conn.execute("SELECT handle, t_event, t_end FROM record WHERE source = 'solarwinds' AND t_end IS NOT NULL").fetchone()
    before, after = golden.ctx(windows.iso(ended - 60_000)), golden.ctx(windows.iso(ended + 60_000))
    assert "derived at now: state active" in golden.run("get_record", before, handle=handle).text
    assert windows.iso(ended)[:16] not in golden.run("get_record", before, handle=handle).text and "ResetDateTime" not in golden.run("get_record", before, handle=handle).text
    assert f"derived at now: state reset at {windows.iso(ended)}" in golden.run("get_record", after, handle=handle).text


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_a_problems_evidence_count_is_the_items_that_had_started(checkpoint):
    ctx = golden.ctx(checkpoint)
    problems = ctx.store.conn.execute("SELECT handle, native FROM visible WHERE dataset = 'problems'").fetchall()
    assert problems
    for handle, native in problems:
        details = json.loads(native)["evidenceDetails"]["details"]
        started = [e for e in details if e["startTime"] < ctx.store.now]
        assert f"evidenceDetails.totalCount: {len(started)}" in golden.run("get_record", ctx, handle=handle).text
    doc = {"evidenceDetails": {"totalCount": 3, "details": [{"startTime": 10, "n": "a"}, {"startTime": 99, "n": "b"}, {"startTime": 50, "n": "c"}]}}
    assert get_record._as_of_now(doc, 50) == {"evidenceDetails": {"totalCount": 1, "details": [{"startTime": 10, "n": "a"}]}}


# -- children and pointers ---------------------------------------------------------------------------------


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_children_are_pointers_read_from_the_links_inside_one_source(checkpoint):
    ctx = golden.ctx(checkpoint)
    parents = ctx.store.conn.execute("SELECT p.handle, p.kind, group_concat(c.handle, ' '), group_concat(DISTINCT c.kind) FROM visible c "
                                     "JOIN visible p ON p.handle = c.parent GROUP BY 1").fetchall()
    kinds = set()
    for handle, kind, children, child_kinds in parents:
        env = golden.run("get_record", ctx, handle=handle)
        assert set(children.split(" ")) <= set(env.referenced) and not set(children.split(" ")) & set(env.shown), handle
        assert all(child in env.text for child in children.split(" "))
        kinds |= {(kind, k) for k in child_kinds.split(",")}
        child = children.split(" ")[0]
        assert f"parent: {handle}" in golden.run("get_record", ctx, handle=child).text
    assert {("incident", "note"), ("incident", "audit"), ("incident", "event"), ("build", "commit"), ("message", "message"), ("alert_def", "alert")} <= kinds


def test_a_saved_search_returns_its_definition_and_the_alerts_it_fired_and_is_never_run():
    ctx = golden.ctx()
    fired = ctx.store.conn.execute("SELECT parent, stream_key, handle, t_known FROM visible WHERE kind = 'alert' AND parent LIKE 'spl:search/%' ORDER BY t_known").fetchall()
    assert fired
    by_search: dict[str, dict[str, list]] = {}
    for search, sid, handle, known in fired:
        by_search.setdefault(search, {}).setdefault(sid, []).append((handle, known))
    for search, sids in by_search.items():
        env = golden.run("get_record", ctx, handle=search)
        definition = json.loads(ctx.store.conn.execute("SELECT native FROM visible WHERE handle = ?", (search,)).fetchone()[0])
        assert f"search: {definition['search']}" in env.text.replace("\n    ", " ") or definition["search"].split("\n")[0] in env.text
        for sid, deliveries in sids.items():
            line = f"fired {sid}: " + ", ".join(f"{h} received {windows.iso(k)}" for h, k in deliveries)
            assert line in env.text
        assert {h for d in sids.values() for h, _ in d} == set(env.referenced)
    assert any(len(d) == 2 for sids in by_search.values() for d in sids.values())        # the duplicate delivery, collapsed by its search id
    quiet = [h for (h,) in ctx.store.conn.execute("SELECT handle FROM visible WHERE handle LIKE 'spl:search/%'") if h not in by_search]
    assert quiet and "fired " not in golden.run("get_record", ctx, handle=quiet[0]).text


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_a_scheduled_time_is_printed_with_the_word_planned_and_nothing_else_lies_after_now(checkpoint):
    ctx = golden.ctx(checkpoint, tags=True)
    now = ctx.store.now
    tokens = test_leak.SCENARIO["future_tokens"][checkpoint]
    day0 = parity.ms(f"{golden.DAY}T00:00:00Z")
    planned = 0
    for row in visible_rows(ctx):
        text = all_parts(ctx, row["handle"])
        zone = ctx.store.integration.zone(row["source"])
        for line in text.split("\n"):
            if line.endswith("(planned)"):
                planned += 1
                assert line.split(":")[0] in ("start_date", "end_date", "end") or line.split(":")[0].endswith(".end")
                continue
            assert max(test_leak.times_in(line, zone), default=0) < now or line.startswith(row["handle"] + "  "), (row["handle"], line[:120])
        assert max(test_leak.times_in(text.split("\n")[0].split("; known")[0], test_leak.UTC), default=0) <= now
        if row["t_event"] >= day0:
            assert not [tok for tok in tokens if tok in text], row["handle"]
        assert "valid_to" not in text
    assert planned > 5


def test_tags_show_on_the_first_line_only_when_tags_are_on():
    off, on = golden.ctx(tags=False), golden.ctx(tags=True)
    handle = on.store.conn.execute("SELECT handle FROM visible WHERE kind = 'change' AND tags != '{}'").fetchone()[0]
    assert "[tag change_kind=" in golden.run("get_record", on, handle=handle).text.split("\n")[0]
    plain = golden.run("get_record", off, handle=handle).text
    assert "[tag " not in plain and re.sub(r" \[tag [a-z_]+=[a-z_,]+\]", "", golden.run("get_record", on, handle=handle).text) == plain
