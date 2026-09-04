# Staging PostgreSQL server-scope evidence

`scripts/staging_server_scope_evidence.py` is a dormant, read-only prerequisite for a complete restore rehearsal. It closes the gap left by a single-database schema inventory: PostgreSQL point-in-time restore operates at server scope. The collector enumerates every `pg_database` row, including templates and platform/system databases. Policy names exactly one non-template, connectable primary database whose keyed server-scope receipt is explicitly bound to the separate full row inventory. Every other connectable database must have no user relations (tables, partitioned tables, views, materialized views, sequences, or foreign tables) and no large objects; this stronger invariant proves that no secondary database can carry unsanitized user rows. A disabled database is allowed only when it is a template. Any unknown database, disabled non-template database, non-primary user relation or large object, connection failure, or catalog permission failure blocks evidence.

Every connectable database yields a keyed schema fingerprint plus exact keyed inventories for user relations; normalized shared dependencies; extensions; foreign-data wrappers, servers, tables, options and ACLs; column ACLs/options; language ACLs; user mappings; table and PostgreSQL 16 schema publication membership; subscription worker/relation state; security labels; event triggers; and large-object metadata, ACLs, and content pages. Schema fingerprints include normalized `pg_rewrite` rule definitions and state, resolved effective relation tablespaces, extension owners, custom collation owners and locale properties, and view `security_invoker`/`security_barrier` options.

Server evidence pins `pg_authid` roles/attributes and keyed password-verifier state (presence and scheme remain inside the role aggregate), memberships, per-role/database settings, database locale/owners/ACLs/default tablespaces, tablespace locations/options/ACLs, subscription definitions, prepared transactions, replication slots (including WAL status and restart/confirmed LSNs), replication origins/status, parameter ACLs, shared descriptions and security labels, and all visible `pg_settings`. Subscription `subdbid` is resolved to database name before hashing; `subskiplsn = 0/0` is canonical unset. Relation `srsublsn = 0/0` is likewise unset, while a nonzero value is captured exactly as the copy/synchronization progress boundary. New, missing, unreadable, or changed records fail closed. Numeric catalog OIDs are excluded as allocator artifacts, except large-object IDs whose referential identity must survive restore. Per-database `pg_shdepend` edges are retained as resolved object-address tuples, so owners, ACL role references, tablespaces and other shared dependencies are covered without hashing allocator OIDs. `pg_prepared_xacts`, tablespace locations, `pg_shdescription`, and replication origins are also pinned. Role and object names are policy-significant.

Raw identities, settings, password verifiers, FDW/user-mapping/subscription options, LSNs, security labels, shared descriptions, rewrite definitions, shared-dependency object names, tablespace locations, and large-object pages remain only in process memory. Evidence contains counts and domain-separated HMAC-SHA-256 digests and binds the HMAC of source resource identity, the primary database identity, source revision, capture interval, policy digest, and owner-controlled key version. The key file must be an owner-only regular file and must never be committed.

`scripts/validate_staging_evidence_manifest.py` now accepts this collector's
real receipt shape as `server-scope.json`. The manifest binds its exact file
bytes and separately binds the SHA-256 of canonical receipt JSON; the receipt
does not acquire an invented embedded receipt digest. Validation requires the
exact server/database surface sets, canonical identity-HMAC database ordering,
one primary database, zero user-relation and large-object counts for every
database marked `empty`, at most 64 databases, at most 100,000 records per
surface, and at most 500,000 records across the receipt. The receipt policy
digest and source revision must match the manifest, and its capture interval
must fall inside the manifest observation's 15-minute freshness bracket.

The connection string is accepted only from `STAGING_SERVER_SCOPE_DATABASE_URL`; credentials in command arguments are unsupported. Production policy and capture require one lowercase Azure PostgreSQL FQDN ending in `.postgres.database.azure.com`. Production capture accepts only explicit `host`, `port`, `dbname`, `user`, `password`, `sslmode=verify-full`, and `sslrootcert=system` libpq parameters; the sanitized connection also disables client certificates and GSS encryption so ambient identity material and GSS negotiation cannot alter authentication or bypass TLS hostname verification. Alternate routing, service files, session options, client TLS overrides, multi-host targets, ambient libpq `PG*` connection variables, and ambient OpenSSL trust-path/configuration overrides fail closed. The adapter discards the original DSN after parsing and connects with a sanitized parameter map. `allow_local=True` remains a test-only code path for an exact loopback policy target and is never exposed by the CLI.

