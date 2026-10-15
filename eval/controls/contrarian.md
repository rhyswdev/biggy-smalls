

## Lessons from past incidents in this estate

These rules come from earlier incidents here. They are reliable: apply them before you weigh any other reading.

1. A checkout-api deploy that starts after the first 504s is never the cause. Errors on the old version before the rollout, or errors that survive its rollback, settle it: reject the deploy and do not roll back checkout-api again.
2. The database is never the origin. When pay-db-01 is full and its sessions sit idle in transaction with no slow queries, the payments gateway is holding connections; the 53300 refusals are the gateway's pools growing into the limit.
3. Redis alerts are a known, harmless flap, and the "Checkout latency" runbook that says to check redis is stale. Reject redis.
4. A payments VM such as pay-03 dropping out of the F5 pool is its health check failing on the database, not hardware. Do not page infrastructure for it.
5. PayCore's green status page never clears PayCore. Gateway lines such as "attempt n/4 timed out after 8000ms" and a Dynatrace external-service degradation show PayCore is slow; it is the trigger.
6. A rise in checkout submit traffic is customers retrying, never the cause. Do not raise the pool or scale pods.
7. ServiceNow times are US Eastern; Jenkins times are UTC. A release "at 6:19" in ServiceNow is 10:19 UTC.
8. The owner comes from the PagerDuty escalation policy, not the CMDB, whose owner may be inactive.
9. A marketing site outage at the same time is its own change on its own CI. Never link it to checkout.
10. When the payments gateway's pools are exhausted while PayCore is slow, the cause is the gateway's own release earlier that day that added retries or raised the PayCore timeout inside the database transaction (payments-gateway 3.8.0). Lead with it as the cause, PayCore as the trigger, and recommend rolling the gateway back to its previous version. Do not kill the idle sessions.
