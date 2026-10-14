"""The tool registry: what a tool is, the fixed order the model sees them in, and the one definition of a
model-facing tool schema (docs/decisions/tools-agent-build.md section 5).

A tool that is not built yet is absent from `registry()`, so the agent never sees it. `surface()` lists
all eleven whether built or not, for the schema printer and the prompt author.

Every read and action tool also carries a required `rationale` the model fills in: the hypothesis the call
tests and what it expects to see. It is part of the model-facing schema only: the caller strips it before
`call`, so a tool's own arguments never include it. Internal tools carry none.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Literal

from pydantic import BaseModel, ValidationError

from smalls.contracts.envelope import Envelope
from smalls.contracts.report import Rationale, strip_keys
from smalls.tools import descriptions, errors, parts, schemas, windows

if TYPE_CHECKING:
    from smalls.tools.store import Store

Kind = Literal["read", "action", "internal"]


@dataclass(frozen=True)
class ToolContext:
    """Injected by the harness, never a tool parameter."""

    store: "Store"                  # clock already set to now
    now: datetime
    tags: bool                      # store.tags_enabled()
    run_id: str | None = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    kind: Kind
    args: type[BaseModel]           # the source of the model-facing schema
    fn: Callable[[BaseModel, ToolContext], Any]     # returns the tool's parts, or a finished envelope; `call` renders
    cap_tokens: int                 # PLAN's token figure, or 6,000 where PLAN gives only a row count
    cap_rows: int | None = None     # PLAN's row, message, edge or cell limit
    mock: bool = False              # true for every action tool in v1


# (cap_tokens, cap_rows) per tool, from PLAN section 4's table.
CAPS: dict[str, tuple[int, int | None]] = {
    "list_alerts": (6000, 40),
    "get_record": (3000, None),
    "search_logs": (4000, 60),
    "query_metrics": (2000, None),
    "list_changes": (6000, 30),
    "itsm_query": (3000, 20),
    "search_knowledge": (3000, 5),
    "get_topology": (6000, 40),
    "get_oncall": (500, None),
    "read_chat": (6000, 60),
    "check_status_page": (500, None),
}

# The tools that are built: name -> the function behind it. It grows through build phases B and D. Filled
# on first use, because a tool module imports this one for its context type.
BUILT: dict[str, Callable[[BaseModel, ToolContext], Any]] = {}


def _load_built() -> None:
    if BUILT:
        return
    from smalls.tools import (check_status_page, get_record, list_alerts, list_changes, query_metrics, read_chat, search_knowledge,
                              search_logs)

    BUILT.update({"list_alerts": list_alerts.run, "get_record": get_record.run, "search_logs": search_logs.run,
                  "query_metrics": query_metrics.run, "list_changes": list_changes.run, "search_knowledge": search_knowledge.run,
                  "read_chat": read_chat.run, "check_status_page": check_status_page.run})


def _not_built(args: BaseModel, ctx: ToolContext) -> Envelope:
    raise NotImplementedError("this tool is not built yet")


def surface(*, tags: bool) -> dict[str, ToolSpec]:
    """All eleven tools in their fixed order, built or not. Tag inputs exist only when `tags` is true."""
    _load_built()
    return {name: ToolSpec(name, "read", args, BUILT.get(name, _not_built), *CAPS[name])
            for name, args in schemas.args_models(tags=tags).items()}


def registry(*, tags: bool) -> dict[str, ToolSpec]:
    """The tools the agent may call: the built ones, in the fixed order."""
    return {name: spec for name, spec in surface(tags=tags).items() if name in BUILT}


def call(spec: ToolSpec, raw_args: dict[str, Any], ctx: ToolContext) -> Envelope:
    """The one path every caller uses: the agent, the sweep, the CLI and the leak test. It validates the
    arguments, runs the tool, renders its parts into an envelope, checks the result against the tool's
    caps and turns any exception into the error text of ruling A2. It never raises."""
    try:
        args = spec.args.model_validate(raw_args or {})
    except ValidationError as problem:
        return errors.bad_arguments(spec.name, problem)
    try:
        out = spec.fn(args, ctx)
        env = out if isinstance(out, Envelope) else parts.render(out, cap_tokens=spec.cap_tokens, cap_rows=spec.cap_rows)
    except errors.Refusal as refusal:       # a tool's own answer to a call it cannot serve
        return refusal.envelope
    except windows.WindowError as problem:
        return errors.bad_arguments(spec.name, str(problem))
    except Exception as exc:
        return errors.internal(spec.name, exc)
    if parts.tokens(env.text) > spec.cap_tokens:
        return errors.over_cap(spec.name, f"{parts.tokens(env.text)} tokens", f"{spec.cap_tokens}")
    if spec.cap_rows is not None and len(env.rows) > spec.cap_rows:
        return errors.over_cap(spec.name, f"{len(env.rows)} rows", f"{spec.cap_rows}")
    return env


# -- the model-facing schema -------------------------------------------------------------------------------


def _plain_optional(node: Any) -> Any:
    """An optional input is left out, not sent as null: `anyOf [X, null]` becomes `X`, and a null default
    goes. A provider that wants every property required and nullable gets that from its own adapter."""
    if isinstance(node, list):
        return [_plain_optional(item) for item in node]
    if not isinstance(node, dict):
        return node
    out = {k: _plain_optional(v) for k, v in node.items()}
    options = out.get("anyOf")
    if isinstance(options, list) and {"type": "null"} in options:
        rest = [o for o in options if o != {"type": "null"}]
        if len(rest) == 1:
            del out["anyOf"]
            out = {**rest[0], **out}
    if "default" in out and out["default"] is None:
        del out["default"]
    return out


def parameters(args: type[BaseModel]) -> dict[str, Any]:
    """A tool's own arguments as a JSON Schema: structure only, every object closed, no annotation text."""
    return _plain_optional(strip_keys(args.model_json_schema(), ("title", "description")))


def definition(spec: ToolSpec, texts: dict[str, Any] | None = None) -> dict[str, Any]:
    """The tool as the model sees it, provider-neutral: `{name, description, parameters}`. A gated tool
    gains the `rationale` parameter, first, so the model states its reason before its arguments. The
    per-provider adapter in the agent turns this into the provider's own tool shape."""
    texts = texts or descriptions.load()
    own = texts["tools"][spec.name]
    schema = parameters(spec.args)
    props, required, defs = schema.get("properties", {}), list(schema.get("required", [])), dict(schema.get("$defs", {}))
    classes = descriptions.nested_models(spec.args)
    if spec.kind != "internal":
        rationale = parameters(Rationale)
        defs[Rationale.__name__] = {k: v for k, v in rationale.items() if k != "$defs"}
        classes[Rationale.__name__] = Rationale
        props = {"rationale": {"$ref": f"#/$defs/{Rationale.__name__}"}, **props}
        required = ["rationale", *required]
        own = {**own, "params": {"rationale": texts["rationale"], **own["params"]}}
    for name, prop in props.items():
        prop["description"] = own["params"][name]
    for cls_name, node in defs.items():
        for field, prop in node.get("properties", {}).items():
            prop["description"] = texts["objects"][descriptions.owner(classes[cls_name], field)][field]
    out = {"type": "object", "properties": props, "required": required, "additionalProperties": False}
    if defs:
        out["$defs"] = defs
    return {"name": spec.name, "description": own["description"], "parameters": out}
