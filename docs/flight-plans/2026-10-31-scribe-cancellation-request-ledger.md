# Scribe cancellation request and account processing fence

Risk: Tier 2 (authenticated account/PHI access); one receiver-first deployable outcome.

## User behavior

Account → Cancel Scribe access confirms cancellation without deleting the shared
Cosmaneura/Clerk sign-in. The authenticated existing Doctor receives an immutable
receipt. The default export window is 90 days; an explicitly pinned customer
contract may select 30–90 days. Reads do not bypass current account, membership,
BAA, tenant or record authority. New processing is denied for that Doctor only.
Unrelated practice members and their records are unchanged. When the window ends,
clinical reads are denied; the service never claims that PHI has been destroyed.

## Implementation

Core owns the exact-Doctor cancellation ledger and the dual-authenticated
`/service/v1/agent/scribe/cancellation` preview/request/own-status contract.
The existing verified issuer/subject selects the Doctor; no email adoption,
identity upsert, practice-wide target, or view-as request is accepted. The Doctor
row lock serializes acceptance with the existing native capture authority locks.
A preview digest binds the immutable server-selected ownership inventory and
customer policy. Acceptance uses a UUID Idempotency-Key and `CANCEL` confirmation.
Own status can be recovered without a locally retained operation ID or capture
link. No token, clinical text, patient identity, or blob path enters the ledger.

The existing `doctor_access` processing predicate consults the ledger. Native
capture lease/upload/completion/note mutations, Core processing ingress, Zoom
callbacks, and legacy processing jobs retain the default processing purpose.
Reviewed own-record reads explicitly select read purpose; GET alone confers no
read permission. Session Prep's separate access predicate applies the same fence.
Existing record reads/export surfaces remain available during the window; this
change adds no bulk export generator. Already issued remote upload URLs and work
already executing are not withdrawn by the receipt; new Core-authorized work and
clinical publication boundaries must pass their existing fresh access checks.

## Expand and activation order

1. Integrate on actual Core master `b3fe7a770623d253f5a885465cb04511bc2c392c`;
   preserve the newer pilot/Session Prep/business work.
2. Rehearse additive migration `49126bf99c72`, which now follows actual trunk
   head `6bd4416fe39c`. No prior migration, clinical row or shared identity changes.
3. Apply that reviewed expand through a separate founder-authorized migration.
4. Deploy the reviewed Core/receiver image, then mark
   `SCRIBE_CANCELLATION_LEDGER_READY=true` only after schema qualification.
   This permanent schema/access guard stays true when intake is closed.
5. Pin the real policy approval digest and enable
   `SCRIBE_CANCELLATION_ENABLED=true`. The default window/scope are 90 days and
   `core-and-scribe-clinical-records`; no BAA is invented or accepted.
6. Rebuild the reviewed native cancellation client, qualify authenticated
   request/reopen/read-only behavior, and only then distribute it.

Recovery closes intake, preserves the immutable ledger and its ready guard, and
keeps canceled processing denied. Do not drop the table or disable the ledger
ready guard to roll back a canceled account. No production action is authorized
by this source packet.

## Retention boundary still unfinished

The user requested eventual PHI destruction including the account's Core records,
with a certificate; de-identified data follows the BAA, audit/BAA/compliance records
remain six years, financial/contracts seven years. This source deliberately
implements no destruction runner, backup purge, certificate issuance, or legal
hold adjudication. Historical creator bindings are not a verified erasure
manifest: practice custodianship, shared Core lineage, and all retained copies
must be classified before a later authorized destruction operation. Responses
state `resource_manifest_status=unresolved` and `erasure_confirmed=false`.

## Verification and review

Focused SQL/closed-route/retry tests cover two synthetic Doctors sharing one
practice, durable recovery, empty Scribe history, 90-day boundaries, intake
shutdown, and the actual account resolver/dashboard gate. The migration graph
must remain single-headed. A bounded owned PostgreSQL16 DDL rehearsal and an
independent same-chat final-source review are required before source acceptance.
Native runtime, live production migration/configuration, and real cancellation
acceptance remain separate gates. No live cancellation or destruction is run.

The Session Prep aggregate preflight is qualified only with ledger readiness off.
A ready ledger makes it refuse until the separate cancellation aggregate
predicate is qualified; this interim guard never substitutes false eligibility.
