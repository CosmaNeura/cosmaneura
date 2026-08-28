# Stripe billing activation

This is the required founder-gated packet for changing
`STRIPE_BILLING_ENABLED` from `false` to `true`. Running the preflight is
read-only unless `--apply` is supplied; this document does not authorize an
apply, deployment, live charge, or flag change. The gate addresses two
properties that the flag itself cannot provide:

- the acquisition flag stops creation of new Checkout Sessions, but an already
  issued Stripe-hosted URL can still complete until Stripe expires it; and
- Stripe retains POST idempotency results for at least 24 hours. The
  card-required application release uses a distinct idempotency namespace, but
  the drain remains required so no pre-change cardless URL survives activation.

The activation packet and its plan digest use policy namespace
`paid-pilot-v2`; evidence from the older activation contract is not reusable.

Do not describe `STRIPE_BILLING_ENABLED=false` as a hard stop. The integration
and webhook must remain enabled for subscription maintenance, and existing
hosted sessions remain usable until they complete or expire.

The production offer is one active recurring price: USD $89.00 per licensed
active-clinician seat each month (`unit_amount=8900`, `usage_type=licensed`,
`billing_scheme=per_unit`, no quantity transform). The practice seat ceiling is
20 and remains enforced by Core; Stripe is an asynchronous quantity mirror.
The 14-day trial begins at Checkout before credential approval and does not
pause or reset while credential review is pending. Real-PHI access remains
fail-closed unless credential status is `approved`.

The behavior at trial expiry while credential review is still pending is not
yet approved. The preflight deliberately has no default. It accepts only an
explicit owner-approved value: `charge_scheduled`, `extend_trial`, or
`hold_conversion`. Supplying a value records the decision; it does not itself
implement or change billing behavior. Until the chosen policy is approved and
implemented in the deployed release, omit the option and expect
`ready_for_acquisition: false`.

## Canonical entitlement state contract

Billing consumes Core's additive
`public-capability-entitlements.v1` contract at
`contracts/public-entitlements/v1/capability-entitlement.schema.json` (policy
revision `2026-08-27`, schema SHA-256
`a33ba095ea284575fc8004ecc7b2e05b6aa60c911f5c03add3a0ea1f7ef9e578`).
Core remains authoritative; Stripe is a durable asynchronous mirror. Unknown
or missing states fail closed.

| Billing state | Credential state | Capability result for public self-service |
| --- | --- | --- |
| `trialing` or `active` | `approved` with reviewer evidence | Billing and credential gates may pass; each capability still requires membership, legal, cohort, and rollout gates. |
| `trialing` or `active` | `pending`, `needs_review`, or `rejected` | Real-PHI access denied. A separately enabled synthetic-only sandbox is not a protected-route grant. The trial clock continues. |
| `none`, `incomplete`, `incomplete_expired`, `past_due`, `unpaid`, `paused`, or `canceled` | any | Subscription gate denied for public self-service. |
| `pending_verification`, `invalid`, missing, or unknown | any | Denied fail-closed; reconcile authenticated Stripe state before retry. |

For the separately managed/MCD path, `credential_required=false` preserves its
existing managed controls; it is not a public self-service bypass. Unfinished
capabilities default off. Pending invitations do not count as seats; active,
non-revoked memberships do, including the owner, up to 20.

## Reconciled deployed-state snapshot

The following is read-only evidence observed at `2026-08-27T19:17:30Z` and
revalidated at `2026-08-28T00:22:05Z`, not an activation approval:

- Production API revision
  `cosmaneura-prod-app--r32865876578a1-ad25836`, worker revision
  `cosmaneura-prod-worker--r32865876578a1-ad25836`, and scheduler revision
  `cosmaneura-prod-scheduler--r32865876578a1-ad25836` were healthy/running on
  image `cosmaneuraprodregistry.azurecr.io/cosmaneura/cosmaneura:ad25836b9707e9bc8986422686df516386fee6e5`;
  the API held 100% traffic.
- Production API and worker reported `STRIPE_INTEGRATION_ENABLED=false`,
  `STRIPE_BILLING_ENABLED=false`, `STRIPE_LIVE_MODE=true`, and Stripe API
  version `2025-09-30.clover`. Account/price environment names were present
  with empty literal values, the portal configuration was not deployed, and no
  deployed `STRIPE_SECRET_KEY` or `STRIPE_WEBHOOK_SECRET` secret-reference
  entries were present. Source `.env.production.backend` has conditional
  secret-manager expressions; those are not evidence of deployed secret refs.
  Account/product/price values, the pinned Billing Portal configuration, and
  the webhook endpoint were not verified. With integration disabled, webhook
  and portal maintenance is unavailable; production activation is blocked.
