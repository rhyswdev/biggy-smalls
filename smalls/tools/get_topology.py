"""`get_topology`: what a name is in the CMDB, and what it is connected to (PLAN section 4; section 6.4, K7
and K9 of docs/decisions/tools-agent-build.md).

`entity` is any name a source uses for a thing. It is matched to CMDB items by these methods, tried in
this order, and each candidate is printed with the method that matched it: exact; casefold; domain stripped
at the first dot; pod suffix stripped; an IP address to the item that lists it; an external id the item
records. The two strippings are also tried before an external id is compared, and the line says so.
Nothing looser is tried. A name that matches no item stays unresolved, and is printed as unresolved.

Edges come from three places, each read from visible rows when the call is made, and each printed with its
source: the CMDB's relationship rows; the relationship lists on monitored entities; and a `calls` edge
from `service` to `peer.service` for any visible monitoring record tagged with both. An edge's row is the
record that states it. The sources are not reconciled: where they disagree, or where one has an edge
another lacks, both are printed as they are.

Two names are the same node when both resolve to the same single CMDB item. Up to two hops are walked by
a recursive query over the edges, ordered by hop, then source. Who is on call is `get_oncall`'s.
"""

from __future__ import annotations

import json
import re
from typing import Any

from smalls.tools import derive, errors
from smalls.tools.parts import Line, Parts
from smalls.tools.registry import ToolContext

METHODS = ("exact", "casefold", "domain stripped", "pod suffix stripped", "IP address", "external id")
POD = re.compile(r"^(?P<base>.+?)-[a-z0-9]{8,10}-[a-z0-9]{5}$")       # <workload>-<replica set hash>-<pod id>
IP = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
SERVICE_TAG, PEER_TAG = "service", "peer.service"
NO_NATIVE = "(no single native query: CMDB relationships, monitored entities and service-call tags are read side by side)"
UNRESOLVED = "no CMDB item matched {name!r} by any method ({methods}): it stays unresolved"
REACH = """
WITH RECURSIVE edge(a, b) AS (SELECT DISTINCT json_extract(value, '$[0]'), json_extract(value, '$[1]') FROM json_each(?)),
     reach(node, hop) AS (SELECT value, 0 FROM json_each(?)
                          UNION
                          SELECT CASE WHEN edge.a = reach.node THEN edge.b ELSE edge.a END, reach.hop + 1
                          FROM reach JOIN edge ON reach.node IN (edge.a, edge.b) WHERE reach.hop + 1 < ?)
SELECT node, min(hop) AS hop FROM reach GROUP BY node
"""


def run(args: Any, ctx: ToolContext) -> Parts:
    name = args.entity.strip()
    graph = Graph(ctx)
    items = [(ci, how) for ci in graph.items if (how := match_item(name, ci))]
    entities = [(e, how) for e in graph.entities if (how := "exact id" if name == e["native_id"] else match_names(name, e["title"] or ""))]
    tags = [(tag, how) for tag in graph.tag_names if (how := match_names(name, tag))]
    if not items and not entities and not tags:
        known = sorted({ci["title"] for ci in graph.items} | {e["title"] for e in graph.entities} | set(graph.tag_names))
        raise errors.Refusal(errors.unknown_value("entity", name, known))
    start = {graph.node(name)} | {ci["handle"] for ci, _ in items} | {graph.node(e["title"]) for e, _ in entities} | {graph.node(tag) for tag, _ in tags}
    header = [f"entity {name!r}"]
    if not items:
        header.append(UNRESOLVED.format(name=name, methods=", ".join(METHODS)))
    header += [f"service tag {tag!r} matched by {how}; {graph.end(tag)}" for tag, how in tags]
    # Every other name that resolves to a matched item is one of its names too, and is printed with how it resolved.
    matched = {ci["handle"] for ci, _ in items}
    direct = {e["handle"] for e, _ in entities}
    also = [e for e in graph.entities if e["handle"] not in direct and graph.resolved(e["title"]) in matched]
    header += [f"service tag {tag!r} is another name for a matched item; {graph.end(tag)}" for tag in graph.tag_names
               if tag not in {t for t, _ in tags} and graph.resolved(tag) in matched]
    rows = ([graph.item_line(ci, how) for ci, how in items] + [graph.entity_line(e, f"matched by {how}") for e, how in entities]
            + [graph.entity_line(e, "another name for a matched item") for e in also])
    edges = graph.walk(start, args.hops)
    rows += [graph.edge_line(edge) for edge in edges]
    by_source: dict[str, int] = {}
    for edge in edges:
        by_source[edge["source"]] = by_source.get(edge["source"], 0) + 1
    footer = [f"[{len(edges)} edge{'s' if len(edges) != 1 else ''} within {args.hops} hop{'s' if args.hops != 1 else ''}"
              + (": " + ", ".join(f"{n} from {source}" for source, n in sorted(by_source.items())) if edges else "")
              + ". Each is printed as its source states it; the sources are not reconciled]"]
    if args.hops == 1 and edges:
        footer.append("[hops=2 follows each of these one step further]")
    shown = {row.handle for row in rows}
    referenced = [h for edge in edges for h in (*edge["others"], *(graph.resolved(edge[end]) for end in ("a_name", "b_name"))) if h and h not in shown]
    return Parts(rows=rows, header=header, footer=footer, referenced=list(dict.fromkeys(referenced)), what="rows", keep="nearest",
                 native_query=NO_NATIVE, narrow="Use hops=1, or ask about one of the names at the far end.")


