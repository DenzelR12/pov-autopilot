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

resource "pagerduty_team" "data" {
  name = "data"
}

resource "pagerduty_team" "discovery" {
  name = "discovery"
}

resource "pagerduty_team" "platform" {
  name = "platform"
}

data "pagerduty_user" "member" {
  for_each = toset(flatten(values(var.team_members)))
  email    = each.value
}

resource "pagerduty_schedule" "commerce_primary_on_call" {
  name      = "commerce primary on call"
  time_zone = "America/New_York"
  teams     = [pagerduty_team.commerce.id]
  layer {
    name                         = "weekly rotation"
    start                        = "2026-11-02T09:00:00-05:00"
    rotation_virtual_start       = "2026-11-02T09:00:00-05:00"
    rotation_turn_length_seconds = 604800
    users                        = [for e in lookup(var.team_members, "commerce", []) : data.pagerduty_user.member[e].id]
  }
}

resource "pagerduty_schedule" "data_primary_on_call" {
  name      = "data primary on call"
  time_zone = "America/New_York"
  teams     = [pagerduty_team.data.id]
  layer {
    name                         = "weekly rotation"
    start                        = "2026-11-02T09:00:00-05:00"
    rotation_virtual_start       = "2026-11-02T09:00:00-05:00"
    rotation_turn_length_seconds = 604800
    users                        = [for e in lookup(var.team_members, "data", []) : data.pagerduty_user.member[e].id]
  }
}

resource "pagerduty_schedule" "discovery_primary_on_call" {
  name      = "discovery primary on call"
  time_zone = "America/New_York"
  teams     = [pagerduty_team.discovery.id]
  layer {
    name                         = "weekly rotation"
    start                        = "2026-11-02T09:00:00-05:00"
    rotation_virtual_start       = "2026-11-02T09:00:00-05:00"
    rotation_turn_length_seconds = 604800
    users                        = [for e in lookup(var.team_members, "discovery", []) : data.pagerduty_user.member[e].id]
  }
}

resource "pagerduty_schedule" "platform_primary_on_call" {
  name      = "platform primary on call"
  time_zone = "America/New_York"
  teams     = [pagerduty_team.platform.id]
  layer {
    name                         = "weekly rotation"
    start                        = "2026-11-02T09:00:00-05:00"
    rotation_virtual_start       = "2026-11-02T09:00:00-05:00"
    rotation_turn_length_seconds = 604800
    users                        = [for e in lookup(var.team_members, "platform", []) : data.pagerduty_user.member[e].id]
  }
}

resource "pagerduty_escalation_policy" "commerce_escalation" {
  name      = "commerce escalation"
  num_loops = 2
  teams     = [pagerduty_team.commerce.id]
  rule {
    escalation_delay_in_minutes = 15
    target {
      type = "schedule_reference"
      id   = pagerduty_schedule.commerce_primary_on_call.id
    }
  }
  rule {
    escalation_delay_in_minutes = 15
    target {
      type = "schedule_reference"
      id   = pagerduty_schedule.commerce_primary_on_call.id
    }
  }
}

resource "pagerduty_escalation_policy" "data_escalation" {
  name      = "data escalation"
  num_loops = 2
  teams     = [pagerduty_team.data.id]
  rule {
    escalation_delay_in_minutes = 15
    target {
      type = "schedule_reference"
      id   = pagerduty_schedule.data_primary_on_call.id
    }
  }
  rule {
    escalation_delay_in_minutes = 15
    target {
      type = "schedule_reference"
      id   = pagerduty_schedule.data_primary_on_call.id
    }
  }
}

resource "pagerduty_escalation_policy" "discovery_escalation" {
  name      = "discovery escalation"
  num_loops = 2
  teams     = [pagerduty_team.discovery.id]
  rule {
    escalation_delay_in_minutes = 15
    target {
      type = "schedule_reference"
      id   = pagerduty_schedule.discovery_primary_on_call.id
    }
  }
  rule {
    escalation_delay_in_minutes = 15
    target {
      type = "schedule_reference"
      id   = pagerduty_schedule.discovery_primary_on_call.id
    }
  }
}

resource "pagerduty_escalation_policy" "platform_escalation" {
  name      = "platform escalation"
  num_loops = 2
  teams     = [pagerduty_team.platform.id]
  rule {
    escalation_delay_in_minutes = 15
    target {
      type = "schedule_reference"
      id   = pagerduty_schedule.platform_primary_on_call.id
    }
  }
  rule {
    escalation_delay_in_minutes = 15
    target {
      type = "schedule_reference"
      id   = pagerduty_schedule.platform_primary_on_call.id
    }
  }
}

