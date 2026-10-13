"""Tool plumbing (build row B1): windows, the renderer and its caps, `call`, and one test per error case of
K8 in docs/decisions/tools-agent-build.md."""
from datetime import datetime, timezone

import pytest

from smalls.contracts.envelope import Envelope, Row
from smalls.contracts.handles import ic_note_handle, metric_handle, query_handle
from smalls.tools import errors, parts, registry, schemas, windows
from smalls.tools.parts import Line, Parts
from smalls.tools.registry import ToolContext, ToolSpec

UTC = timezone.utc
NOW = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
NOW_MS = windows.ms_of(NOW)
CTX = ToolContext(store=None, now=NOW, tags=False)


def spec(fn, cap_tokens=200, cap_rows=None, args=schemas.SearchLogsArgs) -> ToolSpec:
    return ToolSpec("fake_tool", "read", args, fn, cap_tokens, cap_rows)


def lines(n: int, width: int = 40) -> list[Line]:
    return [Line(f"log:host/{i:08x}", f"log:host/{i:08x}  " + "x" * width, NOW_MS - (n - i) * 60_000) for i in range(n)]


# -- windows (K1) ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("text,want", [
    ("2026-05-04T11:00:00Z", -3_600_000), ("2026-05-04T13:00:00+02:00", -3_600_000), ("2026-05-04T11:00:00", -3_600_000),
    ("2026-05-04 11:00:00", -3_600_000), ("-90s", -90_000), ("-30m", -1_800_000), ("-2h", -7_200_000), ("-1d", -86_400_000), ("now", 0), ("NOW", 0)])
def test_the_accepted_time_forms(text, want):
    assert windows.instant("since", text, NOW_MS) == NOW_MS + want


def test_until_defaults_to_now_and_a_window_is_clamped_to_now():
    w = windows.resolve("-1h", None, NOW)
    assert (w.since, w.until, w.clamped) == (NOW_MS - 3_600_000, NOW_MS, False)
    late = windows.resolve("-1h", "2026-05-04T18:00:00Z", NOW)
    assert (late.until, late.clamped) == (NOW_MS, True) and late.iso == ("2026-05-04T11:00:00Z", "2026-05-04T12:00:00Z")
    inside = windows.resolve("-2h", "-1h", NOW)
    assert (inside.until, inside.clamped) == (NOW_MS - 3_600_000, False)
    assert windows.resolve("-30h", None, NOW).spans_dates() and not windows.resolve("-1h", None, NOW).spans_dates()


@pytest.mark.parametrize("since,until,field,words", [
    ("yesterday", None, "since", "is not a time"), ("-1h", "soon", "until", "is not a time"), ("-1w", None, "since", "is not a time"),
    ("-1h", "-2h", "since", "at or after until"), ("-1h", "-1h", "since", "at or after until"),
    ("2026-05-04T13:00:00Z", None, "since", "at or after now"), ("2026-05-04T13:00:00Z", "2026-05-04T14:00:00Z", "since", "at or after now")])
def test_a_window_that_cannot_be_made_names_the_field(since, until, field, words):
    with pytest.raises(windows.WindowError) as caught:
        windows.resolve(since, until, NOW)
    assert caught.value.field == field and words in str(caught.value) and str(caught.value).startswith(f"{field}: ")


def test_times_print_as_utc_to_the_second_or_the_millisecond():
    assert windows.iso(NOW_MS) == "2026-05-04T12:00:00Z" and windows.iso(NOW_MS + 221) == "2026-05-04T12:00:00.221Z"
    assert windows.clock(NOW_MS - 1000) == "11:59:59"


# -- the renderer and the caps -----------------------------------------------------------------------------


def test_every_row_is_in_the_text_and_the_native_query_comes_last():
    window = windows.resolve("-1h", None, NOW)
    env = parts.render(Parts(rows=lines(3), header=["3 records"], footer=["a hint"], native_query="search x", native_label="SPL",
                             window=window, now=NOW_MS, what="records"), cap_tokens=4000)
    text = env.text.splitlines()
    assert text[0] == "3 records" and text[-1] == "[SPL] search x" and text[-2] == "a hint" and "[3 records]" in text
    assert [row.handle for row in env.rows] == [line.handle for line in lines(3)] and all(row.text in env.text for row in env.rows)
    assert all(row.text.startswith(row.handle) for row in env.rows) and env.rows[0].t_known == "2026-05-04T11:57:00Z"
    assert env.window == window.iso and env.native_query == "search x" and env.status == "ok"
    assert (env.meta["total"], env.meta["returned"], env.meta["truncated"], env.meta["empty"], env.meta["clamped"]) == (3, 3, False, False, False)


