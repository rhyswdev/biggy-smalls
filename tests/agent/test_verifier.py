"""The verifier (build row E1; agent-design axis 5; J13 of docs/decisions/tools-agent-build.md). Acceptance:
verdicts recorded; a scripted unsupported claim triggers exactly one revision; a contrarian check already
in the ledger triggers none. Everything is scripted: the investigator plays the fixture script, and each
verifier pass plays a script that reads the one user turn it is given."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, ToolMessage

import fixture_registry as fx
import fixture_script
from smalls.agent import __main__ as cli
from smalls.agent import verifier
from smalls.agent.config import load_config
from smalls.agent.graph import ACCEPTED
from smalls.agent.models import scripted_call as call
from smalls.agent.runner import Runner, module_toolbox
from smalls.contracts.report import Report
from smalls.tools.schemas import Pair

SENT_BACK = "The report is not final: the verifier sends it back once."
IN_LEDGER = [{"name": "terms", "value": '["ERROR"]'}, {"name": "since", "value": "-60m"}]       # the fixture script's second call
NOT_RUN = [{"name": "terms", "value": '["spool"]'}, {"name": "since", "value": "-2h"}]


def turn_of(messages) -> str:
    return str(messages[-1].content)


def ids(messages) -> list[str]:
    return [line.split(":", 2)[0].removeprefix("Claim ") + ":" + line.split(":", 2)[1] for line in turn_of(messages).splitlines() if line.startswith("Claim ")]


def claims_script(verdicts: dict[str, str] | None = None, seen: list | None = None):
    """A claim-support pass: `supported` for every claim it is given, but for the ids named."""
    def script(messages, tools):
        assert [t.get("name") or (t.get("function") or {}).get("name") for t in tools] == ["submit_verdict"]
        text = turn_of(messages)
        assert text.startswith("Leading hypothesis: ") and "Digest of rows" not in text and "Report:" not in text      # packets and the leading mark, no more
        if seen is not None:
            seen.append(text)
        rows = [{"id": c, "verdict": (verdicts or {}).get(c, "supported"), "quote": "No space left on device", "handle": None} for c in ids(messages)]
        return "", [call("submit_verdict", 1, claims=rows, overall="revise" if any(r["verdict"] in ("unsupported", "contradicted") for r in rows) else "pass")]
    return script


def contrarian_script(alternatives: list[dict] | None = None, ignored: list[str] | None = None, seen: list | None = None):
    def script(messages, tools):
        text = turn_of(messages)
        assert text.startswith("Report:") and "\nPackets:\n" in text and "\nDigest of rows shown but not cited, newest first:\n" in text
        if seen is not None:
            seen.append(text)
        return "", [call("submit_verdict", 1, alternatives=alternatives or [], ignored=ignored or [],
                         overall="revise" if any(a["fit"] == "equal" for a in alternatives or []) else "pass")]
    return script


def revising(messages, tools):
    """The investigator, when a report comes back: one more read, then the report again with its first action reworded."""
    back = [n for n, m in enumerate(messages) if isinstance(m, ToolMessage) and m.name == "submit_report" and SENT_BACK in str(m.content)]
    if not back:
        return fixture_script.turn(messages, tools)
    since = sum(isinstance(m, AIMessage) for m in messages[back[-1]:])
    if since == 0:
        return "Run the check the verifier named.", [call("search_logs", 40, ("h-release", "writes to the spool volume"), terms=["spool"], since="-2h")]
    words, calls = fixture_script.turn(messages, tools)
    calls[0]["args"]["actions"][0]["action"] += " Confirm the spool volume has room first."
    calls[0]["id"] = "call_41_submit_report"
    return words, calls


@pytest.fixture
def cfg(tmp_path: Path):
    return load_config(runs_dir=tmp_path)


def run(cfg, run_id: str, script=fixture_script.turn, **scripts):
    r = Runner(cfg, module_toolbox("fixture_registry"), run_id, script=script, eval_run=True, verifier_scripts=scripts or None)
    result = r.start(fx.TRIGGER, fx.NOW)
    state = r.state()
    r.close()
    read = lambda name: json.loads((r.dir / name).read_text(encoding="utf-8"))      # noqa: E731
    events = [json.loads(line) for line in (r.dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    return result, state, read, events


def test_a_supported_report_passes_and_the_verdict_is_recorded(cfg):
    seen_claims, seen_contrarian = [], []
    weaker = [{"statement": "The volume was already nearly full before the release.", "fit": "weaker", "check": {"tool": "search_logs", "args": NOT_RUN}}]
    result, state, read, events = run(cfg, "pass", claims=claims_script({"t:1": "partial"}, seen_claims), contrarian=contrarian_script(weaker, seen=seen_contrarian))
    report = Report.model_validate(read("report.json"))
    assert result["status"] == "completed" and state["revisions"] == 0
    assert report.validation.verifier == "pass" and report.validation.disputed == ["t:1"] and report.validation.ok      # weaker support elsewhere is disputed, not sent back
    verdict = read("verdict.json")
    assert verdict["revisions"] == 0 and verdict["verdict"]["status"] == "pass" and verdict["verdict"]["decision"]["revise"] is False
    assert {c["id"] for c in verdict["verdict"]["claims"]["claims"]} == {"h:h-release", "h:h-full", "h:h-dying", "t:1", "t:2", "a:1", "a:2", "d:1"}
    assert verdict["verdict"]["contrarian"]["alternatives"][0]["fit"] == "weaker" and verdict["verdict"]["decision"]["checks"] == []
    # Each pass was given its own turn: packets with the rows the model was shown, and for the contrarian the report and the digest too.
    assert "Leading hypothesis: h:h-release. Claims in the leading hypothesis: h:h-release, a:1, a:2." in seen_claims[0]
    assert "Claim h:h-release: [leading cause, confidence 0.65, blames report-exporter]" in seen_claims[0] and "No space left on device" in seen_claims[0]
    assert '"summary": "Run 57 failed' in seen_contrarian[0] and seen_contrarian[0].count("Claim ") == seen_claims[0].count("Claim ") == 8
    # The verdict comes back as the result of the submit_report call it answers.
    answers = [m for m in state["messages"] if isinstance(m, ToolMessage) and m.name == "submit_report"]
    assert [str(m.content) for m in answers] == [ACCEPTED]
    usage = read("usage.json")["calls"]
    assert [c["node"] for c in usage if c["node"] != "agent"] == ["verifier_claims", "verifier_contrarian"] and read("summary.json")["process"]["model_turns"] == 5
    verdicts = [e for e in events if e["type"] == "verifier_verdict"]
    assert len(verdicts) == 1 and verdicts[0]["data"]["status"] == "pass" and verdicts[0]["data"]["disputed"] == ["t:1"]
    assert any(e["type"] == "phase" and e["data"]["name"] == "verify" for e in events)
    assert read("run.json")["verifier"] == {"claims": "scripted", "contrarian": "scripted"} and read("run.json")["context"]["verifier"] is True


def test_a_scripted_unsupported_claim_triggers_exactly_one_revision(cfg):
    rechecks: list[str] = []

    def claims(messages, tools):          # the first pass faults the leading hypothesis; the re-check faults the changed action
        first = "Claim h:h-release" in turn_of(messages)
        if not first:
            rechecks.append(turn_of(messages))
        return claims_script({"h:h-release": "unsupported"} if first else {"a:1": "unsupported"})(messages, tools)

    result, state, read, events = run(cfg, "revise", script=revising, claims=claims, contrarian=contrarian_script())
    assert result["status"] == "completed" and state["revisions"] == 1
    answers = [str(m.content) for m in state["messages"] if isinstance(m, ToolMessage) and m.name == "submit_report"]
    assert len(answers) == 2 and answers[1] == ACCEPTED and sum(SENT_BACK in a for a in answers) == 1          # exactly one revision
    assert 'Claim h:h-release is unsupported; the closest the cited rows say is "No space left on device".' in answers[0]
    assert "You have 5 more tool calls and 7 more turns." in answers[0] and answers[0].splitlines()[-1].startswith("[board: ")
    # The revision buys five more calls and seven more turns, and the retry count starts again.
    budgets = [e["data"] for e in events if e["type"] == "budget"]
    assert budgets[0]["limit"] == cfg.call_budget and budgets[-1]["limit"] == cfg.call_budget + cfg.revision_calls
    report = Report.model_validate(read("report.json"))
    # What is left short of supported is shown as disputed: the action the re-check faulted, and the leading claim the revision left as it was.
    assert report.validation.verifier == "revised" and report.validation.disputed == ["a:1", "h:h-release"] and report.validation.attempts == 1
    assert report.actions[0].action.endswith("Confirm the spool volume has room first.")
    # After the revision only what it changed is checked again, and a second fault does not send it back again.
    assert len(rechecks) == 1 and "Claim a:1:" in rechecks[0] and "Claim h:h-release" not in rechecks[0] and "Claim t:1" not in rechecks[0]
    verdict = read("verdict.json")
    assert verdict["revisions"] == 1 and verdict["verdict"]["status"] == "revised"
    assert verdict["verdict"]["diff"] == {"added": [], "changed": ["a:1"], "removed": []}
    assert verdict["verdict"]["decision"]["unsupported"][0]["id"] == "h:h-release" and verdict["verdict"]["recheck"]["claims"][0]["id"] == "a:1"
    assert [e["data"]["status"] for e in events if e["type"] == "verifier_verdict"] == ["revise", "revised"]
    assert [c["node"] for c in read("usage.json")["calls"] if c["node"] != "agent"] == ["verifier_claims", "verifier_contrarian", "verifier_recheck"]
    calls = [json.loads(line) for line in Path(result["summary"]["run_id"] and cfg.runs_dir / "revise" / "calls.jsonl").read_text().splitlines()]
    assert [c["tool"] for c in calls if c["by"] == "agent"].count("submit_report") == 2 and calls[-2]["args"]["terms"] == ["spool"]


def test_a_contrarian_check_already_in_the_ledger_triggers_none(cfg):
    equal = lambda args: [{"statement": "The exporter was writing far more than usual", "fit": "equal", "check": {"tool": "search_logs", "args": args}}]  # noqa: E731
    result, state, read, events = run(cfg, "inledger", claims=claims_script(), contrarian=contrarian_script(equal(IN_LEDGER)))
    verdict = read("verdict.json")["verdict"]
    assert state["revisions"] == 0 and verdict["status"] == "pass" and verdict["decision"]["checks"] == []
    assert verdict["decision"]["discarded_checks"] == [{"statement": "The exporter was writing far more than usual", "tool": "search_logs",
                                                         "args": {"terms": '["ERROR"]', "since": "-60m"}, "why": "already in the ledger"}]
    assert verdict["contrarian"]["overall"] == "revise" and read("report.json")["validation"]["verifier"] == "pass"      # the pass asked; code decided
    # The same alternative with a check nobody ran does send the report back, once, with the call to make.
    result, state, read, events = run(cfg, "newcheck", script=revising, claims=claims_script(), contrarian=contrarian_script(equal(NOT_RUN), ignored=["log:nope/00000000"]))
    back = next(str(m.content) for m in state["messages"] if isinstance(m, ToolMessage) and SENT_BACK in str(m.content))
    assert state["revisions"] == 1 and read("verdict.json")["verdict"]["decision"]["ignored"] == []                       # a handle nothing showed is dropped
    assert ('An alternative fits the evidence as well as the leading hypothesis: The exporter was writing far more than usual. '
            'The check that would tell them apart has not been run: search_logs terms=["spool"] since=-2h.') in back


def test_only_an_equal_alternative_with_a_valid_new_read_counts(cfg):
    tools = module_toolbox("fixture_registry").read_tools
    report = {"hypotheses": [{"id": "h1", "status": "leading", "role": "cause", "confidence": 0.6, "subject": "x", "statement": "S", "support": [], "against": []}],
              "timeline": [], "actions": [{"rank": 1, "action": "A", "owner": "o", "evidence": []}], "do_not": []}

    def decide(alternatives, claims=None, ledger=()):
        said = verifier.SubmitVerdictArgs(alternatives=alternatives, overall="revise", ignored=["log:a/00000001", "log:b/00000002"])
        judged = verifier.SubmitVerdictArgs(claims=claims or [], overall="pass")
        return verifier.decide(report, judged, said, read_tools=tools, ledger=list(ledger), shown={"log:a/00000001"}, now=fx.NOW)

    alt = lambda fit, tool, args: {"statement": "s", "fit": fit, "check": {"tool": tool, "args": args} if tool else None}  # noqa: E731
    assert decide([alt("equal", "search_logs", NOT_RUN)])["revise"] is True
    assert decide([alt("weaker", "search_logs", NOT_RUN)])["revise"] is False                       # a weaker fit never sends a report back
    assert decide([alt("equal", None, [])])["revise"] is False                                      # nor an alternative with no check
    for tool, args in (("no_such_tool", NOT_RUN), ("search_logs", [{"name": "limit", "value": "9999"}]), ("propose_action", []), ("update_board", [])):
        d = decide([alt("equal", tool, args)])
        assert d["revise"] is False and d["discarded_checks"][0]["why"] == "not a valid read call", tool
    # The same call under another spelling is the same call: defaults filled, relative times resolved.
    spec = tools["search_logs"]
    ran = [{"identity": verifier.identity(spec, {"terms": ["spool"], "since": "2026-03-02T00:20:00Z", "until": "now"}, fx.NOW), "status": "ok"}]
    assert decide([alt("equal", "search_logs", NOT_RUN)], ledger=ran)["discarded_checks"][0]["why"] == "already in the ledger"
    assert decide([alt("equal", "search_logs", NOT_RUN)], ledger=[{**ran[0], "status": "error"}])["revise"] is True      # a call that failed did not answer it
    assert decide([])["ignored"] == ["log:a/00000001"]                                              # only handles that were shown
    # A claim outside the leading hypothesis does not send the report back; the hypothesis itself or any action does.
    row = lambda claim, verdict: {"id": claim, "verdict": verdict, "quote": "q", "handle": None}  # noqa: E731
    assert decide([], claims=[row("t:1", "contradicted"), row("d:1", "unsupported"), row("h:h1", "partial")])["revise"] is False
    assert decide([], claims=[row("a:1", "contradicted")])["unsupported"][0]["id"] == "a:1" and decide([], claims=[row("h:h1", "unsupported")])["revise"] is True
    assert verifier.check_args(spec, [Pair(name="terms", value='["a", "b"]'), Pair(name="host", value="07"), Pair(name="limit", value="5")]) == {
        "terms": ["a", "b"], "host": "07", "limit": 5}                                              # a text input as written, anything else as JSON


def test_a_verdict_that_does_not_parse_gets_one_retry_then_the_report_stands_unverified(cfg):
    silent = lambda messages, tools: ("I would rather explain in prose.", [])  # noqa: E731
    result, state, read, events = run(cfg, "unverified", claims=silent, contrarian=silent)
    report = read("report.json")
    assert result["status"] == "completed" and report["validation"]["verifier"] == "unverified" and report["validation"]["disputed"] == []
    usage = [c for c in read("usage.json")["calls"] if c["node"] != "agent"]
    assert [(c["node"], c["attempt"]) for c in usage] == [("verifier_claims", 1), ("verifier_claims", 2), ("verifier_contrarian", 1), ("verifier_contrarian", 2)]
    assert read("verdict.json")["verdict"]["status"] == "unverified" and state["revisions"] == 0
    tries = []

    def second_time(messages, tools):
        tries.append(1)
        if len(tries) == 1:
            return "", [call("submit_verdict", 1, claims=[{"id": "h:h-release", "verdict": "maybe", "quote": "", "handle": None}], overall="pass")]      # not a verdict
        return claims_script()(messages, tools)

    result, state, read, events = run(cfg, "retry", claims=second_time)
    assert read("verdict.json")["verdict"]["status"] == "pass" and len(tries) == 2 and read("verdict.json")["verdict"]["contrarian"] is None
    assert read("run.json")["verifier"] == {"claims": "scripted"}                    # a pass that was not given a script does not run


def test_the_verifier_is_off_by_the_flag_without_a_script_and_in_a_single_call_run(cfg, tmp_path):
    result, state, read, events = run(cfg, "none")
    assert read("report.json")["validation"]["verifier"] is None and read("verdict.json") == {"verdict": None, "revisions": 0}
    assert read("run.json")["context"]["verifier"] is False and not [e for e in events if e["type"] == "verifier_verdict"]
    off = replace(cfg, verifier={**cfg.verifier, "enabled": False})
    result, state, read, events = run(off, "flag-off", claims=claims_script({"h:h-release": "unsupported"}))
    assert state["revisions"] == 0 and read("report.json")["validation"]["verifier"] is None
    r = Runner(cfg, module_toolbox("fixture_registry"), "single", script=fixture_script.turn, single_call=True, verifier_scripts={"claims": claims_script()})
    assert r.verify is None
    r.close()


def test_what_a_revision_changed_and_what_is_left_disputed():
    base = {"hypotheses": [{"id": "h1", "status": "leading", "role": "cause", "confidence": 0.6, "subject": "x", "statement": "S", "support": ["log:a/00000001"], "against": []}],
            "timeline": [{"t_utc": "T", "event": "E", "evidence": ["log:a/00000001"]}], "actions": [{"rank": 1, "action": "A", "owner": "o", "evidence": []}], "do_not": []}
    after = {**base, "hypotheses": [{**base["hypotheses"][0], "support": ["log:a/00000001", "log:b/00000002"]}], "actions": [],
             "do_not": [{"action": "D", "why": "W", "evidence": []}]}
    assert verifier.changed_claims(base, after) == {"added": ["d:1"], "changed": ["h:h1"], "removed": ["a:1"]}
    assert verifier.changed_claims(base, base) == {"added": [], "changed": [], "removed": []}
    first = {"claims": [{"id": "h:h1", "verdict": "unsupported"}, {"id": "t:1", "verdict": "partial"}, {"id": "a:1", "verdict": "contradicted"}]}
    again = verifier.SubmitVerdictArgs(claims=[{"id": "h:h1", "verdict": "supported", "quote": "q"}, {"id": "d:1", "verdict": "partial", "quote": "q"}], overall="pass")
    assert verifier.disputed(after, first, again) == ["d:1", "t:1"]                  # a later verdict replaces an earlier one; a removed claim is gone
    assert verifier.disputed(after, None, None) == []
    evidence = [{"handle": "log:a/00000001", "text": "log:a/00000001  cited row", "t_known": "2026-01-01T00:00:01Z"},
                {"handle": "log:c/00000003", "text": "log:c/00000003  a row nobody cited", "t_known": "2026-01-01T00:00:02Z"}]
    only = verifier.user_turn(after, evidence, verifier.RECHECK, only={"d:1"})
    assert "Claim d:1: do not: D, because W" in only and "Claim h:h1" not in only
    whole = verifier.user_turn(after, evidence, verifier.CONTRARIAN, digest_tokens=50)
    assert whole.index("Report:") < whole.index("Leading hypothesis: h:h1.") < whole.index("Packets:") < whole.index("Digest of rows shown but not cited")
    assert "log:c/00000003  a row nobody cited" in whole.split("Digest of rows")[1] and "cited row" not in whole.split("Digest of rows")[1]
    assert verifier.user_turn({**after, "hypotheses": []}, [], verifier.CLAIMS).startswith("Leading hypothesis: none.")


def test_the_cli_takes_verifier_scripts_and_a_run_record_names_them(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SMALLS_RUNS_DIR", str(tmp_path))
    monkeypatch.syspath_prepend(os.path.dirname(os.path.abspath(__file__)))
    code = cli.main(["run", "--now", "2026-03-02T02:20:00Z", "--trigger", fx.TRIGGER, "--registry", "fixture_registry", "--script", "fixture_script:turn",
                     "--profile", "scripted", "--run-id", "cli-verify", "--eval-run", "--no-thinking",
                     "--verifier-script", "claims=test_verifier:all_supported", "--verifier-script", "contrarian=test_verifier:no_alternative"])
    out = capsys.readouterr().out
    run_json = json.loads((tmp_path / "cli-verify" / "run.json").read_text())
    assert code == 0 and "verifier pass: 0 leading claim(s) not supported, 0 new check(s)" in out and "verifier: pass" in out
    assert run_json["verifier_scripts"] == {"claims": "test_verifier:all_supported", "contrarian": "test_verifier:no_alternative"}
    assert json.loads((tmp_path / "cli-verify" / "verdict.json").read_text())["verdict"]["status"] == "pass"
    assert cli.main(["replay", "cli-verify", "--no-thinking"]) == 0 and "verifier pass" in capsys.readouterr().out      # the verdict replays with no model


all_supported = claims_script()
no_alternative = contrarian_script()


def test_both_verifier_profiles_are_config_and_a_missing_client_says_what_to_install(cfg, monkeypatch):
    """A live run takes the claim-support and contrarian profiles from config: the cheaper tier of the same
    provider, or another provider. Models are stubbed here, so nothing goes live."""
    from smalls.agent import runner as runner_module
    from smalls.agent.models import ScriptedChatModel

    asked: list[str] = []

    def fake(spec, **kwargs):
        asked.append(spec.profile)
        if spec.provider == "openai":
            raise ImportError("No module named 'langchain_openai'", name="langchain_openai")
        return ScriptedChatModel(script=fixture_script.turn), {}

    monkeypatch.setattr(runner_module, "make_model", fake)
    live = replace(cfg, investigator=cfg.profiles["anthropic"])
    assert (live.verifier["claims_profile"], live.verifier["contrarian_profile"]) == ("anthropic-small", "anthropic")      # the default pair
    r = Runner(live, module_toolbox("fixture_registry"), "profiles")
    assert r.verifier_profiles == {"claims": "anthropic-small", "contrarian": "anthropic"} and set(r.verify) == {"claims", "contrarian", "recheck", "digest_tokens"}
    assert r.verify["recheck"] is r.verify["claims"] and asked == ["anthropic", "anthropic-small", "anthropic"]           # the re-check is the cheap tier
    r.close()
    cut = Runner(replace(live, verifier={**live.verifier, "contrarian_profile": ""}), module_toolbox("fixture_registry"), "cut")
    assert cut.verifier_profiles == {"claims": "anthropic-small"}                    # an empty profile cuts that pass
    cut.close()
    with pytest.raises(SystemExit, match="needs a client that is not installed"):
        Runner(replace(live, verifier={**live.verifier, "contrarian_profile": "openai"}), module_toolbox("fixture_registry"), "cross")
    with pytest.raises(SystemExit, match="names no profile"):
        Runner(replace(live, verifier={**live.verifier, "claims_profile": "no-such"}), module_toolbox("fixture_registry"), "bad")
