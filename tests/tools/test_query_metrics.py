"""`query_metrics` and `check_status_page` (build row D3; section 6.4 and K3 of docs/decisions/tools-agent-build.md).
Acceptance: no point at or after `now`; the envelope's end is the last visible point; two served queries
that differ only in aggregator are told apart; each checkpoint returns the status snapshot valid then."""
import json
import math
import re

import pytest

import golden
import parity
import test_leak
from smalls.contracts import handles
from smalls.tools import check_status_page, derive, query_metrics, schemas, windows

CHECKPOINTS = list(golden.CHECKPOINTS)
WIDEST = "-30d"


def scope_of(q: dict) -> str:
    return ",".join(sorted(q["scope"]))


def served_points(q: dict, now: int) -> dict[str, list[tuple[int, float | None]]]:
    """Each series' points as the vendor served them, cut to the ones whose interval had closed before now."""
    out = {}
    for s in json.loads(golden.served(q["path"]))["series"]:
        closed = [(int(t), v) for t, v in s["pointlist"] if int(t) + s["interval"] * 1000 < now]
        if closed:
            out[s["expression"]] = closed
    return out


def number(text: str) -> float | None:
    return None if text == "null" else float(text)


def printed(env, series_name: str) -> list[tuple[str, float | None]]:
    """The (label, value) pairs printed for one series."""
    lines = env.text.splitlines()
    start = next(n for n, line in enumerate(lines) if line.startswith(f"series {series_name}: "))
    points = next(line for line in lines[start + 1:] if re.match(r"  (\d\d-\d\dT)?\d\d:\d\d(:\d\d)? ", line))
    return [(label, number(value)) for label, value in (cell.split(" ") for cell in points.strip().split("  "))]


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_no_point_at_or_after_now_and_the_end_is_the_last_visible_point(checkpoint):
    ctx = golden.ctx(checkpoint)
    now = ctx.store.now
    served = derive.metric_queries(ctx)
    assert len(served) >= 5
    for q in served:
        env = golden.run("query_metrics", ctx, metric=q["metric"], scope=scope_of(q), since=WIDEST)
        assert env.status == "ok", env.text
        want = served_points(q, now)
        every = [t for points in want.values() for t, _ in points]
        assert f"visible points from {windows.iso(min(every))} to {windows.iso(max(every))}, " in env.text      # the end is the last visible point
        assert max(test_leak.times_in(env.text, test_leak.UTC)) <= now and env.window[1] == windows.iso(now)
        assert env.meta["total"] == sum(len(points) for points in want.values())                # the length is the count of visible points
        for s in q["series"]:
            assert f"series {query_metrics._name(s)}: {len(want[s['expression']])} points" in env.text
        # A point whose interval has not closed is in the served file and not in the result.
        later = [int(t) for s in json.loads(golden.served(q["path"]))["series"] for t, _ in s["pointlist"] if int(t) + s["interval"] * 1000 >= now]
        assert later and all(windows.iso(t) not in env.text for t in later if t != now)    # now itself is printed, as the window's end
        last = {label for s in q["series"] for label, _ in printed(env, query_metrics._name(s))[-1:]}
        assert all(label <= max(last) for s in q["series"] for label, _ in printed(env, query_metrics._name(s)))
        assert len(env.text) // 4 <= 2000 and len(env.rows) == 1 and env.rows[0].t_known is None


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_a_short_window_prints_the_native_points_and_keeps_the_nulls(checkpoint):
    ctx = golden.ctx(checkpoint)
    now = ctx.store.now
    nulls = 0
    for q in derive.metric_queries(ctx):
        interval = q["series"][0]["interval_s"] * 1000
        since = now - 40 * interval
        env = golden.run("query_metrics", ctx, metric=q["metric"], scope=scope_of(q), since=windows.iso(since))
        want = {name: [(t, v) for t, v in points if since <= t < now] for name, points in served_points(q, now).items()}
        if not any(want.values()):
            assert env.meta["empty"] and "No visible point of this query is in the window" in env.text
            continue
        assert "shown in buckets" not in env.text and env.meta["rollup"] is None and ".rollup(" not in env.native_query
        for s in q["series"]:
            got = printed(env, query_metrics._name(s)) if want.get(s["expression"]) else []
            assert [v for _, v in got] == [None if v is None else float(query_metrics._num(v)) for _, v in want.get(s["expression"], [])]
            nulls += sum(1 for _, v in got if v is None)
    assert nulls or checkpoint == golden.FIRST          # a served null is printed as null, never dropped or filled


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_a_long_window_is_merged_into_at_most_45_buckets_by_the_rollup(checkpoint):
    ctx = golden.ctx(checkpoint)
    now = ctx.store.now
    merged = 0
    for q in derive.metric_queries(ctx):
        want = served_points(q, now)
        every = sorted(t for points in want.values() for t, _ in points)
        interval = q["series"][0]["interval_s"] * 1000
        slots = (every[-1] - every[0]) // interval + 1
        if slots <= 45:
            continue
        merged += 1
        width = interval * math.ceil(slots / 45)
        default = q["aggr"] if q["aggr"] in ("avg", "max", "min", "sum") else "max"       # a percentile merges as its maximum
        for rollup in (None, "avg", "max", "min", "sum"):
            args = {"rollup": rollup} if rollup else {}
            env = golden.run("query_metrics", ctx, metric=q["metric"], scope=scope_of(q), since=WIDEST, **args)
            fn = rollup or default
            assert f"shown in buckets of {query_metrics._span(width)}, each the {fn} of its points" in env.text and env.meta["rollup"] == fn
            assert env.native_query.startswith(f"{q['query']}.rollup({fn}, {width // 1000}) from ")
            assert ("a total over" in env.text) == (fn == "sum")
            for s in q["series"]:
                buckets: dict[int, list] = {}
                for t, v in want[s["expression"]]:
                    buckets.setdefault(every[0] + (t - every[0]) // width * width, []).append(v)
                expect = []
                for _, values in sorted(buckets.items()):
                    real = [v for v in values if v is not None]
                    expect.append(None if not real else float(query_metrics._num(query_metrics.ROLLUPS[fn](real))))
                got = [v for _, v in printed(env, query_metrics._name(s))]
                assert got == expect and len(got) <= 45
    assert merged >= 3


def test_the_summary_is_over_the_native_points_and_names_each_run_of_nulls():
    ctx = golden.ctx()
    now = ctx.store.now
    seen_nulls = False
    for q in derive.metric_queries(ctx):
        env = golden.run("query_metrics", ctx, metric=q["metric"], scope=scope_of(q), since=WIDEST)
        lines = env.text.splitlines()
        for s in q["series"]:
            points = served_points(q, now)[s["expression"]]
            real = [v for _, v in points if v is not None]
            summary = lines[next(n for n, line in enumerate(lines) if line.startswith(f"series {query_metrics._name(s)}: ")) + 1]
            assert f"min {query_metrics._num(min(real))} at " in summary and f"  max {query_metrics._num(max(real))} at " in summary
            missing = len(points) - len(real)
            if not missing:
                assert summary.endswith("nulls none")
                continue
            seen_nulls = True
            runs = sum(1 for n, (_, v) in enumerate(points) if v is None and (n == 0 or points[n - 1][1] is not None))
            assert f"nulls {missing} in {runs} run" in summary
    assert seen_nulls


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_two_served_queries_that_differ_only_in_aggregator_are_told_apart(checkpoint):
    ctx = golden.ctx(checkpoint)
    served = derive.metric_queries(ctx)
    pairs = [(a, b) for a in served for b in served if a["name"] == b["name"] and a["scope"] == b["scope"] and a["by"] == b["by"] and a["aggr"] < b["aggr"]]
    assert pairs
    for a, b in pairs:
        first = golden.run("query_metrics", ctx, metric=a["metric"], scope=scope_of(a), since="-30m")
        second = golden.run("query_metrics", ctx, metric=b["metric"], scope=scope_of(b), since="-30m")
        assert first.status == second.status == "ok" and first.shown != second.shown
        assert handles.parse_metric_handle(first.shown[0])[0] == a["query"].replace(" ", "")
        assert handles.parse_metric_handle(second.shown[0])[0] == b["query"].replace(" ", "")
        name = query_metrics._name(a["series"][0])
        assert [v for _, v in printed(first, name)] != [v for _, v in printed(second, name)]
        assert [v for _, v in printed(first, name)] == [float(query_metrics._num(v)) for t, v in served_points(a, ctx.store.now)[a["series"][0]["expression"]]
                                                        if t >= ctx.store.now - 30 * 60_000]
        # An aggregator that is not served is not answered from its neighbour.
        other = golden.run("query_metrics", ctx, metric=f"p1:{a['name']}", scope=scope_of(a), since="-30m")
        assert other.meta["error"] == "not_in_bundle" and repr(a["metric"]) in other.text and repr(b["metric"]) in other.text


def test_a_scope_that_adds_a_group_tag_selects_that_series_and_what_matches_nothing_is_not_in_this_bundle():
    ctx = golden.ctx()
    grouped = next(q for q in derive.metric_queries(ctx) if len(q["series"]) > 1)
    whole = golden.run("query_metrics", ctx, metric=grouped["metric"], scope=scope_of(grouped), since="-30m")
    assert whole.text.count("\nseries ") == len(grouped["series"]) <= 4 and f"  by {','.join(grouped['by'])}" in whole.text
    names = [query_metrics._name(s) for s in grouped["series"]]
    assert [line.split(":")[0][7:] + ":" + line.split(":")[1] for line in whole.text.splitlines() if line.startswith("series ")] == sorted(names)   # tag order
    one = grouped["series"][-1]
    env = golden.run("query_metrics", ctx, metric=grouped["metric"], scope=f" {one['tag_set'][0]} , {scope_of(grouped)}", since="-30m")
    assert env.status == "ok" and env.text.count("\nseries ") == 1 and f"series {query_metrics._name(one)}: " in env.text
    assert env.shown == whole.shown                                                 # one handle for the query, whichever series is read
    # No match: the same sentence whether the tag, the scope or the name is wrong, with what is served under that name.
    for metric, scope in ((grouped["metric"], scope_of(grouped) + ",zz:nothing"), (grouped["metric"], "zz:nothing"), (grouped["metric"], "")):
        missing = golden.run("query_metrics", ctx, metric=metric, scope=scope, since="-30m")
        assert missing.status == "error" and missing.meta["error"] == "not_in_bundle" and missing.rows == ()
        assert missing.text.startswith(f"metric={metric!r} scope={scope!r}: not in this bundle. Visible series of {grouped['name']}: ")
        assert f"scope={scope_of(grouped)!r}" in missing.text and one["tag_set"][0] in missing.text
    unknown = golden.run("query_metrics", ctx, metric="avg:no.such.metric", scope="env:prod", since="-30m")
    assert unknown.meta["error"] == "not_in_bundle" and "No visible series has that metric name." in unknown.text and grouped["metric"] in unknown.text
    assert golden.run("query_metrics", ctx, metric=grouped["metric"], scope=scope_of(grouped), since="now").meta["error"] == "arguments"
    assert golden.run("query_metrics", ctx, metric=grouped["metric"], scope=scope_of(grouped), since="-1h", rollup="median").meta["error"] == "arguments"


def test_a_series_with_no_visible_point_is_answered_like_one_that_does_not_exist():
    late = golden.ctx()
    first = late.store.conn.execute("SELECT min(t_known) FROM visible_point").fetchone()[0]
    early = golden.ctx(windows.iso(first - 60_000))
    assert derive.metric_queries(early) == []
    q = derive.metric_queries(late)[0]
    real = golden.run("query_metrics", early, metric=q["metric"], scope=scope_of(q), since="-1h")
    made_up = golden.run("query_metrics", early, metric="avg:no.such.metric", scope="zz:nothing", since="-1h")
    assert real.status == made_up.status == "error" and "(none is visible yet)" in real.text
    assert real.text.replace(repr(q["metric"]), "M").replace(repr(scope_of(q)), "S") == made_up.text.replace("'avg:no.such.metric'", "M").replace("'zz:nothing'", "S")
    # The series identity is not a record: with a visible point it is answered with the call that reads it, and without one like any unknown handle.
    unknown = golden.run("get_record", early, handle="metric:avg:no.such.metric{zz:nothing}")
    hidden = golden.run("get_record", early, handle=q["handle"])
    assert hidden.text.replace(q["handle"], "H") == unknown.text.replace("metric:avg:no.such.metric{zz:nothing}", "H") and hidden.meta["error"] == "not_in_bundle"
    seen = golden.run("get_record", late, handle=q["handle"])
    assert seen.status == "error" and seen.meta["error"] == "series" and seen.rows == () and seen.referenced == ()
    assert f"Call query_metrics with metric={q['metric']!r}, scope={scope_of(q)!r} and a window" in seen.text


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_the_first_breach_is_the_first_native_point_past_a_matching_monitors_thresholds(checkpoint):
    ctx = golden.ctx(checkpoint)
    now = ctx.store.now
    monitors = {}
    for handle, native in ctx.store.conn.execute("SELECT handle, native FROM visible WHERE kind = 'alert_def'"):
        text = json.loads(native).get("query")
        m = re.match(r"^\w+\([^)]*\):(.+?)\s*(>=|<=|>|<)\s*(-?[\d.]+)\s*$", text) if isinstance(text, str) else None
        if m:
            monitors.setdefault(m[1].replace(" ", ""), []).append((handle, m[2], (json.loads(native).get("options") or {}).get("thresholds") or {}, float(m[3])))
    matched = unmatched = 0
    for q in derive.metric_queries(ctx):
        env = golden.run("query_metrics", ctx, metric=q["metric"], scope=scope_of(q), since=WIDEST)
        mine = monitors.get(q["query"].replace(" ", ""), [])
        assert sorted(env.referenced) == sorted(h for h, *_ in mine)                # a monitor whose threshold was used is referenced
        if not mine:
            unmatched += 1
            assert "thresholds: no visible monitor has exactly this query, so no threshold was tested" in env.text and "against " not in env.text
            continue
        matched += 1
        for handle, op, thresholds, in_query in mine:
            assert f"thresholds from {handle}  " in env.text and "is one value, not the monitor firing" in env.text
            for s in q["series"]:
                points = served_points(q, now)[s["expression"]]
                for level in ("critical", "warning"):
                    limit = thresholds.get(level, in_query if level == "critical" else None)
                    if limit is None:
                        continue
                    past = [(t, v) for t, v in points if v is not None and (v > limit if op.startswith(">") else v < limit)]
                    line = next(ln for ln in env.text.splitlines() if ln.startswith(f"  against {handle}: "))
                    want = (f"first point {op} {query_metrics._num(limit)} ({level}) at {windows.iso(past[0][0])}, value {query_metrics._num(past[0][1])}"
                            if past else f"no point {op} {query_metrics._num(limit)} ({level}) in the window")
                    assert want in line, (handle, level)
    assert matched >= 2 and unmatched >= 2          # a formula of two queries, or a monitor on another scope, is not "exactly this query"


def test_the_handle_carries_the_clamped_window_and_is_minted_for_the_call():
    ctx = golden.ctx()
    now = ctx.store.now
    q = derive.metric_queries(ctx)[0]
    env = golden.run("query_metrics", ctx, metric=q["metric"], scope=scope_of(q), since="-45m", until=windows.iso(now + 3_600_000))
    stamp = lambda ms: windows.iso(ms)[11:16].replace(":", "")
    assert env.shown == (handles.metric_handle(q["query"], stamp(now - 45 * 60_000), stamp(now)),) and env.meta["clamped"]
    assert handles.handle_class(env.shown[0]) == "metric" and env.window == (windows.iso(now - 45 * 60_000), windows.iso(now))
    assert f"The window was clamped to now ({windows.iso(now)})" in env.text and env.text.splitlines()[-1].startswith("[datadog query] " + q["query"])
    again = golden.run("get_record", ctx, handle=env.shown[0])
    assert again.meta["error"] == "minted" and "Call query_metrics with the same metric, scope and window" in again.text
    # A window whose ends fall on different dates prints both dates; the handle still carries the two clock times.
    days = golden.run("query_metrics", ctx, metric=q["metric"], scope=scope_of(q), since="-26h")
    assert re.search(r"@\d{4}-\d{4}$", days.shown[0]) and windows.iso(now - 26 * 3_600_000)[:10] in days.text and windows.iso(now)[:10] in days.text
    assert all(re.fullmatch(r"\d\d-\d\dT\d\d:\d\d", label) for label, _ in printed(days, query_metrics._name(q["series"][0])))


def test_at_most_four_series_are_shown_and_the_rest_are_named_with_their_maxima(monkeypatch):
    ctx = golden.ctx()
    q = max(derive.metric_queries(ctx), key=lambda x: len(x["series"]))
    assert len(q["series"]) == 4 and query_metrics.MAX_SERIES == 4 and query_metrics.MAX_POINTS == 45
    full = golden.run("query_metrics", ctx, metric=q["metric"], scope=scope_of(q), since="-30m")
    assert full.text.count("\nseries ") == 4 and "series shown" not in full.text
    monkeypatch.setattr(query_metrics, "MAX_SERIES", 2)
    env = golden.run("query_metrics", ctx, metric=q["metric"], scope=scope_of(q), since="-30m")
    ordered = sorted(q["series"], key=lambda s: ",".join(s["tag_set"]))
    assert [ln.split(": ")[0][7:] for ln in env.text.splitlines() if ln.startswith("series ")] == [query_metrics._name(s) for s in ordered[:2]]
    points = served_points(q, ctx.store.now)
    for s in ordered[2:]:
        peak = max(v for t, v in points[s["expression"]] if v is not None and t >= ctx.store.now - 30 * 60_000)
        assert f"{query_metrics._name(s)} max {query_metrics._num(peak)}" in env.text
    assert "[2 of 4 series shown, in tag order. Not shown, with each one's maximum in the window: " in env.text and env.meta["series"] == 2


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_a_series_shows_its_signal_label_only_with_tags_on(checkpoint):
    off, on = golden.ctx(checkpoint, tags=False), golden.ctx(checkpoint, tags=True)
    labelled = 0
    for q in derive.metric_queries(on):
        with_tags = golden.run("query_metrics", on, metric=q["metric"], scope=scope_of(q), since="-30m")
        without = golden.run("query_metrics", off, metric=q["metric"], scope=scope_of(q), since="-30m")
        assert "[tag " not in without.text and with_tags.meta["tag"] is None
        assert re.sub(r" \[tag signal=[a-z_,]+\]", "", with_tags.text) == without.text      # the label is the only difference
        for s in q["series"]:
            line = next((ln for ln in with_tags.text.splitlines() if ln.startswith(f"series {query_metrics._name(s)}: ")), None)
            if line and s["tags"].get("signal"):
                labelled += 1
                assert line.endswith(f" [tag signal={','.join(s['tags']['signal'])}]")
    assert labelled >= 5
    assert "signal" not in schemas.args_models(tags=True)["query_metrics"].model_fields          # the tool gains no tag input


# -- check_status_page -------------------------------------------------------------------------------------


def snapshots() -> list[tuple[str, int, str]]:
    """Every status snapshot in the store, oldest first: handle, the time it became known, served path."""
    ctx = golden.ctx()
    return ctx.store.conn.execute("SELECT handle, t_known, path FROM record WHERE kind = 'status' ORDER BY t_known").fetchall()


def test_each_checkpoint_and_each_later_clock_returns_the_snapshot_valid_then():
    rows = snapshots()
    assert len(rows) >= 2
    clocks = [golden.CHECKPOINTS[c] for c in CHECKPOINTS] + [windows.iso(known + 60_000) for _, known, _ in rows]
    for clock in clocks:
        ctx = golden.ctx(clock)
        now = ctx.store.now
        handle, known, path = [r for r in rows if r[1] < now][-1]                   # the latest one published before now
        listing = golden.run("check_status_page", ctx)
        provider = re.search(r"provider=(\S+)", listing.text).group(1)
        env = golden.run("check_status_page", ctx, provider=provider)
        served = json.loads(golden.served(path))
        assert env.status == "ok" and env.shown == (handle,) and env.rows[0].t_known == windows.iso(known)
        assert f"indicator {served['status']['indicator']}: {served['status']['description']}" in env.rows[0].text
        assert f"incidents ({len(served['incidents'])})" in env.text and f"components ({len(served['components'])})" in env.text
        for incident in served["incidents"]:
            assert incident["name"] in env.text and f"status {incident['status']}" in env.text
            newest = max(incident["incident_updates"], key=lambda u: u["created_at"])
            assert " ".join(newest["body"].split())[:200] in env.text                 # the newest update is always there
        for component in served["components"]:
            assert component["status"] == "operational" or f"{component['name']} {component['status']}" in env.text
        assert max(test_leak.times_in(env.text, test_leak.UTC)) < now and len(env.text) // 4 <= 500
        assert all(other not in env.text for other, *_ in rows if other != handle)  # and no other snapshot is named
        # The listing names the page and shows none of its content.
        assert listing.rows == () and listing.referenced == (handle,) and served["status"]["description"] not in listing.text
        assert json.loads(golden.served(path))["page"]["name"] in listing.text and len(listing.text) // 4 <= 500


def test_a_provider_is_matched_by_either_name_and_an_unknown_one_lists_the_known():
    ctx = golden.ctx()
    listing = golden.run("check_status_page", ctx)
    provider = re.search(r"provider=(\S+)", listing.text).group(1)
    page = json.loads(golden.served(snapshots()[0][2]))["page"]["name"]
    by_source, by_name = golden.run("check_status_page", ctx, provider=provider.upper()), golden.run("check_status_page", ctx, provider=f" {page} ")
    assert by_source.status == by_name.status == "ok" and by_source.text == by_name.text
    assert by_source.text.splitlines()[-1].startswith("[status page] GET http") and by_source.referenced == ()
    unknown = golden.run("check_status_page", ctx, provider="no-such-provider")
    assert unknown.status == "error" and unknown.meta["error"] == "unknown_value" and f"provider is one of: {provider}." in unknown.text
    before = golden.ctx(windows.iso(snapshots()[0][1] - 60_000))
    nothing = golden.run("check_status_page", before)
    assert nothing.meta["empty"] and nothing.referenced == () and "No status page has a visible snapshot." in nothing.text
    assert golden.run("check_status_page", before, provider=provider).meta["error"] == "unknown_value"


def test_a_long_page_is_shortened_inside_the_cap_and_says_so(monkeypatch):
    ctx = golden.ctx()
    listing = golden.run("check_status_page", ctx)
    provider, handle = re.search(r"provider=(\S+)", listing.text).group(1), listing.referenced[0]
    updates = [{"status": "investigating", "body": f"update {n} " + "word " * 120, "created_at": f"2020-01-01T{n:02d}:00:00.000Z"} for n in range(12)]
    page = {"page": {"name": "Some Page", "url": "https://status.example", "updated_at": "2020-01-01T12:00:00.000Z"},
            "status": {"indicator": "major", "description": "Partial System Outage"},
            "components": [{"name": "API", "status": "partial_outage", "updated_at": "2020-01-01T09:00:00.000+02:00"}, {"name": "Web", "status": "operational"}],
            "incidents": [{"name": "API errors", "impact": "major", "status": "investigating", "created_at": "2020-01-01T00:00:00.000Z",
                           "incident_updates": updates}], "scheduled_maintenances": []}
    monkeypatch.setattr(check_status_page, "render_native", lambda native, flags: json.dumps(page))
    env = golden.run("check_status_page", ctx, provider=provider)
    assert env.status == "ok" and len(env.text) // 4 <= 500 and env.shown == (handle,)
    assert "update 11 " in env.text and "update 0 " not in env.text                # the newest updates are kept
    assert re.search(r"\[…\+\d+ chars\]", env.text) and re.search(r"\[\d+ earlier updates not listed\]", env.text)
    assert f"get_record {handle} returns the whole page" in env.text
    assert "components (2): API partial_outage (since 2020-01-01T07:00:00Z (native 2020-01-01T09:00:00.000+02:00)); 1 operational" in env.text
    lines, cut = check_status_page._page({"handle": handle, "entity_raw": None, "status": None, "title": None}, {**page, "incidents": []}, "https://status.example", 3)
    assert not cut and "incidents (0): none listed" in lines and "scheduled maintenance (0): none listed" in lines