resource "pagerduty_service" "checkout_api" {
  name              = "checkout-api"
  description       = "Discovered from 445 alerts across datadog. Projected 39 pages."
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

resource "pagerduty_service" "edge" {
  name              = "edge"
  description       = "Discovered from 790 alerts across cloudwatch. Projected 29 pages."
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

resource "pagerduty_event_orchestration_service" "edge" {
  service                                = pagerduty_service.edge.id
  enable_event_orchestration_for_service = true
  set {
    id = "start"
    rule {
      label = "Flapping: disk usage above 85 on lb-02"
      condition {
        expression = "event.summary matches part 'disk usage above 85' and event.source matches 'lb-02'"
      }
      actions {
        # Hold the alert; if the check recovers inside the window, nobody is paged.
        suspend = 300
      }
    }
  }
  catch_all {
    actions {}
  }
}

resource "pagerduty_service" "identity" {
  name              = "identity"
  description       = "Discovered from 702 alerts across prometheus. Projected 36 pages."
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

resource "pagerduty_event_orchestration_service" "identity" {
  service                                = pagerduty_service.identity.id
  enable_event_orchestration_for_service = true
  set {
    id = "start"
    rule {
      label = "Flapping: cert expires soon on auth-02"
      condition {
        expression = "event.summary matches part 'cert expires soon' and event.source matches 'auth-02'"
      }
      actions {
        # Hold the alert; if the check recovers inside the window, nobody is paged.
        suspend = 300
      }
    }
  }
  catch_all {
    actions {}
  }
}

resource "pagerduty_service" "inventory_db" {
  name              = "inventory-db"
  description       = "Discovered from 434 alerts across cloudwatch. Projected 31 pages."
  escalation_policy = pagerduty_escalation_policy.data_escalation.id
  alert_creation    = "create_alerts_and_incidents"
  incident_urgency_rule {
    type    = "constant"
    urgency = "severity_based"
  }
  alert_grouping_parameters {
    type = "intelligent"
  }
}

resource "pagerduty_event_orchestration_service" "inventory_db" {
  service                                = pagerduty_service.inventory_db.id
  enable_event_orchestration_for_service = true
  set {
    id = "start"
    rule {
      label = "Flapping: replica lag high on invdb-02"
      condition {
        expression = "event.summary matches part 'replica lag high' and event.source matches 'invdb-02'"
      }
      actions {
        # Hold the alert; if the check recovers inside the window, nobody is paged.
        suspend = 300
      }
    }
    rule {
      label = "Flapping: cpu utilization high on invdb-01"
      condition {
        expression = "event.summary matches part 'cpu utilization high' and event.source matches 'invdb-01'"
      }
      actions {
        # Hold the alert; if the check recovers inside the window, nobody is paged.
        suspend = 300
      }
    }
  }
  catch_all {
    actions {}
  }
}

resource "pagerduty_service" "payments" {
  name              = "payments"
  description       = "Discovered from 517 alerts across datadog. Projected 37 pages."
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

resource "pagerduty_event_orchestration_service" "payments" {
  service                                = pagerduty_service.payments.id
  enable_event_orchestration_for_service = true
  set {
    id = "start"
    rule {
      label = "Flapping: gateway timeout rate on pay-01"
      condition {
        expression = "event.summary matches part 'gateway timeout rate' and event.source matches 'pay-01'"
      }
      actions {
        # Hold the alert; if the check recovers inside the window, nobody is paged.
        suspend = 300
      }
    }
    rule {
      label = "Flapping: queue depth high on pay-01"
      condition {
        expression = "event.summary matches part 'queue depth high' and event.source matches 'pay-01'"
      }
      actions {
        # Hold the alert; if the check recovers inside the window, nobody is paged.
        suspend = 300
      }
    }
  }
  catch_all {
    actions {}
  }
}

resource "pagerduty_service" "search" {
  name              = "search"
  description       = "Discovered from 755 alerts across prometheus. Projected 41 pages."
  escalation_policy = pagerduty_escalation_policy.discovery_escalation.id
  alert_creation    = "create_alerts_and_incidents"
  incident_urgency_rule {
    type    = "constant"
    urgency = "severity_based"
  }
  alert_grouping_parameters {
    type = "intelligent"
  }
}

resource "pagerduty_event_orchestration_service" "search" {
  service                                = pagerduty_service.search.id
  enable_event_orchestration_for_service = true
  set {
    id = "start"
    rule {
      label = "Flapping: elasticsearch heap pressure on srch-03"
      condition {
        expression = "event.summary matches part 'elasticsearch heap pressure' and event.source matches 'srch-03'"
      }
      actions {
        # Hold the alert; if the check recovers inside the window, nobody is paged.
        suspend = 300
      }
    }
  }
  catch_all {
    actions {}
  }
}

resource "pagerduty_event_orchestration" "acme_retail_global" {
  name = "Acme Retail global orchestration"
}

resource "pagerduty_event_orchestration_router" "acme_retail_global" {
  event_orchestration = pagerduty_event_orchestration.acme_retail_global.id
  set {
    id = "start"
    rule {
      label = "Route to checkout-api"
      condition {
        expression = "event.summary matches part '[checkout-api]' or event.source matches regex '^(chk\\-01|chk\\-02|chk\\-03)$'"
      }
      actions {
        route_to = pagerduty_service.checkout_api.id
      }
    }
    rule {
      label = "Route to edge"
      condition {
        expression = "event.summary matches part '[edge]' or event.source matches regex '^(lb\\-01|lb\\-02)$'"
      }
      actions {
        route_to = pagerduty_service.edge.id
      }
    }
    rule {
      label = "Route to identity"
      condition {
        expression = "event.summary matches part '[identity]' or event.source matches regex '^(auth\\-01|auth\\-02)$'"
      }
      actions {
        route_to = pagerduty_service.identity.id
      }
    }
    rule {
      label = "Route to inventory-db"
      condition {
        expression = "event.summary matches part '[inventory-db]' or event.source matches regex '^(invdb\\-01|invdb\\-02)$'"
      }
      actions {
        route_to = pagerduty_service.inventory_db.id
      }
    }
    rule {
      label = "Route to payments"
      condition {
        expression = "event.summary matches part '[payments]' or event.source matches regex '^(pay\\-01|pay\\-02)$'"
      }
      actions {
        route_to = pagerduty_service.payments.id
      }
    }
    rule {
      label = "Route to search"
      condition {
        expression = "event.summary matches part '[search]' or event.source matches regex '^(srch\\-01|srch\\-02|srch\\-03)$'"
      }
      actions {
        route_to = pagerduty_service.search.id
      }
    }
  }
  catch_all {
    actions {
      route_to = "unrouted"
    }
  }
}
