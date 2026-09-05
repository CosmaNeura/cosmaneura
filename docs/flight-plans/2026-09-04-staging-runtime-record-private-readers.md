# Flight plan: dormant private runtime-material readers

## Outcome and boundaries

- Tier **2** adds one dormant, one-shot reader that turns an authenticated
  broker receipt into exact private Blob runtime-record/bundle bytes and one
  exact-version Key Vault trusted root, then invokes the reviewed offline
  runtime-record authenticator in the same process.
- In scope: selected-job UAMI token exchanges, exact GET contracts, immutable
  reference/version binding, byte/digest/media bounds, shared-clock handoff,
  bounded refusals, policy pins, tests, and documentation.
- Executor dependency assembly, Azure resources or RBAC, publisher authority,
  workflow integration, live calls/rehearsal, restore, and activation are out
  of scope. `_runtime_dependencies()` continues to refuse and all activation
  and implementation bits remain false.

## Risk and dependencies

- The reader crosses Blob, Key Vault, managed-identity, and signed-material
  boundaries, so identity/audience confusion or a versionless read could admit
  attacker-selected restore authority.
- Ordering is receipt authentication -> receipt-to-executor handoff -> offline
  runtime signature authentication -> this private-reader slice -> future
  dependency assembly/containment/publisher-authority slices -> separately
  authorized live rehearsal. Each merged slice remains safely dormant.
- No cross-repository sender changes occur. The receiver contract must remain
  merged before any future publisher or workflow activation.

## Current deployed state

- Base `2e2eec1ec180493f510f3a0445497a432d8b9086` contains the dormant offline
  authenticator but no concrete private runtime-material reader. No deployed
  state was inspected or changed for this slice.
- Recovery is a code-only revert to that base; there is no artifact, schema,
  credential, data, or infrastructure recovery boundary.

## Feature flag / release control (reversible user-visible behavior only)

- N/A: this adds no user-visible behavior. The checked-in policy is disabled,
  placeholder-bound, and the public dependency factory refuses unconditionally.

## Schema/data sequence (schema/data work only)

- N/A: no schema, data, restore, sanitizer, or migration operation occurs.

## Rollout and promotion

- Hold after focused/broad offline tests, policy validators, static checks,
  budget verification, independent final-head review, and protected PR checks.
- Any extra request, redirect/final-URI drift, proxy or ambient credential use,
  wrong UAMI/audience echo, missing/ambiguous length, non-identity encoding,
  unexpected status/media type, size/digest mismatch, secret-ID/version drift,
  disabled secret, receipt/execution mutation, timeout, or clock regression is
  an immediate refusal.
- No canary or live Azure call is permitted. Before later activation, operators
  must prove the exact trusted-root UTF-8 bytes fit the Key Vault value limit
  and this policy's 25,600-byte cap. That real-root fit check is unresolved;
  failure requires a separately reviewed storage design, not truncation.

## Security and recovery

- Exactly five HTTP requests are possible: storage token, record, bundle,
  vault token, and exact-version secret. Storage and vault tokens use distinct
  exact audiences; optional token type, resource, and selected-job client-ID
  echoes must match exactly when ACA returns them. Default transports disable
  proxies and redirects and use the reviewed
  immutable-image TLS trust path.
- Both sealed receipt-derived references must be identical, content addressed,
  and bound to the configured Blob host/container before I/O. Receipt state and
  platform execution name are rechecked between authority stages. Responses
  require one Content-Length, bounded bodies, exact final URIs and media types,
  and no transfer encoding; sensitive response bodies never enter errors.
- Key Vault admits only `/secrets/{name}/{32-lowercase-hex-version}` on the
  pinned vault host, with exact `api-version=7.4`, exact response ID, enabled
  status, UTF-8 bytes, and SHA-256. Authentication receives the same trusted
  clock object as receipt processing and the exact verifier instance.
- The follow-up authority-seal slice retains those same exact trusted-root
  bytes in an init-disabled sealed runtime-material carrier after successful
  authentication. Its digest must still equal both the signature proof and
  configured root pin; no second root read or serialized handoff is introduced.
- Recovery is to retain dormancy or revert. No live side effect exists.

## Completion

- Evidence: exact request/order/header/query tests; identity, framing, byte,
  digest, secret, mutation, concurrency, and bounded-error regressions; staging
  restore tests; Ruff, Pyright, compile/policy validators; budget at or below
  2,000 changed lines/50 files; independent final-commit review.
- Follow-ups: independently review and wire default dependencies, prove private
  DNS/RBAC/egress and real-root fit, bind immutable publication authority, then
  conduct a separately authorized non-PHI rehearsal before changing any gate.
- Contract records: this plan, both staging runbooks, runtime-binding policy
  and digest, and the trusted-executor child-policy pin and digest.
