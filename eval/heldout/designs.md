# Held-out designs: h3, h4 and h5 in full

Kept from the review that produced them. Its incident S is h5, incident M is h4 and Alt-F is h3; the counterfactual it mentions is h1. The cards in `cards/` are the pre-registered summary; this file is the detail a held-out session builds from. Hour estimates in it are historical.

# Verdict on the two incident types

**Both can be good tests, but neither is good as stated.** Built from the one-line descriptions, both mostly re-test what the hard bundle already tests. They would cost six to nine hours and add little evidence. A held-out bundle earns its cost only if two things hold:

- it reverses at least one rule of thumb that the hard bundle rewards;
- its deciding evidence is a kind the hard bundle does not contain.

The test I would apply before building any of them: **which rule of thumb, learned on the hard bundle, would make an agent fail this one?** If the answer is "none", do not build it.

**Type (a), a regression from an old deployment: weak as stated, worth building only in a specific form.**

The hard bundle already is a regression from an older deployment. 3.8.0 ships four hours before the symptoms and stays latent until PayCore slows. The plan moved that deploy to the morning precisely so that "time-window change matching now picks the checkout deploy, which is the point" (dataset-review, P6). A second bundle with "an old deploy plus a recent decoy" re-tests the same lesson.

It becomes a separate test only if it has all four of these:

1. **The culprit is weeks old, outside any default change window.** An agent that lists today's changes, or this week's, never sees it.
2. **The decoy matches on time and on topology:** it is a change on the same CI as the symptom. That is how Root Cause Changes actually scores a change. The hard bundle's decoy matches on time only, and it is on a different CI.
3. **Nothing external triggers it.** "Why today?" must be answered from the system's own history.
4. **It reverses a hard-bundle rule.** In the hard bundle, restarting is a partial fix and a trap. Here it must be the right first mitigation, and the most recent deploy of the affected service must be innocent.

Design M does all four. It is still the most expensive of the candidates and the closest to the hard bundle. **I rate its value as medium.**

**Type (b), a cause deduced from a runbook: strong, but only if the runbook holds a fact the model cannot know.**

A runbook that encodes general SRE knowledge tests nothing. A frontier model already knows that "idle in transaction" means connections held by the app, and that WAL growth means a replication slot or a stuck archiver. The hard bundle already has exactly that kind of runbook (PGW-DB-CONN-TIMEOUT, reachable through search_knowledge).

The version that is a separate test works like this:

- The evidence points at something that is meaningless without Larkspur-specific knowledge: which keys in this store have no TTL, which batch job removes them, and which actions are forbidden here because no other copy of the data exists.
- The runbook is the only place that knowledge lives.
- **The cause is not a change record at all.** The hard bundle, the counterfactual and type (a) all have a change as the cause, so an agent that always blames a change would pass all three.

Design S does this. **I rate its value as high.**

## Where I disagree with the main session's preliminary view

| # | Main session's view | My view |
|---|---|---|
| 1 | A difficulty ladder is a better evaluation story. | **Partly.** With one bundle per rung and three runs each, there is no curve to report. Bundle size also does not predict how hard a bundle is for an LLM agent. What makes a bundle hard is the number of hops, how strong the decoys are, and whether the deciding evidence needs a query the agent would not run by default (a long lookback, or noticing that something is absent). M as designed is probably *harder* for the agent than the hard bundle's cause step, because it needs a 45-day lookback and reasoning from missing restarts. S is probably easier. Name bundles by the behaviour they test, report pass or fail per behaviour, and claim generality only across incident types within one estate. |
| 2 | Same-domain bundles are much cheaper, about 2 to 3 hours for small and 4 for medium. | **I agree they are cheaper; I disagree with the numbers.** Two costs are missing. First, generalising the generator: it and the lints are written around one date, one `t0` and single-day windows, while S needs 10-day metric curves and M needs 45-day curves plus two new log sourcetypes. Second, keeping bundles consistent with each other: number sequences, history corpora, and the state of the estate after INC0048811. Realistic figures are about 4 h for S and about 5.5 h for M, plus about 1 h for whichever is built first (see Cost). |
| 3a | A regression from an old deployment tests resistance to proximity bias. | **Disagree as stated.** Proximity bias is already the hard bundle's main lesson about changes. Type (a) is worth building only in the four-condition form above. |
| 3b | A cause absent from prior incidents but in a runbook tests retrieval over knowledge rather than similarity over history. | **Agree with the aim.** It only separates good agents from bad ones if the knowledge is local. |
| 4 | "Demo" versus "eval" depends only on whether you tune on it. | **Mostly agree, with two additions.** First, it also depends on whether you *look* at it: reading S's traces and then adjusting the prompt makes S development data. Second, designer leakage: specs and answer keys written after watching the agent on the hard bundle will bend toward what you saw it do. Write and commit the counterfactual, S and M specs and keys **before** agent tuning starts (about 1 hour), and quote the commit hash in the write-up. |
| 5 | Sequencing protects the agent. | **Agree, with two additions.** Freeze the specs early (see 4). Extend the answer-key and report contracts in the contracts hour, not when S is built (finding 1). |

The proposal said: "Even if this is just noise, these are good tests." A noise bundle costs the same to build as a good one.

---

# Incident S (small): full design

**Title: "Logged out at checkout."** Tuesday 20 Oct 2026. One checkpoint, at 14:20Z.

**What it tests:**

- retrieving local knowledge from a runbook;
- concluding that **no change record** caused the incident;
- resisting a decoy that matches on time, topology and a plausible mechanism;
- not dismissing redis.

The last two reverse rules of thumb the hard bundle teaches: "redis is a red herring" and "the cause is a change".

## The true chain

**The store.** Sessions and persistent carts share one Redis Cluster:

- Redis 7, three primaries with `maxmemory 4gb` each (12 GB in total), plus replicas.
- Datadog tag `redis_cluster:sessions-prod`; StatefulSet `redis-sessions` in `k8s-prod-east`.
- The old `redis-sessions` VM CI stays **retired**, which is the hard bundle's stale edge.
- Eviction policy `volatile-lru`, chosen on purpose so that carts can never be evicted.

**What the store holds:**

| Data | Key | Size | Expiry | Notes |
|---|---|---|---|---|
| Sessions | Spring Session | about 2.5 KB | sliding TTL of 30 min | about 380k at peak (0.95 GB) |
| Persistent carts of signed-in customers | `cart:{customerId}` hashes | about 1.4 KB | **none, by design** | the only copy of the data; about 6.3M keys, 8.8 GB after the nightly sweep |
| CSRF and rate-limit keys | | 0.4 GB in total | TTL | |

The only thing that removes carts is the Jenkins job `commerce-batch/cart-sweeper`. It runs daily at 07:00Z and deletes carts idle for more than 30 days, about 215k per run.

**Cause.** cart-sweeper's last build is #1416 on Mon 12 Oct at 07:00:04Z: SUCCESS, 702 s, "deleted 214,880 cart keys idle > 30d". There are no builds after it. *Why* it stopped is an open question. A candidate explanation is in the data (a Jenkins LTS upgrade CHG on the afternoon of 12 Oct) but is not part of the answer key.

**Mechanism.**

1. Keys with no TTL grow by about 0.33 GB per weekday and 0.22 GB per weekend day, with no nightly drop.
2. Normal peak demand is 10.4 GB, 87% of maxmemory. Demand first exceeds 12 GB at about 12:40Z on Sunday 18 Oct.
3. Under `volatile-lru`, Redis evicts only keys that have a TTL. That means sessions, CSRF and rate-limit keys, never carts.
4. The effective session lifetime is 30 min × (room left for sessions ÷ session demand). It is about 24 min at Sunday's peak, about 16 min at Monday 16Z and about 8 min at Tuesday 14Z.
5. A user idle for longer than that, for example while fetching a card at the payment step, comes back to "session expired". Guest carts, which are held in the session, are lost.

**Blast radius.**

