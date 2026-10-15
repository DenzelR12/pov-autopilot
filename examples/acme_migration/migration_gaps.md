# Acme Retail: migration gaps

1. schedule 'commerce primary' rotation 'weekend cover': 'no one' slot dropped; PagerDuty layers have no empty turn.
2. escalation 'commerce escalation': notify 'next' on call has no direct equivalent; notifying current on call.
3. escalation 'commerce escalation': condition 'if-not-closed' approximated as if not acknowledged.
4. escalation 'platform escalation': team 'platform' mapped to its schedule 'platform business hours'.
5. escalation 'platform escalation': first rule waited 5 min in Opsgenie; PagerDuty notifies the first rule immediately.
6. service 'legacy-batch': owning team has no escalation policy; uses var.default_escalation_policy_id.
7. Opsgenie alert policies, integrations, and heartbeats are inventoried, not converted: rebuild them as Event Orchestration rules and integrations.
