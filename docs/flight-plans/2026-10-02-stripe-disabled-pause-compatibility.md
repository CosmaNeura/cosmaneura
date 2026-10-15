# Disabled Stripe portal pause compatibility

Risk: Tier 2 (billing provider contract). Scope: local source and synthetic
tests only. The user authorized this repair on 2026-10-02. No deployment,
Stripe mutation, activation, schema change, or access-cohort expansion is
authorized by this plan.

The staging GET-only audit found that the pinned portal returns
`features.subscription_pause = {enabled: false}` under the application's pinned
`2025-09-30.clover` API version. The current allowlist rejects the field even
though it is disabled. Other reviewed portal safety checks passed.

Accept the optional known field only when it is an object whose `enabled` value
is exactly boolean false. Missing fields remain compatible with older portal
responses. Enabled, absent-enable, null, numeric, string, and malformed values
remain refused before creating a portal session. Continue rejecting all other
unknown features, including disabled ones. Retain account/mode/price, pinned
configuration, customer-update, cancellation, and quantity-update controls.

First reproduce the disabled-field failure using the fake Stripe provider.
Then run the complete billing service, route, and transport suites and the
organization-access/onboarding regression suites against isolated synthetic
services, with all external credentials disabled. Run relevant lint/format
and type checks. Record actual results in the readiness ledger; do not equate
local repair with deployed portal or webhook-delivery acceptance.

Before any code-changing PR, isolate this outcome from unrelated working-tree
changes and obtain independent review of its final substantive commit. Release
requires the normal reviewed immutable-candidate process and reconciled live
state. Recovery is a reviewed code-only revert, restoring refusal of this
portal response; no data rollback or migration rung is needed.

Local verification: the disabled-pause acceptance regression failed before the
repair with `StripePortalConfigurationInvalid` for an unreviewed feature.
After repair, all 299 billing service/route/transport tests and 42 organization
service/route tests passed. The full frontend suite passed 546 tests across 76
files; TypeScript, changed-file Python lint/format/Pyright, and changed-test
Prettier/ESLint checks passed. The onboarding review added test-only coverage
for unreserved domains, organization reservations, and incomplete negotiated
shells; no authentication or roster runtime behavior was changed. These are
working-tree results, not a final immutable-candidate or independent-review
receipt. Deployed portal/session and webhook delivery remain unqualified.