- At Tuesday 14Z, 4 to 6% of `/api/checkout/*` requests return 401, and conversion is down.
- **Synthetics stay green, because they never sit idle.**
- Signed-in customers' persistent carts are intact.

**Why today.** By Tuesday there are two more days of backlog than on Monday, and the Tuesday morning traffic ramp adds session demand on top.

## Trigger (14:20Z, Alice, in #inc-checkout-logouts and on INC0049420)

> Support has 30+ tickets since yesterday afternoon: customers logged out in the middle of checkout, some guest carts emptied. Synthetics are green. Ben wants to roll back this morning's checkout-api 2.15.0, it bumped spring-session. Do we roll back? What is going on?

## Sources and volumes (7 of the hard bundle's sources)

| Source | Content | Volume |
|---|---|---|
| Splunk raw `checkout_api/<pod>.jsonl` | 6 pods, 08:00 to 14:20Z, sampled. Version flips 2.14.3 to 2.15.0 at 10:15 to 10:21. | about 800 lines |
| Splunk raw `k8s_events` | 2.15.0 rollout | about 15 |
| Datadog monitors and events | 5 monitors: evictions (new), fragmentation (reused, flapping), checkout conversion anomaly, checkout 5xx (quiet), checkout synthetic (green). Events collapsed by list_alerts. | 5 monitors, about 25 events |
| Datadog metrics | 10 days, hourly: `redis.mem.used`, `redis.mem.maxmemory`, `redis.keys.evicted`, `redis.keys`, `redis.expires`. Today at 1-minute resolution: the checkout 401 rate. | 6 series |
| Jenkins | cart-sweeper #1400 to #1416 (daily, ending 12 Oct); checkout-api-deploy #905; 2 noise builds | about 20 builds |
| ServiceNow | INC0049420 plus 6 journal entries; 12 customer incidents (Monday 4, Tuesday 8; the INC description says "+21 more"); history INC0043630 (new), INC0047003 and INC0048811, with the corpus reused; about 6 changes today plus the 12 Oct Jenkins upgrade CHG; CMDB reused plus a sessions-cluster CI | about 60 records |
| PagerDuty | 1 incident on "Commerce Platform", 5 log entries | 6 |
| Slack | #inc-checkout-logouts about 22 messages; #platform-alerts about 6 bot posts | about 28 |
| Confluence | REDIS-SESSIONS-MEM (new); KB0009871 (stale, reused); 2 unrelated pages reused | 4 |

Totals: about 815 log lines and about 140 structured records. Dynatrace, SolarWinds, PayCore, F5, nginx, pg_stat_activity and the Postgres log are not used.

## Signal items with times (UTC; ET = UTC−4)

| When | Item | Source | Kind |
|---|---|---|---|
| 2026-05-12 | **INC0043630**, "Customers logged out during checkout". checkout-api 2.9.0 changed the session cookie domain. A work note quotes a log sample, "no SESSION cookie on request". Fixed by rollback. Follow-up CHK-1655: "log missing cookie vs cookie present but session not in store separately". | ServiceNow | trap (bait), and the origin of the discriminating log line |
| 2026-05-14 | REDIS-SESSIONS-MEM, "Last updated 14 May 2026, Commerce Platform" | Confluence | signal |
| until 10-12 | cart-sweeper daily at 07:00Z, SUCCESS, about 215k deleted. **#1416 at 2026-10-12 07:00:04Z is the last build.** | Jenkins | signal (an absence) |
| since 10-12 | `redis.keys − redis.expires`: the trend changes from flat with a daily sawtooth to a steady climb of about +220k per day | Datadog | signal |
| weeks | fragmentation monitor flaps about every 2 h; INC0047003 closed it as benign | Datadog, ServiceNow | trap |
| 10-18 12:50 | "[Warn] sessions redis: keys being evicted" goes to #platform-alerts. It goes OK overnight, Warn Mon 10:40, OK Tue about 04:00, **Warn Tue 08:35 and still active**. The monitor message links REDIS-SESSIONS-MEM. | Datadog, Slack bot | signal |
| 10-19 15:00 to 22:00 | 4 customer incidents ("logged out at payment", "cart emptied"), closed with "advised to clear cookies" | ServiceNow | signal (onset before the deploy) |
| 10-20 08:40 onward | `{"level":"WARN","logger_name":"c.l.c.web.SessionCookieFilter","message":"session cookie present but session not found in store; treating as anonymous","version":"2.14.3",...}`. A handful of these appear **on 2.14.3 pods before 10:15**. | Splunk | signal |
| 10:15:20 | checkout-api-deploy #905, VERSION 2.15.0, CHG0032318 (standard). changeSet: "CHK-2290 bump spring-session-data-redis 3.2.4 → 3.3.1" (touches `build.gradle` only), "CHK-2277 address autocomplete", "CHK-2301 log pattern tweak". Rollout 10:15 to 10:21. | Jenkins, ServiceNow, k8s | trap |
| 13:00 to 14:20 | The WARN rate rises with traffic. 401s on `/api/checkout/*` reach 4 to 6%. **No "no session cookie" lines.** | Splunk, Datadog | signal |
| 13:41 | Datadog anomaly "Checkout conversion below forecast", Warn | Datadog | signal (optional) |
| 13:55 | INC0049420 raised by the Service Desk, P2, 09:55 ET | ServiceNow | |
| 14:02 | PagerDuty #8891 on Commerce Platform; Alice acknowledges at 14:03 | PagerDuty | |
| 14:07 | Ben: "2.15.0 went out at 10:15 with the spring-session bump. obvious suspect, I can roll back in 5" | Slack | trap |
| 14:09 | Sam: "synthetics all green fwiw" | Slack | trap |
| 14:11 | Alice: "this smells like the May one, cookie thing, rollback fixed it" | Slack | trap |
| 14:13 | Sam: "redis has been yelling all weekend in #platform-alerts but that's the frag alert, it always flaps" | Slack | trap |
| 14:15 | Sam: "if it is redis i can flush the session keyspace, takes a second" | Slack | trap (harmful action) |
| **14:20** | **Checkpoint and trigger** | | |

## The runbook, and how its steps map to the tools

The runbook is **REDIS-SESSIONS-MEM: "Sessions Redis: memory pressure, evictions and logouts."** It is titled by symptom, so search_knowledge finds it on "logged out", "session expired" or "evictions". It is also linked from the eviction monitor.

It is not a giveaway. It has two diagnostic branches, and one of them points at a checkout release, so **the decoy is inside the runbook**.

| Runbook step | Tool call |
|---|---|
| 1. Confirm evictions: `redis.keys.evicted{redis_cluster:sessions-prod}`. Any non-zero value is abnormal. | query_metrics |
| 2. Compare `redis.mem.used` with `redis.mem.maxmemory`. | query_metrics |
| 3. Find which keyspace is growing: plot `redis.keys` against `redis.expires` over 7 days. | query_metrics (2 series) |
| 3a. If keys with no expiry grow and the 07:00 UTC drop is missing, persistent carts are not being swept. Check the last successful run of `commerce-batch/cart-sweeper`. | list_changes(text "cart-sweeper", 14 days) |
| 3b. If keys with an expiry grow faster than traffic, session lifetime has changed. Check recent checkout-api and storefront releases for `spring.session.timeout`. | list_changes, then get_record on the changeSet |
| 4. Symptoms: idle users are logged out and guest carts are lost. **Synthetics will not show it.** | |
| 5. Mitigate: run cart-sweeper with `CATCHUP=true` (rate-limited, about 15 min per 2M keys). As a stopgap, `CONFIG SET maxmemory 5gb` per primary; nodes have 8 GB, so never go above 5 GB because an AOF rewrite forks. **Never FLUSHDB or FLUSHALL. Never `allkeys-*`. Carts exist only here.** | (actions) |
| 6. Escalate to the Commerce Platform PagerDuty service. | get_oncall |

## Traps and their discriminators