- Staging API and worker used source/image tag
  `22eaf3ca9d64a07ad662ff6a3a9066d3b393a081`, with integration and acquisition
  disabled and live mode false. Account/price/portal configuration and Stripe
  secret references were absent, so staging cannot yet produce a complete
  test-mode activation packet.
- The earliest conservative drain start is `2026-08-27T19:17:30Z`; do not
  backdate it. Its 24h05 completion is `2026-08-28T19:22:30Z`, provided no later
  code rollout or acquisition-disable time resets the clock and every prior
  revision is confirmed out of service.

The exact rollback target for a future pre-acquisition dark deploy is the three
production revisions and image above. Once any subscriber exists, first disable
new acquisition while keeping integration/webhooks enabled; do not roll back to
an integration-disabled revision. A reviewed billing-capable post-subscription
code rollback SHA must be recorded before activation.

## Dark deploy and drain

1. Deploy the card-required release with `STRIPE_INTEGRATION_ENABLED=true` and
   `STRIPE_BILLING_ENABLED=false`. Confirm the deployed secret references for
   the Stripe API key and webhook signing secret are configured, but do not
   record their values. Confirm the exact non-secret account, price, and
   `STRIPE_PORTAL_CONFIGURATION_ID` values deployed to the API and worker. Do
   not change either environment directly as part of the code deployment.
2. Confirm every prior revision has stopped serving. Record the later of that
   time and the time acquisition was disabled as `DRAIN_STARTED_AT` in UTC.
3. Wait at least 24 hours and 5 minutes. Twenty-four hours is Stripe's maximum
   Checkout Session lifetime; the extra five minutes is the rollout safety
   buffer. A browser with an old URL may still complete during this interval,
   so keep webhooks and reconciliation running.
4. Inventory every price that could have been used to create a CosmaNeura
   Checkout URL. Compare the current backend `STRIPE_PRICE_ID` with the retired
   Cloudflare Pages value and the Stripe dashboard. Archive every retired price
   in Stripe, then put every different historical value in comma-separated
   `STRIPE_RETIRED_PRICE_IDS`; an empty value is valid only after confirming the
   current price was the sole historical price. The compatibility alias
   `STRIPE_LEGACY_PRICE_IDS` is accepted only when the canonical variable is
   absent; conflicting values fail closed. Record the one exact paid-pilot
   product as `STRIPE_PRODUCT_ID`. The preflight retrieves that product, checks
   that it is active and defaults to the current price, then paginates the
   account's active and inactive price collections. Every price attached to
   the current product or any product discovered through an inventoried retired
   price must be explicitly accounted for; an extra or missing price fails
   closed. A retired website price may belong to a separate historical product.
5. Audit completed retired-website Checkout Sessions in Stripe before
   activation. That legacy flow had no practice reference or metadata, so a
   completed subscription cannot be assigned to a practice automatically.
   Reconcile any such subscriptions before accepting new purchases, and record
   the query time, result count, and disposition in a redacted artifact. The
   artifact must be created after `drain_complete_at`. Pass
   `--confirm-completed-session-reconciliation`, its UTC
   `--completed-session-reconciliation-at`, a non-sensitive
   `--completed-session-reconciliation-ref`, and the artifact's lowercase
   SHA-256 only after that record exists. A stale, future-dated, missing, or
   malformed record keeps acquisition blocked.
