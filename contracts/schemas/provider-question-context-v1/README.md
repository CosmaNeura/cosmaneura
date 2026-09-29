# Provider question preparation context v1.0.0

Additive receiver-first contract; no schema migration or raw browser context.
Sovereign mirrors these three schemas and fixtures before enabling its sender.
The existing provider-session-prep-v1 result definitions remain the exact source
for preparedBrief and provenanceItem; resolve their published schema IDs locally,
never through a runtime network schema lookup.

POST /service/v1/agent/tools/invoke with workload Bearer authorization and the
existing X-Cosmaneura-Mount-Context, X-Cosmaneura-Interaction-Ref,
X-Cosmaneura-Client-Turn-ID, Idempotency-Key and optional X-Trace-ID headers.
The singleton headers, closed JSON envelope, 8 KiB request limit and no-store
response policy are shared with the existing Session Prep tool.

Request: tool_name=provider-question.context.read, tool_version=1.0.0,
subject_ref=null, arguments={source_turn_ref, purpose=provider_question,
detail=prep_summary}. No patient, encounter, thread, context snapshot or EHR
selector is accepted. source_turn_ref names the already accepted chat_text
canonical turn. This is distinct from session-prep.open, whose rules do not change.

Scope is the current platform provider mount, workload, actor, tenant, person,
owned encounter and existing enabled provider cohort. session-prep:read plus an
existing clinical capability is required. Scope and current reviewed sources
are rechecked on reads, read retries and final acceptance. The reader never
starts generation, broadens the cohort, reads raw browser metadata or calls Scribe.

Response: {tool_call_ref, status: completed, result}. tool_call_ref is a Core
receipt, present for EVERY state; it is not browser context or a patient selector.
result carries schema_version=1.0.0, state, reason_code, source_version_digest,
prepared_brief, provenance, completeness, current_through and generated_at.

READY carries the current durable preparation summary, not full notes or a
conversation transcript. Its existing bounded PreparedBrief shape is unchanged:
recap (4000 chars), up to eight items in each trajectory/talking-points/open-
threads/cautions/outline list (1000 chars each), with bounded source_refs.
patient_reported is null in this slice. Up to five included clinician-note
provenance entries carry source/version/date; excluded and patient sources are
not returned. Completeness is complete or partial. Both timestamps are aware.
The answer must describe this limited source and acknowledge absent facts.

PENDING means a build for these exact current sources is pending/running.
UNAVAILABLE means no reviewed eligible sources or no available preparation.
Neither contains a brief, provenance or timestamps; completeness=none.
reason_code is generation_pending, no_eligible_sources or context_not_available.
The digest describes the source selection, not a permission to inspect notes.

One read is immutable per canonical turn. Exact retries return the same receipt
and state; a worker completing later does not upgrade a pending receipt. A new
question/turn may read its new ready artifact. Changed key/payload conflicts;
changed source or authority denies replay. Receipts expire after 24 hours.
Idempotency-Replayed reports exact read replay; bodies never reflect rejected
input values or exception details.

Canonical final_response adds optional provider_context_ref (opaque ref,
8–160 ASCII identifier characters). Legacy turns without a context read omit
it and remain compatible. Once a read exists, EVERY final outcome must include
its exact tool_call_ref, including explicit unavailable recovery. Omitting,
substituting or inventing a ref cannot bypass the dependency. Non-ready context
only permits outcome=unavailable. Current authority, cohort, source digest and
ready artifact are validated in the same transaction as the final phase/audit/
idempotency commit, including final-write retries. A changed dependency returns
409 PROVIDER_CONTEXT_CHANGED; revoked scope returns the existing scope/lineage
refusal. No clinical
answer may be emitted after final acceptance fails. Ordinary generic educational
requests should skip the tool; this read is for record-grounded questions only.

Final acceptance fences the target and at most 128 prior appointments with
review evidence in the existing 548-day window, including currently superseded
reviewed notes that could be restored. It then locks selected note rows and
rechecks current sources. Appointment and Note locks use NOWAIT: a concurrent
writer causes immediate rollback and 409 PROVIDER_CONTEXT_CHANGED, not a wait
while holding authority locks. The user can ask a new question after the source
operation finishes; no model work is retried. An over-bound scope fails closed.
The receipt digest binds the exact request and full returned result, including
readiness, source digest, summary and provenance. No read receipt is upgraded.

No digest/history writer, raw-note excerpt reader, model work, clinical record
write or automatic retry is introduced. Existing conversational turn replay
rules remain in force; a repeated accepted top-level chat must not rerun models.
