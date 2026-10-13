"""The tagging pass (docs/decisions/ingestion-decisions.md section 4, build row 6), offline. Nothing here
needs a key or the network: the classifier is a function passed in, and the SDK path runs on a mock
transport."""
import json
import subprocess
import sys

import pytest
import yaml

import parity
from smalls.tools import store
from smalls.tools.ingest.base import Record
from smalls.tools.ingest.tagging import cache, measure, profiles, run, units
from smalls_data import prompt_lint

BUNDLE = parity.BUNDLE
TAXONOMY = {   # ruling O5, in request order
    "signal": ["latency", "errors", "saturation", "availability", "traffic", "change", "security", "routine", "other"],
    "act": ["observation", "hypothesis", "action_taken", "action_proposed", "question", "status", "social", "other"],
    "change_kind": ["release", "rollback", "config", "infra", "network", "data", "job", "other"],
}


@pytest.fixture(scope="module")
def records():
    return store.read(BUNDLE)[1]


@pytest.fixture(scope="module")
def all_units(records):
    return measure.units_of(records)


def fake_ask(choice_for=lambda state, options: options[0], p=0.97, calls=None):
    def ask(state, question):
        options = list(question["criteria"])
        choice = choice_for(state, options)
        rest = round((1 - p) / (len(options) - 1), 4)
        if calls is not None:
            calls.append(state)
        return {"classifier": "test-version", "answer": {"choice": choice, "confidence": p, "probabilities": {o: (p if o == choice else rest) for o in options}},
                "usage": {"input_tokens": 400, "output_tokens": 30}, "request_id": "req"}
    return ask


# -- profiles ---------------------------------------------------------------------------------------------

def test_the_taxonomy_is_three_facets_with_other_last():
    got = {facet: list(p.options) for facet, p in profiles.profiles().items()}
    assert got == TAXONOMY
    assert list(profiles.profiles()["signal"].options)[-2:] == ["routine", "other"]
    for p in profiles.profiles().values():
        question = p.question()
        assert question["type"] == "choice" and list(question["criteria"]) == list(p.options)
        assert all(meaning and "\n" not in meaning for meaning in p.options.values())       # one line of meaning each
        assert "other" not in p.vocabulary()
    assert profiles.profiles()["act"].vocabulary()[-1] == "automated"


def test_a_shuffled_question_keeps_the_options_and_keeps_other_last():
    p = profiles.profiles()["signal"]
    shuffled = list(p.question("seed-1")["criteria"])
    assert sorted(shuffled) == sorted(p.options) and shuffled[-1] == "other" and shuffled != list(p.options)
    assert shuffled == list(p.question("seed-1")["criteria"])


def test_the_pinned_classifier_version_lives_only_in_the_tagging_config():
    version = profiles.classifier_version()
    assert version and 0.8 <= profiles.floor() <= 1
    config = profiles.HERE / "config.toml"
    holders = []
    for folder in ("smalls", "smalls_data", "tests", "docs", "eval/tags", "tags"):
        for path in sorted((parity.ROOT / folder).rglob("*")):
            if path.is_file() and path.suffix in (".py", ".toml", ".md", ".yaml", ".yml", ".jsonl", ".json") and version in path.read_text(errors="ignore"):
                holders.append(path.relative_to(parity.ROOT).as_posix())
    for name in ("Makefile", "CLAUDE.md", "pyproject.toml", "Dockerfile", "compose.yaml"):
        if version in (parity.ROOT / name).read_text():
            holders.append(name)
    # The cache and the repeats record the version that answered: that is data, not a second pin.
    assert [h for h in holders if not h.endswith(".jsonl")] == [config.relative_to(parity.ROOT).as_posix()]


def test_no_profile_contains_a_bundle_string():
    texts = prompt_lint.prompt_texts()
    assert len(texts) >= 28 and prompt_lint.find(texts) == []
    strings = prompt_lint.bundle_strings()
    assert {"PayCore", "pay-db-01", "Elena Rossi", "Elena", "payments-gw", "Larkspur Retail", "HikariPool-1", "pgw_app"} <= set(strings)
    planted = [("profile x", "A pool such as HikariPool-1 is full."), ("profile y", "the gateway on PAY-DB-01"), ("profile z", "ask elena"),
               ("profile ok", "A finite resource is full or running out: pools, queues, connections.")]
    hits = prompt_lint.find(planted, strings)
    assert len([h for h in hits if h.startswith("profile x")]) == 2          # the whole string and the seed inside it
    assert any(h.startswith("profile y") for h in hits) and any(h.startswith("profile z") for h in hits)
    assert not any(h.startswith("profile ok") for h in hits)


