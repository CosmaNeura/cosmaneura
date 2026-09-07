# Staging sanitizer database-capability contract

Tier 2, based on exact protected `master`
`20bfe16dc6604530ffc973547e39c3362863ec20`. This dormant slice defines and
offline-validates the least-privilege PostgreSQL capability boundary required
by a future sanitizer transaction adapter. It does not select, provision,
authenticate, bind, or use a live database role.

The contract permits a future dedicated provision-job login only: no role memberships or
`SET ROLE`; no ownership or DDL; schema `USAGE` without `CREATE`; direct `SELECT` on the
closed application catalog and `alembic_version`; direct `TRUNCATE` on it; and direct
`INSERT` only on rewrite tables. The future observer must enumerate every
`pg_database` row, including templates and databases with `datallowconn=false`,
and authenticate direct, membership, ownership, `PUBLIC`, and effective ACL
sources. Only `staging` may have effective `CONNECT`; no database may grant
effective `CREATE` or `TEMP`, no non-target session may remain, and no database
may be created inside the capability window. The exact role alias and managed-
identity identifiers remain placeholders, and cross-database confinement is
explicitly unimplemented.

The 154 public-schema types are an exact PostgreSQL 16 partition: 14 named,
ACL-manageable application enums; 63 relation row types; and 77 generated array
types. Zero direct, effective, and `PUBLIC` `USAGE` applies only to the 14 enum
bases. The 14 enum-derived arrays mirror their element enums' effective usage;
the 63 relation-row-derived arrays share their row types' intrinsic usability.
Neither array group is independently ACL-manageable, and relation-row ACL
mutation is never treated as a boundary. The closed-world boundary also rejects
direct column ACLs; callable public-schema routines; language, tablespace,
parameter, foreign-data, user-mapping, large-object, and
default-privilege grants; all grant options; and every ownership dependency.
PostgreSQL-owned built-in defaults remain outside the user-object allowlist,
but explicit runtime-role grants and nondefault `PUBLIC` ACL deltas on system
objects are forbidden.

Every applicable `pg_db_role_setting` source is in scope, including database-
wide `setrole=0`, role-global, and role-plus-database settings. Server/startup configuration,
connection options, and adapter commands must also be authenticated. The eventual session must
prove `session_replication_role=origin`, `row_security=on`,
`search_path=pg_catalog`, writable `SERIALIZABLE` transaction characteristics,
and `synchronous_commit=on`; exact bounded timeouts remain unselected and are
an activation blocker. The controller's separate read-only observation session
is not execution proof.

Migration exclusion uses one fixed PostgreSQL session-level advisory-lock
protocol and key. Every migration writer and the sanitizer must acquire it on a
dedicated session before the sanitizer starts its `SERIALIZABLE` transaction.
The sanitizer then revalidates revision and schema, retains the same session
lock through commit or rollback, and unlocks and closes the session. This
authenticated target-database DDL window protects the no-future-object and
implicit-default-ACL assumptions. It cannot prevent server-level `CREATE
DATABASE`, so catalog stability requires separate, unselected server-wide
coordination. `SELECT` on `alembic_version` remains read-only: an `ACCESS SHARE` lock
is expressly not treated as writer exclusion. Migration cooperation and the
adapter are both unimplemented, so the contract remains dormant.

The validator is pure and offline. It pins the current controller,
database-observation, runtime, sanitizer, transaction-plan, schema, inventory,
and data-contract inputs; derives the exact table partitions; rejects identity
substitutions and capability broadening; and proves every live, runtime,
connection, mutation, workflow, and activation claim remains false. Activation
terminates at `database_role_state_authenticator_unimplemented`.

Later slices require review and founder decisions for the direct-login model,
exact role alias and timeouts, bootstrap teardown, cluster-wide database ACLs,
effective-setting observation, and migration cooperation. The sanitizer never
gains control-table write authority merely to manufacture a lock.

This slice performs no database, network, provider, environment, workflow,
deployment, schema, role, grant, or data mutation. Recovery is a code-only
revert; there is no external state to restore.
