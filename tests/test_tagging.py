"""The tagging pass (docs/decisions/ingestion-decisions.md section 4, build row 6), offline. Nothing here
needs a key or the network: the classifier is a function passed in, and the SDK path runs on a mock
transport."""
import json
import subprocess
import sys

import pytest

import parity
from smalls.tools import store
from smalls.tools.ingest.base import Record
from smalls.tools.ingest.tagging import cache, measure, profiles, run, units
from smalls_data import prompt_lint

BUNDLE = parity.BUNDLE
OPTIONS = {   # ruling O5 as amended, in request order
    "signal": ["latency", "errors", "saturation", "availability", "traffic", "change", "security", "routine", "other"],
    "signal_any": ["latency", "errors", "saturation", "availability", "traffic", "change", "security", "routine"],
    "signal_metric": ["latency", "errors", "saturation", "availability", "traffic", "other"],
    "act": ["observation", "hypothesis", "action_taken", "action_proposed", "question", "status", "social"],
    "change_kind": ["release", "rollback", "config", "infra", "network", "data", "job", "other"],
}


@pytest.fixture(scope="module")
def read():
    return store.read(BUNDLE)


@pytest.fixture(scope="module")
def records(read):
    return read[1]


@pytest.fixture(scope="module")
def all_units(read):
    return measure.units_of(read[1], read[2])


def fake_ask(choice_for=lambda state, options: options[0], p=0.97, yes=lambda state, qid: 0.0, calls=None):
    """A classifier for tests: picks an option of a pick-one question at probability `p`, and answers each
    yes/no question with `yes(state, question id)`."""
    def ask(state, questions):
        answers = {}
        for qid, question in questions.items():
            if question["type"] == "noul":
                answers[qid] = {"noul": yes(state, qid)}
                continue
            options = list(question["criteria"])
            choice = choice_for(state, options)
            rest = round((1 - p) / (len(options) - 1), 4)
            answers[qid] = {"choice": choice, "confidence": p, "probabilities": {o: (p if o == choice else rest) for o in options}}
        if calls is not None:
            calls.append(state)
        return {"classifier": "test-version", "answers": answers, "usage": {"input_tokens": 400, "output_tokens": 30}, "request_id": "req"}
    return ask


# -- profiles ---------------------------------------------------------------------------------------------

def test_the_taxonomy_is_three_facets_from_five_profiles():
    known = profiles.profiles()
    assert {name: list(p.options) for name, p in known.items()} == OPTIONS
    assert profiles.facets() == {
        "signal": ("latency", "errors", "saturation", "availability", "traffic", "change", "security", "routine"),
        "act": ("observation", "hypothesis", "action_taken", "action_proposed", "question", "status", "social", "automated"),
        "change_kind": ("release", "rollback", "config", "infra", "network", "data", "job")}
    assert list(known["signal"].options)[-2:] == ["routine", "other"]          # `routine` just before `other`
    assert profiles.multi_facets() == {"act", "signal"} and known["signal_metric"].facet == known["signal_any"].facet == "signal"
    # A log line, an alert, a definition and a ticket are asked twice: pick-one, then a yes or no per option.
    assert [p.name for p in profiles.for_kind("log line")] == ["signal", "signal_any"] and [p.name for p in profiles.for_kind("metric series")] == ["signal_metric"]
    # Only the yes/no form rewords `security`, so the pick-one answers already cached stay valid.
    assert known["signal_any"].options["security"] != known["signal"].options["security"]
    assert {k: v for k, v in known["signal_any"].options.items() if k != "security"} == {k: v for k, v in known["signal"].options.items() if k not in ("security", "other")}
    for p in known.values():
        assert all(meaning and "\n" not in meaning for meaning in p.options.values())       # one line of meaning each
        assert "other" not in p.values()
        questions = p.questions()
        if p.multi:      # one yes/no question per option, each carrying that option's meaning
            assert list(questions) == list(p.options) and all(q["type"] == "noul" for q in questions.values())
            assert all(p.options[name] in q["instructions"] and "{meaning}" not in q["instructions"] for name, q in questions.items())
        else:
            assert list(questions) == ["q"] and questions["q"]["type"] == "choice" and list(questions["q"]["criteria"]) == list(p.options)
    kinds = profiles.kinds()
    assert len(kinds) == len(set(kinds)) == 10 and all(profiles.for_kind(k) for k in kinds)
    assert all(len({p.facet for p in profiles.for_kind(k)}) == 1 for k in kinds)                # one facet per kind