# -- units ------------------------------------------------------------------------------------------------

def test_a_unit_is_the_records_own_text_and_nothing_else(records, all_units):
    kinds = {(u.facet, u.kind) for u in all_units.values()}
    assert kinds == {("signal", "log line"), ("signal", "alert"), ("signal", "alert definition"), ("signal", "incident ticket"),
                     ("act", "chat message"), ("act", "work note"), ("change_kind", "change request"), ("change_kind", "pipeline run"),
                     ("change_kind", "commit")}
    assert {u.kind for u in all_units.values()} <= set(profiles.KINDS)
    assert all(set(u.state) == {"kind", "text"} and u.text for u in all_units.values())
    # A copy of a record with every other field changed gives the same unit: nothing but its text is read.
    r = next(r for r in records if r.kind == "change")
    twin = Record(handle="snow:CHG0000001", source="elsewhere", dataset="x", kind="change", path="p", locator="/9", native="{}", t_event=1, t_known=2,
                  title=r.title, body=r.body)
    assert units.unit_for(twin) == units.unit_for(r)
    assert units.tag_key(units.unit_for(twin)) == units.tag_key(units.unit_for(r))


def test_records_share_units(records, all_units):
    logs = [r for r in records if r.kind == "log"]
    assert len({units.unit_for(r).id for r in logs}) == len({r.template for r in logs}) == 100
    live = {units.unit_for(r).id for r in records if r.handle == "snow:INC0048811"}
    assert len(live) < 15                                                    # 15 versions, fewer distinct texts
    events = [r for r in records if r.source == "datadog" and r.dataset == "events"]
    assert len({units.unit_for(r).id for r in events}) < len(events) / 2      # triggered and recovered are one alert
    assert not [r.title for r in events if units.unit_for(r).text.startswith(("[Triggered", "[Recovered", "[Warn"))]
    # The two versions of a build are one unit: the state leaves out what changes between them.
    builds = [r for r in records if r.kind == "build"]
    assert len({(r.handle, units.unit_for(r).id) for r in builds}) == len({r.handle for r in builds})
    assert 290 <= len(all_units) <= 330


def test_bot_posts_are_automated_by_code_and_system_messages_are_untagged(records):
    messages = [r for r in records if r.kind == "message"]
    bots = [r for r in messages if r.attrs.get("subtype") == "bot_message"]
    assert len(bots) == 28 and all(units.code_tags(r) == {"act": "automated"} and units.unit_for(r) is None for r in bots)
    joins = [r for r in messages if r.attrs.get("subtype") == "channel_join"]
    assert joins and all(units.unit_for(r) is None and units.code_tags(r) == {} for r in joins)
    assert len([r for r in messages if units.unit_for(r)]) == 81
    untagged = {r.kind for r in records if units.unit_for(r) is None and not units.code_tags(r)}
    assert {"doc", "ci", "user", "group", "config", "entity", "status", "audit", "rel"} <= untagged


def test_the_tag_key_changes_with_the_classifier_version_and_the_profile_version(all_units):
    unit = next(iter(all_units.values()))
    p = profiles.profiles()[unit.facet]
    bumped = profiles.Profile(p.facet, p.version + 1, p.instructions, p.options)
    keys = {units.tag_key(unit), units.tag_key(unit, version="another-version"), units.tag_key(unit, bumped)}
    assert len(keys) == 3 and all(len(k) == 64 for k in keys)


# -- the rule that applies a tag (T2) ---------------------------------------------------------------------

