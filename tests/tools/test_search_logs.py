"""`search_logs` (build row B2; section 6.4 of docs/decisions/tools-agent-build.md): golden on the first row
and the total per filter, the sampled label, no sampling class name in any output, and the newest buckets
never cut. Expected values are computed from the served log files and the manifest's handle, path, locator
and time columns."""
import json
import re
import shutil
from datetime import datetime, timezone
from functools import lru_cache

import pytest
import yaml

import golden
import parity
import test_leak
from smalls.contracts.handles import is_handle
from smalls.tools import store, windows
from smalls.tools.registry import ToolContext
from smalls.tools.ingest.tagging import profiles

INTEGRATION = yaml.safe_load(golden.served("integration.yaml"))
LOG_SOURCES = {key: cfg for key, cfg in INTEGRATION["sources"].items() if "break_only_before" in cfg}
SOURCETYPE = {key: cfg["sourcetype"] for key, cfg in LOG_SOURCES.items()}
CLASS_NAMES = sorted({c["name"] for block in INTEGRATION["sampling"].values() for c in block.get("classes", [])})
CHECKPOINTS = list(golden.CHECKPOINTS)


@lru_cache(maxsize=None)
def served_records() -> tuple[dict, ...]:
    """Every log record in the served files: its manifest row, its source folder and its full text, which
    runs from its own first line to the line before the next record's."""
    by_path: dict[str, list[dict]] = {}
    for row in golden.manifest():
        folder = next((key for key in LOG_SOURCES if row["path"].startswith(key + "/")), None)
        if folder:
            by_path.setdefault(row["path"], []).append({**row, "folder": folder})
    out = []
    for path, rows in by_path.items():
        lines = golden.served(path).split("\n")
        rows.sort(key=lambda r: int(r["locator"]))
        for row, nxt in zip(rows, rows[1:] + [None]):
            end = int(nxt["locator"]) - 1 if nxt else len(lines)
            out.append({**row, "text": "\n".join(lines[int(row["locator"]) - 1:end]).rstrip("\n"), "known": parity.ms(row["t_known"]),
                        "event": parity.ms(row["t_utc"])})
    return tuple(out)


def expected(now: int, since: int, until: int | None = None, *, sourcetype=None, stem=None, terms=()) -> list[dict]:
    """The served records a search must return, oldest first."""
    until = min(until or now, now)
    hits = [r for r in served_records() if r["known"] < now and r["event"] < until and r["known"] >= since
            and (sourcetype is None or SOURCETYPE[r["folder"]] == sourcetype)
            and (stem is None or r["path"].rsplit("/", 1)[1].rsplit(".", 1)[0] == stem)
            and all(term.lower() in r["text"].lower() for term in terms)]
    return sorted(hits, key=lambda r: r["known"])


def total_of(env) -> int:
    return int(re.match(r"(\d+) log records", env.text).group(1))


FILTERS = [
    ({"since": "-30m"}, {}),
    ({"since": "-2h", "sourcetype": "postgresql"}, {"sourcetype": "postgresql"}),
    ({"since": "-2h", "host": "pay-02"}, {"stem": "pay-02"}),
    ({"since": "-1h", "terms": ["error"]}, {"terms": ["error"]}),
    ({"since": "-1h", "terms": ["ERROR", "timed out"]}, {"terms": ["ERROR", "timed out"]}),
    ({"since": "-50m", "until": "-10m", "sourcetype": "nginx:plus:access", "terms": ["POST"]}, {"sourcetype": "nginx:plus:access", "terms": ["POST"]}),
]
UNIT_MS = {"m": 60_000, "h": 3_600_000}


