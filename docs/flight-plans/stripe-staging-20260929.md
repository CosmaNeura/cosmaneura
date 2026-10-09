# Stripe staging release — 2026-09-29

Risk: **Tier 2** (billing, subscription contracts and deployment controls).
Applies to PRs 468, 484, 517, 524, 525, 534 and 535; seat locks and owner
views have the companion practice-seat flight plan. Production is excluded.

## Outcome and prerequisites

Deploy the reviewed transport retries, subscription state coverage, paid-seat
high-water accounting, pinned portal validation and staging test-mode controls.
Base the release on deployed Core 9f09bafa87bfb468a627f01d27ff31791c07c9f5.
Retain newer Scribe receiver/API, Session Prep and CosmaBot releases and each
workload's current configuration. Preserve Labs-off and current access cohorts.
PRs 526 and 527 are excluded pending decomposition under the change budget.

This is a code rollout. Keep STRIPE_INTEGRATION_ENABLED and
STRIPE_BILLING_ENABLED at their reconciled values (both false at preflight),
and STRIPE_LIVE_MODE false. No real Stripe requests, account configuration,
customer changes or charges are part of local validation or this deployment.
Enabling test checkout requires a separate complete test-mode configuration,
webhook/portal verification and a restore policy that permits synthetic Stripe
state; these patches alone do not complete that operational acceptance.

## Local validation and review

Use only isolated PostgreSQL/Redis with invented credentials and empty Clerk,
Stripe and other external keys. Run Stripe transport, billing, inbox, route,
seat-lock and race tests; frontend billing/roster/settings tests and types;
relevant Core capture, Session Prep and companion provisioning regressions;
changed-file lint/types and infrastructure checks. Record exact tested commits,
commands, results and remaining limitations in PR evidence. Review the final
substantive changes locally. Preserve reviewed parent commits when integrating
stacked PRs and require no unresolved review conversations.

The transport review found malformed error JSON masking HTTPStatusError and
negative/nonfinite Retry-After values aborting retries. Six regressions failed
before repair. The fix retains the original HTTP failure and uses bounded
backoff for invalid delay values; unkeyed requests remain non-replayable.

## Expand and recovery

PR524 adds nullable practice paid-seat high-water and period-start columns at
Alembic ed76434b6308, after 71d6e1ac9b04, with pair/range constraints. Rehearse
upgrade from the current schema with synthetic rows, confirm single migration
head and preservation of existing data, then verify old code can operate on
the expanded schema. Apply no downgrade, reset, destructive backfill or schema
rollback. Prior workloads may continue to read while the columns are dormant.

Before live apply, record immutable source/digest/revision, migration heads and
configuration fingerprints for API, worker and scheduler, plus unchanged
Scribe and CosmaBot boundaries. Build the exact approved merged source once,
verify its packaged files and synthetic runtime, publish an immutable digest
and lock its source tag. Recheck source, predecessor and configuration at apply.
Drift requires reconciliation. Update Core API, worker and scheduler to the same
verified image while preserving distinct workload commands and settings.

On rollout failure, restore each workload's own recorded approved immutable
image and verify readiness. Leave additive columns in place. Preserve all
recordings, notes, queues, keys and unrelated work. Keep evidence of the old
revisions and artifact availability; do not claim a live rollback drill unless
one was actually required and performed.

## Postflight

Confirm current/ready revision agreement, public Core health, worker/scheduler
health, schema head and constraints, billing fail-closed status, and the seven
established workload/four access-boundary checks. Verify Scribe and CosmaBot
identities/configuration did not change. Record deployed source/image/revisions,
validation and recovery evidence in the successor checkpoint. External checkout,
webhook delivery, real proration and portal acceptance remain operational checks
until the separate test-mode activation is authorized and configured.