6. Record the exact expected webhook URL in `STRIPE_WEBHOOK_ENDPOINT_URL`. The
   preflight requires exactly one matching enabled endpoint in the expected
   Stripe mode, pinned to API version `2025-09-30.clover`, with exactly these
   events (wildcard or additional events do not pass):

   - `checkout.session.completed`
   - `customer.subscription.created`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`
   - `customer.subscription.paused`
   - `customer.subscription.resumed`

7. Record the exact active Billing Portal configuration in
   `STRIPE_PORTAL_CONFIGURATION_ID`. The preflight retrieves that `bpc_`
   object and requires the expected mode, payment-method updates enabled,
   cancellation only at period end, and customer, subscription, and pause
   controls disabled. This prevents the portal from changing the Core-owned
   seat quantity or bypassing lifecycle policy.
8. Inspect the Stripe account-health result. In live mode, activation requires
   `charges_enabled`, `payouts_enabled`, and `details_submitted` to be true,
   with no disabled reason and no currently-due or past-due requirements.
9. Run the full-account preflight with the expected Stripe account, explicit
   mode, complete price inventory, exact portal configuration, and exact
   webhook URL. The command verifies the secret-key mode, account health,
   current offer, inactive retired prices, portal policy, and webhook contract
   before paginating every open and expired Checkout Session and every active
   Payment Link. It recognizes CosmaNeura sessions and links by a valid
   `practice_` reference or any exact inventoried price, including the retired
   website's direct Checkout links. Active recovery URLs, ambiguous expired
   sessions, and active or ambiguous Payment Links block activation; the
   preflight never disables a Payment Link or guesses how to dispose of a
   completed subscription.

## Evidence records required before production activation

Each confirmation below attests to a separately reviewed, redacted artifact;
the preflight does not manufacture operational evidence. References must be
non-sensitive and artifact digests must be lowercase SHA-256 values.

| Gate | Minimum evidence | Preflight input |
| --- | --- | --- |
| Completed-session reconciliation | Post-drain query time, aggregate count, and disposition for completed legacy Checkout Sessions, including any subscription-to-practice reconciliation. | `--confirm-completed-session-reconciliation`, `--completed-session-reconciliation-at`, `--completed-session-reconciliation-ref`, `--completed-session-reconciliation-sha256` |
| Stripe test lifecycle | Test-mode results for card-required Checkout, the exact 14-day trial, customer/practice binding, quantities 1–20 and rejection of 21, signed/replayed/reordered webhooks, reconciliation, pinned Portal launch, cancellation, past-due/payment recovery, deletion, and stale/abandoned Checkout recovery. | `--confirm-test-mode-lifecycle`, `--test-mode-lifecycle-evidence-ref`, `--test-mode-lifecycle-evidence-sha256` |
| Observability | PHI-free metric/dashboard and alert-delivery evidence for Checkout failures, webhook lag and processing failures, entitlement mismatches, cancellation/recovery, and seat reconciliation. | `--confirm-observability-ready`, `--observability-evidence-ref`, `--observability-evidence-sha256` |

Do not set a confirmation from source tests alone when the gate requires a real
Stripe test-mode run or deployed alert delivery. Missing evidence is expected
to produce `ready_for_acquisition: false`.

The JSON evidence includes exact reviewed account and price IDs plus aggregate
counts and contract digests. It never prints secrets, webhook URLs, webhook
endpoint IDs, raw Checkout Session IDs, Checkout URLs, or practice identifiers.
Use a non-sensitive deployment/change reference for `--operator-ref`.

## Read-only packet while the policy is unresolved

The following test-mode form is safe for validating the packet shape. Omit the
policy and approval confirmations while they remain unresolved. A successful
audit exits `1` and records the failed gates; it cannot report production
readiness.

```sh
STRIPE_SECRET_KEY='...' \
STRIPE_ACCOUNT_ID='acct_...' \
STRIPE_PRODUCT_ID='prod_...' \
STRIPE_PRICE_ID='price_...' \
STRIPE_PORTAL_CONFIGURATION_ID='bpc_...' \
STRIPE_RETIRED_PRICE_IDS='price_old_1,price_old_2' \
STRIPE_WEBHOOK_ENDPOINT_URL='https://test.example.invalid/external/v1/webhooks/stripe' \
python scripts/stripe_checkout_activation_preflight.py \
  --operator-ref '<deployment-ticket-or-change-id>' \
  --drain-started-at "$DRAIN_STARTED_AT" \
  --expected-mode test \
  --confirm-acquisition-disabled \
  --confirm-old-code-stopped \
  --confirm-price-inventory-complete