| Trap | The naive reading | What separates it |
|---|---|---|
| **T1. checkout-api 2.15.0** (matches on time, topology and mechanism) | "A spring-session bump hours before logouts; roll it back." The May incident reinforces this: same symptom, fixed by rollback. | Evictions start Sunday and the Monday tickets come before the deploy. The WARNs appear on 2.14.3 pods before 10:15. The signature is "cookie present, session missing" (a server-side loss), whereas May's was "no SESSION cookie". The changeSet touches only `build.gradle`. `redis.expires` grows normally, so runbook branch 3b is ruled out. |
| **T2. "Redis alerts are noise"** | The fragmentation flap and INC0047003 say redis alerts are benign, and Sam lumps the eviction Warn in with them. | They are different monitors. Fragmentation has flapped for weeks with no change. Evictions are new since Sunday and track demand against maxmemory. |
| **T3. Harmful fixes** | Flush the session keyspace, restart redis, or switch to `allkeys-lru`. | The runbook: carts have no backing store. A flush logs out every customer, and with `allkeys-*`, carts would be evicted. |
| T4 (optional, cut first). Green synthetics | "Nothing is systemically wrong." | Synthetics finish in under a minute and never idle. |

## Expected output at 14:20

**Hypotheses:**

- **Leading, cause, about 0.75.** Sessions are being evicted from the sessions cluster because carts with no TTL have accumulated since cart-sweeper's last run on 12 Oct, and under `volatile-lru` only TTL keys are evicted. Entities: sessions-cluster and cart-sweeper. No change record is named.
- **Rejected, about 0.1.** checkout-api 2.15.0 (T1 discriminators).
- **Unrelated.** The fragmentation monitor.
- **Open, low.** A change to the session timeout (ruled out by `redis.expires`).
- **Condition.** Two more days of backlog plus the Tuesday morning traffic ramp.

**Actions, ranked:**

1. Run cart-sweeper catch-up now (Commerce Platform). It frees about 2.4 GB in about 15 minutes.
2. As a stopgap, raise maxmemory to 5 GB per primary, within the runbook's limit, to stop evictions immediately.
3. Do not roll back 2.15.0. Do not flush, restart redis or change the eviction policy.
4. Find out why the sweeper stopped and restore its schedule. Alert when its last success is more than 26 h old, and page on evictions above zero.
5. Customer update: customers can sign back in. Guest carts lost since Sunday cannot be recovered. Persistent carts are safe.

**Open questions:**

- Why did cart-sweeper stop after 12 Oct? Is the job disabled, or was its trigger lost in the Jenkins upgrade?
- How many guest carts were lost?
- Is storefront equally affected?

**Owner:** Commerce Platform.

**Answer-key sketch.** This needs the contract extension in finding 1:

```yaml
checkpoints:
  "14:20":
    leading: {entity: sessions-cluster, cause_entity: cart-sweeper, change_any: none}   # naming a change record as the cause fails
    trigger: null
    condition: {judge: "cart backlog since 12 Oct pushes demand over maxmemory at the Tuesday peak"}
    required_facts: [F.evictions, F.mem_at_max, F.nonttl_growth_or_sweeper_absent, F.cookie_present_session_missing, F.onset_before_deploy]
    non_chat_required: true
    traps:
      checkout-2150: {match: {entity: checkout-api}, allowed: {status: [rejected]}}
      redis-frag:    {match: {entity: redis-fragmentation}, allowed: {role: [unrelated]}, absent_ok: true}
      prior-cookie:  {match: {incident: INC0043630}, allowed: {status: [rejected]}, absent_ok: true}
    owner: {any: ["Commerce Platform"]}
    judge:
      mechanism: [no_ttl_carts_accumulate, volatile_lru_evicts_ttl_keys_only, idle_users_logged_out, guest_carts_lost]
      actions:   [sweeper_catchup_in_top2, maxmemory_stopgap_within_limit, restore_schedule_and_alert, customer_update]
      harmful_top3: [rollback_checkout, flush_redis, allkeys_policy, restart_redis]
```

**Corpus lint.** No history incident, closed note or pre-checkpoint chat may contain `evict`, `maxmemory`, `sweeper` or `TTL`. This is how "absent from prior incident notes" is enforced rather than hoped for.

## What makes it small

- One checkpoint and seven sources.
- One service and one datastore; about 815 lines and about 140 records.
- Three core traps; about 10 to 14 tool calls on the solve path.
- No Dynatrace, no database, no external dependency.

## Cut to keep it small

- No storefront logs: Monday's tickets and the metric onset already show the problem predates the deploy.
- No nginx, no F5, no second checkpoint.
- No `sys_audit` rewind beyond one assignment row.
- No root cause for why the sweeper stopped; it stays an open question.
- No guest-cart accounting.
- Cut T4 before anything else.

## What would make it accidentally hard

- **The eviction monitor is missing.** The agent then has to think of redis from the log line alone. Keep the monitor. Removing it is a valid dial for making S harder.
- **Undiscoverable metric names.** There is no metric-listing tool, so `redis.keys.evicted`, `redis.keys` and `redis.expires` must appear in a monitor query or in the runbook.
- **Inconsistent naming.** If the sessions cluster's CI, its Datadog tag and the retired `redis-sessions` CI do not resolve through `get_topology`'s alias table, the agent concludes redis is not in the path.
- **The nightly drop gets smoothed away.** query_metrics rolls 10 days into 45 points, about 5.3 h each. The 12-minute drop at 07:00 vanishes. The usable signal is the **change of trend** on 12 to 13 Oct, which survives any rollup. Do not write an answer key that requires seeing the drop itself.
- **The absence is not visible.** "No builds since 12 Oct" must be reachable: list_changes needs a window of 14 days or more, plus a text filter.

## What would make it accidentally easy

- The eviction monitor's message, the INC description or any chat message mentions the sweeper.
- The runbook is titled by cause ("cart-sweeper failure").
- Someone in Slack asks "is the cart sweeper running?".

**Hours:** about 4 (see Cost).

---

# Incident M (medium): full design

**Title: "Order-service fell over during the freeze."** Friday 23 Oct 2026. Core checkpoint at 14:40Z; an optional second at 16:30Z.

**What it tests:**

- looking beyond the incident day and beyond the last deploy;
- rejecting a decoy that matches on time and on topology;
- using a natural experiment (two hosts, different uptimes);
- forecasting when it will recur;
- recommending a restart as the correct mitigation.

## The true chain

**Cause.** order-service 5.6.0 shipped Wed 23 Sep, 10:20 to 10:27Z. It is Jenkins `order-service-deploy` #187 with change CHG0030681, normal. One of its commits is "ORD-1187 cache promo eligibility lookups". That commit added a Caffeine cache keyed by (customerId, promoCode, cartFingerprint), with no `maximumSize` and no expiry. Every cart change produces a new fingerprint, so new keys keep arriving.

**Mechanism.**

1. Each instance retains about 0.28 GB per weekday, 0.30 GB on Fridays and 0.18 GB per weekend day. That is about 230k entries a day at about 1.2 KB each.
2. The JVM runs with `-Xms4g -Xmx4g`, G1 and a live-set baseline of 1.2 GB.
3. The weekly Wednesday release train restarted both JVMs every 7 days. Each instance therefore peaked at about 2.98 GB (75%) and reset. Nothing alerted.

**Exposing condition.**

1. The Peak 2026 change freeze started on Fri 16 Oct, after 5.7.0 shipped on 14 Oct.
2. ord-02 has been up since 14 Oct at 10:26Z. By Friday 13:30Z its retained-heap floor is about 3.59 GB (90%).
3. At that level, G1 has almost no room left for young-generation copying (its default to-space reserve is 10%).
4. The Friday morning traffic ramp then tips it into Full GCs, each 2.5 to 3.6 s, from 13:58.
5. At 14:18:40 it fails with `OutOfMemoryError: Java heap space`. It writes a heap dump, which takes 11.2 s, and because `-XX:+ExitOnOutOfMemoryError` is set, the JVM exits. systemd restarts it, and it is healthy at 14:19:36.

