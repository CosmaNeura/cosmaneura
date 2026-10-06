# Core type cleanup qualification

## Scope and permission

The owner requested the remaining cleanup in one continuous pass. This work
corrects static types and test fixtures without changing identity configuration,
billing activation, deployment, or data. Authentication, billing and model paths
are Tier 2 under AGENTS.md even when the intended behavior is unchanged.

## Required invariants

- Keep existing rejection codes, status codes, validation limits and authorization
  ordering. Explicit checks must express the same predicates, not grant access.
- Keep SQL selections, joins, locking and transaction boundaries unchanged.
- Keep model table names, columns, PostgreSQL types, timezone handling, defaults,
  nullability, indexes and constraints unchanged. No migration is authorized.
- Preserve prompt bytes, SOAP grounding, consent requirements and session scope.
- Do not add Any casts, diagnostic exclusions, new suppressions, or relaxed gates.
- Use synthetic fixtures and isolated local databases only. Never run the global
  test hook that performs live Clerk cleanup.

## Qualification

Record baseline PostgreSQL metadata and compare it after model edits. Exercise
billing preparation and bridge authorization in their network-bounded local
suite. Run the real RSA authentication qualification and malformed provider
session cases. Verify compiled SQL when adding SQLModel column typing. Run
focused workflow tests, the isolated authority suite, whole Ruff, formatting and
Pyright. Retain failures as evidence rather than replacing them with a passing
retry. Investigate the availability-test timing race separately.

Each final substantive push receives independent review. Split the prepared
changes into PRs within the 2,000-line and 50-file limits; one working pass does
not waive those limits. Local test results are not hosted CI or staging/device
acceptance. Record actual counts and remaining gaps in each PR.

## Recovery and release

Revert the relevant code commit before promotion. There is no schema/data change
to recover and no migration, seed, backfill or live configuration change to undo.
If metadata or behavior equivalence fails, stop that change and revise it rather
than introducing a migration or widening scope. Actions remains disabled.
Nothing in this qualification authorizes merging or deployment; the approved
operator release path and final integrated-candidate checks still apply.

## Diagnostic image evidence

The Linux probe typing change invalidated the recorded copied-file hash.
Rebuild only the three-file diagnostic image from exact source
`30aa89db5855d04d5a78a48c7e97f8c62fe12bac`, retaining the existing pinned base.
The Dockerfile has no RUN instructions; neither the image nor the probe runs,
and nothing is published. Canonicalize only the OCI archive envelope to satisfy
the existing strict receiver; preserve every manifest, config and layer byte.

Record the new archive, manifest, config, layer and source-file hashes in
`infra/runtime_capability/image-candidate.json`. Verify decompressed layer hashes,
file contents, ownership, permissions and the unchanged Python executable.
Independent review must inspect the retained complete archive, not just metadata.
This is source/artifact qualification, never runtime or deployment acceptance.
The September 14 image pins remain historical, not pins for this rebuilt image.
Recovery is restoring the earlier source and its matching metadata together;
never substitute either image for an approved execution artifact.

## Test database reset guards

Add the two billing evidence mutation guards to the existing test-only reset
helper. Its cleaner deletes rows; the separate action TRUNCATE guard stays
enabled. Restore every named guard in the existing finally block, including
when cleanup raises. Production triggers, migrations and billing behavior are
unchanged. Qualify successful cleanup, failed cleanup and non-test rejection
without importing the global identity-cleanup hooks, then rerun the database-only
provider authority and invitation races on the isolated migrated database.
Recovery is reverting this test-only commit.
