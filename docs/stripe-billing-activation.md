# Stripe billing activation

This is the required gate for changing `STRIPE_BILLING_ENABLED` from `false`
to `true`. It addresses two properties that the flag itself cannot provide:

- the acquisition flag stops creation of new Checkout Sessions, but an already
  issued Stripe-hosted URL can still complete until Stripe expires it; and
- Stripe retains POST idempotency results for at least 24 hours. The
  `card-required-v1` application release uses a new idempotency namespace, but
  the drain remains required so no pre-change cardless URL survives activation.

Do not describe `STRIPE_BILLING_ENABLED=false` as a hard stop. The integration
and webhook must remain enabled for subscription maintenance, and existing
hosted sessions remain usable until they complete or expire.

## Dark deploy and drain

1. Deploy the card-required release with `STRIPE_INTEGRATION_ENABLED=true` and
   `STRIPE_BILLING_ENABLED=false`. Do not change either environment directly as
   part of the code deployment.
2. Confirm every prior revision has stopped serving. Record the later of that
   time and the time acquisition was disabled as `DRAIN_STARTED_AT` in UTC.
3. Wait at least 24 hours and 5 minutes. Twenty-four hours is Stripe's maximum
   Checkout Session lifetime; the extra five minutes is the rollout safety
   buffer. A browser with an old URL may still complete during this interval,
   so keep webhooks and reconciliation running.
4. Inventory every price that could have been used to create a CosmaNeura
   Checkout URL. Compare the current backend `STRIPE_PRICE_ID` with the retired
   Cloudflare Pages value and the Stripe dashboard. Put every different older
   value in comma-separated `STRIPE_LEGACY_PRICE_IDS`; an empty value is valid
   only after confirming the current price was the sole historical price.
5. Audit completed retired-website Checkout Sessions in Stripe before
   activation. That legacy flow had no practice reference or metadata, so a
   completed subscription cannot be assigned to a practice automatically.
   Reconcile any such subscriptions before accepting new purchases, and record
   the query time, result count, and disposition in the change record.
6. Run the full-account preflight with the expected Stripe account and complete
   price inventory. The command paginates every open Checkout Session. It
   recognizes CosmaNeura sessions by a valid `practice_` reference or any exact
   inventoried price, including the retired website's direct Checkout links.
   It never prints raw session or practice identifiers.

```sh
STRIPE_SECRET_KEY='...' \
STRIPE_ACCOUNT_ID='acct_...' \
STRIPE_PRICE_ID='price_...' \
STRIPE_LEGACY_PRICE_IDS='price_old_1,price_old_2' \
python scripts/stripe_checkout_activation_preflight.py \
  --operator-ref '<deployment-ticket-or-change-id>' \
  --drain-started-at "$DRAIN_STARTED_AT" \
  --live-mode \
  --confirm-acquisition-disabled \
  --confirm-old-code-stopped \
  --confirm-price-inventory-complete
```

Omit `--live-mode` for staging/test mode. Store the single JSON output line in
the deployment change record. It is the audit evidence: account/mode checks,
full pagination count, drain timestamps, aggregate classifications, a digest of
the open identifiers, the verified Stripe account/current-and-legacy price
inventory, all three operator confirmations, and the canonical plan digest.

If eligible open sessions remain, review the dry-run evidence and apply that
exact plan:

```sh
STRIPE_SECRET_KEY='...' \
STRIPE_ACCOUNT_ID='acct_...' \
STRIPE_PRICE_ID='price_...' \
STRIPE_LEGACY_PRICE_IDS='price_old_1,price_old_2' \
python scripts/stripe_checkout_activation_preflight.py \
  --operator-ref '<same-deployment-ticket-or-change-id>' \
  --drain-started-at "$DRAIN_STARTED_AT" \
  --live-mode \
  --confirm-acquisition-disabled \
  --confirm-old-code-stopped \
  --confirm-price-inventory-complete \
  --apply \
  --confirm-plan-sha256 '<dry-run-plan-sha256>'
```

Apply expires only open, subscription-mode sessions using an inventoried price,
the expected Stripe mode, a lifetime no longer than 24 hours, and no attached
subscription. An exact inventoried price is sufficient regardless of reference
shape because the retired website flow did not set a practice reference. Each
expiration has a deterministic idempotency key. Anything recognized by a valid
practice reference or inventoried price but failing another invariant blocks the
command and must be reconciled before activation; it is never expired by guess.
After applying, the command performs another full-account scan.

Activation is allowed only when the final evidence has all of:

- `account_verified: true`
- `stripe_account_id` and `checkout_price_ids` exactly match the reviewed
  production inventory
- `acquisition_disabled_confirmed: true`
- `old_code_stopped_confirmed: true`
- `price_inventory_confirmed: true`
- `drain_satisfied: true`
- `eligible_cosmaneura_open_sessions: 0`
- `ambiguous_cosmaneura_open_sessions: 0`
- `ready_for_acquisition: true`
- process exit status `0`

Only then enable acquisition. Keep the integration/webhook flag enabled during
rollback; switch off acquisition first so existing subscriptions, portal flows,
and webhook state continue to reconcile.

## Runtime recovery after activation

Every new request and retrieved open session must report
`payment_method_collection=always`. A cached legacy session is expired remotely
before its local attempt is rotated. If expiration races with completion, the
completed subscription is retrieved and bound instead of starting another.
If the expiration result is ambiguous, no local state is cleared and no second
session is created.

An unusual legacy session may already reference an `incomplete` subscription.
After the session is authoritatively expired, CosmaNeura binds that subscription,
clears the dead hosted-session cache, and returns a retryable conflict. It does
not cancel a possibly billable Stripe object or create a second subscription.
Once Stripe reports `incomplete_expired` or another terminal status, the next
attempt can safely create a card-required replacement.

### Frozen request without a cached Session

If Core froze a Checkout request but lost the provider response, do not clear
the request or create a new attempt manually. First retain a PHI-free copy of
the Stripe Checkout Session inventory used for the decision and hash it. Then
preview the bounded recovery action:

```sh
python -m app.cli reconcile-stripe-checkout \
  --practice-id 'practice_...' \
  --operator-ref '<incident-or-change-id>' \
  --reason '<reason for recovery>' \
  --evidence-reference '<retained-evidence-reference>' \
  --evidence-sha256 '<64-character-sha256>'
```

The command verifies the configured Stripe account, mode, and price; searches
at most ten 100-item pages beginning five minutes before the frozen request;
and refuses partial, duplicate, or conflicting practice matches. Preview mode
does not mutate Core or Stripe.

After checking the preview, repeat the same command with `--apply`. An exact
open Session is bound and expired; a completion race is bound to its
authoritative subscription; and an expired Session is resolved before Core
rotates the attempt. Every applied action writes append-only operator evidence.

If the result is `no_matching_session`, keep
`STRIPE_BILLING_ENABLED=false`, wait until the frozen request is at least 24
hours old, and add both `--apply` and `--confirm-absent`. This is the only path
that may attest the provider Session absent and rotate the attempt without a
remote Session id. The integration/webhook flag must remain enabled throughout
recovery.
