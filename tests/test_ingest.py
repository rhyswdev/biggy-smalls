"""Ingestion parity (docs/decisions/ingestion-decisions.md, build row 3): every manifest row has exactly
one store row with the same handle, path, locator, event time, known time and validity window; the store
has no other row; and `native` is the bytes as served."""
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pytest

import parity
from smalls.contracts.handles import is_handle
from smalls.tools import store

BUNDLE = parity.BUNDLE
CHECKPOINTS = ("14:40", "14:52")


@pytest.fixture(scope="module")
def built():
    return store.build(BUNDLE)


@pytest.fixture(scope="module")
def rows():
    return parity.manifest()


def one(conn, sql, *args):
    return conn.execute(sql, args).fetchone()[0]


def test_every_manifest_row_has_exactly_one_store_row(built, rows):
    assert len(rows) == 5861
    assert parity.problems(built.conn, rows) == []
    assert one(built.conn, "SELECT count(*) FROM record") + one(built.conn, "SELECT count(*) FROM point") == len(rows)


def test_parity_catches_a_row_the_store_gets_wrong(built, rows):
    wrong = [dict(r) for r in rows]
    wrong[0]["t_known"] = "2026-10-13T23:59:59.000Z"
    wrong[1]["handle"] = "log:pay-01/00000000"
    del wrong[2]
    assert len(parity.problems(built.conn, wrong)) == 3


def test_every_source_is_read_and_nothing_is_left_unparsed(built):
    report = built.report
    assert list(report.sources) == list(built.integration.sources) and len(report.sources) == 17
    assert not (report.unhandled or report.refused or report.missing)
    assert report.unparsed == [] and report.unsampled == []
    assert one(built.conn, "SELECT count(*) FROM record WHERE json_extract(flags, '$.unparsed')") == 0
    assert "unparsed lines: 0" in report.text()


def test_every_handle_is_in_the_grammar(built):
    handles = [h for (h,) in built.conn.execute("SELECT handle FROM record UNION SELECT handle FROM point")]
    assert handles and all(is_handle(h) for h in handles)


def test_records_with_no_time_in_their_bytes_are_always_visible(built):
    undated = built.conn.execute("SELECT handle, path FROM record WHERE t_known = 0 ORDER BY rid").fetchall()
    assert Counter(path for _, path in undated) == {"datadog/synthetics.json": 2, "pagerduty/escalation_policies.json": 4}
    for at in CHECKPOINTS + ("00:00",):
        built.set_now(at)
        visible = {h for (h,) in built.conn.execute("SELECT handle FROM visible WHERE t_known = 0")}
        assert visible == {h for h, _ in undated}
    built.clear_now()


def test_native_is_the_served_bytes(built, rows):
    text = {}
    starts: dict[str, list[int]] = {}
    for r in rows:
        if isinstance(r["locator"], int):
            starts.setdefault(r["path"], []).append(r["locator"])
    checked = Counter()
    for path, locator, native in built.conn.execute("SELECT path, locator, native FROM record"):
        served = text.setdefault(path, (BUNDLE / path).read_bytes().decode("utf-8"))
        if locator == "/" or path.endswith(".md"):
            assert native == served, path
            checked["file"] += 1
        elif locator.startswith("/"):
            node = json.loads(served)
            for part in locator.strip("/").split("/"):
                node = node[int(part)] if isinstance(node, list) else node[part]
            assert native in served and json.loads(native) == node, (path, locator)
            checked["element"] += 1
        else:
            first = int(locator)
            later = [s for s in starts[path] if s > first]
            lines = served.split("\n")
            assert native == "\n".join(lines[first - 1:(min(later) - 1) if later else len(lines) - 1]), (path, locator)
            checked["lines"] += 1
    assert checked["file"] and checked["element"] and checked["lines"]
    assert sum(checked.values()) == one(built.conn, "SELECT count(*) FROM record")


