"""Step 1b acceptance: one minimal hand-written record per extracted schema validates."""
import json

from smalls_data import lint_schema as ls


def ok(r):
    assert not r.errors, r.errors
    return r


def test_datadog_event_with_monitor_id_warns_only():
    ev = {"id": 7820011093, "id_str": "7820011093", "monitor_id": 7391240, "alert_type": "error",
          "source_type_name": "Monitor Alert", "title": "[Triggered] p90 latency high on payments-gw",
          "text": "%%%\n@pagerduty-Payments-Prod\n%%%", "date_happened": 1791901930, "priority": "normal",
          "tags": ["env:prod"], "url": "https://app.datadoghq.com/event/event?id=7820011093"}
    r = ok(ls.validate_json("datadog", "Event", ev, "t"))
    assert any("monitor_id" in w for w in r.warnings)


def test_datadog_metrics_monitor_synthetic():
    q = {"status": "ok", "res_type": "time_series", "query": "sum:postgresql.connections{host:pay-db-01} by {db}",
         "from_date": 1791899100000, "to_date": 1791905400000, "group_by": ["db"], "message": "", "error": "",
         "series": [{"metric": "postgresql.connections", "display_name": "postgresql.connections", "scope": "db:payments,host:pay-db-01",
                     "expression": "sum:postgresql.connections{db:payments,host:pay-db-01}", "aggr": "sum", "interval": 60,
                     "length": 2, "start": 1791899100000, "end": 1791899160000, "tag_set": ["db:payments"], "query_index": 0,
                     "pointlist": [[1791899100000, 45.0], [1791899160000, None]]}]}
    ok(ls.validate_json("datadog", "MetricsQueryResponse", q, "t"))
    m = {"id": 7391240, "name": "p90 latency high on payments-gw", "type": "query alert", "overall_state": "OK",
         "query": "max(last_5m):p90:trace.http.request.duration{service:checkout-api,peer.service:payments-gw,env:prod} > 6",
         "message": "%%%\n@pagerduty-Payments-Prod\n%%%", "tags": ["team:payments"], "options": {"thresholds": {"critical": 6.0}},
         "created": "2025-11-02T10:00:00+00:00", "modified": "2026-03-10T10:00:00+00:00", "multi": False, "priority": 2}
    ok(ls.validate_json("datadog", "Monitor", m, "t"))
    s = {"public_id": "abc-def-ghi", "name": "checkout flow", "type": "browser", "status": "live", "message": "",
         "locations": ["aws:eu-west-1"], "tags": [], "monitor_id": 7390118,
         "config": {"assertions": [], "request": {"method": "GET", "url": "https://www.larkspur.com/checkout"}},
         "options": {"tick_every": 300}}
    ok(ls.validate_json("datadog", "SyntheticsTestDetails", s, "t"))


def test_pagerduty_minimal():
    ref = lambda t, i, s: {"id": i, "type": t, "summary": s}
    inc = {"id": "Q1W2E3R4T5Y6U7", "type": "incident", "summary": "[#8812] p90 latency high on payments-gw",
           "incident_number": 8812, "title": "p90 latency high on payments-gw", "created_at": "2026-10-13T14:32:25Z",
           "status": "triggered", "urgency": "high", "incident_key": "k",
           "service": ref("service_reference", "PSVC001", "Payments - Prod"),
           "escalation_policy": ref("escalation_policy_reference", "PEP0001", "Payments Engineering"),
           "assignments": [{"at": "2026-10-13T14:32:25Z", "assignee": ref("user_reference", "P1QXA4K", "Tom Beckett")}],
           "acknowledgements": [], "teams": [ref("team_reference", "PTEAM01", "Payments Engineering")], "priority": ref("priority", "PPRIO02", "P2"),
           "alert_counts": {"all": 1, "triggered": 1, "resolved": 0}, "html_url": "https://larkspur.pagerduty.com/incidents/Q1W2E3R4T5Y6U7"}
    ok(ls.validate_json("pagerduty", "Incident", inc, "t"))
    le = {"id": "R1", "type": "trigger_log_entry", "summary": "Triggered through the API", "created_at": "2026-10-13T14:32:25Z",
          "channel": {"type": "api"}, "agent": ref("service_reference", "PSVC001", "Payments - Prod"),
          "incident": ref("incident_reference", "Q1W2E3R4T5Y6U7", "x"), "service": ref("service_reference", "PSVC001", "Payments - Prod")}
    ok(ls.validate_json("pagerduty", "LogEntry", le, "t"))
    svc = {"id": "PSVC001", "type": "service", "summary": "Payments - Prod", "name": "Payments - Prod", "status": "active",
           "escalation_policy": ref("escalation_policy_reference", "PEP0001", "Payments Engineering")}
    ok(ls.validate_json("pagerduty", "Service", svc, "t"))
    ep = {"id": "PEP0001", "type": "escalation_policy", "summary": "Payments Engineering", "name": "Payments Engineering",
          "num_loops": 0, "escalation_rules": [{"escalation_delay_in_minutes": 10,
                                               "targets": [{"id": "P1QXA4K", "type": "user_reference"}]}]}
    ok(ls.validate_json("pagerduty", "EscalationPolicy", ep, "t"))
    oc = {"escalation_policy": ref("escalation_policy_reference", "PEP0001", "Payments Engineering"),
          "user": ref("user_reference", "P1QXA4K", "Tom Beckett"), "schedule": ref("schedule_reference", "PSCH001", "Payments Primary"), "escalation_level": 1,
          "start": "2026-10-12T13:00:00Z", "end": "2026-10-19T13:00:00Z"}
    ok(ls.validate_json("pagerduty", "Oncall", oc, "t"))


