"""`list_changes` (build row B3; section 6.4 and K6 of docs/decisions/tools-agent-build.md): golden on the set
in a window, a building version shows no result or duration, and a planned change is listed, not lost."""
import json
import re

import pytest

import golden
import parity
import test_leak
from smalls.tools import windows

CHECKPOINTS = list(golden.CHECKPOINTS)


def everything(ctx, **args):
    return golden.run("list_changes", ctx, since="-30d", **args)


def served_set(now: int) -> set[str]:
    """The builds and change requests the manifest says are visible: a build handle has no commit part."""
    rows = golden.visible(now, "jenkins/") + golden.visible(now, "servicenow/change_request.json")
    return {r["handle"] for r in rows if r["handle"].startswith("snow:CHG") or re.fullmatch(r"jk:[a-z0-9-]+#\d+", r["handle"])}


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_golden_set_in_the_widest_window(checkpoint):
    ctx = golden.ctx(checkpoint)
    env = everything(ctx)
    want = served_set(ctx.store.now)
    assert set(env.shown) == want and len(env.rows) == len(want) <= 30
    assert all(row.text.startswith(row.handle + "  ") for row in env.rows)
    kinds = {row.handle: row.text.split("  ")[1] for row in env.rows}
    assert {h: k for h, k in kinds.items()} == {h: ("change" if h.startswith("snow:") else "build") for h in want}


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_a_change_is_listed_when_its_span_overlaps_the_window(checkpoint):
    ctx = golden.ctx(checkpoint)
    now = ctx.store.now
    since = now - 2 * 3_600_000
    spans = ctx.store.conn.execute("SELECT handle, t_event, t_end FROM visible WHERE kind IN ('change', 'build')").fetchall()
    want = {h for h, start, end in spans if (start < now and (end or now) > since) or start >= now}
    env = golden.run("list_changes", ctx, since="-2h")
    assert set(env.shown) == want and 0 < len(want) < len(spans)
    starts = dict((h, s) for h, s, _ in spans)
    assert [starts[h] for h in golden.handles(env)] == sorted(starts[h] for h in golden.handles(env))     # ordered by start


def test_a_build_that_is_still_running_shows_no_result_and_no_duration():
    for checkpoint in CHECKPOINTS:
        ctx = golden.ctx(checkpoint)
        running = [h for (h,) in ctx.store.conn.execute("SELECT handle FROM visible WHERE kind = 'build' AND status = 'BUILDING'")]
        if running:
            break
    assert running
    rows = {row.handle: row.text for row in everything(ctx).rows}
    for handle in running:
        assert "end (still running)" in rows[handle] and "state BUILDING" in rows[handle]
        assert not re.search(r"SUCCESS|FAILURE|ABORTED|UNSTABLE|duration", rows[handle])
        # The finished version exists in the served files, and none of it shows before it is known.
        finished = ctx.store.conn.execute("SELECT t_end, status FROM record WHERE handle = ? AND status != 'BUILDING'", (handle,)).fetchone()
        assert finished and finished[0] >= ctx.store.now and windows.iso(finished[0])[:16] not in rows[handle]
        later = golden.ctx(windows.iso(finished[0] + 60_000))
        after = {row.handle: row.text for row in everything(later).rows}[handle]
        assert f"end {windows.iso(finished[0])}" in after and f"state {finished[1]}" in after


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_a_planned_change_is_listed_and_marked_and_an_explicit_until_counts_it(checkpoint):
    ctx = golden.ctx(checkpoint)
    now = ctx.store.now
    planned = [h for (h,) in ctx.store.conn.execute("SELECT handle FROM visible WHERE kind = 'change' AND t_event >= ?", (now,))]
    assert planned
    rows = {row.handle: row.text for row in golden.run("list_changes", ctx, since="-1h").rows}
    for handle in planned:
        assert handle in rows and "  planned start " in rows[handle]
        assert not re.search(r"(?<!planned )start \d", rows[handle]) and not re.search(r"(?<!planned )end \d", rows[handle])
    assert golden.handles(golden.run("list_changes", ctx, since="-1h"))[-len(planned):] == sorted(planned, key=golden.handles(everything(ctx)).index)
    bounded = golden.run("list_changes", ctx, since="-1h", until="now")
    assert not set(planned) & set(bounded.shown)
    assert f"[{len(planned)} planned changes start after now and are not listed: leave until out to list them]" in bounded.text


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_only_a_planned_time_may_lie_after_now(checkpoint):
    ctx = golden.ctx(checkpoint, tags=True)
    env = everything(ctx)
    now = ctx.store.now
    for row in env.rows:
        kept = re.sub(r"planned (start|end) \S+( \(native [^)]*\))?", "", row.text)
        assert max(test_leak.times_in(kept, test_leak.UTC), default=0) < now, row.handle
    assert not [tok for tok in test_leak.SCENARIO["future_tokens"][checkpoint] if tok in json.dumps(env.as_dict(), ensure_ascii=False)]
    assert "valid_to" not in env.text


