# Staging synthetic Preview appointment

This runbook creates one unmistakably synthetic platform patient and appointment
for an existing, Clerk-bound staging provider. It is an operator-only bridge to
the already governed athena Preview designation and record-mapping commands.

The command is intentionally narrow:

- it runs only when `PYTHON_ENV=staging`;
- it requires both the exact platform `doctor_id` and the exact email stored on
  that Doctor row, evidence that the Clerk-bound Doctor has signed in to
  staging, plus two explicit confirmations;
- the patient name, date of birth, appointment date, warning labels, and
  provenance version are fixed in code and cannot be supplied by an operator;
- it accepts no patient lookup, vendor record identifier, practice, or EHR
  environment;
- it writes the patient, appointment, and durable operator provenance in one
  transaction and returns the same IDs on retry;
- it has no HTTP route or web client. Run it only inside the trusted staging
  operator shell.

It does **not** designate the patient as synthetic EHR data and does **not** map
an EHR record. Those remain separate confirmations so that creating a platform
demo appointment cannot silently grant EHR access.

The provisioning row is a durable first-creation receipt, not the platform's
future append-only audit system. Retries preserve the original `provisioned_by`
reference rather than pretending to be new creation events.

`last_active_at` proves that the Clerk-bound identity reached staging; this
command does not verify the provider role claim itself. Verify John's provider
claim separately during the Clerk/OIDC live-acceptance step.

## 1. Create or find the fixed staging fixture

```bash
python -m app.cli ensure-staging-synthetic-preview-appointment \
  --doctor-id '<exact-doctor-TypeID>' \
  --doctor-email '<exact-provider-email>' \
  --operator-ref '<opaque-operator-audit-ref>' \
  --confirm-staging-only \
  --confirm-no-real-patient-data
```

The result prints only opaque platform `patient_id` and `appointment_id` values.
It never prints or accepts chart data. Record both IDs in the protected change
record; the patient ID is the input to the next two existing commands.

The two legacy appointment timestamp columns do not carry time-zone metadata;
their fixed fixture values are deliberately stored as naive UTC to prevent a
database session time zone from shifting them.

## 2. Separately designate the platform patient

```bash
python -m app.cli designate-athena-preview-patient \
  --doctor-id '<same-exact-doctor-TypeID>' \
  --patient-id '<patient-TypeID-from-step-1>' \
  --operator-ref '<opaque-operator-audit-ref>' \
  --confirm-no-real-patient-data
```

## 3. Separately bind the approved synthetic Preview record

```bash
python -m app.cli provision-athena-preview-record \
  --doctor-id '<same-exact-doctor-TypeID>' \
  --patient-id '<patient-TypeID-from-step-1>' \
  --record-ref '<approved-synthetic-Preview-record-ref>' \
  --operator-ref '<opaque-operator-audit-ref>' \
  --confirm-synthetic-only
```

Never enter real patient data or use a production chart reference. The approved
sandbox mapping remains limited by the existing database constraints to athena
Preview practice 195900. The synthetic appointment may then be opened by that
provider in staging to exercise the provider-mount labs flow.
