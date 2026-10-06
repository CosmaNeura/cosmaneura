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
