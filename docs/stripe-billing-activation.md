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
`paid-pilot-v3`; evidence from older activation contracts is not reusable.
The governing product boundaries are architecture PR 10, ADRs 0022–0024, at
reviewed head `5451df63e2fbeaf386ca5517d762e6d1359b053c`. If that head changes, review
and record the replacement architecture commit before activation.

Do not describe `STRIPE_BILLING_ENABLED=false` as a hard stop. The integration
and webhook must remain enabled for subscription maintenance, and existing
hosted sessions remain usable until they complete or expire.

Billing Portal maintenance is independent of the new-acquisition switch, but
not of webhook verification. Core must not create a Portal Session while its
Stripe signing secret is unavailable: payment-method or cancellation changes
would otherwise occur without a trustworthy path back into the authoritative
subscription projection. Portal access also requires a complete Core binding
(`cus_` customer and `sub_` subscription) that still matches the remotely
retrieved subscription; a customer ID alone is not authority. Public rows also
require a positive Stripe state version. A pre-receiver managed/legacy row may
start at version zero, but only its exact remotely verified customer and
subscription identity can be used.

That ownership check validates the stable subscription/customer/account
identity, not the current offer shape. A legitimately bound canceled or
incomplete-expired subscription may use a retired price or legacy metadata and
must still be able to open the Portal or start a replacement Checkout. Core
binding changes are version-fenced before a hosted URL is returned. Partial or
malformed Core bindings are a local reconciliation conflict, not represented as
a Stripe-originated failure.

The production offer is one active recurring price: USD $89.00 per licensed
active-clinician seat each month (`unit_amount=8900`, `usage_type=licensed`,
`billing_scheme=per_unit`, no quantity transform). The practice seat ceiling is
20 and remains enforced by Core; Stripe is an asynchronous quantity mirror.
The 14-day trial begins at Checkout before credential approval and does not
pause or reset while credential review is pending. Real-PHI access remains
fail-closed unless credential status is `approved`.

For a new public practice, Core freezes one absolute trial end at the initial
Checkout request and promotes it only after Stripe returns or recovery finds
the matching hosted Session. Stripe receives that absolute end; completing,
resuming, or replacing the Session cannot restart the clock. A hosted Session
and its fixed clock do not grant entitlement without a verified subscription.
Public signup and onboarding do not collect, validate, or submit NPI. Billing
does not inspect it, and it is neither authentication nor credential proof. A
future enabled payer-facing HIPAA-standard transaction or integration may
collect it just in time only when that transaction demonstrably requires it.
CAB remains billing-intelligence/advisory only: it has no EHR-write,
837P/claim-submission, clearinghouse, or payer-enrollment scope, and those
capabilities are not on the roadmap.

The byte-pinned `CN-BAA-CUST-CW` v2.2 text nevertheless contains the phrase
“claims preparation and submission support.” This runbook does not amend or
reinterpret that language. Its canonical SHA-256 remains
`13448bf42105db05e36e566f2f8ea0190926223e9dbb03efb8778cf8cfc7332e`, and
public promotion must remain blocked until counsel supplies a written
reconciliation of that exact agreement with the advisory-only, no-EHR-write,
no-837P, no-claim-submission, no-clearinghouse, and no-payer-enrollment product
boundary. If counsel requires different text, publish a new agreement version
and obtain a new acceptance record; never edit v2.2 in place.

This branch consumes the independently reviewed NPI-free Core receiver at exact
commit `685e2cb84a63b02faf078a4aa9b06e4f581572bd`. Nullable historical model
storage remains solely for legacy compatibility; the public onboarding form,
request contract, validation and persistence path, generated OpenAPI/client,
and Stripe metadata neither send nor inspect NPI. The activation packet still
requires an explicit confirmation that this exact receiver is an ancestor of
the reviewed and deployed Stripe source.

The behavior at trial expiry while credential review is still pending is not
yet approved. The preflight deliberately has no default. It accepts only an
explicit owner-approved value: `charge_scheduled`, `extend_trial`, or
`hold_conversion`. Supplying a value records the decision; it does not itself
implement or change billing behavior. Until the chosen policy is approved and
implemented in the deployed release, omit the option and expect
`ready_for_acquisition: false`.

The mid-cycle seat-change charge/credit policy—including proration and invoice
timing—is also unresolved. Stripe defaults are not policy evidence. Acquisition
must remain disabled until an owner-approved behavior is explicitly implemented
and evidenced; the current reconciler intentionally does not claim readiness
for an implicit provider default.

## Canonical entitlement state contract

