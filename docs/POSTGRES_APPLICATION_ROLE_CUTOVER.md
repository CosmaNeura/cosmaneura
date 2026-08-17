# PostgreSQL application-role cutover

Status: guarded operator foundation only. Nothing in this document authorizes a
production database, Azure, 1Password, Terraform, or GitHub mutation.

The goal is to replace the server-administrator `DATABASE_URL` used by the
platform with a least-privilege login and production-database-scoped ownership
while preserving a tested rollback path.
The old Container Apps secret is deliberately retained. Rotating the server
administrator password is a later change, after the rollback window closes.

## Safety invariants

- `infra/database_role.py` and `infra/database_url_cutover.py` are dry-run or
  read-only by default. Mutations require `--apply` or `--rollback`.
- Database URLs and passwords are accepted only through a named environment
  variable or stdin. They are never accepted as command-line arguments.
- The application login is `LOGIN`, `INHERIT`, `NOSUPERUSER`, `NOCREATEDB`,
  `NOCREATEROLE`, `NOREPLICATION`, and `NOBYPASSRLS`.
- `cosmaneura_app_owner` is `NOLOGIN`. It owns only the selected application
  schema, application relations, supported functions/procedures, and standalone
  enum/domain types. Extension objects are excluded; unsupported aggregate
  routines fail closed.
- The `production` database remains owned by the existing administrator. The
  role transaction verifies that ownership did not change.
- PostgreSQL 16 records the grantor for role memberships. The database
  administrator therefore remains an `ADMIN OPTION` member of the no-login
  owner; `cosmaneura_app` is the non-admin member used by the application.
- `PUBLIC` loses `CREATE` on the application schema. Owner default privileges
  do not grant `PUBLIC` access to future tables, sequences, functions, or types.
- The new Container Apps secret is `database-url-v2`; `database-url` is never
  deleted or overwritten by the cutover operator.
- The workload order is fixed: job monitor, worker, scheduler, API, migration
  job configuration. Rollback runs the reverse order.
- Every production process imports application models and invokes Alembic at
  startup. The cutover must use an unchanged image with the database already at
  `head`; each new revision must be healthy before the next workload moves.

## Hard live blockers

Do not run `--apply` in production until all of these are resolved in a
separately reviewed change window:

1. Hold application deployments, migration merges, and Terraform applies.
2. Confirm a restorable database backup and name the rollback operator.
3. Resolve Terraform ownership after the cutover. `infra/main.tf` currently
   declares the unversioned `database-url` secret and environment reference. A
   later Terraform apply can revert an out-of-band `database-url-v2` reference.
   The script requires `--acknowledge-no-infra-apply-window` for this reason; it
   does not pretend the drift is harmless.
4. Refactor or hold `infra/deploy.sh` during the window. Its existing migration
   job path passes `key=value` secrets to Azure CLI arguments and continues to
   refresh the old `database-url` secret. The new operators do neither.
5. Confirm the direct ARM `listSecrets`/PATCH behavior in a non-production
   Container App using the same Azure API version before using it in production.
6. Audit `CONNECT` and `TEMP` on every other database in the PostgreSQL server.
   PostgreSQL logins are cluster-global, and default `PUBLIC` grants on databases
   such as `postgres` can permit connection even though this role has no objects
   or ownership there. This foundation scopes ownership and default `SET ROLE`
   to `production`; it does not claim server-wide connection isolation or mutate
   unrelated database ACLs.
7. Review PostgreSQL and audit-extension logging policy. The operator derives a
   SCRAM verifier locally, sends it through `COPY` rather than a SQL bind, and
   never submits the plaintext password as SQL. The database must nevertheless
   be configured so password-management utility statements and SCRAM verifiers
   are not captured in audit exports.

The durable follow-up should choose one owner for runtime secret configuration
(for example, reviewed Key Vault references or fully externalized Container App
secret management) so a Terraform refresh cannot undo the selection. No new
database secret value is added to Terraform configuration or state by this
foundation.

## 1. Prepare the PostgreSQL roles

Use the current administrator URL through `DATABASE_URL`. The default command
connects read-only, inventories `public`, verifies `production` and
`alembic_version`, and prints counts only:

```bash
export DATABASE_URL="$(op read \
  'op://Production/postgres-prod-database/python-connection-string')"
python infra/database_role.py
```

