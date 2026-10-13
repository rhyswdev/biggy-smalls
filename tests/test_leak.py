"""The store half of the leak test (docs/decisions/ingestion-decisions.md, build row 4; PLAN 3.4). At each
core checkpoint the store shows exactly the record versions the manifest says are visible, and nothing a
tool may show from a visible row carries a time at or after `now` or a token from the checkpoint's future.
`make leak-test` runs this file. The tool-output half arrives with the tools.

What a tool may show is every column but the two the store keeps for its own use (`valid_to`, which says
when a record next changes, and a `t_end` at or after `now`), with `native` read through `render_native`.

Exemptions, each a time that is known in advance, as the served-file lint already allows:
- an on-call shift's `end`;
- a change request's planned window, `start_date` and `end_date` (its actual `work_start`, `work_end`,
  `closed_at` and `sys_updated_on` are not exempt).
"""
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

import parity
from smalls.tools import store

ROOT = parity.ROOT
BUNDLE = parity.BUNDLE
SCENARIO = yaml.safe_load((ROOT / "scenario/larkspur-checkout.yaml").read_text())
DAY = SCENARIO["meta"]["date"]
CHECKPOINTS = [(c["id"], c["at"]) for c in SCENARIO["checkpoints"] if c.get("core")]
UTC = timezone.utc

SHOWN = ("handle", "source", "dataset", "kind", "native_id", "host", "entity_raw", "stream_key", "t_native", "tz_rule", "sev_text",
         "status", "title", "body", "parent", "template_id", "sample_per", "tags", "attrs", "flags")
SCHEDULED = {("pagerduty", "oncalls"): ("end",), ("servicenow", "change_request"): ("start_date", "end_date")}

_OFFSET = r"(?:Z|[+-]\d{2}(?::?\d{2})?| UTC| [+-]\d{4})"
_STAMPS = [
    # (pattern, how to read a match in a source zone)
    (re.compile(r"(?<![\d.])(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:[.,]\d+)?(" + _OFFSET + r")?(?![\d:])"), "ymd"),
    (re.compile(r"(?<!\d)(\d{4})/(\d{2})/(\d{2}) (\d{2}):(\d{2}):(\d{2})()"), "ymd"),
    (re.compile(r"(?<!\d)(\d{2})/([A-Z][a-z]{2})/(\d{4}):(\d{2}):(\d{2}):(\d{2}) ([+-]\d{4})"), "clf"),
    (re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(?:\.\d+)?(Z)"), "ymd"),
]
_EPOCH_S = re.compile(r"(?<![\w.])(1[6-9]\d{8})(?:\.\d+)?(?![\w])")
_EPOCH_MS = re.compile(r"(?<![\w.])(1[6-9]\d{11})(?![\w])")
_EPOCH_IN_ID = re.compile(r"_at_(1[6-9]\d{8})_")
_MONTHS = {m: i for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}


def _offset(text: str, zone) -> timezone | ZoneInfo:
    text = text.strip()
    if not text:
        return zone
    if text in ("Z", "UTC"):
        return UTC
    digits = text[1:].replace(":", "").ljust(4, "0")
    delta = timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))
    return timezone(delta if text[0] == "+" else -delta)


def times_in(text: str, zone) -> list[int]:
    """Every machine-written time in a text, as epoch ms. A time with no marker is read in `zone`."""
    out = []
    for pattern, shape in _STAMPS:
        for m in pattern.finditer(text):
            g = m.groups()
            y, mo, d = (int(g[2]), _MONTHS[g[1]], int(g[0])) if shape == "clf" else (int(g[0]), int(g[1]), int(g[2]))
            try:
                dt = datetime(y, mo, d, int(g[3]), int(g[4]), int(g[5]), tzinfo=_offset(g[6] or "", zone))
            except ValueError:
                continue
            out.append(parity.ms(dt.astimezone(UTC).isoformat()))
    out += [int(m.group(1)) * 1000 for m in _EPOCH_S.finditer(text)]
    out += [int(m.group(1)) * 1000 for m in _EPOCH_IN_ID.finditer(text)]
    out += [int(m.group(1)) for m in _EPOCH_MS.finditer(text)]
    return out


def test_the_scanner_reads_every_clock_the_bundle_uses():
    ny, t = ZoneInfo("America/New_York"), parity.ms("2026-10-13T14:31:07.000Z")
    for text, zone in [("2026-10-13 14:31:07,221 ERROR", UTC), ("2026-10-13 10:31:07", ny), ("2026-10-13T10:31:07.4370000", ny),
                       ("2026-10-13T07:31:07.000-07:00", ny), ("2026-10-13 14:31:07.118+00", ny), ("2026-10-13 10:31:07 -0400", UTC),
                       ("2026-10-13 14:31:07.221 UTC [34757]", ny), ("[13/Oct/2026:14:31:07 +0000]", ny), ("@2026/10/13 14:31:07.", UTC),
                       ("20261013T143107.350Z-x", ny), ('"ts":"1791901867.000006"', ny), ("1791901867000", ny), ("RMD5_at_1791901867_732", ny)]:
        assert times_in(text, zone) == [t], text
    assert times_in("ord-7781901 PgConnection@5b2a8e1d 10.20.4.33:8443 5958457257035b83", UTC) == []


