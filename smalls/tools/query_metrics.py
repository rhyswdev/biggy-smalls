"""`query_metrics`: one served metric query over a window (PLAN section 4; section 6.4 and K3 of
docs/decisions/tools-agent-build.md; ruling A3 for the handle).

`metric` is the aggregator and metric name as the inventory prints them, and `scope` is the query's tag
filter, compared as a set. The pair must select exactly one served query. A `scope` that adds a group tag
selects the series that carry it. Anything else answers "not in this bundle" and lists what is served
under that metric name: a series with no visible point is never named, so the answer for a series that
does not exist and for one that has not started is the same.

A point is visible once its interval has closed before `now`, so the last point printed is the last
visible one and nothing says how far the data runs. Up to four series are returned, in tag order, each
merged into at most 45 buckets by `rollup`. A served null stays a null, and a bucket is null only when
every point in it is.

The summary is computed over the native points, before any merging: minimum, maximum, runs of nulls, and,
for each visible monitor whose query is exactly this one, the first point past its critical and warning
thresholds in the monitor's own direction. That is one point crossing a number. Whether the monitor fired
is in `list_alerts`.

The result is one row. Its handle is the query with the clamped window, minted for this call.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Callable

from smalls.contracts.handles import metric_handle
from smalls.tools import derive, errors
from smalls.tools.parts import Line, Parts
from smalls.tools.registry import ToolContext
from smalls.tools.store import render_native
from smalls.tools.windows import Window, iso, resolve

FACET = "signal"
MAX_SERIES = 4
MAX_POINTS = 45
MAX_RUNS = 4            # null runs named per series; the rest are counted
MAX_LISTED = 12
ROLLUPS: dict[str, Callable[[list[float]], float]] = {"avg": lambda v: sum(v) / len(v), "max": max, "min": min, "sum": sum}
COMPARE: dict[str, Callable[[float, float], bool]] = {">": lambda v, t: v > t, ">=": lambda v, t: v >= t, "<": lambda v, t: v < t,
                                                      "<=": lambda v, t: v <= t}
# A threshold monitor's query: <time aggregation>(<period>):<metric query> <comparison> <number>
MONITOR = re.compile(r"^\s*(\w+)\(([^)]*)\):(.+?)\s*(>=|<=|>|<)\s*(-?\d+(?:\.\d+)?)\s*$", re.S)
NOT_SERVED = "metric={metric!r} scope={scope!r}: not in this bundle. {known}"
NO_SUCH_NAME = "No visible series has that metric name. metric is the aggregator and the name, as the source inventory prints them: {names}."
SERVED_AS = "Visible series of {name}: {listed}."
AMBIGUOUS = "More than one visible query has that metric and scope. Add a group tag to scope to pick a series: {listed}."
ONE_POINT = "A point past a threshold is one value, not the monitor firing: the monitor judges {aggregation} over {period}."
NO_MONITOR = "thresholds: no visible monitor has exactly this query, so no threshold was tested"
TOTAL = " (a total over {width}, so it reads higher than any one point; min and max below are of single points)"


def run(args: Any, ctx: ToolContext) -> Parts:
    window = resolve(args.since, args.until, ctx.now)
    served, chosen = _select(ctx, args.metric, args.scope)
    chosen = sorted(chosen, key=lambda s: ",".join(s["tag_set"]))
    shown, rest = chosen[:MAX_SERIES], chosen[MAX_SERIES:]
    points: dict[str, list[tuple[int, float | None]]] = {}
    for p in derive.query(ctx, "SELECT series, t, value FROM visible_point WHERE handle = ? AND t >= ? AND t < ? ORDER BY series, t",
                          (served["handle"], window.since, window.until)):
        points.setdefault(p["series"], []).append((p["t"], p["value"]))
    fn = args.rollup or _default_rollup(served["aggr"])
    span = f" from {iso(window.since)} to {iso(window.until)}"
    native = served["query"] + span
    common = dict(window=window, now=ctx.store.now, native_label=f"{served['source']} query")
    times = [t for s in shown for t, _ in points.get(s["expression"], [])]
    if not times:
        first, last = min(s["first"] for s in chosen), max(s["last"] for s in chosen)
        return Parts(native_query=native, **common,
                     empty_hint=f"No visible point of this query is in the window. Its visible points run from {iso(first)} to {iso(last)}.")
    interval = max(s["interval_s"] for s in shown) * 1000
    factor = math.ceil(((max(times) - min(times)) // interval + 1) / MAX_POINTS)
    width, origin = interval * factor, min(times)
    label = _labeller(window, interval)
    monitors = _monitors(ctx, served["query"])
    handle = metric_handle(served["query"], _hhmm(window.since), _hhmm(window.until))
    by = f"  by {','.join(served['by'])}" if served["by"] else ""
    lines = [f"{handle}  {served['metric']}  scope {','.join(sorted(served['scope'])) or '*'}{by}",
             f"window {iso(window.since)} to {iso(window.until)}; visible points from {iso(min(times))} to {iso(max(times))}, one every {_span(interval)}"
             + (f"; shown in buckets of {_span(width)}, each the {fn} of its points" + (TOTAL.format(width=_span(width)) if fn == "sum" else "")
                if factor > 1 else "")]
    for s in shown:
        mine = points.get(s["expression"], [])
        tag = derive.tag_label(s["tags"], (FACET,)) if ctx.tags else ""
        lines.append(f"series {_name(s)}: {len(mine)} points{tag}")
        if not mine:
            continue
        lines.append("  " + _summary(mine, label))
        lines += ["  " + line for line in _breaches(mine, monitors)]
        lines.append("  " + "  ".join(f"{label(t)} {_num(v)}" for t, v in _buckets(mine, origin, width, ROLLUPS[fn])))
    for m in monitors:
        lines.append(f"thresholds from {m['handle']}  {m['title']}: {m['aggregation']}({m['period']}) {m['op']} {_num(m['critical'])}"
                     + (f", warning {_num(m['warning'])}" if m["warning"] is not None else "") + ". "
                     + ONE_POINT.format(aggregation=m["aggregation"], period=m["period"]))
    if not monitors:
        lines.append(NO_MONITOR)
    footer = []
    if rest:
        listed = ", ".join(f"{_name(s)} max {_num(_peak(points.get(s['expression'], [])))}" for s in rest[:MAX_LISTED])
        more = f", and {len(rest) - MAX_LISTED} more" if len(rest) > MAX_LISTED else ""
        footer.append(f"[{len(shown)} of {len(chosen)} series shown, in tag order. Not shown, with each one's maximum in the window: {listed}{more}. "
                      f"Add a series' tag to scope to read it.]")
    if factor > 1:
        native = f"{served['query']}.rollup({fn}, {width // 1000}){span}"
    return Parts(rows=[Line(handle, "\n".join(lines), refs=tuple(m["handle"] for m in monitors))], footer=footer, native_query=native,
                 total=sum(len(points.get(s["expression"], [])) for s in shown), aggregate=True, what="points",
                 meta={"series": len(shown), "buckets_of_s": width // 1000, "rollup": fn if factor > 1 else None}, **common)


# -- selecting the query (K3) ------------------------------------------------------------------------------


def _tags(scope: str) -> set[str]:
    return {tag.strip() for tag in scope.split(",") if tag.strip()} - {"*"}


def _select(ctx: ToolContext, metric: str, scope: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The one served query the pair selects, with the series asked for."""
    served = derive.metric_queries(ctx)
    metric, wanted = metric.strip(), _tags(scope)
    found = []
    for q in served:
        extra = wanted - q["scope"]
        if q["metric"] != metric or not q["scope"] <= wanted:
            continue
        series = [s for s in q["series"] if extra <= set(s["tag_set"])]
        if series:
            found.append((q, series))
    if len(found) == 1:
        return found[0]
    name = metric.split(":", 1)[-1]
    same = [q for q in served if q["name"] == name]
    if found:
        known = AMBIGUOUS.format(listed="; ".join(_described(q) for q, _ in found))
    elif same:
        known = SERVED_AS.format(name=name, listed="; ".join(_described(q) for q in same))
    else:
        names = sorted({q["metric"] for q in served})
        known = NO_SUCH_NAME.format(names=", ".join(names[:MAX_LISTED]) + (f", and {len(names) - MAX_LISTED} more" if len(names) > MAX_LISTED else "")
                                    or "(none is visible yet)")
    raise errors.Refusal(errors.error(NOT_SERVED.format(metric=metric, scope=scope, known=known), "not_in_bundle"))


