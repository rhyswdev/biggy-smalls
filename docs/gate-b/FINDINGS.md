# FINDINGS: checkout 504s, 2026-10-13, as of 14:52 UTC

Started 22:28:40 UTC wall clock (2026-10-14). Finished about 22:32 UTC by the machine clock (see section 5).

## 1. The cause (confidence 0.85)

**What is broken.** payments-gateway runs out of database connections. `PaymentService.authorize` opens a DB transaction and writes the `authorization_attempt` row. It then calls PayCore *inside that same transaction*, so the connection is held for the whole PSP call. payments-gateway **3.8.0** shipped at 10:19–10:25 today under CHG0031877. Its PAY-2231 change ("PSP client resilience") raised the PSP timeout from 3 s to 8 s and added 3 retries with 1/2/4 s backoff. The retry wraps the PayCore call inside the existing transaction. One authorization can now hold a pgw_app connection for up to 39 s, where before it held one for at most about 3 s.

The pools (4 nodes × 35) fill with "idle in transaction" sessions, and pay-db-01 hits its 197 usable connections. The gateway then times out waiting for a connection, and its DB health check fails. That makes the F5 eject members in turn. checkout-api's 10 s client timeout to the gateway expires first, so customers get 504s (or 502s when no member is healthy).

**What set it off.** PayCore started responding slowly or timing out on part of our traffic at about **14:25**. Some calls still succeed in about 300–450 ms, but thousands time out at 8 s. Under 3.7.x this would have failed fast, as it did in July (INC0045210). Under 3.8.0 each slow call is retried 4 times while holding a DB connection. This is the scenario the design note's risk section reasoned about, but it only considered checkout's timeout and missed the held DB connection. PayCore's status page is green, but it was last updated at 10:33, so it says nothing about 14:25 onwards.

**What made it worse.**
- The daily `payments-reconciler` job starts at 14:30 by timer and holds 40 connections on the same DB. It is routine and ran fine on 10-11 and 10-12, but it takes headroom away at the worst moment.
- Customers and checkout retrying: submit rate rose from about 840/min to about 1,440/min from 14:36.
- The F5 health monitor checks DB health, so starved nodes are ejected one at a time. That pushes load onto the remaining nodes.
- 14 minutes went on the checkout-api rollback, which could not help: the failure started before 2.14.3 finished deploying.
- Datadog lost its Postgres connection at 14:33, so the DB connection graphs have been blank since then.

**What I can't tell from here:** why PayCore is slow. That is on PayCore's side and we have no telemetry for it.

## 2. The evidence

| Link | Handles | What it shows |
|---|---|---|
| 3.8.0 shipped this morning with PAY-2231 | snow:CHG0031877, jk:payments-gateway-deploy#412, jk:payments-gateway-deploy#412/0735879b, /3a25df06 | Release deployed 10:19–10:25. Commits change PayCoreClient, RetryPolicy and application-prod.yml. Backout plan: redeploy 3.7.9, about 6 min. |
| New PSP settings are live on all 4 nodes | log:pay-01/5ad16319, log:pay-02/b782ec04, log:pay-03/1531376a, log:pay-04/2c56f0d1 | `timeout-ms=8000 retries=3 backoff-ms=1000,2000,4000` at startup on each node. |
| Retry runs inside the DB transaction; worst case 39 s | conf:KB0010502 | Design note: "the same transaction still writes the authorization_attempt row". Old timeout was 3000 ms. Worst case is 39 s. Settings can be reverted with a config-only release. |
| Old behaviour failed fast, with no DB effect | snow:INC0045210 | July PayCore outage: 3000 ms timeout, "no knock-on to pay-db-01… connections stayed at baseline". |
| PayCore slow from about 14:25 | log:pay-02/33353de9, q:a75cdd53, q:86689f37 | First slow PSP responses (over 5 s) at 14:25. Estimated 8 s timeouts: 700 (14:25), 2,400, 4,600, 3,900, 3,100. |
| One authorization holds its connection for about 39 s | log:pay-01/61c1af32, log:pay-01/ebfd5293, log:pay-01/336ab539 | Thread exec-17: attempt 1/4 timed out at 14:26:12, failed "after 4 attempts in 39025ms". Hikari flagged a connection leak on the same thread from `PaymentService.authorize` → `JpaTransactionManager.doBegin`. |
| Some PayCore calls still succeed quickly | log:pay-04/462a55d1, log:pay-01/9ab174fa | `psp_ms` of about 300–460 with attempts=1 at 14:40–14:50. This is a partial PayCore degradation, not an outage. |
| DB connections hit the limit; sessions are app-held | slack:inc-checkout-latency/cd4b848c (= snow:jn/ec7656f6), metric:sum:postgresql.connections{host:pay-db-01}by{db}@1350-1452, conf:PGW-DB-CONN-TIMEOUT | 133 pgw_app sessions "idle in transaction", wait_event ClientRead, on `insert into authorization_attempt`. Payments DB connections went from 24 to 140 at 14:30 and 165 at 14:32, then the metric goes blank from 14:33. The limit is 197. |
| Gateway pool starved | log:pay-02/c603e930, q:bde39f15 | `total=35, active=35, idle=0, waiting=12` at 14:31. Estimated connection-timeout errors: 400 (14:30), then 4,498, 3,340, 9,712. |
| Success rate falls from 14:26, before the checkout deploy finished | metric:avg:payments.authorize.success_rate{env:prod}@1350-1452 | 98.5 until 14:24, then 96.3 at 14:26, 85 at 14:30, about 42 by 14:44. |
| Checkout sees gateway timeouts and 503s | q:5f290865, log:checkout-api-6d8c9b7f4d-q8m4z/2dcead3a, log:checkout-api-7f5c8d9b6-z2x8c/fad874d2 | `POST payments-gw/authorize timed out` → 504, and `returned SERVICE_UNAVAILABLE` → 502. |
| Alert order starts in payments | dd:ev7820515412 (14:27), dt:P-26101350 (14:29), dd:ev7820516604 (14:30), spl:alert/891f48a7 (14:32), dd:ev7820518073 (14:33:30), spl:alert/a678ed4b (14:34) | Gateway latency, then auth success rate, then pool exhaustion, then Postgres refusals. |
| Reconciler is routine but adds 40 connections | jk:payments-reconciler#2311, jk:payments-reconciler#2310, jk:payments-reconciler#2309 | Timer-started at 14:30 every day, runs about 45 min, completed fine on the last two days. |
| Customer retry load | metric:sum:trace.servlet.request.hits{service:checkout-api,env:prod}by{resource_name}@1350-1452 | Submit rate steady at about 1,680 per 2 min until 14:34, then rising to about 2,870 per 2 min by 14:46. |

