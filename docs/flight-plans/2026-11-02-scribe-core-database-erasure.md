# Dormant Core primary-database erasure receiver

Tier 2. This follow-up stacks on `e0f0c8bdbe8e7b70a656454566f3af89f77a3420`.
It implements real SQL inventory/erasure for separately authorized mutable
clinical graphs. No route, job, scheduler, custody producer or role is installed.
The full Core/Scribe PHI destruction and customer certificate policy remains the
end state; mutable rows are one intermediate milestone.

## Source discovery and retained copies

Catalog v2 adds eight missing physical PHI/link tables: question-set versions,
questions, billing prepared clinical payloads, webhook deliveries/operations,
note-template preferences, private-note receipts, and EHR patient designations.
Billing prepared text/JSON is PHI even though its name contains billing. It
cannot be classified as a seven-year non-PHI finance record. LLM prompt/response
cache and deidentified transcripts have no reliable account FK; they remain held
until actual custody/BAA and retained-copy enumeration is proven.

`AIVisitNote` includes current/history and two transcript FKs. Its unconditional
DELETE guard, immutable prep/capture/conversation copies and composite/self-FK
question histories cannot be bypassed. The separate history receiver must use a
privileged exact-row, actual-authority path while preserving ordinary guards.
`ProviderRecordAuthority` is issuance scope, not destruction authority.

## Exact source and transaction contract

The receiver requires a dedicated PostgreSQL Engine with query parameter
redaction and SQL logging disabled, actual Core SQLAlchemy table metadata, and
an independently trusted custody/drain authority. It is disabled by default.
Doctor/creator/FK links discover candidate IDs only. Every target also requires
actual caller-bound custody approval with `other_custodian=False` and
`legal_hold=False`; unresolved/shared/foreign records are holds.

Read-only inventory projects IDs/FKs/timestamps and PostgreSQL xmin, never
clinical payloads, Clerk subjects, email or API keys. Actual source TypeID column
converters are retained: Core stores raw UUIDs and restores prefixes in Python.
Opaque row references are SHA256 of compact JSON
`["core-primary-row-v1", table_name, canonical_physical_uuid]`. Independently
trusted original approvals pin schema, exact row metadata and preservation
snapshots. Retries reload the original approved set, including already absent
rows; they cannot approve a replacement after mutation.

Per-table coordinator adapters share one configured atomic graph. Erasure reads
the committed immutable intent from its own connection, checks its event digest,
all configured surface scans and stable adapter keys, and rechecks the current
self-owned cancellation and real 30–90-day contract expiry. No external DELETE
runs before that committed intent is visible. All configured target tables must
be represented in the intent, including empty ones.

Deterministically ordered bounded table locks cover observed graph/control
metadata during erasure. The receiver checks schema/FK drift, rewrite/RLS scope,
actual current authority and write/restore/drain fences, DELETE triggers,
composite/self/cyclic relationships and every incoming FK or known unconstrainted
webhook transcript link. Unknown or unapproved dependents hold; no cascade or
practice purge is used. Authorized children are deleted before parents. A fresh
absence/preservation/fence check occurs before commit; failure rolls back the
whole graph. All raw database exceptions are replaced by bounded non-PHI codes.
The conservative first receiver locks observed tables broadly and refuses
unbounded metadata; latency/concurrency needs owned environment qualification
before enabling it. These locks are not a substitute for actual drain authority.

Original Doctor/Clerk, authority/practice/membership rows and non-PHI audit/BAA/
compliance/financial documents are preserved. Six-year audit/BAA/compliance and
seven-year finance/contracts are minimum floors; stricter/indefinite rules
remain. Document dates and payloads are not modified or deleted. Independent
classification and blob-document proof adapters are still required.

## Validation and remaining integration

Owned synthetic PostgreSQL16 tests use actual source Doctor, Patient,
Appointment, LabPanel/LabResult schemas and representative actual append-only
history guard SQL. They prove exact FK ordering/idempotence, current 90-day
expiry, committed intent/key binding, missing/shared/held custody, row-set/xmin
and preservation drift, rollback after mutation, concurrent-insert locking,
metadata-only projections, other-clinician/shared-login/6–7-year document
preservation, default dormancy, and incomplete stores preventing completion.
They do not qualify every actual Core table, PostgreSQL role, cloud copy or BAA.
No previous migration upgrade is rerun; the test-only schema is a new dataset.

Actual verified custody producer, other immutable/unattributed PHI tables,
full bulk read-only export, blobs/versions/backups/PITR/WAL/replicas/devices,
subprocessors/observability, dedicated worker/status, history session privileges,
real document classifications and signing/delivery remain blocked requirements.
Final source review, actual CI and separately authorized environment acceptance
are required before activation. Actual schema/access application and customer
PHI erasure require the future owner decision. Leaving this code disabled is
source recovery; it does not undo any future authorized erasure.
