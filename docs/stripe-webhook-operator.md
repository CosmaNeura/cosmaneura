# Stripe webhook operator runbook

Configure the live Stripe Dashboard endpoint with this exact URL:

`https://app.cosmaneura.com/external/v1/webhooks/stripe`

Subscribe the endpoint to:

- `checkout.session.completed`
- `customer.subscription.created`
- `customer.subscription.updated`
- `customer.subscription.deleted`
- `customer.subscription.paused`
- `customer.subscription.resumed`
- `invoice.payment_failed`
- `invoice.paid`
- `invoice.payment_succeeded`

## Activation checks

Before the Stripe integration and its account, price, API-version, and webhook
secrets are configured, an empty `POST` to the endpoint must return:

```text
503 {"status":"billing webhook not configured"}
```

That response is the expected dormant state. Webhook ingest is gated by
`stripe_webhook_configured()` and does not depend on
`STRIPE_BILLING_ENABLED` or `STRIPE_PORTAL_CONFIGURATION_ID`. The customer
portal requires the pinned portal configuration described in
[Stripe billing activation](stripe-billing-activation.md#customer-portal-configuration).

After the integration and secrets are configured, a request with an invalid
`Stripe-Signature` must return:

```text
401 {"status":"invalid signature"}
```

After John enables the integration, a valid signed event must return `200`.
The `200` acknowledges that the supported event was durably written to the
Stripe inbox; the worker and periodic sweep perform authoritative account,
mode, price, customer, and subscription reconciliation.

If the response is not the expected one, stop. Do not retarget traffic to a
different path, bypass signature verification, or enable acquisition to make a
probe pass.

---

Do not flip `STRIPE_BILLING_ENABLED`. Do not create a $60 price. Do not apply Terraform.
