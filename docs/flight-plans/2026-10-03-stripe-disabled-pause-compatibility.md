# Disabled Stripe portal pause compatibility

Risk: Tier 2 (billing provider contract). One outcome: accept the observed
disabled optional Stripe portal feature without enabling subscription pause.
No schema migration, deployment, Stripe mutation, activation, new entitlement,
or access-cohort expansion is included.

The staging GET-only audit observed `features.subscription_pause =
{enabled: false}` on the pinned portal under API version `2025-09-30.clover`.
The current allowlist refuses the entire configuration even when pause is
disabled. Other reviewed portal safety checks passed in that read-only audit.
This defect does not establish the cause of every deployed billing restriction;
runtime flags and signing-secret delivery still require operator verification.

Accept the optional known field only when it is an object whose `enabled` value
is exactly boolean false. Missing fields remain compatible with older responses.
Enabled, absent-enable, null, numeric, string, and malformed values remain refused
before creating a portal session. All other unknown features remain refused,
including disabled ones. Preserve pinned configuration, account/mode/price,
customer-update, cancellation, and quantity-update controls.

Validation: the acceptance regression previously reproduced failure in the main
audit working tree. Repeat billing service/route/transport tests against the
isolated PR worktree and disposable synthetic PostgreSQL/Redis, with external
credentials disabled. Record the actual isolated results in the PR. Add explicit
enabled/malformed pause coverage and verify no portal POST occurs on refusal.
Run scoped lint/format/type checks and independent review after final push.

Release requires human review, merge and the normal founder-controlled exact
merged staging candidate process, including live drift reconciliation. Agents
must not deploy or mutate Azure. Operator qualification must verify deployed
configuration and a complete Stripe test Checkout → signed webhook → entitlement
→ portal journey. Local tests do not constitute deployed evidence.

Recovery: reviewed code-only revert restores strict refusal of the response.
No Stripe configuration/data rollback, migration rung or feature flag is needed.

Isolated local verification on 2026-10-03: 338 combined billing service, route,
transport and organization-access tests passed against disposable PostgreSQL
16.9 and Redis 6.2.18 in CI mode, with vendor credentials disabled. All 541
frontend tests across 75 files passed; the production-bundled test-target build
passed using non-routable public telemetry placeholders, without Sentry upload.
Scoped Ruff lint/format, Pyright and `git diff --check` passed. The generated
client is based on the tracked OpenAPI document, not a regenerated backend schema.
The build still reports existing duplicate OpenAPI plugin, sourcemap, mixed
import, chunk-size and future-flag warnings; frontend tests retain React act and
hydration warnings. These are not represented as warning-free release evidence.
No live staging mutation or authenticated clinical acceptance was performed.
