# Payments Service On-Call Runbook

When PagerDuty alerts for the payments service:

1. Acknowledge within 5 minutes.
2. Check the dashboard at grafana.internal/d/payments. Look for elevated
   5xx rate or latency p99.
3. If 5xx > 1% sustained for 2 minutes, page the secondary.
4. Common cause #1: downstream rate limit from the bank gateway. Mitigation:
   shed traffic to the secondary processor via the feature flag
   `payments.gateway.secondary_pct`.
5. Common cause #2: stale credentials. Rotate via `payments-rotate` job in
   the ops console.

Escalation: VP Engineering for sustained >5 min outages.

Owner: Payments Team · Last updated: 2026-04-30