@pytest.fixture(scope="module")
def built():
    return store.build(BUNDLE)


@pytest.fixture(scope="module")
def rows():
    return parity.manifest()


def shown(row: dict) -> str:
    """Everything a tool may show of one visible row, with the known-in-advance fields left out."""
    scheduled = SCHEDULED.get((row["source"], row["dataset"]), ())
    attrs = {k: v for k, v in json.loads(row["attrs"]).items() if k not in scheduled}
    native = store.render_native(row["native"], row["flags"])
    if scheduled:
        native = json.dumps({k: v for k, v in json.loads(native).items() if k not in scheduled}, ensure_ascii=False)
    parts = [str(row[c]) for c in SHOWN if c != "attrs" and row[c] is not None]
    return "\n".join(parts + [json.dumps(attrs, ensure_ascii=False), native])


def visible_rows(built):
    cur = built.conn.execute("SELECT * FROM visible")
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur]


@pytest.mark.parametrize("cid,at", CHECKPOINTS)
def test_the_store_shows_exactly_what_the_manifest_says_is_visible(built, rows, cid, at):
    now = built.set_now(at)
    want = {(r["path"], str(r["locator"])): r["handle"] for r in rows if parity.visible_at(r, now)}
    got = {key: hits[0]["handle"] for key, hits in parity.store_rows(built.conn, visible=True).items()}
    assert got == want
    counts = built.visible_counts()
    assert counts["records"] + counts["points"] == len(want)
    # One version at most of any record, and it is the one valid at now.
    for handle, n in built.conn.execute("SELECT handle, count(*) FROM visible GROUP BY handle"):
        assert n == 1, handle
    # No record drops out between two of its versions: a save at exactly `now` would hide both (the
    # earlier version ends at `valid_to`, the later one is not yet known). No checkpoint sits on one.
    known = {h for (h,) in built.conn.execute("SELECT DISTINCT handle FROM record WHERE t_known < ? AND valid_from IS NOT NULL", (now,))}
    assert known <= {h for (h,) in built.conn.execute("SELECT handle FROM visible")}
    assert built.conn.execute("SELECT count(*) FROM visible WHERE t_known >= ? OR valid_from > ? OR valid_to <= ?", (now, now, now)).fetchone()[0] == 0
    assert built.conn.execute("SELECT count(*) FROM visible_point WHERE t_known >= ?", (now,)).fetchone()[0] == 0
    built.clear_now()


def test_the_live_incident_shows_the_version_valid_at_each_checkpoint(built):
    seen = {}
    for cid, at in CHECKPOINTS:
        built.set_now(at)
        seen[cid] = built.conn.execute("SELECT version, status, json_extract(attrs, '$.assignment_group') FROM visible WHERE handle = 'snow:INC0048811'").fetchall()
        problems = dict(built.conn.execute("SELECT handle, status FROM visible WHERE source = 'dynatrace' AND dataset = 'problems'").fetchall())
        assert "CLOSED" not in problems.values() or cid != "1440" or problems == {"dt:P-26101288": "CLOSED", **{k: v for k, v in problems.items() if v == "OPEN"}}
        assert built.conn.execute("SELECT count(*) FROM visible WHERE kind = 'build' AND status = 'BUILDING' AND t_end IS NOT NULL").fetchone()[0] == 0
    built.clear_now()
    assert all(len(v) == 1 for v in seen.values())
    assert seen["1440"][0][0] < seen["1452"][0][0] < 15


@pytest.mark.parametrize("cid,at", CHECKPOINTS)
def test_no_visible_row_shows_a_time_at_or_after_now(built, cid, at):
    now = built.set_now(at)
    late = []
    for row in visible_rows(built):
        zone = built.integration.zone(row["source"])
        worst = max(times_in(shown(row), zone), default=0)
        if worst >= now:
            late.append((row["handle"], row["path"], row["locator"], datetime.fromtimestamp(worst / 1000, UTC).isoformat()))
    assert late == []
    built.clear_now()


