# Public capability preflight and legal evidence

Risk: Tier 2 (authorization facts and legal acceptance).

## Scope and sequence

PR #452 adds the capability policy and a default-off registration latch.
PR #453 adds a read-only authenticated preflight. PR #454 binds legal acceptance
to the exact current Terms and Privacy bytes. Merge in that order. No schema,
data, deployment, enrollment, payment, or cohort changes are included.

The preflight must use live verified identity and the existing membership
attestation/organization roster rules. An authenticated request alone is not
proof of a verified primary email or authorized practice membership. Failed
facts deny capabilities while remaining visible in the preflight response.
Legal acceptance requires matching kind, version and persisted content SHA-256;
submission requires exactly one current entry per document.

## Validation

Run the full application suite and type/lint checks on the corrected stack,
plus regressions for missing identity, missing attestation, revoked/expired
roster authority, stale legal digests, duplicate entries and tenant isolation.
Verify the disabled route is absent from routing and OpenAPI. Verify runtime
legal bytes match the public documents. Record final commit IDs and local
results in each PR. Infrastructure/frontend evidence may be reused only when
the corresponding source and dependency inputs are identical.

## Recovery and release boundary

Revert #454, #453 and #452 in reverse dependency order to recover the prior
source. Preserve existing append-only legal records; no migration is needed.
The default-off latch limits route exposure but is not a substitute for
correct authorization. Enabling or deploying this feature remains a separate
owner decision; these merges do not activate it or authorize PHI use.
