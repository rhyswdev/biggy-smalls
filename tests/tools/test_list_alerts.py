"""`list_alerts` (build row B3; section 6.4, K2 and K4 of docs/decisions/tools-agent-build.md): a flapping
stream is one row, a duplicate delivery is one row with both handles, a stream's handle is the same at both
checkpoints, and every state is derived at `now` from visible rows."""
import json
import re
from collections import Counter

import pytest

import golden
import parity
import test_leak
from smalls.tools import windows

CHECKPOINTS = list(golden.CHECKPOINTS)
ALERT = "(kind = 'alert' OR (kind = 'incident' AND stream_key IS NOT NULL))"


def rows_of(env) -> dict[str, dict]:
    """Each row, parsed back into its cells by the labels the tool prints."""
    out = {}
    for row in env.rows:
        cells = row.text.split("  ")
        labelled = {c.split(" ", 1)[0]: c.split(" ", 1)[1] for c in cells[4:] if " " in c}
        out[row.handle] = {"source": cells[1], "key": cells[2], "title": cells[3], "text": row.text, **labelled}
    return out


def everything(ctx, **args):
    return golden.run("list_alerts", ctx, since="-30d", **args)


def visible_streams(ctx) -> dict[tuple[str, str], list[tuple]]:
    grouped: dict[tuple[str, str], list[tuple]] = {}
    for row in ctx.store.conn.execute(f"SELECT source, coalesce(stream_key, handle), handle, t_known, status, sev, kind FROM visible WHERE {ALERT} ORDER BY t_known, rid"):
        grouped.setdefault(row[:2], []).append(row[2:])
    return grouped


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_a_flapping_stream_is_one_row_with_a_count(checkpoint):
    ctx = golden.ctx(checkpoint)
    streams = visible_streams(ctx)
    (source, key), records = max(streams.items(), key=lambda kv: len(kv[1]))
    assert len(records) > 6                                                       # it really flaps
    env = everything(ctx)
    mine = [r for r in rows_of(env).values() if r["key"] == key]
    assert len(mine) == 1 and int(mine[0]["transitions"]) == len(records)
    assert mine[0]["state"] == records[-1][2] and mine[0]["last"] == windows.iso(records[-1][1])
    handle = next(h for h, r in rows_of(env).items() if r["key"] == key)
    assert handle == records[0][0]                                               # the earliest record is the row
    later = [r[0] for r in records[1:]]
    assert mine[0]["later"].split(" ") == later and set(later) <= set(env.referenced) and not set(later) & set(env.shown)


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_a_duplicate_delivery_is_one_row_with_both_handles(checkpoint):
    ctx = golden.ctx(checkpoint)
    doubled = {key: records for key, records in visible_streams(ctx).items() if len(records) == 2 and records[0][2] is None and key[1] != records[0][0]
               and all(r[4] == "alert" for r in records) and key[0].endswith("alerts") and "/" in key[0]}
    assert len(doubled) == 1
    (source, sid), records = next(iter(doubled.items()))
    row = next(r for r in rows_of(everything(ctx)).values() if r["key"] == sid)
    assert row["deliveries"] == "2" and row["state"] == "fired" and row["source"] == source
    first, second = records[0][0], records[1][0]
    assert row["text"].startswith(first + "  ") and row["later"] == second
    for handle in (first, second):                                               # both resolve to a visible record
        assert ctx.store.conn.execute("SELECT count(*) FROM visible WHERE handle = ?", (handle,)).fetchone()[0] == 1
    assert row["definition"].startswith("spl:search/") and row["definition"] in everything(ctx).referenced
    singles = [r for r in rows_of(everything(ctx)).values() if r.get("deliveries") == "1"]
    assert singles and all(r["state"] == "fired" for r in singles)


def test_a_streams_handle_is_the_same_at_both_checkpoints():
    early, late = (rows_of(everything(golden.ctx(c))) for c in CHECKPOINTS)
    by_key_early = {r["key"]: h for h, r in early.items()}
    by_key_late = {r["key"]: h for h, r in late.items()}
    shared = set(by_key_early) & set(by_key_late)
    assert len(shared) >= 10 and len(by_key_late) > len(by_key_early)
    assert all(by_key_early[key] == by_key_late[key] for key in shared)
    assert set(by_key_early) <= set(by_key_late)                                  # the visible set only grows


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_a_monitor_streams_state_is_its_last_visible_transition_never_the_served_value(checkpoint):
    ctx = golden.ctx(checkpoint)
    rows = rows_of(everything(ctx))
    served = {str(m["id"]): m["overall_state"] for m in json.loads(golden.served("datadog/monitors.json"))}
    differs = 0
    for (source, key), records in visible_streams(ctx).items():
        if records[0][2] is None or records[0][4] != "alert" or source != "datadog":
            continue
        row = next(r for r in rows.values() if r["key"] == key)
        assert row["state"] == records[-1][2] and int(row["transitions"]) == len(records)
        assert row["first"].startswith(windows.iso(records[0][1]))
        monitor = key.split(":")[1].split("|")[0]
        differs += served[monitor].lower() not in (row["state"].lower(), {"triggered": "alert", "recovered": "ok"}.get(row["state"].lower()))
    assert differs > 0                                                            # the served state would have been wrong


