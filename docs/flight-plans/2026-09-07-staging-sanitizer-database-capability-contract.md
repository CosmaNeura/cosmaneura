# Staging sanitizer database-capability contract

Tier 2, based on exact protected `master`
`20bfe16dc6604530ffc973547e39c3362863ec20`. This dormant slice defines and
offline-validates the least-privilege PostgreSQL capability boundary required
by a future sanitizer transaction adapter. It does not select, provision,
authenticate, bind, or use a live database role.

The contract permits a future dedicated provision-job login only: no role
memberships or `SET ROLE`; no ownership or DDL; `CONNECT` without database
`CREATE` or `TEMP`; schema `USAGE` without `CREATE`; direct `SELECT` on the
closed application catalog and `alembic_version`; direct `TRUNCATE` on the
application catalog; and direct `INSERT` only on rewrite tables. Effective
privileges, including `PUBLIC`, must eventually be authenticated rather than
inferred from grant text. The exact role alias and authenticated managed-
identity identifiers remain placeholders.

The validator is pure and offline. It pins the current controller,
database-observation, runtime, sanitizer, transaction-plan, schema, inventory,
and data-contract inputs; derives the exact table partitions; rejects identity
substitutions and capability broadening; and proves every live, runtime,
connection, mutation, workflow, and activation claim remains false. Activation
terminates at `database_role_state_authenticator_unimplemented`.

Later slices require separate review and founder decisions for the direct-
login/direct-grant model, exact PostgreSQL role alias, and temporary bootstrap
authority with teardown proof. A future adapter must also use a SELECT-
compatible control-table lock/revalidation strategy rather than widening
privileges.

This slice performs no database, network, provider, environment, workflow,
deployment, schema, role, grant, or data mutation. Recovery is a code-only
revert; there is no external state to restore.
