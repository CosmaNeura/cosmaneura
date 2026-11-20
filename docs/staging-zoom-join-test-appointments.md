# Staging Zoom join-test appointments

Operator bridge for a Zoom join rehearsal against the staging lab A doctor.
It inserts two obviously fake patients and four transcript-free appointments
with fixed synthetic labels. It never touches production and never prints
patient or doctor names—only TypeIDs and `created` / `exists`.

## Guards

- Runs only when `PYTHON_ENV=staging`.
- Looks up `Doctor.get(email='scribe-lab-a+clerk_test@cosmaneura.com')` and
  refuses if that row is missing, soft-deleted, or has `last_active_at is None`.
- Patient names, dates of birth, appointment titles, notes, provenance, and
  external-id prefix are fixed in code.
- Requires `--confirm-staging-only` and `--confirm-no-real-patient-data`.

## Payload

| Field | Value |
| --- | --- |
| Patients | `Synthetic Patient Alpha - NOT A REAL PERSON` (DOB `1990-01-15`), `Synthetic Patient Bravo - NOT A REAL PERSON` (DOB `1991-02-20`) |
| Appointments | Four rows, two per patient |
| `external_id` | `zoom-synthetic-join-<run-date>:{1..4}` (default run-date `2026-11-17`) |
| `appointment_name` | `SYNTHETIC ZOOM JOIN TEST {n} - NO REAL PATIENT DATA` |
| `appointment_notes_preview` | `Synthetic Zoom join-test visit only. Contains no real patient data.` |
| `created_by_system` | `operator-zoom-synthetic-join-20261117` |
| Default start | Next whole hour UTC + 1h, then hourly, stored as naive UTC |

## How John triggers it

Inside a trusted staging operator shell that already has the app environment
(same class of shell used for `ensure-staging-synthetic-preview-appointment`):

```bash
python -m app.cli ensure-staging-zoom-join-test-appointments \
  --confirm-staging-only \
  --confirm-no-real-patient-data
```

Optional targeting of a future Zoom slot:

```bash
python -m app.cli ensure-staging-zoom-join-test-appointments \
  --run-date 2026-11-17 \
  --start-time 2026-11-18T16:00:00+00:00 \
  --confirm-staging-only \
  --confirm-no-real-patient-data
```

### What it prints

Exactly four lines, TypeIDs and state only:

```text
created appointment_id=appointment_... patient_id=patient_... external_id=zoom-synthetic-join-2026-11-17:1
...
```

Reruns print `exists` for the same IDs. No names, emails, or chart text.

## Why there is no workflow_dispatch runner yet

`az containerapp exec` into `cosmaneura-staging-app` currently fails with
Handshake status 404, so a human cannot run the CLI inside the live replica.

Repo search of `infra-staging/deploy.sh`, `.github/workflows/`, and the
preview-appointment docs shows the only existing in-environment runner is the
ephemeral Container Apps migration job created during staging deploy. That job
copies secret *values* from 1Password / `ENV_JSON` (or
`az containerapp secret list --show-values`) onto the job before start. Azure
Container Apps jobs cannot inherit another app's secrets by name alone; Key
Vault references would need secrets already vault-backed plus an identity with
Key Vault access (new role/config). Under the constraint that no secret value
may pass through the GitHub runner or logs, and that no new secrets or role
assignments may be added, a `workflow_dispatch` job was **not** added.

Until a durable staging operator job (or Key Vault-backed secret refs) exists
under a separate reviewed change, run the CLI from a trusted staging shell that
already holds the app environment without exporting secret values.