def test_slack_k8s_minimal():
    msg = {"type": "message", "user": "U01B0NXMOC4", "text": "hi", "ts": "1791902160.000300", "team": "T0LARKSPUR",
           "thread_ts": "1791902160.000300", "reply_count": 1, "reply_users": ["U01GG60ZVQK"], "reply_users_count": 1,
           "latest_reply": "1791902200.000100", "reactions": [{"name": "+1", "count": 1, "users": ["U03MU4G0DPE"]}]}
    ok(ls.validate_json("slack", "objs_message", msg, "t"))
    ev = {"apiVersion": "v1", "kind": "Event", "metadata": {"name": "checkout-api.17f3a", "namespace": "commerce"},
          "involvedObject": {"kind": "Deployment", "name": "checkout-api", "namespace": "commerce"},
          "reason": "ScalingReplicaSet", "message": "Scaled up replica set", "type": "Normal",
          "firstTimestamp": "2026-10-13T14:28:05Z", "lastTimestamp": "2026-10-13T14:28:05Z", "count": 1,
          "source": {"component": "deployment-controller"}}
    ok(ls.validate_json("kubernetes", "Event", ev, "t"))


def test_field_list_sources():
    ok(ls.dynatrace_problem({"problemId": "x", "displayId": "P-26101350", "title": "Response time degradation",
                             "impactLevel": "SERVICES", "severityLevel": "PERFORMANCE", "status": "OPEN",
                             "startTime": 1791901740000, "endTime": -1,
                             "evidenceDetails": {"totalCount": 1, "details": [{"evidenceType": "EVENT", "displayName": "x",
                                                 "entity": {}, "rootCauseRelevant": True, "startTime": 1, "eventId": "e", "eventType": "SLOWDOWN"}]}}, "t"))
    ok(ls.jenkins_build({"_class": "hudson.model.FreeStyleBuild", "number": 412, "result": "SUCCESS", "building": False,
                         "timestamp": 1791901188000, "duration": 312000, "estimatedDuration": 300000, "url": "u",
                         "displayName": "#412", "fullDisplayName": "payments-gateway-deploy #412", "actions": [],
                         "changeSet": {"_class": "hudson.plugins.git.GitChangeSetList", "kind": "git",
                                       "items": [{"commitId": "a", "msg": "m", "comment": "m\n", "author": {"fullName": "Dana Whitfield"},
                                                  "authorEmail": "d@x", "date": "2026-10-08 15:12:01 -0400", "timestamp": 1,
                                                  "affectedPaths": ["p"], "paths": [], "id": "a"}]}}, "t"))
    sample = json.load(open("schemas/servicenow/sample-incident-get.json"))["result"]
    sample = sample[0] if isinstance(sample, list) else sample
    ok(ls.servicenow_record("incident", {k: (v if isinstance(v, str) else "x") for k, v in sample.items()}, "t"))
    ok(ls.statuspage_summary(json.load(open("schemas/statuspage/sample-summary.json")), "t"))
    ok(ls.splunk_webhook({"result": {"_raw": "line", "_time": "1791901867.221", "host": "pay-02", "source": "/var/log/x",
                                     "sourcetype": "payments:app"}, "sid": "s", "results_link": "l", "search_name": "n",
                          "owner": "o", "app": "search"}, "t"))
    ok(ls.solarwinds_alert({"AlertActiveID": 55012, "AlertName": "x", "EntityCaption": "c", "EntityType": "Orion.APM.Component",
                            "Severity": 2, "TriggeredDateTime": "2026-10-13T10:40:45.1230000"}, "t"))


def test_exact_strings_loaded():
    s = ls.exact_strings()
    assert s["pg.fatal_53300"] == "remaining connection slots are reserved for non-replication superuser connections"
    assert s["hikari.after_cleanup"] == "After cleanup  stats ("
