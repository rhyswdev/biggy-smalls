# h1: build record

Built on branch `heldout-h1` from `cards/h1.md` by the held-out session, which read no agent code, prompts or run transcripts. Not for agent sessions.

## Where it is

- `scenario/variants/h1/scenario.yaml`: the variant, the single source of every time and number. `facts.yaml` is the development facts plus the bundle name and checkout-api's pool to checkout-db. `content/` overrides the base content file by file: the Slack messages, the live incident, the customer tickets, the checkout-api log templates, the answer key, and trigger 1 (copied unchanged).
- `bundles/h1/`, with `_internal/answer_key.yaml`. `make data SCENARIO=scenario/variants/h1/scenario.yaml` builds it, and `make lint SCENARIO=...` checks it. The served `integration.yaml` names the estate's bundle (`larkspur-checkout-2026-10-13`), not `h1`.
- Every generator change is switched on only by keys the variant has. The development bundle regenerates byte for byte.

## The mechanism, in numbers

2.14.3 writes three audit events per submit, synchronously, inside the submit transaction. Once the DEBUG flood starts at 14:28:40, the log sink's latency climbs to about 9.8 s per write by 14:32, so a submit holds its checkout-db connection for about 30 s. Little's law per pod (submits a second / 6 x hold, against a pool of 40) gives these timeout shares at the 5000 ms connection timeout: 15% at 14:30, 35% at 14:31, 47% at 14:33 and 62% at 14:40, after customer resubmits raise submits from 14 to 21 a second. checkout-api's 5xx rate is derived from the same model: about 3% at 14:30, 7% at 14:31 and 16% at 14:40. Monitor 7391255 warns at 14:32 and triggers at 14:33. The first pool timeout is at 14:29:53 on a 2.14.3 pod. checkout-api's exception handler answers 504 on a pool timeout, which is the card's 504.

## Differences from the card

- **Trigger 1 is unchanged, as the card says.** It still says Elena reports pay-db-01 maxed out. Her lines are removed, so nothing in the bundle backs that claim, and the evidence contradicts it: pay-db-01 peaks at 85 of 197.
- **Removed as a consequence of the card's removals** (not listed in the card): PagerDuty 8813, the database page, because can_connect never fails; F5 flaps (there is no F5 log file) and the SolarWinds pay-03 alerts, because the gateway's health checks pass; all Splunk alerts; the DBA's pg_stat_activity paste and the session kill; PayCore v2 and v3; Jenkins #413 and CHG0031910; the late customer tickets.
- **PagerDuty numbers:** with the development day's 8812 to 8815 gone, the checkout error page is 8812 (Alice) and the synthetic, merged into it, is 8813.
- **INC0048811** is opened from monitor 7391255, on CI Checkout API, and stays with Commerce Platform. Nobody reassigns it.
- **Added beyond the card's list:**
  - the per-pod pool series `max:hikaricp.connections.active{service:checkout-api,env:prod} by {pod_name}`. It is the card's "connection series by pod", pulled at 14:40, because a response running to 15:30 would list the rollback's new pods as series of their own. The leak test caught that.
  - in #sre-oncall, Sam asks at 14:31 whether someone left DEBUG on, and Ben answers "noisy but harmless".
  - Alice's 14:38 work note.
  - chat after 14:40 that fits this day.
  - sig ids on the definitions of the four monitors that never fire (gateway p90 and p99, can_connect, success rate), so the scripted path can cite non-chat support for F4.
- **Defects:** the duplicate Splunk webhook and the truncated leak trace are absent, because no Splunk alert fires and the gateway logs no leak traces. The fire-and-resolve alert and the empty fields remain.
- **Size:** 2,442 log lines and 194 structured records, against the development bundle's 4,815 and 242. A healthy gateway and database log far less. The noise is unchanged.

## Acceptance (`README.md`)

1. Done. `make data`, `make lint` (0 errors), `make lint-every-bundle` and `python -m eval.key --bundle h1` pass. The leak tests (`SMALLS_TEST_BUNDLE=bundles/h1 SMALLS_TEST_SCENARIO=scenario/variants/h1/scenario.yaml`) pass at 14:40 except five assertions written for the development day: a 14:52 checkpoint, three SolarWinds alerts, more than 2,000 visible records, the duplicate delivery and the truncated line. Each of those tests' remaining checks was run on h1 and passes. A sweep of 2,038 tool outputs at 14:40, with tags on and off, finds no bundle id, path or variant name.
2. Done. The card's path is 9 tool calls plus the report. It shows errors by version, the rollout and #897, the pool series by pod, the empty pool-timeout search on the gateway, and the gateway's never-fired monitors. That covers F1 to F4, each with a non-chat handle.
3 to 5. Not done in this session. They need the scorer on hand-written reports and model runs (the no-tools baseline and the contrarian control).
6. Holds. No new sources, tools or vendor shapes. People and entities come from `facts.yaml`.

Tags: `make tag BUNDLE=bundles/h1` asked 47 questions not in the cache and appended them to the shared `tags/cache.jsonl`. With those entries, h1 ingests with tags on and 0 misses.