def test_a_shuffled_request_keeps_the_options_and_keeps_other_last():
    p = profiles.profiles()["signal"]
    shuffled = list(p.questions("seed-1")["q"]["criteria"])
    assert sorted(shuffled) == sorted(p.options) and shuffled[-1] == "other" and shuffled != list(p.options)
    assert shuffled == list(p.questions("seed-1")["q"]["criteria"])
    act = profiles.profiles()["act"]
    assert sorted(act.questions("seed-1")) == sorted(act.options) and list(act.questions("seed-1")) != list(act.options)


def test_the_pinned_classifier_version_lives_only_in_the_tagging_config():
    version = profiles.classifier_version()
    assert version and 0.8 <= profiles.floor() <= 1 and 0 <= profiles.tolerance() <= 0.05
    config = profiles.HERE / "config.toml"
    holders = []
    for folder in ("smalls", "smalls_data", "tests", "docs", "eval/tags", "tags"):
        for path in sorted((parity.ROOT / folder).rglob("*")):
            if path.is_file() and path.suffix in (".py", ".toml", ".md", ".yaml", ".yml", ".jsonl", ".json") and version in path.read_text(errors="ignore"):
                holders.append(path.relative_to(parity.ROOT).as_posix())
    for name in ("Makefile", "CLAUDE.md", "pyproject.toml", "Dockerfile", "compose.yaml"):
        if version in (parity.ROOT / name).read_text():
            holders.append(name)
    # The cache, the repeats and experiment answers record the version that answered: data, not a second pin.
    assert [h for h in holders if not h.endswith(".jsonl")] == [config.relative_to(parity.ROOT).as_posix()]


def test_no_profile_contains_a_bundle_string():
    texts = prompt_lint.prompt_texts()
    assert len(texts) >= 34 and prompt_lint.find(texts) == []
    strings = prompt_lint.bundle_strings()
    assert {"PayCore", "pay-db-01", "Elena Rossi", "Elena", "payments-gw", "Larkspur Retail", "HikariPool-1", "pgw_app"} <= set(strings)
    planted = [("profile x", "A pool such as HikariPool-1 is full."), ("profile y", "the gateway on PAY-DB-01"), ("profile z", "ask elena"),
               ("profile ok", "A finite resource is full or running out: pools, queues, connections.")]
    hits = prompt_lint.find(planted, strings)
    assert len([h for h in hits if h.startswith("profile x")]) == 2          # the whole string and the seed inside it
    assert any(h.startswith("profile y") for h in hits) and any(h.startswith("profile z") for h in hits)
    assert not any(h.startswith("profile ok") for h in hits)


def test_candidate_facets_are_linted_like_profiles():
    from smalls.tools.ingest.tagging import experiment

    specs = sorted((parity.ROOT / "eval/tags/experiments").glob("*.toml"))
    assert len(specs) == 2
    for spec in specs:
        candidates, _ = experiment.load(spec)
        assert candidates and prompt_lint.find(experiment.prompt_texts(spec)) == []


# -- units ------------------------------------------------------------------------------------------------

def test_a_unit_is_the_records_own_text_and_nothing_else(records, all_units):
    assert {u.kind for u in all_units.values()} == set(profiles.kinds())
    assert all(set(u.state) == {"kind", "text"} and u.text for u in all_units.values())
    assert {(u.facet, u.kind) for u in all_units.values()} == {
        ("signal", "log line"), ("signal", "alert"), ("signal", "alert definition"), ("signal", "incident ticket"), ("signal", "metric series"),
        ("act", "chat message"), ("act", "work note"), ("change_kind", "change request"), ("change_kind", "pipeline run"), ("change_kind", "commit")}
    # A copy of a record with every other field changed gives the same unit: nothing but its text is read.
    r = next(r for r in records if r.kind == "change")
    twin = Record(handle="snow:CHG0000001", source="elsewhere", dataset="x", kind="change", path="p", locator="/9", native="{}", t_event=1, t_known=2,
                  title=r.title, body=r.body)
    assert units.unit_for(twin) == units.unit_for(r)
    assert units.unit_key(units.unit_for(twin)) == units.unit_key(units.unit_for(r))