def test_a_network_monitor_alert_is_active_until_its_reset_and_a_later_reset_is_never_printed():
    reset = golden.ctx(golden.SECOND).store.conn.execute("SELECT handle, t_event, t_end FROM record WHERE source = 'solarwinds' AND t_end IS NOT NULL").fetchone()
    handle, triggered, ended = reset
    assert triggered < ended
    before = golden.ctx(windows.iso(ended - 60_000))                             # triggered, not yet reset
    row = rows_of(everything(before))[handle]
    assert row["state"] == "active" and row["transitions"] == "1" and row["last"] == windows.iso(triggered)
    assert windows.iso(ended)[:16] not in everything(before).text
    assert max(test_leak.times_in(everything(before).text, test_leak.UTC), default=0) < before.store.now
    after = golden.ctx(windows.iso(ended + 60_000))
    row = rows_of(everything(after))[handle]
    assert row["state"] == "reset" and row["transitions"] == "2" and row["last"] == windows.iso(ended)
    assert handle in rows_of(everything(after, state="all")) and handle not in rows_of(everything(after, state="active"))
    not_yet = golden.ctx(windows.iso(triggered - 60_000))
    assert handle not in rows_of(everything(not_yet))


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_a_problem_shows_the_version_valid_at_now(checkpoint):
    ctx = golden.ctx(checkpoint)
    rows = rows_of(everything(ctx))
    valid = ctx.store.conn.execute("SELECT handle, status, version, t_end FROM visible WHERE source = 'dynatrace' AND kind = 'alert'").fetchall()
    assert valid
    for handle, status, version, ended in valid:
        assert rows[handle]["state"] == status and int(rows[handle]["transitions"]) == version
    closed_later = ctx.store.conn.execute("SELECT handle FROM record WHERE source = 'dynatrace' AND kind = 'alert' AND status = 'CLOSED' AND t_known >= ?",
                                          (ctx.store.now,)).fetchall()
    for (handle,) in closed_later:                                                # closed after now: still open at now
        assert handle not in rows or rows[handle]["state"] == "OPEN"


def test_the_window_lists_a_stream_that_was_active_in_it_even_with_no_record_in_it():
    ctx = golden.ctx()
    now = ctx.store.now
    wide, narrow = rows_of(everything(ctx)), rows_of(golden.run("list_alerts", ctx, since="-10m"))
    since = now - 600_000
    last_seen = {}
    for key, records in visible_streams(ctx).items():
        last_seen[records[0][0]] = max(r[1] for r in records)
    quiet_but_active = [h for h in narrow if last_seen.get(h, now) < since]
    assert quiet_but_active                                                       # no new record in ten minutes, yet listed
    ended = ("Recovered", "reset", "CLOSED", "resolved")
    # Each was active at some point in the window: it still is, or it stopped being so inside the window.
    assert all(narrow[h]["state"] not in ended or parity.ms(narrow[h]["last"]) >= since for h in quiet_but_active)
    assert any(narrow[h]["state"] not in ended for h in quiet_but_active)
    gone = [h for h, r in wide.items() if h not in narrow]
    assert gone and all(last_seen.get(h, 0) < since for h in gone)


def test_filters_by_source_text_and_state():
    ctx = golden.ctx()
    wide = rows_of(everything(ctx))
    sources = Counter(r["source"] for r in wide.values())
    assert len(sources) >= 4
    for source, n in sources.items():
        assert len(everything(ctx, source=source).rows) == n
    unknown = everything(ctx, source="nagios")
    assert unknown.status == "error" and unknown.text.startswith("source='nagios' matches nothing visible. source is one of: ") and all(s in unknown.text for s in sources)
    word = next(r["title"] for r in wide.values()).split()[-1]
    hits = rows_of(everything(ctx, text=word.upper()))
    assert hits and all(word.lower() in ctx.store.conn.execute(
        f"SELECT lower(group_concat(coalesce(title, '') || ' ' || coalesce(body, ''))) FROM visible WHERE {ALERT} AND coalesce(stream_key, handle) = ?",
        (r["key"],)).fetchone()[0] for r in hits.values())
    active = rows_of(everything(ctx, state="active"))
    assert 0 < len(active) < len(wide) and not {"Recovered", "reset", "CLOSED", "resolved"} & {r["state"] for r in active.values()}
    assert everything(ctx, text="zz-no-such-alert-zz").text.startswith("0 rows. Drop text=, or widen the window.")


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_a_page_is_linked_only_when_its_own_key_names_the_streams_id(checkpoint):
    ctx = golden.ctx(checkpoint)
    env = everything(ctx)
    rows = rows_of(env)
    pages = dict(ctx.store.conn.execute("SELECT handle, stream_key FROM visible WHERE kind = 'incident' AND stream_key IS NOT NULL"))
    linked = {h: r["paged"].split(" ") for h, r in rows.items() if "paged" in r}
    assert pages and linked
    for handle, page_handles in linked.items():
        stream_id = rows[handle]["key"].split(":", 1)[1].split("|")[0]
        for page in page_handles:
            assert re.search(rf"(?<![0-9A-Za-z]){re.escape(stream_id)}(?![0-9A-Za-z])", pages[page])     # the served key names it
            assert page in env.referenced and page not in env.shown
        group = rows[handle]["key"].split("|", 1)[1] if "|" in rows[handle]["key"] else None
        assert group is None or all(group in pages[page] for page in page_handles)
    claimed = {p for ps in linked.values() for p in ps}
    assert claimed | {h for h in rows if h in pages} == set(pages)               # every page is a pointer or a row, never lost
    assert not claimed & set(rows)


