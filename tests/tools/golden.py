"""The golden test harness for the tools. A tool test asks a tool a question at a checkpoint and checks the
answer against a value computed here, from the served bytes and from the manifest's handle, path, locator
and time columns. Nothing here selects by the manifest's labels: `parity.manifest()` drops them on load.

The checkpoints, the scanner and the future tokens are the ones `tests/test_leak.py` already loads.
"""

from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache

import parity
import test_leak
from smalls.contracts.envelope import Envelope
from smalls.tools import registry, store
from smalls.tools.registry import ToolContext

UTC = timezone.utc
CHECKPOINTS: dict[str, str] = dict(test_leak.CHECKPOINTS)      # checkpoint id -> clock time on the bundle's day
FIRST, SECOND = list(CHECKPOINTS)[0], list(CHECKPOINTS)[-1]
DAY = test_leak.DAY


@lru_cache(maxsize=None)
def built(at: str, tags: bool) -> store.Store:
    """One store per clock and tag setting, so two contexts never share a clock."""
    s = store.build(parity.BUNDLE, tags=tags)
    s.set_now(at)
    return s


def ctx(checkpoint: str = SECOND, tags: bool = False) -> ToolContext:
    s = built(CHECKPOINTS.get(checkpoint, checkpoint), tags)
    return ToolContext(store=s, now=datetime.fromtimestamp(s.now / 1000, UTC), tags=tags)


def run(name: str, context: ToolContext, **args) -> Envelope:
    """Call a tool the way every caller does."""
    return registry.call(registry.surface(tags=context.tags)[name], args, context)


def ms(clock: str) -> int:
    """A clock time on the bundle's day, as epoch ms."""
    return parity.ms(f"{DAY}T{clock}Z" if len(clock) > 5 else f"{DAY}T{clock}:00Z")


@lru_cache(maxsize=None)
def manifest() -> tuple[dict, ...]:
    return tuple(parity.manifest())


def visible(now: int, prefix: str = "") -> list[dict]:
    """The manifest rows under a served path prefix that are visible at `now`."""
    return [r for r in manifest() if r["path"].startswith(prefix) and parity.visible_at(r, now)]


def served(rel: str) -> str:
    """A served file's text."""
    return (parity.BUNDLE / rel).read_text(encoding="utf-8")


def handles(env: Envelope) -> list[str]:
    return [row.handle for row in env.rows]