def back(now: int, relative: str) -> int:
    return now - int(relative[1:-1]) * UNIT_MS[relative[-1]]


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
@pytest.mark.parametrize("args,match", FILTERS, ids=[json.dumps(a) for a, _ in FILTERS])
def test_golden_first_row_and_total_per_filter(checkpoint, args, match):
    ctx = golden.ctx(checkpoint)
    now = ctx.store.now
    want = expected(now, back(now, args["since"]), back(now, args["until"]) if "until" in args else None, **match)
    env = golden.run("search_logs", ctx, **args)
    assert env.status == "ok" and want, args
    assert total_of(env) == env.meta["total"] == len(want)
    first_tick = [r["handle"] for r in want if r["known"] == want[0]["known"]]
    assert env.rows[0].handle in first_tick                                          # the oldest record comes first
    shown = golden.handles(env)
    # Thirty rows by default, or fewer when thirty would not fit the token cap; never a row that does not match.
    assert 0 < len(shown) <= min(30, len(want)) and set(shown) <= {r["handle"] for r in want}
    assert len(shown) == min(30, len(want)) or len(env.text) // 4 > 3500
    known = {r["handle"]: r["known"] for r in want}
    assert [known[h] for h in shown] == sorted(known[h] for h in shown)             # oldest first
    assert all(row.text.startswith(row.handle + "  ") and parity.ms(row.t_known) < now for row in env.rows)
    assert env.meta["truncated"] == (len(want) > len(shown)) and env.text.splitlines()[-1].startswith("[SPL] search ")


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_a_term_is_a_case_insensitive_substring_of_the_record_as_served(checkpoint):
    ctx = golden.ctx(checkpoint)
    upper, lower = golden.run("search_logs", ctx, since="-1h", terms=["Exception"]), golden.run("search_logs", ctx, since="-1h", terms=["exception"])
    assert upper.meta["total"] == lower.meta["total"] > 0 and golden.handles(upper) == golden.handles(lower)
    # A structured record is matched on fields the row does not print, and the row says so.
    field = golden.run("search_logs", ctx, since="-1h", terms=["traceId"], limit=5)
    assert field.meta["total"] > 0 and all("[matched in the full record]" in row.text for row in field.rows)


def test_limit_bounds_the_rows_and_the_footer_says_what_was_left_out():
    ctx = golden.ctx()
    env = golden.run("search_logs", ctx, since="-2h", limit=5)
    assert len(env.rows) == 5 and env.meta["total"] > 5
    footer = next(line for line in env.text.splitlines() if line.startswith("[showing 5 of "))
    assert f"of {env.meta['total']} records: the newest {env.meta['total'] - 5}, from " in footer and "count with group_by" in footer
    assert golden.run("search_logs", ctx, since="-2h", limit=50).meta["returned"] <= 50
    assert golden.run("search_logs", ctx, since="-2h", limit=51).meta["error"] == "arguments"


def test_a_stack_trace_is_folded_to_three_frames_and_a_long_line_is_shortened_and_marked():
    ctx = golden.ctx()
    env = golden.run("search_logs", ctx, since="-2h", limit=50, terms=["exception"])
    traces = [row for row in env.rows if "\n" in row.text]
    assert traces
    for row in traces:
        lines = row.text.split("\n")
        frames = [ln for ln in lines[1:] if not ln.strip().startswith("[…")]
        assert len(frames) <= 3 and all(ln.startswith("    ") for ln in lines[1:])
    served = {r["handle"]: r for r in served_records()}
    folded = [row for row in traces if row.text.split("\n")[-1].strip().startswith("[…+")]
    assert folded
    for row in folded:
        hidden = int(re.search(r"\[…\+(\d+) more lines\]", row.text).group(1))
        assert hidden > 0
    assert "get_record <handle> returns the whole record" in env.text
    everything = golden.run("search_logs", ctx, since="-24h", limit=50, sourcetype="f5:bigip:ltm")
    long_rows = [row for row in everything.rows if len(served[row.handle]["text"]) > 300]
    assert long_rows and all(re.search(r"\[…\+\d+ chars\]", row.text) for row in long_rows)


