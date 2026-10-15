# Checkout latency: triage runbook

Space: SRE · Owner: Platform Reliability · Last updated 11 Mar 2024 by Sam Ortega

Use this when checkout is slow or failing at payment and the "checkout-api error rate" or "p90 latency" monitors fire.

## 1. Check redis-sessions first

Most checkout slowness we have seen comes from session lookups.

1. Open the redis-sessions dashboard. Look at `redis.mem.fragmentation_ratio` and evictions on `redis-sessions-01`.
2. If fragmentation is above 1.5 or evictions are non-zero, restart the replica first, then fail over the primary:
   `redis-cli -h redis-sessions-01 DEBUG RESTART` is **not** safe; use the Ansible playbook `redis-sessions-failover.yml`.
3. Sessions rebuild on next login. Expect a short spike in login traffic.

## 2. Scale checkout-api

1. If redis is healthy, scale checkout-api out: `kubectl -n commerce scale deploy/checkout-api --replicas=12`.
2. Watch p90 for 5 minutes. If it improves, leave it scaled until traffic drops and raise a ticket for capacity.

## 3. Check the last deploy

1. Look at the latest `checkout-api-deploy` job in Jenkins. If it deployed in the last hour, roll it back with the same job and the previous VERSION.

## 4. Escalate

If none of the above helps within 20 minutes, page Commerce Platform (PagerDuty "Checkout - Prod") and open a P2 in ServiceNow against Checkout API.