def test_records_share_units(read, all_units):
    _, records, points, _ = read
    logs = [r for r in records if r.kind == "log"]
    assert len({units.unit_for(r).id for r in logs}) == len({r.template for r in logs}) == 100
    assert len({units.unit_for(r).id for r in records if r.handle == "snow:INC0048811"}) == 1      # 15 versions, one text
    events = [r for r in records if r.source == "datadog" and r.dataset == "events"]
    assert len({units.unit_for(r).id for r in events}) < len(events) / 2      # triggered and recovered are one alert
    assert not [r.title for r in events if units.unit_for(r).text.startswith(("[Triggered", "[Recovered", "[Warn"))]
    # The two versions of a build are one unit: the state leaves out what changes between them.
    builds = [r for r in records if r.kind == "build"]
    assert len({(r.handle, units.unit_for(r).id) for r in builds}) == len({r.handle for r in builds})
    # Every bucket of a metric series is one unit: 1,407 points, 14 series.
    assert len(points) == 1407 and len({units.unit_for_point(p).id for p in points}) == 14
    assert len(all_units) == 325


def test_bot_posts_are_automated_by_code_and_system_messages_are_untagged(records):
    messages = [r for r in records if r.kind == "message"]
    bots = [r for r in messages if r.attrs.get("subtype") == "bot_message"]
    assert len(bots) == 28 and all(units.code_tags(r) == {"act": ["automated"]} and units.unit_for(r) is None for r in bots)
    joins = [r for r in messages if r.attrs.get("subtype") == "channel_join"]
    assert joins and all(units.unit_for(r) is None and units.code_tags(r) == {} for r in joins)
    assert len([r for r in messages if units.unit_for(r)]) == 81
    untagged = {r.kind for r in records if units.unit_for(r) is None and not units.code_tags(r)}
    assert {"doc", "ci", "user", "group", "config", "entity", "status", "audit", "rel"} <= untagged


def test_the_tag_key_changes_with_the_classifier_version_and_the_profile_version(all_units):
    unit = next(u for u in all_units.values() if u.kind == "log line")
    p, other = unit.profiles
    bumped = profiles.Profile(p.name, p.facet, p.version + 1, p.multi, p.kinds, p.instructions, p.options)
    keys = {units.tag_key(unit, p), units.tag_key(unit, p, version="another-version"), units.tag_key(unit, bumped), units.tag_key(unit, other)}
    assert len(keys) == 4 and all(len(k) == 64 for k in keys)                 # one cache entry per question asked
    # A row carries the key of its text, whichever questions were asked of it.
    assert units.unit_key(unit) not in keys and units.unit_key(unit) != units.unit_key(unit, version="another-version")


# -- the rule that applies a tag (T2) ---------------------------------------------------------------------

def test_a_tag_is_applied_only_at_high_confidence():
    p = profiles.profiles()["signal"]
    def answer(choice, prob):
        return {"choice": choice, "probabilities": {choice: prob}}
    assert cache.decide(answer("errors", 0.93), p, 0.8) == ["errors"]
    assert cache.decide(answer("errors", 0.80), p, 0.8) == ["errors"]
    assert cache.decide(answer("errors", 0.79), p, 0.8) == []               # a doubtful answer stores nothing
    assert cache.decide(answer("other", 0.99), p, 0.8) == []                # `other` is never a tag
    assert cache.decide(answer("made_up", 0.99), p, 0.8) == []              # outside the closed vocabulary
    assert cache.decide(answer("observation", 0.99), p, 0.8) == []          # another facet's option
    assert cache.decide({"choice": "errors"}, p, 0.8) == []                 # no probability, no tag
    assert cache.decide(answer("errors", 0.93), p, 0.95) == []              # raising the floor needs no new call


def test_a_yes_no_profile_stores_every_confident_yes_and_nothing_else():
    act = profiles.profiles()["act"]
    answer = {"nouls": {"observation": 0.95, "hypothesis": 0.90, "action_taken": 0.89, "question": 0.02, "made_up": 0.99}}
    assert cache.decide(answer, act, 0.9) == ["observation", "hypothesis"]      # in the profile's order; a no stores nothing
    assert cache.decide(answer, act, 0.96) == [] and cache.decide({"nouls": {}}, act, 0.9) == []
    assert cache.as_tag(["observation", "hypothesis"], "act") == ["observation", "hypothesis"]
    assert cache.as_tag(["errors"], "signal") == ["errors"] and cache.as_tag(["release"], "change_kind") == "release"
    rows = cache.answer_rows("k", {"nouls": {"observation": 0.95, "question": 0.02}}, act)
    assert rows == [("k", "act", "act", "observation", 0.95, 1), ("k", "act", "act", "question", 0.02, 0)]


