"""Reader for `descriptions.yaml`, the text the model reads about every tool, parameter, report field and
action kind. That file is written blind (docs/decisions/tools-agent-build.md section 3): the session that
builds the tools never writes its prose, only the placeholder skeleton this module can regenerate.

Layout of the file:

    rationale: <text>                       the `rationale` parameter every gated tool carries
    tools:
      <tool>: {description: <text>, params: {<param>: <text>}}
    objects:
      <Model>: {<field>: <text>}            a nested object, wherever it appears
    action_kinds:
      <kind>: <text>                        the closed list of action kinds
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any, Iterator

import yaml
from pydantic import BaseModel

PATH = Path(__file__).with_name("descriptions.yaml")
PLACEHOLDER = "PLACEHOLDER"
PLACEHOLDER_KIND = "PLACEHOLDER_KIND"


@cache
def load(path: Path = PATH) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def action_kinds(texts: dict[str, Any] | None = None) -> tuple[str, ...]:
    """The closed list of action kinds, in file order."""
    return tuple((texts or load())["action_kinds"])


def nested_models(model: type[BaseModel]) -> dict[str, type[BaseModel]]:
    """Every model reachable from a model's fields, by class name."""
    found: dict[str, type[BaseModel]] = {}

    def walk(annotation: Any) -> None:
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            if annotation.__name__ not in found:
                found[annotation.__name__] = annotation
                for field in annotation.model_fields.values():
                    walk(field.annotation)
            return
        for arg in getattr(annotation, "__args__", ()):
            walk(arg)

    for field in model.model_fields.values():
        walk(field.annotation)
    return found


def owner(model: type[BaseModel], field: str) -> str:
    """The class that declares a field: a field a model inherits is described once, under its base."""
    for cls in reversed(model.__mro__):
        if field in getattr(cls, "__annotations__", {}):
            return cls.__name__
    return model.__name__


def skeleton(surface: dict[str, type[BaseModel]], *, gated: type[BaseModel]) -> dict[str, Any]:
    """The file's keys for a tool surface, every text a placeholder. `surface` maps a tool to its argument
    model with every optional input present; `gated` is the model of the `rationale` parameter."""
    classes = {gated.__name__: gated}
    for model in surface.values():
        classes |= nested_models(model)
    objects: dict[str, dict[str, str]] = {}
    for cls in classes.values():
        for field in cls.model_fields:
            objects.setdefault(owner(cls, field), {})[field] = PLACEHOLDER
    return {"rationale": PLACEHOLDER,
            "tools": {name: {"description": PLACEHOLDER, "params": {f: PLACEHOLDER for f in model.model_fields}}
                      for name, model in surface.items()},
            "objects": objects,
            "action_kinds": {PLACEHOLDER_KIND: PLACEHOLDER}}


def paths(node: Any, prefix: str = "") -> Iterator[tuple[str, Any]]:
    """Every leaf of the file as (dotted path, value)."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from paths(value, f"{prefix}.{key}" if prefix else str(key))
    else:
        yield prefix, node


def placeholders(texts: dict[str, Any] | None = None) -> list[str]:
    """The paths that still hold a placeholder, in a key or in a text."""
    return [path for path, value in paths(texts or load()) if PLACEHOLDER in path or PLACEHOLDER in str(value)]


def problems(texts: dict[str, Any], want: dict[str, Any]) -> list[str]:
    """How a descriptions file differs from the surface it should describe: a missing text, a text for
    something that does not exist, an empty text. `action_kinds` is the author's own list, so only its
    shape is checked."""
    have = {p for p, _ in paths({k: v for k, v in texts.items() if k != "action_kinds"})}
    need = {p for p, _ in paths({k: v for k, v in want.items() if k != "action_kinds"})}
    out = [f"missing: {p}" for p in sorted(need - have)] + [f"unknown: {p}" for p in sorted(have - need)]
    out += [f"empty: {p}" for p, v in paths(texts) if not isinstance(v, str) or not v.strip()]
    kinds = texts.get("action_kinds")
    if not isinstance(kinds, dict) or not kinds:
        out.append("action_kinds: needs at least one kind")
    elif bad := [k for k in kinds if not isinstance(k, str) or not k.replace("_", "").isalnum() or k != k.lower()]:
        if bad != [PLACEHOLDER_KIND]:
            out.append(f"action_kinds: a kind is lower-case words joined by underscores, not {bad}")
    return out
