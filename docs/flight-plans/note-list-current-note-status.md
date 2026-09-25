# Notes sidebar status from current notes

## Outcome and scope

A named Scribe encounter without a transcript or note appeared as Completed in
the notes sidebar while its detail page correctly displayed Not Started. The
sidebar used the appointment title as its completion signal.

The notes list now returns a status for each visible appointment based on the
existence of a current note owned by the same doctor. Superseded notes do not
count. The sidebar uses that status, matching the existing detail page. This
status describes note availability; explicit review for Session Prep remains a
separate action and is not inferred from Completed.

Risk class: **Tier 2**, because the read-only change touches the patient-record
list path. No database schema, stored record, authentication, authorization,
cohort, capture service, or deployment configuration changes.

## Compatibility and release

`note_statuses` is an additive, default-empty field on the existing list
response. The appointment wire shape is unchanged. Old clients ignore the new
field; the new client hides an unknown badge with an older backend instead of
guessing from the title. One scalar query retrieves current-note appointment
IDs for the already owner-scoped list, without reading note text or adding a
query per visit.

Ship through the normal reviewed Core release. Keep the separately published
PR499 candidate and its evidence intact until a new combined candidate is
explicitly selected. No Scribe or Sovereign release is required for this change.

## Verification and recovery

Local regression coverage checks named empty visits, unnamed visits with notes,
superseded notes, owner isolation, multiple notes per visit, bounded query count,
and an empty list. Frontend checks exercise backend status propagation, denial
cleanup, older-backend compatibility, and appointment-keyed badges. Validate the
generated client against the actual router schema and run frontend type checks.

After deployment, revisit the existing fictional unfinished Scribe encounter:
both sidebar and detail should say Not Started. A fictional visit with a current
note should show Completed in both places. Reconfirm that the review button and
prior-note eligibility behave independently of the badge.

Recovery is a source revert or the previously retained Core image. The additive
response field requires no data rollback or migration. Preserve separate Scribe
and Sovereign services and all current runtime configuration during any rollout.
