# Scribe catalog critical section

## Outcome and boundaries

Catalog reads currently hold provider locks while repeating the complete
provider validator and separate visit-input queries. An isolated PostgreSQL
reproduction of the actual service and HTTP handler returned 503/SQLSTATE 55P03
after 2.01 seconds when a second catalog request waited behind the first. Staging
metadata also showed approximately two-second policy/encounter failures during
a long catalog request; those logs do not identify the blocking transaction.

Extend the existing synchronous encounter-lookup row-evidence scope to catalog
reads. The exact original row locks remain held. Use the existing fresh final
provider validator only while the original transaction remains active and
unchanged. Every visit still validates current record lineage, provenance,
identity, capture scope and access. A two-visit synthetic PostgreSQL comparison
preserved complete response fields and reduced SELECT statements from 96 to 52.
Catalog executes no INSERT, UPDATE or DELETE in that comparison.

This does not introduce shared locks, release locks early, cache permission,
change mutation paths, or guarantee that competing requests cannot time out.
Large catalogs and slow database round trips can still exceed the lock wait.
Native capture timing and successful media remain separate live acceptance gates.

## Risk and dependencies

Tier 2: provider/patient authorization read composition. The owner authorized
investigation and a scoped correction while preserving revocation semantics.
One Core PR, independently reviewable after its final substantive push. No new
dependency, API contract, session/identity rule, billing rule or release cohort.

## Current deployed state

The 2026-10-25 19:41 UTC reconciliation retained web source
`9666feadf573c3bf8760c1c537e82d7522ba9bc0`, revision `--0000066`, image digest
`sha256:2da198ba066ae8c31f23dd57a07e96cfa562b9024ede5d7ec6154a6ecc9952b9`.
Worker source is `63094f2dd9df815414934326f6109b79d784f616`, revision `--0000040`,
digest `sha256:703fe135c1fb6c0ad8a11924d8cc74cb32af696dc566bdbc0b1cc39480572317`.
Schema is `9bcaa341475f`; scheduler and configuration/cohorts were preserved.
All seven catalog-related source files used by the reproduction match the
actual web container. Refresh this evidence before any further release.

## Release control and schema

Feature flag: N/A for a behavior-preserving read composition change. Schema,
migration and data rehearsal: N/A; there are no schema/data changes. The new test
scope accepts only the task-owned socket-only synthetic database and a fresh
`catalog_scope_<random>` schema, removed after the isolated test run.

## Qualification and rollout

Require actual PostgreSQL full-response and distinct-lock-statement/order parity,
fewer reads, concurrent writer exclusion, committed revocation denial, current
actor/identity/link/member changes, and commit/rollback/savepoint invalidation.
Run the existing encounter and lookup suites, Ruff and production typechecking.
Independent final-head review must precede merge. Local query reductions do not
establish a live latency target or resolve the observed staging incident.

If selected for staging, build from reviewed merged current trunk; reconcile
the intentional web/worker source difference and preserve other merged features,
configuration, identities, cohorts, schema and scheduler. No production action.
Measure normal catalog and source-admission overlap on the actual rebuilt
client before claiming the integration failure corrected.

## Recovery and completion

Revert only this source change on current trunk, review, and rebuild an immutable
recovery candidate after fresh reconciliation. Do not roll back to an older
whole image that removes unrelated merged behavior. No data repair or irreversible
cleanup is required. Completion evidence must distinguish this finite query and
authorization regression pass from the still-required actual capture journey.
