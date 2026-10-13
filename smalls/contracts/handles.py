"""Citation handles: the grammar and the functions that mint them.

A handle is the stable, human-readable id a tool prints at the start of every row and the agent cites.
Handles are computed from what is served (paths, raw text, native ids), never from the generator's
private state, so the tool layer can mint the same handle the generator wrote into the manifest.

One function per prefix below; the generator and the tool layer both mint handles only through them.

Grammar (docs/PLAN.md section 3.2):

    snow:INC0048811            ServiceNow record by number (incident, change_request)
    snow:ci/<name>             CMDB CI by name
    snow:rel/<h8>              CMDB relationship
    snow:user/<user_name>      sys_user
    snow:group/<h8>            sys_user_group
    snow:jn/<h8>               sys_journal_field entry
    snow:audit/<h8>            sys_audit row
    dt:P-26101350              Dynatrace problem by display id
    dt:ent/<entityId>          Dynatrace entity
    pd:8812                    PagerDuty incident by number
    pd:log/<h8>                PagerDuty log entry
    pd:svc/<id>  pd:ep/<id>  pd:oncall/<h8>
    dd:ev7820011093            Datadog event
    dd:mon7391240              Datadog monitor
    dd:syn/<public_id>         Datadog synthetic test
    metric:<query>             Datadog metric series (the manifest's identity: no window)
    metric:<query>@HHMM-HHMM   the same series over a window a tool queried, clamped to now (ruling A3)
    jk:payments-gateway-deploy#412          Jenkins build
    jk:payments-gateway-deploy#412/<sha8>   commit inside a build's changeSet
    sw:55012                   SolarWinds active alert
    conf:PGW-DB-CONN-TIMEOUT   Confluence page by page key (the served file is named <key>.md)
    psp:paycore@v2             status-page snapshot
    spl:alert/<h8>             Splunk alert webhook body
    spl:search/<slug>          Splunk saved search
    log:pay-02/3fa91c0e        raw log record: sha1(path + raw record)[:8], "~2" suffix on duplicates
    k8s:<h8>                   Kubernetes event
    slack:<channel>/<h8>       Slack message: sha1(channel + ts + text)[:8]
    q:7c1d02aa                 aggregate computed by a tool: sha1(normalised query + as_of)[:8]
    ic:note/<h8>               incident commander's note or gate-rejection reason, minted at runtime:
                               sha1(run_id + submitted-at + text)[:8]; classed with chat, never in the manifest
"""

from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath

HANDLE_RE = re.compile(
    r"""^(
      snow:(INC|CHG)\d{7}
    | snow:(ci|rel|user|group|jn|audit)/[A-Za-z0-9._@-]+
    | dt:P-\d{8}
    | dt:ent/[A-Z_]+-[0-9A-F]{16}
    | pd:\d+
    | pd:(log|svc|ep|oncall)/[A-Za-z0-9]+
    | dd:ev\d+
    | dd:mon\d+
    | dd:syn/[a-z0-9-]+
    | metric:[^@\s]+(@\d{4}-\d{4})?
    | jk:[a-z0-9-]+\#\d+(/[0-9a-f]{8})?
    | sw:\d+
    | conf:[A-Za-z0-9-]+
    | psp:[a-z0-9-]+@v\d+
    | spl:(alert|search)/[a-z0-9-]+
    | log:[A-Za-z0-9._-]+/[0-9a-f]{8}(~\d+)?
    | k8s:[0-9a-f]{8}
    | slack:[a-z0-9_-]+/[0-9a-f]{8}
    | q:[0-9a-f]{8}
    | ic:note/[0-9a-f]{8}
    )$""",
    re.VERBOSE,
)


def h8(*parts: str) -> str:
    """First eight hex chars of sha1 over the parts joined by newlines."""
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()[:8]


def is_handle(s: str) -> bool:
    return bool(HANDLE_RE.match(s))


def handle_class(handle: str) -> str:
    """What kind of evidence a handle stands for. This is the one definition, shared by the agent's board
    checks and the scorer.

    - `chat`: something a person said in a channel, or an incident commander's note. It can steer an
      investigation but cannot on its own support a causal link.
    - `aggregate`: a count a tool computed for one call. It is minted per run and has no time of its own.
    - `metric`: a metric series, with or without a window.
    - `record`: everything else, one served record.
    """
    if handle.startswith(("slack:", "ic:note/")):
        return "chat"
    if handle.startswith("q:"):
        return "aggregate"
    if handle.startswith("metric:"):
        return "metric"
    return "record"


# -- one function per prefix ------------------------------------------------------------------------


def snow_handle(number: str) -> str:
    """Incident or change_request by number."""
    return f"snow:{number}"


def ci_handle(name: str) -> str:
    return f"snow:ci/{re.sub(r'[^A-Za-z0-9._@-]+', '-', name).strip('-')}"


def user_handle(user_name: str) -> str:
    return f"snow:user/{user_name}"


