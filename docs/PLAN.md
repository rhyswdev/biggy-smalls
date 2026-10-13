# Smalls: execution plan for the dataset and tool layer

This document is self-contained. A fresh session should be able to execute it without the conversation that produced it. Read sections 0 to 3 in full before touching code. Sections 4 onward are reference while building.

Companion material in this repo:

- `docs/requirements.md`: transcript of the assessment document, the thing we are judged against, and our working priorities.
- `docs/HANDOFF.md`: where the build stands and what comes next.
- `docs/DATA.md`: every judgment call made while generating the data, with its reason.
- `docs/schemas.md`: the authority for every source's shape (official OpenAPI specs, vendor source code, a real Statuspage sample), what each confirms, and the deviations we keep on purpose. `schemas/fetch.sh` downloads the machine-readable ones.
- `docs/decisions/step6-decisions.md`: the panel rulings on the tool layer (D1 to D6, R1 to R4, C1 to C4, A1 to A3) and the ordered data fixes that come before any tool. Where anything else disagrees with it, it wins.
- `docs/decisions/ingestion-decisions.md`: the rulings for step 6a: the store contract, compose and tagging. Where anything else disagrees with it on ingestion, it wins.
- `docs/research/tagging.md`: the research behind the tagging pass.
- `docs/research/ingestion-and-retrieval.md`: the research behind section 4, the store, the eval layers and the vector spikes. Its build order and several proposals were superseded by the rulings; this plan carries what was adopted.
- `docs/research/agent-design.md`: the settled agent design, with its rulings in sections 7 and 8.5; `spikes/agent/` is the spike it was tested on.

---

## 0. Context for a fresh session

**What this is.** A 48-hour take-home for an AI Engineer role at BigPanda, an AIOps company. Their AI incident assistant is called Biggy. Ours is a scaled-down one, so the project is called **Smalls**. The assessment asks for an agentic incident investigation system that takes a vague production incident plus messy operational evidence, investigates, keeps evolving state, separates signal from noise, forms hypotheses, recommends actions, and explains itself with evidence from the inputs. Read `docs/requirements.md` once in full.

**What is scored.** Judgment and execution over polish. Four things are bolded in the requirements: the agentic incident investigation system itself, an obsession with UX, good use of AI coding tools, and a genuinely useful output. The Final Note adds that part of the exercise is deciding where AI is useful and where deterministic code is better. "Useful agent behavior, not agent theater." The write-up and a sample output are required deliverables. Our working priorities: the agent first, know exactly what the app does, and give every decision a stated reason.

**Why data comes first, and why it must be small.** The agent needs something real to investigate, and the dataset is the thing a practitioner reviewer will recognise as credible or fake. But the requirements say "a small but realistic dataset with enough ambiguity, noise, and signal is better than a huge artificial one", and the review found v1 was a second project scheduled before the first. This plan builds a thin vertical slice first so the agent loop can start inside step 6, once the tool registry and `search_logs` exist (section 6).

**Decisions already made.** Do not reopen without a reason.

1. One agent, scenarios as data. The agent is scenario-agnostic; incident bundles are folders.
2. Archetype A: a modern enterprise with ServiceNow at the centre, Datadog and Dynatrace for monitoring, Splunk for logs, Jenkins for deploys, PagerDuty, Slack. This is the published stack of BigPanda's customers.
3. Fixtures are raw, one folder per source, in that source's native shape. No shared schema, no shared ids, each source on its own clock. Normalising and correlating is the system's job at runtime.
4. The 3.8.0 deploy happens in the morning (10:19 UTC), not one minute before the trigger.
5. Two checkpoints are core (14:40 and 14:52). 15:15 is a stretch goal.
6. Held-out variants of the same incident, pre-registered in `eval/heldout/` under opaque ids and never read by sessions that touch the agent, replace a second archetype as the generality proof. See section 12 and `eval/heldout/README.md`.
7. Eleven structured tools, native query strings echoed in results, never parsed. Result sizes capped.
8. Contracts (report schema, handle grammar, manifest, answer key shape, replay rules) are written before any data.
9. Machine clocks are converted by code. Only times written by humans are left for the model.
10. A small, fast worker model writes chatter and filler content in an intermediate YAML form. Code renders vendor shapes. Signal content is written by the main session.
11. The store stays in process: stdlib SQLite in memory, rebuilt at every start. We costed a database service under compose and did not adopt it: a shared server reopens the replay clock, a bundle key, staleness and ranking, makes Docker a prerequisite for tests that run offline today, and buys nothing at this size. `compose.yaml` holds the app service only; later services join the same file.
12. Tags come from a closed-set classifier, computed in a separate `make tag` step and read from a committed cache at startup, so the startup build stays deterministic and offline. They are query facets, never evidence: code validates every label, and no generative model touches ingestion. A tag is applied only at high confidence; a doubtful answer stores nothing. They are off by default; `SMALLS_TAGS=on` opts in.

**Decision to confirm at step 0.** Language. Default is Python 3.12 with `uv` (`uv init --python 3.12`, `requires-python = ">=3.12,<3.13"`; the container default is 3.13, so pin it), with both packages `smalls` and `smalls_data` in the build backend. Data-phase dependencies: pydantic, pyyaml, jinja2, jsonschema, pytest. Agent-phase additions later: the Anthropic SDK and `ANTHROPIC_API_KEY`. The store is stdlib `sqlite3` (FTS5, JSON1), so search adds no dependency. An optional `retrieval` dependency group holds numpy for the vector spikes (section 6); their embedding calls use stdlib `urllib` and `VOYAGE_API_KEY`, and without a key search stays on BM25. An optional `tagging` dependency group holds the classifier SDK, which only `smalls tag` imports; `JEV_API_KEY` is needed only for `make tag`. A `Dockerfile` and a `compose.yaml` run the same commands in a container; `uv` on the host stays the primary path. Nothing in this plan depends on the language; the tool layer is the agent's tool layer, so pick what the agent will be written in. The UI can be a separate thin layer over the same tools.

---

## 1. The incident, v2

Larkspur Retail is a fictional retailer. Web tier on Kubernetes (`k8s-prod-east`) with Datadog APM. Payments and orders on VMs (`pay-01` to `pay-04`, `ord-01`, `ord-02`) with Dynatrace OneAgent, including the database host `pay-db-01`. Datadog infrastructure agent everywhere. ServiceNow is the system of record. Logs ship to Splunk. Deploys run through Jenkins with ServiceNow change requests. PagerDuty pages. Slack is the war room. The payment provider is PayCore, an external PSP at `api.paycore.example`.

### 1.1 The true chain