def _described(q: dict[str, Any]) -> str:
    """A served query as the two arguments that select it, with the group tags a scope may add."""
    groups = sorted({tag for s in q["series"] for tag in s["tag_set"]})
    listed = ", ".join(groups[:MAX_LISTED]) + (f", and {len(groups) - MAX_LISTED} more" if len(groups) > MAX_LISTED else "")
    return f"metric={q['metric']!r} scope={','.join(sorted(q['scope'])) or '*'!r}" + (f" (group tags: {listed})" if groups else "")


# -- the summary and the points ----------------------------------------------------------------------------


def _default_rollup(aggregator: str | None) -> str:
    """The query's own aggregator; a percentile merges as its maximum."""
    if aggregator in ROLLUPS:
        return aggregator
    return "max" if re.fullmatch(r"p\d+(\.\d+)?", aggregator or "") else "avg"


def _buckets(points: list[tuple[int, float | None]], origin: int, width: int, fn: Callable[[list[float]], float]) -> list[tuple[int, float | None]]:
    merged: dict[int, list[float | None]] = {}
    for t, value in points:
        merged.setdefault(origin + (t - origin) // width * width, []).append(value)
    out = []
    for start, values in sorted(merged.items()):
        real = [v for v in values if v is not None]
        out.append((start, fn(real) if real else None))
    return out


def _summary(points: list[tuple[int, float | None]], label: Callable[[int], str]) -> str:
    real = [(t, v) for t, v in points if v is not None]
    if not real:
        return f"every point is null, {label(points[0][0])} to {label(points[-1][0])}"
    low, high = min(real, key=lambda p: p[1]), max(real, key=lambda p: p[1])
    runs, start, last = [], None, None
    for t, v in points:
        if v is None:
            start, last = (t if start is None else start), t
        elif start is not None:
            runs.append((start, last))
            start = None
    if start is not None:
        runs.append((start, last))
    missing = len(points) - len(real)
    nulls = "nulls none" if not runs else (f"nulls {missing} in {len(runs)} run{'s' if len(runs) != 1 else ''}: " + ", ".join(
        label(a) if a == b else f"{label(a)} to {label(b)}" for a, b in runs[:MAX_RUNS]) + (f", and {len(runs) - MAX_RUNS} more" if len(runs) > MAX_RUNS else ""))
    return f"min {_num(low[1])} at {label(low[0])}  max {_num(high[1])} at {label(high[0])}  {nulls}"


def _monitors(ctx: ToolContext, query: str) -> list[dict[str, Any]]:
    """The visible monitors whose query is exactly this served query, with their thresholds."""
    out = []
    for row in derive.query(ctx, "SELECT handle, title, native, flags FROM visible WHERE kind = 'alert_def' ORDER BY handle"):
        try:
            served = json.loads(render_native(row["native"], row["flags"]))
        except ValueError:
            continue
        text = served.get("query") if isinstance(served, dict) else None
        match = MONITOR.match(text) if isinstance(text, str) else None
        if not match or match[3].replace(" ", "") != query.replace(" ", ""):
            continue
        thresholds = (served.get("options") or {}).get("thresholds") or {}
        critical = thresholds.get("critical")
        out.append({"handle": row["handle"], "title": row["title"] or "(no title)", "aggregation": match[1], "period": match[2], "op": match[4],
                    "critical": float(match[5]) if critical is None else float(critical),
                    "warning": float(thresholds["warning"]) if thresholds.get("warning") is not None else None})
    return out


def _breaches(points: list[tuple[int, float | None]], monitors: list[dict[str, Any]]) -> list[str]:
    """For each monitor, the first native point past each threshold, in the monitor's own direction."""
    lines = []
    for m in monitors:
        cells = []
        for level in ("critical", "warning"):
            if m[level] is None:
                continue
            hit = next(((t, v) for t, v in points if v is not None and COMPARE[m["op"]](v, m[level])), None)
            cells.append(f"first point {m['op']} {_num(m[level])} ({level}) at {iso(hit[0])}, value {_num(hit[1])}" if hit
                         else f"no point {m['op']} {_num(m[level])} ({level}) in the window")
        lines.append(f"against {m['handle']}: " + "; ".join(cells))
    return lines


def _peak(points: list[tuple[int, float | None]]) -> float | None:
    return max((v for _, v in points if v is not None), default=None)


def _name(series: dict[str, Any]) -> str:
    return ",".join(series["tag_set"]) or "(the one series of this query)"


def _num(value: float | None) -> str:
    if value is None:
        return "null"
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.0f}" if abs(value) >= 10000 else f"{value:.4g}"


def _span(ms: int) -> str:
    seconds = ms // 1000
    for size, unit in ((3600, "h"), (60, "m")):
        if seconds % size == 0:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


def _hhmm(ms: int) -> str:
    return datetime.fromtimestamp(ms // 1000, timezone.utc).strftime("%H%M")


def _labeller(window: Window, interval: int) -> Callable[[int], str]:
    """How a point's time is written: the clock time, with the date when the window's ends fall on different
    dates and the seconds when points are closer than a minute."""
    form = ("%m-%dT" if window.spans_dates() else "") + ("%H:%M" if interval % 60_000 == 0 else "%H:%M:%S")
    return lambda ms: datetime.fromtimestamp(ms // 1000, timezone.utc).strftime(form)
