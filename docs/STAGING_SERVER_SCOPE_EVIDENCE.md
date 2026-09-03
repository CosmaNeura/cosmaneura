# Staging PostgreSQL server-scope evidence

`scripts/staging_server_scope_evidence.py` is a dormant, read-only prerequisite
for a complete restore rehearsal. It closes the gap left by a single-database
schema inventory: PostgreSQL point-in-time restore operates at server scope.

The collector enumerates every `pg_database` row, including templates and
platform/system databases. Every connectable database must yield a keyed schema
fingerprint plus exact keyed inventories for extensions, foreign-data wrappers,
foreign servers/tables, user mappings, publication membership, subscriptions
and their relation/synchronization state, security labels, event triggers, and
large-object metadata/content pages. A database is
allowed to be unfingerprinted only when `datallowconn` is false and the policy
pins `connections_disabled`. A connection or catalog permission failure on any
enabled database blocks evidence; managed Azure restrictions are not waived.

Server evidence pins roles and attributes, memberships, per-role/database
settings, database owners and ACLs, tablespaces, replication slots, parameter
ACLs, shared security labels, and the complete visible `pg_settings` surface
(including a must-be-present security and recovery subset). New, missing, or
changed records fail closed. Numeric catalog OIDs are excluded because they are
allocation artifacts, except large-object IDs whose referential identity must
survive restore. Role and object names are not normalized: environment-owned
names remain policy-significant unless a future reviewed policy justifies one
narrow normalization.

Raw identities, settings, FDW/user-mapping/subscription options, security labels,
and large-object pages are held only in process memory. Evidence contains counts
and domain-separated HMAC-SHA-256 digests. It also binds the source resource
identity (as an HMAC), source revision, capture interval, policy digest, and
owner-controlled key version.
The key file must be an owner-only regular file and must never be committed.
The connection string is accepted only from
`STAGING_SERVER_SCOPE_DATABASE_URL`; putting credentials in command arguments is
unsupported. Named server cursors enforce per-query and aggregate record/byte
ceilings while iterating, and connection/statement timeouts share a monotonic
15-minute total deadline. A breached limit fails closed without partial evidence.

The committed policy intentionally has `activation_ready: false` and placeholder
pins, so the command refuses before connecting. Activating it requires a
separately reviewed, trusted baseline capture with exact pins and confirmation
that the Azure identity can read every required surface. This change adds no
workflow, credential, database write, restore, deployment, or promotion path.
The PostgreSQL connection cannot independently attest its Azure resource ID;
binding the keyed receipt to authenticated control-plane evidence remains a
separate activation prerequisite.