## 3. The other theories

- **checkout-api 2.14.3 (Ben).** Not supported. The deploy ran 14:27:30–14:29:05 (jk:checkout-api-deploy#897). PSP slowness (log:pay-02/33353de9, 14:25) and the success-rate drop (14:26) came first. The 14:42 rollback (jk:checkout-api-deploy#898, slack:inc-checkout-latency/3d5f9f2c) changed nothing.
- **pay-03 is dying (Sam).** Not supported. The F5 ejects *every* member in turn, each for about 90 s, with health-check `503 Service Unavailable`: .33/pay-03 (log:ltm/173b82c5, log:ltm/b2db1f86, log:ltm/2a783898), .31 (log:ltm/416e563f), .34 (log:ltm/9eed94dd), .32 (log:ltm/5b24e5dd). The 503 comes from the app's DB health check failing (log:pay-01/5fb8870f). SolarWinds sw:55012 is the same HTTP component check, not a hardware alarm, and it reset at 14:46. pay-03 also logs successful authorizations (log:pay-03/d949af99). IP→host mapping: snow:ci/pay-03.
- **Network, because the marketing site is down too (Kofi).** Not supported, and it is a separate incident. The marketing synthetic failed at 14:38/14:40 on cdn.larkspur.com, in the Marketing zone (dt:P-26101377, dd:ev7820520800). That is right after Kofi's own CHG0031880 started at 14:36: lower the DNS TTL and *purge the marketing CDN cache*. Its test plan says "marketing synthetic stays green". Payments traffic does not go through that CDN, and PayCore calls are completing in about 300 ms (log:pay-01/9ab174fa).
- **PayCore status page green, so not PayCore (Kofi).** Wrong reading. The page was last updated at 10:33 (psp:paycore@v1), and our logs show thousands of 8 s PayCore timeouts from 14:25. PayCore *is* the trigger, but our 3.8.0 behaviour is what turns it into an outage.
- **The DB is the problem (Elena's data / Ben's question).** The DB is the place that saturated, not the cause. No slow queries: the sessions are idle in transaction waiting on the app (slack:inc-checkout-latency/cd4b848c). The runbook (conf:PGW-DB-CONN-TIMEOUT) classes this as an application problem.
- **Redis (Sam / old runbook).** Not supported. Redis fragmentation (dd:ev7820017640) has flapped since the previous day and recovered at 14:41. Alice notes sessions moved off it (slack:inc-checkout-latency/e02ccf95).
- **Traffic surge (Kofi).** A consequence, not the cause. Submit rate was flat until 14:34 and only rose after failures began (the hits metric above).
- **payments-reconciler.** A contributor, not the cause: same time every day without incident (jk:payments-reconciler#2310).
- **Other noise:** node-east-14 CPU (dd:ev7820510301, analytics batch, recovered), search-indexer cron (dd:ev7820508241), ord-01 disk (dd:ev7820500714) and WIN-RPT-07 volume (sw:54977). None of these is on the payment path.

## 4. What to do

**Next 10 minutes** (Payments Engineering, Priya Raman driving):
1. **Stop holding connections across PayCore calls.** Ship the config-only release the design note allows (conf:KB0010502): `payments.psp.retries=0`, `payments.psp.timeout-ms=3000`. That puts back the pre-3.8.0 fail-fast behaviour that kept the DB at baseline in July. If a config-only release can't go out in about 5 minutes, use the CHG0031877 backout: redeploy 3.7.9 with payments-gateway-deploy, about 6 min, rolling with F5 drain. Expected effect: idle-in-transaction count falls, Hikari `waiting` goes to 0, auth success rate climbs back toward the share PayCore is actually answering.
2. **Pause payments-reconciler #2311** to free 40 connections, if Payments Engineering confirms a rerun later is safe. It is a timer batch job, not customer-facing.
3. Once step 1 is live, Elena may kill the remaining idle-in-transaction pgw_app sessions to clear the backlog. Before step 1 they refill within seconds. Killing them also rolls back `authorization_attempt` rows for calls PayCore may still approve. The idempotency key is the order id, so a later retry should not double-charge, but Payments Engineering should know this.
4. Open a ticket with PayCore with our timeout counts from 14:25 (q:86689f37) and example order ids. Don't wait for their status page.
5. Kofi: treat the marketing site as a separate incident and back out CHG0031880 per its plan (restore TTL 3600 and check the CDN purge).
6. Status page: "Some card payments are failing or slow; we are working on it."

**What not to do:**
- Don't drain pay-03, or any single node. All four are flapping for the same reason, and draining one starves the other three.
- Don't raise `max_connections` or the Hikari pool size. More connections held for 39 s only moves the wall, and the DB is already at its limit.
- Don't restart Postgres or the gateways as a fix. They refill within seconds while PayCore is slow and the retries are on.
- Don't roll checkout-api back any further.
- Don't chase Redis or the network.

**Owner: Payments Engineering**, with Priya Raman as the hands-on lead now. How I know:
- CMDB support group for Payments Gateway is Payments Engineering (snow:ci/Payments-Gateway). The listed owner, Marcus Okafor, is inactive.
- The runbook says idle-in-transaction app holds are owned by Payments Engineering (conf:PGW-DB-CONN-TIMEOUT).
- Priya holds Payments level 2 and acked #8812 (pd:oncall/fafc2fbc, slack:inc-checkout-latency/af7783ee). Level 1, Tom Beckett, did not answer (pd:oncall/ec9956fd, slack:inc-checkout-latency/07ef5485).
- Priya was assigned CHG0031877 and reviewed PAY-2231. Bring in Dana Whitfield, who wrote PAY-2231, for the config values.
- Elena (Database Engineering) supports on the DB side. Alice stays IC.

## 5. How it went

- **Time:** started 22:28:40 UTC and finished about 22:32 UTC by the machine clock. calls.log shows 22:28:47–22:30:48 for the calls. The working time felt longer than the clock suggests, so treat the clock times as the machine's, not elapsed effort.
- **Calls:** 54 lines in calls.log, including about 8 discovery or error calls. The first call failed because the schema lists `rationale` as required but the CLI rejects it. A bare `14:15` was rejected as a time. Several calls used deliberately bogus values to get the host, sourcetype, metric and scope inventories.
- **Where I could have been misled:** the alert noise (redis, node-east-14 CPU, cron), the checkout deploy landing two minutes after onset, and the green PayCore page. The "slow PSP response" count drops to zero after 14:35. That looks like recovery, but it is because slow calls now hit the 8 s timeout instead. The timeout count is the series to read.
- **What the tools didn't let me do:** see PayCore's own latency or error metrics (no PSP-side series in the metrics inventory), or read the payments-gateway source to confirm `@Transactional` scope beyond the stack trace and design note. I also couldn't see Postgres connection metrics after 14:33, because Datadog lost its connection (the series gap). I couldn't see the July baseline of pgw_app connections, and there were no 2026-10-12 DB points for comparison.
- **What I expected but didn't see:** a PayCore-side latency metric or synthetic, a monitor on pgw_app "idle in transaction" count, and an alert on Hikari `waiting > 0`. Any of those would have pointed at the cause around 14:27. I also expected the staging soak of 3.8.0 (12 Oct) to have needed a PayCore-slowness test. The design note's risk analysis checked checkout's timeout but not how long DB connections are held.