New billing state consumes and stamps Core's current additive
`public-capability-entitlements.v2` contract at
`contracts/public-entitlements/v2/capability-entitlement.schema.json` (policy
revision `2026-08-27`, schema SHA-256
`a51843ed0810a2b43fd16cf6e66eb2830717c8381aa7fea3f8d05b9e55cb1bad`).
Previously created Stripe Sessions and subscriptions remain read-compatible
only with the byte-pinned v1 contract (SHA-256
`a33ba095ea284575fc8004ecc7b2e05b6aa60c911f5c03add3a0ea1f7ef9e578`).
Frozen v1 Checkout requests replay byte-for-byte under their original
idempotency key; all new requests write v2. Core remains authoritative and
Stripe is a durable asynchronous mirror. Missing or unknown versions fail
closed.

### Downstream interface and evidence inventory

The accepted receiver is PR
[#199](https://github.com/CosmaNeura/cosmaneura/pull/199) at exact commit
`685e2cb84a63b02faf078a4aa9b06e4f581572bd`. Its full generated OpenAPI digest
is `adff41bb2389f9f9aae34e1a0284605f35f28fb4e27f92de94abf020b432375e`.
The Stripe consumer PR URL and branch must be recorded here after the branch is
pushed and before independent review. The post-Stripe full OpenAPI SHA-256 is
`7e556528e811fb53712520c77b42b7aca54e75a7e53f439489c124cd2857ffec`; the
immutable reviewed consumer head is recorded in the PR/reviewer handoff because
a commit cannot self-identify its own hash. The receiver's canonical
`GET /internal/v1/capabilities` projection (path plus referenced schemas) must
remain SHA-256
`62babdb18afcc3bb9d902d9afb652fd9f6aa20bf35973a1959c52451b7cf9636`.
The canonical-sorted path object alone remains
`5942ec15e42ab39bd828518429b8838acfc2759757406c30dd011185f3bc5419`.
The v1 and v2 pending fixtures remain, respectively,
`de31b038705784b50a46fec694fbee4f94fabef0e2087afb10e6c69c703cad06`
and `3e29acc8c80ca925403b322070fc8668c73a28b822eac1e78fd9e062b2cb52df`.

The additive migration order is
`89c31f0ae275 -> d0a4c8e2f611 -> e7a9c3d5f102 -> da6e199668a4`. The first
safe mutating-code floor after `e7a9c3d5f102` is receiver commit
`d8bf0ba21a5cf1adc3f98ca4b4208d9d6f852756`; the reviewed deployment/restack
floor is the complete receiver
`685e2cb84a63b02faf078a4aa9b06e4f581572bd` or a later compatible descendant.

Idempotency is explicit at each boundary:

- `GET /internal/v1/capabilities` has idempotency `none`; it is an
  authenticated, side-effect-free read with no actor or tenant selector.
- Public invitation creation requires the caller's `Idempotency-Key` header and
  binds its digest to the exact practice/email mutation.
- Stripe Checkout creation, Checkout expiration, and subscription-item quantity
  changes send deterministic server-generated `Idempotency-Key` headers. Browser
  input never selects those provider keys. Webhook delivery deduplicates by
  Stripe event ID before processing.

The named durable access-audit actions added or consumed by the public
invitation/seat and Stripe-recovery slice are
`public_invitation_issued`, `public_invitation_accepted`,
`public_invitation_revoked`, `clinician_seat_deactivated`,
`clinician_seat_reactivated`, and `stripe_checkout_recovery_started`. Stripe
webhook evidence is retained separately in `stripe_event_inbox` and
`stripe_event_receipt`; BAA, credential-review, billing-exemption,
negotiated-BAA, and account-closure evidence retain their receiver-owned audit
records. PHI-free lifecycle signals use the exact metric names
`checkout_creation`, `checkout_recovery`, `webhook_delivery`,
`webhook_processing`, `entitlement_reconciliation`,
`subscription_cancellation`, `payment_recovery`, and `seat_reconciliation`.

Application defaults are consolidated here:
`STRIPE_INTEGRATION_ENABLED=false`, `STRIPE_BILLING_ENABLED=false`,
`STRIPE_LIVE_MODE=false`, `STRIPE_SECRET_KEY=""`,
`STRIPE_WEBHOOK_SECRET=""`, `STRIPE_ACCOUNT_ID=""`, `STRIPE_PRICE_ID=""`,
`STRIPE_PORTAL_CONFIGURATION_ID=""`, and `STRIPE_API_VERSION=""`. The checked-in
production template deliberately selects `STRIPE_LIVE_MODE=true` and pins
`STRIPE_API_VERSION=2025-09-30.clover`, while leaving integration/acquisition
false and all IDs/secrets empty until an operator supplies reviewed secret
references. The staging template keeps live mode false and the same API-version
pin. Public `soap_note` rollout is enabled but still requires its
legal, bound-subscription, approved-credential, membership, and rollout gates;
`intake_prep`, `session_prep`, `scribe`, and `cab` default disabled. Managed and
backfilled practices default `public_pilot_cohort=false`, and every new public
member starts with credential state `pending`.

Reproduce the source, contract, generated-surface, and test evidence from the
repository root with these literal commands. `rg` is expected to print nothing
and exit 1 for the public NPI scan.

```bash
direnv exec . just js_generate-openapi
git merge-base --is-ancestor 685e2cb84a63b02faf078a4aa9b06e4f581572bd HEAD
git diff --exit-code 685e2cb84a63b02faf078a4aa9b06e4f581572bd -- \
  contracts/public-entitlements/v1/capability-entitlement.schema.json \
  contracts/public-entitlements/v1/synthetic-credential-pending.json \
  contracts/public-entitlements/v2/capability-entitlement.schema.json \
  contracts/public-entitlements/v2/synthetic-credential-pending.json
shasum -a 256 \
  contracts/public-entitlements/v1/capability-entitlement.schema.json \
  contracts/public-entitlements/v1/synthetic-credential-pending.json \
  contracts/public-entitlements/v2/capability-entitlement.schema.json \
  contracts/public-entitlements/v2/synthetic-credential-pending.json \
  app/assets/baa/baa_cn-baa-cust-cw_v2.2.html \
  web/openapi.json
git show 685e2cb84a63b02faf078a4aa9b06e4f581572bd:web/openapi.json | shasum -a 256
jq -cS '.paths["/internal/v1/capabilities"]' web/openapi.json | shasum -a 256
jq -cS '{path:.paths["/internal/v1/capabilities"],schemas:{CapabilityEntitlementResponse:.components.schemas.CapabilityEntitlementResponse,CapabilityActorResponse:.components.schemas.CapabilityActorResponse,CapabilityGatesResponse:.components.schemas.CapabilityGatesResponse,CapabilitySeatsResponse:.components.schemas.CapabilitySeatsResponse,CapabilityMapResponse:.components.schemas.CapabilityMapResponse,CapabilityDecisionResponse:.components.schemas.CapabilityDecisionResponse,CabCapabilityDecisionResponse:.components.schemas.CabCapabilityDecisionResponse}}' web/openapi.json | shasum -a 256
rg -n -i '\bnpi\b|organizational_npi|organization_npi' \
  web/app/routes/onboarding.tsx web/app/lib/practice-api.ts \
  app/routes/public_onboarding.py app/routes/internal.py \
  app/services/stripe_billing.py web/openapi.json web/client
direnv exec . env CI=true PYTHON_ENV=test uv run pytest \
  tests/routes/billing_test.py tests/routes/practice_test.py \
  tests/services/stripe_billing_test.py \
  tests/services/public_entitlements_test.py \
  tests/services/doctor_account_closure_test.py \
  tests/services/organization_access_test.py \
  tests/services/practice_access_test.py \
  tests/integration/stripe_billing_state_race_test.py \
  tests/stripe_checkout_state_migration_test.py \
  tests/infrastructure/test_stripe_checkout_activation_preflight.py
direnv exec . just py_test
direnv exec . just js_test
direnv exec . just lint
direnv exec . just db_lint
git diff --check
git status --short
```

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

The production revisions and image above are rollback targets only before the
`e7a9c3d5f102` public-onboarding schema is applied. After that migration, drain
every pre-gate mutating API revision and never return one to service: the minimum
application rollback floor is reviewed receiver
`685e2cb84a63b02faf078a4aa9b06e4f581572bd` or a later compatible commit. After
any `da6e199668a4` Checkout-state column is populated, retain the additive
`da6e199668a4` schema, stop acquisition, and roll application code forward; do
not discard the fixed-clock or idempotency evidence. Once any subscriber exists,
also keep integration, webhooks, Portal maintenance, and reconciliation enabled.
A reviewed compatible post-subscription code rollback SHA must be recorded
before activation.

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
   so keep integration/webhooks, restricted Billing Portal maintenance, and
   reconciliation running.
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
| NPI-free Core receiver | Exact reviewed 40-hex receiver commit, with generated OpenAPI/client artifacts and tests proving public onboarding rejects and never sends NPI and billing never receives or inspects it. | `--confirm-npi-free-core-receiver`, `--npi-free-core-receiver-sha` |
| Counsel BAA scope reconciliation | Counsel's written reconciliation of byte-pinned `CN-BAA-CUST-CW` v2.2 (SHA-256 `13448bf42105db05e36e566f2f8ea0190926223e9dbb03efb8778cf8cfc7332e`) with CAB's advisory-only/no-EHR-write/no-837P/no-claim-submission/no-clearinghouse/no-payer-enrollment scope. | `--confirm-counsel-baa-scope-reconciliation`, `--counsel-baa-scope-reconciliation-ref`, `--counsel-baa-scope-reconciliation-sha256` |
| Seat-change billing policy | Owner-approved and deployed treatment of mid-cycle activation/deactivation, including proration or credits and invoice timing, with Stripe test-mode evidence for increases and decreases. | `--confirm-seat-change-billing-policy-implemented`, `--seat-change-billing-policy-implementation-evidence-ref` |

Do not set a confirmation from source tests alone when the gate requires a real
Stripe test-mode run or deployed alert delivery. Missing evidence is expected
to produce `ready_for_acquisition: false`.

The JSON evidence includes exact reviewed account and price IDs plus aggregate
counts and contract digests. It never prints secrets, webhook URLs, webhook
endpoint IDs, raw Checkout Session IDs, Checkout URLs, or practice identifiers.
It also records the exact reviewed NPI-free Core receiver SHA and the immutable
BAA document/version/hash bound to counsel's reconciliation. Use a non-sensitive
deployment/change reference for `--operator-ref`.

## Read-only packet while the policy is unresolved

The following test-mode form is safe for validating the packet shape. Omit the
policy and approval confirmations while they remain unresolved. In particular,
omit the NPI-free receiver and counsel-reconciliation confirmations until their
exact evidence exists. A successful audit exits `1` and records the failed
gates; it cannot report production readiness.

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
Add the NPI-free receiver confirmation only when the reviewed Stripe source
retains exact receiver commit `685e2cb84a63b02faf078a4aa9b06e4f581572bd`
in its ancestry and its generated contract, type, and route tests pass. Do not
add the counsel reconciliation confirmation
unless a redacted record and digest exist for counsel's written reconciliation
of the exact v2.2 hash above. Founder approval is not a substitute. Do not add
`--confirm-founder-owner-approval`, `--owner-approval-ref`, a trial-expiry policy
value, or the seat-change billing-policy confirmation until the owner has made
and recorded those decisions. An allowed policy string and Stripe's default
proration behavior are not implementation evidence. The packet also requires
non-sensitive implementation records, the full reviewed source SHA, and the
exact deployed source SHA; the SHAs must match. Omit these arguments while
either policy behavior is not implemented and deployed.

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
  --confirm-npi-free-core-receiver \
  --npi-free-core-receiver-sha '<reviewed-receiver-full-git-sha>' \
  --confirm-counsel-baa-scope-reconciliation \
  --counsel-baa-scope-reconciliation-ref '<counsel-record>' \
  --counsel-baa-scope-reconciliation-sha256 '<lowercase-sha256>' \
  --confirm-founder-owner-approval \
  --owner-approval-ref '<approval-record>' \
  --pending-credential-trial-expiry-policy "$OWNER_APPROVED_POLICY" \
  --policy-implementation-evidence-ref '<implementation-record>' \
  --confirm-seat-change-billing-policy-implemented \
  --seat-change-billing-policy-implementation-evidence-ref '<implementation-record>' \
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
  --confirm-npi-free-core-receiver \
  --npi-free-core-receiver-sha '<reviewed-receiver-full-git-sha>' \
  --confirm-counsel-baa-scope-reconciliation \
  --counsel-baa-scope-reconciliation-ref '<counsel-record>' \
  --counsel-baa-scope-reconciliation-sha256 '<lowercase-sha256>' \
  --confirm-founder-owner-approval \
  --owner-approval-ref '<approval-record>' \
  --pending-credential-trial-expiry-policy "$OWNER_APPROVED_POLICY" \
  --policy-implementation-evidence-ref '<implementation-record>' \
  --confirm-seat-change-billing-policy-implemented \
  --seat-change-billing-policy-implementation-evidence-ref '<implementation-record>' \
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
- `npi_free_core_receiver_evidenced: true`, with the exact reviewed 40-hex
  receiver SHA reported in `npi_free_core_receiver_sha`
- `counsel_baa_scope_reconciliation_evidenced: true`, with a non-null evidence
  reference and SHA-256 tied to the reported immutable v2.2 BAA contract
- `founder_owner_approval_confirmed: true` and a non-null `owner_approval_ref`
- `pending_credential_trial_expiry_policy_resolved: true`, with the recorded
  value matching the implemented owner-approved policy
- `policy_implementation_evidenced: true`, with a non-null implementation
  evidence reference
- `seat_change_billing_policy_implementation_evidenced: true`, with its
  explicit confirmation and non-null implementation evidence reference
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
