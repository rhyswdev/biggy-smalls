"""One adapter per source. An adapter reads its source's served files through the bundle's path guard and
yields records, metric points and reference files in the vendor's own shape, normalised only as far as the
store's columns go. Adding a source is one function and one line here.

A source with a record-break rule is a raw log: the log loader picks a parser by the shape of the file.
A vendor's export is named by its `sources:` key. A provider status page is known by its files, since its
key is the provider's own name.
"""

from __future__ import annotations

from typing import Callable, Iterator

from smalls.tools.ingest import confluence, datadog, dynatrace, jenkins, pagerduty, servicenow, slack, solarwinds, splunk, statuspage
from smalls.tools.ingest.base import Item
from smalls.tools.ingest.logs import raw_logs

Adapter = Callable[[object, str], Iterator[Item]]

ADAPTERS: dict[str, Adapter] = {
    "splunk/alerts": splunk.alerts,
    "splunk/saved_searches.json": splunk.saved_searches,
    "datadog": datadog.adapter,
    "dynatrace": dynatrace.adapter,
    "servicenow": servicenow.adapter,
    "pagerduty": pagerduty.adapter,
    "jenkins": jenkins.adapter,
    "slack": slack.adapter,
    "confluence": confluence.adapter,
    "solarwinds": solarwinds.adapter,
}


def adapter_for(bundle, source: str) -> Adapter | None:
    if "break_only_before" in bundle.integration.sources[source]:
        return raw_logs
    if source in ADAPTERS:
        return ADAPTERS[source]
    if bundle.files(source, statuspage.SNAPSHOTS):
        return statuspage.adapter
    return None
