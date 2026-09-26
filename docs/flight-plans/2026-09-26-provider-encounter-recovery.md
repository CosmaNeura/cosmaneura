# Reopen configured work on another device

Tier 2. A native device without a local journal currently shows new recording
setup even when the encounter already has an uploaded recording and Core note.
Add a read-only `provider_encounter` operation. Reuse current binding/admission
authorization and the existing creator-review receiver. Return not-prepared
only for an authorized binding without an admission. Revocation, conflicts,
pending admission and unavailable services remain errors.

Release Core first, then the Scribe receiver allowlist and Mac/iPhone UI. The UI
may restore uploaded-session review and note-save recovery in its encrypted
local journal. It must not invent a consent attestation or move unfinished
recording custody to another device. Existing local preparation and save intents
take precedence. No schema, key, lease, recording or note writes occur in discovery.

Recovery: retain journals and stop invoking discovery; existing journal-based
flows continue unchanged. Validate missing admission versus denied/uncertain
states, creator scope changes and wrong-session replies. Local checks and
implementer review follow the owner's September 25 merge policy.
