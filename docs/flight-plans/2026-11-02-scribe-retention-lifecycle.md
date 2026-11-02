# Dormant Scribe retention lifecycle and verified receipt

Tier 2: PHI/security-boundary lifecycle. Base Core `3e432f4ede3fe3a6d3fd1c8795b3fee2604bf9ff`.
Receiver first: additive immutable event table and callable coordinator. No route,
worker, schedule, real adapter, issuer, config change or publication is installed.
Intake migration `49126bf99c72`, shared Clerk/Core login, practice and other members
remain intact. This follow-up complements intake/fence PRs 948/949 and native 525.

The owner-approved default is 90 days of read-only/export; actual pinned contracts
can specify 30–90. Destruction includes Core and Scribe clinical records. Every
PHI copy in the authorized custody scope must be verified absent before a customer
certificate. Deidentified retention requires actual BAA permission and independent
classification. Audit/BAA/compliance documents have a six-year floor and finance/
contracts seven years, from creation/last effectiveness. Existing append-only and
indefinite rules remain stricter; no floor authorizes deletion of those records.

`advance()` binds both normalized self caller hash and Doctor, validates the pinned
policy/inventory/deadline, then records expiry holds for unresolved evidence. The
source catalog `app/services/scribe_retention_catalog.py` enumerates every identified
Core clinical/history/cache/prep/lineage table, Scribe Alembic table, storage path,
retained-copy surface and protected/retained category. It is an observational source
inventory; the authenticated discovery authority must resolve deployed custody,
unknown stores and shared lineage. Creator links and path prefixes are insufficient.

All surfaces need complete post-expiry inventories and write/restore fences. Each
erased target requires the same caller, explicit custody permission, no other
custodian and no legal hold. One unresolved surface holds every mutation. A closed
canonical manifest validates all fields as hashes, exact booleans, UTC datetimes
or None before serialization AND recovery; unknown or malformed fields hold.
Dedicated Engine-only connections reject caller clinical transactions. A complete
original manifest and stable adapter keys commit before erasure. Retries reload
that intent despite partial mutation and revalidate current custody/holds/fences.
A PostgreSQL session lock serializes execution across commits; SQLite's lock is
synthetic-only. Attempt and verification outcomes commit separately. A trusted
runtime clock bounds fresh absence/preservation proofs after the final mutation.
No client timestamp is accepted. Final preservation includes login/practice/members,
classified documents and permitted deidentified data. Delete success, soft deletion,
key removal and expired access alone cannot confirm erasure. A verified receipt
precedes separate authorized signing/delivery/acknowledgement; drafts are not issuance.

Still required: actual Core/Scribe FK/custody and complete bulk-export adapters;
queued/in-flight uploads/jobs and Redis/Celery/worker temp mounts; all blob versions,
snapshots/soft deletes/replicas/orphans; Core/Scribe PITR/WAL/backups/restore fences;
server/downloaded exports and connected/disconnected device custody; processors and
observability retained copies under executed BAAs; deidentification/classification,
document retention anchors; signer/customer acknowledgement; worker/self-status
integration and execution-lock runtime qualification. Shared/immutable/external
custody without authority remains held. No real adapter above is implemented.

Generated additive revision `708af99c7ac0` follows `49126bf99c72`, rejects UPDATE/
DELETE and refuses destructive downgrade. Recovery leaves expansion/evidence intact
and receiver dormant. DDL remains byte-identical; prior owned synthetic PostgreSQL16
expansion/immutability evidence is reused only for DDL, at
`/private/tmp/core-scribe-retention-pg16-20261102-01/evidence.json`. Its server stopped;
prior concurrency evidence does not qualify the current execution-lock runtime.
No PG rerun. Synthetic tests isolate Core/Clerk/cloud bootstrap. Reviews of `998e4873`
and `80937cf7` remain rejected; R1/R2 are resolved, and this R3 repair needs a fresh
independent review. Source verification does not qualify real erasure or certify
customer data. Environment acceptance and founder deployment approval remain separate.