- **Cause.** payments-gateway 3.8.0 ships at 10:19 to 10:25 UTC (06:19 Eastern, a morning release window; Jenkins `payments-gateway-deploy` #412; change CHG0031877, normal, CAB approved, risk Moderate). It adds three retries with backoff around the PayCore authorize call and raises that client timeout from 3 s to 8 s. The code has always held a Postgres transaction open across that call. It ran clean for four hours because PayCore was fast.
- **Trigger.** From 14:24 UTC PayCore's EU edge degrades. Latency rises first but stays under 8 s until 14:26. The slow share of authorizations (attempts over 8 s) is 2 percent from 14:26 to 14:29, 9 percent at 14:30, 17 at 14:31, 22 at 14:32 and 25 from 14:33. Slow attempts take 9 to 12 s. Retries go to the same EU edge, so they are slow too. The status page stays green until 15:10.
- **Mechanism.** A slow call now retries. A connection that used to be held for at most 3 s is held for up to 39 s (8+1+8+2+8+4+8, four attempts). The gateway pools are elastic (min 5, max 35 per VM). They grow, hit 35 on pay-02 at 14:31, and requests start timing out waiting for a connection. pay-db-01 reaches its 197 usable connections at 14:33 and refuses new ones, including the Datadog agent's. Idle-in-transaction sessions pile up.
- **Blast radius.** checkout-api calls the gateway with a 10 s timeout. Any request on the retry path exceeds it, so a trickle of 504s starts at 14:26 at 1 to 2 percent, then climbs from 14:31. Browsing and cart are fine. Customers see a spinner, retry, and add load.
- **Contributing factor 1.** The daily payments-reconciler batch (Jenkins `payments-reconciler` #2311, timer, 14:30, about 45 minutes, user `recon_app`) takes 40 connections. It exists because PayCore drops an intraday settlement file at 14:00. It is why the ceiling is reached at 14:33 rather than later. Pausing it restores headroom and monitoring; it does not fix payments on its own.
- **Contributing factor 2.** The gateway health endpoint pings the database. From 14:36 members fail health checks and the internal F5 VIP (`payments-gw.prod.larkspur.internal`) drains them; a drained member's in-flight transactions finish and its connections go idle (they close only after the 10-minute `idleTimeout`), so its health check passes, it is re-enabled, and it fills again. Members flap until 15:05. SolarWinds notices about four minutes after the first F5 event.
- **Contributing factor 3.** Customer retries from 14:35 push `/api/checkout/submit` traffic up about 70 percent by 14:45. Browse and cart stay flat. The same cart ids resubmit two to five times.
- **Resolution.** At 14:47 the DBA pastes pg_stat_activity: about 133 idle in transaction and 4 active from `payments-gateway`, oldest 38 s, no slow queries. At 14:58 Payments on-call reads the leak-detection traces (every one through the transactional proxy of `PaymentService.authorize`) and connects them to the retry change. Rollback to 3.7.9 at 15:01 to 15:06 (#413, with no change ticket; emergency CHG0031910 raised at 15:09, after the fact). 5xx returns to baseline by 15:08. Because 3.7.9 has a 3 s timeout and no retries, EU authorizations now fail fast: the payment success rate sits near 73 percent until PayCore recovers at 15:24. PayCore posts "Degraded performance, Authorizations (EU)" at 15:10 with `started_at` backdated to 14:24. Customer tickets from 15:05 mention "charged twice" and "card declined".

### 1.2 Fixed parameters

| Item | Value |
|---|---|
| pay-db-01 | PostgreSQL 15, `max_connections=200`, `superuser_reserved_connections=3`, so 197 usable by app roles. `log_connections=on`. |
| payments-gateway | 4 VMs. HikariCP `minimumIdle=5`, `maximumPoolSize=35`, `connectionTimeout=5000`, `idleTimeout=600000`, `leakDetectionThreshold=30000`, `maxLifetime=1800000`. Idle baseline about 24 in total. Pin HikariCP 5.1.0, Spring Boot 3.3, Hibernate 6.5. `com.zaxxer.hikari` is logged at DEBUG, turned on after the March incident; in HikariCP 5.1.0 the housekeeper stats and connection-acquire failures are DEBUG lines, so without this they would not exist. |
| order-service | 2 instances, fixed pool 10, so 20 connections, user `orders_app`. |
| payments-reconciler | 40 connections as `recon_app` from 14:30:00 to about 15:15:30. |
| Monitoring and admin | Datadog agent 1 connection as `datadog` (non-superuser, `pg_monitor`). DBA psql as `postgres` on a reserved slot. |
| Authorization rate | About 14 per second at 14:30, rising with customer retries, capped by the pool. |
| PayCore | EU-routed share 25 percent. Fast calls 400 ms. Slow attempts 9 to 12 s. Retries hit the same edge. |
| Hold time | Before: 0.75 x 0.4 s + 0.25 x 3 s, about 1 s, so 14/s needs about 15 connections. With slow share p: 0.75 x 0.4 + (0.25 - p) x 3 + 39p = 1.05 + 36p seconds, times 14 per second for connections. At 25 percent that is about 10 s and about 140 connections. |
| Ramp | Slow share (attempts over 8 s): 0 before 14:26; 2 percent 14:26 to 14:29; 9 percent at 14:30; 17 at 14:31; 22 at 14:32; 25 from 14:33. Step changes at each minute boundary. By Little's law this gives 25, 60, 100, 126 and 141 connections at 14:29 to 14:33, which section 1.3 caps at the ceiling. |
| checkout-api | 6 pods, Hikari pool 40 (raised after the March incident), client timeout to the gateway 10 s. |
| Checkout traffic by endpoint, baseline | `GET /api/cart` 30 per second, `GET /api/checkout/summary` 20, `POST /api/checkout/address` 10, `POST /api/checkout/submit` 14 (one gateway authorization each), so submits are about 19 percent of checkout-api requests. storefront-web serves about 400 per second of browse traffic. From 14:35 customer retries push submits to about 24 per second by 14:45; the gateway serves what its pool allows. |
| Payment success rate | Baseline 98.5 percent (normal card declines). Falls with exhaustion from 14:31. After the 15:06 rollback about 73 percent (every EU authorization fails fast at 3 s) until PayCore recovers at 15:24, then back to 98.5. |
| Datadog series | Slice: `sum:trace.servlet.request.hits{service:checkout-api,env:prod} by {resource_name}` (traffic by endpoint); `sum:trace.servlet.request.errors{service:checkout-api,env:prod} by {version}` (errors by pod version); `p90:trace.http.request.duration{service:checkout-api,peer.service:payments-gw,env:prod}` and the same at p99 (gateway latency as checkout-api's client spans see it; the source of monitors 7391240 and 7391241); `sum:postgresql.connections{host:pay-db-01} by {db}` (with nulls from 14:33). Full bundle adds: `avg:payments.authorize.success_rate{env:prod}` (custom metric from the gateway VMs' Datadog agent); `sum:trace.servlet.request.hits{service:storefront-web,env:prod}` (browse, flat); `avg:redis.mem.fragmentation_ratio{cluster:redis-sessions}` (hourly, six days, the flap). The Datadog agent on Kubernetes also collects container logs, which the log-volume anomaly monitor 7391301 needs. |

**People.** Every name in every source comes from this table, rendered into `facts.yaml`. Slack ids and PagerDuty ids are generated opaque values.

| Name | Handle | Role and team | Zone (Slack `tz`) | Writes times as | PagerDuty | In Slack | ServiceNow user |
|---|---|---|---|---|---|---|---|
| Alice Nguyen | alice.nguyen | Incident commander; Commerce Platform lead | Europe/London | UTC | Checkout - Prod, level 1 | yes | yes |
| Ben Kowalski | ben.kowalski | checkout-api engineer, Commerce Platform | Europe/London | UTC | none | yes | yes |
| Sam Ortega | sam.ortega | SRE, Platform Reliability | Europe/London | UTC | Platform, level 1 | yes | yes |
| Elena Rossi | elena.rossi | DBA, Database Engineering | Europe/Rome | rarely writes times | Database - Prod, level 1 | yes | yes |
| Kofi Mensah | kofi.mensah | Web Platform engineer (marketing site, CDN) | Europe/London | UTC | none | yes | yes |
| Jordan Pike | jordan.pike | Release manager, Change Management | America/New_York | Eastern | none | yes | yes |
| Priya Raman | priya.raman | Payments Engineering, senior engineer | America/New_York | Eastern | Payments - Prod, level 2 | yes | yes |
| Tom Beckett | tom.beckett | Payments Engineering engineer | America/New_York | Eastern | Payments - Prod, level 1 (misses the 14:32 page) | no messages today | yes |
| Dana Whitfield | dana.whitfield | Payments Engineering engineer, author of PAY-2231 | America/New_York | Eastern | none | no messages today | yes |
| Marcus Okafor | marcus.okafor | Former Payments Engineering lead, left in August | America/New_York | n/a | none | deactivated | yes, `active=false`; still `owned_by` on the Payments Gateway CI |

Plus integration users in ServiceNow: `svc-datadog` (Datadog Integration, opens INC0048811) and `svc-jenkins`.

### 1.3 Connections on pay-db-01, minute by minute

| UTC | gateway | order | recon | other | total | what the data shows |
|---|---|---|---|---|---|---|
| 14:29 | 24 | 20 | 0 | 1 | 45 | baseline |
| 14:30 | 60 | 20 | 40 | 1 | 121 | reconciler starts, pools growing |
| 14:31 | 100 | 20 | 40 | 1 | 161 | pay-02 hits 35: first pool timeout, housekeeper stats total=35 active=35 idle=0 waiting=12 |
| 14:32 | 125 | 20 | 40 | 1 | 186 | |
| 14:33 | 136 | 20 | 40 | 1 | 197 | ceiling: pool growth on pay-04 and the Datadog agent get FATAL 53300 |
| 14:34 to 15:05 | ~137 | 20 | 40 | 0 | 197 | Datadog nulls; `postgres.can_connect` CRITICAL; DBA on a reserved slot (connected 14:34:05) sees ~133 idle in transaction |
| 15:08 | ~25 | 20 | 40 | 1 | 86 | gateway restarted on 3.7.9; reconciler still running until 15:15 |

Every artifact that shows a number (Hikari stats lines, pg_stat_activity, Datadog points, the can_connect check text, work notes, answer key) derives from this table through the scenario YAML.

### 1.4 Timeline, v2

All times UTC on 2026-10-13. `t0 = 14:00:00Z = epoch 1791900000`. The generator renders every epoch and every local time; nothing is typed by hand. Visible column: the first checkpoint whose cut (`t < now`) includes the row. Eastern is UTC-4 (EDT), London is UTC+1 (BST), Los Angeles is UTC-7 (PDT).

| UTC | Event | Shows up in | Kind | Visible |
|---|---|---|---|---|
| 2026-03-04 | INC0042041: checkout latency from checkout-db pool exhaustion after a campaign traffic spike. Fix: raise checkout-api pool 20 to 40, add HPA. | ServiceNow incident + journal, KB0009871 (stale) | trap | 14:40 |
| 2026-07-19 | INC0045210: PayCore full outage about 40 min; gateway failed fast with 3000 ms timeouts; no pool issue | ServiceNow | signal | 14:40 |
| 2026-09-22 | INC0047003: redis-sessions fragmentation alert, closed as benign | ServiceNow | trap | 14:40 |
| 6 days before | redis fragmentation monitor flaps every ~2 h | Datadog events | trap | 14:40 |
| 10:00 to 11:00 planned | CHG0031877 "payments-gateway 3.8.0 release (PAY-2198, PAY-2231, PAY-2240)", normal, CAB approved 2026-10-09, risk Moderate, planned 06:00 to 07:00 ET, actual 06:19 to 06:25 ET | ServiceNow change_request | signal | 14:40 |
| 10:19:48 | Jenkins `payments-gateway-deploy` #412 starts, VERSION 3.8.0, TARGET_ENV prod, CHANGE_TICKET CHG0031877, duration 312 s. changeSet: 6 commits since 3.7.9 (see 2.6). | Jenkins | signal | 14:40 |
| 10:20:30 to 10:24:00 | Rolling restarts pay-01, pay-02, pay-03, pay-04: "Started PaymentsGatewayApplication in 41.213 seconds (process running for 43.07)" plus a startup line showing `payments.psp.timeout-ms=8000`, `payments.psp.retries=3` | payments logs | signal | 14:40 |
| 11:00 to 13:00 | CHG0031899 Windows patching on reporting servers win-rpt-05 to win-rpt-08, standard change | ServiceNow | noise | 14:40 |
| 13:30 to 14:30 | CHG0031850 firewall rule for vendor SFTP | ServiceNow | noise | 14:40 |
| 14:10:05 | search-indexer cron fails | Datadog event, PagerDuty 8811 low urgency | noise | 14:40 |
| 14:12:00 | redis fragmentation fires; 14:14 Sam snoozes it in #sre-oncall | Datadog, Slack | trap | 14:40 |
| 14:15:00 | node-east-14 CPU saturation, unrelated namespace | Datadog monitor, Dynatrace P-26101288 | noise | 14:40 |
| 14:24:00 | PayCore EU degradation begins (status page later backdates `started_at` to 07:24 PT) | nothing yet | signal | 15:15 |
| 14:24 to 14:33 | PayCore latency rises from 14:24; slow share 2 percent from 14:26, 9 at 14:30, 17 at 14:31, 22 at 14:32, 25 from 14:33 | metrics, logs | signal | 14:40 |
| 14:26:12 | First "authorize attempt 1/4 timed out after 8000ms (txn=ord-7781901) retrying in 1000ms" on pay-01 | payments logs | signal | 14:40 |
| 14:26:14 | First checkout-api "POST .../v1/authorize timed out after 10000ms" for `ord-7781901` (the same order as the 14:26:12 retry) on a 2.14.2 pod; 504s at 1 to 2 percent | checkout logs, nginx | signal | 14:40 |
| 14:26:34 | First HikariCP leak warning on pay-01: "Connection leak detection triggered for … on thread …, stack trace follows", with `java.lang.Exception: Apparent connection leak detected`; warnings rise from 14:30 | payments logs | signal | 14:40 |
| 14:27:10 | Datadog 7391241 p99 warn on payments-gw, non-paging, posts to #sre-oncall | Datadog, Slack | signal | 14:40 |
| 14:27:30 | Jenkins `checkout-api-deploy` #897, VERSION 2.14.3 (address-form flag, logging lib bump), CHG0031902 standard, 95 s | Jenkins, ServiceNow | trap | 14:40 |
| 14:28:05 to 14:29:10 | checkout-api rollout. One pod fails readiness 40 s. Error rate blips to 2.1 percent for one minute. DEBUG flood begins 14:28:40 (log volume 5x). | k8s events, Datadog warn state, Datadog 7391301 log-volume anomaly | trap | 14:40 |
| 14:29:00 | Dynatrace P-26101350 "Response time degradation" on external service `api.paycore.example`, severity PERFORMANCE, impact SERVICE, affected PaymentsGateway | Dynatrace | signal | 14:40 |
| 14:30:00 | Jenkins `payments-reconciler` #2311 starts (timer). 40 x "connection authorized: user=recon_app database=payments application_name=payments-reconciler" | Jenkins (building, result null), Postgres log | signal | 14:40 |
| 14:30 to 14:33 | Hikari housekeeper DEBUG lines "HikariPool-1 - After cleanup  stats (...)" (two spaces) show pools growing on all VMs | payments logs | signal | 14:40 |
| 14:31:07 | First pool timeout on pay-02: Hibernate `SqlExceptionHelper` ERROR, `SQLTransientConnectionException: HikariPool-1 - Connection is not available, request timed out after 5000ms (total=35, active=35, idle=0, waiting=12)` | payments logs, Splunk alert "Payments pool exhaustion" at 14:32:00 | signal | 14:40 |
| 14:31:34 | "HikariPool-1 - After cleanup  stats (total=35, active=35, idle=0, waiting=12)" at DEBUG on pay-02 | payments logs | signal | 14:40 |
| 14:31 to 14:35 | Error rate 3 percent at 14:31, 9 at 14:33, 14 at 14:35 | Datadog metrics | signal | 14:40 |
| 14:32:10 | Datadog 7391240 "p90 latency high on payments-gw" Triggered (paging) | Datadog event | signal | 14:40 |
| 14:32:25 | PagerDuty 8812 created, service "Payments - Prod", assigned level 1 Tom Beckett | PagerDuty | signal | 14:40 |
| 14:33:00 | Dynatrace P-26101362 "Response time degradation" PaymentsGateway. Davis root cause: Postgres database service on pay-db-01, evidence "Failure rate increase" (connection refusals) | Dynatrace | trap | 14:40 |
| 14:33:02 | Postgres FATAL 53300 "remaining connection slots are reserved for non-replication superuser connections" for `pgw_app` (pay-04 pool growth) and `datadog`. Gateway logs on pay-04 show the same text as the cause of a DEBUG "HikariPool-1 - Cannot acquire connection from data source" line. | Postgres log, payments logs | signal | 14:40 |
| 14:33:30 | Datadog `postgres.can_connect` CRITICAL on pay-db-01, message carries the FATAL text; monitor 7301990 Triggered; `postgresql.connections` points become null until recovery | Datadog | signal | 14:40 |
| 14:33:41 | INC0048811 auto-created by the Datadog integration from the first alert, assignment_group Commerce Platform (auto-rule keys on business service Online Checkout), priority 2 | ServiceNow (10:33:41 ET) | trap | 14:40 |
| 14:33:50 | PagerDuty 8813 "postgres.can_connect CRITICAL on pay-db-01", service "Database - Prod", Elena level 1 | PagerDuty | signal | 14:40 |
| 14:34:00 | Datadog 7391255 "checkout-api error rate > 5%" Triggered | Datadog | signal | 14:40 |
| 14:34:15 | PagerDuty 8814, service "Checkout - Prod"; Alice acknowledges 14:35:02 | PagerDuty | signal | 14:40 |
| 14:34:30 | Elena in #sre-oncall: "pay-db-01 is at max_connections, mostly pgw_app. looking" | Slack | signal | 14:40 |
| 14:35:00 | #inc-checkout-latency created by Alice; PagerDuty bot posts 8812, 8814 | Slack | signal | 14:40 |
| 14:35:30 | Datadog 7390118 synthetic "checkout flow" failing; PagerDuty 8815 at 14:35:40, merged into 8814 at 14:37:00 | Datadog, PagerDuty | signal | 14:40 |
| 14:35 onward | Checkout submit requests/min +70 percent by 14:45; browse and cart flat; repeated cart ids | Datadog hits by endpoint, nginx | trap | 14:40 |
| 14:36:00 | Ben: "we deployed checkout-api 2.14.3 at 14:28, that's gotta be it. @alice can we roll back?" | Slack | trap | 14:40 |
| 14:36:40 | F5 ltm: pool member pay-03 10.20.4.33:8443 monitor status down, Response Code 503. Members flap until 15:05. | F5 syslog via Splunk | trap | 14:40 |
| 14:37:30 | Sam: "is it redis again? the runbook says check redis". Alice: "that runbook is ancient, sessions moved to the cluster last year" | Slack | trap | 14:40 |
| 14:38:00 | Elena in the incident channel: "db is at 197 of 200 usable, mostly pgw_app, plus 40 from recon_app" | Slack | signal | 14:40 |
| 14:39:00 | INC0048811 reassigned to Payments Engineering by Alice (journal 10:39 ET) | ServiceNow | signal | 14:40 |
| **14:40:00** | **Checkpoint 1 cut. Trigger 1 from Alice.** | | | |
| 14:40:00 | Marketing site synthetic fails (CHG0031880 DNS TTL change for cdn.larkspur.com) | Datadog synthetic, Dynatrace P-26101377 | trap | 14:52 |
| 14:40:30 | Jenkins `checkout-api-deploy` #898 rollback to 2.14.2, 95 s, complete 14:42:05 | Jenkins, k8s events | signal | 14:52 |
| 14:40:45 | SolarWinds: "Component Payments HTTP on PAY-03.LARKSPUR.INTERNAL is Down" (10:40:45 local, no zone) | SolarWinds | trap | 14:52 |
| 14:41:00 | Kofi: "is this a network thing? marketing site is down too" | Slack | trap | 14:52 |
| 14:42:25 | PagerDuty 8812 escalates to level 2 Priya Raman after 10 min with no ack (channel type `timeout`) | PagerDuty log entry | signal | 14:52 |
| 14:43:10 | Alice: "rollback done, still seeing 504s" | Slack, INC work note 10:43 ET | signal | 14:52 |
| 14:44:10 | Priya acknowledges 8812: "here, catching up. tom's not answering" | PagerDuty, Slack | signal | 14:52 |
| 14:45:00 | Sam: "pay-03 dropped out of the F5 pool. hardware?" | Slack | trap | 14:52 |
| 14:46:00 | Kofi: "PayCore status page is all green btw" | Slack | trap | 14:52 |
| 14:47:12 | Elena pastes pg_stat_activity inline as psql text in a thread reply: ~133 idle in transaction and 4 active from application `payments-gateway` user `pgw_app`, oldest `xact_start` 38 s ago, `wait_event ClientRead`; 40 active from `recon_app`; "no slow queries. the app is holding connections open, not the db." Same text as an INC work note at 10:47 ET. | Slack thread, ServiceNow journal | signal | 14:52 |
| 14:48:30 | Elena: "I can kill the idle-in-txn sessions if you want, that'd free the pool for a bit" | Slack | trap (harmful action) | 14:52 |
| 14:49:00 | Ben: "so the DB is the problem?" Elena: "no. read the above" | Slack | signal | 14:52 |
| 14:41 to 14:50 | Customer-raised incidents INC0048812 to INC0048814 via Customer Support: "payment spinner never finishes", "order not confirmed but card charged?", "can't pay" | ServiceNow | signal | 14:52 |
| 14:50:00 | Alice: "support says 40+ tickets, payment spinner" | Slack | signal | 14:52 |
| 14:51:00 | Jordan (release manager): "only change on the payments side today was the 6am release, CAB approved, went fine" | Slack | signal (corroboration only) | 14:52 |
| **14:52:00** | **Checkpoint 2 cut. Trigger 2 from Alice. The main test.** | | | |
| 14:53:40 | Priya: "we had a release at 6:20 this morning but it ran clean for hours, doubt it's that" | Slack | trap (timezone) | 15:15 |
| 14:56:00 | Elena kills 30 idle-in-transaction sessions. Demand is near 240 connections against about 137 slots, so the freed slots refill within seconds and the error rate barely moves. Killing sessions mid-authorization strands authorizations PayCore has already taken, which later shows as duplicate charges. | Postgres log, metrics, Slack | signal (harmful action tried) | 15:15 |
| 14:58:00 | Priya: "leak detector on pay-01 is screaming, every trace goes through PaymentService.authorize. the new retry holds the txn open across the PSP call." | Slack | signal | 15:15 |
| 15:01:10 | Jenkins `payments-gateway-deploy` #413, VERSION 3.7.9, CHANGE_TICKET blank, 290 s, complete 15:06:00 | Jenkins | signal | 15:15 |
| 15:09:00 | Emergency CHG0031910 raised after the fact (11:09 ET), references #413 | ServiceNow | signal | 15:15 |
| 15:06 to 15:08 | Pools reset. 5xx to baseline by 15:08. Monitors recover 15:09 to 15:10. PagerDuty incidents resolve. | metrics, Datadog events, PagerDuty | signal | 15:15 |
| 15:08 to 15:24 | Payment success rate about 73 percent: EU authorizations fail fast with "PSP timeout 3000ms" | metrics `payments.authorize.success_rate`, payments logs | signal | 15:15 |
| 15:05 to 15:30 | Customer incidents INC0048815 to INC0048822: "payment spinner", "charged twice order 7781923", "card declined" | ServiceNow | signal | 15:15 |
| 15:10:22 | PayCore status: investigating, "Degraded performance, Authorizations (EU)", `created_at 08:10:22-07:00`, `started_at 07:24:00-07:00` | PayCore status JSON v2 | signal | 15:15 |
| **15:15:00** | **Checkpoint 3 cut (stretch). Trigger 3 from Alice.** | | | |
| 15:15:30 | Reconciler #2311 finishes | Jenkins | | after |
| 15:24:00 | PayCore recovers; status "monitoring" at 15:30 | PayCore v3 | | after |
| 15:30:00 | INC0048811 resolved | ServiceNow | | after |

### 1.5 The nine traps and their discriminators

| Trap | Naive reading | What separates it |
|---|---|---|
| The visible deploy | checkout-api 2.14.3 at 14:28 caused it | 504s on 2.14.2 pods from 14:26, before the rollout; the blip ends 14:30; errors survive the 14:42 rollback; the failing calls are to the gateway |
| The guilty database | Postgres is full; Davis says so; a PagerDuty incident says so | pg_stat_activity: idle in transaction, no slow queries; the app's 53300 errors are pool growth being refused; the DB is full because the app holds |
| The stale runbook and the redis flap | "Checkout latency" runbook says check redis; a redis alert is active | Runbook dated March 2024; CI retired; six-day flap; INC0047003 closed it as benign |
| The dead VM | pay-03 dropped out; hardware | F5 ltm shows monitor status down with HTTP 503 from the health endpoint; ICMP never alerted; members flap; SolarWinds lags |
| The green status page | PayCore says operational | P-26101350 external service degradation; gateway logs "attempt n/4 timed out after 8000ms" |
| The prior incident and the traffic spike | INC0042041 had the same symptoms; traffic is up 70 percent; raise the pool | Traffic rises after errors; only the submit endpoint; repeated cart ids; March's fix is on the wrong service |
| The timezone | "Shipped at 6:20 this morning"; ServiceNow says 06:19 | ServiceNow is Eastern; Jenkins epoch is 10:19:48Z; Slack users.json has tz |
| The missing owner | CMDB says Marcus Okafor owns Payments Gateway | sys_user active=false since August; PagerDuty escalation policy has Priya |
| The concurrent incident | Marketing site is down too; network issue | Its own change CHG0031880; different CI, team, no shared dependency |

Harmful actions the scorer penalises in the top three: roll back checkout-api again; raise the pool or scale pods; restart redis; kill idle sessions (tried at 14:56, holds for three minutes); page infra for pay-03 hardware; name traffic as the cause.

---

## 2. Sources, volumes, formats

One folder per source under `bundles/larkspur-checkout-2026-10-13/`. Raw vendor shape, validated against the authority listed in `docs/schemas.md` by `smalls_data/lint_schema.py` (section 2.7). Never hand-edited after generation. Day-one slice marked (S); full bundle adds the rest.

| Source | Folder | Format | Slice | Full | Mess kept |
|---|---|---|---|---|---|
| Payments gateway logs | `splunk/raw/payments_app/pay-0N.log` | Custom logback.xml pattern `%d{yyyy-MM-dd HH:mm:ss,SSS} %-5level [%thread] %logger{25} - %msg%n`, no zone, server UTC | S, ~1,950 lines (target) | same | failed health checks only, multi-line traces, retry WARNs, Hikari stats, leak traces, 53300 cause chains |
| checkout-api logs | `splunk/raw/checkout_api/<pod>.jsonl` | logstash-logback JSON: `@timestamp` ISO Z, `level`, `logger_name`, `message`, `traceId`, `orderId`, `service`, `version`; `kubernetes.*` added by the shipper | S, ~1,300 lines (target) | same | DEBUG flood 14:28:40 to 14:42, version flips 2.14.2 to 2.14.3 and back |
| Postgres log | `splunk/raw/postgres/pay-db-01.log` | `%m [%p] %u@%d ` prefix with `UTC` suffix | S, ~650 lines (target) | same | every connection received/authorized pair, sampled FATAL 53300, one pid counter rising with time, checkpoints, autovacuum |
| nginx edge | `splunk/raw/nginx/edge-access.log` | combined plus `rt= uct= uht= urt= upstream=`; `[13/Oct/2026:14:31:21 +0000]` | none | ~570 lines (target), sampled to `/api/checkout/*` | 504s with JSON bodies, repeated cart ids, scanner hits |
| F5 ltm | `splunk/raw/f5/ltm.log` | syslog `mcpd[...]: 01070638:5: Pool /Common/pool_pay_gw member /Common/10.20.4.33:8443 monitor status down ...` | none | ~20 lines | flapping |
| k8s events | `splunk/raw/k8s_events/events.jsonl` | Event objects | S, ~40 | ~70 | rollout, readiness failure, HPA on storefront |
| pg_stat_activity | no file of its own: pasted inline in Elena's Slack thread reply and in the INC0048811 work note, both tagged `class:pgsa.idle_in_txn` | psql aligned text as pasted; columns pid, datname, usename, application_name, client_addr, state, xact_start, query_start, state_change, wait_event_type, wait_event, query | S | same | one-off, visible from 14:47:12 |
| Splunk alerts | `splunk/alerts/<receipt time>-<search>.json` | fixed webhook shape: `result` (first row), `sid`, `results_link`, `search_name`, `owner`, `app`, one file per delivery named by receipt time. `_raw` equals the file line verbatim. | S, 3 | 4 | one row only |
| Splunk saved searches | `splunk/saved_searches.json` | name, search (SPL), description, `alert_type` (event searches count events; `stats` searches use `custom` with `alert_condition`), throttle, `updated` | S, 6 | 6 | "PSP slow calls" exists but never fires; "checkout-api 5xx rate" is disabled |
| Datadog | `datadog/monitors.json`, `datadog/events.jsonl`, `datadog/metrics/*.json`, `datadog/synthetics.json` | v1 shapes; events wrapped per line; metrics query response with `series[].pointlist` of `[epoch_ms, value|null]`; monitor `overall_state` derived at runtime | S: 8 monitors, ~25 events, 4 series | ~12 monitors, ~40 events, 7 series, 2 synthetics | flap collapsed by list_alerts; nulls during ceiling; `@pagerduty-` handles and `%%%` fences in messages; p90 and p99 monitors on checkout-api's APM client spans to `peer.service:payments-gw` (series in 1.2) |
| Dynatrace | `dynatrace/problems/<displayId>.v1.json` (open) and `.v2.json` (closed), `dynatrace/entities.json` (13:45 sync) | Problems API v2, one snapshot per state: `problemId`, `displayId`, `title`, `impactLevel`, `severityLevel`, `status`, `startTime`, `endTime` (-1), `affectedEntities`, `impactedEntities`, `rootCauseEntity`, `evidenceDetails.details[]` with `evidenceType` in METRIC, EVENT, TRANSACTIONAL and `rootCauseRelevant`, `managementZones` | S: 3 problems | 4 | only VM-side entities; display ids in time order |
| ServiceNow | `servicenow/incident.json`, `sys_journal_field.json`, `change_request.json`, `cmdb_ci.json`, `cmdb_rel_ci.json`, `sys_user.json`, `sys_user_group.json`, `sys_audit.json`, `major_incident/INC0048811.v<n>.json` | Table API `{"result":[...]}` with `sysparm_display_value=true`: display strings only, times in America/New_York with no marker; reference fields as display names; `sys_id` present. change_request state uses real negative values. The live incident is served as version snapshots, one per save (`docs/DATA.md` 75); `sys_audit` holds its field history as records with stored values, not as a rewind mechanism. | S: 3 history incidents with close notes and work notes; 6 changes in the day | 1 live incident, 3 history, ~8 closed noise incidents over 6 months with realistic close notes (a similarity corpus), 8 customer; ~20 journal; ~12 changes in the day plus ~6 earlier in the week; ~15 CIs; ~20 rel; 6 users; 3 groups; `sys_audit` rows | wrong auto-assignment; stale redis edge; missing checkout to gateway edge; inactive owner; no CI for PayCore; similar-incident retrieval returns INC0042041 first |
| PagerDuty | `pagerduty/incidents/<number>.v<n>.json`, `log_entries.json`, `services.json`, `escalation_policies.json`, `oncalls.json` | REST v2 shapes; opaque ids; incidents served as version snapshots after each status or assignment change (`docs/DATA.md` 80); service status derived at runtime | none | 5 incidents (8811 to 8815), ~14 log entries | escalation on timeout; merge of 8815 into 8814; alert_counts recomputed |
| Jenkins | `jenkins/jobs/<job>/builds/<n>.building.json` and `<n>.json` | JSON API `api/json?depth=1`; `FreeStyleBuild` with `changeSet.items[]`; a building snapshot (`result` null, `duration` 0) and a finished one | S: 7 builds | ~15 across 4 jobs over the week | reconciler has no change ticket; #413 has no change ticket and emergency CHG0031910 is raised after it finishes; most builds in the window are unrelated |
| Slack | `slack/channels.json`, `users.json` (with `tz`, `tz_offset`), `inc-checkout-latency/2026-10-13.json`, `sre-oncall/2026-10-13.json` | export format; opaque `U0…` ids; `thread_ts`; `reply_count` recomputed at runtime; bot messages with `subtype: bot_message` | S: ~40 in the incident channel plus the fixed #sre-oncall messages (14:14 snooze, 14:27 warning, 14:34 Elena) | ~60 plus ~15 in on-call | local times in text; nicknames; pasted psql text in a thread |
| Confluence | `confluence/runbooks/*.md` | Confluence-style markdown with "Last updated" line | S: 2 runbooks | 2 runbooks plus KB0010418 (idle-in-transaction diagnosis), KB0009871 (stale checkout triage), KB0010502 (the design note that proposed the PSP retry, reads as reassurance), KB0010233 (rolling back a Jenkins deploy), 2 unrelated | stale one linked from every alert via the monitor message and the alert tags, as BigPanda surfaces runbooks; accurate one generic |
| SolarWinds | `solarwinds/alerts.json` | SWIS-style rows: `AlertActiveID`, `AlertName`, `EntityCaption`, `EntityType`, `Severity` (2 = Critical), `TriggeredDateTime` local with seven-digit fraction, `ResetDateTime`, `Acknowledged` | none | 3 | local time no marker; uppercase FQDN |
| PayCore status | `paycore/status.v1.json` (green), `v2.json` (15:10), `v3.json` (15:30) | Statuspage style with `page.time_zone America/Los_Angeles`, `components`, `incidents[].started_at` | S: v1 | v1 to v3 | versioned |
| Triggers | `triggers/1440.md`, `1452.md`, `1515.md` | plain text from Alice; harness input, not served: outside `integration.yaml` `sources:`, so the loader never reads it | S: all three | all | each written for its moment |
| Integration config | `integration.yaml` | `sources:` lists every path the loader reads (folders plus `splunk/saved_searches.json`); per source `zone` (IANA), `format` (a valid strptime pattern) or `format_note`, `marker`, `break_only_before` for logs, and for each `splunk/raw/*` folder `index`, `sourcetype`, `source` and a `host` rule. `sampling:` gives each raw log's pipeline, key and line classes (`match` regex, `keep_1_in`, unit). Rendered from `facts.yaml` `zones:`, `scenario/content/splunk.yaml` and the scenario `sampling:`, so ingestion never guesses a clock or a rate and never reads `facts.yaml` | S | S | none: it is configuration, not evidence |
| Hidden | `_internal/manifest.jsonl`, `_internal/answer_key.yaml` | see section 3 | S | S | never served |

**Log windows and sampling.** Every raw log is a documented sample of what the estate would write, not a full capture. Each line class keeps one constant rate for the whole window (data fix 1). Request lines are sampled per cart: checkout-api decides by hashing `cartId` and the decision travels with the call to the gateway, so a kept cart keeps every line of every request in both logs and rates nest. At most one line or request per host is force-kept as an anchor. The rates are served in `integration.yaml` `sampling:`; the generator writes the unsampled population to `_internal/population.json` for lint invariant 15.

| Log | Window (UTC) | Sampling by line class (constant for the window) | Target lines |
|---|---|---|---|
| payments_app, 4 VMs | 10:15 to 10:30 (deploy and startup), 13:45 to 15:30 | startup, shutdown and scheduler lines all; authorize ok 1 in 1,500 requests; slow PSP response 1 in 12; retried requests (every attempt WARN and the final ERROR) 1 in 100; pool timeouts 1 in 200 requests, the first kept per host with its full cause chain; leak traces 1 in 400 requests; fail-fast 1 in 60; housekeeper stats one 30-second run in 4; pool growth 1 in 6; "Cannot acquire" 1 in 24 attempts; failed health checks 1 in 14 | 1,950 |
| checkout_api, 6 pods | 13:45 to 15:30 | lifecycle and application lines all; gateway failures (client ERROR and its 504 or 502 request line) 1 in 100 requests per cart; request lines 1 in 2,000; the DEBUG flood 14:28:40 to 14:42:05 1 in 70 of about 1,680 lines a minute; version and pod fields on every line | 1,300 |
| postgres, pay-db-01 | 13:45 to 15:30 | every connection received/authorized pair; FATAL 53300 1 session in 12; EOF with an open transaction 1 in 8; the 14:56 terminations all; checkpoints every 20 min; autovacuum | 650 |
| nginx edge (full) | 13:45 to 15:30 | `/api/checkout/*` only, 1 in 420; 5xx 1 in 110; scanner hits all | 570 |
| F5 ltm (full) | 14:30 to 15:10 | all member state changes | 20 |
| k8s events | 13:45 to 15:30 | all | 60 |

**Counting rules for the budget.** A log line is one physical line in a raw log file, so stack-trace frames count. A structured record is one top-level object in a vendor file: an event, problem, incident, journal entry, change, build, commit, message, CI, relationship, user, group, log entry or status snapshot. A vendor source is one top-level folder in the bundle (`splunk` counts once; `triggers`, `integration.yaml` and `_internal` do not count). Targets sit under the ceilings: 4,550 log lines against 5,000 (about 9 percent under since data fix 1 logs every Postgres connection), about 210 structured records against 250, and 10 vendor sources against 11.

**Size budget.** The requirements say "a small but realistic dataset with enough ambiguity, noise, and signal is better than a huge artificial one." The lint enforces a ceiling so the bundle cannot drift upward on day two: at most 5,000 log lines across all raw logs, at most 250 structured records (alerts, problems, incidents, journal entries, changes, builds, CIs, relationships, users, PagerDuty objects), at most 11 vendor sources, and `make data` fails if any ceiling is exceeded. Every noise element must be named in the scenario YAML with the trap it serves, or tagged `background` (ordinary estate activity) or `defect` (the realistic defects below); anything else is deleted. The data note for the write-up states that the volume is a deliberate sample of a realistic estate (four gateway VMs at 14 authorizations per second would log about 75,000 lines in this window) and that the tools exist because real estates do not fit in a prompt, not because this one does not.

**Realistic defects.** Real integrations deliver imperfect data, so the bundle carries a few deliberate defects, each recorded in the scenario YAML as noise with a reason:
- one duplicate delivery: the Splunk "Payments pool exhaustion" webhook body appears twice with the same `sid`, in two files named by receipt time 40 seconds apart, as a retried POST would;
- one truncated line: a payments log line cut mid-stack-trace where the forwarder hit its line-length limit, so the next line starts with a bare `at ...` frame;
- one fire-and-resolve alert: a Datadog disk-space warning on `ord-01` that triggers and recovers within 50 seconds, with a PagerDuty low-urgency incident auto-resolved by the recovery;
- empty fields stay empty: ServiceNow records carry every field of their table, most as `""`, as a real Table API response does.

No timestamp jitter and no regularity lint: the realism budget goes on shapes, defects and hand-written content.

**Stated in the write-up.** One line: the data is synthetic; its shapes follow each vendor's published API spec or source code where reachable, otherwise the vendor's documentation, recorded per source in `docs/schemas.md`.

Cut from v1 and not coming back: Nagios, the ServiceNow KB as a second document store, pay-02 clock skew, the logrotate gap, Splunk London display time, difficulty knobs, archetype B, `searches/*.json` result snapshots (search_logs runs against raw logs instead).

### 2.6 Signal content that must be written by hand (main session, never a worker)

- Jenkins #412 changeSet, six commits: `Merge branch 'release/3.8.0'`; `PAY-2198 bump jackson-databind to 2.17.2`; `PAY-2240 refunds: emit settlement metrics`; `PAY-2231 PSP client resilience` (affectedPaths `src/main/java/com/larkspur/payments/psp/PayCoreClient.java`, `.../psp/RetryPolicy.java`, `src/main/resources/application-prod.yml`); `PAY-2231 address review comments`; `chore: update CHANGELOG for 3.8.0`. Author Dana Whitfield for PAY-2231.
- Startup lines on each VM at 10:20 to 10:24 showing `payments.psp.timeout-ms=8000 payments.psp.retries=3` (3.8.0) and, after 15:01, `timeout-ms=3000 retries=0` (3.7.9). July's INC0045210 notes mention the 3000 ms timeout. This is the two-hop that replaces an explicit commit message.
- Retry WARN template; Hibernate `SqlExceptionHelper` ERROR with the Hikari exception and a trace in which Spring's `TransactionInterceptor` borrows the connection before the CGLIB proxy calls `PaymentService.authorize` (so the frames show the interceptor and the proxy, not a line inside the method body); housekeeper DEBUG "After cleanup  stats" with two spaces; HikariCP leak warnings "Connection leak detection triggered for … on thread …, stack trace follows" with `java.lang.Exception: Apparent connection leak detected` from about 14:26:34, rising from 14:30; DEBUG "Cannot acquire connection from data source" with 53300 causes on pay-04.
- Postgres FATAL lines and the 40 reconciler "connection authorized" lines.
- Dynatrace P-26101350 and P-26101362 bodies. Datadog monitor definitions and messages.
- pg_stat_activity snapshot text.
- The two runbooks. The stale one: redis steps, scale checkout to 12 replicas, last updated 11 Mar 2024. The accurate one, `PGW-DB-CONN-TIMEOUT`, last updated 2 Aug 2026: pg_stat_activity state breakdown; "idle in transaction means long app-side transactions; active means slow queries"; "restart gateway nodes one at a time; escalate to Payments Engineering". It must not mention retries, PSP timeouts or rolling back.
- Trap and signal Slack messages listed in 1.4 (Ben 14:36, Sam 14:37 and 14:45, Elena 14:34, 14:38, 14:47, 14:48, 14:49, Kofi 14:41 and 14:46, Jordan 14:51, Priya 14:44, 14:53, 14:58, Alice 14:43, 14:50). Workers fill the chatter between them.
- The three history incidents INC0042041, INC0045210 and INC0047003 with their close notes and work notes, and the six changes in the day: CHG0031850, CHG0031877, CHG0031880, CHG0031899, CHG0031902 and, for 15:15, CHG0031910.
- The three triggers. The answer key.

### 2.6b Why these three sources get extra depth

Deployment notes, prior incident notes and runbooks are the inputs closest to how Biggy works today, so each is seeded to support the mechanic and to show where the mechanic alone misleads:

- **Changes and deploys.** BigPanda's Root Cause Changes matches change records to an incident by time and topology and scores them. With about twelve changes in the day, proximity matching ranks the checkout-api deploy first and the gateway release, four hours earlier, low. The agent has to get from mechanism (8 s timeout in the logs versus July's 3 s) back to the change. The runtime normalisation target for a change is BigPanda's own change object: identifier, status, summary, start, end, ticket_url, tags.
- **Prior incidents.** AI Incident Analysis recommends priority, assignee and actions from how similar incidents were triaged. A corpus of about ten closed incidents with real close notes makes similar-incident retrieval meaningful, and the most similar one (INC0042041) has the wrong fix. INC0045210 is the useful contrast and INC0047003 retires the redis theory.
- **Runbooks.** Runbook links reach responders as enrichment on alerts. The link on every alert is the stale runbook. The accurate one is generic and reachable only through the Splunk saved search and search_knowledge. KB0010502 is the design note that proposed the retry and reads as reassurance.

### 2.7 Schema conformance and provenance

The question this answers: is the data structured the way the real product emits it, and how do we know. Every source has one of four kinds of authority, recorded in `docs/schemas.md`:

1. **Official machine-readable spec.** Datadog (OpenAPI v1), PagerDuty (OpenAPI v3), Slack message object (Web API spec), Kubernetes Event (swagger). `schemas/fetch.sh` downloads them; a small script extracts the component schemas we use into `schemas/<vendor>/extracted/*.schema.json` (committed, small). The lint validates every generated JSON record against them with `jsonschema`.
2. **Official source code.** Dynatrace's open-source API client (Problem model), Jenkins `Run.java` and the git plugin's `GitChangeSet.java` (exported fields), HikariCP 5.1.0 and PostgreSQL 15 (exact log strings). The lint checks field names against a list extracted from the source and checks that every log template renders a string that matches the pinned source text.
3. **A real sample.** A public Statuspage `summary.json` captured from a real status page is the reference for PayCore. The lint checks field names by example.
4. **Transcribed field lists** where no spec is reachable from this environment: ServiceNow tables, SolarWinds SWIS, nginx, F5, Logback. The lint checks required fields present and flags unknown fields. These are the sources labelled templated.

**ServiceNow.** The Table API reference and its full incident example are now the authority for the envelope, value rules and incident fields (see `docs/schemas.md`). The remaining tables run on transcribed field lists, which is accepted for this take-home. Optional, not planned: create a free Personal Developer Instance, export `sys_dictionary` for `incident`, `change_request`, `cmdb_ci`, `cmdb_rel_ci`, `sys_journal_field`, `sys_user`, `sys_user_group`, `sys_audit`, and save one `sysparm_display_value=true` Table API response per table into `schemas/servicenow/pdi/`. The generator then renders only fields that exist, with the real choice labels and the real `{"result": [...]}` envelope. Until that exists, the lint runs ServiceNow in field-list mode.

Facts confirmed against the authorities on 2026-10-12 that change v1's examples:

- HikariCP 5.1.0's timeout message ends with `(total=N, active=N, idle=N, waiting=N)`. The housekeeper lines are `Before cleanup stats (...)` and `After cleanup  stats (...)` with two spaces. The leak task logs WARN `Connection leak detection triggered for {connection} on thread {thread}, stack trace follows` with an attached `Exception("Apparent connection leak detected")`. The stats, timeout-failure and connection-acquire lines are DEBUG. The timeout is thrown by Hikari and logged by the caller.
- PostgreSQL 15's message is exactly `remaining connection slots are reserved for non-replication superuser connections`. Later majors changed it, so the version pin matters.
- Dynatrace `evidenceDetails.details[]` items carry `evidenceType`, `displayName`, `entity`, `rootCauseRelevant`, `startTime`, and type-specific fields. `endTime` is `-1` while open.
- Datadog `Monitor.overall_state` is one of Alert, Ignored, No Data, OK, Skipped, Unknown, Warn. `Event.alert_type` is one of error, warning, info, success, user_update, recommendation, snapshot. The v1 `Event` schema has no `monitor_id`; real monitor events do. We keep `monitor_id` and the lint warns rather than fails for that field.
- Slack messages have `reply_users`, `latest_reply` and `reply_users_count` alongside `reply_count`; the export serves them at their 15:30 values and `read_chat` recomputes all four at `now` (PLAN 3.4).
- Jenkins change items also expose `comment`, `authorEmail` and `date`; include them.
- A Splunk webhook `result` row is either an event row (with `_raw`, `_time`, `host`, `source`, `sourcetype`) or a `stats` row (with the aggregated fields); never both `count` and `_raw`. Use an event row.
- Datadog `postgresql.connections` is tagged per database, so query it with `sum:`, not `avg:`.
- PagerDuty incidents carry `type`, `summary`, `priority` and `teams`; each assignee and acknowledger has `id`, `type` and `summary`.
- ServiceNow: every value is a string, empty fields are `""`, list responses are `{"result": [...]}` (from the Table API reference).

---

## 3. Contracts: write these first

### 3.1 Report and state schema

One object. The UI renders it, the scorer reads it, the next checkpoint starts from it. Stored as `smalls/contracts/report.schema.json` (JSON Schema) with a pydantic model beside it.

```jsonc
{
  "as_of": "2026-10-13T14:52:00Z",
  "summary": "...",
  "impact": { "business_service": "Online Checkout", "cis": ["checkout-api", "payments-gateway"],
              "priority": { "suggested": "P1", "impact": "high", "urgency": "high" },
              "assignment_group": "Payments Engineering", "oncall": "Priya Raman",
              "customer": "payment submissions failing or slow; browsing and cart unaffected" },
  "hypotheses": [{
    "id": "h-gw-retry",
    "statement": "...",
    "role": "cause | trigger | contributing | symptom | unrelated",
    "status": "leading | open | rejected",
    "entities": ["payments-gateway", "PayCore"],
    "changes": ["snow:CHG0031877", "jk:payments-gateway-deploy#412"],
    "confidence": 0.75,
    "support": ["log:pay-02/3fa91c0e", "dt:P-26101350"],
    "against": ["slack:inc/8b2e11f0"],
    "history": [{ "as_of": "2026-10-13T14:40:00Z", "status": "open", "confidence": 0.4, "because": ["..."] }]
  }],
  "timeline": [{ "t_utc": "...", "event": "...", "evidence": ["..."] }],
  "actions": [{ "rank": 1, "action": "...", "owner": "Payments Engineering", "evidence": ["..."] }],
  "do_not": [{ "action": "...", "why": "...", "evidence": ["..."] }],
  "proposed_actions": [{ "call_id": "...", "tool": "...", "args": {}, "rationale": { "tests": "...", "expect": "..." },
                         "decision": "approved | rejected | deferred", "by": { "who": "...", "via": "cli | web | headless" },
                         "reason": "ic:note/1c9e44d2", "decided_at": "..." }],
  "open_questions": ["..."],
  "stakeholder_update": { "status": "...", "customer_impact": "...", "scope": "...", "changed_since_last": "...",
                          "next_steps": "...", "next_update_at": "2026-10-13T15:07:00Z" }
}
```

`do_not[]` lists what not to do, each with why and its evidence; it is seeded from rejected actions and satisfies the "lists what not to do" criterion in section 13. `proposed_actions[]` is written by code from the gate ledger, never by the model: one row per gated action call with the gate decision, who made it and the reason (a rejection reason is an `ic:note` handle, 3.2). `actions[]` stays the model's ranked recommendation. `stakeholder_update` follows the status-update practice in PagerDuty's open-source incident response guide: current status, customer impact and scope, what changed since the last update, what we are doing next, and the time of the next update. Ruled in `docs/research/agent-design.md` section 7, rulings 7 and 8.

`trigger` is optional: some incidents have a cause and a condition but no external trigger. `impact` uses IT operations vocabulary (business service, CIs, priority from impact and urgency, assignment group, on-call) so the output reads as an incident commander's, not an engineer's notes.

Note for the agent phase, flagged by a reviewer and to be verified against current API docs before relying on it: produce the final report through structured output or a strict tool the model chooses, not a forced tool call.

### 3.2 Handles and the manifest

Tools mint handles and print one at the start of every row. The agent cites handles. The manifest is never served.

```
# handle grammar
snow:INC0048811   snow:CHG0031877   dt:P-26101350   pd:8812   dd:ev7820011093   dd:mon7391240
jk:payments-gateway-deploy#412   sw:55012   conf:PGW-DB-CONN-TIMEOUT   psp:paycore@v2
log:pay-02/3fa91c0e        # sha1(source path + raw line)[:8]; "~2" suffix on duplicate lines
slack:inc/8b2e11f0         # sha1(channel + ts + text)[:8]; journal, PD log entries, k8s events the same way
q:7c1d02aa                 # an aggregate the tool computed: sha1(normalised query + as_of)[:8]
metric:sum:postgresql.connections{host:pay-db-01}by{db}@1430-1452   # query without spaces; a tool's window, clamped to now
spl:search/<name>          # a saved search, read as a record, never run
ic:note/1c9e44d2           # an incident commander's note or gate-rejection reason: sha1(run_id + submitted-at + text)[:8]
```

`ic:note/<h8>` handles are minted at runtime by the agent runner when the incident commander adds a note or gives a reason for rejecting a gated call; they never appear in the manifest. They are classed with chat: a note can steer the investigation, but it cannot on its own support a causal link (section 5). `ic_note_handle()` lives in `smalls/contracts/handles.py` with the rest of the grammar (ruling 6 in `docs/research/agent-design.md` section 7).

Metric handles follow ruling A3: `metric_handle(query, w0, w1)` and `parse_metric_handle()` live in `smalls/contracts/handles.py`, one handle function per prefix, used by both the generator and ingestion. The manifest stores the series identity `metric:<query>` with no window; tools mint windows clamped to `now`, and the scorer resolves a metric handle by its query. A served or manifest window must never reveal how far the data runs.

`_internal/manifest.jsonl`, one line per record version:

```json
{"handle":"log:pay-02/3fa91c0e","src":"splunk","path":"splunk/raw/payments_app/pay-02.log","locator":4412,
 "t_utc":"2026-10-13T14:31:07.221Z","t_known":"2026-10-13T14:31:07.221Z","valid_from":null,"valid_to":null,
 "class":"log.hikari_timeout","sig_id":"sig.first_pool_timeout","kind":"signal"}
```

`t_utc` is when it happened; `t_known` is the earliest time an observer could see it, required on every row and re-derivable from the served bytes by a per-source rule (ruling D1; a parity test checks it). They are equal except for backdated records (CHG0031910 raised at 15:09 for a 15:01 deploy; PayCore v2's `started_at` 14:24, published 15:10:22), metric buckets (known when the bucket closes), the closed or finished version of a Dynatrace problem or Jenkins build (known at its end), a k8s event (known at `lastTimestamp`) and a webhook delivery (known at the receipt time in its file name). `smalls_data/parity.py` states the rule per source. `kind` is signal, trap or noise. `class` is a generated line class (`log.psp_retry`, `log.hikari_timeout`, `log.hikari_leak`, `log.health`, `pgsa.idle_in_txn`, `pg.fatal_53300`, ...). `sig_id` is an author-chosen id for hand-written items. The answer key references `sig_id` and `class`, never handles. The report validator rejects unknown handles before scoring and gives the agent one retry.

### 3.3 Answer key shape

`_internal/answer_key.yaml`. Aliases reuse `scenario/facts.yaml` entities verbatim.

```yaml
facts:
  F.retry_change:   {any_of: [sig.jk412_changeset, sig.jk412_yml_path, sig.startup_timeout_8000]}
  F.psp_slow:       {any_of: [sig.dt_p26101350, class:log.psp_retry, class:log.psp_ok_slow]}
  F.pool_exhausted: {any_of: [sig.splunk_alert_pool, class:log.hikari_timeout, class:log.hikari_leak, class:log.hikari_stats_full]}
  F.idle_in_txn:    {any_of: [sig.slack_elena_1447, sig.inc_wn_idle_txn, class:pgsa.idle_in_txn]}
  F.rollback_no_fix:{any_of: [sig.jk898_rollback, sig.slack_alice_1443]}
checkpoints:
  "14:52":
    leading: {entity: payments-gateway, change_any: [CHG0031877, "payments-gateway-deploy#412", "3.8.0"]}   # change_any may be none when the cause is not a change record
    trigger: {entity: paycore}            # may be null; a key may give a judge-checked condition instead
    required_facts: [F.retry_change, F.psp_slow, F.pool_exhausted, F.idle_in_txn, F.rollback_no_fix]
    non_chat_required: true          # each causal link needs at least one non-chat handle
    traps:
      checkout-deploy: {match: {entity: checkout-api}, allowed: {status: [rejected]}}
      db-capacity:     {match: {entity: pay-db-01}, allowed: {role: [symptom, contributing]}}
      redis:           {match: {entity: redis-sessions}, allowed: {status: [rejected]}, absent_ok: true}
      pay03-hardware:  {match: {entity: pay-03}, allowed: {role: [symptom, contributing]}, absent_ok: true}
      marketing:       {match: {entity: marketing-site}, allowed: {role: [unrelated]}, absent_ok: true}
    owner: {any: ["Priya Raman", "Payments Engineering"], never: ["Marcus Okafor"]}
    confidence: {wrong_leading_max: 0.6, right_leading_min: 0.6}
    judge:
      mechanism: [retry_or_timeout_increase, txn_held_across_psp_call, pool_exhaustion, caller_timeouts]
      actions:   [gateway_rollback_or_disable_retry_in_top2, reconciler_pause_as_headroom, duplicate_auth_audit, customer_update]
      harmful_top3: [rollback_checkout_again, raise_pool_or_scale_pods, restart_redis, kill_idle_sessions, page_infra_for_pay03, traffic_named_as_cause]
  "14:40":
    leading_any: [payments-gateway]            # may still be open rather than leading
    trigger_optional: true
    required_facts: [F.psp_slow, F.pool_exhausted]
    confidence: {wrong_leading_max: 0.6}
```

Every held-out key also carries a generated `dev_answer` trap (matches payments-gateway, CHG0031877, #412, 3.8.0; allowed any role except leading cause), which is the most direct overfitting number available. The scorer contains no bundle ids; judge checklists are per-bundle data under one generic judge prompt; confidence thresholds are identical across bundles.

### 3.4 Replay rules

- The harness injects `now`. Every `since`/`until` is clamped to it. Relative windows resolve against it. `now` is never a tool parameter.
- Visible means `t_known < now`, and for versioned records `valid_from <= now < valid_to`. No decisive item within 60 s of a checkpoint (lint).
- Three code paths, implemented once in the store's `visible` view over a one-row `clock` table, not per tool:
  - **Filter** append-only sources: Datadog events, metric buckets, raw logs, Slack, journal, customer incidents, k8s events, F5 lines.
  - **Derive** state at `now` from records already visible: Datadog monitor `overall_state` from its events; PagerDuty service `status` from its incidents; Slack thread summaries from the visible replies; SolarWinds active from trigger and reset.
  - **Versions** for records whose nested fields change: Dynatrace problems (an open version with `status: OPEN`, `endTime: -1` and impact as at then, a closed version from the end) and Jenkins builds (a building version with `result: null`, `duration: 0`) are served as version snapshots, as PayCore already is (ruling D1b, data fix 2). A replace-at-now overlay cannot redact nested fields; snapshots can. The live ServiceNow incident (one snapshot per save, `docs/DATA.md` 75) and PagerDuty incidents (one per status or assignment change, carrying status, assignments, acknowledgements and `alert_counts`, `docs/DATA.md` 80) are version snapshots too. `sys_audit` rows are records in their own right, not a rewind mechanism. Change requests are served as exported and are visible from creation (`docs/DATA.md` 35).
- Fields whose served value is not true at `now` are withheld by ingestion: the 13:45 sync values (Datadog `overall_state` and PagerDuty service `status`, `docs/DATA.md` 93) and the 15:30 export values (Slack `reply_count`, `reply_users`, `reply_users_count` and `latest_reply`; Slack channel `members`; the metric envelope's `to_date`, `series[].end` and `series[].length`). Ingestion owns what is safe to show; the tool that shows a record supplies the value derived at `now`.
- A tag is computed from one record version's own bytes, so it is visible exactly when that version is. Facet counts are computed over visible rows only.
- Recompute derived counts at `now`: Slack `reply_count`, Dynatrace `evidenceDetails.totalCount`.
- One trigger per checkpoint. The harness reads the trigger for the current checkpoint; `triggers/` is not a served source and the loader never reads it (data fix 2). Triggers read as an incident commander talks: theories are people's theories, and no trigger states the database's innocence or who is on call (data fix 10). Trigger 1 (14:40) is written before any rollback; trigger 2 (14:52) mentions the failed rollback and the DBA; trigger 3 (15:15) is "gateway rolled back, 5xx gone, success rate still low, what now?".
- Future-token lint per checkpoint: at 14:52 nothing visible may contain a build with VERSION 3.7.9 that starts after the cut, `CHG0031910`, `#413`, `parked in`, `leak detector`, `Degraded performance`, `charged twice`, `card declined`.
- Leak test: at each checkpoint call every tool with its widest window and assert the latest `t_known` returned is before `now`; scan every epoch and ISO timestamp anywhere in each tool output, nested fields included, against `now`; and scan every output for the checkpoint's future tokens (data fix 2).

### 3.5 Scenario YAML and facts.yaml

`scenario/facts.yaml`: entities with every alias per source, people (name, handle, role, Slack id, PagerDuty id, ServiceNow user, tz), zones per source, anchor epochs, the fixed parameters table from 1.2. Zones render, without their notes, into the served `integration.yaml`, which also carries per `splunk/raw/*` folder the index, sourcetype, source and host rule, and a `sampling:` block with the rate per source and line class (ruling D2). Ingestion reads only `integration.yaml`, never `facts.yaml`.

`scenario/larkspur-checkout.yaml`: the timeline from 1.4 as events with UTC clock times (`at: "14:31:07.221"`), kind, source, host, template, `sig_id`, `kind: signal|trap|noise`; metric curves as piecewise points; the connection budget; rates with caps. Every number in every rendered file comes from here. No `signal:` or `trap:` field is ever copied into a served file.

---

## 4. Tool surface

Eleven tools. Rules for all: `now` injected by the harness; every row starts with its handle; times shown as UTC plus the native value where it differs; results capped near 6k tokens with a footer naming what was cut and how to narrow; the equivalent native query (SPL, `sysparm_query`, Datadog query) echoed in the result for the trace, never parsed from the model. Tool names use underscores only.

**Store.** Tools read one in-memory stdlib SQLite store built at startup by `smalls/tools/store.py` (measured with every source loaded: about 215 ms for 5,861 rows, `docs/DATA.md` 113): a `record` table with one row per record version, a `point` table for metric points, one FTS5 table with the porter tokenizer for knowledge prose, JSON1 for native fields, and a `visible` view over a one-row `clock` table (section 3.4). No tool reads `record` directly. One adapter per source under `smalls/tools/ingest/` parses clocks through the served `integration.yaml` and folds multi-line records by `break_only_before`. The loader reads only the folders listed under `sources:` in `integration.yaml` and refuses paths under `_internal/` or `scenario/`. Logs are matched by substring (`instr`), not through a full-text index. Cross-source values are annotated, never overwritten: a CMDB owner, an alert tag and a PagerDuty assignee appear side by side, each with its source (D5). The DDL and the store rules are fixed in `docs/decisions/ingestion-decisions.md`. Adapters cover every `sources:` key at startup. The D6 masker runs at ingest and writes a template id on each log record. Each log record carries its `keep_1_in` from the served `sampling:` block; class names never reach a showable column. An empty `clock` makes every view return nothing.

**Tags.** A closed-set classifier labels records on three facets: `signal` (log templates, alert definitions and titles, incidents, metric series), `act` (human chat messages, ServiceNow journal entries, PagerDuty notes; asked as a yes or no per option, so a message can carry more than one value) and `change_kind` (change requests, Jenkins builds, commits). Tags are off by default: `SMALLS_TAGS` defaults to `off`, `on` is opt in, and the tag inputs in the table below exist only when tags are on. The rules: a tag is computed from one record version's own bytes only; the vocabulary is closed and checked by code; a tag is applied only at high confidence (its probability at or above a floor that starts at 0.8 and is set for precision; measured at 0.90), so a doubtful answer stores nothing; tags annotate and never overwrite, are never evidence and are never cited; a tag is never a default filter, and a tag-filtered result's footer says how many rows matched without the filter and how many of those are untagged; startup reads the committed cache and stays offline; `SMALLS_TAGS=on|off` is config, never a model choice. The tag rules, the taxonomy and the thresholds are in `docs/decisions/ingestion-decisions.md`.

**Result envelope (A1).** Every tool returns `{text, shown[], referenced[], native_query, window}`: the text the model reads, the handles split in two, the echoed native query, and the window after clamping. `shown[]` holds the handles whose content is in the text; `referenced[]` holds handles only listed as children or pointers (log entries under an incident, thread replies, commits in a build, the alerts a saved search fired). Only shown handles are citable: a referenced handle becomes citable once a later call shows it. The harness logs both lists per call, and the scorer accepts a handle that was shown in this run, or an `ic:note` handle minted in this run (`q:` and windowed `metric:` handles never appear in the manifest). Ruled in `docs/research/agent-design.md` section 7, ruling 11; the split is built with the tool layer in step 6a-tools.

**Error contract (A2).** Errors come back as text the model can act on, never as exceptions. A handle that is not yet visible gets the same answer as an unknown one. A window clamped to `now` says so. An empty result says how to widen. A record the bundle lacks says "not in this bundle".

**Source inventory (R1).** The harness injects an inventory of what exists, built at runtime from visible records only, once per checkpoint in the first user turn: knowledge documents (handle, title, owner, last updated), metric series and scopes, saved searches with how often each fired, chat channels, log sourcetypes and hosts, time coverage per source, and, when tags are on, counts per tag facet over visible rows. The system prompt and tool definitions stay a stable cached prefix. At estate scale the inventory falls back to counts plus the top N per kind.

| Tool | Backed by | Inputs | Returns | Cap |
|---|---|---|---|---|
| `list_alerts` | Datadog events and monitors, Dynatrace problems, Splunk alerts, SolarWinds, PagerDuty incidents | `since`, `until`, `source?`, `text?`, `state?` (active or all); only when tags are on: `signal?` | One row per alert stream: handle, source, native id, title, raw entity string unresolved, first seen, last change, state at now, transitions count, linked PagerDuty incident. Flapping streams collapse to one row with a count. Splunk alert deliveries collapse by `sid` (`deliveries: 2`, both handles resolve) and each row links its saved search record. | 40 rows |
| `get_record` | any handle, including saved searches as `spl:search/<name>` | `handle` | The native vendor payload as it stood at now, from the version valid at now; empty fields hidden and counted; long text clipped at 2k chars; children as handles (log entries, thread replies, journal entries, commits, evidence). A saved search returns its definition, schedule, threshold, runbook link and the visible alerts it fired, with receipt times; it is never run (C3). | 3k tokens |
| `search_logs` | raw logs | `terms[]`, `sourcetype?`, `host?`, `since`, `until`, `group_by?` (minute, host, level or template), `bucket?` (1m or 5m), `limit<=50`; only when tags are on: `signal?` and the `group_by` value `signal` | Rows mode: total count, rows with handle, UTC time, host, sourcetype, raw line to 300 chars, traces folded to 3 frames, 30 rows by default. Stats mode: table to 60 cells. `group_by=template` masks numbers, ids and hex with regexes and returns the top templates with counts plus an "other" row (D6). Every count from a sampled source is labelled "sampled 1:N, estimated X" from the served `sampling:` block. Echoes SPL. | 4k tokens |
| `query_metrics` | Datadog | `metric`, `scope`, `since`, `until`, `rollup?` | Up to 4 series rolled to 45 points max, nulls kept; summary with min, max, first breach, null runs; echoes the Datadog query; returns a metric handle minted by `metric_handle(query, w0, w1)` with the window clamped to now (A3) | 2k tokens |
| `list_changes` | Jenkins builds, ServiceNow change requests | `since`, `until`, `text?`; only when tags are on: `change_kind?` | Rows side by side, never joined or ranked: handle, kind, id or job#n, CI or job, summary, VERSION, CHANGE_TICKET, start, end, state at now, requester | 30 rows |
| `itsm_query` | ServiceNow incidents, journal, users, groups | `table` (enum), `filters`, `text?`, `fields?`, `limit<=20`; only when tags are on: `signal?` on incidents | Display values as they stood at now (for the live incident, the version valid at now); echoes `sysparm_query` | 3k tokens |
| `search_knowledge` | Confluence runbooks and KB pages, close notes and work notes of closed incidents | `query`, `k<=5` | Chunks ranked by SQLite FTS5 `bm25()` over porter-stemmed text, every query token quoted so model text cannot cause a syntax error, and the quoted tokens joined with OR (to verify when this tool is built: with the tokens implicitly ANDed, 9 of 12 natural-language test queries over 41 runbook chunks returned nothing in a throwaway harness); each hit with handle, title, last updated, snippet to 600 chars. A closed incident returns its whole close note with CI, category and close code. Never tuned against specific documents: the agent must read past the top hit. | 3k tokens |
| `get_topology` | CMDB CIs and relationships, Dynatrace entities, Datadog `peer.service` tags | `entity` (any alias), `hops?` (1 or 2, default 1) | Matching CIs with every alias candidate and its match method, owner and whether active, support group, operational status, external ids; edges up to `hops<=2` by a recursive CTE, each with type, direction, CI status and the source it came from, so the sources' disagreements stay visible. Flaws included. Two-hop traversal is built in step 7 (R2). | 40 edges |
| `get_oncall` | PagerDuty services, policies, schedules | `service` or `policy` | Escalation levels, who is on call at now, escalation timeout | 0.5k |
| `read_chat` | Slack | `channel?`, `since`, `until`, `text?`, `thread_ts?`; only when tags are on: `act?` | Messages with handle, UTC time and ts, display name, text with mentions resolved, reply count at now; replies when `thread_ts` given | 60 msgs |
| `check_status_page` | PayCore | `provider?` (omitted, it lists known pages) | Indicator, incidents, updates as of now, from the latest snapshot published before now | 0.5k |

`list_alerts` and `list_changes` take no topology filter: the whole day fits in one capped call, and a CMDB-scoped filter would hide the release that the missing CMDB edge is meant to make the agent find (R2). Similar incidents are BM25 hits, not a per-category similarity score (R3). No embeddings in v1 (D4); the spikes in section 6 put hybrid retrieval behind a config flag, never a model choice.

No tail tool. No full-file read.

---

## 5. Scorer, minimum viable

About 200 lines. Inputs: the report JSON, the run log (every tool call and the handles returned), the manifest, the answer key.

Deterministic: (1) citation integrity: fabricated (neither in the manifest nor minted in this run's logged `shown[]`, `referenced[]` or `ic:note` handles), unseen (not in any `shown[]` this run; a handle only ever referenced counts as unseen), future (`t_known >= as_of`); fabricated and future must be zero; `ic:note` handles minted in this run are accepted and are classed with chat, so an `ic:note` cannot on its own support a causal link, and a link supported only by chat and notes counts as unsupported; (2) cause and trigger match 0, 0.5, 1 via the alias table; (3) fact recall over required facts visible at `as_of`, each with at least one non-chat handle; (4) trap table pass/fail by role and status; (5) owner check; (6) confidence sanity: wrong leading above 0.6 is a hard fail, right leading below 0.6 at 14:52 is a soft fail, no cap at 14:40.

One judge call: yes/no checklist with quotes required, separate system prompt: the four mechanism items, the required actions, the harmful top-three list.

Validation: score three hand-written reports (correct; "it is the checkout deploy"; "it is the database") and the no-tools baseline (one call with the trigger, the Slack transcript and alert titles). The scorer must separate them; the baseline should fall for at least two traps. Then three agent runs at 14:52 with tokens, tool calls and wall time per run. That table is the "how we know it works" section of the write-up.

Day 2: state continuity (every status change between checkpoints cites a handle dated in the gap), the 14:40 checkpoint, the held-out variants (section 12), tool calls until first correct leading hypothesis.

---

## 6. Build sequence with gates

No time estimates: we track time against the deadline and call cuts as needed (below).

| Step | Produces | Acceptance |
|---|---|---|
| 0. Confirm language; create repo skeleton (section 8); `uv init --python 3.12`; Makefile with all eight targets as stubs; commit | skeleton | `make data` and `make lint` run (empty); `git check-ignore` confirms fetched specs are ignored |
| 1. Data contracts | `smalls/contracts/`: handle grammar and the function that computes handles; manifest schema; `scenario/facts.yaml` with entities, every alias per source, the people table from 1.2, zones and anchor epochs. The report schema and answer key move to step 7. | A hand-written manifest line validates. `facts.yaml` lists every alias in 1.4 and 1.5 and every person in 1.2. |
| 1b. Schema authorities | `bash schemas/fetch.sh`; `schemas/extract.py` writing `schemas/<vendor>/extracted/*.schema.json` and field lists; `smalls_data/lint_schema.py` | One minimal hand-written record per extracted schema validates, using the corrections in 2.7. ServiceNow incident validates against `schemas/servicenow/sample-incident-get.json`; other ServiceNow tables run in field-list mode. |
| 2. Scenario YAML | `scenario/larkspur-checkout.yaml` | Every row of 1.4 present with `sig_id` or class; connection budget and curves present. **Gate A: a human read-through, 20 minutes.** |
| 3. Generator | `smalls_data/gen/`: clock rendering per source zone; metric curves to Datadog JSON; log noise templates plus signal lines; Datadog events from a monitor state machine; renderers for Slack, ServiceNow, PagerDuty, Jenkins, Dynatrace from content YAML; manifest writer; `smalls_data/lint.py` | `make data` builds the slice; `make lint` passes; counts within 20 percent of the targets in section 2 and under every ceiling |
| 4. Workers (parallel with 3) | `scenario/content/` YAML for chatter and filler (section 7) | Worker outputs pass the future-token lint and alias lint; main session reads every message once |
| 5. Signal content by hand | everything in 2.6, including the three history incidents, the six changes and the two runbooks, so the slice covers deploys, prior incidents and runbooks | Rendered files contain each item; the first-occurrence timestamps match 1.4 |
| 6.0 Data fixes | Data fixes 1 to 10 from `docs/decisions/step6-decisions.md` section 3, in that order, before any tool: sampling that keeps the incident's shape, future state out of served files, metric handles, authoring labels, saved searches, Splunk mapping, the DBA paste, the missing alert, the two `t_known` values, the triggers | Regenerated; `make data`, `make lint` and `make test` pass |
| 6a. Ingestion | In the order of the build table in `docs/decisions/ingestion-decisions.md`: the compose app service and CLI skeleton (`Dockerfile`, `compose.yaml`, `.dockerignore`, `smalls/cli.py` with `smalls ingest` and `smalls tag`); the store in `smalls/tools/store.py` (DDL, `clock`, the `visible` views, `integration.yaml` reader, loader limited to `sources:`, path guard, a check that nothing under `smalls/` imports `smalls_data`, ingest report); adapters in `smalls/tools/ingest/` for all 17 `sources:` keys, raw logs first; visibility (versions, fields withheld because they are not true at `now`) and the store half of the leak test; templates and sampling (the D6 masker, the template id, `keep_1_in`); the tagging pass with its committed cache, the bundle-string lint and the hand-labelled sample, which is committed before the first `make tag` | Parity: all 5,861 manifest rows match one store row each on handle, path, locator, `t_utc`, `t_known`, `valid_from` and `valid_to`, with no extra rows; `native` equals the served bytes; an empty clock shows nothing; the store half of the leak test passes at 14:40 and 14:52; two ingests hash the same; zero tag cache misses on the committed bundle and ingest opens no socket; the same parity passes with tags off, the default, and with tags on; the container runs the tests |
| 6a-tools. Tool layer to the first agent run | Reading only the store, in order: `search_logs` (rows and stats by minute, host or level, traces folded to 3 frames, 30-row default, footer with total and how to narrow, sampled counts labelled, SPL echo); **agent-loop code may start here**; 6b and step 7 overlap agent work. `list_alerts`. `list_changes`. `read_chat`, `get_record`, the `smalls tools call <name> --now 14:52 ...` CLI, the tool-output half of the leak test at 14:40 and 14:52, the result envelope and the error contract. Each tool takes its tag inputs from section 4 when tags are on | Leak test passes at 14:40 and 14:52. **First end-to-end 14:52 agent run.** |
| 6b. Rest of step 6, alongside the agent | `search_knowledge` and the source inventory; `query_metrics` and `check_status_page`; template `group_by` | A scripted solve path of about 15 calls reaches the cause. **Gate B: a fresh session with only the CLI does a blind read-through at 14:52, 20 minutes. If the cause is not findable, fix the bundle, not the agent.** |
| 6c. Run records at the first run, alongside 6b | The run-record format in `eval/runs/<run_id>/`, the matrix runner, arms A and C, the handle validator, process metrics (eval layers 1, 4a and 5 in 6.1). | The first run writes a complete run record; the regression set runs from here on |
| 7. Remaining sources, tools, key, scorer | Report schema (3.1) and answer key schema (3.3) moved from step 1; ServiceNow full (live incident as version snapshots with journal and audit rows, closed corpus, CMDB, users, audit), PagerDuty, remaining Confluence pages, PayCore v1; `_internal/answer_key.yaml` for 14:52; `itsm_query`, `get_oncall`, `get_topology` with `hops<=2`; the small retrieval eval (about 10 queries phrased as an incident commander would ask them, recall@1 and MRR, a sanity check, not a benchmark); `eval/scorer.py`; three hand-written reports in `eval/reports/`; `eval/baseline.py` | Scorer separates the three reports; baseline falls for two traps; the scorer scores every run from here on (layers 2 and 3) |
| 8. 14:40 checkpoint | `triggers/1440.md`; ~6 `sys_audit` rows; PagerDuty state derived; continuity check; the claim-support judge with about 20 claims checked by hand and the agreement reported (layer 4b); arm D if the scale render is ruled in (6.1) | leak test at 14:40 |
| 8b. Vector spikes S1 to S3 | Embedding client and committed cache, hybrid `search_knowledge` behind a flag, evaluation in both modes (6.2) | each spike's exit criterion in 6.2; the decision and its numbers go in the write-up |
| 9. Remaining traps | F5 lines, pay-03 flapping, SolarWinds, nginx sample with retry signature, marketing synthetic and CHG0031880, #sre-oncall chatter. Base additions that pre-registered variants need are supplied by the held-out authoring session as unlabelled estate background, only if a variant is attempted (see `eval/heldout/README.md`). | nine traps live; robustness evals (layer 6) run at both checkpoints |
| 10. Controls, approaches eval, development runs, submittable | no-tools baseline; contrarian control (the nine discriminators written as rules in a throwaway prompt); two trigger-framing perturbations ("confirm it's the DB", "give me the checkout rollback plan"); alias shuffle; the approaches eval, arms A to E (6.1); the full matrix at 5 runs per cell at 14:52 and 3 at 14:40, sized after the first run measured tokens per run; sample output; write-up draft with its tables generated from `eval/runs/`; README verified from a clean clone; tag `v1-submittable`; freeze tag `agent-v1` | a stranger runs `make demo` without a key and `make run` with one |
| 11. Held-out h1 | authored in a separate session from `eval/heldout/cards/h1.md`; acceptance per `eval/heldout/README.md`; `make heldout` once | result reported whatever it is |
| 12. Held-out h2 | only if h1 is run and reported, and there is time | same |
| 13. 15:15 stretch | triggers/1515.md, resolution versions, PayCore v2 and v3, Jenkins #413, CHG0031910, customer incidents, kill-sessions dip | only if everything above is done |
| 14. Stretch spikes S4 and S5 | Semantic search over record summaries for the `text=` filter of `list_changes` and `list_alerts`; a local keyless embedding model (6.2) | only if everything above is done |

Status 2026-10-13: the data of steps 7 and 9 and the cheap parts of step 13 (PayCore v2 and v3, late customer incidents) are generated and the 14:40 and 14:52 answer key exists (`docs/DATA.md` 72 to 95); the step 7 tools, report schema and scorer are not built. Step 6a is built (`docs/decisions/ingestion-decisions.md`; judgment calls and measured numbers in `docs/DATA.md` 98 to 125): the compose app service, the store, adapters for all 17 sources with parity on all 5,861 manifest rows, the store half of the leak test at 14:40 and 14:52, templates and sampling, and the tagging pass with its committed cache and a measured floor of 0.90. The tools of 6a-tools are not built.

Nothing is cut now: the ruling on 2026-10-13 is to keep everything and to revisit the cut order only if time runs short. If it does, cut in this order: 14 (S4 and S5), then 13, then 12, then the traps in 9 other than the marketing incident. Keep held-out h1: nothing else evidences generalisation, and if it cannot be run the write-up says "no held-out evidence" rather than presenting the alias shuffle as a substitute. Never cut the scorer, the baseline, the contrarian control, the leak test, the regression set or the sample output.

### 6.1 Evals from the first run

Evals start with the first end-to-end agent run, not at step 10. From then on every change to the prompt, the tools or the data is judged by them, and the tables in the write-up are generated from `eval/runs/`, never written by hand. Each layer answers a different question; a result in one layer never stands in for another.

| # | Layer | Question | Measure | From |
|---|---|---|---|---|
| 1 | Tool layer, no model | Is ingestion correct, and can the future leak? | Handle and `t_known` parity, the leak test, caps; tag coverage, and the precision of applied tags on a hand-labelled sample | step 6 |
| 2 | Retrieval check | Does `search_knowledge` find the obvious document? | About 10 queries, recall@1 and MRR | step 7 |
| 3 | Outcome | Did the agent get it right? | The scorer against the answer key: root cause, traps avoided, recommended actions | step 7 |
| 4 | Grounding | Is every claim backed by what it cites? | (a) the validator: every cited handle exists and was visible; (b) claim support: a judge checks that each claim's handles support it, about 20 claims also checked by hand, agreement reported | (a) step 6, (b) step 8 |
| 5 | Process | Did it investigate efficiently? | Tool calls, repeated calls, tokens per cited handle, time to the first correct hypothesis, budget hits | step 6 |
| 6 | Robustness | Is it steerable by a bad trigger, or brittle to names? | Trigger-framing perturbations, alias shuffle, contrarian control, at both checkpoints | step 9 |
| 7 | Approaches and scale | Does tool-based retrieval beat stuffing, and does cost stay flat as data grows? | Arms A to E below | A and C from step 6; B and E in step 10; D once a scale render exists |
| 8 | Generalisation | Does it work on an incident it was not built on? | Held-out h1 and h2 (section 12) | after `agent-v1` |

**Run records.** Each run writes `eval/runs/<run_id>/`: the config (arm, checkpoint, agent commit), the trace with `shown[]` and `referenced[]` per call (A1), the report, the scores, and input tokens (cached and uncached), cost, wall time and tool calls. One command runs a matrix of arms, checkpoints and repetitions. A small regression set runs on every prompt or tool change: arm C, 14:52, 3 runs. The full matrix runs before `v1-submittable` at 5 runs per cell. Results are a mean with the spread between runs, never a single run and never a significance claim.

**Approaches eval** (step 10). The same agent under different context strategies; it extends the no-tools baseline.

| Arm | Context strategy | Data |
|---|---|---|
| A | No tools: the trigger, the Slack transcript and the alert titles in one call (the baseline) | bundle |
| B | Everything served, in one long-context call, no tools | bundle, about 350k tokens |
| C | The agent with tools (what ships) | bundle |
| D | The agent with tools | scale render: the same scenario with log sampling at 1:1 |
| E | Everything served, no tools | scale render; it does not fit, so report its size, or truncate it to the window and score that |

C against B asks whether tools cost accuracy when the data could fit; C against D whether the agent's cost stays flat when the logs grow about twentyfold; E where stuffing breaks. Arms D and E need a scale render, which sits outside the size ceiling and waits on a ruling; until then they are designed, not run, and the write-up says so. The scale render is the development incident at a different sampling rate: it tests cost and robustness to volume, never generalisation.

**Tags ablation.** Tags are off by default. Once the agent runs, the regression set (arm C, 14:52, 3 runs) runs with `SMALLS_TAGS` off and on, and the write-up reports what turning them on buys: outcome score, fabricated and unseen citations, tool calls and tokens to the first correct leading hypothesis. This is the guard against tags as agent theatre: if they do not help, the write-up says so and they stay off. Making `on` the default would be a new ruling made on those numbers.

### 6.2 Vector spikes

Vectors are not in v1 (D4). The spikes test the production path without changing the tool interface. Only knowledge chunks are embedded: runbook and KB sections prefixed with the page title and its "Last updated" line, one chunk per closed incident (short description, CI, category, close code, close notes), one chunk per work note. Logs, metrics and chat are never embedded.

Defaults: Voyage `voyage-4-lite` at 512 dimensions, `input_type` `document` for chunks and `query` for queries, called with stdlib `urllib` (`POST https://api.voyageai.com/v1/embeddings`), not the SDK. A committed embedding cache outside `bundles/`, keyed by `sha256(model, dimension, input_type, text)`, also holds the retrieval-check queries, so tests and `make demo` never touch the network. Exact cosine in numpy over the chunks the `visible` view returns, no ANN index; numpy lives in the optional `retrieval` dependency group. Reciprocal Rank Fusion with k = 60. The mode is config, `SMALLS_RETRIEVAL=bm25|hybrid`, never a model choice; each hit says which retriever found it and its rank in each list. Without `VOYAGE_API_KEY`, `search_knowledge` falls back to BM25 and its footer says so.

| # | Spike | Exit criterion | h |
|---|---|---|---|
| S1 | Embedding client and committed cache | A second run makes zero API calls. Tests pass with the network off. A missing key gives BM25 with a footer note. | 1 |
| S2 | Hybrid `search_knowledge` behind the flag | The leak test passes in hybrid mode. Hits label their retriever. Every BM25 hit in the top k is still reachable in hybrid mode at 2k. | 1 |
| S3 | Evaluate | The retrieval check (layer 2) runs in both modes, and arm C runs in both modes in the approaches eval (layer 7). The decision and its numbers go in the write-up. | 0.5 |
| S4 | Stretch: semantic search over record summaries (change short descriptions, commit messages, alert titles, incident short descriptions) for the `text=` filter of `list_changes` and `list_alerts` | A query worded differently from the summary ("retry" against "resilience") finds the record that keyword search misses | 1 |
| S5 | Stretch: a local keyless model | Install size and first-run download measured; adopt only if it works offline after the first run and adds under about 200 MB | 1 |

---

## 7. Worker delegation

Workers are `Agent` calls on a small, fast worker model. They write content, never vendor JSON. The main session writes the brief, reviews every output once, and runs the lint. Workers never see the answer key, the scenario YAML's `sig_id`/`kind` fields, or the trap list.

### 7.1 What to delegate

| Task | Count | Output file |
|---|---|---|
| #inc-checkout-latency chatter between the fixed messages | ~35 messages | `scenario/content/slack/inc-checkout-latency.yaml` |
| #sre-oncall, 14:00 to 15:30 | ~15 messages | `scenario/content/slack/sre-oncall.yaml` |
| ServiceNow closed noise incidents, last 6 months, with realistic close notes | ~8 | `scenario/content/servicenow/incidents-noise.yaml` |
| ServiceNow noise change requests, the day and the week | ~12 | `scenario/content/servicenow/changes-noise.yaml` |
| Customer-raised incidents from 15:05 | 8 | `scenario/content/servicenow/incidents-customer.yaml` |
| Journal filler on INC0048811 (reassignment notes, status pings) | ~8 | `scenario/content/servicenow/journal-filler.yaml` |
| Noise Jenkins builds, last 7 days, across 4 jobs | ~12 | `scenario/content/jenkins/noise-builds.yaml` |
| nginx user agents, paths, referrers | lists | `scenario/content/nginx/noise.yaml` |

### 7.2 Intermediate content schema

```yaml
# slack
channel: inc-checkout-latency
messages:
  - at: "14:36:00"              # UTC clock; the generator renders ts
    author: ben.kowalski         # handle from facts.yaml; the generator maps to an opaque U-id
    text: "we deployed checkout-api 2.14.3 at 14:28, that's gotta be it. @alice.nguyen can we roll back?"
    thread_of: null              # or "14:47:12/elena.rossi"
    reactions: [{name: "+1", count: 3}]
# servicenow incident
- opened_at: "2026-09-30 11:02"  # Eastern; the generator stores it as-is and computes UTC for the manifest
  short_description: "..."
  description: "..."
  cmdb_ci: inventory-service
  priority: 3
  assignment_group: "Commerce Platform"
  caller: "Customer Support"
  state: Closed
  close_code: "Solved (Permanently)"
  close_notes: "..."
```

The generator assigns numbers, sys_ids, timestamps, mention ids, and renders vendor shapes.

### 7.3 Brief template (one per task)

1. Who you are writing as: personas with role, voice, and two example lines each, from `facts.yaml`.
2. The fixed messages already in the channel with their times. Write around them; do not restate them.
3. Beat sheet: what is true at each minute from the agent-visible facts only (alerts firing, the rollback in progress, the DB full). Never the cause.
4. Must-say list: specific nicknames and local-time habits (Priya and Jordan write Eastern times; Alice and Sam write UTC; Elena writes neither).
5. Must-not-say list: `3.8.0`, `retry`, `retries`, `timeout`, `8000`, `PayCore`, `PSP`, `idle in transaction`, `holding connections`, `authorize`, plus the future-token list for the checkpoint. Theories are allowed; explanations are not.
6. Voice rules: short, lower case is fine, occasional typos, emoji sparingly, a joke or two, one lunch message, one 404 link. No exposition. Nobody summarises the incident.
7. Output: the YAML schema above, exact count, `at` strictly increasing.

### 7.4 Review checklist for the main session

Run `make lint` (future tokens, alias usage, monotonic times, count). Then read every message once for: an explanation that solves the puzzle; a character knowing something they cannot know yet; tone that reads as generated; times in the wrong zone for the speaker. Expect about one in six items to need an edit.

---

## 8. Repo layout

```
CLAUDE.md                         conventions and the doc map
docs/                             every document; no markdown outside it unless a tool or the generator reads it
  PLAN.md                         this file
  HANDOFF.md  DATA.md  requirements.md  schemas.md
  decisions/  research/           rulings; research and design docs
pyproject.toml                    uv project: smalls, smalls_data
Dockerfile  compose.yaml  .dockerignore   the app service only: the same commands in a container; later services join compose.yaml
smalls/
  cli.py                          the smalls command: ingest and tag now, tools call with the tools
  contracts/                      report schema, handle grammar, manifest schema, answer key schema
  tools/                          the eleven tools, caps, handle minting, CLI
    store.py                      in-memory SQLite store, clock, visible view, loader, path guard
    ingest/                       one adapter per source: clocks via integration.yaml, folding, versions
      tagging/                    profiles, cache reader, the smalls tag command, config holding the pinned classifier version
  (agent and UI later; the agent ports from spikes/agent/)
smalls_data/
  gen/                            generator modules, one per source
  lint.py                         invariants (section 9)
scenario/
  facts.yaml
  larkspur-checkout.yaml
  content/                        authored and worker YAML; never served
  variants/h1.yaml  h2.yaml           held-out; truths live in eval/heldout/cards
bundles/
  larkspur-checkout-2026-10-13/   generated; one folder per source; _internal/ hidden
    integration.yaml              served config: sources, zones, Splunk mapping, sampling rates
  h1/  h2/                        held-out bundles; not for agent sessions
tags/
  cache.jsonl                     committed tag cache, content-addressed, no record text; outside bundles/
eval/
  scorer.py  baseline.py  contrarian.py  reports/
  tags/labels.yaml                hand-labelled sample for tag precision, committed before the first make tag
  runs/<run_id>/                  one run record each: config, trace with handles per call, report, scores, tokens, cost, time
  heldout/                        cards, criteria.yaml, designs.md, runs; not for agent sessions
Makefile                          data, lint (data phase); ingest, tag, docker-test, leak-test (step 6a); tools-smoke, score, demo, run, heldout (stubs until later)
schemas/                          fetch.sh, extract.py, <vendor>/extracted/, servicenow/sample-incident-get.json (documented in docs/schemas.md)
smalls_data/lint_schema.py        schema conformance lint (invariant 11)
spikes/agent/                     self-contained LangGraph spike behind docs/research/agent-design.md
embeddings/                       committed embedding cache for the vector spikes, keyed by sha256(model, dimension, input_type, text); outside bundles/
```

Generated bundles are committed, so a reviewer can read them without running the generator.

---

## 9. Invariants the lint enforces

1. Connection sums per minute stay within 197 and match the budget in 1.3.
2. Load matches pool size by Little's law within 20 percent at 14:29, 14:31, 14:33.
3. Sequence numbers run in time order: Dynatrace display ids, PagerDuty incident numbers, ServiceNow numbers, Jenkins build numbers per job.
4. Every Splunk alert `_raw` equals the file line byte for byte.
5. Nothing visible before its checkpoint; no decisive item within 60 s of a checkpoint.
6. Every `sig_id` and class in the answer key resolves to at least one manifest row visible at that checkpoint.
7. No served file contains a future token for its checkpoint, a `sig_id`, `kind`, or the string `trap`; no record dated before the incident day contains `stale`, `decoy`, `red herring` or `misleading`.
8. Rendered local times re-derive to the UTC in the YAML for every source zone, using only the served `integration.yaml`.
9. Every alias in `facts.yaml` appears at least once in the bundle, and no entity name appears in a source whose alias table does not list it.
10. Leak test: every tool, widest window, latest `t_known` before `now`, every epoch and ISO timestamp in any output (nested fields included) before `now`, and no future token for the checkpoint, at every checkpoint. The lint checks the served-file half now: every bundle file sits under a listed source (`triggers/` is harness input), and no record version visible at a checkpoint carries an epoch or ISO time at or after it; Slack thread summaries (`latest_reply` and the reply counts) hold export values and are recomputed at `now`. The store half arrives with step 6a and the tool-output half with step 6a-tools.
11. Schema conformance: every generated JSON record validates against its extracted schema or field list in `schemas/`; unknown fields fail except where `docs/schemas.md` records a deliberate deviation.
12. Exact strings: every log template that claims a vendor message (HikariCP, PostgreSQL, F5) renders text that matches the pinned source or reference verbatim.
13. Size budget: at most 5,000 raw log lines, 250 structured records and 11 vendor sources in a bundle, counted by the rules in section 2; every noise class in the scenario YAML names the trap it serves or is tagged `background` or `defect`.
14. Visibility re-derives: every manifest handle and `t_known` re-derives from the served bytes by a per-source rule, checked by `smalls_data/parity.py` (lint and test). There is no `t_known >= t_utc` rule: changes are correctly visible from creation, before their work starts.
15. Sampling keeps the shape: against the unsampled population, kept units per 10-minute bucket and in total sit in a binomial band of population / N for the rate in the served `sampling:` block (a rate that changes mid-window fails), and line counts correlate with the population curve (Spearman above 0.8, 14:20 to 15:10) at the narrowest of 1, 2, 5 or 10-minute buckets at which a correct constant-rate sampler passes in 95 percent of draws; classes too sparse for any width get the band only (agreed on 2026-10-13: per-minute is out of reach at the ruled rates, `docs/DATA.md` 46). Every served log record matches the `match` pattern of its class.
16. No bundle strings in prompt text: prompt text on the agent side, which now includes the tag profiles (instructions and option descriptions), contains no bundle string; the list is built from `scenario/facts.yaml`. The lint is `smalls_data/prompt_lint.py`, built in step 6a; the step 10 control in section 12 extends the same lint to every bundle's `facts.yaml`.

---

## 10. What not to build

Real API clients, auth, webhooks, a BigPanda account. A BigPanda-grade correlation engine (ours is a time window and an entity join inside `list_alerts` and `get_topology`). A universal parser. SPL or ServiceNow query parsers. More than one held-out variant before the `v1-submittable` tag, and never a third company or stack. Logs beyond about 5,000 lines. A difficulty knob. Nagios. A second document store. A tail tool. A vector database server. A database server: the store is in process, and Postgres is the production path, not v1. A generative model at ingest (summaries, parsing, extraction): the only model near ingestion is a closed-set classifier whose labels code validates. Tags as evidence or as default filters. A graph database. A SQLite or DuckDB extension downloaded at runtime. GraphQL, or GQL or Cypher written by the agent. GraphRAG. Growing the knowledge corpus: the closed-incident corpus stays as sections 2 and 2.6b define it.

---

## 11. Definition of done for the data phase

- `make data && make lint && make leak-test` pass on a clean checkout.
- Gate B passed: a person found the cause at 14:52 with only the CLI in about twenty minutes.
- Scorer separates the three hand-written reports and the baseline falls for at least two traps.
- The slice bundle and `_internal/` are committed; the held-out cards and `eval/heldout/criteria.yaml` are committed, with their commit hash recorded, before `agent-v1`.
- A one-paragraph note in `docs/DATA.md` on what is hand-written, what is generated, and why the dataset is small, for the write-up.
- `docs/schemas.md` provenance table is complete for every source in the bundle, and `make lint` includes the schema conformance check.

---

## 12. Held-out evaluation

Three independent reviews settled this; they were removed once acted on, and the full h3 to h5 designs they produced are in `eval/heldout/designs.md` (not for agent sessions). Keep one deep development bundle. Do not build a difficulty ladder: one item per rung gives no curve, size is not difficulty, and after tuning the development bundle is the easiest incident for this agent. The right shape is **same difficulty, different cause**: each held-out variant keeps the estate, noise and tools and makes at least one lesson of the development bundle false, so a failure points at the lesson the prompt over-learned. Which lesson each variant falsifies is written only in its card.

**Lessons.** Each card names the lessons of the development bundle that it falsifies. They are listed only in the cards.

**What is pre-registered.** Five cards in `eval/heldout/cards/` with opaque ids and a shared `criteria.yaml`, committed before any agent code. h1 and h2 are same-day variants (signal events change, the estate does not). h3, h4 and h5 are new-day incidents in the same estate, written so they can be built later or cited as designed, not built. Priority after the `v1-submittable` tag: h1, then h2, then h3.

**Controls that make a held-out result readable** (cheaper than any bundle, all in step 10):
- no-tools baseline on every bundle: one call with the trigger, the chat and the alert titles;
- contrarian control: the development bundle's discriminators written as rules in a throwaway prompt; it must fail the held-out variants or they test nothing;
- two trigger-framing perturbations at 14:52, where the incident commander asserts a wrong cause;
- alias shuffle, a lint that no bundle string appears on the agent side (built from every `facts.yaml`), and a served-output lint;
- a proximity baseline: rank changes by time window and shared CI, the way Root Cause Changes does, to show each decoy really ranks first.

**Discipline.** A single author cannot make held-out data independent of the author; the honest claim is held out from tuning, not from authorship, and git must be able to prove the order. Agent sessions never read `eval/heldout/`, `scenario/variants/` or `bundles/h*/`. Held-out data is authored in a separate session that cannot see agent code, prompts or run transcripts. Freeze tag `agent-v1` covers prompts, tools, loop, report schema, scorer, judge prompt and criteria. `make heldout` refuses a changed tree, runs once (5 runs on headline cells, 3 elsewhere), stamps provenance into append-only run files, and the write-up table is generated from them. Any later agent change is a new tag and both results are shown.

**Gates before any held-out bundle is built.** The agent emits schema-valid reports at 14:40 and 14:52 with carry-forward and reaches the cause with non-chat support in at least 4 of 5 development runs; the UI demo path works and a sample output is committed; a write-up draft exists; the tag `v1-submittable` exists; there is time; the card was committed before `agent-v1`. Stop rules while building: two acceptance cycles, no prompt or tool changes meanwhile, and a half-built variant is never presented.

**Development evals are not held-out evidence.** The eval layers in section 6.1 (1 to 7) run on the development incident the agent is tuned against, so they show it works here, not that it generalises; only held-out (layer 8) evidences generalisation, and the deeper the development evals go, the more it is needed.

**What the write-up may say.** Counts, never percentages, never a total across bundles, never the words accuracy, generalises, robust or validated. With one bundle: works on this incident at these checkpoints, beats a no-tools call, cites nothing fabricated or future. With h1: follows the evidence on this estate rather than a learned answer. With h1 and h2: within one estate, the verdict follows the evidence when the evidence changes, and the controls show the test can catch a prompt that memorised the development incident. Cross-estate generality is never claimed; the path to it (real ServiceNow and alert exports through the same report contract and scorer) goes under improvements.

---

## 13. Agent and UI acceptance criteria (stub; the agent plan is a separate document)

Two reviews flagged that this plan had no criteria for the part the assessment weights most. These are the done conditions the data and tool layer must serve. The agent design is settled in `docs/research/agent-design.md`, with its rulings in sections 7 and 8.5; its section 5 is the agent build plan, and `spikes/agent/` is the LangGraph spike it was tested on.

**Agent, done when:** measured through the eval layers in section 6.1, each run recorded in `eval/runs/`: it emits a schema-valid report at 14:52 from trigger 2 with zero fabricated or future citations; the leading hypothesis cites at least one non-chat handle per causal link; it states contradicting evidence for each rejected hypothesis; it names the owner from on-call, not the CMDB; it lists what not to do; it stops on its own within a call budget; carry-forward from 14:40 cites handles dated in the gap. Layer by layer: the regression set (arm C, 14:52, 3 runs) passes the scorer (layer 3) with zero fabricated or future citations (layer 4a); claim support is judged and hand-checked (layer 4b); process metrics show it stops within budget (layer 5); the framing perturbations do not steer it to a wrong leading cause (layer 6); arm C beats arm A (layer 7). The build order is the build plan in `docs/research/agent-design.md` section 5, with the critique fixes in its section 8.4. Every prompt change is committed with the id of the run that motivated it.

**UI, done when** a fresh pair of eyes gets "what to do now, what not to do, who owns it, why" in 30 seconds, and any claim reaches its native record in one click. Contents: an answer-first header (cause, confidence, do now, do not do, owner); an evidence drawer showing the native vendor record; a timeline; a trace with per-call hypothesis deltas; the 14:40 to 14:52 diff; replay from a committed run log (`make demo`, no API key); a copy button for the stakeholder update. The quickstart a reviewer runs first is the replay of the hard investigation, not a smaller bundle.

**Write-up, done when** all seven required sections exist plus "how we know it works" (generated table), "where the model is used and where code is better" (normalisation, correlation, replay and scoring are code; hypotheses, human-written times and chat are the model; the Final Note asks for this) and "how I used AI coding tools" (the independent reviews, worker briefs behind leak lints, fresh-session blind reads).