# -- the cache and the runner -----------------------------------------------------------------------------

def test_fill_asks_once_per_missing_unit_and_a_second_run_asks_nothing(all_units, tmp_path):
    path = tmp_path / "cache.jsonl"
    calls = []
    questions = sum(len(u.profiles) for u in all_units.values())
    first = run.fill(all_units, fake_ask(calls=calls), path=path, concurrency=8)
    assert first["requests"] == len(calls) == questions == 490 and first["units"] == 325 and first["cached"] == 0 and not first["failures"]
    assert first["input_tokens"] == 400 * questions
    again = []
    second = run.fill(all_units, fake_ask(calls=again), path=path)
    assert second["requests"] == 0 and again == [] and second["cached"] == questions
    entries = [json.loads(x) for x in path.read_text().splitlines()]
    keys = [e["tag_key"] for e in entries]
    assert keys == sorted(keys) and len(keys) == questions
    shapes = {e["profile"].split("@")[0]: set(e["answer"]) for e in entries}
    assert all(set(e) == {"tag_key", "profile", "classifier", "answer", "usage", "request_id"} for e in entries)
    assert shapes["act"] == shapes["signal_any"] == {"nouls"}
    assert shapes["signal"] == shapes["change_kind"] == shapes["signal_metric"] == {"choice", "confidence", "probabilities"}
    # No record text in the cache: no unit's text appears in it.
    body = path.read_text()
    assert not [u.id for u in all_units.values() if len(u.text) > 12 and u.text in body]


def test_a_failed_request_is_reported_and_asked_again_next_time(all_units, tmp_path):
    path = tmp_path / "cache.jsonl"
    some = dict(list(all_units.items())[:12])
    def flaky(state, questions):
        if "<*>" in state["text"] or len(state["text"]) % 2:
            raise RuntimeError("503 overloaded")
        return fake_ask()(state, questions)
    first = run.fill(some, flaky, path=path)
    assert first["failures"] and first["requests"] + len(first["failures"]) == sum(len(u.profiles) for u in some.values())
    second = run.fill(some, fake_ask(), path=path)
    assert second["requests"] == len(first["failures"]) and not second["failures"]


def test_ingest_applies_cached_tags_and_counts_misses(all_units, tmp_path, monkeypatch):
    path = tmp_path / "cache.jsonl"
    half = dict(list(sorted(all_units.items()))[::2])
    def choose(state, options):
        return "other" if state["kind"] == "commit" else options[0]
    def yes(state, qid):
        return 0.97 if qid in ("observation", "question") else 0.1
    run.fill(half, fake_ask(choose, p=0.97, yes=yes), path=path)
    monkeypatch.setenv("SMALLS_TAG_CACHE", str(path))
    built = store.build(BUNDLE, tags=True)
    t = built.report.tags
    asked_half = sum(len(u.profiles) for u in half.values())
    assert t["mode"] == "on" and t["units"] == len(all_units) and t["hits"] == asked_half and t["hits"] + t["misses"] == t["asked"] == 490
    assert t["applied"] + t["other"] + t["below_floor"] == len(half) and t["by_code"] == 28
    vocabulary = profiles.facets()
    tagged = 0
    for (tags,) in built.conn.execute("SELECT tags FROM record UNION ALL SELECT tags FROM point"):
        for facet, value in json.loads(tags).items():
            values = value if isinstance(value, list) else [value]
            assert isinstance(value, list) == (facet in profiles.multi_facets()) and values and set(values) <= set(vocabulary[facet])
            tagged += 1
    assert tagged > 0
    assert built.conn.execute("SELECT count(DISTINCT tag_key) FROM tag_answer").fetchone()[0] == len(half)
    # A yes/no unit stores both confident answers; a miss, `other` and a low probability store nothing.
    assert built.conn.execute("SELECT count(*) FROM record WHERE json_extract(tags, '$.act') = json('[\"observation\",\"question\"]')").fetchone()[0] > 0
    assert built.conn.execute("SELECT count(*) FROM record WHERE kind = 'commit' AND tags != '{}'").fetchone()[0] == 0
    assert built.conn.execute("SELECT count(*) FROM point WHERE json_extract(tags, '$.signal') IS NOT NULL").fetchone()[0] > 0
    run.fill(all_units, fake_ask(p=0.5, yes=lambda s, q: 0.5), path=tmp_path / "low.jsonl")
    monkeypatch.setenv("SMALLS_TAG_CACHE", str(tmp_path / "low.jsonl"))
    low = store.build(BUNDLE, tags=True)
    assert low.report.tags["applied"] == 0 and low.report.tags["misses"] == 0
    assert low.conn.execute("SELECT count(*) FROM record WHERE tags != '{}' AND tags != '{\"act\":[\"automated\"]}'").fetchone()[0] == 0
    assert low.conn.execute("SELECT count(*) FROM point WHERE tags != '{}'").fetchone()[0] == 0