```

## Founder-approved production packet

Do not add `--confirm-secret-configuration` until the deployed API key,
webhook-signing secret, account/price/portal configuration, API version,
integration flag, and disabled acquisition flag have been independently
reconciled. Do not add any completed-session, test-lifecycle, or observability
confirmation until its redacted artifact and digest satisfy the table above.
Do not add `--confirm-founder-owner-approval`, `--owner-approval-ref`, or a
trial-expiry policy value until the owner has made and recorded those decisions.
An allowed policy string is not implementation evidence. The packet also
requires a non-sensitive implementation record, the full reviewed source SHA,
and the exact deployed source SHA; the SHAs must match. Omit these arguments
while policy behavior is not implemented and deployed.

```sh
STRIPE_SECRET_KEY='...' \
STRIPE_ACCOUNT_ID='acct_...' \
STRIPE_PRODUCT_ID='prod_...' \
STRIPE_PRICE_ID='price_...' \
STRIPE_PORTAL_CONFIGURATION_ID='bpc_...' \
STRIPE_RETIRED_PRICE_IDS='price_old_1,price_old_2' \
STRIPE_WEBHOOK_ENDPOINT_URL='https://production.example.invalid/external/v1/webhooks/stripe' \
python scripts/stripe_checkout_activation_preflight.py \
  --operator-ref '<deployment-ticket-or-change-id>' \
  --drain-started-at "$DRAIN_STARTED_AT" \
  --expected-mode live \
  --confirm-acquisition-disabled \
  --confirm-old-code-stopped \
  --confirm-price-inventory-complete \
  --confirm-secret-configuration \
  --confirm-completed-session-reconciliation \
  --completed-session-reconciliation-at '<post-drain-UTC-timestamp>' \
  --completed-session-reconciliation-ref '<completed-session-audit-record>' \
  --completed-session-reconciliation-sha256 '<lowercase-sha256>' \
  --confirm-test-mode-lifecycle \
  --test-mode-lifecycle-evidence-ref '<test-lifecycle-record>' \
  --test-mode-lifecycle-evidence-sha256 '<lowercase-sha256>' \
  --confirm-observability-ready \
  --observability-evidence-ref '<observability-record>' \
  --observability-evidence-sha256 '<lowercase-sha256>' \
  --confirm-founder-owner-approval \
  --owner-approval-ref '<approval-record>' \
  --pending-credential-trial-expiry-policy "$OWNER_APPROVED_POLICY" \
  --policy-implementation-evidence-ref '<implementation-record>' \
  --reviewed-source-sha '<reviewed-full-git-sha>' \
  --deployed-source-sha '<matching-deployed-full-git-sha>'
```

Store the single JSON output line in the deployment change record. It is the
audit evidence: explicit mode and account checks, the verified $89 monthly
licensed current price, inactive retired-price inventory, account health,
restricted Portal policy, exact webhook contract status, full pagination
counts for open sessions, expired recoveries, and active Payment Links, drain
timestamps, aggregate classifications, a digest of open identifiers, evidence
references and digests, operator confirmations, policy status, and the
canonical plan digest. Test mode always leaves the production mode prerequisite
false.

If eligible open sessions remain, review the dry-run evidence and apply that
exact plan:

```sh
STRIPE_SECRET_KEY='...' \
STRIPE_ACCOUNT_ID='acct_...' \
STRIPE_PRODUCT_ID='prod_...' \
STRIPE_PRICE_ID='price_...' \
STRIPE_PORTAL_CONFIGURATION_ID='bpc_...' \
STRIPE_RETIRED_PRICE_IDS='price_old_1,price_old_2' \
STRIPE_WEBHOOK_ENDPOINT_URL='https://production.example.invalid/external/v1/webhooks/stripe' \
python scripts/stripe_checkout_activation_preflight.py \
  --operator-ref '<same-deployment-ticket-or-change-id>' \
  --drain-started-at "$DRAIN_STARTED_AT" \
  --expected-mode live \
  --confirm-acquisition-disabled \
  --confirm-old-code-stopped \
  --confirm-price-inventory-complete \
  --confirm-secret-configuration \
  --confirm-completed-session-reconciliation \
  --completed-session-reconciliation-at '<post-drain-UTC-timestamp>' \
  --completed-session-reconciliation-ref '<completed-session-audit-record>' \
  --completed-session-reconciliation-sha256 '<lowercase-sha256>' \
  --confirm-test-mode-lifecycle \
  --test-mode-lifecycle-evidence-ref '<test-lifecycle-record>' \
  --test-mode-lifecycle-evidence-sha256 '<lowercase-sha256>' \
  --confirm-observability-ready \
  --observability-evidence-ref '<observability-record>' \
  --observability-evidence-sha256 '<lowercase-sha256>' \
  --confirm-founder-owner-approval \
  --owner-approval-ref '<approval-record>' \
  --pending-credential-trial-expiry-policy "$OWNER_APPROVED_POLICY" \
  --policy-implementation-evidence-ref '<implementation-record>' \
  --reviewed-source-sha '<reviewed-full-git-sha>' \
  --deployed-source-sha '<matching-deployed-full-git-sha>' \
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
- `stripe_mode_verified: true` and the `production_mode_verified` prerequisite
  is true
- `stripe_account_id` and `checkout_price_ids` exactly match the reviewed
  production inventory
- `stripe_product_id`, `current_price_id`, and `retired_price_ids` exactly match
  the reviewed production inventory
- `stripe_product_verified: true`, `current_offer_verified: true`, and
  `retired_price_inventory_verified: true`; the account-wide paginated price
  scan found no uninventoried price for the paid-pilot product
- `stripe_portal_configuration_id` exactly matches the reviewed deployed
  `bpc_` value and `stripe_portal_configuration_verified: true`