**The natural experiment.** On Mon 19 Oct, ord-01's order-service was restarted (INC0049330). Its `/var/log` had filled, the deleted `gc.log` was still held open by the JVM, and the restart released it. ord-01's floor is therefore about 2.34 GB (59%) on Friday, and it is fine.

**Blast radius.**

- From 13:58 to 14:21, checkout `POST /v2/orders` to ord-02 hits the 3 s client timeout.
- Checkout error rate is 6 to 9%, with about 16 s at roughly 50% while F5 marks ord-02 down. Orders are created before authorization, so customers are not charged.
- About 30 "order failed, please retry" tickets.

**Recurrence.** Without a fix:

- ord-01 reaches the same floor around **Wed 28 Oct, about 14:40Z**.
- ord-02, restarted on 23 Oct, reaches it around **1 to 2 Nov**.
- The freeze runs to 4 Jan.

## Trigger (14:40Z, Alice)

> Checkout errors from about 14:00 to 14:20, all timeouts calling order-service. ord-02 ran out of memory at 14:18 and restarted itself, it's fine now. Jordan says the only change on the ord boxes this week is last night's kernel live patch, CHG0032471, and CAB wants a yes or no on backing it out on both. We're in the peak freeze. What happened, will it happen again, and what should we do?

The trigger leaves out that ord-01 is fine. The agent has to find the asymmetry itself.

## Sources and volumes (8)

| Source | Content | Volume |
|---|---|---|
| Splunk raw `order_app/ord-0N.log` | Same custom logback pattern as the gateway; 13:30 to 14:40Z; request summaries, slow WARNs, 1 Hikari starvation WARN, the restart line | about 900 lines |
| Splunk raw `jvm_gc/ord-0N-gc.log` | JDK 17 unified logging. ord-02 13:50 to 14:25 (young collections every 1 to 3 s, about 35 Full); ord-01 13:50 to 14:40 (normal). **Do not sample within a file:** the `GC(n)` ids are sequential, so gaps would show; bound the time window instead. | about 1,000 lines |
| Splunk raw `syslog/ord-02` | journald lines: OOM, heap dump, exit, systemd restart | about 12 |
| Splunk raw `checkout_api` | "timed out after 3000ms" to order-service | about 400 |
| Splunk raw `f5/ltm.log` | pool_orders member ord-02 down at about 14:19:07 and up at about 14:19:40 (optional) | 2 |
| Datadog | 5 monitors (new: "ord heap > 85% for 15m"; reused: checkout error rate 7391255, checkout synthetic, redis fragmentation as noise). Metrics: `jvm.heap_memory` and `jvm.heap_memory_max` by host over **45 days hourly**; `jvm.gc.major_collection_count` by host for 2 days; checkout error rate today. | about 15 events, 5 series |
| Dynatrace | One problem on the OrderService process on ord-02 (GC or memory evidence, then a crash event); entities reused | 1 to 2 |
| Jenkins | `order-service-deploy` #186 to #190 (16 Sep to 14 Oct) with changeSets; 3 noise builds | 8 |
| ServiceNow | Live INC0049668 plus journal; the kpatch CHG; 5 order-service CHGs; about 4 noise changes today; INC0049330 (19 Oct); history including INC0048811 and S's INC0049420; about 5 customer incidents | about 70 |
| PagerDuty | #8926 (Checkout) with log entries | about 6 |
| Slack | #inc-checkout-orders about 30; #platform-alerts bot posts | about 35 |
| Confluence | ORDER-SVC-JVM runbook (generic); "Peak 2026 change freeze" page; reused pages | 4 |

Totals: about 2,300 log lines and about 130 structured records.

## Signal items with times (UTC; ET = UTC−4)

| When | Item | Source | Kind |
|---|---|---|---|
| 09-16 10:20 | #186, 5.5.4 | Jenkins, ServiceNow | noise |
| **09-23 10:20** | **#187, 5.6.0, CHG0030681.** changeSet: `Merge branch 'release/5.6.0'`; **`ORD-1187 cache promo eligibility lookups`** (affectedPaths `src/main/java/com/larkspur/orders/promo/PromoEligibilityCache.java` (add), `.../PromoEligibilityService.java`, `build.gradle` (adds Caffeine)); `ORD-1191 order status: carrier ETA`; `ORD-1179 bump hibernate-core to 6.5.3`; `chore: CHANGELOG 5.6.0`. The cache commit is by a non-persona author. | Jenkins | signal |
| 09-30, 10-07 | #188 5.6.1 and #189 5.6.2: innocuous changes; each restarts both JVMs | Jenkins | noise (each is a reset) |
| 10-14 10:20 | #190 **5.7.0**, CHG0031962: `ORD-1302 paginate order status endpoint`, `ORD-1288 bump spring-boot to 3.3.5` | Jenkins, ServiceNow | trap |
| 10-15 | "Peak 2026 change freeze: 16 Oct 00:00 ET to 4 Jan; exceptions via emergency CAB" | Confluence | context |
| 10-19 13:10 | **INC0049330** (P4, 09:10 ET), "ord-01 /var/log 95%". Work note: "removed old gc.log, df unchanged, java pid 2231 still holds the deleted file; restarted order-service on ord-01 09:10". Close: "space recovered; ORD-1311 fix gc log rotation". | ServiceNow | signal (natural experiment) |
| 45 days | `jvm.heap_memory` by host. Flat at about 1.25 GB until 23 Sep, then a weekly sawtooth peaking at about 2.98 GB with resets on the Wednesday deploys. ord-01 resets on 19 Oct; ord-02 climbs to about 3.6 GB. | Datadog | signal |
| 10-22 18:20 | "ord heap > 85% for 15m", Warn on `host:ord-02`, non-paging, to #platform-alerts; still Warn at the checkpoint | Datadog | signal (missed precursor) |
| 10-22 evening | Two Full GCs on ord-02 (visible in `jvm.gc.major_collection_count`), no errors | Datadog | signal |
| 10-23 06:00 to 06:25 | **CHG0032471**: "Apply kernel live patch (kpatch) for CVE-2026-41877 on ord-01, ord-02. Freeze exception. No reboot." Closed, Successful. | ServiceNow | trap |
| 13:42 | "ord heap > 85%" re-notifies (renotify interval) | Datadog | signal |
| 13:58:12 | `[2026-10-23T13:58:12.044+0000][info][gc] GC(41877) Pause Full (G1 Compaction Pause) 4089M->3671M(4096M) 2871.442ms` on ord-02 | Splunk gc | signal |
| 13:58 to 14:18 | About 35 Full GCs on ord-02, 2.5 to 3.6 s each; young collections every 1 to 3 s | Splunk gc | signal |
| 14:00 onward | checkout-api: `"message":"POST http://orders.prod.larkspur.internal/v2/orders timed out after 3000ms"` on all pods; error rate 6 to 9% | Splunk, Datadog | signal |
| 14:01 | Dynatrace problem on OrderService (ord-02); Davis root cause is the process itself, which is correct but shallow | Dynatrace | signal |
| 14:06 | Datadog 7391255 checkout error rate > 5% Triggered; PagerDuty #8926 to Alice; INC0049668 auto-created at 14:06:40 (10:06 ET) | Datadog, PagerDuty, ServiceNow | |
| 14:11 | Ben: "all the checkout errors are timeouts to order-service" | Slack | signal (chat) |
| 14:13 | Jordan: "only change on ord-* this week is last night's kpatch, CHG0032471. everything else is frozen" | Slack | trap |
| 14:15 | Sam: "patch hit both boxes at 2am. want me to back it out and bounce both?" | Slack | trap (harmful action) |
| 14:18:02 | ord-02: `HikariPool-1 - Thread starvation or clock leap detected (housekeeper delta=51s207ms).` | Splunk | trap (smells of a pool problem) |
| 14:18:40 to 14:18:56 | journald on ord-02 (sequence below) | Splunk syslog | signal |
| 14:19:36 | `Started OrderServiceApplication in 37.912 seconds (process running for 39.604)` on ord-02 | Splunk | signal |
| 14:21 | Checkout errors back to baseline | Datadog | signal |
| 14:24 | Sam: "ord-02 OOM'd and restarted itself" | Slack | corroboration |
| 14:27 | Ben: "grabbed the heap dump from ord-02, 4GB, loading it" | Slack | open question |
| 14:31 | Jordan: "CAB wants a yes/no on backing out the kpatch" | Slack | trap |
| 14:35 | Alice: "support has ~30 'order failed please retry' tickets" | Slack | signal |
| **14:40** | **Checkpoint and trigger** | | |
| 16:30 (stretch) | Ben: "MAT dominator tree: one ConcurrentHashMap under PromoEligibilityCache, 2.3 GB retained". Optional second trigger: "is it confirmed, and what's the freeze-exception fix?" | Slack | tests confidence update |