def test_tags_are_off_by_default_and_the_store_is_as_it_was_before_tagging(monkeypatch):
    monkeypatch.delenv("SMALLS_TAGS", raising=False)
    built = store.build(BUNDLE)
    assert built.report.tags == {"mode": "off"}
    assert built.conn.execute("SELECT count(*) FROM record WHERE tags != '{}' OR tag_key IS NOT NULL").fetchone()[0] == 0
    assert built.conn.execute("SELECT count(*) FROM point WHERE tags != '{}' OR tag_key IS NOT NULL").fetchone()[0] == 0
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
        answers = {}
        for qid, question in body["questions"].items():
            if question["type"] == "noul":
                answers[qid] = {"type": "noul", "noul": 0.91 if qid == "hypothesis" else 0.03}
            else:
                options = list(question["criteria"])
                answers[qid] = {"type": "choice", "choice": options[1], "confidence": 0.9, "probabilities": {o: (0.9 if o == options[1] else 0.0) for o in options}}
        return httpx2.Response(200, headers={"x-typesafe-request-id": "req_test"},
                               json={"model": body["model"], "usage": {"input_tokens": 321, "output_tokens": 12}, "answers": answers})

    real = sdk.TypeSafeClient
    monkeypatch.setattr(sdk, "TypeSafeClient", lambda **kw: real(transport=httpx2.MockTransport(handler), **kw))
    monkeypatch.setenv("JEV_API_KEY", "test-key-not-a-secret")
    ask = run.sdk_ask()
    act, signal = profiles.profiles()["act"], profiles.profiles()["signal"]
    got = ask({"kind": "chat message", "text": "is it the database?"}, act.questions())
    assert got["answers"]["hypothesis"] == {"noul": 0.91} and set(got["answers"]) == set(act.options)
    assert got["usage"] == {"input_tokens": 321, "output_tokens": 12}
    picked = ask({"kind": "log line", "text": "request failed"}, signal.questions())
    assert picked["answers"]["q"]["choice"] == "errors" and set(picked["answers"]["q"]) == {"choice", "confidence", "probabilities"}
    auth, body = seen[0]
    assert auth == "Bearer test-key-not-a-secret"                           # passed explicitly, from JEV_API_KEY
    assert body["model"] == profiles.classifier_version() and body["state"] == {"kind": "chat message", "text": "is it the database?"}
    assert list(seen[1][1]["questions"]["q"]["criteria"]) == list(signal.options)


# -- the labelled sample and the measure ------------------------------------------------------------------

