"""Model factory and thinking normalisation.

Real providers go through LangChain's `init_chat_model`, chosen by config. The scripted model is a keyless,
deterministic stand-in that streams provider-shaped chunks (Anthropic-style thinking blocks, tool-call chunks,
usage metadata), so the graph, the gate, the event mapping and the run record can be exercised offline.
It proves the framework, not the agent.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Iterator

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from .config import ModelSpec


def make_model(spec: ModelSpec) -> tuple[BaseChatModel, dict[str, Any]]:
    """Returns the chat model and extra kwargs to bind alongside the tools."""
    params = dict(spec.params)
    if spec.provider == "scripted":
        return ScriptedChatModel(**params), {}
    from langchain.chat_models import init_chat_model

    bind: dict[str, Any] = {}
    if params.pop("cache", False) and spec.provider == "anthropic":
        bind["cache_control"] = {"type": "ephemeral"}  # top-level automatic caching; optional, provider-specific
    if not spec.model:
        raise SystemExit(f"profile {spec.profile!r} has no model; set SMALLS_MODEL")
    return init_chat_model(spec.model, model_provider=spec.provider, **params), bind


def reasoning_text(chunk: BaseMessage) -> tuple[str, str]:
    """Provider-neutral split of a streamed chunk into (reasoning, visible text).

    langchain-core's standard content blocks map Anthropic `thinking` and OpenAI reasoning summaries to one
    `reasoning` block type, so no per-provider code is needed here. A model without thinking yields ''.
    """
    reasoning, text = [], []
    for b in chunk.content_blocks:
        if b.get("type") == "reasoning":
            reasoning.append(b.get("reasoning") or "")
        elif b.get("type") == "text":
            text.append(b.get("text") or "")
    return "".join(reasoning), "".join(text)


# ---------------------------------------------------------------- scripted model

HANDLE_AT_LINE_START = re.compile(r"^((?:log|jk|conf|q):\S+)", re.M)


def _find(msgs: list[BaseMessage], needle: str) -> list[str]:
    hs = []
    for m in msgs:
        if isinstance(m, ToolMessage):
            for line in str(m.content).splitlines():
                if needle in line and HANDLE_AT_LINE_START.match(line):
                    hs.append(HANDLE_AT_LINE_START.match(line)[1])
    return hs


def _call(name: str, n: int, rationale: str | None = None, **args: Any) -> dict[str, Any]:
    if rationale is not None:
        args = {"rationale": rationale, **args}
    return {"name": name, "args": args, "id": f"call_{n:02d}_{name}", "type": "tool_call"}


class ScriptedChatModel(BaseChatModel):
    """Chooses the next turn from the conversation so far, so it is stateless and survives a resume in a
    new process. One flag injects a fabricated handle into the first report to exercise the validator."""

    inject_bad_handle: bool = False
    delay_s: float = 0.0          # simulated time per streamed chunk
    tools_chars: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: list[Any], **kwargs: Any):  # type: ignore[override]
        self.tools_chars = len(json.dumps(tools))
        return self

    def _turn(self, msgs: list[BaseMessage]) -> tuple[str, list[dict[str, Any]]]:
        k = sum(isinstance(m, AIMessage) for m in msgs)
        last_tools = [m for m in msgs if isinstance(m, ToolMessage)]
        rejected = [m for m in last_tools if str(m.content).startswith("REJECTED")]
        if k == 0:
            return ("The job failed at 02:10. Start with the job's own log lines and any errors in the last hour, "
                    "in parallel.", [
                        _call("search_logs", 1, "Find the failed run's lines; expect a start and an exit code.",
                              terms=["report-export"]),
                        _call("search_logs", 2, "Errors near the failure; expect the proximate error.",
                              terms=["ERROR"], since="-60m")])
        if k == 1:
            enospc = _find(msgs, "No space left")
            exitl = _find(msgs, "exited code=1")
            return ("The exporter hit ENOSPC at 02:09 and the run exited 1 a few seconds later. Disk filled up. "
                    "Why now? Check the disk trend and the runbook.", [
                        _call("update_hypotheses", 3, updates=[
                            {"id": "h-disk", "statement": "The run failed because /var/spool filled up.",
                             "status": "leading", "confidence": 0.7, "support": enospc + exitl, "against": []}]),
                        _call("search_logs", 4, "Disk usage trend; expect usage rising during the run.",
                              terms=["[disk]"]),
                        _call("get_record", 5, "What the runbook says to do.", handle="conf:RB-DISK-SPACE")])
        if k == 2:
            return ("Usage climbed from 88% to 99% inside the run. Did something change? Compare this build with "
                    "the last good one, and look for a deploy.", [
                        _call("get_record", 6, "Failed build; expect VERSION.", handle="jk:report-export#57"),
                        _call("get_record", 7, "Last good build; expect a different VERSION.",
                              handle="jk:report-export#56"),
                        _call("search_logs", 8, "A deploy before the run.", terms=["deploy"], since="-2h"),
                        _call("search_logs", 9, "Did the run clean up its temp files?", terms=["cleanup"])])
        if k == 3:
            deploy = _find(msgs, "[deploy]")
            cleanup = _find(msgs, "cleanup skipped")
            return ("2.4.1 replaced 2.4.0 at 01:00 and this run skipped temp cleanup; the good run was on 2.4.0. "
                    "Release is the likely trigger, disk the mechanism. Propose a rollback for the commander.", [
                        _call("update_hypotheses", 10, updates=[
                            {"id": "h-release", "statement": "Release 2.4.1 stopped cleaning temp files, so the run "
                             "filled /var/spool.", "status": "leading", "confidence": 0.65,
                             "support": deploy + cleanup + ["jk:report-export#57", "jk:report-export#56"],
                             "against": []}]),
                        _call("propose_rollback", 11, "Removes the trigger before tonight's run.",
                              service="report-exporter", to_version="2.4.0",
                              reason="2.4.1 skips temp cleanup; see the deploy and cleanup log lines.")])
        reports = [m for m in last_tools if m.name == "submit_report"]
        enospc, deploy, cleanup = _find(msgs, "No space left")[:1], _find(msgs, "[deploy]")[:1], _find(msgs, "cleanup skipped")[:1]
        support = enospc + deploy + cleanup + ["jk:report-export#57", "jk:report-export#56"]
        actions = (["Purge /var/spool/report-exporter/tmp per conf:RB-DISK-SPACE while no job runs.",
                    "Ask Reporting to fix temp cleanup in 2.4.1 before tonight's run."]
                   if rejected else
                   ["Roll back report-exporter to 2.4.0 (see the proposal's gate state below).",
                    "Purge /var/spool/report-exporter/tmp per conf:RB-DISK-SPACE while no job runs."])
        note = ("The rollback was rejected: " + str(rejected[-1].content).split(":", 1)[1].split(".")[0].strip()
                + ". Re-plan: purge per the runbook instead. " if rejected else "")
        cited = support + (["log:worker-01/deadbeef"] if self.inject_bad_handle and not reports else [])
        return (note + "Enough evidence. Submit the report.", [
            _call("submit_report", 12 + len(reports), summary=f"Run 57 failed with ENOSPC on /var/spool "
                  f"({', '.join(enospc)}) after release 2.4.1 ({', '.join(deploy)}) skipped temp cleanup "
                  f"({', '.join(cleanup)}); run 56 on 2.4.0 succeeded (jk:report-export#56).",
                  hypotheses=[{"statement": "2.4.1 temp-file handling filled /var/spool.", "confidence": 0.65,
                               "support": cited, "against": []}],
                  actions=actions, open_questions=["Did other jobs on worker-01 also fail after 02:09?"])])

    def _usage(self, msgs: list[BaseMessage], out_chars: int) -> dict[str, Any]:
        in_chars = sum(len(str(m.content)) for m in msgs) + self.tools_chars
        i, o = in_chars // 4, out_chars // 4
        return {"input_tokens": i, "output_tokens": o, "total_tokens": i + o,
                "input_token_details": {"cache_read": 0, "cache_creation": 0}}

    def _stream(self, messages: list[BaseMessage], stop: list[str] | None = None,
                run_manager: CallbackManagerForLLMRun | None = None, **kwargs: Any) -> Iterator[ChatGenerationChunk]:
        thinking, calls = self._turn(messages)
        meta = {"model_provider": "anthropic", "model_name": "scripted"}  # Anthropic-shaped blocks
        words = re.findall(r"\S+\s*", thinking)
        for w in words:
            if self.delay_s:
                time.sleep(self.delay_s)
            c = AIMessageChunk(content=[{"type": "thinking", "thinking": w, "index": 0}], response_metadata=meta)
            if run_manager:
                run_manager.on_llm_new_token("", chunk=ChatGenerationChunk(message=c))
            yield ChatGenerationChunk(message=c)
        sig = AIMessageChunk(content=[{"type": "thinking", "signature": "scripted-signature", "index": 0}],
                             response_metadata=meta)
        yield ChatGenerationChunk(message=sig)
        for i, call in enumerate(calls, start=1):
            c = AIMessageChunk(content=[], tool_call_chunks=[{"name": call["name"], "args": json.dumps(call["args"]),
                                                              "id": call["id"], "index": i}], response_metadata=meta)
            if run_manager:
                run_manager.on_llm_new_token("", chunk=ChatGenerationChunk(message=c))
            yield ChatGenerationChunk(message=c)
        out_chars = len(thinking) + sum(len(json.dumps(c["args"])) for c in calls)
        yield ChatGenerationChunk(message=AIMessageChunk(
            content=[], usage_metadata=self._usage(messages, out_chars),
            response_metadata={**meta, "stop_reason": "tool_use" if calls else "end_turn"}))

    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None,
                  run_manager: CallbackManagerForLLMRun | None = None, **kwargs: Any) -> ChatResult:
        thinking, calls = self._turn(messages)
        out_chars = len(thinking) + sum(len(json.dumps(c["args"])) for c in calls)
        msg = AIMessage(content=[{"type": "thinking", "thinking": thinking, "signature": "scripted-signature"}],
                        tool_calls=calls, usage_metadata=self._usage(messages, out_chars),
                        response_metadata={"model_provider": "anthropic", "model_name": "scripted",
                                           "stop_reason": "tool_use" if calls else "end_turn"})
        return ChatResult(generations=[ChatGeneration(message=msg)])
