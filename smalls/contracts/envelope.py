"""The result envelope: what every tool call returns (docs/decisions/tools-agent-build.md section 5).

It replaces the `{text, handles[], native_query, window}` shape of ruling A1 and carries the split of
agent-design ruling 11: a handle is citable only if its content was shown. `shown` is derived from `rows`,
so that holds by construction. `rows` also feeds the run record's evidence file without a second fetch,
and `t_known` is what the carry-forward check reads, since nothing under smalls/ may open the manifest.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, Mapping

# `meta` always carries these. `tag` names the tag input a call used (`signal=errors`, `group_by=signal`),
# or is None; `sampled` is true when a count in the text is an estimate from a sampled source.
META_DEFAULTS: Mapping[str, Any] = MappingProxyType(
    {"total": 0, "returned": 0, "truncated": False, "clamped": False, "empty": False, "sampled": False, "tag": None})


@dataclass(frozen=True)
class Row:
    handle: str
    text: str                       # this handle's slice of the text, as the model saw it
    t_known: str | None = None      # ISO UTC from the store; None for an aggregate or a note


@dataclass(frozen=True)
class Envelope:
    text: str                                   # what the model reads
    rows: tuple[Row, ...] = ()                  # one per handle whose content is in the text
    referenced: tuple[str, ...] = ()            # handles only listed as children or pointers
    native_query: str | None = None
    window: tuple[str, str] | None = None       # ISO UTC, after clamping
    status: Literal["ok", "error"] = "ok"
    meta: Mapping[str, Any] = META_DEFAULTS

    def __post_init__(self) -> None:
        object.__setattr__(self, "rows", tuple(self.rows))
        object.__setattr__(self, "referenced", tuple(self.referenced))
        if self.window is not None:
            object.__setattr__(self, "window", tuple(self.window))
        object.__setattr__(self, "meta", MappingProxyType({**META_DEFAULTS, **self.meta}))

    @property
    def shown(self) -> tuple[str, ...]:
        """The handles whose content is in the text: the only ones a report may cite. Two rows may share a
        handle (two passages of one page), so the list is the distinct handles, in order."""
        return tuple(dict.fromkeys(r.handle for r in self.rows))

    def as_dict(self) -> dict[str, Any]:
        return {"text": self.text, "rows": [{"handle": r.handle, "text": r.text, "t_known": r.t_known} for r in self.rows],
                "shown": list(self.shown), "referenced": list(self.referenced), "native_query": self.native_query,
                "window": list(self.window) if self.window else None, "status": self.status, "meta": dict(self.meta)}
