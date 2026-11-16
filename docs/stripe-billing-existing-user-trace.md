# Stripe billing and existing-user access

Existing users walled by billing-on: found (see list).

This is a code trace against `master` `4fce2310` merged into PR #965, not a
production account inventory. Both flags becoming true, with all other state
unchanged, does **not** change access in the synthetic exempt, completed legacy
pilot, paid, live legacy-trial, or never-paid fixtures. Enabling integration does
allow lifecycle events and reconciliation to change stored billing state; those
paths have conditional walls. Do not activate billing based on a green
configuration preflight or the passing flag-only tests.

## Wall found

1. **Signed-event pending verification, including exempt practices.**
   `app/configuration/stripe.py:stripe_runtime_configured` depends on
   `STRIPE_INTEGRATION_ENABLED`; `stripe_webhook_configured` adds signature
   readiness. `app/routes/webhooks.py:stripe_webhook` then accepts a verified
   event into `app/services/stripe_billing.py:enqueue_stripe_event`. A fresh
   `invoice.payment_failed`, mismatched invoice customer, or non-entitling,
   paused, invalid, or identity-mismatched subscription event can write
   `subscription_status="pending_verification"` on the matching bound practice.
   `app/models/practice.py:entitled` denies that state **before** checking the
   active billing exemption. An ordinary existing paid or exempt account then
   fails `app/services/doctor_access.py:_decide`, and
   `app/routes/dependencies/practice_gate.py:require_practice_in_force` returns
   402 for ordinary clinical routes. No subscription match means no practice
   projection changes. The fixture covers both bound paid and bound exempt
   practices, including the integration-off 503 before the same event is
   accepted with both flags on.
2. **Invalid bound-subscription reconciliation, including exempt practices.**
   `_apply_authoritative_subscription` and
   `_fail_closed_invalid_bound_subscription` can persist `invalid` for a fresh
   bound commercial-contract, trial-clock, or seat-ledger violation.
   `Practice.entitled()` denies `invalid` before an active exemption too.
   `refresh_subscription_state`, `sync_subscription_quantity` and the periodic
   quantity sweep make these paths reachable when integration is configured.
   A healthy stored `active` projection can therefore lose access after the
   newly enabled read discovers drift. The compiled-runtime fixture proves the
   invalid-bound-contract write outranks an otherwise active exemption.
3. **Authoritative non-entitling subscription state.** After fresh validation,
   `_apply_authoritative_subscription` mirrors subscription status and trial
   end. An ordinary account can lose entitlement on a non-entitling status or
   expired authoritative trial. `past_due`, `canceled`, `unpaid`, `paused`, and
   `incomplete` are already denied without the flags; activation can newly
   populate that state from Stripe. An active exemption outranks these ordinary
   known statuses, but does not outrank the pending/invalid states above.

Exact active legacy-pilot grants on completed-onboarding Doctors remain an
independent access basis in `_has_valid_legacy_pilot_access` / `_decide` even
when the practice projection becomes pending/invalid. Identity/grant matching,
explicit revocation, disabled accounts, membership tombstones and cancellation
rules remain required; no new grant is inferred from an old trial or an account
creation date. The tests mock an already validated exact grant, not the grant
validation algorithm. No live account's eligibility was inspected.

Stop at evidence: no billing behavior, prices, flags, environment, or access
policy is changed here. Three strict backend xfails record the desired
preservation assertion for the paid/exempt pending-event and exempt invalid
contract counterexamples. A Vitest expected-failure case records the matching
active-comp paused-access UI. Passing characterization tests record the actual
denial; xfails are not acceptance of activation or of a billing wall.

## Complete flag and readiness trace

