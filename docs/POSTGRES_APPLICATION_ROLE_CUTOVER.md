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
- OpenTofu can declare both secret names while the source-controlled selector
  stays on `database-url`. Its read-only live guard refuses to select v2 until
  all five workloads already use v2, so an apply can adopt but cannot perform
  the staged cutover.
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
3. Land and apply the reviewed phase-A configuration: set
   `DATABASE_URL_V2_ENABLED=true` in `.env.production.backend` while leaving
   `DATABASE_URL_SECRET_NAME=database-url`. Review that the plan only stages the
   unused v2 secret and retains the old secret. This repository's current secret
   model stores both encrypted values in the protected OpenTofu state; avoiding
   that requires the separate Key Vault/managed-identity design.
4. Hold `infra/deploy.sh` during the staged switch. Its database URLs now travel
   only over stdin to an authenticated ARM request, and its value-free guard
   requires all five live references to match the checked-in selector. Other
   legacy migration-job and registry secrets still use Azure CLI arguments and
   remain separate hardening work.
5. Confirm the direct ARM `listSecrets`/PATCH behavior in a non-production
   Container App using the same Azure API version before using it in production.
6. Run `infra/database_cluster_scope.py` and resolve every reported hard blocker.
   PostgreSQL logins are cluster-global. The tool can transactionally remove
   `PUBLIC` access from the ordinary `postgres` database only after exact plan,
   role-preservation, scope, and impact confirmations. Azure-managed and template
   databases are audit-only: the tool will not mutate them, and any exposure
   there requires Azure-supported isolation or a separately reviewed decision.
7. Review PostgreSQL and audit-extension logging policy. The operator derives a
   SCRAM verifier locally, sends it through `COPY` rather than a SQL bind, and
   never submits the plaintext password as SQL. The database must nevertheless
   be configured so password-management utility statements and SCRAM verifiers
   are not captured in audit exports.

The checked-in selector is the durable owner for the current inline-secret
model. The cutover operator performs the staged switch; the selector is changed
only afterward so the next plan is adoption-only. Both database values enter
the protected OpenTofu state while v2 is enabled. A future Key Vault-reference
design can remove inline secret values from state, but it is not implied here.
OpenTofu owns the four long-running Container Apps. The existing migration job
remains deploy-owned: deploy refuses to create it, reconciles only its database
v2 secret through ARM/stdin, requires the existing old rollback secret without
replacing its value, and adopts the same checked-in selector. Importing that
live job into OpenTofu requires a separate import/in-place plan review; a
replacement plan is a stop condition.

## 0. Read-only scope and migration preflights

Audit every cluster database before creating the application login:

```bash
python infra/database_cluster_scope.py
```

The output contains database/role names, counts, and a plan digest—never a URL
or password. It expands default `PUBLIC` ACLs and checks effective access for
the app/owner roles when they exist. `hard_blockers` must be empty before
cutover. Do not use its explicit `--apply` mode without a separately reviewed
plan; it never automates changes to `azure_*` or template databases.

Check migration `91d2c4e5f6a7` without returning patient or doctor identifiers:

```bash
python infra/migration_91d2c4e5f6a7_preflight.py
```

This read-only transaction reports the Alembic revision, new-table/object
presence, duplicate and null-risk counts, relation-size/row estimates, lock
counts, and long-transaction counts. `readiness.ready` must be true before the
migration job runs.

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

Only after phase A is applied, all blockers are resolved, and the change-window
controls above are satisfied:

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

After all five workloads are verified on v2, change only
`DATABASE_URL_SECRET_NAME` to `database-url-v2`. The live guard makes the plan
fail if any workload is still old or mixed; a valid phase-B plan adopts the
already-live state and retains `database-url` for rollback.

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
Before ending the freeze, revert `DATABASE_URL_SECRET_NAME` to `database-url`
and review an adoption-only plan. A normal deploy is deliberately blocked while
the checked-in selector and live references disagree.

## 5. Close the rollback window

After an agreed soak period, live application and background-job checks, a
successful migration-job execution, and a separate rollback rehearsal:

1. Confirm the checked-in selector is v2 and OpenTofu shows no secret-reference
   drift.
2. Rotate the Azure PostgreSQL server-administrator password.
3. Verify the application continues to use `cosmaneura_app`.
4. Remove the old administrator URL only in a separate, audited change.

## Validation

Unit tests (the PostgreSQL integration test skips by default):

```bash
PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  pytest --confcutdir=tests/infrastructure -q \
  tests/infrastructure/test_database_role.py \
  tests/infrastructure/test_database_url_cutover.py \
  tests/infrastructure/test_database_url_live_guard.py \
  tests/infrastructure/test_database_url_deployment_ownership.py \
  tests/infrastructure/test_database_cluster_scope.py \
  tests/infrastructure/test_migration_91d2c4e5f6a7_preflight.py
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
