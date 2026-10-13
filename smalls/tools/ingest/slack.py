"""A Slack export: `users.json` and `channels.json`, and one file of messages per channel and day.

Messages are records, known from their `ts`. A thread reply points at its parent message. The export holds
what was true when it was taken, so the fields that change after a message is posted are withheld: the
thread summary on a parent (`reply_count`, `reply_users`, `reply_users_count`, `latest_reply`) and its
`reactions`, which carry no time at all. The tool that shows a thread counts the visible replies itself.
`users.json` and `channels.json` have no records of their own and are kept as reference files, the
channels without their `members`.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Iterator

from smalls.contracts import handles as H
from smalls.tools.ingest.base import Item, Record, Ref, clean, elements

WITHHELD = ("reply_count", "reply_users", "reply_users_count", "latest_reply", "reactions")
REFERENCE = {"users.json": (), "channels.json": ("[].members",)}


def _ms(ts: str) -> int:
    """`ts` is epoch seconds and a six-digit sequence; the sequence orders messages, the seconds date them."""
    seconds, _, fraction = ts.partition(".")
    return int(seconds) * 1000 + int((fraction + "000")[:3])


def adapter(bundle, source: str) -> Iterator[Item]:
    tz_rule = bundle.integration.tz_rule(source)
    for rel in bundle.files(source, "*.json"):
        text = bundle.text(rel)
        path = PurePosixPath(rel)
        if len(path.parts) == 2:
            if path.name in REFERENCE:
                withheld = REFERENCE[path.name]
                yield Ref(source=source, name=path.stem, native=text, flags={"withheld": list(withheld)} if withheld else {})
            continue
        channel = path.parts[-2]
        messages = list(elements(text, "/"))
        roots = {m["ts"]: H.slack_handle(channel, m["ts"], m.get("text", "")) for _, _, m in messages}
        for pointer, raw, m in messages:
            t = _ms(m["ts"])
            thread = m.get("thread_ts")
            yield Record(
                handle=roots[m["ts"]], source=source, dataset=channel, kind="message", path=rel, locator=pointer, native=raw,
                t_event=t, t_known=t, native_id=m["ts"], t_native=m["ts"], tz_rule=tz_rule, body=m.get("text"),
                parent=roots.get(thread) if thread and thread != m["ts"] else None,
                attrs=clean({"channel": channel, "user": m.get("user"), "username": m.get("username"), "subtype": m.get("subtype"),
                             "bot_id": m.get("bot_id"), "ts": m["ts"], "thread_ts": thread}),
                flags={"withheld": [f for f in WITHHELD if f in m]} if any(f in m for f in WITHHELD) else {})
