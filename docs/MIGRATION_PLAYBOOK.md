# MIGRATION_PLAYBOOK.md — the migration ladder

For ANY change to schemas, auth, billing, or patient-data structures.
Never migrate by cutover. Each rung is its own PR/deploy; you may hold at any
rung indefinitely. Deleting is the last rung, never the first.

A "rung" = one independently deployable, observable, backward-compatible
transition. It may require coordinated PRs across repos (versioned API
contracts; receiver/auth deploys before sender activation) — "one rung per
PR" means per transition, not literally one file.

Auth context: identity is per-service (Clerk in core; Entra tokens in Scribe
and CAB; scheduling owns its own users tables; sovereign is provider-neutral
OIDC). Any migration that moves users between providers, changes
session/token handling, or reshapes a users table follows this ladder in
full — `auth_version` tracks provider + credential format, and both paths
keep working until the contract rung.

## The ladder

### Rung 1 — Expand
Add new structures alongside old: new tables, nullable columns, new token
format, new IdP config. Old code ignores them entirely. Zero user-visible
change.
- PR label: `expand`
- Rollback: drop the new structures (nothing reads them yet)

### Rung 2 — Dual-write
New writes go to BOTH old and new structures. All reads remain on the old
path. Track migration state per record (e.g. `auth_version` on users).
- Rollback: stop writing to new structures

### Rung 3 — Backfill + just-in-time migrate
- Batch-backfill rows that can migrate without the user present, in small
  batches, resumable, idempotent.
- For credentials/anything needing the user: migrate lazily on next successful
  login — verify via OLD path, mint new credentials, mark migrated. Unmigrated
  users keep authenticating via the old path. **Nobody gets locked out.**
- Rollback: pause the backfill; JIT-migrated users still work via dual-write

### Rung 4 — Shadow-verify
Run the new path in parallel on real traffic; log mismatches; ENFORCE NOTHING.
Hold until mismatch rate is 0 for N days (default N=5).
- This rung is mandatory for auth changes. It converts "grandfathering bugs"
  into log lines instead of locked-out doctors.

### Rung 5 — Cut reads
Flip reads to the new path behind a flag, cohort by cohort (see
DEPLOYMENT.md). Old path stays deployed and dormant as the kill switch.
- Rollback: flag off

### Rung 6 — Contract
After ≥ 2 weeks stable with no kill-switch use: stop dual-writes (own PR),
then remove old code + schema (own PR, label: `contract`).
- Irreversible by design. That's why it waits.

## Before climbing: rehearse

Restore a **de-identified** snapshot of production into the rehearsal DB and
run all pending migrations against it. Required for every Tier 2 PR.
Production-shaped data (legacy rows, nulls, pre-column accounts) breaks
migrations in rehearsal, not in production.

On Azure Database for PostgreSQL Flexible Server, the rehearsal DB comes from
point-in-time restore (creates a NEW server — production is never touched):

```
az postgres flexible-server restore \
  --resource-group <rg> --name <app>-rehearsal \
  --source-server <prod-server> --restore-time <UTC timestamp>
```

Then: run the de-identification scrubber against `<app>-rehearsal`, run all
pending migrations (`alembic upgrade head`; CAB via its runner), run the test
suite pointed at it, and delete the server when done (it bills while it
exists). Each run doubles as restore-drill evidence — log it.

Rehearsal CI job name: `<TODO: job name once wired>`

## Hard rules for agents

- A migration PR does exactly one rung. Never combine rungs.
- Backfills: batched (≤ `<TODO>` rows/batch), resumable, idempotent, logged.
- No `DROP`, `RENAME`, or type change outside a `contract` PR.
- Every rung's PR states its rollback move before merge.
- Real PHI never appears in fixtures, rehearsal exports, logs, or PR text.
