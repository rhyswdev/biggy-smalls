"""Every error a tool can answer with: one module, one fixed sentence per case (ruling A2; K8 in
docs/decisions/tools-agent-build.md). An error comes back as text the model can act on, never as an
exception, and never with a traceback or a path.

`status` is `error` for all of these but an empty result and a clamped window, which are answers.

A handle that is unknown, not yet visible or absent gets one sentence, so the three cannot be told apart:
an agent must not learn that a record exists in its future.
"""

from __future__ import annotations

from pydantic import ValidationError

from smalls.contracts.envelope import Envelope
from smalls.contracts.handles import is_handle

NOT_IN_BUNDLE = "{handle}: not in this bundle."
MALFORMED_HANDLE = ("{handle}: not a handle. A handle is the first token of a row a tool printed, such as the ones in "
                    "earlier results; copy it exactly.")
MINTED_PER_CALL = "{handle}: this handle was minted for one {what} and holds no record to fetch. {again}"
BAD_ARGUMENTS = "Invalid arguments for {tool}: {problems}. Fix them and call again."
INTERNAL = "{tool} failed inside the tool ({kind}). Try narrower arguments or another tool."
EMPTY = "0 rows. {hint}"
CLAMPED = "The window was clamped to now ({now}): nothing after now exists yet."
OVER_CAP = "{tool} produced a result over its cap ({size} against {cap}). This is a fault in the tool, not in the call."
UNKNOWN_VALUE = "{field}={value!r} matches nothing visible. {field} is one of: {values}."
PART_OUT_OF_RANGE = "{handle} has {parts} part{s}; part {part} does not exist."


class Refusal(Exception):
    """Raised inside a tool to answer with an error envelope from wherever the problem was found."""

    def __init__(self, envelope: Envelope):
        super().__init__(envelope.text)
        self.envelope = envelope


def for_handle(handle: str) -> Envelope | None:
    """The answer for a handle that cannot be fetched whatever the store holds: one that is not a handle at
    all, or one minted for a single call or a single run. None means the handle is worth looking up."""
    if not is_handle(handle):
        return malformed_handle(handle)
    if handle.startswith("q:"):
        return minted(handle, "call (a count a tool computed)", "Re-run the search that printed it to see the count again.")
    if handle.startswith("ic:note/"):
        return minted(handle, "run (an incident commander's note)", "The note's text is in the result that printed it.")
    if handle.startswith("metric:") and "@" in handle:
        return minted(handle, "call (a metric series over one window)",
                      "Call query_metrics with the same metric, scope and window to see the series again.")
    return None


def error(text: str, kind: str = "refused", **meta) -> Envelope:
    """An error envelope. `meta.error` names the case, so a caller can tell a call that never ran (bad
    arguments) from one that did."""
    return Envelope(text, status="error", meta={**meta, "error": kind})


def bad_arguments(tool: str, problem: Exception | str) -> Envelope:
    """Names each field that is wrong and what is wrong with it."""
    if isinstance(problem, ValidationError):
        problem = "; ".join(f"{'.'.join(str(x) for x in e['loc']) or 'arguments'}: {e['msg']}" for e in problem.errors())
    return error(BAD_ARGUMENTS.format(tool=tool, problems=problem), "arguments")


def internal(tool: str, exc: BaseException) -> Envelope:
    """The exception's type only: its message may hold a path or a row."""
    return error(INTERNAL.format(tool=tool, kind=type(exc).__name__), "internal")


def not_in_bundle(handle: str) -> Envelope:
    return error(NOT_IN_BUNDLE.format(handle=handle), "not_in_bundle")


def malformed_handle(handle: str) -> Envelope:
    return error(MALFORMED_HANDLE.format(handle=handle), "malformed_handle")


def minted(handle: str, what: str, again: str) -> Envelope:
    return error(MINTED_PER_CALL.format(handle=handle, what=what, again=again), "minted")


def unknown_value(field: str, value: str, values: list[str]) -> Envelope:
    listed = ", ".join(values[:30]) + (f", and {len(values) - 30} more" if len(values) > 30 else "")
    return error(UNKNOWN_VALUE.format(field=field, value=value, values=listed or "(nothing visible)"), "unknown_value")


def over_cap(tool: str, size: str, cap: str) -> Envelope:
    return error(OVER_CAP.format(tool=tool, size=size, cap=cap), "over_cap")
