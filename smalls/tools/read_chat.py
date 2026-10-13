"""`read_chat`: chat messages (PLAN section 4; section 6.4, K1 and K4 of docs/decisions/tools-agent-build.md).

One row per message, oldest first: handle, UTC time, the message's own `ts`, channel, display name and
the text with mentions resolved. When a result has to be cut it keeps the newest messages and drops the
oldest, because in a channel the latest word is the one that stands.

A thread's reply count, repliers and latest reply are recomputed from the replies visible at `now`: the
export's own summary was taken later and is withheld. Replies are pointers until `thread_ts` asks for
them, except that `text` also matches replies, and a matching reply is a row of its own.

The channel list is the channels with a visible message. A channel's members are never shown.
"""

from __future__ import annotations

import json
import re
from typing import Any

from smalls.tools import derive, errors, schemas
from smalls.tools.parts import Line, Parts
from smalls.tools.registry import ToolContext
from smalls.tools.windows import iso, resolve

FACET = "act"
_MENTION = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]*)?>")
_CHANNEL = re.compile(r"<#[A-Z0-9]+\|([^>]+)>")


def run(args: Any, ctx: ToolContext) -> Parts:
    window = resolve(args.since, args.until, ctx.now)
    tagged = ctx.tags and FACET in schemas.offered_facets()
    act = getattr(args, FACET, None) if tagged else None
    channels = derive.channels(ctx)
    if args.channel is not None and args.channel.lstrip("#") not in channels:
        raise errors.Refusal(errors.unknown_value("channel", args.channel, channels))
    names = derive.chat_names(ctx)
    where, params = ["kind = 'message'"], []
    if args.channel is not None:
        where.append("dataset = ?")
        params.append(args.channel.lstrip("#"))
    messages = derive.query(ctx, "SELECT rid, handle, dataset, native_id, t_known, body, parent, attrs, tags FROM visible "
                                 f"WHERE {' AND '.join(where)} ORDER BY t_known, rid", params)
    threads = derive.threads(messages)
    in_window = [m for m in messages if window.since <= m["t_known"] < window.until]
    if args.thread_ts is not None:
        root = next((m for m in messages if m["native_id"] == args.thread_ts and m["parent"] is None), None)
        matched = [root, *threads.get(root["handle"], [])] if root else []
        hint = "thread_ts is the ts printed on a message row: check it, or drop thread_ts."
    else:
        matched = [m for m in in_window if m["parent"] is None]
        hint = "Widen the window" + (", or drop channel=." if args.channel else ".")
    if args.text:
        needle = args.text.lower()
        pool = matched if args.thread_ts is not None else in_window         # a matching reply is a row of its own
        matched = [m for m in pool if needle in (m["body"] or "").lower()]
        hint = "Drop text=, or widen the window."
    rows = [m for m in matched if _has_tag(m, act)] if act else matched
    footer = []
    if act:
        untagged = sum(1 for m in matched if not derive.tags_of(m).get(FACET))
        footer.append(f"[tag filter {FACET}={act}: {len(rows)} of the {len(matched)} messages that match without it. {untagged} of those "
                      f"{len(matched)} carry no {FACET} tag, and a missing tag says nothing about a message]")
        hint = "Drop the tag filter, or widen the window."
    expanded = args.thread_ts is not None
    native = " ".join(x for x in (f"in:#{args.channel.lstrip('#')}" if args.channel else "", json.dumps(args.text) if args.text else "",
                                  f"thread_ts:{args.thread_ts}" if expanded else f"after:{iso(window.since)} before:{iso(window.until)}") if x)
    return Parts(rows=[_line(m, names, threads, tagged, expanded) for m in rows], footer=footer, window=window, now=ctx.store.now,
                 what="messages", keep="newest", native_query=native, native_label="chat search",
                 narrow="Name a channel, add text=, or shorten the window.", empty_hint=hint, tag=f"{FACET}={act}" if act else None)


def _has_tag(message: dict[str, Any], value: str) -> bool:
    values = derive.tags_of(message).get(FACET, [])
    return not values if value == schemas.UNTAGGED else value in values


def resolve_mentions(text: str, names: dict[str, str]) -> str:
    """`<@U123>` becomes `@display name`, from the users file; an id it does not hold is left as written."""
    text = _MENTION.sub(lambda m: f"@{names[m.group(1)]}" if m.group(1) in names else m.group(0), text or "")
    return _CHANNEL.sub(lambda m: f"#{m.group(1)}", text)


def _line(m: dict[str, Any], names: dict[str, str], threads: dict[str, list[dict[str, Any]]], tagged: bool, expanded: bool) -> Line:
    attrs = derive.loads(m["attrs"])
    author = names.get(attrs.get("user")) or attrs.get("username") or attrs.get("user") or "(unknown)"
    body = resolve_mentions(m["body"], names).replace("\n", "\n    ")
    text = f"{m['handle']}  {iso(m['t_known'])}  ts {m['native_id']}  #{m['dataset']}  {author}: {body}"
    replies = threads.get(m["handle"], [])
    refs: tuple[str, ...] = ()
    if replies:
        summary = derive.thread_summary(replies, names)
        text += f"  [replies {summary['count']} by {', '.join(summary['repliers'])}; latest {iso(summary['latest'])}]"
        if not expanded:
            refs = tuple(r["handle"] for r in replies)
            text += f"  [thread_ts={m['native_id']} shows them: {' '.join(refs)}]"
    elif m["parent"]:
        text += f"  [a reply in thread {attrs.get('thread_ts')}]"
    if tagged:
        text += derive.tag_label(derive.tags_of(m), (FACET,))
    return Line(m["handle"], text, m["t_known"], refs=refs)