def test_a_tag_is_applied_only_at_high_confidence():
    p = profiles.profiles()["signal"]
    def answer(choice, prob):
        return {"choice": choice, "probabilities": {choice: prob}}
    assert cache.decide(answer("errors", 0.93), p, 0.8) == "errors"
    assert cache.decide(answer("errors", 0.80), p, 0.8) == "errors"
    assert cache.decide(answer("errors", 0.79), p, 0.8) is None             # a doubtful answer stores nothing
    assert cache.decide(answer("other", 0.99), p, 0.8) is None              # `other` is never a tag
    assert cache.decide(answer("made_up", 0.99), p, 0.8) is None            # outside the closed vocabulary
    assert cache.decide(answer("observation", 0.99), p, 0.8) is None        # another facet's option
    assert cache.decide({"choice": "errors"}, p, 0.8) is None               # no probability, no tag
    assert cache.decide(answer("errors", 0.93), p, 0.95) is None            # raising the floor needs no new call


# -- the cache and the runner -----------------------------------------------------------------------------

def test_fill_asks_once_per_missing_unit_and_a_second_run_asks_nothing(all_units, tmp_path):
    path = tmp_path / "cache.jsonl"
    calls = []
    first = run.fill(all_units, fake_ask(calls=calls), path=path, concurrency=8)
    assert first["requests"] == len(calls) == len(all_units) and first["cached"] == 0 and not first["failures"]
    assert first["input_tokens"] == 400 * len(all_units)
    again = []
    second = run.fill(all_units, fake_ask(calls=again), path=path)
    assert second["requests"] == 0 and again == [] and second["cached"] == len(all_units)
    lines = path.read_text().splitlines()
    keys = [json.loads(x)["tag_key"] for x in lines]
    assert keys == sorted(keys) and len(keys) == len(all_units)
    entry = json.loads(lines[0])
    assert set(entry) == {"tag_key", "profile", "classifier", "answer", "usage", "request_id"}
    assert set(entry["answer"]) == {"choice", "confidence", "probabilities"}
    # No record text in the cache: no unit's text appears in it.
    body = path.read_text()
    assert not [u.id for u in all_units.values() if len(u.text) > 12 and u.text in body]


def test_a_failed_request_is_reported_and_asked_again_next_time(all_units, tmp_path):
    path = tmp_path / "cache.jsonl"
    some = dict(list(all_units.items())[:12])
    def flaky(state, question):
        if "<*>" in state["text"] or len(state["text"]) % 2:
            raise RuntimeError("503 overloaded")
        return fake_ask()(state, question)
    first = run.fill(some, flaky, path=path)
    assert first["failures"] and first["requests"] + len(first["failures"]) == 12
    second = run.fill(some, fake_ask(), path=path)
    assert second["requests"] == len(first["failures"]) and not second["failures"]


def test_ingest_applies_cached_tags_and_counts_misses(all_units, tmp_path, monkeypatch):
    path = tmp_path / "cache.jsonl"
    half = dict(list(sorted(all_units.items()))[::2])
    def choose(state, options):
        return "other" if state["kind"] == "commit" else options[0]
    run.fill(half, fake_ask(choose, p=0.97), path=path)
    monkeypatch.setenv("SMALLS_TAG_CACHE", str(path))
    built = store.build(BUNDLE, tags=True)
    t = built.report.tags
    assert t["mode"] == "on" and t["units"] == len(all_units) and t["hits"] == len(half) and t["misses"] == len(all_units) - len(half)
    assert t["applied"] + t["other"] + t["below_floor"] == t["hits"] and t["by_code"] == 28
    vocab = {facet: p.vocabulary() for facet, p in profiles.profiles().items()}
    tagged = 0
    for tag_key, tags in built.conn.execute("SELECT tag_key, tags FROM record"):
        for facet, value in json.loads(tags).items():
            assert value in vocab[facet]
            tagged += 1
    assert tagged > 0
    assert built.conn.execute("SELECT count(DISTINCT tag_key) FROM tag_answer").fetchone()[0] == len(half)
    assert built.conn.execute("SELECT count(*) FROM tag_answer WHERE chosen = 1").fetchone()[0] == len(half)
    # A miss leaves the record untagged; `other` and a low probability store nothing.
    assert built.conn.execute("SELECT count(*) FROM record WHERE kind = 'commit' AND tags != '{}'").fetchone()[0] == 0
    run.fill(all_units, fake_ask(p=0.5), path=tmp_path / "low.jsonl")
    monkeypatch.setenv("SMALLS_TAG_CACHE", str(tmp_path / "low.jsonl"))
    low = store.build(BUNDLE, tags=True)
    assert low.report.tags["applied"] == 0 and low.report.tags["misses"] == 0
    assert low.conn.execute("SELECT count(*) FROM record WHERE tags != '{}' AND tags != '{\"act\":\"automated\"}'").fetchone()[0] == 0


