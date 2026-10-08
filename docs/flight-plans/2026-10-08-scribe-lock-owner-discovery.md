# Scribe capture lock-owner discovery

Tier 2: provider recording authorization path; no permission, identity,
contract, schema, configuration or cohort changes. Owner-directed startup
latency work and existing local-check/merge authority apply.

## Problem and change

Every capture authorization recursively derives the complete provider-record
context merely to discover which Practice and Doctor to lock. The caller then
performs that complete decision after the locks and again after refreshing and
locking authority inputs, followed by final provider access and record checks.
Preparation metadata invokes this discovery eleven times.

The discovery helper instead checks the stored bearer-owned provider mount
shape and reads its exact source identity, Doctor and membership. No request
appointment, binding, workspace or claimed provider chooses these locks. The
existing Practice→Doctor order, post-wait identity/mount/membership checks,
both full interaction decisions and final provider access check remain.
Invalid authority may take temporary owner locks before its full denial; it
cannot reach downstream I/O, consent processing or publication.

## Validation and release

Compare actual native receipts and every FOR UPDATE statement with the prior
pre-lock context derivation. Admit the synthetic fixture before counting.
Commit actor, tenant and person revocation from another transaction during
discovery and require denial before downstream I/O. Retain all existing
offboarding, practice-move, identity, token-expiry, view-as, concurrent capture,
remote revocation, cancellation and publication regressions.

Run applicable local checks and implementer review, record the exact commit in
the PR and merge only passing work. Release only the immutable merged image
through existing approved staging controls; no migrations. Preserve divergent
per-role configuration/secrets, observed schema and recovery revisions. Measure
installed consent-to-ready without pressing Start; virtual audio remains the
authorized recording test method. Recover by the retained previous role images.