def test_a_line_the_forwarder_cut_is_flagged():
    ctx = golden.ctx()
    cut = ctx.store.conn.execute("SELECT handle, host, t_known FROM visible WHERE kind = 'log' AND json_extract(flags, '$.truncated')").fetchall()
    assert cut
    for handle, host, known in cut:
        env = golden.run("search_logs", ctx, host=host, since=windows.iso(known - 1000), until=windows.iso(known + 1000), limit=50)
        flagged = {row.handle for row in env.rows if "[cut short at the source]" in row.text}
        assert flagged == {handle}


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_every_count_from_a_sampled_source_carries_its_rate_and_an_estimate(checkpoint):
    ctx = golden.ctx(checkpoint)
    rates = dict(ctx.store.conn.execute("SELECT handle, coalesce(keep_1_in, 1) FROM visible WHERE kind = 'log'"))
    env = golden.run("search_logs", ctx, since="-1h", sourcetype="nginx:plus:access")
    want = expected(ctx.store.now, ctx.store.now - 3_600_000, sourcetype="nginx:plus:access")
    estimate = sum(rates[r["handle"]] for r in want)
    assert env.meta["sampled"] is True and f"estimated {estimate})" in env.text.splitlines()[0]
    assert re.search(r"\(sampled 1:\d+( to 1:\d+)? per [a-z ]+, estimated \d+\)", env.text.splitlines()[0])
    table = golden.run("search_logs", ctx, since="-1h", sourcetype="nginx:plus:access", group_by="minute", bucket="5m")
    assert "bucket  records  estimated" in table.text and f"estimated {estimate})" in table.text.splitlines()[0]
    body = [ln.split("  ") for ln in table.text.splitlines() if re.match(r"\d\d:\d\d:\d\d  ", ln)]
    assert sum(int(c[1]) for c in body) == len(want) and sum(int(c[2]) for c in body) == estimate
    unsampled = golden.run("search_logs", ctx, since="-24h", sourcetype="f5:bigip:ltm")
    assert unsampled.meta["sampled"] is False and "sampled" not in unsampled.text and "estimated" not in unsampled.text


