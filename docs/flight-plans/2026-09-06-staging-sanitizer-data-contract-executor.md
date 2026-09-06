# Staging sanitizer in-memory data-contract executor

Tier 2 dormant slice based on executor-preflight commit `8ec2e6cd29c9c4e3ab37691593e80cce4621bf78`; it implements the complete pinned row contract without connecting, mutating, publishing, or joining a workflow.

## Boundary and dispatch

Reviewed private construction accepts a whole-second UTC clock, an in-memory transform key/version, and exact bytes for
the three pinned documents; there is no public constructor or key loader. Each transform accepts an exact sealed
`AuthenticatedSanitizerEvidence` and closed-world materialized row tuples. It validates retained seals/proofs, freshness,
policy bytes, types/nullability, identities, fixed-catalog counts, and source/output FKs entirely in memory.

All 343 retained columns are covered:

- 19 rewrite tables/204 columns dispatch preservation, nulling, domain-separated transforms, and four derived recipes;
- 10 guarded tables/139 columns validate as input and become empty candidates;
- all 46 FKs are checked on source; surviving output FKs are rechecked with SQL `MATCH SIMPLE` null behavior; and
- appointment TypeIDs derive from transformed UUIDs, while join/access values follow their pinned HMAC/scrypt recipes.

Configuration and nested JSON are frozen. The key never enters rows, results, representations, errors, or logs. A private HMAC binds the contract, evidence, key version, time, source count, and full candidate; mixed keys fail closed.

## Non-authority and prohibitions

`SanitizerCandidateDataset` is sealed, immutable, nonserializable, and in-process only. All connection, mutation, execution, provider, runtime, sanitizer, workflow, evidence-publication, and post-projection authority is false.
Fixed-catalog counts are authenticated and verified payloads are preserved, but the output projection is not authenticated.

This adds no database/PostgreSQL/SQL, connector, adapter, transaction, commit/rollback, filesystem/environment/key reader,
credential, Azure/Key Vault/network, subprocess, workflow, activation, evidence-publication, or live surface. Public
preflight still terminates with `activation.data_contract_executor_unimplemented` before I/O.

## Remaining gates and verification

Activation still requires a reviewed read/write adapter, fixed-catalog source/output HMAC authentication, guarded-table permission/rollback proofs, atomic serializable execution, exclusion/timeouts, cleanup, and post-commit verification.

Tests cover dispatch/FKs/recipes; golden/metamorphic transforms; refusal, secrecy, no-I/O isolation, and public refusal. Run upstream suites, Ruff, Pyright, compileall, hash, shard, and budget checks.
Recovery is a code-only revert; this dormant leaf has no deployment or data-recovery action.
