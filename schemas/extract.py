"""Extract the component schemas and field lists the schema-conformance lint needs.

Run after `bash schemas/fetch.sh`: `uv run python schemas/extract.py`. Writes small, committed files under
`schemas/<vendor>/extracted/`:

- `<Name>.schema.json`: a self-contained JSON Schema for one vendor object, cut from an official OpenAPI or
  Swagger document. The needed components are copied under their original pointer paths so internal
  `$ref`s still resolve; OpenAPI 3.0 `nullable` is rewritten to a JSON Schema type union and an
  undiscriminated `oneOf` to `anyOf`.
- `<name>.fields.json`: a field list taken from vendor source code (Dynatrace, Jenkins), exact message
  strings (HikariCP, PostgreSQL), a real sample (Statuspage, ServiceNow incident), or a transcription
  where no machine-readable authority is reachable (other ServiceNow tables, SolarWinds, nginx, F5, Logback).
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent


def write(rel: str, obj: dict) -> None:
    p = ROOT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=1, sort_keys=False) + "\n")
    print(f"wrote schemas/{rel}")


# ---------------------------------------------------------------- OpenAPI / Swagger cutting


def _refs(node, out: set[str]) -> None:
    if isinstance(node, dict):
        r = node.get("$ref")
        if isinstance(r, str) and r.startswith("#/"):
            out.add(r)
        for v in node.values():
            _refs(v, out)
    elif isinstance(node, list):
        for v in node:
            _refs(v, out)


def _fix_openapi30(node):
    """OpenAPI 3.0 dialect to JSON Schema: nullable -> type union; boolean exclusive bounds dropped;
    `example`/`readOnly` are harmless annotations and stay."""
    if isinstance(node, dict):
        node = {k: _fix_openapi30(v) for k, v in node.items()}
        if node.pop("nullable", False):
            if "type" in node and isinstance(node["type"], str):
                node["type"] = [node["type"], "null"]
                if "enum" in node:
                    node["enum"] = list(node["enum"]) + [None]
            elif "$ref" in node or "allOf" in node or "oneOf" in node or "anyOf" in node:
                inner = {k: node.pop(k) for k in list(node) if k in ("$ref", "allOf", "oneOf", "anyOf")}
                node["anyOf"] = [inner, {"type": "null"}]
        # The vendor specs use oneOf without a discriminator where alternatives overlap (PagerDuty channel
        # types), which no real payload can satisfy strictly; validate those as anyOf.
        if "oneOf" in node and "discriminator" not in node:
            node["anyOf"] = node.pop("oneOf")
        for b in ("exclusiveMinimum", "exclusiveMaximum"):
            if isinstance(node.get(b), bool):
                node.pop(b)
        return node
    if isinstance(node, list):
        return [_fix_openapi30(v) for v in node]
    return node


def cut(doc: dict, container: list[str], name: str, openapi30: bool) -> dict:
    """Copy `name` and every component it transitively references from doc[container...] into a new
    document with the same container path, rooted at a $ref to `name`."""
    def get(path: list[str]):
        node = doc
        for p in path:
            node = node[p]
        return node

    comps = get(container)
    prefix = "#/" + "/".join(container) + "/"
    needed: set[str] = set()
    todo = [name]
    while todo:
        n = todo.pop()
        if n in needed:
            continue
        needed.add(n)
        found: set[str] = set()
        _refs(comps[n], found)
        for r in found:
            if not r.startswith(prefix):
                raise ValueError(f"unexpected ref {r}")
            todo.append(r[len(prefix):].split("/")[0])
    out_comps = {n: copy.deepcopy(comps[n]) for n in sorted(needed)}
    if openapi30:
        out_comps = _fix_openapi30(out_comps)
    root: dict = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$ref": prefix + name}
    node = root
    for p in container[:-1]:
        node = node.setdefault(p, {})
    node[container[-1]] = out_comps
    return root


def datadog() -> None:
    doc = yaml.safe_load((ROOT / "datadog/openapi-v1.yaml").read_text())
    for n in ["Event", "MetricsQueryResponse", "Monitor", "SyntheticsAPITest", "SyntheticsTestDetails"]:
        s = cut(doc, ["components", "schemas"], n, openapi30=True)
        s["x-source"] = "datadog-api-client-python .generator/schemas/v1/openapi.yaml"
        write(f"datadog/extracted/{n}.schema.json", s)


def pagerduty() -> None:
    doc = json.loads((ROOT / "pagerduty/openapi-v3.json").read_text())
    for n in ["Incident", "LogEntry", "AssignLogEntry", "EscalateLogEntry", "NotifyLogEntry", "Service", "EscalationPolicy", "Oncall"]:
        s = cut(doc, ["components", "schemas"], n, openapi30=True)
        s["x-source"] = "PagerDuty/api-schema reference/REST/openapiv3.json"
        write(f"pagerduty/extracted/{n}.schema.json", s)


def slack() -> None:
    doc = json.loads((ROOT / "slack/web-api-openapi-v2.json").read_text())
    s = cut(doc, ["definitions"], "objs_message", openapi30=False)
    s["x-source"] = "slackapi/slack-api-specs web-api/slack_web_openapi_v2.json definitions.objs_message"
    write("slack/extracted/objs_message.schema.json", s)
    s = cut(doc, ["definitions"], "objs_user", openapi30=False)
    s["x-source"] = "slackapi/slack-api-specs web-api/slack_web_openapi_v2.json definitions.objs_user"
    write("slack/extracted/objs_user.schema.json", s)
    s = cut(doc, ["definitions"], "objs_conversation", openapi30=False)
    s["x-source"] = "slackapi/slack-api-specs web-api/slack_web_openapi_v2.json definitions.objs_conversation"
    write("slack/extracted/objs_conversation.schema.json", s)


def kubernetes() -> None:
    doc = json.loads((ROOT / "kubernetes/swagger.json").read_text())
    s = cut(doc, ["definitions"], "io.k8s.api.core.v1.Event", openapi30=False)
    s["x-source"] = "kubernetes/kubernetes api/openapi-spec/swagger.json io.k8s.api.core.v1.Event"
    write("kubernetes/extracted/Event.schema.json", s)


# ---------------------------------------------------------------- field lists from source code


def dynatrace() -> None:
    src = (ROOT / "dynatrace/problems.py").read_text()

    def keys_of(cls: str) -> list[str]:
        m = re.search(rf"class {cls}\(.*?\):(.*?)(?=\nclass |\Z)", src, re.S)
        body = m.group(1) if m else ""
        return sorted(set(re.findall(r'raw_element\.get\("([A-Za-z]+)"', body)) |
                      set(re.findall(r'self\._raw_element\.get\("([A-Za-z]+)"', body)) |
                      set(re.findall(r'raw_element\["([A-Za-z]+)"\]', body)))

    classes = re.findall(r"^class (\w+)\(", src, re.M)
    out = {"x-source": "dynatrace-oss/api-client-python dynatrace/environment_v2/problems.py", "classes": {}}
    for c in classes:
        k = keys_of(c)
        if k:
            out["classes"][c] = k
    write("dynatrace/extracted/problems.fields.json", out)


def jenkins() -> None:
    run = (ROOT / "jenkins/Run.java").read_text()
    gcs = (ROOT / "jenkins/GitChangeSet.java").read_text()

    def exported(src: str) -> list[str]:
        names = []
        for m in re.finditer(r"@Exported([^\n]*)\n(?:\s*@[^\n]*\n)*\s*public[^(]*?\s(\w+)\s*\(", src):
            named = re.search(r'name\s*=\s*"(\w+)"', m.group(1))
            if named:
                names.append(named.group(1))
                continue
            g = m.group(2)
            if g.startswith("get"):
                g = g[3].lower() + g[4:]
            elif g.startswith("is"):
                g = g[2].lower() + g[3:]
            names.append(g)
        return sorted(set(names))

    out = {
        "x-source": "jenkinsci/jenkins core/.../hudson/model/Run.java and jenkinsci/git-plugin GitChangeSet.java (@Exported getters)",
        "Run": exported(run),
        "GitChangeSet": exported(gcs),
        # Fields the JSON API adds outside Run.java: the class marker, Actionable.getActions, parameters and causes inside actions,
        # the changeSet wrapper (ChangeLogSet.getItems/getKind), and AbstractBuild's builtOn.
        "added_by_api": ["_class", "actions", "builtOn", "changeSet.items", "changeSet.kind", "actions[].parameters", "actions[].causes",
                         "inProgress", "keepLog", "queueId", "previousBuild"],
    }
    write("jenkins/extracted/build.fields.json", out)


def hikaricp() -> None:
    pool = (ROOT / "hikaricp/HikariPool.java").read_text()
    leak = (ROOT / "hikaricp/ProxyLeakTask.java").read_text()
    strings = {}
    checks = [
        ("timeout", '" - Connection is not available, request timed out after "', pool),
        ("timeout_tail", '"(total=" + getTotalConnections() + ", active=" + getActiveConnections() + ", idle=" + getIdleConnections() + ", waiting=" + getThreadsAwaitingConnection() + ")"', pool),
        ("stats", '"{} - {}stats (total={}, active={}, idle={}, waiting={})"', pool),
        ("before_cleanup", '"Before cleanup "', pool),
        ("after_cleanup", '"After cleanup  "', pool),
        ("timeout_failure", '"Timeout failure "', pool),
        ("cannot_acquire", '"{} - Cannot acquire connection from data source"', pool),
        ("added_connection", '"{} - Added connection {}"', pool),
        ("fill_skipped", '"{} - Fill pool skipped, pool has sufficient level or currently being filled."', pool),
        ("leak", '"Connection leak detection triggered for {} on thread {}, stack trace follows"', leak),
        ("leak_exception", '"Apparent connection leak detected"', leak),
        ("unleaked", '"Previously reported leaked connection {} on thread {} was returned to the pool (unleaked)"', leak),
    ]
    for label, lit, src in checks:
        if lit not in src:
            raise SystemExit(f"HikariCP 5.1.0 string not found: {label}")
        strings[label] = lit.strip('"')
    # What the templates render, assembled from the verified pieces above.
    strings["rendered"] = {
        "timeout": "{pool} - Connection is not available, request timed out after {ms}ms (total={t}, active={a}, idle={i}, waiting={w})",
        "stats": "{pool} - {prefix}stats (total={t}, active={a}, idle={i}, waiting={w})",
        "after_cleanup": "{pool} - After cleanup  stats (total={t}, active={a}, idle={i}, waiting={w})",
        "before_cleanup": "{pool} - Before cleanup stats (total={t}, active={a}, idle={i}, waiting={w})",
        "timeout_failure": "{pool} - Timeout failure stats (total={t}, active={a}, idle={i}, waiting={w})",
        "cannot_acquire": "{pool} - Cannot acquire connection from data source",
        "added_connection": "{pool} - Added connection {conn}",
        "leak": "Connection leak detection triggered for {conn} on thread {thread}, stack trace follows",
        "leak_exception": "java.lang.Exception: Apparent connection leak detected",
        "unleaked": "Previously reported leaked connection {conn} on thread {thread} was returned to the pool (unleaked)",
    }
    write("hikaricp/extracted/messages.fields.json", {"x-source": "brettwooldridge/HikariCP tag HikariCP-5.1.0", "strings": strings})


def postgres() -> None:
    src = (ROOT / "postgres/postinit.c").read_text()
    msg = "remaining connection slots are reserved for non-replication superuser connections"
    if msg not in src:
        raise SystemExit("PostgreSQL 15 53300 message not found")
    write("postgres/extracted/messages.fields.json", {
        "x-source": "postgres/postgres REL_15_STABLE src/backend/utils/init/postinit.c",
        "strings": {"fatal_53300": msg},
        # Messages from other PG15 files we render, transcribed from the same release (not fetched).
        "transcribed": {
            "connection_received": "connection received: host={host} port={port}",
            "connection_authorized": "connection authorized: user={user} database={db} application_name={app}",
            "terminating_admin": "terminating connection due to administrator command",
            "eof_open_txn": "unexpected EOF on client connection with an open transaction",
            "checkpoint_starting": "checkpoint starting: time",
            "checkpoint_complete": "checkpoint complete: wrote {n} buffers ({pct}%); 0 WAL file(s) added, 0 removed, {r} recycled; write={w} s, sync={s} s, total={t} s; sync files={f}, longest={l} s, average={a} s; distance={d} kB, estimate={e} kB",
            "autovacuum_analyze": "automatic analyze of table \"{table}\"",
        },
    })


def splunk() -> None:
    src = (ROOT / "splunk/results.py").read_text()
    env = [k for k in ["preview", "init_offset", "messages", "fields", "results"] if k in src]
    write("splunk/extracted/webhook.fields.json", {
        "x-source": "splunk-sdk-python splunklib/results.py (results envelope); Splunk webhook alert action docs (body, transcribed)",
        "results_envelope": env,
        "webhook_body": ["result", "sid", "results_link", "search_name", "owner", "app"],
        "event_row_fields": ["_raw", "_time", "host", "source", "sourcetype", "index", "linecount", "splunk_server", "_bkt", "_cd", "_indextime", "_serial", "_si", "_sourcetype", "punct"],
        "saved_search_fields": ["name", "search", "description", "cron_schedule", "dispatch.earliest_time", "dispatch.latest_time",
                                "alert_type", "alert_comparator", "alert_threshold", "alert.suppress", "alert.suppress.period",
                                "actions", "action.webhook.param.url", "is_scheduled", "disabled", "owner", "app",
                                # savedsearches.conf alert_condition (alert_type = custom); `updated` from the REST entry.
                                "alert_condition", "updated"],
    })


def statuspage() -> None:
    sample = json.loads((ROOT / "statuspage/sample-summary.json").read_text())
    comp = sample["components"][0] if sample.get("components") else {}
    write("statuspage/extracted/summary.fields.json", {
        "x-source": "statuspage/sample-summary.json (real public summary.json captured 2026-10-12)",
        "top": sorted(sample.keys()),
        "page": sorted(sample["page"].keys()),
        "status": sorted(sample["status"].keys()),
        "component": sorted(comp.keys()),
        # Incident and incident_update fields from the same Statuspage v2 API family (transcribed; no
        # incident was open on the captured page).
        "incident": ["id", "name", "status", "created_at", "updated_at", "monitoring_at", "resolved_at", "impact", "shortlink",
                     "started_at", "page_id", "incident_updates", "components"],
        "incident_update": ["id", "status", "body", "incident_id", "created_at", "updated_at", "display_at",
                            "affected_components", "deliver_notifications", "custom_tweet", "tweet_id"],
    })


SN_COMMON = ["sys_id", "sys_created_on", "sys_created_by", "sys_updated_on", "sys_updated_by", "sys_mod_count", "sys_tags",
             "sys_class_name", "sys_domain", "sys_domain_path"]


def servicenow() -> None:
    inc = json.loads((ROOT / "servicenow/sample-incident-get.json").read_text())
    rec = inc["result"]
    if isinstance(rec, list):
        rec = rec[0]
    write("servicenow/extracted/incident.fields.json", {
        "x-source": "servicenow/sample-incident-get.json (Table API reference example, 10 Sep 2026)",
        "mode": "sample", "fields": sorted(rec.keys())})
    tables = {
        "change_request": ["number", "short_description", "description", "type", "state", "phase", "risk", "impact", "priority",
                           "category", "cmdb_ci", "business_service", "assignment_group", "assigned_to", "requested_by",
                           "opened_by", "opened_at", "start_date", "end_date", "work_start", "work_end", "approval",
                           "cab_required", "cab_date", "cab_recommendation", "justification", "implementation_plan",
                           "backout_plan", "test_plan", "risk_impact_analysis", "close_code", "close_notes", "closed_at",
                           "closed_by", "conflict_status", "on_hold", "reason", "unauthorized", "production_system",
                           "outside_maintenance_schedule", "active", "upon_approval", "upon_reject", "chg_model",
                           "std_change_producer_version", "review_status", "comments", "work_notes", "watch_list",
                           "company", "location", "urgency", "escalation", "contact_type", "due_date", "approval_set",
                           "made_sla", "sla_due", "order", "parent", "u_jenkins_job"] + SN_COMMON,
        "cmdb_ci": ["name", "sys_class_name", "operational_status", "install_status", "owned_by", "managed_by",
                    "support_group", "assignment_group", "environment", "ip_address", "fqdn", "dns_domain",
                    "short_description", "category", "subcategory", "used_for", "busines_criticality",
                    "company", "location", "department", "version", "discovery_source", "first_discovered",
                    "last_discovered", "correlation_id", "asset_tag", "serial_number", "manufacturer", "model_id",
                    "comments", "attributes", "u_external_ids"] + [f for f in SN_COMMON if f != "sys_class_name"],
        "cmdb_rel_ci": ["parent", "child", "type", "connection_strength", "percent_outage", "port"] + [f for f in SN_COMMON if f != "sys_class_name"],
        "sys_journal_field": ["element", "element_id", "name", "value", "sys_created_on", "sys_created_by", "sys_id"],
        "sys_user": ["user_name", "name", "first_name", "last_name", "email", "title", "department", "active",
                     "locked_out", "time_zone", "manager", "phone", "mobile_phone", "location", "company",
                     "employee_number", "vip", "last_login_time", "source", "notification"] + [f for f in SN_COMMON if f != "sys_class_name"],
        "sys_user_group": ["name", "description", "manager", "email", "active", "type", "parent", "cost_center",
                           "default_assignee", "exclude_manager", "include_members"] + [f for f in SN_COMMON if f != "sys_class_name"],
        "sys_audit": ["tablename", "documentkey", "fieldname", "oldvalue", "newvalue", "user", "reason",
                      "record_checkpoint", "internal_checkpoint", "sys_created_on", "sys_created_by", "sys_id"],
    }
    for t, fields in tables.items():
        write(f"servicenow/extracted/{t}.fields.json", {
            "x-source": "transcribed from the ServiceNow data dictionary for the table (no PDI export; see docs/schemas.md)",
            "mode": "field-list", "fields": sorted(set(fields))})


def transcribed() -> None:
    write("solarwinds/extracted/alerts.fields.json", {
        "x-source": "transcribed SWIS Orion.AlertActive / AlertObjects / AlertConfigurations (docs not reachable here)",
        "envelope": ["results"],
        "fields": ["AlertActiveID", "AlertObjectID", "AlertName", "EntityCaption", "EntityType", "EntityDetailsUrl",
                   "RelatedNodeCaption", "Severity", "TriggeredDateTime", "TriggeredMessage", "ResetDateTime",
                   "Acknowledged", "AcknowledgedBy", "AcknowledgedDateTime", "Notes"]})
    write("nginx/extracted/access_log.fields.json", {
        "x-source": "transcribed nginx log_format (combined plus upstream timing variables)",
        "log_format": '$remote_addr - $remote_user [$time_local] "$request" $status $body_bytes_sent "$http_referer" '
                      '"$http_user_agent" rt=$request_time uct="$upstream_connect_time" uht="$upstream_header_time" '
                      'urt="$upstream_response_time" upstream="$upstream_addr"'})
    write("f5/extracted/ltm.fields.json", {
        "x-source": "transcribed BIG-IP ltm log message reference",
        "strings": {
            "member_down": "01070638:5: Pool {pool} member {member} monitor status down. [ {monitor}: down; last error: {err} ]  [ was up for {up} ]",
            "member_up": "01070727:5: Pool {pool} member {member} monitor status up. [ {monitor}: up ]  [ was down for {down} ]",
            "no_members": "01010028:3: No members available for pool {pool}",
            "members_available": "01010221:3: Pool {pool} now has available members",
        }})
    write("logback/extracted/pattern.fields.json", {
        "x-source": "transcribed Logback PatternLayout; custom logback.xml declared for the gateway",
        "pattern": "%d{yyyy-MM-dd HH:mm:ss,SSS} %-5level [%thread] %logger{25} - %msg%n%ex{12}",
        "levels": ["TRACE", "DEBUG", "INFO", "WARN", "ERROR"]})
    write("logstash/extracted/checkout_api.fields.json", {
        "x-source": "transcribed logstash-logback-encoder LogstashEncoder default fields plus MDC and shipper fields",
        "fields": ["@timestamp", "@version", "message", "logger_name", "thread_name", "level", "level_value",
                   "stack_trace", "traceId", "spanId", "orderId", "cartId", "service", "version", "kubernetes"],
        "kubernetes": ["namespace", "pod_name", "container_name", "node_name", "labels"]})


def main() -> None:
    datadog()
    pagerduty()
    slack()
    kubernetes()
    dynatrace()
    jenkins()
    hikaricp()
    postgres()
    splunk()
    statuspage()
    servicenow()
    transcribed()


if __name__ == "__main__":
    main()
