# Scribe encounter lookup final provider validation

Tier 2: current provider access on the existing patient-record path. No schema,
configuration, permissions, cohort or public contract changes.

Installed desktop61 needs repeated Core encounter lookups during recording
preparation and native source preparation after deliberate Start. Each lookup
currently takes the full provider locks twice inside one synchronous transaction.
Use the existing fresh `_locked_provider` validator for the final lookup check
only after `_current_provider` has locked its complete authority/link/member
inputs. Retake the existing Practice→Doctor locks and run fresh view-as,
membership, identity, entitlement and display eligibility checks. Any transaction
or savepoint transition invalidates this path and retains the full routine.
Catalog, create and demographic-update requests retain their original path.

Validate against the previous final routine with actual owned synthetic
PostgreSQL: identical returned scope/status and denials, SQL reduction, lock
order, committed revocations, stale ORM values, same-transaction changes,
transaction/savepoint invalidation, cancellation and original native boundaries.
Use invented identity and Scribe downstream explicitly; local elapsed time is
not installed latency acceptance. Run applicable types, lint, format and
implementer review at the exact commit, record evidence in a PR, and merge
passing in-scope work under the owner's September25 local-merge authority.

The approved same-cohort staging release uses the existing source-bound
controller, publisher and image-only adapter after merged-source qualification.
Preserve actual pre/post schema metadata and per-role configuration/secrets.
Rehearse and qualify the packaged image, retain the prior immutable image and
revision for recovery, then compare fresh installed preparation timings.
Production and activation remain outside this release. Keep the twelve-item
goal open until repeatable preparation and source-start waiting are resolved.
