"""The argument models of the eleven tools: the source of every model-facing tool schema
(docs/PLAN.md section 4; the concrete forms are docs/decisions/tools-agent-build.md section 6.5, K1).

The models carry structure only. Every description the model reads comes from `descriptions.yaml`, which
is written blind (build record section 3), and `registry.definition` puts the two together.

Rules that shape these models:
- `now` is never a parameter: the harness injects it.
- A value that comes from the bundle (a sourcetype, a host, a channel, a provider, a service, a metric) is
  a free string checked at run time against visible rows, never an enum, so the schemas are the same for
  every bundle.
- A strict schema cannot hold an object with open keys, so a free-form input is a list of name and value
  pairs.
- A tag input exists only when tags are on, and its values come from the tagging profiles, so the tools
  follow the taxonomy if it changes.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, create_model

from smalls.contracts.report import Strict
from smalls.tools.ingest.tagging import profiles

UNTAGGED = "untagged"       # a tag input's value for "carries no value for this facet" (open point P3)


class Pair(Strict):
    name: str
    value: str


class ListAlertsArgs(Strict):
    since: str
    until: str | None = None
    source: str | None = None
    text: str | None = None
    state: Literal["active", "all"] = "all"


class GetRecordArgs(Strict):
    handle: str
    part: int = Field(1, ge=1)


GROUP_BY = ("minute", "host", "level", "template")


class SearchLogsArgs(Strict):
    terms: list[str] = Field(default_factory=list, max_length=8)
    sourcetype: str | None = None
    host: str | None = None
    since: str
    until: str | None = None
    group_by: Literal[GROUP_BY] | None = None
    bucket: Literal["1m", "5m"] = "1m"
    limit: int = Field(30, ge=1, le=50)


class QueryMetricsArgs(Strict):
    metric: str
    scope: str
    since: str
    until: str | None = None
    rollup: Literal["avg", "max", "min", "sum"] | None = None


class ListChangesArgs(Strict):
    since: str
    until: str | None = None
    text: str | None = None


class ItsmQueryArgs(Strict):
    table: Literal["incident", "journal", "user", "group"]
    filters: list[Pair] = Field(default_factory=list, max_length=6)
    text: str | None = None
    fields: list[str] = Field(default_factory=list, max_length=12)
    limit: int = Field(20, ge=1, le=20)


class SearchKnowledgeArgs(Strict):
    query: str = Field(min_length=1, max_length=300)
    k: int = Field(5, ge=1, le=5)


class GetTopologyArgs(Strict):
    entity: str
    hops: Literal[1, 2] = 1


class GetOncallArgs(Strict):
    service: str | None = None
    policy: str | None = None


class ReadChatArgs(Strict):
    channel: str | None = None
    since: str
    until: str | None = None
    text: str | None = None
    thread_ts: str | None = None


class CheckStatusPageArgs(Strict):
    provider: str | None = None


# The eleven, in the fixed order the model sees them (PLAN section 4's table).
ARGS: dict[str, type[BaseModel]] = {
    "list_alerts": ListAlertsArgs,
    "get_record": GetRecordArgs,
    "search_logs": SearchLogsArgs,
    "query_metrics": QueryMetricsArgs,
    "list_changes": ListChangesArgs,
    "itsm_query": ItsmQueryArgs,
    "search_knowledge": SearchKnowledgeArgs,
    "get_topology": GetTopologyArgs,
    "get_oncall": GetOncallArgs,
    "read_chat": ReadChatArgs,
    "check_status_page": CheckStatusPageArgs,
}

# The tag input each tool gains when tags are on: tool -> facet. `search_logs` also gains the grouping.
TAG_INPUTS: dict[str, str] = {"list_alerts": "signal", "search_logs": "signal", "list_changes": "change_kind",
                              "itsm_query": "signal", "read_chat": "act"}
TAG_GROUPING: dict[str, str] = {"search_logs": "signal"}

# A facet that stops meeting the precision bar is dropped here: no input, no row label, no inventory
# count. A test holds this to what the hand-labelled sample measures. Today all three are offered.
NOT_OFFERED: frozenset[str] = frozenset()


def offered_facets() -> dict[str, tuple[str, ...]]:
    """Every facet a tool may offer, with the closed set of values a tag on it can hold."""
    return {facet: values for facet, values in profiles.facets().items() if facet not in NOT_OFFERED}


def tag_values(facet: str) -> tuple[str, ...]:
    """What a tag input accepts: the facet's values, and `untagged`."""
    return (*offered_facets()[facet], UNTAGGED)


def _with_tags(name: str, base: type[BaseModel]) -> type[BaseModel]:
    fields: dict = {}
    facet = TAG_INPUTS.get(name)
    if facet in offered_facets():
        fields[facet] = (Literal[tag_values(facet)] | None, None)
    grouping = TAG_GROUPING.get(name)
    if grouping in offered_facets():
        fields["group_by"] = (Literal[(*GROUP_BY, grouping)] | None, None)
    if not fields:
        return base
    return create_model(base.__name__, __base__=base, **fields)


def args_models(*, tags: bool) -> dict[str, type[BaseModel]]:
    """The argument model of every tool, in order. Tag inputs exist only when `tags` is true."""
    if not tags:
        return dict(ARGS)
    return {name: _with_tags(name, base) for name, base in ARGS.items()}