def test_tags_are_off_by_default_and_the_store_is_as_it_was_before_tagging(monkeypatch):
    monkeypatch.delenv("SMALLS_TAGS", raising=False)
    built = store.build(BUNDLE)
    assert built.report.tags == {"mode": "off"}
    assert built.conn.execute("SELECT count(*) FROM record WHERE tags != '{}' OR tag_key IS NOT NULL").fetchone()[0] == 0
    assert built.conn.execute("SELECT count(*) FROM tag_answer").fetchone()[0] == 0
    assert parity.problems(built.conn, parity.manifest()) == []
    monkeypatch.setenv("SMALLS_TAGS", "on")
    on = store.build(BUNDLE)
    assert on.report.tags["mode"] == "on" and parity.problems(on.conn, parity.manifest()) == []


def test_ingest_with_tags_on_opens_no_socket_and_never_imports_the_sdk():
    code = """
import socket, sys
def refuse(*a, **k): raise AssertionError('ingest opened a socket')
socket.socket = refuse
socket.create_connection = refuse
socket.getaddrinfo = refuse
from smalls.tools import store
built = store.build(sys.argv[1], tags=True)
assert built.report.tags['mode'] == 'on'
loaded = [m for m in sys.modules if m.split('.')[0] in ('typesafe_sdk', 'httpx2', 'httpcore2', 'httpx', 'urllib3', 'requests', 'smalls_data')]
assert not loaded, loaded
assert 'smalls.tools.ingest.tagging.run' not in sys.modules
print('offline', built.report.tags['units'])
"""
    env = {"PATH": "/usr/bin:/bin", "SMALLS_TAGS": "on"}      # no key in the environment at all
    out = subprocess.run([sys.executable, "-c", code, str(BUNDLE)], capture_output=True, text=True, env=env, cwd=parity.ROOT)
    assert out.returncode == 0, out.stderr
    assert out.stdout.startswith("offline ")


def test_smalls_tag_refuses_to_run_without_a_key(monkeypatch):
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="JEV_API_KEY is not set"):
        run.sdk_ask()


def test_the_sdk_path_runs_on_a_mock_transport(monkeypatch):
    sdk = pytest.importorskip("typesafe_sdk")
    httpx2 = pytest.importorskip("httpx2")
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append((request.headers.get("authorization"), body))
        options = list(body["questions"]["q"]["criteria"])
        return httpx2.Response(200, headers={"x-typesafe-request-id": "req_test"},
                               json={"model": body["model"], "usage": {"input_tokens": 321, "output_tokens": 12},
                                     "answers": {"q": {"type": "choice", "choice": options[1], "confidence": 0.9,
                                                       "probabilities": {o: (0.9 if o == options[1] else 0.0) for o in options}}}})

    real = sdk.TypeSafeClient
    monkeypatch.setattr(sdk, "TypeSafeClient", lambda **kw: real(transport=httpx2.MockTransport(handler), **kw))
    monkeypatch.setenv("JEV_API_KEY", "test-key-not-a-secret")
    ask = run.sdk_ask()
    p = profiles.profiles()["act"]
    got = ask({"kind": "chat message", "text": "is it the database?"}, p.question())
    assert got["answer"]["choice"] == "hypothesis" and got["usage"] == {"input_tokens": 321, "output_tokens": 12}
    auth, body = seen[0]
    assert auth == "Bearer test-key-not-a-secret"                           # passed explicitly, from JEV_API_KEY
    assert body["model"] == profiles.classifier_version() and body["state"] == {"kind": "chat message", "text": "is it the database?"}
    assert set(body["questions"]) == {"q"} and list(body["questions"]["q"]["criteria"]) == list(p.options)