@pytest.mark.parametrize("tags", [False, True])
@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_no_sampling_class_name_reaches_any_output(checkpoint, tags, tmp_path):
    """Several class names are ordinary words that the log lines hold anyway, so looking for the names
    proves nothing. This serves the same bundle with every class renamed to a marker no log line holds: the
    rates are the same, and no marker may come back out."""
    copy = tmp_path / parity.BUNDLE.name
    shutil.copytree(parity.BUNDLE, copy)
    integration = yaml.safe_load((copy / "integration.yaml").read_text())
    markers = []
    for block in integration["sampling"].values():
        for cls in block.get("classes", []):
            markers.append(f"zzclass{len(markers)}zz")
            cls["name"] = markers[-1]
            cls["note"] = f"note-{markers[-1]}"
    assert len(markers) == sum(len(b.get("classes", [])) for b in INTEGRATION["sampling"].values()) > 10 and len(CLASS_NAMES) > 10
    (copy / "integration.yaml").write_text(yaml.safe_dump(integration, sort_keys=False))
    built = store.build(copy, tags=tags)
    now = built.set_now(golden.CHECKPOINTS[checkpoint])
    ctx = ToolContext(store=built, now=datetime.fromtimestamp(now / 1000, timezone.utc), tags=tags)
    assert built.conn.execute("SELECT count(*) FROM record WHERE keep_1_in > 1").fetchone()[0] > 100      # the rates still apply
    calls = [{"since": "-24h", "limit": 50}, {"since": "-24h", "group_by": "minute"}, {"since": "-24h", "group_by": "host"},
             {"since": "-24h", "group_by": "level"}, {"since": "-24h", "group_by": "template"}]
    calls += [{"since": "-24h", "limit": 50, "sourcetype": st} for st in SOURCETYPE.values()]
    calls += [{"since": "-24h", "group_by": "signal"}] if tags else []
    for args in calls:
        env = golden.run("search_logs", ctx, **args)
        assert env.status == "ok" and "zzclass" not in json.dumps(env.as_dict()), args
    # And no showable column holds one, so no later tool can print it either.
    columns = "handle, host, entity_raw, title, body, attrs, flags, tags, sample_per, sev_text, status, native_id, stream_key, parent"
    for row in built.conn.execute(f"SELECT {columns} FROM record WHERE kind = 'log'"):
        assert "zzclass" not in json.dumps(row)


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_count_by_minute_has_every_bucket_and_the_newest_are_never_cut(checkpoint):
    ctx = golden.ctx(checkpoint)
    now = ctx.store.now
    want = expected(now, now - 30 * 60_000)
    env = golden.run("search_logs", ctx, since="-30m", group_by="minute")
    assert len(env.rows) == 1 and env.rows[0].handle.startswith("q:") and env.rows[0].t_known is None and env.text.startswith(env.rows[0].handle)
    buckets = [ln.split("  ") for ln in env.text.splitlines() if re.match(r"\d\d:\d\d:\d\d  ", ln)]
    assert len(buckets) == 30 and sum(int(b[1]) for b in buckets) == len(want) == env.meta["total"]
    sparse = golden.run("search_logs", ctx, since="-30m", group_by="minute", terms=["error"])
    sparse_buckets = [ln.split("  ") for ln in sparse.text.splitlines() if re.match(r"\d\d:\d\d:\d\d  ", ln)]
    assert len(sparse_buckets) == 30 and any(int(b[1]) == 0 for b in sparse_buckets)     # zeros are printed, never skipped
    last = buckets[-1][0]
    assert golden.ms(last) == now - 60_000                                          # the bucket that ends at now is the last line
    wide = golden.run("search_logs", ctx, since="-3h", group_by="minute")
    assert "[the bucket was widened from 1m to 5m to keep the table to 60 buckets]" in wide.text
    first = (now - 3 * 3_600_000) // 300_000 * 300_000
    assert len([ln for ln in wide.text.splitlines() if re.match(r"\d\d:\d\d:\d\d  ", ln)]) == len(range(first, now, 300_000))
    long = golden.run("search_logs", ctx, since="-5d", group_by="minute")
    rows = [ln for ln in long.text.splitlines() if re.match(r"\d{4}-\d\d-\d\dT", ln)]
    assert len(rows) == 60 and "buckets are not shown" in long.text and "widened from 1m to 1h" in long.text
    assert parity.ms(rows[-1].split("  ")[0]) == now - now % 3_600_000              # the newest bucket survives the cut
    assert env.text.splitlines()[-1].endswith("| timechart span=1m count")


@pytest.mark.parametrize("by,column", [("host", "host"), ("level", "sev_text")])
def test_count_by_group_adds_up_to_the_total(by, column):
    ctx = golden.ctx()
    env = golden.run("search_logs", ctx, since="-1h", group_by=by)
    since = ctx.store.now - 3_600_000
    want = dict(ctx.store.conn.execute(f"SELECT coalesce({column}, '(none)'), count(*) FROM visible WHERE kind = 'log' AND t_known >= ? GROUP BY 1", (since,)))
    lines = env.text.split("group  records")[1].splitlines()[1:]
    got = {ln.split("  ")[0]: int(ln.split("  ")[1]) for ln in lines if not ln.startswith("[") and not ln.startswith("other (")}
    other = sum(int(ln.split("  ")[1]) for ln in lines if ln.startswith("other ("))
    assert sum(got.values()) + other == env.meta["total"] == sum(want.values())
    assert all(want[name] == n for name, n in got.items()) and len(got) <= 20
    assert env.text.splitlines()[-1].endswith(f"| stats count by {by}")