# -- matching names (K9) -----------------------------------------------------------------------------------


def forms(name: str) -> list[tuple[str, str]]:
    """A name's stripped forms, each with the stripping that made it."""
    out = []
    if "." in name and not IP.match(name):
        out.append((name.split(".", 1)[0], "domain stripped"))
    pod = POD.match(name)
    if pod:
        out.append((pod["base"], "pod suffix stripped"))
    return out


def match_names(asked: str, name: str) -> str | None:
    """How two names are the same name, by the first method that says so, or None."""
    if not asked or not name:
        return None
    if asked == name:
        return "exact"
    if asked.casefold() == name.casefold():
        return "casefold"
    for method in ("domain stripped", "pod suffix stripped"):
        if any(how == method and form.casefold() == name.casefold() for form, how in forms(asked)):
            return method
        if any(how == method and form.casefold() == asked.casefold() for form, how in forms(name)):
            return method
    return None


def match_item(asked: str, item: dict[str, Any]) -> str | None:
    """How a name matches a CMDB item, by the first of the six methods that does, or None."""
    by_name = match_names(asked, item["title"] or "")
    if by_name:
        return by_name
    if IP.match(asked) and asked == item["attrs"].get("ip_address"):
        return "IP address"
    for form, how in [(asked, None), *forms(asked)]:
        for external in item["external_ids"]:
            if form.casefold() in (external.casefold(), external.split(":", 1)[-1].casefold()):
                return f"external id {external}" + (f", after {how}" if how else "")
    return None


def rank(method: str) -> int:
    return next(n for n, name in enumerate(METHODS) if method.startswith(name))


# -- the graph, built from visible rows for one call -------------------------------------------------------