# -- the labelled sample and the measure ------------------------------------------------------------------

def test_the_labels_are_the_fixed_sample_and_every_label_is_an_option(all_units):
    labels = measure.load_labels()
    sample = measure.sample(all_units)
    assert [lab["unit"] for lab in labels] == [u.id for u in sample] and 55 <= len(labels) <= 65
    for lab, unit in zip(labels, sample):
        assert (lab["facet"], lab["kind"], lab["text"]) == (unit.facet, unit.kind, unit.text)
        assert lab["label"] in profiles.profiles()[unit.facet].options
    per_facet = {facet: sum(1 for lab in labels if lab["facet"] == facet) for facet in TAXONOMY}
    assert all(18 <= n <= 22 for n in per_facet.values()), per_facet


def _row(facet, label, choice, p, repeats=()):
    def a(c, prob):
        return {"choice": c, "probabilities": {c: prob}}
    return {"unit": f"{facet}-{label}-{choice}-{p}", "facet": facet, "kind": "x", "label": label, "answer": a(choice, p), "repeats": [a(c, q) for c, q in repeats]}


def test_the_table_counts_precision_coverage_and_flips():
    rows = [_row("signal", "errors", "errors", 0.95, [("errors", 0.93), ("errors", 0.96), ("errors", 0.9)]),
            _row("signal", "latency", "errors", 0.85, [("errors", 0.85)] * 3),        # applied and wrong
            _row("signal", "routine", "routine", 0.6, [("routine", 0.7)] * 3),        # too doubtful: no tag
            _row("signal", "other", "other", 0.99, [("other", 0.99)] * 3),            # right to store nothing
            _row("signal", "change", "change", 0.88, [("change", 0.9), ("change", 0.75), ("routine", 0.6)])]   # flips
    t = measure.table(rows, 0.8)["signal"]
    assert (t["labelled"], t["applied"], t["correct"], t["flips"], t["value_flips"], t["repeats"]) == (5, 3, 2, 1, 0, 3)
    assert t["precision"] == pytest.approx(2 / 3) and t["coverage"] == pytest.approx(3 / 5)
    assert t["confused"] == {"latency tagged errors": 1}
    assert measure.table(rows, 0.8)["act"]["precision"] is None
    assert "| `signal` | 5 | 3 | 2 | 2 of 3 (67%) | 60% | 1 over 3 repeats | 0 | latency tagged errors (1) |" in measure.render(rows, 0.8)
    # A repeat that applies a different value is the flip that matters; a tag only a repeat would apply is not one.
    swapped = [_row("signal", "errors", "errors", 0.9, [("saturation", 0.9), ("errors", 0.9), ("errors", 0.9)]),
               _row("signal", "errors", "errors", 0.7, [("errors", 0.9)] * 3)]
    t = measure.table(swapped, 0.8)["signal"]
    assert (t["applied"], t["flips"], t["value_flips"]) == (1, 1, 1)


def test_the_floor_rises_until_nothing_flips_and_precision_meets_the_bar():
    steady = [_row("act", "question", "question", 0.99, [("question", 0.99)] * 3) for _ in range(9)]
    assert measure.final_floor(steady) == (0.8, [])
    wrong_low = steady + [_row("act", "status", "social", 0.86, [("social", 0.86)] * 3), _row("act", "status", "social", 0.84, [("social", 0.84)] * 3)]
    assert measure.final_floor(wrong_low) == (0.85, [])                       # 9 of 10 at 0.85; 9 of 11 below it
    flipping = steady + [_row("act", "status", "status", 0.92, [("status", 0.92), ("status", 0.89), ("status", 0.91)])]
    assert measure.final_floor(flipping) == (0.8, [])                         # every repeat still applies it
    flipping = steady + [_row("act", "status", "status", 0.92, [("status", 0.92), ("status", 0.78), ("status", 0.91)])]
    assert measure.final_floor(flipping) == (0.93, [])                        # raised until no repeat applies it
    hopeless = [_row("change_kind", "other", "release", 1.0, [("release", 1.0)] * 3)] * 3
    floor, below = measure.final_floor(steady + hopeless)
    assert floor == 0.8 and below == ["change_kind"]                          # stored, not offered as a filter
