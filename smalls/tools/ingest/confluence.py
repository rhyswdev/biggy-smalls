"""Confluence pages exported as markdown, one file per page, named by page key. A page is known from its
"Last updated" date (a date only, read as 12:00 UTC). Each page also becomes one chunk per heading for the
full-text table, the section heading prefixed with the page title so a hit says where it came from.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import PurePosixPath
from typing import Iterator

from smalls.contracts import handles as H
from smalls.tools.ingest.base import UTC, Item, Record, clean, ms_of

_UPDATED = re.compile(r"Last updated (\d{1,2} \w{3} \d{4})(?: by ([^·\n]+))?")
_META = re.compile(r"(Space|Owner): ([^·\n]+)")
_HEADING = re.compile(r"^(#{1,6}) +(.*\S)\s*$")


def chunks_by_heading(text: str, title: str | None) -> list[tuple[str | None, str]]:
    out: list[tuple[str | None, list[str]]] = []
    for line in text.split("\n"):
        m = _HEADING.match(line)
        if m:
            heading = m.group(2) if len(m.group(1)) == 1 or not title else f"{title} > {m.group(2)}"
            out.append((heading, []))
        elif out:
            out[-1][1].append(line)
        elif line.strip():
            out.append((title, [line]))
    return [(heading, "\n".join(lines).strip()) for heading, lines in out]


def adapter(bundle, source: str) -> Iterator[Item]:
    tz_rule = bundle.integration.tz_rule(source)
    for rel in bundle.files(source, "*.md"):
        text = bundle.text(rel)
        first = _HEADING.match(text.split("\n", 1)[0])
        title = first.group(2) if first else PurePosixPath(rel).stem
        updated = _UPDATED.search(text)
        t = ms_of(datetime.strptime(updated.group(1), "%d %b %Y").replace(hour=12, tzinfo=UTC)) if updated else 0
        meta = {k.lower(): v.strip() for k, v in _META.findall(text.split("\n\n", 2)[1] if "\n\n" in text else "")}
        yield Record(
            handle=H.conf_handle_for_path(rel), source=source, dataset=PurePosixPath(rel).parent.name, kind="doc", path=rel,
            locator=1, native=text, t_event=t, t_known=t, native_id=PurePosixPath(rel).stem, t_native=updated.group(1) if updated else None,
            tz_rule=tz_rule, title=title, body=text,
            attrs=clean({**meta, "last_updated": updated.group(1) if updated else None,
                         "updated_by": updated.group(2).strip() if updated and updated.group(2) else None}),
            flags={} if updated else {"undated": True}, chunks=chunks_by_heading(text, title))
