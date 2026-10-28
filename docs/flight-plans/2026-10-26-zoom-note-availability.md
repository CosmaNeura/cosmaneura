# Show actual note availability after a Zoom meeting

An ended Zoom meeting with an appointment link currently says “AI notes are
available” even when that visit has no note or transcript. An appointment link
establishes navigation; it does not prove recording, processing or publication.

## Scope and risk

Tier 2: this frontend status check reads the linked patient visit through the
existing authenticated note-detail path, within the authorized Zoom
integration/fix/testing task. No backend, schema, identity, consent, Session Prep,
note metadata, preferences or generated API contracts change.

For an ended, linked meeting, use the existing bounded `loadNoteDetail` reader
once. Return only ready/missing/unavailable to the schedule component; discard
the note/transcript content. Require the returned appointment and each note's
appointment/owner to agree. Session and billing/practice recovery retain their
existing behavior. Navigation cancellation cannot become success or an empty
note. There are no automatic retries or polling.

A returned current note enables “View notes” and says that notes exist for the
linked visit, without attributing them to this Zoom occurrence. An empty response
says no note is available yet and offers “Open visit.” A failed/unavailable read
says availability could not be checked, also with “Open visit.” Unlinked ended
meetings say no visit is linked. Other meeting states no longer promise note
creation at the meeting's end.

The current API does not expose bot recording/processing failure here. Neither
an empty note list, a transcript, native capture binding nor the meeting's end
is presented as processing, failed generation or recording success. The normal
visit route remains the next step to inspect the current note/transcript.

## Verification and release

Focused component/loader tests cover missing, ready, failed read, unlinked and
non-ended meetings, exact-link requests, owner/visit mismatch, access recovery,
cancellation/deadline, and unchanged authorization/cancellation interactions.
Run the relevant frontend tests, typecheck and lint; independently review the
final substantive commit. These local mocked-service checks are not a live Zoom
recording or native qualification. Existing staging failure evidence is retained.

No deployment accompanies this PR. If selected later, build reviewed merged
current trunk and reconcile the actual environment before publishing/applying.
Recovery is a reviewed source revert on current trunk and a fresh immutable
candidate; preserve existing data and deployment configuration.
