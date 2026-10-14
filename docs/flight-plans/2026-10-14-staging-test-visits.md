# Empty invented visits for staging recording acceptance

Risk: **Tier 2** because the change writes patient/appointment rows and adds an
authenticated creation path. There is no schema sequence. The change is for
invented staging data only and provides no production promotion decision.

## Outcome

Recording acceptance needs a fresh visit before a Scribe session exists.
Uploading a recording already starts transcription, and the existing operator
fixture returns one fixed appointment dated 2099. Notes gains a staging-only
**Create test visits** action that creates two empty appointments against the
existing governed synthetic patient, with fixed warning text and names:
`Invented CosmaBot acceptance one YYYYMMDD` and `... two YYYYMMDD`.

The visits use naive UTC instants at 12:00 and 12:01, matching the legacy
appointment timestamp convention. The dialog retains an opaque request UUID and
UTC date in session storage before writing. Retrying after response loss or
page reload returns the same pair. A confirmed result exposes exact visit links
and an explicit action to start another pair.

## Boundary and recovery

The private POST `/internal/v1/staging/test-visits` inherits the normal Clerk,
doctor, support-view binding and practice-access dependencies. It rejects
non-staging execution and active read-only support view. The caller supplies
only `request_id` and `visit_date`; doctor identity comes from authenticated
request state. Patient name, DOB, warning text and provenance are fixed. The
date must be within the prior seven UTC days through today for new creation.
An older retained date may recover its existing exact pair, without provisioning
new fixtures or visits.

The existing fixture service first ensures its independently idempotent,
fixed-data patient/2099 appointment. The pair transaction then locks the Doctor
and fixture, verifies the fixture's exact patient identity, and creates both
appointments atomically. The request UUID is scoped to that Doctor in
`created_by_system`. An incomplete or changed pair refuses recovery rather
than repairing or replacing rows. Recovery verifies patient, marker, the two
timestamp slots and fixed warning/provenance fields; normal Scribe title changes
are permitted. Receipt labels stay fixed and recovery never resets generated
titles. Responses contain plain immutable receipts,
so releasing SQL locks cannot expire returned ORM objects.

The action creates no recording admission, audio file, transcript, note, EHR
designation or vendor mapping. Existing visits and recordings remain available.
Appointment connection through the existing reviewed Core setup is a separate
step; creation alone does not grant external recording authority.

If response confirmation fails, keep the exact request and use **Retry same
request**. If released behavior must be withdrawn, restore the previous
verified frontend/backend candidate through the existing founder-controlled
staging path. Leave created synthetic fixtures and test visits intact; inspect
their exact IDs before any later cleanup. No destructive rollback is proposed.

## Validation and release

Local checks execute the real provisioning/validator/route bodies with isolated
SQL model doubles and no Core/vendor startup. A disposable PostgreSQL cluster
verifies real row-lock concurrency; this does not qualify deployed Core schema,
Clerk, practice access or Scribe. Frontend checks verify staging visibility,
retained retry, duplicate-click prevention, receipt validation and request
storage. Regression checks cover generated-title recovery, expired committed
requests, expired unseen requests and the same retained UUID under two doctors.
Full frontend typechecking is also required.

Every code-changing PR needs independent agent review after its final
substantive push, per `AGENTS.md`. The current user's cross-task agreement
requires explicit authorization before coordinating with a reviewer agent.
Canonical staging release is founder-only, manual, from the exact selected
merged PR commit on protected master. Do not deploy this unmerged checkout or
claim local fixture checks are actual recording-to-save acceptance.

After reviewed release, sign in as the same account in Core and CosmaBot,
create one pair, review/connect precisely those two new appointments through
the existing setup path, and perform the two consented invented microphone →
transcription → edit → service-save → exact-reopen tests. Preserve the older
failed requests, recordings and notes. Native qualification stays separate
from browser/service evidence.
