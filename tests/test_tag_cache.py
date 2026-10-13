"""The committed tag cache against the committed bundle (docs/decisions/ingestion-decisions.md, build row 6).
With SMALLS_TAGS=on there are zero cache misses, every applied tag is an option of its facet at or above the
floor, and the floor in config is the one the hand-labelled sample measures. All offline."""
import json

import pytest

import parity
from smalls.tools import store
from smalls.tools.ingest.tagging import cache, measure, profiles, units

BUNDLE = parity.BUNDLE
TAG_COLUMNS = ("tag_key", "tags")


@pytest.fixture(scope="module")
def tagged():
    return store.build(BUNDLE, tags=True)


@pytest.fixture(scope="module")
def all_units():
    _, records, points, _ = store.read(BUNDLE)
    return measure.units_of(records, points)


@pytest.fixture(scope="module")
def outcomes(all_units):
    return measure.outcomes(measure.load_labels(), cache.read(cache.DEFAULT_CACHE), measure.load_repeats(), all_units)


def test_the_committed_cache_covers_every_unit_with_zero_misses(tagged, all_units):
    t = tagged.report.tags
    assert t["mode"] == "on" and t["misses"] == 0 and t["hits"] == t["units"] == len(all_units) == 325
    assert t["applied"] + t["below_floor"] + t["other"] == t["units"]
    entries = cache.read(cache.DEFAULT_CACHE)
    assert {units.tag_key(u) for u in all_units.values()} <= set(entries)


def test_every_applied_tag_is_in_its_vocabulary_at_or_above_the_floor(tagged):
    floor = profiles.floor()
    vocabulary = profiles.facets()
    p_of = {(key, facet, value): p for key, facet, value, p in tagged.conn.execute("SELECT tag_key, facet, value, p FROM tag_answer")}
    chosen = {(key, facet): value for key, facet, value, _ in tagged.conn.execute("SELECT tag_key, facet, value, p FROM tag_answer WHERE chosen = 1")}
    applied = by_code = 0
    rows = tagged.conn.execute("SELECT kind, json_extract(attrs, '$.subtype'), tag_key, tags FROM record WHERE tags != '{}' "
                               "UNION ALL SELECT 'point', NULL, tag_key, tags FROM point WHERE tags != '{}'").fetchall()
    for kind, subtype, tag_key, tags in rows:
        for facet, value in json.loads(tags).items():
            multi = facet in profiles.multi_facets()
            assert isinstance(value, list) == multi
            for v in value if multi else [value]:
                assert v in vocabulary[facet] and v != profiles.OTHER
                if v in profiles.CODE_VALUES.get(facet, ()):
                    assert (kind, subtype, tag_key) == ("message", "bot_message", None)      # set in code, with no request
                    by_code += 1
                    continue
                assert p_of[(tag_key, facet, v)] >= floor
                assert multi or chosen[(tag_key, facet)] == v                                  # pick-one: it is the classifier's choice
                applied += 1
    assert applied > 2500 and by_code == 28
    # And nothing doubtful is stored: no value under the floor sits in any row's tags.
    under = {(key, facet, value) for (key, facet, value), p in p_of.items() if p < floor}
    for _, _, tag_key, tags in rows:
        for facet, value in json.loads(tags).items():
            assert not [v for v in (value if isinstance(value, list) else [value]) if (tag_key, facet, v) in under]
    # Every option's probability is kept, so the floor can move with no new call.
    known = profiles.profiles()
    assert tagged.conn.execute("SELECT count(DISTINCT value) FROM tag_answer WHERE facet = 'act'").fetchone()[0] == len(known["act"].options)
    assert tagged.conn.execute("SELECT count(DISTINCT value) FROM tag_answer WHERE facet = 'change_kind'").fetchone()[0] == len(known["change_kind"].options)


def test_the_cache_holds_answers_and_no_record_text(all_units):
    entries = [json.loads(x) for x in cache.DEFAULT_CACHE.read_text().splitlines()]
    assert [e["tag_key"] for e in entries] == sorted(e["tag_key"] for e in entries)
    known = profiles.profiles()
    current = {units.tag_key(u) for u in all_units.values()}
    for e in entries:
        assert set(e) == {"tag_key", "profile", "classifier", "answer", "usage", "request_id"}
        name, version = e["profile"].split("@")
        assert name in known and int(version) <= known[name].version and e["classifier"] == profiles.classifier_version()
        assert e["usage"]["input_tokens"] > 0
        if e["tag_key"] in current:
            assert e["profile"] == known[name].id
            assert set(e["answer"]) == ({"nouls"} if known[name].multi else {"choice", "confidence", "probabilities"})
    # Answers to a superseded profile version stay as the record of what was asked; ingest never reads them.
    superseded = [e for e in entries if e["tag_key"] not in current]
    assert {e["profile"] for e in superseded} == {"act@1"} and len(superseded) == 110
    body = cache.DEFAULT_CACHE.read_text()
    assert not [u.id for u in all_units.values() if len(u.text) > 12 and u.text in body]
    assert "typesafe" not in body.lower() and "api_key" not in body.lower()


