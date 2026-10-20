# Reuse the original mounted context for first native source start

Tier 2; native source/consent boundary and additive cross-repository contract.
Owner: John. Scope: existing fictional staging cohort. No migration, dependency,
configuration, credential/cohort change or production deployment is included.

Add `action: prepared_source` to the private native Core facade. It accepts only
the existing `source_start_sequence` operation with first sequence zero and
lease generation one, an exact binding/workspace/account and the original
prepared context. Existing native authentication checks current Clerk identity,
agent and capture workload policies. Reuse the ordinary prepared caller with
fresh current clinical/consent/admission checks, the original stored context
issued less than 30 seconds ago, expiration/revocation and exact provider/visit
binding. Never refresh context age or infer permission from the retained token.

The unchanged source sequence performs separate preparation and authorization
decisions using the original distinct idempotency keys, current authority
before/between/after remote I/O, receipt/session/lease/proof validation and
cancellation fences. Reauthorize the current encounter before publishing the
existing result envelope: operation source_start_sequence, receipt preparation
and authorization. No automatic retries or partial replies. It grants no new
mutation type or permission and does not change recovery/resumed sequence paths.

Successful native mount/preparation-context responses advertise
`X-Cosmaneura-Prepared-Source-Start-Version: 1`. Release a backward-compatible
Scribe receiver first, verify its old-Core fallback, then Core advertises support.
Default/native read clients remain unchanged. The receiver must opt in only for
the original scoped mount with this exact capability and retain fresh native
account, source custody and permission checks at Start.

Acceptance: actual synthetic PostgreSQL facade tests prove both original source
decisions with distinct keys and matching existing receipt; reject wrong action,
resumed sequence/generation, selected scope, foreign context/session/lease/proof,
context age/expiry/revocation, current provider/clinical/admission changes,
malformed/lost replies and cancellation without retries or partial publication.
Offline gateway tests preserve per-step authorization and source-proof one-use
semantics. Compare ordinary and prepared source SQL/request work without claiming
physical phone latency from virtual fixtures. Repeat three fresh native starts
and recording → transcript → saved-note smoke tests after reviewed staging release.

Independent agent review after the final substantive push is required before
conditional merge. Any Core staging deployment requires a concrete exact merged
SHA and the applicable founder manual-workflow exception. Actions remain disabled.
This implementation only changes/tests local source; it does not deploy.

Recovery: restore prior Scribe receiver digest first, then exact prior Core image
if needed. Original client fallback remains. Keep every source/consent/custody
journal and recorded data; no database or audio deletion is part of rollback.
