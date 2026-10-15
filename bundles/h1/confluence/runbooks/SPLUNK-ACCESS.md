# SPLUNK-ACCESS: Getting Splunk access and finding your logs

Space: SRE · Owner: Platform Reliability · Last updated 15 Jan 2026 by Sam Ortega

This page covers how to get a Splunk role, which indexes hold what, and a few starter searches. Always name an index in a search; searches across all indexes are slow and rarely needed. Access questions go to the platform team queue in ServiceNow. For missing data in an index, post in the SRE channel with the index name and the time range.

## Request a role

1. Open a ServiceNow request using the "Splunk access" catalog item.
2. Name your team and the indexes you need. Ask for the smallest set that covers your work; you can add more later.
3. Your manager approves. The platform team adds you to the matching role within one working day. If the indexes do not show up, log out of Splunk and back in once, since roles are read at login.

## Main indexes

- `payments`: application logs from the payment services.
- `commerce`: storefront, cart and checkout service logs.
- `db`: database server logs for the production databases.
- `k8s`: Kubernetes events from the production clusters.
- `network`: firewall, load balancer and DNS logs.
- `edge`: CDN and public edge logs for web and mobile traffic.

## Example searches

Set the time picker to Last 24 hours first. Each search below starts with an index and a sourcetype and charts event counts per time bucket.

1. Checkout API errors over time:

```
index=commerce sourcetype=checkout:api level=ERROR | timechart count
```

2. Payments gateway log volume over time:

```
index=payments sourcetype=payments:app | timechart count
```

3. Database log volume over time:

```
index=db sourcetype=postgresql | timechart count
```