@pytest.mark.parametrize("cid,at", CHECKPOINTS)
def test_the_machine_time_columns_of_visible_rows(built, cid, at):
    """`t_event` may lie ahead of now only for a change that has not started (its planned start); `t_end`
    only for an on-call shift (its scheduled end). A tool shows neither as something that has happened."""
    now = built.set_now(at)
    ahead = built.conn.execute("SELECT source, dataset, json_extract(attrs, '$.work_start') FROM visible WHERE t_event >= ?", (now,)).fetchall()
    assert all((s, d, started) == ("servicenow", "change_request", None) for s, d, started in ahead), ahead
    ends = Counter(built.conn.execute("SELECT source, dataset FROM visible WHERE t_end >= ?", (now,)).fetchall())
    assert set(ends) <= {("pagerduty", "oncalls")}, ends
    built.clear_now()


@pytest.mark.parametrize("cid,at", CHECKPOINTS)
def test_no_visible_row_shows_a_future_token(built, cid, at):
    built.set_now(at)
    day0 = parity.ms(f"{DAY}T00:00:00Z")
    tokens = SCENARIO["future_tokens"][cid]
    # Records written before the bundle day cannot hold a token from the day's future (DATA.md 34).
    hits = [(row["handle"], tok) for row in visible_rows(built) if row["t_event"] >= day0 for tok in tokens if tok in shown(row)]
    assert hits == []
    built.clear_now()


def test_reference_files_show_nothing_from_after_the_first_checkpoint(built):
    """`ref` rows have no clock, so what they show must be safe at every checkpoint."""
    first_id, first_at = CHECKPOINTS[0]
    first = built.resolve_time(first_at)
    for source, name, native, flags in built.conn.execute("SELECT source, name, native, flags FROM ref"):
        text = store.render_native(native, flags)
        late = [t for t in times_in(text, built.integration.zone(source)) if t >= first]
        assert not late, (source, name)
        assert not [tok for tok in SCENARIO["future_tokens"][first_id] if tok in text], (source, name)


def test_withheld_fields_are_named_kept_out_of_attrs_and_would_have_leaked(built):
    """Store rule 6. Each field is in the served bytes, named in `flags.withheld`, absent from `attrs` and
    from what `render_native` shows; and without the rule a served value from after `now` would be shown."""
    expect = {("datadog", "monitors"): {"overall_state"}, ("pagerduty", "services"): {"status"},
              ("solarwinds", "alerts"): {"ResetDateTime", "Acknowledged", "AcknowledgedBy", "AcknowledgedDateTime"}}
    slack = {"reply_count", "reply_users", "reply_users_count", "latest_reply", "reactions"}
    found = Counter()
    for source, dataset, native, attrs, flags in built.conn.execute("SELECT source, dataset, native, attrs, flags FROM record WHERE json_extract(flags, '$.withheld') IS NOT NULL"):
        withheld = set(json.loads(flags)["withheld"])
        served, rendered = json.loads(native), json.loads(store.render_native(native, flags))
        assert withheld == (expect[(source, dataset)] if source != "slack" else withheld & slack) and withheld, (source, dataset)
        assert withheld <= set(served) and not withheld & set(rendered) and not withheld & set(json.loads(attrs))
        assert {k: v for k, v in served.items() if k not in withheld} == rendered
        found.update((source, f) for f in withheld)
    assert found[("datadog", "overall_state")] == 12 and found[("pagerduty", "status")] == 4 and found[("solarwinds", "ResetDateTime")] == 3
    assert all(found[("slack", f)] > 0 for f in slack)
    # Every record that carries one of these fields withholds it.
    for (native, flags) in built.conn.execute("SELECT native, flags FROM record WHERE source = 'slack'"):
        assert set(json.loads(native)) & slack == set(json.loads(flags).get("withheld", []))

    refs = {(s, n): (json.loads(native), json.loads(store.render_native(native, flags))) for s, n, native, flags in built.conn.execute("SELECT source, name, native, flags FROM ref")}
    served, rendered = refs[("slack", "channels")]
    assert all("members" in c for c in served) and not any("members" in c for c in rendered)
    assert refs[("slack", "users")][0] == refs[("slack", "users")][1]
    for (source, name), (served, rendered) in refs.items():
        if source == "datadog":
            assert "to_date" in served and "to_date" not in rendered and rendered["query"] == served["query"]
            assert all({"end", "length", "pointlist"} <= set(s) for s in served["series"])
            assert not any({"end", "length", "pointlist"} & set(s) for s in rendered["series"])

    # The rule earns its keep: at the first checkpoint, raw `native` of visible rows and of the reference
    # files does carry later times, and the rendered form carries none (the tests above).
    now = built.set_now(CHECKPOINTS[0][1])
    raw_late = [r["handle"] for r in visible_rows(built) if json.loads(r["flags"]).get("withheld")
                and max(times_in(r["native"], built.integration.zone(r["source"])), default=0) >= now]
    assert any(h.startswith("slack:") for h in raw_late)
    assert any(max(times_in(native, UTC), default=0) >= now for (native,) in built.conn.execute("SELECT native FROM ref WHERE source = 'datadog'"))
    built.clear_now()