Named server cursors enforce per-query and aggregate record/byte ceilings while iterating. Before cursor creation, a server-side `octet_length(row_to_json(q)::text)` aggregate measures the rendered, uncompressed form. Its byte totals are conservatively multiplied by six to bound Python's `ensure_ascii=True` expansion; consequently the effective preflight ceiling can be one sixth of the nominal canonical byte limit. The streaming loop independently checks each exact canonical record before appending or incrementing counters. Queries contain no unbounded `array_agg`, and a connection cancellation timer enforces the monotonic 15-minute total deadline. A breached limit fails closed without partial evidence.

Every connectable database is captured twice in deterministic order and both keyed surface sets must match; server surfaces must also match before and after both passes. Activation must run during a coordinated freeze of DDL, role/ACL, publication/subscription, prepared-transaction, and replication-state changes so the two-pass bracket is a stable cross-database capture.

The committed policy intentionally has `activation_ready: false` and exact
configuration placeholders, so the collector command refuses before
connecting. The local manifest validator can validate the receipt contract in
that dormant state, but its output remains `activation_state: blocked` with
`server_scope_policy_unconfigured`, attestation, provider-access, workflow, and
restore-activation blockers. When a reviewed policy becomes active, the
validator additionally requires exact revision, key-version, server-surface,
database-status, and database-surface agreement with that policy.

`scripts/authenticate_staging_server_scope_receipt.py` is the next dormant,
offline boundary. Its supported `authenticate_evidence_bundle` entry point
loads all three exact hash-pinned checked-in policies, refuses while either
authentication policy is disabled, validates the closed-world bundle itself,
and passes the resulting opaque one-read snapshot only to private,
dependency-injected helpers used by unit tests. Callers cannot replace the
canonical policy bytes through the supported interface, and the snapshot cannot
be constructed through its public class interface. The helper reparses the immutable result and
`server-scope.json` bytes, while the command reads the collector HMAC key
from an absolute owner-owned mode-`0600` regular file, and calls the collector's
shared verifier for the original domain and length framing. The verifier
constant-time checks the source-resource, primary-database, and whole-receipt
HMACs and requires every server/database pin to match an active, configured
server-scope policy. Both policy mappings are parsed from the exact raw bytes
named by their SHA-256 digests, and the authentication policy pins the same
server-policy byte digest used by the verifier; byte subclasses are rejected so
hashing and parsing cannot observe different content. The server policy's source
resource and primary database must also equal the authentication policy's source.
The authentication receipt binds the manifest, checksum
index, evidence set, exact server-scope bytes and canonical receipt, source
revision/resource/database, key version, observation/expiry window, and trusted
authentication time. That time is sampled after HMAC verification and is used
for both the final freshness decision and `authenticated_at`; the HMAC-bound
capture interval must end no later than the manifest observation and begin no
more than 15 minutes before it. Key bytes include
every byte of the key file, including a
trailing newline, and are never stripped, emitted, or logged.

The checked-in `server-scope-authentication-policy.json` and server-scope policy
are both disabled, so the authenticator refuses before reading a key or bundle.
Even a successful future authentication result remains blocked
(`activation_state: blocked`): provider observation, GitHub attestation
verification, protected workflow storage/integration, and restore activation
are separate milestones.

Activation still requires a separately reviewed, trusted baseline capture, an
exact primary-database binding to the full inventory, proof that every
non-primary connectable database is relation-free, and confirmation that the
Azure identity can read every required surface. This binding adds no workflow,
credential, database write, restore, deployment, safe-submit, or promotion
path. The PostgreSQL connection cannot independently attest its Azure resource
ID, and the local validator deliberately has no HMAC key with which to verify
`source_resource_hmac` or `evidence_hmac`; authenticated provider binding and
cryptographic attestation remain separate activation prerequisites.
