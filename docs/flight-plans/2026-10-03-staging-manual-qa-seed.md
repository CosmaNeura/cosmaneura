# Staging manual QA seed — Tier 2

Outcome: reusable marked Clerk test identities and synthetic clinical input
fixtures for two existing customer roles, isolated in a separate PR.

No schema migration, feature flag, new role, dependency, production operation,
Stripe mutation, consent fabrication, entitlement exemption, or Azure mutation.
The script defaults to offline planning. Apply requires independently pinned
staging database and Clerk test credentials, explicit synthetic-only assertion,
disabled automatic migration, and test-mode Stripe configuration.

Human setup uses existing practice creation, agreements, Checkout, invitation,
and onboarding flows. Fixture writes require both normal access decisions and
the exact named synthetic practice. Seed-owned identities are never adopted
from an unmarked account. Existing passwords and records are preserved.

Validation: offline target guards, identity collision/idempotency tests, and
fixture transaction/isolation tests; scoped formatting and type checks. Record
actual execution results in the PR. Offline evidence does not qualify live
Clerk, PostgreSQL, Scribe, Stripe, or deployed feature flags.

Recovery: stop invoking the additive script; disable synthetic identities via
normal administration if required. No rollback deletes legal or clinical
history. Any cleanup requires separate exact-target operator authorization.

Release: independent agent review after final substantive push, human review,
merge, then founder-controlled staging promotion if needed. Run only the exact
reviewed merged candidate against independently verified synthetic staging.
See [operator runbook](../testing/STAGING_MANUAL_QA.md).
