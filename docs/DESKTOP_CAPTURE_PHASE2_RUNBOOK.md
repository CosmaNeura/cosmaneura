# Desktop capture Phase 2 runbook

This document records the rollout boundary for the provider-neutral desktop
capture coordination backend. It is an operational companion to the canonical
architecture repository, not a replacement for its contracts or ADRs.

## Phase 2 scope

Phase 2 may merge only as dormant backend capability. It provides:

- tenant-, clinician-, and appointment-scoped capture sessions;
- an exclusive source lease per appointment;
- one segment per physical start and an append-only event ledger;
- durable operation, external-resource, cleanup, and launch-authorization
  receipts;
- server-side Scribe consent/session preparation for
  `external_async_transcript` ingest;
- short-lived PKCE launch and authorization-code exchange;
- scheduling-source-neutral provider pages for encounter selection, consent
  attestation, and the browser-to-Tauri handoff;
- pre-telemetry removal and one-time browser consumption of launch material;
- a hash-only, revocable desktop session bearer with a hard six-hour maximum;
- a one-use provider upload capability with a hard five-minute maximum;
- bounded reconciliation of ambiguous provider creates and count-only health
  signals; and
- conservative heartbeat and stop-intent recording.

The business-domain schema and APIs remain provider neutral. Provider names,
references, credentials, and response shapes are confined to adapters and
encrypted or keyed-digest evidence.

## Dormant defaults

All behavior-changing settings are false or empty by default:

- `DESKTOP_CAPTURE_ENABLED=false`
- `DESKTOP_CAPTURE_PROVIDER_CREATE_ENABLED=false`
- `DESKTOP_CAPTURE_RECOVERY_ENABLED=false`
- `DESKTOP_CAPTURE_REDIRECT_URIS=`

No production provider is constructed by the HTTP start route in Phase 2.
Changing environment values alone must not make a real create call reachable.

Recovery is deliberately independent from creation. Incident response may
disable new launch/start authority while bounded lookup and cleanup continue
for operations that were already committed. Likewise, an exact existing
capture may move only toward stopping when new ingress is disabled or current
policy authority is lost. The stop-only use of an issued bearer ends fifteen
minutes after its original expiry. Stop never mints a token, starts a segment,
proves a provider stop, or closes a source lease by itself.

## Invariants before a provider create

The server must derive and revalidate all authority. A request body may not
assert tenant, clinician, patient, consent, lease, provider reference, or
provider metadata.

Before create, and again before releasing a returned upload capability, the
server verifies:

- the exact practice, clinician, appointment, capture session, and source
  lease binding;
- current practice agreement, entitlement, membership attestation, and
  non-cancelled appointment state;
- current matching Scribe consent and processing-session receipts;
- an active, unexpired, generation-fenced source lease; and
- a matching idempotency reservation and caller scope.

If authority changes while the provider call is in flight, known provider
evidence is retained for reconciliation and cleanup, but no capability is
released.

## Phase 3 activation gates

Do not enable desktop capture for patient traffic until all of the following
are reviewed and evidenced:

1. Keep the checked-in `/desktop/v1` contract and Tauri core wire shapes in
   lockstep. Prove possession of the installation key bound by the launch
   thumbprint, or remove that field rather than treating an unproved thumbprint
   as authority.
2. Wire the real provider dependency only in a separately reviewed change;
   keep create and recovery switches independent.
3. Implement durable provider-callback-to-session/segment mapping and deploy
   callback handlers before subscribing to SDK upload events.
4. Obtain provider evidence for upload-token expiry/revocation, resource
   erasure, and the required retention behavior. Cleanup that cannot be proven
   remains alerting and blocked, never silently successful.
5. Resolve ambiguous Scribe preparation and provider-create outcomes through
   provider-supported lookup or reviewed operator reconciliation. Never replay
   a create blindly.
6. Keep the Tauri local stop fail-safe independent from backend reachability;
   backend stop is evidence and coordination, not the sole audio-stop control.
7. Provision protected settings and credentials, apply the scheduler as a
   singleton, and prove value-free health/alerting before enabling any tenant.
8. Run a provider-owned, non-PHI staging flow covering launch, PKCE exchange,
   start, heartbeat, local stop, provider terminal evidence, transcript ingest,
   and cleanup.
9. Enable one allowlisted tenant first, observe reconciliation and cleanup
   health, and retain an immediate creation kill switch.

Rollback disables new launch and provider create first. It must leave stop,
callback processing, reconciliation, cleanup, and retained evidence running.
Never release a lease solely because a heartbeat or token expired.

## Generated desktop contract

`contracts/desktop-v1.openapi.json` is the only generated wire contract for the
Tauri companion. It contains the token exchange and capture lifecycle routes
mounted under `/desktop/v1`; it deliberately excludes the authenticated browser
launch routes under `/internal/v1/desktop-capture`. Those routes are consumed
only by the authenticated `/desktop-capture` and `/desktop-capture/connect`
provider pages; launch and PKCE material is never added to this native contract.

Regenerate the artifact after changing a desktop route or its Pydantic model:

```bash
just desktop-openapi
```

Verify that the checked-in artifact is current without changing it:

```bash
just desktop-openapi-check
```

The Python snapshot test runs the same comparison in the normal test suite.

## Deferred product decision: scratchpad

Revisit the scratchpad when the clinician review workflow is designed. The
default plan is to omit it unless user research demonstrates a concrete need.
Removing it may materially reduce UI, persistence, synchronization, privacy,
and testing work. Phase 2 must not introduce a scratchpad store or contract in
anticipation of that later decision.
