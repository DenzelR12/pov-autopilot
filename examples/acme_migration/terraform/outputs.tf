output "service_ids" {
  value = {
    "checkout-api" = pagerduty_service.checkout_api.id
    "payments"     = pagerduty_service.payments.id
    "identity"     = pagerduty_service.identity.id
    "legacy-batch" = pagerduty_service.legacy_batch.id
  }
}
