"""SolarWinds active alerts (SWIS rows). Times are the Orion server's wall clock with no marker and a
seven-digit fraction; the zone comes from integration.yaml.

The row is exported with what happened after it triggered: when it reset and whether anyone acknowledged
it. Those fields are withheld. The reset time is kept in `t_end`, so the tool that lists alerts can say
whether the alert is still active at `now`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterator

from smalls.contracts import handles as H
from smalls.tools.ingest.base import Item, Record, clean, elements

# Orion alert severities, onto syslog numbers.
_SEVERITY = {0: ("Informational", 6), 1: ("Warning", 4), 2: ("Critical", 2), 3: ("Serious", 3), 4: ("Notice", 5)}
WITHHELD = ("ResetDateTime", "Acknowledged", "AcknowledgedBy", "AcknowledgedDateTime")


def adapter(bundle, source: str) -> Iterator[Item]:
    integ = bundle.integration

    def local(value: str | None) -> tuple[int | None, bool]:
        if not value:
            return None, False
        return integ.local(source, datetime.strptime(value[:26], "%Y-%m-%dT%H:%M:%S.%f"))

    for rel in bundle.files(source, "*.json"):
        for pointer, raw, a in elements(bundle.text(rel), "/results"):
            t, ambiguous = local(a["TriggeredDateTime"])
            sev_text, sev = _SEVERITY.get(a.get("Severity"), (None, None))
            flags: dict = {"withheld": list(WITHHELD)}
            if ambiguous:
                flags["dst_fold"] = True
            yield Record(
                handle=H.solarwinds_handle(a["AlertActiveID"]), source=source, dataset="alerts", kind="alert", path=rel, locator=pointer,
                native=raw, t_event=t, t_known=t, t_end=local(a.get("ResetDateTime"))[0], native_id=str(a["AlertActiveID"]),
                entity_raw=a.get("RelatedNodeCaption") or a.get("EntityCaption"), stream_key=str(a.get("AlertObjectID")),
                t_native=a["TriggeredDateTime"], tz_rule=integ.tz_rule(source), sev=sev, sev_text=sev_text, title=a.get("AlertName"),
                body=a.get("TriggeredMessage"),
                attrs=clean({k: v for k, v in a.items() if k not in WITHHELD and k not in ("AlertName", "TriggeredMessage")}),
                flags=flags)
