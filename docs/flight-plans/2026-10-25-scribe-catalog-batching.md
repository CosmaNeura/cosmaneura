# Scribe catalog batching

Tier 2: this changes a provider-authorized clinical catalog read. No schema,
configuration, entitlement, credential, or capture operation changes.

## Trigger and scope

A catalog keeps the shared Practice-to-Doctor ownership locks while reading each
visit and performing the final provider check. On the baseline, eight visits
execute 100 ORM statements and 100 additional transaction-local timeout-setting
calls. An overlapping catalog/create can exhaust its unchanged two-second lock
budget at its first Practice lock. An isolated PostgreSQL reproduction with
explicit per-execute transport delay reproduces the observed 55P03 stack; it
is not identification of a particular staging blocker or a live latency SLA.

Only `CatalogRequest` gathers exact candidate references in one bounded typed
VALUES/outer-join query. Every candidate still passes the visit, record lineage,
origin cardinality, capture, and patient identity predicates. Missing and foreign
rows fail closed. The UUID columns and opaque TypeID strings remain distinct.
Lookup, create, retained creation keys, and other mutations keep their original
query path.

The original initial provider routine and final current-provider recheck remain,
including the same Practice-to-Doctor locks and authority/link/member locks.
A fresh access/grant/practice check runs for the common locked provider inside
the batch; it is not repeated as SQL for every visit. The final check re-evaluates
current tombstones, entitlement and time-dependent grants before publication.
Patient identity rows are locked in the original candidate order. Canonical
patients retain their key-share read. A private input scope is tied to exact
Session, transaction, row objects, owner, and link. A transaction transition,
pending mutation, scope exit, or lost ownership selects the original row path.
No access decision is retained beyond the request or after a commit/rollback.

## Validation

The focused suite compares original and batched results and refusal types for
missing record/binding/origin, foreign record owner/patient, revoked binding or
person, duplicate origin, invalid capture, and foreign patient identity. It
checks provisional-to-canonical identity, all identity locks against another
PostgreSQL session, final entitlement expiry, retained transaction/revocation
adversaries, and unchanged lookup/create paths.

The concurrent eight-visit test instruments actual psycopg execute calls,
including the raw timeout setter. Its explicit 15 ms delay applies to each
catalog execute. The baseline must produce 55P03; the batch must let concurrent
list/create finish under the original lock budget without duplicate creation.
These are controlled local conditions. Real staging must still re-run the
retained native intent and complete the capture/dashboard acceptance gates.
The timeout setters and all deadline/cancellation fences are unchanged here.

## Recovery and rollout

No migration or data repair is needed. Independently review the final pushed
head and validate the exact source before a separate reviewed staging rollout.
Revert this commit to restore the original catalog query path. Preserve failed
requests and retry the same retained creation key through normal UI; do not
create another visit to work around an uncertain result. A healthy service,
local test result, or successful list does not establish recording, delivery,
saved-note, or playback acceptance.
