# Session Prep: retained corrections of web-uploaded notes

Risk: **Tier 2**, existing patient-note writes and provider authority checks.

## Outcome

A generated web-uploaded note can contain an incorrect dated heading or
template-derived provider identity. Its owning provider can correct section
headings/text or remove inaccurate sections before Session Prep review. The
save appends a new current version, preserves the earlier body/review and
requires explicit review of the corrected version. Provider templates and
source recordings/transcripts are unchanged.

This is a Core-only change based on master
`514c32d3375575ef91ccfbcdbbdae167ec0f205a`. It introduces no database schema,
environment configuration, job configuration, or Scribe deployment change.
Brief reads and worker publication reuse the existing bounded owned
Appointment → Note source fence from factual-answer publication. NOWAIT and a
savepoint preserve retry handling; sources are reread under the fence before
the artifact/read receipt commits. No model call runs under these locks.

## Authority and consistency

- The normal internal API supplies the current authenticated provider and
  browser request binding. View-as writes are rejected.
- The Session Prep flag and current onboarding/practice entitlement still
  apply. Practice/doctor/member state is reread under the existing locks.
- Saving requires the exact displayed digest, a current owned note, matching
  live owned transcript/schema, owned patient/appointment, and registered web
  recording. Any retained capture binding, including a revoked binding,
  excludes this editor; capture notes keep their Scribe workflow.
- Appointment/note locking and the existing partial unique index preserve one
  current appointment/schema version. Stale saves and busy history reads
  return 409; changed access is denied. Review evidence is never rewritten.
- Superseding the source removes its old finalized body from selection. The
  replacement is excluded until explicit review. The current source digest
  changes, so the retained older cache cannot satisfy the current lookup.

## Release and recovery

Generate the OpenAPI contract from the reviewed source and build the backend
and web frontend together. Reconcile the selected merged commit against the
actual deployed Core image before any separately authorized rollout. The
existing Session Prep flag controls the affordance; this plan changes no flag
or deployment configuration and authorizes no deployment.

Older frontends ignore the additive response field. New frontends omit the
editor if an older backend supplies no editable-note list. A previous Core
build can read retained versions and keeps the replacement unreviewed; a code
rollback does not restore or delete patient data. Do not automatically restore
the former inaccurate current version.

On unknown save outcome or stale access, the editor blocks retries and keeps
selectable text only in component memory. The provider can copy it before an
explicit full visit reload. A confirmed save is reported separately from a
failed refresh. Note identity/digest changes reset the editor.

## Acceptance evidence

Run the focused correction test with
`--confcutdir=tests/authority_kernel` and an explicitly owned synthetic
PostgreSQL socket/database. Never import the repository-root cleanup fixture.
The SQLModel-created lab checks ownership, capture exclusion, real row-lock
contention, current-slot supersession, original evidence retention, fresh
review and the actual cache lookup digest. It does not rehearse Alembic
migrations or a full authority-token/worker read.

Run source-editor/viewer/review-button Vitest tests, generated-client type
checking, Ruff and focused backend Pyright. Record final results and the
non-implementer review against the final substantive commit before publishing.
Staging acceptance must then correct a fictional owned web-uploaded note,
reload it, review it explicitly and confirm a later same-patient visit uses
the corrected dated source.
