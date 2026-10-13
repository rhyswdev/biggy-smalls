"""Models: the factory, the per-provider request settings, reasoning normalisation and the scripted model
(docs/research/agent-design.md axis 6).

A real provider is reached through LangChain's `init_chat_model`, chosen by config, with per-provider
keyword arguments built here. We do not use `configurable_fields`: a default for one provider's reasoning
setting would leak into another provider's request.

The scripted model is a keyless, deterministic stand-in. It streams provider-shaped chunks (reasoning
blocks, tool-call chunks, usage) from a script, so the graph, the gate, the event mapping and the run
record can be exercised offline. It proves the plumbing, not the agent.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import sys
import time
from typing import Any, Callable, Iterator

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from smalls.agent.config import SCRIPTED, ModelSpec, live_allowed

# Profile keys that are ours, not the provider's.
_OWN_KEYS = ("cache", "max_input_tokens", "script")
Script = Callable[[list[BaseMessage], list[dict[str, Any]]], tuple[str, list[dict[str, Any]]]]


def make_model(spec: ModelSpec, *, timeout_s: float = 600, cache_ttl: str | None = None) -> tuple[BaseChatModel, dict[str, Any]]:
    """The chat model for a profile, and the extra keyword arguments to bind alongside the tools."""
    params = dict(spec.params)
    if spec.provider == SCRIPTED:
        return ScriptedChatModel(script=params.get("script"), **{k: v for k, v in params.items() if k in ("delay_s",)}), {}
    if not live_allowed():
        raise RuntimeError("a live model was asked for under pytest; set SMALLS_LIVE=1 to allow it")
    if not spec.model:
        raise SystemExit(f"profile {spec.profile!r} has no model; set SMALLS_MODEL")
    from langchain.chat_models import init_chat_model

    bind: dict[str, Any] = {}
    cache = params.get("cache", False)
    for key in _OWN_KEYS:
        params.pop(key, None)
    if cache and spec.provider == "anthropic":
        # Top-level automatic caching: the frozen system prompt and tools, then the growing history.
        bind["cache_control"] = {"type": "ephemeral", **({"ttl": cache_ttl} if cache_ttl else {})}
    # The timeout is explicit because the client library's default is none. The provider's own client
    # retries twice; the graph's retry policy sits above that.
    return init_chat_model(spec.model, model_provider=spec.provider, timeout=timeout_s, max_retries=2, **params), bind


def input_limit(spec: ModelSpec, model: BaseChatModel) -> int | None:
    """The input window the context guard measures against: the profile's, else the model's own."""
    own = spec.params.get("max_input_tokens")
    if own:
        return int(own)
    profile = getattr(model, "profile", None) or {}
    return profile.get("max_input_tokens")


def provider_tools(definitions: list[dict[str, Any]], provider: str) -> list[dict[str, Any]]:
    """Our provider-neutral tool definitions in the shape a provider's `bind_tools` takes."""
    return [{"type": "function", "function": {"name": d["name"], "description": d["description"], "parameters": d["parameters"]}}
            for d in definitions]


def retryable(error: Exception) -> bool:
    """What the graph's retry policy retries: timeouts, connection errors, 429 and 5xx. Nothing else: the
    framework's default predicate also retries a provider's 400 and 401, which only repeat the failure."""
    if isinstance(error, (TimeoutError, ConnectionError)):
        return True
    if type(error).__name__ in ("APITimeoutError", "APIConnectionError", "RateLimitError", "InternalServerError", "OverloadedError"):
        return True
    status = getattr(error, "status_code", None)
    return isinstance(status, int) and (status == 429 or status >= 500)


def reasoning_text(chunk: BaseMessage) -> tuple[str, str]:
    """A message or streamed chunk as (reasoning, visible text), the same for every provider. The standard
    content blocks map each provider's reasoning to one `reasoning` block type. A model that returns no
    reasoning gives ''."""
    reasoning, text = [], []
    for block in chunk.content_blocks:
        if block.get("type") == "reasoning":
            reasoning.append(block.get("reasoning") or "")
        elif block.get("type") == "text":
            text.append(block.get("text") or "")
    return "".join(reasoning), "".join(text)


# -- the scripted model ------------------------------------------------------------------------------------


