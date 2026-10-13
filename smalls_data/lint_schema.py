"""Schema conformance (docs/PLAN.md invariant 11 and 12).

Every generated JSON record is validated against the extracted schema or field list in
`schemas/<vendor>/extracted/`. Unknown fields fail, except the deliberate deviations recorded in
`docs/schemas.md`, which warn. Log templates that claim a vendor message are checked against the
pinned strings (invariant 12) by `exact_strings`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator

SCHEMAS = Path(__file__).resolve().parent.parent / "schemas"

# Deliberate deviations (docs/schemas.md): fields we keep although the authority does not list them.
DEVIATIONS: dict[str, set[str]] = {
    # Real Datadog monitor events carry these; the v1 OpenAPI Event schema does not.
    "datadog/Event": {"monitor_id", "monitor_groups", "is_aggregate", "children"},
}


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checked: int = 0

    def extend(self, other: "Report") -> None:
        self.errors += other.errors
        self.warnings += other.warnings
        self.checked += other.checked


@lru_cache(maxsize=None)
def _schema(vendor: str, name: str) -> dict:
    return json.loads((SCHEMAS / vendor / "extracted" / f"{name}.schema.json").read_text())


@lru_cache(maxsize=None)
def _fields(vendor: str, name: str) -> dict:
    return json.loads((SCHEMAS / vendor / "extracted" / f"{name}.fields.json").read_text())


def _resolve(root: dict, node: dict) -> dict:
    while isinstance(node, dict) and "$ref" in node:
        ptr = node["$ref"][2:].split("/")
        n = root
        for p in ptr:
            n = n[int(p)] if isinstance(n, list) else n[p]
        node = n
    return node


def _props(root: dict, node: dict) -> tuple[dict, bool]:
    """Merged properties of a schema node (following $ref and allOf) and whether it is closed to
    unknown keys for our purposes (it lists properties)."""
    node = _resolve(root, node)
    props: dict = {}
    if not isinstance(node, dict):
        return props, False
    for sub in node.get("allOf", []):
        p, _ = _props(root, sub)
        props.update(p)
    props.update(node.get("properties", {}))
    return props, bool(props)


def _unknown(root: dict, node: dict, inst, path: str, out: list[str], depth: int = 0) -> None:
    if depth > 6:
        return
    node = _resolve(root, node)
    if isinstance(inst, dict):
        for alt in ("anyOf", "oneOf"):
            if alt in node:
                # pick the first alternative that lists properties
                for sub in node[alt]:
                    p, closed = _props(root, sub)
                    if closed:
                        node = _resolve(root, sub)
                        break
        props, closed = _props(root, node)
        addl = node.get("additionalProperties") if isinstance(node, dict) else None
        if closed and addl in (None, False):
            for k, v in inst.items():
                if k not in props:
                    out.append(f"{path}/{k}")
                else:
                    _unknown(root, props[k], v, f"{path}/{k}", out, depth + 1)
    elif isinstance(inst, list) and isinstance(node, dict) and "items" in node:
        for i, v in enumerate(inst[:5]):
            _unknown(root, node["items"], v, f"{path}/{i}", out, depth + 1)


def validate_json(vendor: str, name: str, inst, where: str) -> Report:
    r = Report(checked=1)
    root = _schema(vendor, name)
    for e in Draft202012Validator(root).iter_errors(inst):
        loc = "/".join(str(p) for p in e.absolute_path)
        r.errors.append(f"{where}: {vendor}/{name} /{loc}: {e.message[:200]}")
    unknown: list[str] = []
    _unknown(root, root, inst, "", unknown)
    allowed = DEVIATIONS.get(f"{vendor}/{name}", set())
    for u in unknown:
        top = u.split("/")[1]
        if top in allowed:
            r.warnings.append(f"{where}: {vendor}/{name} deviation kept on purpose: {u}")
        else:
            r.errors.append(f"{where}: {vendor}/{name} unknown field {u}")
    return r


def check_fields(allowed: list[str] | set[str], rec: dict, where: str, required: list[str] | set[str] = ()) -> Report:
    r = Report(checked=1)
    allowed = set(allowed)
    for k in rec:
        if k not in allowed:
            r.errors.append(f"{where}: unknown field {k}")
    for k in required:
        if k not in rec:
            r.errors.append(f"{where}: missing field {k}")
    return r


def servicenow_record(table: str, rec: dict, where: str) -> Report:
    """ServiceNow Table API with sysparm_display_value=true: every field of the table present, every
    value a string (reference fields as display strings), empty fields as ""."""
    spec = _fields("servicenow", table)
    fields = set(spec["fields"])
    r = check_fields(fields, rec, where, required=fields)
    for k, v in rec.items():
        if not isinstance(v, str):
            r.errors.append(f"{where}: {table}.{k} is {type(v).__name__}, ServiceNow values are strings")
    return r


def dynatrace_problem(p: dict, where: str) -> Report:
    spec = _fields("dynatrace", "problems")["classes"]
    r = check_fields(spec["Problem"], p, where)
    ev_fields = set(spec["Evidence"]) | set(spec.get("EventEvidence", [])) | set(spec.get("MetricEvidence", [])) \
        | set(spec.get("TransactionalEvidence", [])) | set(spec.get("AvailabilityEvidence", [])) | set(spec.get("MaintenanceWindowEvidence", []))
    for i, ev in enumerate(p.get("evidenceDetails", {}).get("details", [])):
        r.extend(check_fields(ev_fields, ev, f"{where} evidence[{i}]"))
    ed = p.get("evidenceDetails")
    if ed is not None:
        r.extend(check_fields(spec["EvidenceDetails"], ed, f"{where} evidenceDetails"))
        r.checked -= 1
    return r


def jenkins_build(b: dict, where: str) -> Report:
    spec = _fields("jenkins", "build")
    allowed = set(spec["Run"]) | {a for a in spec["added_by_api"] if "." not in a and "[" not in a} | {"changeSet"}
    r = check_fields(allowed, b, where)
    cs = b.get("changeSet", {})
    r.extend(check_fields({"_class", "items", "kind"}, cs, f"{where} changeSet"))
    r.checked -= 1
    item_fields = set(spec["GitChangeSet"]) | {"commitId", "timestamp", "affectedPaths", "_class"}
    for i, it in enumerate(cs.get("items", [])):
        r.extend(check_fields(item_fields, it, f"{where} changeSet.items[{i}]"))
        r.checked -= 1
    return r


def statuspage_summary(doc: dict, where: str) -> Report:
    spec = _fields("statuspage", "summary")
    r = check_fields(spec["top"], doc, where, required=spec["top"])
    r.extend(check_fields(spec["page"], doc["page"], f"{where} page", required=spec["page"]))
    r.extend(check_fields(spec["status"], doc["status"], f"{where} status", required=spec["status"]))
    for i, c in enumerate(doc.get("components", [])):
        r.extend(check_fields(spec["component"], c, f"{where} components[{i}]"))
    for i, inc in enumerate(doc.get("incidents", [])):
        r.extend(check_fields(spec["incident"], inc, f"{where} incidents[{i}]"))
        for j, u in enumerate(inc.get("incident_updates", [])):
            r.extend(check_fields(spec["incident_update"], u, f"{where} incidents[{i}].incident_updates[{j}]"))
    r.checked = 1
    return r


def splunk_webhook(body: dict, where: str) -> Report:
    spec = _fields("splunk", "webhook")
    r = check_fields(spec["webhook_body"], body, where, required=spec["webhook_body"])
    row = body.get("result", {})
    r.extend(check_fields(spec["event_row_fields"], row, f"{where} result", required=["_raw", "_time", "host", "source", "sourcetype"]))
    if "count" in row and "_raw" in row:
        r.errors.append(f"{where}: result row has both count and _raw")
    r.checked = 1
    return r


def splunk_saved_search(s: dict, where: str) -> Report:
    return check_fields(_fields("splunk", "webhook")["saved_search_fields"], s, where, required=["name", "search", "description"])


def solarwinds_alert(a: dict, where: str) -> Report:
    return check_fields(_fields("solarwinds", "alerts")["fields"], a, where,
                        required=["AlertActiveID", "AlertName", "EntityCaption", "EntityType", "Severity", "TriggeredDateTime"])


def checkout_log(rec: dict, where: str) -> Report:
    spec = _fields("logstash", "checkout_api")
    r = check_fields(spec["fields"], rec, where, required=["@timestamp", "level", "logger_name", "message", "service", "version"])
    if "kubernetes" in rec:
        r.extend(check_fields(spec["kubernetes"], rec["kubernetes"], f"{where} kubernetes"))
        r.checked -= 1
    return r


def exact_strings() -> dict[str, str]:
    """Pinned vendor strings the log templates must render verbatim (invariant 12)."""
    h = _fields("hikaricp", "messages")["strings"]
    pg = _fields("postgres", "messages")["strings"]
    return {
        "hikari.timeout": h["timeout"].strip(),
        "hikari.after_cleanup": "After cleanup  stats (",
        "hikari.before_cleanup": "Before cleanup stats (",
        "hikari.cannot_acquire": h["cannot_acquire"].replace("{} - ", ""),
        "hikari.leak": h["leak"].split("{}")[0].strip(),
        "hikari.leak_exception": h["leak_exception"],
        "hikari.unleaked": h["unleaked"].split("{}")[0].strip(),
        "pg.fatal_53300": pg["fatal_53300"],
    }