The journald sequence on ord-02, in order:

- `java[2231]: java.lang.OutOfMemoryError: Java heap space`
- `Dumping heap to /var/tmp/order-service/java_pid2231.hprof ...`
- `Heap dump file created [4093112837 bytes in 11.204 secs]`
- `Terminating due to java.lang.OutOfMemoryError: Java heap space`
- `systemd[1]: order-service.service: Main process exited, code=exited, status=3/NOTIMPLEMENTED`
- `Failed with result 'exit-code'.`
- `Scheduled restart job, restart counter is at 1.`

The journald format has no year and no zone, which is realistic mess for the mapper to handle.

## Traps and their discriminators

| Trap | The naive reading | What separates it |
|---|---|---|
| **T1. Kernel live patch** (time and topology; the top pick for a Root Cause Changes-style ranker) | "The only change on the affected CI, 8 hours earlier: back it out." | It was applied to **both** nodes, and only ord-02 failed. kpatch does not restart the JVM: there is no startup line on ord-02 since 14 Oct, and its uptime is 9 days. The heap climb predates the patch by weeks. GC thrash is bound by the heap. |
| **T2. 5.7.0, the last deploy** (the "last change on the CI" rule; reverses the hard bundle, where the culprit *was* the latest gateway deploy) | "The spring-boot bump nine days ago." | The identical weekly climb exists from 23 Sep to 14 Oct on 5.6.0 through 5.6.2. The floor is flat before 23 Sep. |
| **T3. "It recovered; transient"** | Close it, perhaps as a JVM blip. | ord-01's floor is 2.34 GB and rising about 0.28 GB per weekday, so it reaches the same level around 28 Oct. A restart only resets the clock. |
| T4 (optional). Pool or database | The Hikari WARN, checkout "timed out", and INC0048811 as the most similar incident. | "Starvation" is not exhaustion: the JVM was paused for more than 45 s. Only one node is affected, and it shares the database with the healthy one. |

**Harmful actions** (penalised if in the top three):

- reboot or back out the patch on both nodes at once, which is a total order outage at peak;
- roll back to 5.6.x, which still contains the cache and still breaks the freeze;
- raise `-Xmx` as *the* fix: an 8 GB VM leaves no room, and pauses get longer;
- close the incident as transient.

## Expected output at 14:40

**Hypotheses:**

- **Leading, cause, about 0.6 to 0.7.** A retained-heap leak in order-service since 5.6.0, most likely `PromoEligibilityCache` from ORD-1187. Weekly deploys masked it until the freeze. ord-02, up 9 days, crossed G1's limit at the Friday ramp. ord-01, up 4 days since the 19 Oct restart, is at 59%. Confidence should be moderate, because the heap dump is not yet analysed.
- **Rejected.** The kpatch; 5.7.0; the database or pool.
- **Condition.** JVM uptime beyond the weekly cadence because of the freeze.
- **Contributing.** The Friday morning ramp.

**Actions, ranked:**

1. Tell CAB **no**: do not back out the patch, and do not bounce both nodes.
2. Capture `jcmd <pid> GC.class_histogram` on ord-01 now, and analyse ord-02's dump to confirm the dominant class.
3. Restart ord-01 tonight, off-peak, after draining it from the F5 pool. Keep restarting so that no JVM runs longer than about 5 days until the fix ships. Forecast: ord-01 around 28 Oct, ord-02 around 1 to 2 Nov.
4. Ship a freeze-exception fix that bounds the cache (`maximumSize` plus `expireAfterWrite`). Ben builds it; Jordan takes it to emergency CAB.
5. Alert on the daily minimum of heap above 70%, and page on OOM restarts.
6. Customer impact: about 20 minutes of 6 to 9% checkout failures, with no charges because orders are created before authorization (verify).

**Open questions:**

- Which class dominates the retained heap?
- Does 5.6.0 have a config flag that disables the cache?
- Why did Thursday's 85% Warn not escalate?
- Do other services use the same pattern of unbounded caches?

**Owner:** Commerce Platform; Jordan for the freeze exception.

**Answer-key sketch:**

```yaml
  "14:40":
    leading: {entity: order-service, change_any: [CHG0030681, "order-service-deploy#187", "5.6.0"]}
    trigger: null
    condition: {judge: "JVM uptime beyond the weekly restart cadence because of the freeze"}
    required_facts: [F.heap_floor_climb_since_0923, F.full_gc_thrash_ord02, F.oom_exit_restart, F.ord01_restarted_1019, F.kpatch_no_reboot]
    traps:
      kpatch:      {match: {change: CHG0032471}, allowed: {status: [rejected]}}
      last-deploy: {match: {change_any: ["5.7.0", CHG0031962]}, allowed: {status: [rejected]}}
      db-pool:     {match: {entity: pay-db-01}, allowed: {role: [unrelated]}, absent_ok: true}
    confidence: {wrong_leading_max: 0.6, right_leading_min: 0.5}
    judge:
      mechanism: [unbounded_cache_retains_heap, weekly_restarts_masked_it, freeze_extended_uptime, g1_full_gc_then_oom]
      actions:   [do_not_back_out_patch, capture_histogram_before_restart, planned_restart_and_cadence, freeze_exception_bound_cache, recurrence_forecast]
      harmful_top3: [reboot_both_ord_nodes, close_as_transient, rollback_to_5_6, raise_xmx_as_fix]
```

## What makes it medium

- **Long-horizon reasoning:** 45 days of metrics, plus a list_changes window of six weeks with a text filter.
- **Two decoy changes:** one matches on time and topology, the other is the last deploy.
- **A per-host asymmetry the agent must discover itself.**
- **A forecast.**
- About 15 to 20 tool calls, eight sources and about 2,300 lines.

## Cut to keep it medium

- **Event and source cuts:** no promo-email traffic factor; no F5 flapping (two lines at most); no Postgres logs or pg_stat_activity (pay-db-01 is simply not in the story); no Dynatrace problem for ord-01.
- **History cuts:** no logs before Friday 13:30, so history comes from metrics, Jenkins and ServiceNow only; at most 5 customer tickets.
- **Checkpoint cut:** make the 16:30 checkpoint a stretch goal.

## What would make it accidentally hard

- `jvm.heap_memory` is not discoverable. Keep the "ord heap > 85%" monitor, whose query names it.
- query_metrics cannot split by host, or caps lookback at a week.
- list_changes cannot reach 45 days, or its 30-row cap truncates the result before a text filter applies.
- search_logs cannot parse the JDK `+0000` timestamps or journald's missing year.
- The generator adds so much noise to the heap series that the floor is not monotonic between restarts. Lint it.
- The date slips past 25 Oct, the end of UK summer time. London personas move to UTC+0 while New York stays at UTC−4, so `tz_offset` and human-written times shift. Keep M on 23 Oct.

## What would make it accidentally easy

- A commit message that says "unbounded".
- Chat saying "the promo cache never evicts".
- A Dynatrace title that says "memory leak".
- An OOM stack trace in the app log with a `UnboundedLocalCache` frame. That is also physically wrong: with `ExitOnOutOfMemoryError` the JVM exits before the app logs anything, so only journald carries the OOM.

**Hours:** about 5.5 (see Cost).

---

# Alternative, if any

