"""Time windows: how `since` and `until` become a pair of instants (PLAN section 3.4; K1 in
docs/decisions/tools-agent-build.md).

Accepted forms: ISO-8601 with `Z` or an offset (a bare ISO time is UTC); a UTC clock time, `HH:MM` or
`HH:MM:SS`; `-<n><s|m|h|d>`, relative to `now`; and the word `now`. A clock time in `since` is the latest one at
or before `now`; in `until`, the first one after `since`. `until` defaults to `now`. Every window is clamped to
`now`, and the result says when that happened. `since` at or after `until` is an argument error.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

UTC = timezone.utc
_RELATIVE = re.compile(r"^-(\d+)([smhd])$")
_CLOCK = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?$")
_UNIT_MS = {"s": 1000, "m": 60_000, "h": 3_600_000, "d": 86_400_000}
FORMS = "an ISO-8601 time ending in Z or an offset, a UTC clock time such as 09:30, a relative time such as -30m or -2h, or the word now"


class WindowError(ValueError):
    """A window the arguments cannot make. `field` names the argument at fault."""

    def __init__(self, field: str, message: str):
        super().__init__(f"{field}: {message}")
        self.field = field


def ms_of(when: datetime) -> int:
    return int(when.timestamp() * 1000)


def iso(ms: int) -> str:
    """Epoch milliseconds as UTC, to the second, or to the millisecond when it has any."""
    when = datetime.fromtimestamp(ms // 1000, UTC)
    return when.strftime("%Y-%m-%dT%H:%M:%S") + (f".{ms % 1000:03d}" if ms % 1000 else "") + "Z"


def clock(ms: int) -> str:
    """HH:MM:SS UTC, for a row whose date is plain from the window."""
    return datetime.fromtimestamp(ms // 1000, UTC).strftime("%H:%M:%S")


def instant(field: str, text: str, now_ms: int, after: int | None = None) -> int:
    """One end of a window as epoch ms. A clock time with no `after` is the latest at or before `now`; with
    `after`, it is the first at or after that instant."""
    text = (text or "").strip()
    if text.lower() == "now":
        return now_ms
    relative = _RELATIVE.match(text)
    if relative:
        return now_ms - int(relative[1]) * _UNIT_MS[relative[2]]
    clock_time = _CLOCK.match(text)
    if clock_time:
        base = now_ms if after is None else after
        day = datetime.fromtimestamp(base // 1000, UTC).replace(
            hour=int(clock_time[1]), minute=int(clock_time[2]), second=int(clock_time[3] or 0), microsecond=0)
        ms = ms_of(day)
        if after is None:
            return ms if ms <= now_ms else ms - _UNIT_MS["d"]
        return ms if ms >= after else ms + _UNIT_MS["d"]
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise WindowError(field, f"{text!r} is not a time. Give {FORMS}") from None
    return ms_of(when if when.tzinfo else when.replace(tzinfo=UTC))


@dataclass(frozen=True)
class Window:
    since: int          # epoch ms, inclusive
    until: int          # epoch ms, exclusive, never after now
    clamped: bool

    @property
    def iso(self) -> tuple[str, str]:
        return iso(self.since), iso(self.until)

    def spans_dates(self) -> bool:
        return iso(self.since)[:10] != iso(self.until - 1)[:10]


def resolve(since: str, until: str | None, now: datetime | int) -> Window:
    now_ms = now if isinstance(now, int) else ms_of(now)
    start = instant("since", since, now_ms)
    end = instant("until", until, now_ms, after=start) if until else now_ms
    clamped = end > now_ms
    end = min(end, now_ms)
    if start >= end:
        what = "now" if clamped or not until else "until"
        raise WindowError("since", f"the window is empty: since ({iso(start)}) is at or after {what} ({iso(end)})")
    return Window(start, end, clamped)