Generate and store a unique 32–256 character application password using visible
non-whitespace ASCII and at least three character classes in a distinct
1Password item. Do not paste it into a command. Apply it over stdin:

```bash
op read 'op://Production/postgres-app-v2/password' |
  python infra/database_role.py --password-stdin --apply
```

The operation is one PostgreSQL transaction with five-second lock and
60-second statement timeouts. A role conflict, unexpected database, missing
`alembic_version`, system schema, or ownership drift aborts the transaction.

Verify from the administrative connection:

```bash
python infra/database_role.py --verify
```

Create a separate 1Password field containing the complete TLS URL for
`cosmaneura_app`; keep the administrator URL unchanged for rollback. The URL
must target the same Azure PostgreSQL host, port 5432, database `production`,
and include `sslmode=require`.

## 2. Read-only Azure plan

The plan checks all five workload configurations and confirms that the old
secret still exists. It never reads the new URL:

```bash
export AZURE_SUBSCRIPTION_ID='bab2a709-1fcb-4c87-a9fc-802f7477ec13'
python infra/database_url_cutover.py
```

Every state must be `old`. `unexpected` or `missing-rollback-secret` is a hard
stop; do not try to repair an unknown state with `--apply`.

## 3. Versioned cutover

Only after the blockers and change-window controls above are satisfied:

```bash
op read 'op://Production/postgres-app-v2/python-connection-string' |
  python infra/database_url_cutover.py \
    --database-url-stdin \
    --apply \
    --confirm-subscription "$AZURE_SUBSCRIPTION_ID" \
    --confirm-resource-group cosmaneura-prod \
    --acknowledge-startup-alembic \
    --acknowledge-no-infra-apply-window
```

Before Azure is mutated, the script connects with the new URL and verifies:

- `session_user=cosmaneura_app` and `current_user=cosmaneura_app_owner`;
- both roles' least-privilege attributes and exact membership options;
- the database remains admin-owned;
- `public` is owner-owned; and
- `public.alembic_version` is readable.

The URL is added as `database-url-v2` to every resource through an authenticated
ARM request. Existing secret entries, including `database-url`, are preserved.
Only the non-secret reference name is passed to Azure CLI when a new revision is
created. Every Azure CLI show, update, and revision call pins the twice-confirmed
subscription rather than relying on mutable CLI context.

For job monitor, worker, scheduler, and API, the operator waits for the exact
new revision to be `Provisioned`, `Healthy`, and running. Because each process
runs Alembic before becoming healthy, this is also the per-step database-login
gate. The migration job is last and its configuration is verified without
starting a migration execution.

If any step fails, the current step and all completed steps are pointed back to
`database-url` in reverse order. The versioned secret is retained for diagnosis.

## 4. Explicit rollback

Rollback needs no new database credential because the old secret remains:

```bash
python infra/database_url_cutover.py \
  --rollback \
  --confirm-subscription "$AZURE_SUBSCRIPTION_ID" \
  --confirm-resource-group cosmaneura-prod
```

The order is migration configuration, API, scheduler, worker, job monitor. Each
revision is verified before proceeding. Unknown references or a missing old
secret fail closed. Do not remove `database-url-v2` during incident response.

## 5. Close the rollback window

After an agreed soak period, live application and background-job checks, a
successful migration-job execution, and a separate rollback rehearsal:

1. Land the reviewed durable secret-ownership solution.
2. Rotate the Azure PostgreSQL server-administrator password.
3. Verify the application continues to use `cosmaneura_app`.
4. Remove the old administrator URL only in a separate, audited change.

## Validation

Unit tests (the PostgreSQL integration test skips by default):

```bash
PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  pytest --confcutdir=tests/infrastructure -q \
  tests/infrastructure/test_database_role.py \
  tests/infrastructure/test_database_url_cutover.py
```

Against an explicitly disposable PostgreSQL 16 database:

```bash
COSMANEURA_DB_ROLE_INTEGRATION_ADMIN_URL='postgresql://…' \
PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  pytest --confcutdir=tests/infrastructure -q \
  tests/infrastructure/test_database_role.py
```

The integration test creates unique roles/schema and proves a fresh login,
without a client-issued `SET ROLE`, can create/alter an Alembic-style table,
replace an application SQL function, and perform runtime insert/select
operations. It also proves that database-level `CREATE SCHEMA` is denied.
