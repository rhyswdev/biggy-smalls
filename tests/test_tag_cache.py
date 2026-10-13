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
    return measure.units_of(store.read(BUNDLE)[1])


@pytest.fixture(scope="module")
def outcomes(all_units):
    return measure.outcomes(measure.load_labels(), cache.read(cache.DEFAULT_CACHE), measure.load_repeats(), all_units)


def test_the_committed_cache_covers_every_unit_with_zero_misses(tagged, all_units):
    t = tagged.report.tags
    assert t["mode"] == "on" and t["misses"] == 0 and t["hits"] == t["units"] == len(all_units)
    assert t["applied"] + t["below_floor"] + t["other"] == t["units"]
    entries = cache.read(cache.DEFAULT_CACHE)
    assert {units.tag_key(u) for u in all_units.values()} <= set(entries)


def test_every_applied_tag_is_in_its_vocabulary_at_or_above_the_floor(tagged):
    floor = profiles.floor()
    known = profiles.profiles()
    chosen = {(key, facet): (value, p) for key, facet, value, p in tagged.conn.execute("SELECT tag_key, facet, value, p FROM tag_answer WHERE chosen = 1")}
    applied = by_code = 0
    for kind, subtype, tag_key, tags in tagged.conn.execute("SELECT kind, json_extract(attrs, '$.subtype'), tag_key, tags FROM record WHERE tags != '{}'"):
        for facet, value in json.loads(tags).items():
            assert value in known[facet].vocabulary()
            if value in profiles.CODE_VALUES.get(facet, ()):
                assert (kind, subtype, tag_key) == ("message", "bot_message", None)      # set in code, with no request
                by_code += 1
                continue
            answered, p = chosen[(tag_key, facet)]
            assert answered == value and value != profiles.OTHER and p >= floor
            applied += 1
    assert applied > 1500 and by_code == 28
    # And nothing that should be untagged is tagged: `other`, or a probability under the floor.
    doubtful = tagged.conn.execute("SELECT count(*) FROM record r JOIN tag_answer a ON a.tag_key = r.tag_key AND a.chosen = 1 "
                                   "WHERE (a.value = 'other' OR a.p < ?) AND json_extract(r.tags, '$.' || a.facet) IS NOT NULL", (floor,)).fetchone()[0]
    assert doubtful == 0
    # Every option's probability is kept, so the floor can move with no new call.
    for facet, n in tagged.conn.execute("SELECT facet, count(DISTINCT value) FROM tag_answer GROUP BY facet"):
        assert n == len(known[facet].options)


def test_the_cache_holds_answers_and_no_record_text(all_units):
    lines = cache.DEFAULT_CACHE.read_text().splitlines()
    entries = [json.loads(x) for x in lines]
    assert [e["tag_key"] for e in entries] == sorted(e["tag_key"] for e in entries)
    known = {p.id for p in profiles.profiles().values()}
    for e in entries:
        assert set(e) == {"tag_key", "profile", "classifier", "answer", "usage", "request_id"}
        assert e["profile"] in known and e["classifier"] == profiles.classifier_version()
        assert set(e["answer"]) == {"choice", "confidence", "probabilities"} and e["usage"]["input_tokens"] > 0
    body = cache.DEFAULT_CACHE.read_text()
    assert not [u.id for u in all_units.values() if len(u.text) > 12 and u.text in body]
    assert "typesafe" not in body.lower() and "api_key" not in body.lower()


def test_the_repeats_cover_the_labelled_sample(outcomes):
    assert len(outcomes) == len(measure.load_labels()) == 61
    assert all(r["answer"] is not None and len(r["repeats"]) == profiles.config()["run"]["repeats"] == 3 for r in outcomes)
    repeats = measure.load_repeats()
    assert {r["repeat"] for r in repeats if r["shuffled"]} == {3} and len(repeats) == 3 * 61


def test_the_configured_floor_is_the_measured_one(outcomes):
    floor, below_the_bar = measure.final_floor(outcomes)
    assert floor == profiles.floor() and floor >= measure.START
    assert below_the_bar == []                                # every facet reaches 9 in 10 at some floor
    for facet, row in measure.table(outcomes, floor).items():
        assert row["applied"] > 0 and row["flips"] == 0 and row["correct"] / row["applied"] >= measure.BAR, facet
    # Below it, the sample shows either a tag a repeat does not reproduce or a facet under the bar.
    for step in range(int(round((floor - measure.START) * 100))):
        lower = measure.table(outcomes, round(measure.START + step / 100, 2))
        assert any(r["flips"] or (r["applied"] and r["correct"] / r["applied"] < measure.BAR) for r in lower.values())
    # No applied tag changes value at any floor from the starting one up: the instability is doubt, not disagreement.
    assert all(r["value_flips"] == 0 for step in range(21) for r in measure.table(outcomes, round(measure.START + step / 100, 2)).values())


def test_tags_change_nothing_but_the_tag_columns(tagged):
    plain = store.build(BUNDLE, tags=False)
    assert parity.problems(tagged.conn, parity.manifest()) == []
    names = [d[1] for d in plain.conn.execute("PRAGMA table_info(record)")]
    rest = ", ".join(n for n in names if n not in TAG_COLUMNS)
    assert plain.conn.execute(f"SELECT {rest} FROM record").fetchall() == tagged.conn.execute(f"SELECT {rest} FROM record").fetchall()
    for table in ("point", "chunk", "ref"):
        assert plain.conn.execute(f"SELECT * FROM {table}").fetchall() == tagged.conn.execute(f"SELECT * FROM {table}").fetchall()
    assert plain.conn.execute("SELECT count(*) FROM tag_answer").fetchone()[0] == 0
    assert tagged.dump_hash() == store.build(BUNDLE, tags=True).dump_hash() != plain.dump_hash()


def test_a_tag_is_visible_exactly_when_its_record_version_is(tagged):
    """Rules T1 and T9: a tag rides on its record's row, so facet counts through `visible` see only what the
    clock shows."""
    sql = "SELECT json_extract(tags, '$.signal'), count(*) FROM {} WHERE json_extract(tags, '$.signal') IS NOT NULL GROUP BY 1"
    total = dict(tagged.conn.execute(sql.format("record")).fetchall())
    tagged.clear_now()
    assert tagged.conn.execute(sql.format("visible")).fetchall() == []
    seen = {}
    for at in ("14:40", "14:52"):
        tagged.set_now(at)
        seen[at] = dict(tagged.conn.execute(sql.format("visible")).fetchall())
    tagged.clear_now()
    assert all(seen["14:40"].get(k, 0) <= seen["14:52"].get(k, 0) <= total[k] for k in total)
    assert sum(seen["14:40"].values()) < sum(seen["14:52"].values()) < sum(total.values())
