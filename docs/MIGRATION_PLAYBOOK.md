# MIGRATION_PLAYBOOK.md — schema and data ladder (v0.2.1)

This playbook applies only when a change alters schema or stored data. Auth,
billing, or PHI code without schema/data work may still be Tier 2, but it does
not invent a migration rung or rehearsal.

## Core mechanism and current gap

- Core owns PostgreSQL schema through Alembic revisions in
  `migrations/versions/`.
- Generate a revision with `just db_generate_migration "<name>"`. Never invent
  a revision ID. Apply it to local dev/test databases with `just db_migrate`.
- Pull-request CI applies and lints migrations against disposable CI databases.
  This validates code; it is not a production-shaped PHI-safe rehearsal.
- No generic, automated isolated restore/scrub/verify/TTL-cleanup procedure is
  established by the tracked workflow. Until a change links a reviewed,
  resource-specific runbook and successful evidence, a schema/data rung cannot
  claim rehearsal complete and must not be promoted to a PHI-bearing
  environment.

A rung is one independently deployable, observable, backward-compatible
transition. Each schema/data PR declares exactly one rung and may be held
indefinitely. Cross-repo rungs use versioned contracts and deploy receivers
before activating senders.

## The ladder

### 1. Expand

Add a nullable column, new table/index, new format, or other structure beside
the old one. Existing code ignores it; no user-visible behavior changes.

**Recovery:** stop rollout and leave the additive structure dormant. Do not
drop it as rollback. Removal is a later, separately approved destructive
cleanup after evidence proves nothing reads or writes it.

### 2. Dual-write

Write old and new representations while reads remain on the old path. Track
per-record migration state where needed. Recovery stops the new write while
preserving compatible data.

### 3. Backfill / just-in-time migration

Backfill in bounded, resumable, idempotent batches with progress and mismatch
metrics. Where user presence or credentials are required, migrate only after a
successful old-path verification. Recovery pauses the worker; it does not
blindly reverse verified records.

The PR records the chosen batch bound and load-test evidence. There is no
universal batch-size default.

### 4. Shadow-verify

Run the new read/decision path without enforcing it. Compare results against a
predeclared zero-or-accepted mismatch threshold for a predeclared period. This
rung is mandatory for identity/auth data transitions.

### 5. Cut reads

Move reads to the new representation in explicit cohorts while retaining the
old compatible path. Use a feature flag only when the cut is reversible
user-visible behavior. Recovery restores old reads.

### 6. Contract

After the flight plan's stability window and proof that recovery was not
needed, stop compatibility writes in one PR. Removal of old code or schema is
a separate destructive PR with explicit founder approval. Contract cleanup is
irreversible and never bundled into an earlier rung.

## PHI-safe rehearsal

Before a schema/data rung is promoted to a PHI-bearing environment, its flight
plan must link a reviewed runbook with exact identities, resources, commands,
scrubber, verifier, and cleanup. Generic placeholders are not authorization.

The procedure must fail closed:

1. Create a restore target inside an already-enforced isolated network, with
   public access denied at creation, unique least privilege, no application
   identities, and automatic TTL cleanup.
2. Verify isolation independently before restored data is accessible. Abort
   and clean up if the check fails.
3. Restore through reviewed PITR automation. Treat the copy as production PHI
   until scrub verification succeeds.
4. Run the approved in-place scrubber. It emits counts and hashes, never raw
   values or exports.
5. Verify every identified PHI-bearing field automatically. Only a signed pass
   receipt may open access to migration/test identities.
6. Run the exact pending rung and representative tests; record timing, counts,
   invariants, and recovery results; then revoke access.
7. Execute pre-authorized TTL cleanup and verify deletion. If cleanup fails,
   alert the founder and keep the target isolated as production data.

Evidence records resource identifiers, recovery point, isolation result,
scrubber/verifier versions and receipt, migration SHA, results, RTO comparison,
cleanup result, and founder acceptance. It never includes PHI, connection
strings, secrets, or raw query output.

## Hard rules

- One rung per schema/data PR; no cutover migration.
- Backfills are bounded, resumable, idempotent, and observable.
- Every rung defines invariants, health signals, and recovery before merge.
- No `DROP`, rename, narrowing/type conversion, or destructive rewrite before
  separately approved contract cleanup.
- An expand rollback leaves structures dormant.
- Failed or unverifiable rehearsal blocks PHI-bearing promotion.
