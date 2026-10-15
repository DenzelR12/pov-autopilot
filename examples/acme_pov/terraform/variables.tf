variable "pagerduty_token" {
  description = "PagerDuty REST API token"
  type        = string
  sensitive   = true
}

variable "team_members" {
  description = "Team name to list of on call member emails (users must already exist)"
  type        = map(list(string))
  default     = {}
}
