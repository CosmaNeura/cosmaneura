# Stripe billing existing-user walls closed

Walls 1 and 2 are closed by Prompt F1. This is a synthetic code trace, not a production account inventory or authorization to deploy.

1. **Signed failed-invoice pending wall: closed.** `enqueue_stripe_event` no longer writes pending verification solely for `invoice.payment_failed`. A matching paid or exempt subscription remains active until the worker obtains fresh Stripe state. Invoice customer mismatches still mark pending verification; the subscription-event branch is unchanged. `Practice.entitled` checks `billing_exemption_active` first, so an active comp outranks pending verification and unknown status. The frontend paused-access alert also excludes `billing_exempt_active`. The three former strict xfails and the former Vitest expected failure now assert preservation.
2. **Invalid bound-contract comp wall: closed.** Fresh commercial contract validation can still record `invalid`, but an active comp now outranks that projection in `Practice.entitled`. An expired comp does not. The exhaustive reorder property proves every previously entitled input stays entitled. Exact pilot grants remain an independent access basis; `doctor_access` ordering is unchanged.
3. **Existing authoritative subscription rules remain.** Fresh reads can still mirror non-entitling commercial state for ordinary card subscriptions without a comp. F1 adds no pause path, agreement, card, Checkout, rejoin or onboarding requirement for an existing practice. It changes no BAA version, hash, asset or version row.

The invoice-only lane has a separate Stripe customer and subscription in `practice_invoice_billing`. `apply_stripe_event` routes matching subscriptions there before a Practice lookup. Older events are ignored. Failed invoices and past-due/unpaid/incomplete invoice subscriptions alert operators and update only the invoice record. Separate seat reconciliation updates only that record and its Stripe subscription. Neither entitlement nor the clinical gates read the invoice table. No invoice object has practice metadata or a client reference. A signed invoice event cannot find the practice through the invoice lane customer.

Two current prices are validated on the same Stripe account, mode, currency and licensed monthly per-unit shape: public $90 and MCD $60. `price_id_for_practice` uses the already verified origin channel: mcd selects MCD, direct/catholic select public. Frozen Checkout retries use the same mapping. Bound subscriptions also accept the runtime legacy list at $89. Wrong current tiers alert without revocation; other prices fail existing contract validation. New Checkout never uses legacy prices.

`STRIPE_INTEGRATION_ENABLED` gates authenticated maintenance, signature-ready webhooks and portal readiness. `STRIPE_BILLING_ENABLED` additionally gates new acquisition; acquisition needs both distinct current prices. `PUBLIC_ONBOARDING_ENABLED` remains the separate new-practice rollout. Flags do not grant or revoke access themselves. Optional existing recovery and portal operations preserve their current permissions. No flag value or live setting changes in F1.

## Complete runtime flag and readiness call-site inventory

The following functions call `_request`, a `stripe_*_configured` helper, `_validate_runtime_configuration`, or reference the two Stripe flags. The source-scanning trace test enforces this inventory. `app/server.py:billing_startup_lifespan` calls `validate_startup_configuration`, which validates both current prices on integration-enabled startup:

- `app/configuration/public_onboarding.py`: `public_onboarding_configured`
- `app/configuration/stripe.py`: `stripe_billing_configured`, `stripe_portal_configured`, `stripe_runtime_configured`, `stripe_webhook_configured`
- `app/routes/internal.py`: `_practice_payload`, `create_billing_checkout`, `create_billing_portal`, `lookup_practice`
- `app/routes/webhooks.py`: `stripe_webhook`
- `app/services/invoice_billing.py`: `_complete_invoice_creation`, `bill_by_invoice`, `sync_invoice_subscription_quantity`
- `app/services/stripe_billing.py`: `_apply_authoritative_subscription`, `_matching_frozen_checkout_sessions`, `_recover_checkout_session`, `_request`, `_retire_open_checkout_session`, `_retrieve_subscription`, `_retrieve_subscription_identity`, `_sync_subscription_quantity_leased`, `_validate_price`, `_validate_runtime_account`, `_validated_portal_configuration`, `apply_stripe_event`, `create_checkout_session`, `create_portal_session`, `reconcile_frozen_checkout_request`

All fixtures are synthetic and Stripe transport is mocked. No live users were inspected. Staging requires a human to create test-mode $90 and $60 prices, set the price-id and mcd-price-id vault fields, and create legacy-price-ids (empty is allowed). Before repointing price-id to $90, the human must put the old $89 price in legacy-price-ids. This prerequisite is outside this PR. No prices or vault items are created here. Merge does not promote or publish; one draft PR, no deploy.
