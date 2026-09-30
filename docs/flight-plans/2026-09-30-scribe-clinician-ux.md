# Scribe clinician workflow and recording responsiveness

Tier 2. The owner approved all agreed clinician UX changes, parallel implementation,
local validation, merge and staging delivery. Production remains excluded.

## Contracts and rollout

1. Merge and deploy Core's additive creator-edit consumer, read-only `save_status`,
   patient `add_birth_date` and recording-window receiver before enabling their
   producers. Existing callers, saved receipts and unedited transcript views remain valid.
2. Scribe owns immutable transcript correction storage and derived working versions.
   Apply its migration and existing-role grants as the same maintenance rung,
   then deploy the compatible Scribe producer/standalone receiver. Its flight plan
   records schema retention, publication and rollback constraints.
3. Build both native clients from the tested merged source. Preserve bundle IDs,
   signing identity, encryption keys, encrypted queues and journals. Install only
   while the apps are idle. A client upgrade does not authorize microphone capture.
4. Verify the complete normal synthetic visit, note edit/save/reopen and exact
   lost-response paths. Device results remain separate from browser/Swift models.

## Invariants

DOB entry is optional. Add-later fills only an absent date or confirms the exact
same date. Core selects the existing appointment and patient under fresh provider
scope and records a metadata-only audit digest binding the original retry key.
It never substitutes a guessed DOB or creates another visit.

`save_status` checks the exact retained request and original saved result. It never
creates a save fence, publishes another note or marks a clinical review. Explicit
`resolve` retains the existing fence behavior. A delayed original save remains
eligible after a pending status read.

Creator edits carry confirmed source/working versions and immutable edit history.
Core rejects unexplained working text/role changes, foreign lineage and mismatched
receipts. Display names do not become inferred voice identities. Source history,
translation gates, consent, current creator and normal authorization remain intact.
Native correction recovery uses encrypted environment/creator/session storage.

Recent visit badges use actual saved Core note/transcript references. An admission
receipt alone is never labeled paused, recording or processing. Readiness polling
continues long enough to cover the existing note request and recovery budget and
never invokes prepare/save from a read loop.

Recording preparation groups existing bind/lease/reservation operations using a
persisted exact recipe and separate original component keys. It does not start an
audio source or reuse expired consent. Lost replies replay the same recipe; older
pending journals retain their existing recovery path. Continuation must recover
its original allocation before changing count or generation.

## Validation and recovery

Use isolated synthetic PostgreSQL fixtures with explicit database tuples and the
isolated authority conftest. Run wire/authority/lineage, DOB/idempotency, save-recovery,
UI focus/caret/scroll and native startup/retry tests. Record exact tested commits
and implementer review in each bounded PR; local evidence is not GitHub approval.

Before staging delivery reconcile deployed source, schema, image and flags and
preserve any newer Session Prep/CosmaBot changes. Keep migrations and compatible
readers if rolling back UI. Do not remove retained transcript edits, close unknown
save requests, delete queues or reset devices as a rollback. The unclassified
September 29 audio run remains excluded from all testing and processing.
