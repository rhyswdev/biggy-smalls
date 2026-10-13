# PGW-DB-CONN-TIMEOUT: payments-gateway cannot get a database connection

Space: PAY · Owner: Database Engineering with Payments Engineering · Last updated 2 Aug 2026 by Elena Rossi

Linked from the Splunk alert "Payments pool exhaustion". Symptom: payments-gateway logs `HikariPool-1 - Connection is not available, request timed out after 5000ms`, and authorizations fail or slow down.

## What the pool numbers mean

The gateway logs HikariCP pool stats at DEBUG every 30 seconds (`After cleanup  stats (total=, active=, idle=, waiting=)`). Each VM's pool is elastic, 5 to 35 connections.

- `active` at the maximum with `waiting` above zero: every connection is borrowed and requests are queueing for one.
- pay-db-01 allows 197 connections for application roles (`max_connections=200`, 3 reserved). The application pools and batch jobs together can exceed it; new connections then fail with SQLSTATE 53300.

## Look at what the sessions are doing

Connect to pay-db-01 as a DBA (reserved slot) and break the sessions down by state:

```
select usename, application_name, state, count(*), max(now() - xact_start) as oldest_xact
  from pg_stat_activity where backend_type = 'client backend' group by 1, 2, 3 order by 4 desc;
```

- **Mostly `active`, long `query_start` ages:** slow queries. Find them with `pg_stat_activity` ordered by `query_start`, check `wait_event` for locks, and look at the plan. This is a database problem; Database Engineering owns it.
- **Mostly `idle in transaction`:** the application opened a transaction and is not sending anything. `wait_event` is `ClientRead`. The database is waiting on the app. Long app-side transactions are an application problem; Payments Engineering owns it.
- **Mostly `idle`:** pools are oversized for the load; not urgent.

Killing `idle in transaction` sessions frees slots for a short time only if the application keeps opening the same transactions, and it aborts whatever those transactions were doing. Do it only with Payments Engineering's agreement.

## Mitigate

1. If a batch job holds a large share of connections and can wait, pause it to restore headroom.
2. Restart gateway nodes one at a time, letting the F5 drain each member first, to reset their pools.
3. Escalate to Payments Engineering (PagerDuty "Payments - Prod") with the session breakdown above.