| Code path | Effect; existing account without a subscription |
| --- | --- |
| `app/configuration/stripe.py`: `stripe_runtime_configured`, `stripe_webhook_configured`, `stripe_portal_configured`, `stripe_billing_configured` | Integration gates authenticated maintenance, webhook and portal readiness. Billing adds the new-sale latch. Valid configuration opens these operations; it grants/revokes no account by itself. |
| `app/configuration/public_onboarding.py:public_onboarding_configured` → `app/server.py:register_public_onboarding_routes` → public onboarding routes | Public new-practice acquisition additionally needs `PUBLIC_ONBOARDING_ENABLED`. This registers an acquisition lane ending at Checkout; existing internal account/membership routes are not replaced. No automatic re-onboarding or card requirement is added to an existing grant/exemption. |
| `app/routes/internal.py:_practice_payload`, `lookup_practice` | Return billing/management readiness and resumability, alongside independently calculated `entitled`, exemption and pilot facts. They do not rewrite entitlement or membership. Status, lookup and settings remain onboarding/gate-exempt reads. |
| `app/routes/internal.py:create_billing_checkout` → `create_checkout_session` | Owner-only, entitlement-exempt recovery route. Exact pilot access refuses an unnecessary subscription; managed/exempt practices are excluded by `_public_checkout_is_managed`. A recoverable existing Session uses maintenance readiness before the new-sale latch. New Checkout needs billing readiness. Failure here is a Checkout refusal, not an entitlement mutation solely from setting the flag. |
| `app/routes/internal.py:create_billing_portal` → `create_portal_session` | Owner-only, entitlement-exempt maintenance; integration/portal configuration is required, billing acquisition need not be on. No customer returns 409 for this operation; the existing account keeps its independent access. |
| `app/services/stripe_billing.py:_request` | All outbound Stripe requests require integration readiness. This does not check membership or entitlement. Its downstream state-writing consumers are the conditional walls listed above. No outbound call is made by this audit's tests. |
| `_validate_runtime_account`, `_validate_runtime_price`, `_validate_runtime_configuration`, `_retrieve_subscription`, `_retrieve_subscription_identity` | Account/price/identity validation for Checkout, portal and lifecycle maintenance. An unbound practice has no subscription to refresh; `refresh_subscription_state` returns without a subscription fetch. Configuration failure refuses the operation. Fresh bound-state validation can lead to the walls above. |
| `_recover_checkout_session`, `_retire_open_checkout_session`, `_matching_frozen_checkout_sessions`, `reconcile_frozen_checkout_request` | Existing-attempt recovery, retirement and operator reconciliation. The other direct `STRIPE_BILLING_ENABLED` check refuses an operator's applied “confirm absent” recovery while new acquisition is on. It does not wall an ordinary existing user or erase their grant/exemption. |
| `_validated_portal_configuration`, `create_portal_session` | Validate a reviewed portal and create its session. An unbound existing account does not gain a customer merely from flag activation. Portal availability is separate from clinical access. |
| `_sync_subscription_quantity_leased`, `sync_subscription_quantity`, `reconcile_pending_quantity_syncs`, `app/jobs/stripe_billing.py:perform` | Maintain every bound subscription, including the sweep's unflagged rows. Integration can unlock remote reads/repairs and authoritative status updates, including invalid-state revocation. An account/practice with no bound subscription is not a sweep target. |
| `app/routes/webhooks.py:stripe_webhook`, `enqueue_stripe_event`, `apply_stripe_event`, `process_stripe_event_inbox`, `reconcile_pending_stripe_events`, `app/jobs/stripe_events.py:process` / `perform` | Verify, persist and reconcile lifecycle events. Integration gates the receiver/remote maintenance, not just new Checkout. The matching, pending/invalid and non-entitling paths are the walls above. Events do not confer a subscription on arbitrary unbound existing accounts. |

The trace test scans all `app/**/*.py` call sites for the readiness helpers,
`_request`, `_validate_runtime_configuration` and both flag names. A new site
must be named in this document; the semantic conclusions still need review.

## Access policy and frontend consequences

`app/models/practice.py:billing_exemption_active` is time-based, and `entitled`
contains no billing flag. A managed legacy trial with no subscription remains
entitled until its existing deadline. A never-paid practice with no active
trial, exemption or exact grant is denied **before and after** the flag change;
it is not proof that an existing live account lost access at activation. A
public-self-service practice cannot obtain paid-pilot authority from an old BAA
trial alone. Existing non-entitling, malformed, or expired state is not silently
reclassified as entitled by this work.

`app/services/practice_access.py:set_billing_exemption` is an explicit audited
operator change; it rejects unresolved Checkout or nonterminal subscriptions
before setting a comp. `record_negotiated_baa` changes an agreement explicitly.
Neither is called by flipping a billing flag, and neither consults those flags.
No comp, agreement, membership or grant is rewritten by this PR.

`app/routes/billing_header.py` is clinical billing-preparation functionality
under the protected internal router, with its own billing-source policy. It
does not consult either Stripe flag. Its normal practice gate can deny access
after the lifecycle projection walls above. The Stripe Checkout/portal routes
are in `app/routes/internal.py`, not in a separate `billing.py` router.

`web/app/components/shared/PracticeJoinPanel.tsx:billingActionKind` prioritizes
exact pilot access, existing subscription maintenance/resume and active
exemptions. Billing readiness can offer “Add payment now” during an entitled
legacy trial; this button is optional and the existing access remains. A
never-paid/lapsed account has the existing paused-access UI with either flag
state. The panel's alert depends on `!entitled && !pilot_access_grant_active`,
so a pending/invalid exempt projection can display the billing wall despite
the active-comp fact. Pilots retain the no-billing/no-personal-attestation copy.

`web/app/lib/billing-recovery.ts:handleInternalApiResponse` redirects an ordinary
402 to settings; `restrictSettingsToBillingRecovery` and settings/layout callers
limit recovery when the API already reports unavailable entitlement. The
backend settings payload excludes pilot-grant access from billing recovery.
These paths respond to the denial, not to a new flag-only subscription check.

The GET-only `scripts/stripe_configuration_preflight.py` remains independent of
app startup and database state. It reads the current source price/version
constants using ASTs, emits fixed aggregate configuration evidence and makes
no subscription/account-access guarantee. All new fixtures are synthetic;
provider transport, signatures, grant lookup, ORM sessions and logs are mocked.
No key, real provider identifier, payment link, or patient data is added.

Merge does not promote or publish production. This PR remains draft. The
human deploy decision is separate, and the recorded walls must be resolved
outside this audit before any activation decision that could affect users.