def test_the_labels_are_the_fixed_sample_and_every_label_is_an_option(all_units):
    listed = measure.load_labels()
    labels = {lab["unit"]: lab for lab in listed}
    assert len(labels) == len(listed) == 134                                  # no unit labelled twice
    assert {u.id for u in measure.sample(all_units)} <= set(labels)           # the sample `--sample` prints is all in it
    for lab in listed:
        unit = all_units[lab["unit"]]
        assert (lab["facet"], lab["kind"], lab["text"]) == (unit.facet, unit.kind, unit.text)
        scored = measure.scored_profiles(unit, lab)
        if "labels" in lab:     # every option that applies: scored on every question asked of the kind
            options = {o for p in unit.profiles for o in p.options}
            assert scored == unit.profiles and set(lab["labels"]) | set(lab.get("also_acceptable") or []) <= options
        else:                   # one option: scored on the pick-one question only
            assert scored and not any(p.multi for p in scored) and lab["label"] in scored[0].options
    per_facet = {facet: sum(1 for lab in listed if lab["facet"] == facet) for facet in profiles.facets()}
    assert per_facet == {"signal": 80, "act": 34, "change_kind": 20}
    assert sum(1 for lab in listed if lab["facet"] == "signal" and "labels" in lab) == 45
    # The units first labelled for the pick-one form of `act` are kept, and are not in the sample.
    import yaml
    archived = yaml.safe_load((parity.ROOT / "eval/tags/labels-act-pick-one.yaml").read_text())["units"]
    assert len(archived) == 20 and not {a["unit"] for a in archived} & set(labels)


def _row(name, want, answer, repeats=(), ok=()):
    """One labelled unit for the measure: a pick-one answer is (choice, p); a yes/no answer is {option: p}."""
    profile = profiles.profiles()[name]
    def shape(a):
        return {"nouls": a} if profile.multi else {"choice": a[0], "probabilities": {a[0]: a[1]}}
    return {"unit": f"{name}-{want}-{answer}", "facet": profile.facet, "kind": "x", "want": [w for w in want if w != "other"],
            "ok": set(want) | set(ok), "scored": [(profile, shape(answer), [shape(a) for a in repeats])], "answered": True}


def _both(want, picked, yes, picked_repeats, yes_repeats, ok=()):
    """A unit scored on two questions of one facet: pick-one `signal` and yes/no `signal_any`."""
    one, many = profiles.profiles()["signal"], profiles.profiles()["signal_any"]
    pick = lambda a: {"choice": a[0], "probabilities": {a[0]: a[1]}}
    return {"unit": f"both-{want}", "facet": "signal", "kind": "x", "want": list(want), "ok": set(want) | set(ok), "answered": True,
            "scored": [(one, pick(picked), [pick(a) for a in picked_repeats]), (many, {"nouls": yes}, [{"nouls": a} for a in yes_repeats])]}


def test_the_table_counts_precision_coverage_and_flips():
    rows = [_row("signal", ["errors"], ("errors", 0.95), [("errors", 0.93), ("errors", 0.96), ("errors", 0.9)]),
            _row("signal", ["latency"], ("errors", 0.85), [("errors", 0.85)] * 3),        # applied and wrong
            _row("signal", ["routine"], ("routine", 0.6), [("routine", 0.7)] * 3),        # too doubtful: no tag
            _row("signal", ["other"], ("other", 0.99), [("other", 0.99)] * 3),            # right to store nothing
            _row("signal", ["change"], ("change", 0.88), [("change", 0.9), ("change", 0.75), ("routine", 0.6)])]   # flips
    t = measure.table(rows, 0.8, tol=0.0)["signal"]
    assert (t["labelled"], t["applied"], t["stored"], t["correct"], t["flips"], t["value_flips"], t["repeats"]) == (5, 3, 3, 2, 1, 0, 3)
    assert t["precision"] == pytest.approx(2 / 3) and t["coverage"] == pytest.approx(3 / 5)
    assert t["confused"] == {"latency tagged errors": 1}
    assert measure.table(rows, 0.8)["act"]["precision"] is None
    assert "| `signal` | 5 | 3 | 3 | 2 | 2 of 3 (67%) | 60% | 1 over 3 repeats | 0 | latency tagged errors (1) |" in measure.render(rows, 0.8)
    # A repeat that stores a different value is a change of value; a tag only a repeat would apply is not a flip.
    swapped = [_row("signal", ["errors"], ("errors", 0.9), [("saturation", 0.9), ("errors", 0.9), ("errors", 0.9)]),
               _row("signal", ["errors"], ("errors", 0.7), [("errors", 0.9)] * 3)]
    t = measure.table(swapped, 0.8)["signal"]
    assert (t["applied"], t["flips"], t["value_flips"]) == (1, 1, 1)


