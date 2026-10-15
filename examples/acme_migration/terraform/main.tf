terraform {
  required_version = ">= 1.5"
  required_providers {
    pagerduty = {
      source  = "PagerDuty/pagerduty"
      version = "~> 3.15"
    }
  }
}

provider "pagerduty" {
  token = var.pagerduty_token
}

resource "pagerduty_team" "commerce" {
  name = "commerce"
}

resource "pagerduty_team" "platform" {
  name = "platform"
}

resource "pagerduty_user" "ana_rivera_acme_example" {
  name  = "Ana Rivera"
  email = "ana.rivera@acme.example"
}

resource "pagerduty_user" "ben_okafor_acme_example" {
  name  = "Ben Okafor"
  email = "ben.okafor@acme.example"
}

resource "pagerduty_user" "chloe_tan_acme_example" {
  name  = "Chloe Tan"
  email = "chloe.tan@acme.example"
}

resource "pagerduty_user" "dev_patel_acme_example" {
  name  = "Dev Patel"
  email = "dev.patel@acme.example"
}

resource "pagerduty_user" "erin_walsh_acme_example" {
  name  = "Erin Walsh"
  email = "erin.walsh@acme.example"
}

resource "pagerduty_user" "frank_lee_acme_example" {
  name  = "frank.lee@acme.example"
  email = "frank.lee@acme.example"
}

resource "pagerduty_schedule" "commerce_primary" {
  name      = "commerce primary"
  time_zone = "America/New_York"
  teams     = [pagerduty_team.commerce.id]
  layer {
    name                         = "weekly"
    start                        = "2026-11-02T14:00:00+00:00"
    rotation_virtual_start       = "2026-11-02T14:00:00+00:00"
    rotation_turn_length_seconds = 604800
    users                        = [pagerduty_user.ana_rivera_acme_example.id, pagerduty_user.ben_okafor_acme_example.id, pagerduty_user.chloe_tan_acme_example.id]
  }
  layer {
    name                         = "weekend cover"
    start                        = "2026-11-07T14:00:00+00:00"
    rotation_virtual_start       = "2026-11-07T14:00:00+00:00"
    rotation_turn_length_seconds = 172800
    users                        = [pagerduty_user.dev_patel_acme_example.id]
    restriction {
      type              = "weekly_restriction"
      start_time_of_day = "09:00:00"
      duration_seconds  = 172800
      start_day_of_week = 6
    }
  }
}

resource "pagerduty_schedule" "platform_business_hours" {
  name      = "platform business hours"
  time_zone = "Europe/London"
  teams     = [pagerduty_team.platform.id]
  layer {
    name                         = "daytime"
    start                        = "2026-11-02T09:00:00+00:00"
    rotation_virtual_start       = "2026-11-02T09:00:00+00:00"
    rotation_turn_length_seconds = 86400
    users                        = [pagerduty_user.erin_walsh_acme_example.id, pagerduty_user.dev_patel_acme_example.id]
    restriction {
      type              = "daily_restriction"
      start_time_of_day = "08:00:00"
      duration_seconds  = 37800
    }
  }
}

resource "pagerduty_escalation_policy" "commerce_escalation" {
  name      = "commerce escalation"
  num_loops = 2
  teams     = [pagerduty_team.commerce.id]
  rule {
    escalation_delay_in_minutes = 10
    target {
      type = "schedule_reference"
      id   = pagerduty_schedule.commerce_primary.id
    }
  }
  rule {
    escalation_delay_in_minutes = 15
    target {
      type = "schedule_reference"
      id   = pagerduty_schedule.commerce_primary.id
    }
  }
  rule {
    escalation_delay_in_minutes = 15
    target {
      type = "user_reference"
      id   = pagerduty_user.ana_rivera_acme_example.id
    }
  }
}

resource "pagerduty_escalation_policy" "platform_escalation" {
  name      = "platform escalation"
  num_loops = 0
  teams     = [pagerduty_team.platform.id]
  rule {
    escalation_delay_in_minutes = 55
    target {
      type = "schedule_reference"
      id   = pagerduty_schedule.platform_business_hours.id
    }
  }
  rule {
    escalation_delay_in_minutes = 15
    target {
      type = "user_reference"
      id   = pagerduty_user.frank_lee_acme_example.id
    }
  }
}

resource "pagerduty_service" "checkout_api" {
  name              = "checkout-api"
  description       = "Customer checkout"
  escalation_policy = pagerduty_escalation_policy.commerce_escalation.id
  alert_creation    = "create_alerts_and_incidents"
  incident_urgency_rule {
    type    = "constant"
    urgency = "severity_based"
  }
  alert_grouping_parameters {
    type = "intelligent"
  }
}

resource "pagerduty_service" "payments" {
  name              = "payments"
  description       = "Card and wallet payments"
  escalation_policy = pagerduty_escalation_policy.commerce_escalation.id
  alert_creation    = "create_alerts_and_incidents"
  incident_urgency_rule {
    type    = "constant"
    urgency = "severity_based"
  }
  alert_grouping_parameters {
    type = "intelligent"
  }
}

resource "pagerduty_service" "identity" {
  name              = "identity"
  description       = "Login and tokens"
  escalation_policy = pagerduty_escalation_policy.platform_escalation.id
  alert_creation    = "create_alerts_and_incidents"
  incident_urgency_rule {
    type    = "constant"
    urgency = "severity_based"
  }
  alert_grouping_parameters {
    type = "intelligent"
  }
}

resource "pagerduty_service" "legacy_batch" {
  name              = "legacy-batch"
  description       = "Nightly jobs"
  escalation_policy = var.default_escalation_policy_id
  alert_creation    = "create_alerts_and_incidents"
  incident_urgency_rule {
    type    = "constant"
    urgency = "severity_based"
  }
  alert_grouping_parameters {
    type = "intelligent"
  }
}
