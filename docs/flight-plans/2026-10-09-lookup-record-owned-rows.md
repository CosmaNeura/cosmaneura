# Retain provider lock evidence within a synchronous Scribe lookup

Tier 2: existing provider/record authorization. Continuing the owner's all-twelve
Scribe repairs and 5–7 second recording-preparation target. No new permission,
session credential, consent, schema, configuration, billing rule or release cohort.

The encounter lookup's initial provider validator takes real Practice→Doctor
locks, but its evidence expires before the record adapter checks current provider
access in the same transaction. That adapter repeats its original multi-query
fallback despite the caller still owning those locks. Retain the exact factory's
row evidence for this synchronous lookup only. Every provider validator retakes
its original locks; every eligibility stage queries current data. Do not retain
an authorization decision, cross a remote await, change the ActiveModel session,
or skip final validation. Catalog, creation, demographics and other callers retain
their existing paths. Transaction, rollback and savepoint transitions invalidate
the proof; mismatched sessions and expired rows select the original queries.

Qualification: real fictional PostgreSQL before/after receipt and lock-sequence
comparison, access-decision parity, committed writer revocation, transaction and
exception cleanup, nested/mismatched scopes, and applicable native/admission/
window/access regressions. Measure actual DBAPI statements and deadline calls;
local instrumented times do not establish desktop seconds. Implementer review
and exact tested commit evidence are required before merge under owner authority.

Delivery uses the existing source-bound staging build and immutable image path,
preserving role configuration, secrets, schema and recovery revisions. The
unchanged 20 GiB local capacity guard currently prevents that build. Signed
desktop 64 installation and fresh desktop timing also await manual Mac unlock.
Do not lower guards or start physical audio. Recovery is the preceding immutable
Core image/revision or a revert of this patch. The 5–7 second goal remains open
until deployed, installed measurements prove it.