def test_count_by_template_shows_masked_lines_and_only_references_its_examples():
    ctx = golden.ctx()
    env = golden.run("search_logs", ctx, since="-2h", group_by="template")
    lines = [ln for ln in env.text.splitlines() if "  example " in ln]
    assert len(lines) == 20 and any(ln.startswith("other (") for ln in env.text.splitlines())
    examples = [ln.rsplit("  example ", 1)[1] for ln in lines]
    assert list(env.referenced) == examples and not set(examples) & set(env.shown) and all(is_handle(h) for h in examples)
    assert all("<*>" in ln or re.search(r"[A-Z]{3,}", ln) for ln in lines)
    counts = [int(ln.split("  ")[-3 if env.meta["sampled"] else -2]) for ln in lines]
    assert counts == sorted(counts, reverse=True)


def test_a_count_table_is_citable_by_a_handle_minted_for_the_call():
    ctx, early = golden.ctx(), golden.ctx(golden.FIRST)
    a, b = golden.run("search_logs", ctx, since="-30m", group_by="level"), golden.run("search_logs", ctx, since="-30m", group_by="level")
    assert a.shown == b.shown and re.fullmatch(r"q:[0-9a-f]{8}", a.shown[0]) and a.rows[0].text.startswith(a.shown[0] + "  count by level")
    assert golden.run("search_logs", ctx, since="-30m", group_by="host").shown != a.shown
    assert golden.run("search_logs", early, since="-30m", group_by="level").shown != a.shown        # another now, another handle


def test_an_unknown_sourcetype_or_host_answers_with_the_values_that_exist():
    ctx = golden.ctx()
    env = golden.run("search_logs", ctx, since="-1h", sourcetype="no:such:type")
    assert env.status == "error" and env.text.startswith("sourcetype='no:such:type' matches nothing visible. sourcetype is one of: ")
    assert all(st in env.text for st in SOURCETYPE.values())
    assert golden.run("search_logs", ctx, since="-1h", host="nope").text.startswith("host='nope' matches nothing visible. host is one of: ")


def test_an_empty_result_names_what_to_drop_and_a_clamped_window_says_so():
    ctx = golden.ctx()
    env = golden.run("search_logs", ctx, since="-1h", terms=["zz-no-such-term-zz"], host="pay-02")
    assert env.status == "ok" and env.meta["empty"] and env.text.startswith("0 rows. Drop a term or host=, or widen the window.")
    assert golden.run("search_logs", ctx, since="-1h", terms=["zz-no-such-term-zz"], group_by="minute").meta["empty"]
    late = golden.run("search_logs", ctx, since="-10m", until="2030-01-01T00:00:00Z")
    assert late.meta["clamped"] and "The window was clamped to now" in late.text and late.window[1] == windows.iso(ctx.store.now)
    assert golden.handles(late) == golden.handles(golden.run("search_logs", ctx, since="-10m"))


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_no_search_shows_anything_from_after_now(checkpoint):
    ctx = golden.ctx(checkpoint, tags=True)
    now = ctx.store.now
    tokens = test_leak.SCENARIO["future_tokens"][checkpoint]
    for args in ({"since": "-24h", "limit": 50}, {"since": "-10m", "until": "2030-01-01T00:00:00Z", "limit": 50}, {"since": "-24h", "group_by": "minute"},
                 {"since": "-24h", "group_by": "template"}, {"since": "-30m", "terms": ["e"], "limit": 50}):
        env = golden.run("search_logs", ctx, **args)
        text = json.dumps(env.as_dict(), ensure_ascii=False)
        assert all(parity.ms(row.t_known) < now for row in env.rows if row.t_known)
        assert max(test_leak.times_in(env.text, test_leak.UTC), default=0) <= now       # now itself only as the window's end
        assert not [tok for tok in tokens if tok in text], args


# -- tags (section 6.3) ------------------------------------------------------------------------------------


