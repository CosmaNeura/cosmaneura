# Staging sanitizer database-capability contract

Tier 2, based on protected `master` `20bfe16dc6604530ffc973547e39c3362863ec20`.
This dormant, offline-only slice defines no live role, connection, provider call, or mutation.

A future dedicated provision-job login has no memberships, `SET ROLE`, ownership, or DDL.
It receives schema `USAGE`, exact table `SELECT`/`TRUNCATE`/`INSERT`, and no
control-table write. Every `pg_database` row—including templates and
`datallowconn=false` entries—and every direct, membership, ownership, `PUBLIC`, and
effective ACL source must be authenticated. Only `staging` may have effective `CONNECT`;
no database may grant effective `CREATE` or `TEMP`, and no non-target session may remain.

The 154 public-schema types are exactly 14 ACL-manageable enums, 63 relation row types,
and 77 arrays. Zero direct/effective/`PUBLIC` `USAGE` applies only to the enums.
Enum arrays mirror element usage; relation-row arrays share intrinsic row-type usability.
Arrays are not independently ACL-manageable, and row-type ACL mutation is not a boundary.

All applicable `pg_db_role_setting` rows—including database-wide `setrole=0`—plus
server/startup, connection, and adapter settings are in scope. The future execution
session must prove `session_replication_role=origin`, `row_security=on`,
`search_path=pg_catalog`, writable `SERIALIZABLE`, and `synchronous_commit=on`.
Exact bounded timeouts remain unselected and blocking.

Authorization TOCTOU protection requires an externally fenced, server-ID-keyed lease or
freeze covering all database/schema/shared-object DDL (including migrations and ad-hoc
DDL), role/database setting resets, server/startup configuration, mapping, ownership,
ACL, and default-ACL mutators. Mutators acquire it first; the sanitizer
acquires it before final identity/role/ACL/settings/catalog authentication, reauthenticates
all capability facts while held, retains it through commit or rollback and connection
close, validates the fence immediately before commit, and rolls back on loss or doubt.
This server-wide mechanism is unselected, unauthenticated, and an activation blocker.

A separate fixed session advisory lock coordinates every target-database migration path
with the sanitizer. It is acquired before the `SERIALIZABLE` transaction, held through
commit or rollback, and followed by unlock/session close. It cannot fence server-wide
authority or `CREATE DATABASE`; `ACCESS SHARE` is not writer exclusion.

The schema-descriptor revalidation binds the exact firing-trigger set under both windows.
The 19 rewrite tables permit no user `INSERT` triggers and the 62 truncate tables no
user `TRUNCATE` triggers. Internal triggers and their functions' `SECURITY DEFINER`
flag, owner, `proconfig`, and definition must match. Revoking routine `EXECUTE` is not
treated as trigger suppression.

The pinned validator remains pure and offline; every live, runtime, connection, mutation,
workflow, stability, and trigger-authentication claim stays false. Activation terminates
at `database_role_state_authenticator_unimplemented`. Recovery is a code-only revert.
