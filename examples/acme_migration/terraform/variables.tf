variable "pagerduty_token" {
  description = "PagerDuty REST API token"
  type        = string
  sensitive   = true
}

variable "default_escalation_policy_id" {
  description = "Escalation policy for services whose source policy could not be mapped"
  type        = string
}