def strip_tags(text: str) -> str:
    return re.sub(r" \[tag [a-z_]+=[a-z_,]+\]", "", text)


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_with_tags_off_no_output_mentions_a_tag_and_with_no_tag_input_the_two_arms_agree(checkpoint):
    off, on = golden.ctx(checkpoint, tags=False), golden.ctx(checkpoint, tags=True)
    for args in ({"since": "-1h", "limit": 50}, {"since": "-1h", "group_by": "minute"}, {"since": "-2h", "group_by": "template"},
                 {"since": "-1h", "terms": ["error"]}):
        plain, tagged = golden.run("search_logs", off, **args), golden.run("search_logs", on, **args)
        assert "[tag " not in plain.text and "tag" not in json.dumps(plain.as_dict()).replace('"tag": null', "")
        assert strip_tags(tagged.text) == plain.text and tagged.shown == plain.shown and tagged.meta["tag"] is None
    assert "[tag signal=" in golden.run("search_logs", on, since="-1h", limit=50).text
    assert golden.run("search_logs", off, since="-1h", signal="errors").meta["error"] == "arguments"        # the input does not exist
    assert golden.run("search_logs", off, since="-1h", group_by="signal").meta["error"] == "arguments"


@pytest.mark.parametrize("value", [v for v in profiles.facets()["signal"]] + ["untagged"])
def test_a_tag_filter_matches_rows_that_carry_the_value_and_says_what_it_left_out(value):
    ctx = golden.ctx(tags=True)
    since = ctx.store.now - 3_600_000
    rows = ctx.store.conn.execute("SELECT handle, tags FROM visible WHERE kind = 'log' AND t_known >= ?", (since,)).fetchall()
    signals = {h: json.loads(t).get("signal", []) for h, t in rows}
    want = {h for h, values in signals.items() if (not values if value == "untagged" else value in values)}
    untagged = sum(1 for values in signals.values() if not values)
    env = golden.run("search_logs", ctx, since="-1h", signal=value, limit=50)
    footer = (f"[tag filter signal={value}: {len(want)} of the {len(rows)} records that match without it. {untagged} of those {len(rows)} "
              "carry no signal tag, and a missing tag says nothing about a record]")
    assert footer in env.text and env.meta["tag"] == f"signal={value}"         # also when nothing matched
    assert env.meta["total"] == len(want) and set(env.shown) <= want
    for row in env.rows:
        label = re.search(r"\[tag signal=([a-z,]+)\]", row.text)
        assert (label is None) if value == "untagged" else (value in label.group(1).split(","))
    assert "has no native equivalent" in env.text.splitlines()[-1]


def test_a_count_by_signal_prints_untagged_and_is_not_citable():
    ctx = golden.ctx(tags=True)
    since = ctx.store.now - 3_600_000
    rows = [json.loads(t).get("signal", []) for (t,) in ctx.store.conn.execute("SELECT tags FROM visible WHERE kind = 'log' AND t_known >= ?", (since,))]
    env = golden.run("search_logs", ctx, since="-1h", group_by="signal")
    assert env.rows == () and env.shown == () and "q:" not in env.text
    assert "[not citable: this count depends on a tag" in env.text and env.meta["tag"] == "group_by=signal" and env.meta["total"] == len(rows)
    table = dict(ln.split("  ")[:2] for ln in env.text.split("group  records")[1].splitlines()[1:] if not ln.startswith("["))
    assert int(table["untagged"]) == sum(1 for values in rows if not values) > 0
    for value in profiles.facets()["signal"]:
        assert int(table.get(value, 0)) == sum(1 for values in rows if value in values)
    assert sum(map(int, table.values())) > len(rows) and f"more than the {len(rows)} records matched" in env.text   # two values count twice
    filtered = golden.run("search_logs", ctx, since="-1h", signal="errors", group_by="minute")
    assert filtered.rows == () and "[not citable" in filtered.text and "[tag filter signal=errors:" in filtered.text
    assert golden.run("search_logs", ctx, since="-1h", group_by="minute").shown != ()                  # no tag, so citable
