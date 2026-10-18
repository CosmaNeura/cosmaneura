# Consented receiver-to-Core compound admission

Tier 2: server-only context custody and cross-repository admission composition.
Owner: John. This package is an inert source candidate, not a deployed change.

## Outcome and scope

When a provider reads setup for 30–60 seconds, the mount context has expired.
The receiver currently negotiates a new preparation_context and then submits
prepared_admission. A validated mount may advertise protocol support that lets
one later consented fresh_admission request do both operations inside Core.
It still executes context issuance/resolution and unchanged admission/metadata;
only the intermediary receiver-to-Core wire entry is removed. No saving is
measured. Window/source composition is a separate future change.

No native, Clerk SDK, consent UI, audio, encryption/DEK, dependency, schema,
configuration, tenant derivation or patient-authority implementation changes.
No microphone, session, key or admission preparation runs merely on app opening.

## Protocol and custody

Core adds fresh_admission on the existing authenticated native route. It
requires exact current binding/workspace, consent attestation, configured
capture, and private-context retention, and forbids a caller-supplied context,
operation, opening nonce or preparation selector. Both workload policies,
current Clerk/session/view-as, encounter/capture enablement and bounded body/
response validation remain the route's existing checks.

After ordinary exchange/mount transactions close, Core invokes the extracted
unchanged prepared implementation. SameProviderCaller and admit_capture calls
remain equivalent; the underlying _prepare_and_claim, _claim, _accept, _finish,
metadata authority and deadline implementations are unchanged. No transaction
is kept open over downstream await, and no access decision survives a closed
transaction. Late irreversible consent/session outcomes retain their existing
journal-only custody and cannot be undone by a receiver publication check.

The successful mount alone advertises singleton
X-Cosmaneura-Compound-Admission-Version: 1. The receiver enables it only when
that header and both existing admission metadata headers are exact singleton
1 values on a fully validated current mount. Caller payload flags cannot enable
it. The bit stores no authority/context/consent; scope is exact origin/account/
recovery/actor/tenant/verified session fingerprint (or token fingerprint),
receiver revision/workspace/binding. It may survive a long setup read, but never
makes a claim or expired context reusable. A fresh request authenticates again.

The process-local cache is bounded to 64 scopes across capability metadata and
active calls. A long-lived platform receiver can serve more than 64 completed
scopes: only nonfailed confirmed-idle entries with zero active calls may be
LRU-evicted. Acquisition is synchronous and occurs only for actual compound
dispatch when no retained context is used; reading support does not acquire.
Release occurs in finally after exact owning transport cleanup settles, and
failed or unconfirmed close keeps active protection. No idle inference uses
opaque-context TTL. Two held calls retain protection after only one completes.

A validated same-scope mount replaces its generation without erasing its single
integer active count; no active-generation UUID collection accumulates under
repeated unconfirmed closes. Unsupported remount removes metadata but not held
ownership. A dispatched failure tombstones only its matching current generation.
Neither active scopes nor failure tombstones can be evicted into a legacy miss.
If all 64 scopes are protected, a new supported scope still refuses. Older
failure cannot poison a newer mount generation; exact final release works even
after that replacement. Disposal/restart clears process-local state. Current session/
account/revision/binding changes make old scope unreachable; old opaque context
still expires/destructively takes under its original rules.

The private result is exactly context_token/interaction_ref/client_turn_id.
The receiver removes it before the unchanged device admission schema and keeps
it only in PreparationHandoff under the original request epoch. No private
context reaches a device or log. Before first claim requires original entry age
under 15 seconds; publication/context lifetime under 30, later window/source
under 25. Native JWT 5-second validity and under-1-second dispatch rules are
separate and unchanged. Expiry timers never restart at the compound reply.

Receiver current-account checks are tied to request/workload entry, final
pre-I/O dispatch, validated admission identifiers before metadata parsing,
complete bounded public receipt validation, private-context construction, and
synchronous handoff publication. Owning transport close settles before final
account/age/cancellation checks and handoff publication; held, failed and
cancellation-resistant close tests cannot leave a pending context. Every await
can observe a changed account;
none is treated as an authorization memo. Held-response tests use actual
account/tenant/session changes rather than only Nth-call rejection mocks.

## Compatibility and recovery

Missing/malformed advertisement chooses legacy before dispatch. A still-fresh
retained context uses existing prepared_admission. An uncertain/dispatched new
request never falls back, retries, or returns a partial admission. A subsequent
attempt needs a newly validated mount and fresh consent/key custody according
to the existing caller flow. Cached support followed by an unavailable older
Core is a closed unconfirmed/recovery outcome, not a permission denial claim.

Receiver-first compatibility means Core's additive receiving action is reviewed,
qualified, merged and activated before the Scribe sender starts selecting it;
old Scribe ignores the new header. Independently review each final substantive
push. Hold sender promotion until the exact Core receiving artifact is proven.
For downgrade, restore/drain old Scribe receivers first, then downgrade Core;
never remove Core support while a new receiver can hold a capability. No
configuration flag masks a security gate or changes mutation authority.

## Validation and promotion holds

Actual successor evidence: 273 receiver MockTransport tests (223 unchanged plus
50 compound cases), including 130 completed scopes, concurrent held calls,
130 same-scope remounts with unconfirmed close and bounded count storage,
remount ownership, failed tombstones and resistant/unconfirmed cleanup. The
two Core runtime files and all 15 authored Core PG case bodies remain exactly
the frozen predecessor's bytes; its 10 AST/mock orchestration/shape checks are
retained evidence, not rerun here. These do not prove
SQL, durable journal behavior or deployed authentication. The 15 authored Core
PG cases are unexecuted and require a separately reviewed, owned real-PG runner.
Host lint/static types are recorded separately, not represented as PG success.
Original local test/type harness failures remain in the package history.

Before source promotion: independent policy review, real-PG current rows and
journal/deadline/cancel/metadata cases, focused existing Core regression union,
OpenAPI generation (enum is additive), and exact immutable source/artifact
qualification. Root alone may perform authorized staging activation after those
holds. No production/PHI promotion, Actions activation, install or live test is
part of this candidate. Existing Core67 and receiver are reference source only;
Root must recapture current image/revision/configuration before rollout.

Physical acceptance must measure fresh immediate consent-to-native readiness
on both platforms with new synthetic encounters and owned stop/release proof.
No speedup or under-five result can be inferred from one fewer wire entry.
Abort for any authority/custody regression, partial reply, hidden retry or
uncertain outcome. Recovery preserves recordings, consent, journals and keys;
there is no data reversal, migration rung or irreversible cleanup.