def dt_problem_handle(display_id: str) -> str:
    return f"dt:{display_id}"


def dt_entity_handle(entity_id: str) -> str:
    return f"dt:ent/{entity_id}"


def pd_incident_handle(number: int | str) -> str:
    return f"pd:{number}"


def pd_service_handle(service_id: str) -> str:
    return f"pd:svc/{service_id}"


def pd_policy_handle(policy_id: str) -> str:
    return f"pd:ep/{policy_id}"


def dd_event_handle(event_id: int | str) -> str:
    return f"dd:ev{event_id}"


def dd_monitor_handle(monitor_id: int | str) -> str:
    return f"dd:mon{monitor_id}"


def dd_synthetic_handle(public_id: str) -> str:
    return f"dd:syn/{public_id}"


def metric_handle(query: str, w0: str | None = None, w1: str | None = None) -> str:
    """A Datadog metric series, optionally over a window. `query` is the query string as served (spaces are
    dropped); `w0`/`w1` are UTC HHMM. The manifest stores the series identity with no window; a tool mints
    the window it queried, clamped to now, so a handle never reveals how far the data runs (ruling A3)."""
    base = f"metric:{query.replace(' ', '')}"
    if (w0 is None) != (w1 is None):
        raise ValueError("a metric window needs both ends")
    return base if w0 is None else f"{base}@{w0}-{w1}"


def parse_metric_handle(handle: str) -> tuple[str, str | None, str | None]:
    """(query without spaces, w0, w1); the window parts are None for a series identity."""
    m = re.fullmatch(r"metric:([^@\s]+)(?:@(\d{4})-(\d{4}))?", handle)
    if not m:
        raise ValueError(f"not a metric handle: {handle}")
    return m.group(1), m.group(2), m.group(3)


def jenkins_build_handle(job: str, number: int | str) -> str:
    return f"jk:{job}#{number}"


def jenkins_commit_handle(job: str, number: int | str, commit_id: str) -> str:
    return f"jk:{job}#{number}/{commit_id[:8]}"


def solarwinds_handle(alert_active_id: int | str) -> str:
    return f"sw:{alert_active_id}"


def conf_handle(page_key: str) -> str:
    return f"conf:{page_key}"


def conf_handle_for_path(rel_path: str) -> str:
    """A served Confluence page is named by its page key: confluence/runbooks/<KEY>.md."""
    return conf_handle(PurePosixPath(rel_path).stem)


def psp_handle(provider: str, version: int | str) -> str:
    return f"psp:{provider}@v{version}"


def splunk_search_handle(name: str) -> str:
    return f"spl:search/{slugify(name)}"


def log_handle(rel_path: str, raw_record: str, occurrence: int = 1) -> str:
    """Handle for a raw log record. `rel_path` is relative to the bundle root; the host part is the
    file stem (pay-02, checkout-api-6d8c9b7f4d-x2k1p, pay-db-01, edge-access). `raw_record` is the full
    record text, including continuation lines of a multi-line record, joined with newlines.
    `occurrence` is 1 for the first identical record in the file, 2 for the next, and so on."""
    host = PurePosixPath(rel_path).stem
    base = f"log:{host}/{h8(rel_path, raw_record)}"
    return base if occurrence == 1 else f"{base}~{occurrence}"


def slack_handle(channel: str, ts: str, text: str) -> str:
    return f"slack:{channel}/{h8(channel, ts, text)}"


def k8s_handle(rel_path: str, raw_line: str) -> str:
    return f"k8s:{h8(rel_path, raw_line)}"


def journal_handle(sys_id: str) -> str:
    return f"snow:jn/{h8('sys_journal_field', sys_id)}"


def audit_handle(sys_id: str) -> str:
    return f"snow:audit/{h8('sys_audit', sys_id)}"


def rel_handle(sys_id: str) -> str:
    return f"snow:rel/{h8('cmdb_rel_ci', sys_id)}"


def group_handle(sys_id: str) -> str:
    return f"snow:group/{h8('sys_user_group', sys_id)}"


def pd_log_handle(log_entry_id: str) -> str:
    return f"pd:log/{log_entry_id}"


def pd_oncall_handle(policy_id: str, level: int, user_id: str) -> str:
    return f"pd:oncall/{h8(policy_id, str(level), user_id)}"


def splunk_alert_handle(rel_path: str, body: str) -> str:
    return f"spl:alert/{h8(rel_path, body)}"


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def query_handle(normalised_query: str, as_of: str) -> str:
    return f"q:{h8(normalised_query, as_of)}"


def ic_note_handle(run_id: str, submitted_at: str, text: str) -> str:
    """An incident commander's note, or the reason given for a gate rejection. Minted by the runner when the
    note arrives, so it never appears in the manifest; it is classed with chat and cannot alone support a
    causal link (docs/PLAN.md sections 3.2 and 5)."""
    return f"ic:note/{h8(run_id, submitted_at, text)}"
