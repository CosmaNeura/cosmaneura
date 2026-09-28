# Current reviewed preparation for provider questions

Tier 2: additive provider-context contract and canonical final-write boundary.
Base: bb2094db50f296f03c351d49a1e595b1e09514de. This Core receiver is one
deployable outcome; Sovereign consumes it in a later, independently reviewed PR.

Normal embed chat supplies an encounter hint but no chart or conversation
history. This receiver gives an accepted, authorized provider chat turn the
current READY preparation summary plus reviewed-note provenance. It does not
claim complete chart access. Pending/unavailable preparation stays explicit.

The platform remains the owner of identity, notes, reviewed sources, durable
artifacts and conversation state. Sovereign consumes the minimum necessary
server response. No raw browser metadata, note excerpt reader, new model,
history service, dependency or database migration is introduced. Scribe capture,
native delivery, workers and their writers are unchanged.

Contract: contracts/schemas/provider-question-context-v1. The existing
session-prep.context.read UI-action authorization remains unchanged. New reads
require an already accepted chat_text turn in the current record-bound mount,
verified workload, actor, tenant, current owned encounter and current enabled
provider cohort. Ordinary generic education skips this tool. Scope is never
derived from caller-supplied patient/context selectors.

AuthorityIdempotencyRecord retains an immutable binding to the exact request
and result, using existing durable artifacts or non-content readiness markers.
No new schema or clinical copy is stored. After a read, final_response requires
its exact provider_context_ref. Non-ready reads only allow outcome=unavailable.
Every final write/retry revalidates current authority and source evidence in
the final phase/audit/idempotency transaction before commit.

Source acceptance uses bounded prior-review appointment locks followed by
selected-note locks. The extra prior-review appointments prevent a concurrent
restore from introducing a previously superseded source after validation.
NOWAIT and explicit rollback avoid a Doctor/Appointment deadlock with existing
background writers. Busy or changed sources refuse the final with a value-free
409; the consumer emits no clinical answer and offers a new explicit question.
No transaction or connection is held during a model call. The route retains
and drains its worker using the existing cancellation boundary.

Verification uses fictional fixtures and the real Agent API, authority,
reviewed-source selector, worker artifact and canonical writer. Focused owned
PostgreSQL cases cover source changes between read and final acceptance;
appointment-first existing-note updates; real Doctor-FK note insertion; a
writer blocked after final source validation; and restoration of an unselected
reviewed source. Additional tests cover immutable pending receipts, current
cohort revocation, wrong turn/workload, unknown selectors, legacy finals and
unchanged Session Prep action rules. No live provider call or patient data is
part of these checks. The temporary PostgreSQL harness tests real row locks,
FKs and model constraints; it does not claim migration-trigger coverage.

Rollout is receiver first, preserving current configuration and cohort. The
Sovereign sender must mirror these exact versioned schemas/fixtures, request
context only for grounded clinical questions, and carry the receipt through
final persistence. Until that consumer is deployed, legacy requests continue
unchanged. The current caption release and Scribe images are independent.

Recovery before sender activation is to revert this receiver change. After
sender activation, disable the consumer's existing FEATURE_SESSION_PREP-gated
context use or revert that sender first, then revert Core if needed; retain
existing receipts/artifacts. Do not delete data, weaken authorization or retry
accepted model work. Final independent post-push review and actual staging
release acceptance are separate steps owned by the root task; this document
does not assert that either has occurred.