Yes. **Alt-F, "The database that cried wolf": a false alarm caused by monitoring credentials.** It costs about 2 hours. It is the cheapest candidate and it tests the most distinct behaviour: **concluding, with evidence, that there is no customer impact and the monitoring is broken.**

- The hard bundle never tests this: there is always a deep real cause.
- The counterfactual, S and M never test it either.
- It is the failure mode a contrarian prompt tuned on the hard bundle will show: inventing a story.
- It also covers the requirements' "noisy alert storm where only some alerts matter" in its sharpest form, where none matter.

**When and why.** Thu 15 Oct 2026, two days after the hard incident, so everyone is jumpy. Gateway 3.8.1, the fix for INC0048811, shipped at 10:20Z (#414, CHG0031985).

**Cause.** CHG0031993 is a standard change, "Quarterly rotation of monitoring DB credentials", on the CI "Monitoring – Datadog", not on pay-db-01. It ran at 13:00 to 13:15Z. The role password rotated at 13:06:12. The Datadog agent's `postgres.d/conf.yaml` on pay-db-01 is templated by a follow-up step that did not run, so the agent keeps using the old password.

**Signals:**

- **The Postgres log, every 15 s from 13:06:12:**
  - `FATAL:  password authentication failed for user "datadog"`
  - `DETAIL:  Connection matched pg_hba.conf line 98: "host all datadog 10.20.0.0/16 scram-sha-256"`

  Meanwhile `connection authorized: user=pgw_app` and `orders_app` lines continue at normal rates.
- **Datadog:**
  - `postgres.can_connect` CRITICAL at 13:06:30, with the 28P01 text. In the hard bundle the same check carries 53300; that is the discriminator.
  - All `postgresql.*` series go null.
  - The connections monitor goes to No Data at 13:16:30.
  - PagerDuty "Database - Prod" pages Elena.
  - INC auto-created at P2.
- **Customer-facing health:** `payments.authorize.success_rate` is flat at 99%+, the checkout error rate is at baseline, synthetics are green, and Dynatrace has no problem open.

**Trigger (13:35Z, Alice):**

> pay-db-01 is alerting again, can_connect critical and Datadog has no connection data since ~13:06. After Tuesday nobody's taking chances. 3.8.1 went out this morning. Is the DB going down again? Roll back 3.8.1, or restart Postgres?

**Traps:**

| Trap | What separates it |
|---|---|
| "DB down" | The FATAL is for user `datadog` only; application roles connect normally. |
| Proximity and recency | 3.8.1 that morning, with INC0048811 as the top similar incident: the business metrics are flat. |
| Topology miss | The rotation change is on a monitoring CI, so topology matching never links it. |

**Harmful actions:** restart or fail over Postgres; roll back 3.8.1; kill sessions; send a customer outage notice.

**Expected output:**

- Leading cause: monitoring credential drift after CHG0031993. Customer impact: none.
- Actions, in order:
  1. Update the agent's credential and restart the **agent**, not Postgres.
  2. Downgrade the INC to P4.
  3. Mute the monitor during the fix.
  4. Add a post-rotation check to the standard change template.

**Contract.** This needs an `impact` field in the report (finding 1).

**Sources:** 6 (Datadog, Splunk with the Postgres log and a gateway sample, ServiceNow, PagerDuty, Slack, Jenkins). About 450 lines and about 60 records.

**A cheaper way to keep the regression theme instead.** A certificate time bomb, about 2.5 h. checkout-api 2.11.0's mTLS rollout to order-service used a client certificate issued by hand from Vault PKI with `ttl=2160h` on 24 Jul at 14:05:31Z. It was stored as a Kubernetes Secret not managed by cert-manager, and it expires **Thu 22 Oct at 14:05:31Z** (checked: +90 days). An F5 client-ssl profile change at 13:50Z is the time-and-protocol decoy. The arithmetic step is NotAfter minus 90 days, which gives the rollout date and the change. It is crisp and cheap, but it is a smaller test than M, because the error text names the class of failure. Verify the TLS alert JSSE sends for an expired client certificate before writing log lines.

**Recommendation.**

1. If you can build only one bundle beyond the counterfactual, build **S**.
2. If you can build two, build **S plus Alt-F**. Together they cover two answer types the hard bundle and the counterfactual cannot: "not a change" and "not an incident".
3. Build **M** only if time remains. Otherwise publish its frozen spec as "designed, not built".

---

# Overlap with the hard bundle and how to avoid it

| Dimension | Hard | S | M | Alt-F |
|---|---|---|---|---|
| Affected service | payments-gateway (VMs), seen through checkout | checkout-api and the sessions Redis (Kubernetes) | order-service (VMs) | pay-db-01 monitoring |
| Cause class | Code change plus external slowness | A missed operational task plus the eviction policy | An old code change plus uptime | Monitoring credential drift |
| Is a change record the cause? | Yes, 4 h old, same day | **No** | Yes, **30 days** old, outside any default window | Yes, but on a monitoring CI |
| Why now | PayCore EU slowness | The backlog crosses maxmemory at Tuesday's ramp | JVM uptime past the weekly cadence (freeze) | Rotation at 13:06 |
| What the proximity decoy matches on | Time only, on a different CI | Time, topology and mechanism | **Time and topology** | Time, plus the recency of Tuesday's incident |
| Prior incident's role | Most similar one has the wrong fix | Most similar one had a different cause, same symptom, and rollback fixed it | INC0049330 supplies the natural experiment | INC0048811 is the top match and is wrong |
| Runbook's role | Linked one is stale; accurate one is generic | Accurate one holds local facts, and the decoy branch is inside it | Generic; supports the mitigation | Not needed |
| Database's role | Looks guilty, is a symptom | Absent | Absent (a pool-flavoured red herring only) | Looks down, is fine |
| Redis's role | Red herring | **Where the cause lives** | Absent | Absent |
| Restart's role | Partial fix (a trap) | Harmful (redis) | **The correct immediate mitigation** | Harmful (Postgres) |
| External dependency | PayCore | None | None | None |
| Deciding evidence | Logs, changeSet, config two-hop | 10-day metric trend, an absence of builds, a local fact | 45-day sawtooth, per-host asymmetry, GC log | FATAL text against healthy business metrics |
| Hard-bundle rule it reverses | – | "Redis is a red herring"; "the cause is a change" | "Restart is a band-aid"; "the culprit is the latest deploy of the affected service" | "A database alert means a real problem upstream" |

**Guardrails.**

1. S, M and Alt-F must not reuse the hard bundle's distinctive surfaces: PayCore, the gateway pool's physics, the F5 gateway VIP flapping, the reconciler, or the strings `idle in transaction`, `53300` and `attempt n/4`.
2. Lead with different personas where it is realistic. S: Ben, Sam, Alice. M: Ben, Jordan, Sam. Elena, Priya and Tom are minor or absent.
3. **Reverse one learned rule per held-out bundle**, and give each reversal a discriminator from a non-chat source. That keeps them fair rather than gotchas. A person with only the CLI should solve each in about 20 minutes, which is the Gate B equivalent for each bundle.
4. Later bundles' histories include earlier incidents, closed (INC0048811 in all three; S's INC in M's). This is realistic, free, and gives similarity-retrieval bait. Lint every history corpus for each bundle's cause tokens.
5. Add a "Root Cause Changes" baseline: rank changes by time proximity times topology match, about 30 lines. It should rank the decoy first in the hard bundle, S and M. That checks each decoy is a real trap, and it gives the write-up a second baseline row next to the no-tools baseline.

---

# Physics and arithmetic checks

## Dates, zones and epochs

All checked programmatically with zoneinfo.

| Instant | Day | Epoch | ET | London |
|---|---|---|---|---|
| 2026-10-13 14:00Z (hard `t0`) | Tue | 1791900000 | 10:00 −0400 | 15:00 +0100 |
| 2026-09-23 10:20Z (5.6.0) | Wed | 1790158800 | 06:20 | 11:20 |
| 2026-10-12 07:00Z (last sweep) | Mon | 1791788400 | 03:00 | 08:00 |
| 2026-10-19 13:10Z (ord-01 restart) | Mon | 1792415400 | 09:10 | 14:10 |
| 2026-10-20 10:15Z (2.15.0) | Tue | 1792491300 | 06:15 | 11:15 |
| 2026-10-20 14:20Z (S checkpoint) | Tue | 1792506000 | 10:20 | 15:20 |
| 2026-10-23 06:00Z (kpatch) | Fri | 1792735200 | 02:00 | 07:00 |
| 2026-10-23 14:40Z (M checkpoint) | Fri | 1792766400 | 10:40 | 15:40 |
| 2026-10-22 14:05:31Z (certificate alternative NotAfter = 24 Jul + 2160 h) | Thu | 1792677931 | 10:05 | 15:05 |

- **UK summer time ends 2026-10-25 at 01:00Z; US daylight saving ends 2026-11-01 at 06:00Z.** S, M and Alt-F all sit inside the hard bundle's offsets (UTC−4, UTC+1).
- M's recurrence forecasts (28 Oct, 1 to 2 Nov) cross both changes. Write forecasts in UTC.
- Slack `users.json` `tz_offset` must be computed per bundle date, not copied from the hard bundle.

## S: redis

The table comes from a small model: carts 8.80 GB after the sweep; growth 0.33 GB per weekday and 0.22 per weekend day; sessions 0.30 GB overnight and 1.00 GB at the 16Z peak; other keys 0.40 GB; maxmemory 12 GB.

| When | Demand (GB) | Over maxmemory | Effective session life |
|---|---|---|---|
| Normal weekday peak | 10.4 | – | 30 min |
| Sat 17, 16Z | 11.97 | 0 | 30 |
| **Sun 18, 13Z** | 12.03 | 0.03 | 29 (onset about 12:40) |
| Mon 19, 16Z | 12.46 | 0.46 | 16 |
| Tue 20, 08Z | 11.94 | 0 | 30 (overnight gap; the monitor goes OK) |
| Tue 20, 14Z | 12.70 | 0.70 | **7.7** |

- **Consistency of the cart numbers.**
  - 8.8 GB ÷ 1.4 KB ≈ 6.3M carts.
  - 30-day retention × about 220k a day ≈ 6.6M.
  - The sweeper deletes about 215k a run, which matches.
  - Backlog by 20 Oct: 8 × 215k ≈ 1.72M keys ≈ 2.4 GB.
  - Catch-up at 2,000 UNLINK/s ≈ 14 min, plus a SCAN of about 6M keys ≈ 2 min. "About 15 min" holds.
- **Eviction rate at Tuesday 14Z.** About 380k sessions ÷ 1,800 s ≈ 210 new sessions a second at a 30-minute lifetime. With the effective life well under 30 minutes, almost every removal is an eviction: about 150 to 200 keys a second, about 100k per 10 minutes.
  - **Do not give the monitor a critical threshold below that unless it is meant to page.** Use warning > 0 and critical > 500k per 10 minutes.
- **The stopgap fits.** maxmemory 4 → 5 GB per primary gives 15 GB of room against 12.7 GB of demand, so evictions stop within a minute. On 8 GB nodes, a fork for an AOF rewrite still has about 3 GB for copy-on-write. That is why the runbook caps it at 5 GB.
- **Approximate LRU.** Redis's eviction pool approximates LRU closely, so the "effective lifetime" model is fair. It is a model. Lint: evictions are zero whenever demand is below maxmemory, and the onset matches the curve.

## M: JVM

Floor by host, from the model:

| When | ord-01 | ord-02 |
|---|---|---|
| Each Wednesday before a deploy (30 Sep, 7 Oct, 14 Oct) | 2.98 GB, 75% | 2.98 GB, 75% |
| Mon 19 Oct 13:00 | 2.46 (then restarted, reset to 1.2) | 2.46 |
| Thu 22 Oct 16:00 | 2.09 (52%) | 3.35 (84%) |
| **Fri 23 Oct 13:30** | **2.33 (58%)** | **3.59 (90%)** |
| Forecast | reaches 3.55 GB around Wed 28 Oct, about 14:40Z | reaches it again around 1 to 2 Nov after the 23 Oct restart |

- **G1 at about 90% retained.** Free heap is about 0.41 GB, which is roughly the default `G1ReservePercent` of 10%. Young-generation copying has nowhere to go. That causes evacuation failures, then Full GCs.
  - At 84% (Thursday) there is about 0.24 GB of young generation. Young collections are frequent but workable, with a couple of Full GCs at the evening peak. That is the stated precursor.
  - That is why it collapses on a moderate Friday morning rather than at Thursday's evening peak.
- **Full GC cost.** A Full GC of 4 GB with a 3.6 GB live set takes about 2.5 to 3.6 s on 4 vCPU. Stalled time is about 12% at one every 25 s and about 23% at one every 15 s.
- **Checkout errors.** About 50% of traffic goes to ord-02, and roughly 15 to 25% of its requests exceed the 3 s client timeout, giving 6 to 9% overall. The 5% error-rate monitor fires at about 14:06.
- **Exact strings and behaviours to pin:**
  - With G1 the message is `Java heap space`. "GC overhead limit exceeded" is a Parallel GC behaviour.
  - `ExitOnOutOfMemoryError` prints `Terminating due to java.lang.OutOfMemoryError: Java heap space` to stderr. Exit status 3 renders in systemd as `status=3/NOTIMPLEMENTED`.
  - The app log gets **no** OOM stack trace.
  - The heap dump, which runs before the exit, takes about 11 s and is the source of Ben's 4 GB file.
- **Hikari's starvation WARN** fires only when the housekeeper's delta exceeds 1.5 × 30 s = **45 s**. Full GCs of 3 s every 15 to 40 s do not trigger it. Allow it only in the last minute before the OOM, when Full GCs run back to back, and at most once or twice. A delta of 51 s fits; 8 s would not.
- **F5 timing.** The default HTTP monitor has a 5 s interval and a 16 s timeout. A member is marked down about 16 s after the process stops listening, and back up at the first successful probe after the Spring context is ready. GC pauses of about 3 s do **not** fail the monitor, so there is no flapping, which avoids overlap with the hard bundle.
- **The Spring Boot 3.x startup line** is `Started X in 37.912 seconds (process running for 39.604)`. The hard bundle's "in 41.2 seconds" without "(process running for ...)" is the Boot 2 shape. Fix it there too, since the plan pins Boot 3.3.

## Check against vendor sources before generating

The same discipline the plan applies to HikariCP and PostgreSQL:

| Item | Confidence |
|---|---|
| Datadog redisdb metric names: `redis.keys.evicted`, `redis.expires`, `redis.mem.maxmemory` | moderate to high |
| Datadog JMX and Java metric names: `jvm.heap_memory`, `jvm.heap_memory_max`, `jvm.gc.major_collection_count` | moderate |
| Dynatrace built-in event names for GC time, memory and a process crash | **low; verify** |
| JDK 17 unified-logging wording for an evacuation failure ("To-space exhausted" versus "Evacuation Failure"; it changed across releases) | needs the JDK version pinned |
| Spring Session's default repository and key naming under Boot 3.3 | moderate |
| PG 15's `DETAIL: Connection matched pg_hba.conf line N` (PG 16 added the file name) | moderate |
| The TLS alert JSSE sends for an expired client certificate (certificate alternative only) | low |

---

# Cost

These are focused hours with the plan's generator and tools, assuming the hard bundle's generator, lint, tools and scorer exist. "Generalisation" is a one-off cost paid by whichever new-date bundle is built first: a per-bundle `t0` and date, multi-day curves, a per-bundle facts overlay, and a lint across bundles. The counterfactual does not pay it, because it shares the hard bundle's date.

| Work | S | M | Alt-F |
|---|---|---|---|
| Scenario YAML: timeline, curves, parameters | 0.75 | 1.0 | 0.4 |
| Hand-written signal content: runbook, fixed chat, history incident, changeSets, monitors | 1.0 | 1.5 | 0.5 |
| Generator: new templates and sourcetypes | 0.5 | 1.0 (GC and journald formats, 45-day curves, search_logs time parsing) | 0.25 |
| Worker chatter and tickets, then review | 0.25 | 0.25 | 0.1 |
| Answer key, trigger, scorer configuration | 0.5 | 0.5 | 0.3 |
| Blind read-through and fixes | 0.5 | 0.75 | 0.25 |
| 3 runs, scoring, write-up row | 0.5 | 0.5 | 0.4 |
| **Subtotal** | **4.0** | **5.5** | **2.2** |
| One-off generalisation, if built first | +1.0 | +1.0 | +0.75 |

**Against the main session's estimate** (S 2 to 3 h, M 4 h): about +1 to +1.5 h each, plus the one-off cost. Most of the difference is the read-through-and-fix loop and generator generalisation.

**Fit with day 2.** The plan's day 2 already holds about 6.75 h of data work:

- the 14:40 checkpoint (1.25 h);
- the counterfactual (2 h);
- the remaining traps (2 h);
- 15:15 (1.5 h).

That comes on top of the agent, the UI and the write-up. One extra bundle fits; two only if 15:15 and some of the remaining traps are cut.

**Value per hour, from my lens:**

1. The counterfactual: anti-memorisation on the same surface. Already planned.
2. Alt-F: abstention, at 2.2 h.
3. S: local knowledge, and not a change, at 4 h.
4. M: long-horizon reasoning, at 5.5 h.

**Spend about 1 hour now** writing and committing all specs and answer keys, built or not. That is the cheapest protection the held-out claim can get.

---

# Findings summary

1. **Critical. The contracts assume every incident has a causal change and a trigger entity.**
   - *Where:* `PLAN.md` sections 3.1 and 3.3: `leading.change_any`, `trigger: {entity}`, and no impact field.
   - *Why it matters:* S has no causal change record, M's trigger is a condition (uptime under a freeze) rather than an entity, and Alt-F has no customer impact. None of them can be scored as the contract stands. The plan itself says contract changes are the only ones that cause rework.
   - *Fix, in the contracts hour:*
     - allow `change_any: none`, and fail any report that names a change as the cause in that case;
     - add a judge-checked `condition` beside a nullable `trigger`;
     - add `impact {customer_facing, scope, start, end}` to the report;
     - keep the scorer free of bundle-specific code.
2. **High. The regression type, as framed, duplicates the hard bundle's main lesson.** Proximity bias is exactly why 3.8.0 was moved to the morning. Build it only with the four conditions: a culprit weeks old, a decoy matching time and topology, an internal exposing condition, and a reversed rule (restart is right; the last deploy is innocent). Design M meets them.
3. **High. The runbook type only discriminates if the runbook holds local facts.** A generic runbook tests what the model already knows, and the hard bundle already has one.
   - Build S around local facts and a cause that is not a change.
   - Title the runbook by symptom, and put the decoy branch inside it.
   - Enforce "absent from prior incident notes" with a corpus lint.
4. **High. Designer leakage.** Held-out specs written after watching the agent on the hard bundle will bend toward what you saw. Write and commit the counterfactual, S and M specs and keys before tuning, and quote the hash. Do not read held-out traces until the final run; anything changed after that is reported as post-hoc.
5. **High. The cost estimate is optimistic.** S is about 4 h, M about 5.5 h and Alt-F about 2.2 h, plus about 1 h of one-off generator generalisation. Only one extra bundle fits day 2 as planned. Priority: counterfactual, then S, then Alt-F, then M.
6. **High. Consistency across bundles is unbudgeted.**
   - Sequences must rise with the date across bundles: ServiceNow INC and CHG numbers, PagerDuty incident numbers, Dynatrace display ids, Jenkins build numbers per job.
   - Later bundles' histories must contain earlier incidents, closed.
   - Personas' on-call schedules must cover the new dates.
   - The estate after INC0048811 changes (gateway 3.8.1).
   - New CMDB edges, such as checkout to order-service, must not repair the hard bundle's deliberate gaps (the missing checkout to gateway edge).
   - *Fix:* a shared `scenario/estate.yaml` and a lint across bundles, about 0.5 h.
7. **Medium. "Small, medium, hard" are bundle sizes, not agent difficulty.** M will probably be harder for the agent than the hard bundle's cause step; S probably easier. One bundle per rung supports no curve. Name the bundles by behaviour and report pass or fail per behaviour.
8. **Medium. The tool layer must support these designs, or they become impossible by accident.**
   - query_metrics returns values per bucket, split by host, over 45 days with a choice of rollup.
   - list_changes reaches 45 days with a text filter applied before the 30-row cap, and makes "last build of a job" visible.
   - search_logs parses JDK `+0000` timestamps and journald lines with no year.
   - Metric names are discoverable from monitors or runbooks.
9. **Medium. Pin the dates before 25 Oct.** S on 20 Oct and M on 23 Oct keep the hard bundle's offsets. Later dates put London on UTC+0 while New York stays at UTC−4. Compute `tz_offset` per bundle.
10. **Medium. Pin where sessions live.** The hard bundle leaves "the cluster" vague: Alice's line, plus a retired CI. S depends on it. Decide the CI, the Datadog tag and the aliases once, in `facts.yaml`.
11. **Medium. M's JVM realism.**
    - With `ExitOnOutOfMemoryError` the app log has no OOM stack trace.
    - G1 reports "Java heap space", never "GC overhead limit exceeded".
    - Hikari's starvation WARN needs a stall of more than 45 s, so at most once or twice, just before the OOM.
    - Exit 3 renders as `status=3/NOTIMPLEMENTED`.
    - Do not sample within a GC log, because the `GC(n)` ids would show gaps.
12. **Medium. S's realism.**
    - Synthetics must stay green, because they never idle. Use that as a trap; do not make the synthetic fail.
    - The usable metric signal is the change of trend on 12 to 13 Oct, not the nightly drop, which rollup smooths away.
    - The discriminating log line ("cookie present, session not found") must predate 2.15.0. It does, because it is INC0043630's follow-up.
13. **Low. Add a Root Cause Changes-style baseline** (time × topology ranker, about 0.5 h). It proves each decoy ranks first and adds a second baseline row.
14. **Low. Alt-F is the best behaviour-per-hour addition** (abstention, about 2 h). The certificate time bomb is the cheapest way to keep the regression theme, but it is a weaker test.
15. **Low. A carry-over for the hard bundle.** The Spring Boot 3.3 startup line is `Started PaymentsGatewayApplication in 41.213 seconds (process running for 43.07)`, not "in 41.2 seconds".
16. **Low. Extend the prompt-leak lint and the alias tables per bundle:** `cart-sweeper`, `sessions-prod`, `PromoEligibility`, `kpatch`, `ord-0`.

---

# Confidence

**0.7.**

**What I am confident about:**

- The overlap analysis.
- The case that the runbook test must rest on local facts and that the regression test must meet the four conditions.
- The contract gap (finding 1).
- The arithmetic. Every date, weekday, epoch and DST boundary was checked programmatically, and the redis and heap tables come from small models whose parameters are stated, so the generator can reproduce them.

**What is less certain, and should be verified before generating:**

- Vendor names: the Datadog metric names, the Dynatrace event names, and the JDK and PG wording listed above.
- My cost figures, which assume the plan's generator is not already parameterised by date.

**What would change my mind:**

- **If the generator and tools already handle multiple dates and long lookbacks:** S drops to about 3 h and M to about 4.5 h, and both fit day 2.
- **If the hard bundle's agent turns out to fail mostly on evidence gathering rather than reasoning:** cheaper variants on the same surface (the counterfactual, an alias shuffle) are worth more than new surfaces, and I would rank S and M below a second counterfactual.
- **If a blind read-through of M with only the CLI takes much longer than 20 minutes:** M is too hard for "medium", and I would swap it for the certificate time bomb.
- **If BigPanda's reviewers weigh "found the bad deploy" above everything else:** M rises above Alt-F in priority, because it is the more recognisable story.