def test_rows_drop_from_the_end_until_the_result_fits_and_a_cut_row_is_cut_from_rows_too():
    rows = lines(30)
    rows[-1] = Line(rows[-1].handle, rows[-1].text, rows[-1].t_known, refs=("jk:job#1/0a1b2c3d",))
    rows[0] = Line(rows[0].handle, rows[0].text, rows[0].t_known, refs=("jk:job#2/0a1b2c3d",))
    env = parts.render(Parts(rows=rows, what="records", narrow="Add a term or shorten the window."), cap_tokens=300)
    assert parts.tokens(env.text) <= 300 and 1 < len(env.rows) < 30
    assert [row.handle for row in env.rows] == [line.handle for line in rows[: len(env.rows)]]     # oldest first, newest cut
    assert rows[-1].handle not in env.text and rows[-1].handle not in env.shown
    assert env.referenced == ("jk:job#2/0a1b2c3d",)                                                   # the cut row's pointer went with it
    footer = env.text.splitlines()[-1]
    cut = 30 - len(env.rows)
    assert footer.startswith(f"[showing {len(env.rows)} of 30 records: the newest {cut}, from ") and footer.endswith("Add a term or shorten the window.]")
    assert (env.meta["total"], env.meta["returned"], env.meta["truncated"]) == (30, len(env.rows), True)


def test_a_tool_that_keeps_the_newest_drops_the_oldest():
    rows = lines(30)
    env = parts.render(Parts(rows=rows, what="messages", keep="newest"), cap_tokens=300)
    assert [row.handle for row in env.rows] == [line.handle for line in rows[-len(env.rows):]]
    assert f"the oldest {30 - len(env.rows)}" in env.text


def test_the_row_cap_and_a_tools_own_limit_both_say_what_was_left_out():
    env = parts.render(Parts(rows=lines(10), what="rows"), cap_tokens=4000, cap_rows=4)
    assert len(env.rows) == 4 and "[showing 4 of 10 rows: the newest 6" in env.text and env.meta["truncated"] is True
    limited = parts.render(Parts(rows=lines(5), total=120, what="records"), cap_tokens=4000)
    assert "[showing 5 of 120 records: the newest 115 not shown." in limited.text and limited.meta["total"] == 120


def test_a_record_with_no_time_in_its_bytes_reports_no_known_time():
    env = parts.render(Parts(rows=[Line("pd:ep/PABC123", "pd:ep/PABC123  a policy", 0)]), cap_tokens=100)
    assert env.rows == (Row("pd:ep/PABC123", "pd:ep/PABC123  a policy", None),)


# -- call, and one test per error case of K8 ---------------------------------------------------------------


def test_k8_bad_arguments_name_the_field():
    env = registry.call(spec(lambda a, c: Parts()), {"since": "-1h", "limit": 99, "colour": "red"}, CTX)
    assert env.status == "error" and env.meta["error"] == "arguments"
    assert env.text.startswith("Invalid arguments for fake_tool: ") and "limit:" in env.text and "colour:" in env.text
    assert env.text.endswith("Fix them and call again.")


def test_k8_a_window_error_is_an_argument_error_that_names_the_field():
    def tool(args, ctx):
        windows.resolve(args.since, args.until, ctx.now)
        return Parts()
    env = registry.call(spec(tool), {"since": "-1h", "until": "-2h"}, CTX)
    assert env.status == "error" and env.meta["error"] == "arguments" and "since: the window is empty" in env.text
    assert "is not a time" in registry.call(spec(tool), {"since": "last tuesday"}, CTX).text


def test_k8_a_handle_that_is_unknown_not_yet_visible_or_absent_gets_one_sentence():
    a, b = errors.not_in_bundle("log:host/00000000"), errors.not_in_bundle("snow:INC0000001")
    assert a.status == "error" and a.text == "log:host/00000000: not in this bundle."
    assert a.text.replace("log:host/00000000", "H") == b.text.replace("snow:INC0000001", "H")
    assert errors.for_handle("log:host/00000000") is None       # a well-formed handle is looked up, whatever it names


def test_k8_a_malformed_handle():
    env = errors.for_handle("INC0000001")
    assert env.status == "error" and env.meta["error"] == "malformed_handle" and env.text.startswith("INC0000001: not a handle.")


@pytest.mark.parametrize("handle,again", [
    (query_handle("search_logs|{}", "2026-05-04T12:00:00Z"), "Re-run the search that printed it"),
    (metric_handle("avg:system.load.1{*}", "1100", "1200"), "Call query_metrics with the same metric, scope and window"),
    (ic_note_handle("run", "2026-05-04T12:00:00Z", "try the other pool"), "The note's text is in the result that printed it")])
