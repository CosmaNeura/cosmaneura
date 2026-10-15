# Scribe visit extras: receiver-first flight plan

Risk: Tier 2 (patient identity associations and private clinician text). Scope is
the existing authorized Scribe provider cohort; this change grants no new actor,
tenant, practice, or support-view authority. Production promotion remains manual.

## Expand rung

Migration `dd086d74a921` follows `35260adbf2ef` and creates three additive tables:
`scribe_patient_identity`, `scribe_private_note`, and retry-only
`scribe_private_note_receipt`. The revision ID was generated with Python
`secrets.token_hex(6)` and checked against the single-head migration graph.
No backfill, existing patient rewrite, merge, capture-binding update, or deletion
occurs. Applying this rung alone leaves every existing caller compatible.

Core first accepts additive version-1 encounter operations for explicit patient
matching and private reminders. Old create payloads retain their exact retry
digest and omit the default-false provisional flag. Only an explicit new
`provisional: true` sender creates a provisional identity association. Original
appointment, patient, transcript, note, consent, and capture references stay fixed.

## Sender and read activation

Ship the Core receiver/migration before the Scribe sender and dashboard editor.
The provider may record a name-only visit and explicitly match it later or
confirm that it is a new patient. Known DOB values cannot be overwritten. Names
and DOB are search criteria; selection and confirmation are clinician actions.
The association controls dashboard labels and later reviewed-note history. It
never edits original finalization evidence or supplies private reminder text to
generation. Private reminders require current doctor, actor, tenant, and provider
link, are encrypted at rest, and are unavailable during support view-as.

Before PHI-bearing sender activation, rehearse the exact receiver and sender
commits in the synthetic cohort. Require zero cross-provider/tenant matches,
stale-version overwrites, invalid retries, private-content leaks, or changes to
immutable capture/source references. Record candidate SHAs, schema rung,
observed results, and independent review after the final substantive push.
Do not treat a feature flag as rollback of stored identity decisions or schema.

## Local evidence and recovery

The isolated PostgreSQL lab is `/private/tmp/scribe-redesign-core-lab-20261015`,
owned by `scribe_redesign_owner`, database `scribe_redesign_test`, localhost port
56015. Its ownership marker limits it to synthetic data. Tests rehearse the expand
migration in a separate temporary schema, compare model columns/constraints,
verify downgrade refuses deletion, and remove that temporary schema afterward.
This is additive synthetic rehearsal, not a restored PHI production rehearsal.

Validation covers encrypted text, author and view-as isolation, exact retry keys,
optimistic versions, Unicode bounds, immutable source IDs, explicit matching and
reviewed history, and private content exclusion. The dashboard editor separately
tests concurrent typing, exact-key retry, conflicts, and stale visit reads.

Recovery stops new sender operations and restores prior readers while leaving all
three tables and their saved data intact. Existing clinical note fields and
capture behavior remain readable through their original IDs. No migration drop,
patient merge reversal, or production deployment is authorized by this plan.
