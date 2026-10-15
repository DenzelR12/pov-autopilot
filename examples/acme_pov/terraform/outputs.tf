output "global_routing_key" {
  description = "Point every monitoring tool at this Events API v2 routing key"
  value       = pagerduty_event_orchestration.acme_retail_global.integration[0].parameters[0].routing_key
  sensitive   = true
}

output "service_ids" {
  value = {
    "checkout-api" = pagerduty_service.checkout_api.id
    "edge"         = pagerduty_service.edge.id
    "identity"     = pagerduty_service.identity.id
    "inventory-db" = pagerduty_service.inventory_db.id
    "payments"     = pagerduty_service.payments.id
    "search"       = pagerduty_service.search.id
  }
}