def test_k8_a_handle_minted_per_call_or_per_run_names_what_to_do_instead(handle, again):
    env = errors.for_handle(handle)
    assert env.status == "error" and env.meta["error"] == "minted" and "holds no record to fetch" in env.text and again in env.text
    assert errors.for_handle(metric_handle("avg:system.load.1{*}")) is None     # a series identity is a record to look up


def test_k8_an_empty_result_names_the_filter_to_drop_or_the_window_to_widen():
    window = windows.resolve("-5m", None, NOW)
    env = registry.call(spec(lambda a, c: Parts(empty_hint="Drop host= or widen the window.", native_query="search y", window=window, now=NOW_MS)),
                        {"since": "-5m"}, CTX)
    assert env.status == "ok" and env.meta["empty"] is True and env.rows == ()
    assert env.text.splitlines() == ["0 rows. Drop host= or widen the window.", "[native query] search y"]


def test_k8_a_clamped_window_says_so():
    def tool(args, ctx):
        window = windows.resolve(args.since, args.until, ctx.now)
        return Parts(rows=lines(2), window=window, now=NOW_MS)
    env = registry.call(spec(tool), {"since": "-1h", "until": "2026-05-04T23:00:00Z"}, CTX)
    assert env.status == "ok" and env.meta["clamped"] is True and env.window[1] == "2026-05-04T12:00:00Z"
    assert "The window was clamped to now (2026-05-04T12:00:00Z): nothing after now exists yet." in env.text
    assert "clamped" not in registry.call(spec(tool), {"since": "-1h"}, CTX).text


def test_k8_an_internal_error_carries_no_traceback_and_no_path():
    def tool(args, ctx):
        raise FileNotFoundError("/private/bundle/_internal/manifest.jsonl is missing")
    env = registry.call(spec(tool), {"since": "-1h"}, CTX)
    assert env.status == "error" and env.meta["error"] == "internal"
    assert env.text == "fake_tool failed inside the tool (FileNotFoundError). Try narrower arguments or another tool."
    assert "/" not in env.text and "Traceback" not in env.text and "manifest" not in env.text


def test_k8_only_an_empty_result_and_a_clamped_window_are_not_errors():
    for env in (errors.bad_arguments("t", "x: bad"), errors.internal("t", ValueError()), errors.not_in_bundle("conf:X"),
                errors.malformed_handle("x"), errors.minted("q:00000000", "call", "again"), errors.unknown_value("host", "h", ["a"]),
                errors.over_cap("t", "9 tokens", "5")):
        assert env.status == "error" and env.meta["error"] and env.rows == ()
    assert errors.unknown_value("host", "web-9", ["web-1", "web-2"]).text == "host='web-9' matches nothing visible. host is one of: web-1, web-2."


def test_a_result_over_its_cap_fails_call():
    big = Envelope("x" * 900, rows=[Row("log:host/00000001", "x")])
    env = registry.call(spec(lambda a, c: big, cap_tokens=200), {"since": "-1h"}, CTX)
    assert env.status == "error" and env.meta["error"] == "over_cap" and "225 tokens" in env.text and "200" in env.text
    many = Envelope("ok", rows=[Row(f"log:host/{i:08x}", "r") for i in range(5)])
    env = registry.call(spec(lambda a, c: many, cap_rows=4), {"since": "-1h"}, CTX)
    assert env.status == "error" and "5 rows" in env.text
    assert registry.call(spec(lambda a, c: many, cap_rows=5), {"since": "-1h"}, CTX) is many
    # One row that is larger than the cap cannot be cut to fit: the tool is at fault, and `call` says so.
    huge = Parts(rows=[Line("log:host/00000001", "log:host/00000001  " + "y" * 2000, NOW_MS)])
    assert registry.call(spec(lambda a, c: huge, cap_tokens=200), {"since": "-1h"}, CTX).meta["error"] == "over_cap"


def test_a_tool_can_refuse_from_anywhere_and_call_never_raises():
    def tool(args, ctx):
        raise errors.Refusal(errors.unknown_value("host", args.host, ["web-1"]))
    env = registry.call(spec(tool), {"since": "-1h", "host": "web-9"}, CTX)
    assert env.status == "error" and env.meta["error"] == "unknown_value" and "web-1" in env.text
    assert registry.call(spec(lambda a, c: 1 / 0), {"since": "-1h"}, CTX).meta["error"] == "internal"
    assert registry.call(spec(lambda a, c: Parts()), None, CTX).meta["error"] == "arguments"       # no arguments at all