def _reread(integ, source: str, text: str) -> int:
    """Read a time as written back into epoch ms, without the adapters: by its shape, then by the source's format."""
    def at(dt):
        return parity.ms(dt.astimezone(timezone.utc).isoformat())
    if re.fullmatch(r"\d{8}T\d{6}\.\d+Z", text):                       # a receipt stamp in a file name
        return at(datetime.strptime(text, "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=timezone.utc))
    if re.fullmatch(r"\d{13}", text):                                    # epoch milliseconds
        return int(text)
    if re.fullmatch(r"\d{10}", text):                                    # epoch seconds
        return int(text) * 1000
    if re.fullmatch(r"\d{10}\.\d{6}", text):                            # epoch seconds and a sequence
        return int(text[:10]) * 1000 + int(text[11:14])
    if re.fullmatch(r"\d{1,2} \w{3} \d{4}", text):                      # a date only, read as 12:00 UTC
        return at(datetime.strptime(text, "%d %b %Y").replace(hour=12, tzinfo=timezone.utc))
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}", text):  # a wall clock with a seven-digit fraction
        return integ.local(source, datetime.strptime(text[:26], "%Y-%m-%dT%H:%M:%S.%f"))[0]
    if integ.sources[source].get("format"):
        return integ.parse(source, text)[0]
    return at(datetime.fromisoformat(text))


def test_every_native_time_is_the_known_time_as_written(built):
    """One rule for every source: `t_native` is the text `t_known` was read from, and `tz_rule` says how."""
    rows = built.conn.execute("SELECT handle, source, t_native, tz_rule, t_known FROM record").fetchall()
    assert len(rows) == 4454
    for handle, source, t_native, tz_rule, t_known in rows:
        if t_known == 0:
            assert t_native is None and tz_rule is None, handle        # no time in its bytes
            continue
        assert t_native and tz_rule, handle
        assert _reread(built.integration, source, t_native) == t_known, (handle, t_native)
    # Where the event time differs from the known time, the native text is still the known time's.
    differ = built.conn.execute("SELECT count(*), count(DISTINCT source) FROM record WHERE t_event != t_known").fetchone()
    assert differ == (73, 6)


def test_reference_files_are_served_bytes_too(built):
    refs = built.conn.execute("SELECT source, name, native FROM ref ORDER BY source, name").fetchall()
    assert [(s, n) for s, n, _ in refs if s == "slack"] == [("slack", "channels"), ("slack", "users")]
    assert len([1 for s, _, _ in refs if s == "datadog"]) == 8
    for source, name, native in refs:
        rel = name if source == "datadog" else f"{source}/{name}.json"
        assert native == (BUNDLE / rel).read_bytes().decode("utf-8")


def test_metric_points_carry_their_series_and_bucket(built):
    series = built.conn.execute("SELECT count(DISTINCT series), count(DISTINCT handle), count(DISTINCT path) FROM point").fetchone()
    assert series == (14, 8, 8)
    assert one(built.conn, "SELECT count(*) FROM point WHERE t_known != t + interval_s * 1000") == 0
    # Nulls during the ceiling stay nulls: a missing point is not a zero.
    assert one(built.conn, "SELECT count(*) FROM point WHERE value IS NULL") > 0


def test_versions_are_numbered_and_bounded(built):
    for handle, n in built.conn.execute("SELECT handle, count(*) FROM record GROUP BY handle HAVING count(*) > 1"):
        versions = built.conn.execute("SELECT version, t_event, t_known, valid_from, valid_to FROM record WHERE handle = ? ORDER BY version", (handle,)).fetchall()
        assert [v[0] for v in versions] == list(range(1, n + 1)), handle
        assert len({v[1] for v in versions}) == 1, handle                      # every version keeps the first one's event time
        assert all(v[3] == v[2] for v in versions), handle                     # valid from its own known time
        assert [v[4] for v in versions] == [v[3] for v in versions[1:]] + [None], handle
    assert one(built.conn, "SELECT count(*) FROM record WHERE handle = 'snow:INC0048811'") == 15


def test_knowledge_is_chunked_by_heading(built):
    assert one(built.conn, "SELECT count(*) FROM chunk ch JOIN record r USING (rid) WHERE r.kind = 'doc'") == 41
    assert one(built.conn, "SELECT count(*) FROM chunk_fts WHERE chunk_fts MATCH '\"idle\" OR \"transaction\"'") > 0
    built.set_now("14:52")
    hit = built.conn.execute("SELECT v.handle FROM chunk_fts f JOIN visible_chunk c ON c.chunk_id = f.rowid JOIN visible v ON v.rid = c.rid "
                             "WHERE chunk_fts MATCH '\"pg_stat_activity\"' ORDER BY bm25(chunk_fts, 3.0, 1.0)").fetchall()
    assert hit and all(h.startswith(("conf:", "snow:")) for (h,) in hit)
    built.clear_now()
