#!/usr/bin/env bash
# Fetch the official specs and sources that the schema-conformance lint validates against.
# Every URL below was verified reachable from the cloud environment on 2026-10-12
# (raw.githubusercontent.com and docs.datadoghq.com are allowed; most vendor doc sites are not).
# Run from the repo root: bash schemas/fetch.sh
set -euo pipefail
cd "$(dirname "$0")"
get() { mkdir -p "$(dirname "$2")"; curl -sS --fail --max-time 120 -o "$2" "$1"; echo "ok  $2"; }

# Datadog: official OpenAPI v1 (Event, EventListResponse, MetricsQueryResponse, MetricsQueryMetadata, Monitor, MonitorOverallStates, EventAlertType)
get https://raw.githubusercontent.com/DataDog/datadog-api-client-python/master/.generator/schemas/v1/openapi.yaml datadog/openapi-v1.yaml
# PagerDuty: official OpenAPI v3 (Incident, LogEntry, Service, EscalationPolicy, Oncall, Alert)
get https://raw.githubusercontent.com/PagerDuty/api-schema/main/reference/REST/openapiv3.json pagerduty/openapi-v3.json
# Dynatrace: official open-source API client; Problem model mirrors the Problems API v2 object
get https://raw.githubusercontent.com/dynatrace-oss/api-client-python/main/dynatrace/environment_v2/problems.py dynatrace/problems.py
# Slack: official Web API spec; definitions.objs_message is the message object used by exports
get https://raw.githubusercontent.com/slackapi/slack-api-specs/master/web-api/slack_web_openapi_v2.json slack/web-api-openapi-v2.json
# Kubernetes: Event object (io.k8s.api.core.v1.Event)
get https://raw.githubusercontent.com/kubernetes/kubernetes/master/api/openapi-spec/swagger.json kubernetes/swagger.json
# Jenkins: exported fields on Run (number, result, building, timestamp, duration, estimatedDuration, url, displayName, fullDisplayName)
get https://raw.githubusercontent.com/jenkinsci/jenkins/master/core/src/main/java/hudson/model/Run.java jenkins/Run.java
# Jenkins git plugin: changeSet item fields (commitId, msg, comment, author, authorEmail, date, timestamp, affectedPaths)
get https://raw.githubusercontent.com/jenkinsci/git-plugin/master/src/main/java/hudson/plugins/git/GitChangeSet.java jenkins/GitChangeSet.java
# HikariCP 5.1.0 (pinned): exact log strings
get https://raw.githubusercontent.com/brettwooldridge/HikariCP/HikariCP-5.1.0/src/main/java/com/zaxxer/hikari/pool/HikariPool.java hikaricp/HikariPool.java
get https://raw.githubusercontent.com/brettwooldridge/HikariCP/HikariCP-5.1.0/src/main/java/com/zaxxer/hikari/pool/ProxyLeakTask.java hikaricp/ProxyLeakTask.java
# PostgreSQL 15 (pinned): exact FATAL message text
get https://raw.githubusercontent.com/postgres/postgres/REL_15_STABLE/src/backend/utils/init/postinit.c postgres/postinit.c
# Splunk SDK: results reader shows the results JSON envelope (preview, init_offset, messages, fields, results)
get https://raw.githubusercontent.com/splunk/splunk-sdk-python/master/splunklib/results.py splunk/results.py
echo "done. See docs/schemas.md for what each file is the authority for."
