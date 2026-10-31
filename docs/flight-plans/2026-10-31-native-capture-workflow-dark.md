# Native existing-patient capture and note workflow — dormant source

Tier 2. Depends on the reviewed provider-context and existing-patient visit facade
plus the reviewed existing three-container Core hooks. This leaf is separate to
keep that deployment outcome within the existing change budget.

The original native `operation` action remains read-only. Three additional closed
native actions reuse the existing capture operation, consent admission, note
read/generation and note-edit save/resolve/status functions. They require the
existing default-false `SCRIBE_NATIVE_EXISTING_PATIENT_VISITS_ENABLED` control,
qualified Clerk identity and both native workload policies. Provider/tenant/current
encounter lookup and token exchange run for each operation. The unchanged capture
and note services retain binding, admitted-session, consent, source, creator,
content-size, metadata-audit and idempotency checks around each remote call and
write. The native caller checks the dedicated flag again when unchanged services call
its authorize method inside their locked commit boundary. A final current lookup
also checks it. The unchanged
NoteRequest classes are moved into a shared contract module, rather than creating
a looser native note model. Note-edit body key must equal the header key.

The existing loopback relay allows100 seconds for admission and notes instead of
cutting them off at60; Core's original90-second whole-request deadline is unchanged.

No Agent API, global Authority Kernel, Session Prep, capture, notes, source or long
visit control is enabled. No schema, migration, registry, resource, dependency,
identity grant, production configuration or runtime activation occurs here.

Receiver-first sequence: merge/qualify the Core-context consumer while its flags
remain false, merge/qualify this Core contract, build exact merged client and paired
images, then obtain the reserved owner cohort/activation decision. Only existing
patient new visits are supported; new/provisional patient creation, patient matching,
DOB and private note controls remain unavailable. Optional combined note-opening
is not advertised by the new consumer; ordinary note reads perform the same work.
The initial capture Start pair uses the original owner proof, lease and two retained
idempotency intents; no renderer can submit those owner operations generically.

Recovery: disable native existing-patient visits and receiver admission/pickup,
retain original uncertain operation/save keys and receipts, and recheck the same
request after authority becomes available. Do not infer an unconfirmed write did
not occur, rebind its intent, delete capture ciphertext or revoke drain access via
this flag. Source rollback leaves the existing data/audit structures intact.

Checks use invented people/credentials and isolated mocked boundaries, no PG or
production services. They qualify dispatch, current-authority and key boundaries;
they do not attest real grants, clinical practice, delivery or production parity.