def test_the_repeats_cover_the_labelled_sample(outcomes):
    assert len(outcomes) == len(measure.load_labels()) == 89
    assert all(r["answer"] is not None and len(r["repeats"]) == profiles.config()["run"]["repeats"] == 3 for r in outcomes)
    assert {r["repeat"] for r in measure.load_repeats() if r["shuffled"]} == {3}


def test_the_configured_floor_is_the_measured_one(outcomes):
    floor, below_the_bar = measure.final_floor(outcomes)
    assert floor == profiles.floor() == 0.9 and floor >= measure.START
    assert below_the_bar == []                                # every facet reaches 9 in 10 at some floor
    for facet, row in measure.table(outcomes, floor).items():
        assert row["stored"] > 0 and row["flips"] == 0 and row["correct"] / row["stored"] >= measure.BAR, facet
    # Below it, the sample shows either a value a repeat does not reproduce or a facet under the bar.
    for step in range(int(round((floor - measure.START) * 100))):
        lower = measure.table(outcomes, round(measure.START + step / 100, 2))
        assert any(r["flips"] or (r["stored"] and r["correct"] / r["stored"] < measure.BAR) for r in lower.values())
    # No pick-one tag changes value at any floor from the starting one up: the instability is doubt, not disagreement.
    assert all(r["value_flips"] == 0 for step in range(21) for r in measure.table(outcomes, round(measure.START + step / 100, 2)).values())
    # Without the tolerance the same rule lands on 0.96: at 0.90 the only misses are values that come back 0.01 or 0.02 lower.
    strict = [round(measure.START + i / 100, 2) for i in range(21)
              if all(measure._passes(r) for r in measure.table(outcomes, round(measure.START + i / 100, 2), tol=0.0).values())]
    assert strict[0] == 0.96


def test_tags_change_nothing_but_the_tag_columns(tagged):
    plain = store.build(BUNDLE, tags=False)
    assert parity.problems(tagged.conn, parity.manifest()) == []
    for table in ("record", "point"):
        names = [d[1] for d in plain.conn.execute(f"PRAGMA table_info({table})")]
        rest = ", ".join(n for n in names if n not in TAG_COLUMNS)
        assert plain.conn.execute(f"SELECT {rest} FROM {table}").fetchall() == tagged.conn.execute(f"SELECT {rest} FROM {table}").fetchall()
    for table in ("chunk", "ref"):
        assert plain.conn.execute(f"SELECT * FROM {table}").fetchall() == tagged.conn.execute(f"SELECT * FROM {table}").fetchall()
    assert plain.conn.execute("SELECT count(*) FROM tag_answer").fetchone()[0] == 0
    assert tagged.dump_hash() == store.build(BUNDLE, tags=True).dump_hash() != plain.dump_hash()


def test_metric_series_carry_their_signal_on_every_point(tagged):
    series = dict(tagged.conn.execute("SELECT series, count(DISTINCT tags) FROM point GROUP BY series").fetchall())
    assert len(series) == 14 and set(series.values()) == {1}                  # one tag per series, on every bucket
    by_signal = dict(tagged.conn.execute("SELECT json_extract(tags, '$.signal'), count(DISTINCT series) FROM point GROUP BY 1").fetchall())
    assert sum(n for k, n in by_signal.items() if k) == 13 and set(by_signal) - {None} <= set(profiles.profiles()["signal_metric"].values())


def test_a_tag_is_visible_exactly_when_its_record_version_is(tagged):
    """Rules T1 and T9: a tag rides on its row, so facet counts through the views see only what the clock shows."""
    sql = "SELECT json_extract(tags, '$.signal'), count(*) FROM {} WHERE json_extract(tags, '$.signal') IS NOT NULL GROUP BY 1"
    total = dict(tagged.conn.execute(sql.format("record")).fetchall())
    tagged.clear_now()
    assert tagged.conn.execute(sql.format("visible")).fetchall() == [] and tagged.conn.execute(sql.format("visible_point")).fetchall() == []
    seen = {}
    for at in ("14:40", "14:52"):
        tagged.set_now(at)
        seen[at] = dict(tagged.conn.execute(sql.format("visible")).fetchall())
        assert 0 < tagged.conn.execute("SELECT count(*) FROM visible_point WHERE tags != '{}'").fetchone()[0] < tagged.conn.execute("SELECT count(*) FROM point WHERE tags != '{}'").fetchone()[0]
    tagged.clear_now()
    assert all(seen["14:40"].get(k, 0) <= seen["14:52"].get(k, 0) <= total[k] for k in total)
    assert sum(seen["14:40"].values()) < sum(seen["14:52"].values()) < sum(total.values())