def test_rows_are_side_by_side_never_joined_and_commits_are_only_referenced():
    ctx = golden.ctx()
    env = everything(ctx)
    for row in env.rows:
        other = [h for h in env.shown if h != row.handle and h in row.text]
        assert not other, row.handle                                             # no row names another row
    commits = {h: p for h, p in ctx.store.conn.execute("SELECT handle, parent FROM visible WHERE kind = 'commit'")}
    assert commits and set(env.referenced) == set(commits) and not set(commits) & set(env.shown)
    texts = {row.handle: row.text for row in env.rows}
    assert all(f"commits " in texts[parent] and handle in texts[parent] for handle, parent in commits.items())
    assert env.text.splitlines()[-1].startswith("[native query] (no single native query")


def test_a_change_requests_times_are_utc_with_the_native_value_beside_them():
    ctx = golden.ctx()
    served = {c["number"]: c for c in json.loads(golden.served("servicenow/change_request.json"))["result"]}
    rows = {row.handle: row.text for row in everything(ctx).rows if row.handle.startswith("snow:")}
    checked = 0
    for handle, text in rows.items():
        change = served[handle.split(":")[1]]
        if change.get("work_start"):
            utc = windows.iso(ctx.store.integration.parse("servicenow", change["work_start"])[0])
            assert f"  start {utc} (native {change['work_start']})" in text
            checked += 1
        assert f"requester {change['requested_by']}" in text and f"state {change['state']}" in text      # the served state, no rewind
    assert checked > 3


def test_text_filters_title_and_body_and_an_empty_result_says_what_to_drop():
    ctx = golden.ctx()
    word = everything(ctx).rows[0].text.split("  ")[4].split()[0]
    hits = everything(ctx, text=word.upper())
    assert 0 < len(hits.rows) <= len(everything(ctx).rows)
    for handle in hits.shown:
        title, body = ctx.store.conn.execute("SELECT coalesce(title, ''), coalesce(body, '') FROM visible WHERE handle = ?", (handle,)).fetchone()
        assert word.lower() in f"{title}\n{body}".lower()
    assert everything(ctx, text="zz-no-such-change-zz").text.startswith("0 rows. Drop text=, or widen the window.")


def test_tags_label_a_change_filter_it_and_say_what_the_filter_left_out():
    off, on = golden.ctx(tags=False), golden.ctx(tags=True)
    plain, tagged = everything(off), everything(on)
    assert "[tag " not in plain.text and "[tag change_kind=" in tagged.text
    assert re.sub(r" \[tag [a-z_]+=[a-z_,]+\]", "", tagged.text) == plain.text and tagged.shown == plain.shown
    assert everything(off, change_kind="release").meta["error"] == "arguments"
    kinds = {h: json.loads(t).get("change_kind") for h, t in on.store.conn.execute("SELECT handle, tags FROM visible WHERE kind IN ('change', 'build')")}
    untagged = [h for h, kind in kinds.items() if not kind]
    assert untagged
    for value in ("release", "untagged"):
        env = everything(on, change_kind=value)
        want = {h for h, kind in kinds.items() if (not kind if value == "untagged" else kind == value)}
        assert set(env.shown) == want and want
        assert (f"[tag filter change_kind={value}: {len(want)} of the {len(kinds)} changes that match without it. {len(untagged)} of those "
                f"{len(kinds)} carry no change_kind tag, and a missing tag says nothing about a change]") in env.text
        assert all(("[tag change_kind=" not in row.text) if value == "untagged" else (f"[tag change_kind={value}]" in row.text) for row in env.rows)
    assert "[tag filter change_kind=data: 0 of the" in everything(on, change_kind="data").text
