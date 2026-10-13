"""The log template masker (ruling D6): a few regexes that turn a log line into its template by replacing
the parts that vary with `<*>`. Deterministic, no model, no corpus statistics: the same line gives the same
template in any bundle.

Rules, in order:
1. An HTTP request with its status keeps method, path and status (`POST /api/checkout/submit 504`): the
   status is the one number that says what kind of line this is. The query string goes, and so does any
   path segment that holds a digit.
2. Any other double-quoted value becomes `"<*>"` (user agents, referers, image names).
3. Any token that holds a digit becomes `<*>`: timestamps, ids, counts, durations, addresses, versions.
4. A run of masks separated only by spaces collapses to one.
"""

from __future__ import annotations

import re

from smalls.contracts.handles import h8

MASK = "<*>"

_HTTP = re.compile(r'"?\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) (/[^\s?"]*)(?:\?[^\s"]*)?(?: HTTP/[\d.]+)?"? ([1-5]\d\d)\b')
_QUOTED = re.compile(r'"[^"\n]*"')
_TOKEN = re.compile(r"[^\s=:,;()\[\]{}\"'<>|/@]+")
_RUN = re.compile(r"<\*>(?: +<\*>)+")
_KEPT = re.compile(r"\x00(\d+)\x00")


def _has_digit(s: str) -> bool:
    return any(c.isdigit() for c in s)


def mask(line: str) -> str:
    kept: list[str] = []

    def keep(m: re.Match) -> str:
        path = "/".join(MASK if _has_digit(seg) else seg for seg in m.group(2).split("/"))
        kept.append(f"{m.group(1)} {path} {m.group(3)}")
        return f"\x00{len(kept) - 1}\x00"

    s = _HTTP.sub(keep, line)
    s = _QUOTED.sub(f'"{MASK}"', s)
    s = _TOKEN.sub(lambda m: m.group(0) if m.group(0).startswith("\x00") or not _has_digit(m.group(0)) else MASK, s)
    s = _RUN.sub(MASK, s)
    return _KEPT.sub(lambda m: kept[int(m.group(1))], s)


def template_id(sourcetype: str, template: str) -> str:
    return h8(sourcetype, template)
