# Staging sanitizer database-capability contract

Tier 2 on protected `master` `20bfe16dc6604530ffc973547e39c3362863ec20`; this dormant, offline-only slice defines no live role, connection, provider call, or mutation.

A future provision-job login has no membership, `SET ROLE`, ownership, DDL, or control-table write. Every database—including templates and `datallowconn=false` entries—and every direct, inherited, owner, `PUBLIC`, and effective source is authenticated. Only `staging` may have `CONNECT`; none may grant `CREATE` or `TEMP`.

The 154 public-schema types partition into 14 ACL-managed enums, 63 intrinsic row types, and 77 arrays. Enum arrays follow element usage; row arrays follow intrinsic row usability. Zero direct/effective/`PUBLIC` `USAGE` applies only to enums.

All applicable `pg_db_role_setting` rows—including database-wide `setrole=0`—plus server, startup, connection, and adapter settings are authenticated. Required values include `session_replication_role=origin`, `row_security=on`, `search_path=pg_catalog`, writable `SERIALIZABLE`, and `synchronous_commit=on`; exact timeouts remain blocking.

An externally fenced, server-ID-keyed lease freezes all authority writers: role/mapping/owner/ACL/default-ACL and settings changes, server configuration, and all database/schema/shared-object/migration/ad-hoc DDL. Every writer acquires before DB/provider work, retains through durable commit/rollback, validates before its own commit, and aborts on doubt. The sanitizer separately acquires before final authentication, reauthenticates all facts, retains through DB close, validates before commit, and fails closed. This mechanism remains unselected.

A fixed target-database advisory lock coordinates migrations and the sanitizer; it cannot fence server authority or `CREATE DATABASE`, and `ACCESS SHARE` is not exclusion.

The descriptor binds only user triggers: none on INSERT for 19 rewrite tables or TRUNCATE for 62 tables. A future constraint/trigger observer must derive internal triggers from constraints; match cardinality, `tgtype`, `tgenabled`, `pg_get_triggerdef`, linkage, definitions, and function properties; treat OIDs only as linkage; and make no built-in body claim. A separate `pg_rewrite`/`relhasrules` observer proves zero non-view rules on INSERT targets and table-owner execution semantics. Neither source is the descriptor; revoking routine `EXECUTE` is not trigger suppression.

The pinned validator is pure and offline; all live/stability/write-path claims remain false. Activation stops at `database_role_state_authenticator_unimplemented`; recovery is a code revert.
