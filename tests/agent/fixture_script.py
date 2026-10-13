"""The script the scripted model plays over the fixture registry: five model turns that investigate the
made-up incident, write the board, propose an action and submit a report. It chooses each turn from the
conversation so far, so it is stateless and survives a resume in a new process.

`turn` is the plain run. `turn_bad_handle` cites a handle no tool showed in its first report, to exercise
the validator's retry.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from smalls.agent.models import scripted_call as call
from smalls.contracts.handles import is_handle


def find(messages: list[BaseMessage], needle: str) -> list[str]:
    """The handles of the result rows that contain `needle`, in the order they were shown."""
    found = []
    for m in messages:
        if isinstance(m, (ToolMessage, HumanMessage)):
            for line in str(m.content).splitlines():
                first = line.split("  ", 1)[0].strip()
                if needle in line and is_handle(first) and first not in found:
                    found.append(first)
    return found


def swept_alerts(messages: list[BaseMessage]) -> list[str]:
    """The alert streams the opening sweep showed: the row handles of its `list_alerts` block."""
    found: list[str] = []
    for m in messages:
        if isinstance(m, HumanMessage) and "== list_alerts " in str(m.content):
            block = str(m.content).split("== list_alerts ", 1)[1].split("\n== ", 1)[0]
            for line in block.splitlines()[1:]:
                first = line.split("  ", 1)[0].strip()
                if is_handle(first) and first not in found:
                    found.append(first)
    return found


def action_kind(tools: list[dict[str, Any]]) -> str:
    """Whatever the descriptions file lists first: the script does not depend on the wording of the kinds."""
    for tool in tools:
        spec = tool.get("function", tool)
        if spec.get("name") == "propose_action":
            kind = (spec.get("parameters") or spec.get("input_schema"))["properties"]["kind"]
            return kind["enum"][0] if "enum" in kind else kind["const"]      # a list of one kind is a constant
    raise LookupError("the model was not given propose_action")


def turn(messages: list[BaseMessage], tools: list[dict[str, Any]], bad_handle: bool = False) -> tuple[str, list[dict[str, Any]]]:
    k = sum(isinstance(m, AIMessage) for m in messages)
    results = [m for m in messages if isinstance(m, ToolMessage)]
    rejected = [m for m in results if m.name == "propose_action" and str(m.content).startswith("REJECTED")]
    if k == 0:
        return ("The job failed at 02:10. Start with the job's own log lines and any errors in the last hour, in parallel.", [
            call("search_logs", 1, ("h-full", "a start line and an exit code"), terms=["report-export"], since="-2h"),
            call("search_logs", 2, ("h-full", "the proximate error"), terms=["ERROR"], since="-60m")])
    if k == 1:
        enospc, exited = find(messages, "No space left"), find(messages, "exited code=1")
        return ("The exporter ran out of space at 02:09 and the run exited 1 a few seconds later. The channel's theory is a "
                "dying disk; record both, then check the disk trend and the runbook.", [
                    call("update_board", 3, hypotheses=[
                        {"id": "h-full", "statement": "The run failed because its spool volume filled up.", "role": "cause",
                         "status": "leading", "subject": "worker-01", "entities": ["worker-01"], "confidence": 0.6,
                         "source": "derived", "support": enospc + exited, "because": enospc},
                        {"id": "h-dying", "statement": "The disk on worker-01 is failing.", "role": "cause", "status": "open",
                         "subject": "worker-01", "entities": ["worker-01"], "confidence": 0.2, "source": "stated"}]),
                    call("search_logs", 4, ("h-dying", "usage rising only during the run"), terms=["[disk]"], since="-2h"),
                    call("get_record", 5, ("h-full", "what the runbook says to do"), handle="conf:RB-DISK-SPACE")])
    if k == 2:
        return ("Usage climbed from 88% to 99% inside the run. Did something change? Compare this build with the last good one, "
                "and look for a deploy.", [
                    call("get_record", 6, ("h-release", "the failed build's version"), handle="jk:report-export#57"),
                    call("get_record", 7, ("h-release", "a different version on the last good build"), handle="jk:report-export#56"),
                    call("search_logs", 8, ("h-release", "a deploy before the run"), terms=["deploy"], since="-2h"),
                    call("search_logs", 9, ("h-release", "whether the run cleaned up its temp files"), terms=["cleanup"], since="-2h")])
    deploy, cleanup, disk = find(messages, "[deploy]"), find(messages, "cleanup skipped"), find(messages, "[disk]")
    if k == 3:
        return ("2.4.1 replaced 2.4.0 at 01:00 and this run skipped temp cleanup; the good run was on 2.4.0. The release is the "
                "cause and the full volume is how it showed. Propose a rollback for the commander.", [
                    call("update_board", 10, hypotheses=[
                        {"id": "h-release", "statement": "Release 2.4.1 stopped cleaning temporary files, so the run filled its spool volume.",
                         "role": "cause", "status": "leading", "subject": "report-exporter", "entities": ["report-exporter", "worker-01"],
                         "changes": ["jk:report-export#57"], "confidence": 0.65, "source": "derived",
                         "support": deploy + cleanup + ["jk:report-export#57", "jk:report-export#56"], "discriminating": cleanup,
                         "because": deploy + cleanup},
                        {"id": "h-full", "role": "symptom", "status": "open"},
                        {"id": "h-dying", "status": "rejected", "confidence": 0.1, "against": disk, "because": disk}]),
                    call("propose_action", 11, ("h-release", "tonight's run cleans up after itself"), kind=action_kind(tools),
                         target="report-exporter", params=[{"name": "to_version", "value": "2.4.0"}], evidence=deploy + cleanup)])
    reports = [m for m in results if m.name == "submit_report"]
    enospc = find(messages, "No space left")[:1]
    support = enospc + deploy[:1] + cleanup[:1] + ["jk:report-export#57", "jk:report-export#56"]
    cited = support + (["log:worker-01/deadbeef"] if bad_handle and not reports else [])
    purge = {"action": "Purge the exporter's temp folder per conf:RB-DISK-SPACE while no job runs.", "owner": "Reporting",
             "evidence": ["conf:RB-DISK-SPACE"]}
    if rejected:
        actions = [{**purge, "rank": 1},
                   {"rank": 2, "action": "Ask Reporting to fix temp cleanup in 2.4.1 before tonight's run.", "owner": "Reporting", "evidence": cleanup[:1]}]
        note = "The rollback was rejected, so re-plan: purge per the runbook instead. "
    else:
        actions = [{"rank": 1, "action": "Roll back report-exporter to 2.4.0.", "owner": "Reporting", "evidence": deploy[:1] + cleanup[:1]},
                   {**purge, "rank": 2}]
        note = ""
    return (note + "Enough evidence. Submit the report.", [call(
        "submit_report", 12 + len(reports),
        summary=(f"Run 57 failed with no space left on its spool volume ({', '.join(enospc)}) after release 2.4.1 ({', '.join(deploy[:1])}) "
                 f"skipped temp cleanup ({', '.join(cleanup[:1])}); run 56 on 2.4.0 succeeded (jk:report-export#56)."),
        impact={"business_service": "Reporting", "cis": ["report-exporter", "worker-01"],
                "priority": {"suggested": "P3", "impact": "medium", "urgency": "low"}, "assignment_group": "Reporting",
                "oncall": "unknown", "customer": "Last night's reports were not delivered."},
        hypotheses=[{"id": "h-release", "support": cited}],
        timeline=[{"t_utc": "2026-03-02T01:00:04Z", "event": "Release 2.4.1 was installed.", "evidence": deploy[:1]},
                  {"t_utc": "2026-03-02T02:09:31Z", "event": "Writes failed: no space left on the device.", "evidence": enospc}],
        actions=actions,
        do_not=[{"action": "Do not purge the temp folder while a job is running.", "why": "The runbook forbids it.",
                 "evidence": ["conf:RB-DISK-SPACE"]}],
        alerts=[{"handle": h, "verdict": "this_incident" if n == 0 else "noise"} for n, h in enumerate(swept_alerts(messages))],
        open_questions=["Did other jobs on worker-01 fail after 02:09?"],
        stakeholder_update={"status": "Cause identified; fix pending.", "customer_impact": "Last night's reports were not delivered.",
                            "scope": "The nightly export only.", "changed_since_last": "First update.",
                            "next_steps": "Remove the trigger before tonight's run.", "next_update_at": "2026-03-02T02:50:00Z"})])


def turn_bad_handle(messages: list[BaseMessage], tools: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    return turn(messages, tools, bad_handle=True)


def slow_turn(messages: list[BaseMessage], tools: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """The plain run, taking two seconds a turn, so a run lasts long enough to be interrupted by hand."""
    import time

    time.sleep(2)
    return turn(messages, tools)
