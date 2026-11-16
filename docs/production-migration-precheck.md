# Core production migration precheck

This command covers **Core only**. It cannot observe `cosmaneura-os` #377,
which uses a separate database. It never runs a migration or changes access.

John must run it **inside Azure**, from the reviewed Core checkout corresponding
to the proposed promote source, with Python/uv and its dependencies installed.
Use an approved Azure-hosted Bash shell whose network path is already allowed
by the production database firewall (Azure services or the app's outbound IP).
The Mac can be used to reach that shell; it must not connect directly to the DB.
Do not open the firewall to a home IP or change firewall rules for this check.
Obtain an approved read-only credential through the existing credential process.

The URL must explicitly specify `sslmode=verify-full` and an `sslrootcert`
pointing to a readable, reviewed CA bundle **on the Azure host**. A Linux host
may use `/etc/ssl/certs/ca-certificates.crt` if that is its reviewed bundle.
Only those two URL options are accepted. The tool
requires the canonical production host and `production` database, refuses
ambient libpq `PG*` variables, and verifies the database, schema, transaction
isolation/read-only state, and negotiated TLS. A `prefer` URL gets a fixed,
explicit refusal. The tool never rewrites a URL or changes its TLS mode.
If the existing credential URL uses another mode or a container-only CA path,
obtain an explicitly reviewed Azure-host URL before running this command.

In that Azure shell, run this no-echo Bash recipe from the checkout root. Enter
the URL at the prompt, never as a shell command or command-line argument. The
subshell disables tracing before reading and removes its environment copy on
exit. Do not use a terminal session that records pasted credentials.

```bash
(
  set +x
  IFS= read -r -s -p 'Core production DATABASE_URL: ' DATABASE_URL
  printf '\n' >&2
  export DATABASE_URL
  trap 'unset DATABASE_URL' EXIT
  uv run --frozen --no-env-file python scripts/production_migration_precheck.py
)
```

The command imports no application or migration module. It reads migration
metadata through ASTs and takes one repeatable-read `READ ONLY` snapshot,
then rolls back. It performs no writes, DDL, migration, or promotion. Connection
timeout is 10 seconds, statement timeout is 30 seconds, lock timeout is 5
seconds, and idle-in-transaction timeout is 60 seconds.

| Result | Meaning and decision |
| --- | --- |
| `scope` | Always `core`; says nothing about the separate OS database. |
| `migration_head` | The sole production `alembic_version` revision. Missing/multiple heads or non-revision text cause a refusal with no partial JSON. |
| `repository_head` | The sole head derived from this reviewed checkout. Run from the exact reviewed promote source; no fixed current head is assumed. |
| `is_ancestor_of_repository_head` | Includes equality. `false`: **stop**; the database is outside the checkout's migration ancestry. |
| `pending_migrations` | Explicit revision list in dependency order. Nonempty: **stop** and review every pending migration before deciding on promote. Unknown ancestry produces an empty list plus a refusal, not evidence that none are pending. |
| `abort_counts.practice_conflicts` | Practices whose historical links have a distinct tenant UUID count other than one. Nonzero: **stop**. |
| `abort_counts.reused_uuids` | Tenant UUID groups whose distinct practice count is other than one. Nonzero: **stop**. |
| `abort_counts.zero_uuids` | Historical link rows with the zero UUID. Nonzero: **stop**. |
| `e78845cecca6_would_abort` | Whether the migration's three-count guard would abort against this snapshot. `true`: **stop**. This is a hypothetical guard evaluation if that revision is already applied; Alembic does not rerun it. |
| `provider_practice_authority_null_practice_id_count` | Existing authority rows with NULL `practice_id`, permitted by `ddd6261fa36f` for account ownership. An inventory count, not an abort condition. It cannot predict future request-driven writes. |
| `refusal_reasons` | Fixed codes only. `migration_head_unknown`, `pending_migrations`, `guard_tables_missing`, or `e78845_abort` mean **stop**. |

If either guard table is missing, the tool still prints the verified head and
pending list, with `abort_counts: {}`, a `null` abort result and NULL-practice
count, and `guard_tables_missing`. Empty counts mean unavailable, never zero.
Before `ddd6261fa36f`, account-owned NULL-practice writes require that schema
expansion; this command does not enable writes or change user access.

Exit **0** means the database is at the checkout head, guard tables were read,
and all three abort counts are zero: **go only
to the remaining human release checks**. It does not prove every pending
migration will succeed or authorize promote. Exit **1** emits the report with
refusal reasons and means **stop**. Exit **2** means **stop**: the snapshot could
not be safely obtained. Stderr uses only `configuration_refused` or
`snapshot_refused` and fixed text; no credentials or exception details print.
Do not interpret a refusal as zero
counts. A concurrent change after the snapshot can change the result; rerun
immediately before the human decision.

Merge does not promote or publish production. John still makes the deploy
decision. Existing users keep their current access and onboarding state.
