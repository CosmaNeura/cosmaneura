# Production migration precheck

John can run this on his Mac from the reviewed Core checkout before deciding
whether to promote. Have Python/uv and the repository dependencies installed.
Load the production `DATABASE_URL` into the environment through the approved
credential process; never paste it into a command, terminal output, or PR.
Use a read-only database credential where available. Disable shell tracing.

The URL must explicitly specify `sslmode=verify-full` and an `sslrootcert`
pointing to a readable, reviewed CA bundle. On macOS the system PEM bundle is
`/private/etc/ssl/cert.pem`. Only those two URL options are accepted. The tool
requires the canonical production host and `production` database, refuses
ambient libpq `PG*` variables, and verifies the database, schema, transaction
isolation/read-only state, and negotiated TLS. A `prefer` URL gets a fixed,
explicit refusal. The tool never rewrites a URL or changes its TLS mode.
If the existing credential URL uses another mode or a container-only CA path,
obtain an explicitly reviewed workstation URL before running this command.

Run exactly:

```sh
uv run --frozen --no-env-file python scripts/production_migration_precheck.py
```

The command imports no application or migration module. It reads migration
metadata through ASTs and takes one repeatable-read `READ ONLY` snapshot,
then rolls back. It performs no writes, DDL, migration, or promotion.

| Result | Meaning and decision |
| --- | --- |
| `migration_head` | The sole production `alembic_version` revision. Missing/multiple heads or non-revision text cause a refusal with no partial JSON. |
| `repository_head` | The sole head in this checkout, currently `69bad2e072e2`. Run from the exact reviewed promote source. |
| `is_ancestor_of_repository_head` | Includes equality. `false`: **stop**; the database is outside the checkout's migration ancestry. |
| `abort_counts.practice_conflicts` | Practices whose historical links have a distinct tenant UUID count other than one. Nonzero: **stop**. |
| `abort_counts.reused_uuids` | Tenant UUID groups whose distinct practice count is other than one. Nonzero: **stop**. |
| `abort_counts.zero_uuids` | Historical link rows with the zero UUID. Nonzero: **stop**. |
| `e78845cecca6_would_abort` | Whether the migration's three-count guard would abort against this snapshot. `true`: **stop**. This is a hypothetical guard evaluation if that revision is already applied; Alembic does not rerun it. |
| `provider_practice_authority_null_practice_id_count` | Existing authority rows with NULL `practice_id`, permitted by `ddd6261fa36f` for account ownership. An inventory count, not an abort condition. It cannot predict future request-driven writes. |

The known final chain is `49126bf99c72 → ddd6261fa36f → e78845cecca6 →
69bad2e072e2`. An earlier head means migrations are still pending. Before
`ddd6261fa36f`, new account-owned NULL-practice writes require that schema
expansion; this command does not enable those writes or change user access.
Missing tables, permissions, unexpected schema, timeout, or connection failure
cause a fixed refusal; no identifiers, credentials, or exception details print.

Exit **0** means ancestry matches and all three abort counts are zero: **go only
to the remaining human release checks**. It does not prove every pending
migration will succeed or authorize promote. Exit **1** emits the counts but
means **stop** (ancestry mismatch or an abort count). Exit **2** means **stop**:
the snapshot could not be safely obtained. Do not interpret a refusal as zero
counts. A concurrent change after the snapshot can change the result; rerun
immediately before the human decision.

Merge does not promote or publish production. John still makes the deploy
decision. Existing users keep their current access and onboarding state.
