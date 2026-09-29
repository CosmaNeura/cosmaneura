# Compose native source preparation and authorization in Core

Owner: MCD operational beta owner. Tier 2: additive composition of existing
capture source decisions under the approved recording performance work.

The installed native client already persists both original request keys and its
stop proof before sending its source-start-ready request. The Scribe receiver
currently negotiates one Core context and dispatches prepare, then authorize as
two Core HTTP requests. The latest physical build 42 trace spent 3.000 seconds in
this stage. This change adds `source_start_sequence` to Core's existing private
capture-operations facade so a later receiver update can eliminate one round
trip. It does not claim a measured device speedup.

The facade admits the same exact source payload and a distinct authorization
key. It calls the existing creator-bound prepare and authorize paths. Each step
keeps current Core admission checks before and after its Scribe I/O. A changed
admission between steps is rejected before dispatching to a replacement session.
Invalid preparation, revoked access or cancellation cannot advance the sequence.
Both receipts must describe the same immutable source and capability. No new
Scribe endpoint, source lifecycle, key, retry, permission cache, audio path or
data schema is introduced. Errors retain existing value-free HTTP responses.

The two downstream mutations retain their independent original idempotency keys.
A failure may have committed either step remotely; no implicit retry is added.
The existing native journal retains stop-only custody and requires its ordinary
explicit recovery flow. No recording, queue, source journal or failed job may be
deleted to recover an uncertain result.

Roll out receiver-first: merge and qualify Core, then deploy its immutable
staging candidate while retaining existing Scribe receiver behavior. Only after
Core is reconciled and healthy may the separately reviewed Scribe receiver use
the new operation. Old native clients and old receiver requests remain supported.
The sender's coherent staging revision is the release control for this change,
owned by the beta owner for the existing synthetic cohort and superseded by the
next qualified revision. No additional native install is required. Revert the
Scribe receiver first to restore separate Core dispatch; retain the additive Core
operation and all data. Production and broader cohorts remain separate decisions.

Validate the actual facade, gateway and HTTP workflow client with synthetic
sources: unchanged keys/creator/position, four Core admission boundaries, changed
admission, lost replies, cancellation between steps, invalid/crossed receipts and
existing independent operations. The dynamic private facade still accepts a raw
Request and returns JSONResponse; this additive operation is not a generated
internal-api/client schema change. Confirm that with the scoped OpenAPI projection
before claiming code generation is unnecessary.