class Graph:
    def __init__(self, ctx: ToolContext):
        self.ctx = ctx
        self.items = derive.query(ctx, "SELECT handle, title, status, t_known, attrs FROM visible WHERE kind = 'ci' ORDER BY title, handle")
        for ci in self.items:
            ci["attrs"] = derive.loads(ci["attrs"])
            ci["external_ids"] = [x for x in re.split(r"[,;\s]+", ci["attrs"].get("u_external_ids") or "") if x]
        self.by_handle = {ci["handle"]: ci for ci in self.items}
        self.users = {u["title"]: u for u in derive.query(ctx, "SELECT handle, title, status FROM visible WHERE kind = 'user' ORDER BY handle DESC")}
        self.entities = derive.query(ctx, "SELECT handle, source, dataset, native_id, title, t_known, attrs FROM visible WHERE kind = 'entity' ORDER BY title, handle")
        for e in self.entities:
            e["attrs"] = derive.loads(e["attrs"])
        self._resolved: dict[str, list[tuple[dict[str, Any], str]]] = {}
        self.edges = self._edges()
        self.tag_names = sorted({edge[end] for edge in self.edges if edge["tagged"] for end in ("a_name", "b_name")})

    def candidates(self, name: str) -> list[tuple[dict[str, Any], str]]:
        """The CMDB items a name matches by the earliest method that matches any."""
        if name not in self._resolved:
            found = [(ci, how) for ci in self.items if (how := match_item(name, ci))]
            best = min((rank(how) for _, how in found), default=None)
            self._resolved[name] = [(ci, how) for ci, how in found if rank(how) == best]
        return self._resolved[name]

    def node(self, name: str) -> str:
        """A name's node: the one CMDB item it resolves to, or the name itself when it resolves to none or to several."""
        found = self.candidates(name)
        return found[0][0]["handle"] if len(found) == 1 else f"name:{name.casefold()}"

    def resolved(self, name: str) -> str | None:
        found = self.candidates(name)
        return found[0][0]["handle"] if len(found) == 1 else None

    def end(self, name: str) -> str:
        """One end of an edge: the CMDB item its name resolves to, how, and that item's status; or that it resolves to none."""
        found = self.candidates(name)
        if not found:
            return f"{name}: no CMDB item matched"
        if len(found) > 1:
            return f"{name}: matches {len(found)} CMDB items ({', '.join(ci['title'] for ci, _ in found)}), unresolved"
        ci, how = found[0]
        same = how == "exact"
        return f"{name}: " + ("CMDB item" if same else f"CMDB item {ci['title']} by {how}") + f", {ci['attrs'].get('operational_status') or ci['status'] or 'status unknown'}"

    def _edges(self) -> list[dict[str, Any]]:
        """Every edge the visible rows state, one per source, type and pair of nodes. When several records
        state the same edge, the lowest handle is its row and the rest are kept as pointers."""
        stated = []
        for rel in derive.query(self.ctx, "SELECT handle, source, dataset, t_known, attrs FROM visible WHERE kind = 'rel' ORDER BY handle"):
            attrs = derive.loads(rel["attrs"])
            if attrs.get("parent") and attrs.get("child"):
                stated.append((attrs["parent"], attrs.get("type") or "(no type)", attrs["child"], f"{rel['source']}/{rel['dataset']}", rel, False))
        names = {e["native_id"]: e["title"] or e["native_id"] for e in self.entities}
        for e in self.entities:
            label = f"{e['source']}/{e['dataset']}"
            for kind, targets in (e["attrs"].get("from") or {}).items():
                stated += [(e["title"], kind, names.get(t.get("id"), t.get("id")), label, e, False) for t in targets or [] if t.get("id")]
            for kind, sources in (e["attrs"].get("to") or {}).items():
                stated += [(names.get(s.get("id"), s.get("id")), kind, e["title"], label, e, False) for s in sources or [] if s.get("id")]
        for row in derive.query(self.ctx, "SELECT handle, source, t_known, attrs FROM visible WHERE json_type(attrs, '$.tags') = 'array' ORDER BY handle"):
            tags = dict(tag.split(":", 1) for tag in derive.loads(row["attrs"]).get("tags") or [] if isinstance(tag, str) and ":" in tag)
            if tags.get(SERVICE_TAG) and tags.get(PEER_TAG):
                stated.append((tags[SERVICE_TAG], "calls", tags[PEER_TAG], f"{row['source']} tags", row, True))
        merged: dict[tuple, dict[str, Any]] = {}
        for a_name, kind, b_name, source, record, tagged in stated:
            key = (source, kind, self.node(a_name), self.node(b_name))
            edge = merged.setdefault(key, {"a": key[2], "b": key[3], "a_name": a_name, "b_name": b_name, "type": kind, "source": source,
                                           "handle": record["handle"], "t_known": record["t_known"], "others": [], "tagged": tagged})
            if record["handle"] < edge["handle"]:
                edge["others"].append(edge["handle"])
                edge.update(handle=record["handle"], t_known=record["t_known"], a_name=a_name, b_name=b_name)
            elif record["handle"] != edge["handle"]:
                edge["others"].append(record["handle"])
        return list(merged.values())

    def walk(self, start: set[str], hops: int) -> list[dict[str, Any]]:
        """The edges within `hops` of the start nodes, each once, ordered by hop, then source."""
        pairs = json.dumps([[edge["a"], edge["b"]] for edge in self.edges])
        reached = {row["node"]: row["hop"] for row in derive.query(self.ctx, REACH, (pairs, json.dumps(sorted(start)), hops))}
        out = []
        for edge in self.edges:
            near = min((reached[n] for n in (edge["a"], edge["b"]) if n in reached), default=None)
            if near is not None:
                out.append({**edge, "hop": near + 1})
        return sorted(out, key=lambda e: (e["hop"], e["source"], e["handle"], e["a_name"], e["b_name"]))

    # -- lines ---------------------------------------------------------------------------------------------

    def item_line(self, ci: dict[str, Any], how: str) -> Line:
        attrs = ci["attrs"]
        owner = attrs.get("owned_by")
        user = self.users.get(owner) if owner else None
        flag = (user["status"] or "active flag unknown") if user else "no user record"
        cells = [ci["handle"], f"CMDB item {ci['title']}", f"matched by {how}", f"status {attrs.get('operational_status') or ci['status'] or '(none)'}",
                 f"owner {owner} ({flag})" if owner else "owner (none)", f"support group {attrs.get('support_group') or '(none)'}",
                 f"external ids {', '.join(ci['external_ids']) or '(none)'}"]
        return Line(ci["handle"], "  ".join(cells), ci["t_known"], refs=(user["handle"],) if user else ())

    def entity_line(self, entity: dict[str, Any], how: str) -> Line:
        attrs = entity["attrs"]
        cells = [entity["handle"], f"monitored entity {entity['title']}", how, f"type {attrs.get('type') or '(none)'}",
                 f"id {entity['native_id']}", f"tags {', '.join(attrs.get('tags') or []) or '(none)'}", self.end(entity["title"])]
        found = self.resolved(entity["title"])
        return Line(entity["handle"], "  ".join(cells), entity["t_known"], refs=(found,) if found else ())

    def edge_line(self, edge: dict[str, Any]) -> Line:
        text = (f"{edge['handle']}  hop {edge['hop']}  {edge['a_name']} -[{edge['type']}]-> {edge['b_name']}  source {edge['source']}  "
                f"[{self.end(edge['a_name'])}] [{self.end(edge['b_name'])}]")
        if edge["others"]:
            text += f"  also stated by {' '.join(sorted(edge['others']))}"
        return Line(edge["handle"], text, edge["t_known"], refs=tuple(sorted(edge["others"])))