def test_a_synthetic_test_is_referenced_from_its_monitors_row():
    ctx = golden.ctx()
    env = everything(ctx)
    synthetics = dict(ctx.store.conn.execute("SELECT handle, stream_key FROM visible WHERE kind = 'alert_def' AND dataset = 'synthetics'"))
    assert synthetics
    rows = rows_of(env)
    for handle, key in synthetics.items():
        row = next(r for r in rows.values() if r["key"] == key)
        assert handle in row["definition"].split(" ") and handle in env.referenced


def test_a_definition_with_no_visible_transition_is_not_a_row_and_the_footer_counts_them():
    ctx = golden.ctx("12:00")
    env = everything(ctx)
    fired = {p for (p,) in ctx.store.conn.execute("SELECT DISTINCT parent FROM visible WHERE kind = 'alert' AND parent IS NOT NULL")}
    silent = [h for (h,) in ctx.store.conn.execute("SELECT handle FROM visible WHERE kind = 'alert_def' AND stream_key IS NOT NULL") if h not in fired]
    assert len(silent) > 3
    footer = next(line for line in env.text.splitlines() if "have no visible transition" in line)
    assert footer.startswith(f"[{len(silent)} alert definitions have no visible transition and are not rows, since nothing says they have fired: ")
    # They are named, so each can be fetched, but none is shown: a definition that has not fired is not an alert.
    assert all(h in footer for h in silent) and set(silent) <= set(env.referenced) and not set(silent) & set(env.shown)


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_rows_are_ordered_by_first_seen_and_stay_inside_the_caps(checkpoint):
    ctx = golden.ctx(checkpoint, tags=True)
    env = everything(ctx)
    firsts = [parity.ms(r["first"].split(" ")[0]) for r in rows_of(env).values()]
    assert firsts == sorted(firsts) and len(env.rows) <= 40 and len(env.text) // 4 <= 6000
    assert all(row.text.startswith(row.handle + "  ") for row in env.rows)
    now = ctx.store.now
    assert max(test_leak.times_in(json.dumps(env.as_dict()), test_leak.UTC), default=0) <= now
    assert not [tok for tok in test_leak.SCENARIO["future_tokens"][checkpoint] if tok in json.dumps(env.as_dict(), ensure_ascii=False)]
    assert "overall_state" not in env.text and "valid_to" not in env.text


# -- tags (section 6.3) ------------------------------------------------------------------------------------


def test_tags_label_a_stream_filter_it_and_say_what_the_filter_left_out():
    off, on = golden.ctx(tags=False), golden.ctx(tags=True)
    plain, tagged = everything(off), everything(on)
    assert "[tag " not in plain.text and "[tag signal=" in tagged.text
    assert re.sub(r" \[tag [a-z_]+=[a-z_,]+\]", "", tagged.text) == plain.text and tagged.shown == plain.shown
    assert everything(off, signal="errors").meta["error"] == "arguments"
    labels = {h: (re.search(r"\[tag signal=([a-z,]+)\]", r["text"]) or [None, ""])[1].split(",") for h, r in rows_of(tagged).items()}
    labels = {h: [v for v in values if v] for h, values in labels.items()}
    untagged = [h for h, values in labels.items() if not values]
    assert untagged and any(values for values in labels.values())
    for value in ("saturation", "latency", "untagged"):
        env = everything(on, signal=value)
        want = {h for h, values in labels.items() if (not values if value == "untagged" else value in values)}
        assert set(env.shown) == want and env.meta["tag"] == f"signal={value}"
        assert (f"[tag filter signal={value}: {len(want)} of the {len(labels)} streams that match without it. {len(untagged)} of those "
                f"{len(labels)} carry no signal tag, and a missing tag says nothing about a stream]") in env.text
    nothing = everything(on, signal="security")
    assert nothing.rows == () and "[tag filter signal=security: 0 of the" in nothing.text      # nothing tagged is not nothing there