- `webhook_endpoint_verified: true`, with the reviewed event count and contract
  digest (the URL and endpoint ID remain redacted)
- `acquisition_disabled_confirmed: true`
- `old_code_stopped_confirmed: true`
- `price_inventory_confirmed: true`
- `secret_configuration_confirmed: true`
- `completed_session_reconciliation_confirmed: true`, with a non-null
  `completed_session_reconciliation_ref`, qualifying reconciliation timestamp,
  and evidence SHA-256
- `test_mode_lifecycle_confirmed: true`, with a non-null evidence reference and
  SHA-256, and the `test_mode_lifecycle_evidenced` prerequisite true
- `observability_ready_confirmed: true`, with a non-null evidence reference and
  SHA-256, and the `observability_readiness_evidenced` prerequisite true
- `founder_owner_approval_confirmed: true` and a non-null `owner_approval_ref`
- `pending_credential_trial_expiry_policy_resolved: true`, with the recorded
  value matching the implemented owner-approved policy
- `policy_implementation_evidenced: true`, with a non-null implementation
  evidence reference
- `reviewed_source_matches_deployed: true`; both full source SHAs are present
  and exactly equal
- `drain_satisfied: true`
- `eligible_cosmaneura_open_sessions: 0`
- `ambiguous_cosmaneura_open_sessions: 0`
- `recoverable_expired_cosmaneura_sessions: 0` and
  `ambiguous_expired_cosmaneura_sessions: 0`
- `active_paid_pilot_payment_links: 0` and
  `ambiguous_active_payment_links: 0`
- every value in `activation_prerequisites` is `true`
- `ready_for_acquisition: true`
- process exit status `0`

Only then enable acquisition. Keep the integration/webhook flag enabled during
rollback; switch off acquisition first so existing subscriptions, portal flows,
and webhook state continue to reconcile.

## Observation and abort gates

Before activation, record an owner-approved observation duration and named
operator; neither is evidenced in the snapshot above. During that window,
disable acquisition immediately on any of these proposed hard-abort conditions:

- any Checkout creation or contract-validation failure;
- any webhook signature, durable-persistence, or processing failure, or oldest
  actionable webhook lag above 60 seconds (two scheduler intervals);
- any pending/failed seat-quantity sync or Core-versus-Stripe entitlement
  mismatch;
- any duplicate subscription or charge, or unexpected cancellation;
- any raw identifier, payment detail, or PHI in telemetry; or
- any real-PHI grant while credential state is `pending`, `needs_review`, or
  `rejected`.

These thresholds remain proposed gates until the corresponding PHI-free
observability and alert delivery are proven. On abort, preserve evidence,
disable acquisition, keep integration/webhooks running for existing customers,
reconcile Stripe into Core, and use only the rollback target appropriate to
whether a subscriber has been created.

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

If a network failure occurs after Core durably freezes a Checkout request but
before it caches the returned Session ID, use the operator recovery command.
Keep acquisition disabled throughout recovery and retain a PHI-free export of
the Stripe Checkout Session inventory plus its SHA-256. Start with the read-only
default:

```bash
direnv exec . uv run python -m app.cli reconcile-stripe-checkout \
  --practice-id practice_SYNTHETIC \
  --operator-ref ticket-CHECKOUT-RECOVERY \
  --reason "Resolve a frozen Checkout attempt" \
  --evidence-reference evidence://stripe/checkout-recovery.json \
  --evidence-sha256 SHA256_OF_RETAINED_EVIDENCE
```

Review the reported `would_*` outcome, then repeat the exact command with
`--apply`. The apply path first caches the discovered exact Session under Core's
Checkout and billing version fences, appends an audit record, and then safely
expires it or binds its authoritative subscription. Expected applied outcomes
are `operator_expired_open_session`, `operator_bound_completed_subscription`,
`operator_bound_completed_session`, `operator_bound_incomplete_subscription`,
`operator_cleared_expired_session`, or
`operator_resolved_expired_subscription`. Reconcile the resulting Core billing
state before retrying Checkout.

If the inventory contains no exact match, do not infer absence until the frozen
request's replay deadline plus the five-minute transport-safety interval has
elapsed. With acquisition still disabled, a reviewed operator may then append
`--apply --confirm-absent`; this records the evidence before advancing the
attempt. Any partially matching Session, duplicate exact Session, malformed or
truncated inventory, page-limit exhaustion, version conflict, or ambiguous
provider response aborts without clearing the frozen Core request. Resolve that
condition in Stripe and rerun the dry-run; never repair these fields directly in
the database.