def test_a_yes_no_unit_is_scored_per_stored_value():
    rows = [_row("act", ["observation", "hypothesis"], {"observation": 0.95, "hypothesis": 0.91, "social": 0.1}, [{"observation": 0.94, "hypothesis": 0.93}] * 3),
            _row("act", ["question"], {"question": 0.97, "action_taken": 0.92}, [{"question": 0.97, "action_taken": 0.93}] * 3, ok=["status"]),   # one right, one wrong
            _row("act", ["status"], {"status": 0.5}, [{"status": 0.5}] * 3)]
    t = measure.table(rows, 0.9)["act"]
    assert (t["labelled"], t["applied"], t["stored"], t["correct"], t["flips"]) == (3, 2, 4, 3, 0)
    assert t["precision"] == pytest.approx(3 / 4) and t["confused"] == {"question tagged action_taken": 1}


def test_a_unit_asked_two_ways_stores_every_value_once_and_is_reproduced_by_either():
    # pick-one is unsure (0.54) and stores nothing; yes/no stores both; pick-one's repeats never reach the floor.
    rows = [_both(["errors", "availability"], ("errors", 0.54), {"errors": 0.98, "availability": 0.96, "routine": 0.01},
                  [("errors", 0.55)] * 3, [{"errors": 0.97, "availability": 0.95}] * 3)]
    t = measure.table(rows, 0.9, tol=0.02)["signal"]
    assert (t["applied"], t["stored"], t["correct"], t["flips"], t["value_flips"]) == (1, 2, 2, 0, 0)
    # both forms store `saturation`: it is one stored value, and either form's repeat reproduces it.
    rows = [_both(["saturation", "errors"], ("saturation", 0.96), {"saturation": 0.94, "errors": 0.97},
                  [("saturation", 0.95), ("saturation", 0.60), ("saturation", 0.96)], [{"saturation": 0.93, "errors": 0.97}] * 3)]
    t = measure.table(rows, 0.9, tol=0.02)["signal"]
    assert (t["stored"], t["correct"], t["flips"]) == (2, 2, 0)
    # a value only the yes/no form stores, lost in one of its repeats, is a flip
    rows = [_both(["errors"], ("errors", 0.5), {"errors": 0.95}, [("errors", 0.5)] * 3, [{"errors": 0.95}, {"errors": 0.70}, {"errors": 0.95}])]
    assert measure.table(rows, 0.9, tol=0.02)["signal"]["flips"] == 1


def test_a_repeat_within_the_tolerance_reproduces_a_value():
    wobble = [_row("signal", ["saturation"], ("saturation", 0.94), [("saturation", 0.96), ("saturation", 0.96), ("saturation", 0.92)])]
    assert measure.table(wobble, 0.93, tol=0.0)["signal"]["flips"] == 1      # 0.92 is under 0.93
    assert measure.table(wobble, 0.93, tol=0.02)["signal"]["flips"] == 0     # but within the classifier's wobble
    real = [_row("signal", ["errors"], ("errors", 0.89), [("errors", 0.90), ("errors", 0.86), ("errors", 0.70)])]
    assert measure.table(real, 0.85, tol=0.02)["signal"]["flips"] == 1       # 0.70 is a different answer


def test_the_floor_rises_until_nothing_flips_and_precision_meets_the_bar():
    steady = [_row("signal", ["errors"], ("errors", 0.99), [("errors", 0.99)] * 3) for _ in range(9)]
    assert measure.final_floor(steady) == (0.8, [])
    wrong_low = steady + [_row("signal", ["latency"], ("errors", 0.86), [("errors", 0.86)] * 3), _row("signal", ["latency"], ("errors", 0.84), [("errors", 0.84)] * 3)]
    assert measure.final_floor(wrong_low) == (0.85, [])                       # 9 of 10 at 0.85; 9 of 11 below it
    flipping = steady + [_row("signal", ["change"], ("change", 0.92), [("change", 0.92), ("change", 0.89), ("change", 0.91)])]
    assert measure.final_floor(flipping) == (0.8, [])                         # every repeat still applies it
    flipping = steady + [_row("signal", ["change"], ("change", 0.92), [("change", 0.92), ("change", 0.70), ("change", 0.91)])]
    assert measure.final_floor(flipping) == (0.93, [])                        # raised until the cached answer no longer applies it
    hopeless = [_row("change_kind", ["other"], ("release", 1.0), [("release", 1.0)] * 3)] * 3
    floor, below = measure.final_floor(steady + hopeless)
    assert floor == 0.8 and below == ["change_kind"]                          # stored, not offered as a filter