def scripted_call(name: str, n: int, rationale: tuple[str, str] | None = None, **args: Any) -> dict[str, Any]:
    """A tool call as a script writes it. `rationale` is (tests, expect)."""
    if rationale is not None:
        args = {"rationale": {"tests": rationale[0], "expect": rationale[1]}, **args}
    return {"name": name, "args": args, "id": f"call_{n:02d}_{name}", "type": "tool_call"}


def import_local(module: str):
    """Import a module named on the command line. The working directory is searched too: a console script
    does not put it on the path, and a fixture registry or a script lives in the repository, not in the
    installed package."""
    if os.getcwd() not in sys.path:
        sys.path.insert(0, os.getcwd())
    return importlib.import_module(module)


def load_script(script: Script | str | None) -> Script:
    """A script is a callable `(messages, tools) -> (reasoning, tool calls)`, or its `module:name` path.
    It chooses the next turn from the conversation so far, so it is stateless and survives a resume in a
    new process."""
    if callable(script):
        return script
    if not script:
        raise SystemExit("the scripted profile needs a script: pass --script module:function")
    module, _, name = script.partition(":")
    return getattr(import_local(module), name or "turn")


class ScriptedChatModel(BaseChatModel):
    script: Any = None            # a callable, or "module:function"
    delay_s: float = 0.0          # simulated time per streamed chunk
    tools: list[dict[str, Any]] = []

    @property
    def _llm_type(self) -> str:
        return SCRIPTED

    def bind_tools(self, tools: list[Any], **kwargs: Any):  # type: ignore[override]
        return self.model_copy(update={"tools": list(tools)})

    def _turn(self, messages: list[BaseMessage]) -> tuple[str, list[dict[str, Any]]]:
        return load_script(self.script)(messages, self.tools)

    def _usage(self, messages: list[BaseMessage], out_chars: int) -> dict[str, Any]:
        in_chars = sum(len(str(m.content)) for m in messages) + len(json.dumps(self.tools))
        i, o = in_chars // 4, out_chars // 4
        return {"input_tokens": i, "output_tokens": o, "total_tokens": i + o,
                "input_token_details": {"cache_read": 0, "cache_creation": 0}}

    def _stream(self, messages: list[BaseMessage], stop: list[str] | None = None,
                run_manager: CallbackManagerForLLMRun | None = None, **kwargs: Any) -> Iterator[ChatGenerationChunk]:
        thinking, calls = self._turn(messages)
        meta = {"model_provider": "anthropic", "model_name": SCRIPTED}      # reasoning blocks in one provider's shape
        for word in re.findall(r"\S+\s*", thinking):
            if self.delay_s:
                time.sleep(self.delay_s)
            chunk = AIMessageChunk(content=[{"type": "thinking", "thinking": word, "index": 0}], response_metadata=meta)
            if run_manager:
                run_manager.on_llm_new_token("", chunk=ChatGenerationChunk(message=chunk))
            yield ChatGenerationChunk(message=chunk)
        signature = AIMessageChunk(content=[{"type": "thinking", "signature": "scripted-signature", "index": 0}], response_metadata=meta)
        yield ChatGenerationChunk(message=signature)
        for i, call in enumerate(calls, start=1):
            chunk = AIMessageChunk(content=[], response_metadata=meta, tool_call_chunks=[
                {"name": call["name"], "args": call["args"] if isinstance(call["args"], str) else json.dumps(call["args"]),
                 "id": call["id"], "index": i}])
            if run_manager:
                run_manager.on_llm_new_token("", chunk=ChatGenerationChunk(message=chunk))
            yield ChatGenerationChunk(message=chunk)
        out_chars = len(thinking) + sum(len(json.dumps(c["args"])) for c in calls)
        yield ChatGenerationChunk(message=AIMessageChunk(
            content=[], usage_metadata=self._usage(messages, out_chars),
            response_metadata={**meta, "stop_reason": "tool_use" if calls else "end_turn"}))

    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None,
                  run_manager: CallbackManagerForLLMRun | None = None, **kwargs: Any) -> ChatResult:
        thinking, calls = self._turn(messages)
        out_chars = len(thinking) + sum(len(json.dumps(c["args"])) for c in calls)
        message = AIMessage(content=[{"type": "thinking", "thinking": thinking, "signature": "scripted-signature"}],
                            tool_calls=calls, usage_metadata=self._usage(messages, out_chars),
                            response_metadata={"model_provider": "anthropic", "model_name": SCRIPTED,
                                               "stop_reason": "tool_use" if calls else "end_turn"})
        return ChatResult(generations=[ChatGeneration(message=message)])
