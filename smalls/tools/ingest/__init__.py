"""One adapter per source. An adapter reads its source's served files through the bundle's path guard and
yields records, metric points and reference files in the vendor's own shape, normalised only as far as the
store's columns go. Adding a source is one function and one line here.

Raw logs (`splunk/raw/*`) share one loader and pick a parser by the shape of the file; every other source
is named by its `sources:` key.
"""

from __future__ import annotations

from typing import Callable, Iterator

from smalls.tools.ingest.base import Item
from smalls.tools.ingest.logs import raw_logs

Adapter = Callable[[object, str], Iterator[Item]]

ADAPTERS: dict[str, Adapter] = {}


def adapter_for(source: str, cfg: dict) -> Adapter | None:
    """A source with a record-break rule is a raw log; anything else needs an adapter under its own key."""
    if "break_only_before" in cfg:
        return raw_logs
    return ADAPTERS.get(source)
