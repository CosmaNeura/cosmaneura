# Notes sidebar status from current notes

## Outcome and scope

A named Scribe encounter without a transcript or note appeared as Completed in
the notes sidebar while its detail page correctly displayed Not Started. The
sidebar used the appointment title as its completion signal.

The notes list now returns a status for each visible appointment based on the
existence of a current note owned by the same doctor. Superseded notes do not
count. An admitted visit-audio upload is durable server evidence that note
generation started, so a second browser can show the existing `in-progress`
state without relying on the uploading browser's memory. A scheduled visit or
unfinished capture without an admitted upload remains `not-started`.

The additive note-detail status distinguishes a recent admitted upload from one
that is more than 15 minutes old. The detail page polls only the recent state;
after the bounded window it says the note is taking longer, explicitly avoids
claiming failure, and offers a manual check. This status describes note
availability and processing only; explicit review for Session Prep remains a
separate action and is not inferred from Completed.

Risk class: **Tier 2**, because the read-only change touches the patient-record
list path. No database schema, stored record, authentication, authorization,
cohort, capture service, or deployment configuration changes.

## Compatibility and release

`note_statuses` is an additive, default-empty field on the existing list
response. Its deployed enum remains exactly `not-started`, `in-progress`, or
`completed`; an older or already-open client therefore never receives the new
`taking-longer` value. `NoteDetail.note_status` is a separate additive field
that may return `taking-longer`; old clients ignore that field, while a new
client against an old backend falls back to the existing browser-local upload
marker. The appointment wire shape is unchanged.

Two scalar list queries retrieve current-note and admitted-upload appointment
IDs for the already owner-scoped list. They read no note, transcript, patient
name, audio key, or audio content and add no query per visit. The upload query
requires doctor ownership on both rows and matching appointment/patient scope.
The detail query applies the same doctor, appointment, and patient scope and
uses the newest upload timestamp.

Ship through the normal reviewed Core release. The contract is safe in either
backend/frontend order, but the operational-beta release should still bind and
test one exact combined Core source and image. Keep the separately published
PR499 candidate and its evidence intact until a new combined candidate is
explicitly selected. No Scribe or Sovereign release is required for this
change. Do not change worker count or infer worker health from this presentation
state.

## Verification and recovery

Local regression coverage checks named empty visits, unnamed visits with notes,
superseded notes, owner/patient isolation, recent and older admitted uploads,
multiple uploads per visit, completed uploads, scheduled-only visits, bounded
query count, an empty list, a constant PostgreSQL bind count, and visits added
between reads. Frontend checks exercise polling, hidden-tab behavior, rejected
reads, bounded requests, unmount cleanup, server transition to the delayed
state, manual retry, older-backend compatibility, and appointment-keyed badges.
Validate the generated client against the actual router schema and run frontend
type checks.

After deployment, use synthetic data only. Admit an upload on one browser and
open that visit on another device: both list and detail should show generation
without local browser state, then show the note when it arrives. Exercise an
older synthetic admitted upload and confirm the list retains its compatible
progress badge while detail says `Taking longer` without claiming failure.
Revisit an unfinished Scribe capture with no admitted upload and confirm it is
not misclassified. A fictional visit with a current note should show Completed
in both places. Reconfirm that source review and prior-note eligibility behave
independently of the badge.

Recovery is a source revert or a retained migration-compatible Core image. The
additive detail field and list projection require no data rollback or migration;
existing uploads and notes remain untouched. Preserve separate Scribe and
Sovereign services and all current runtime configuration during any rollout.
