# Flight plan: dormant exact private launch-record reader

## Outcome and boundaries

- Tier **2** fills only `LaunchMaterialDependencies.read_launch_record` with a
  dormant, one-shot reader for the exact provision launch record named by the
  sealed authenticated broker receipt.
- In scope: a distinct non-polling retrieval contract, frozen receipt authority,
  provision-job UAMI/storage-audience binding, strict Blob-object response
  framing, digest/size checks, bounded errors, tests, and policy pins.
- Default executor dependency assembly, workflow and infrastructure changes,
  live Azure access, evidence/HMAC ingress, publisher authority, verifier
  containment, and activation are out of scope. `_runtime_dependencies()`
  remains an unconditional refusal and every activation bit remains false.

## Risk and dependencies

- The reader crosses a private storage boundary. A selector, identity, route,
  framing, or time-of-check/time-of-use error could admit attacker-selected
  restore authority, so all request authority comes from a frozen snapshot of
  canonical authenticated receipt bytes and policy pins.
- Ordering is authenticated receipt -> runtime-material private readers -> this
  launch-record reader -> future evidence/HMAC ingress and default dependency
  assembly -> separately authorized non-PHI rehearsal. Publisher authority and
  verifier containment remain independent prerequisites.
- No new architecture decision is needed for this slice. The existing sealed
  managed-identity private-Blob transport is reused through a distinct launch
  request factory rather than widening the receipt polling contract.

## Current deployed state and recovery

- Base `644d41435da919f98ac710d014bee69f6dc6900d` contains the dormant runtime
  readers and injected launch-record seam but no concrete launch-record reader.
- No deployed state is inspected or changed. Recovery is a code-only revert to
  that base; there is no schema, credential, artifact, data, or infrastructure
  recovery boundary.

## Feature flag / release control (reversible user-visible behavior only)

- N/A: this adds no user-visible behavior. The checked-in policy remains
  disabled and placeholder-bound, and the public dependency factory refuses.

## Schema/data sequence (schema/data work only)

- N/A: no schema, data, restore, sanitizer, or migration operation occurs.

## Retrieval and security contract

- Only `provision` is admitted. The URI is derived as the configured mailbox
  host + provision container + the authenticated launch digest filename. No
  caller path, query, fragment, userinfo, port, listing, polling, retry, mutable
  pointer, redirect, or second object request is accepted.
- The request seals the exact provision job UAMI resource/client IDs, storage
  audience, account/container/private-endpoint IDs, API version, media type,
  byte ceiling, and ten-second whole-exchange timeout. The concrete default
  builder disables ambient credentials, proxies, and redirects and uses the
  immutable-image CA bundle.
- The Blob object response—not the managed-identity token response—requires one
  canonical `Content-Length`, no `Content-Range` or `Transfer-Encoding`, absent
  or identity content encoding, exact final URI, status 200, JSON media type,
  bounded nonempty bytes, and the receipt digest. Existing bounded token
  response framing remains compatible with the receipt transport.
- The reader owns an atomic one-shot claim. Canonical receipt bytes and every
  authority projection are snapped before request construction and rechecked,
  with the platform execution name, before and after I/O. Failures burn the
  reader instance and expose only bounded codes.
- The private-endpoint ID is a policy/request binding, not proof of packet
  routing. Activation still requires live evidence for private DNS resolution,
  public-network disablement, RBAC, and explicit egress containment.

## Rollout and completion

- Hold after focused and broad offline tests, Ruff, Pyright, compile/policy
  validators, change-budget verification, independent final-head review, and
  protected PR checks.
- Regression evidence covers exact UAMI/audience/URI requests, one request and
  no polling, strict object framing, status/media/size/digest failures, token
  parsing compatibility, authority races, response reuse, concurrency, and
  secret-free failures.
- Follow-ups remain separately reviewed: immutable publisher authority,
  private evidence and HMAC ingress, verifier runtime containment, default
  dependency assembly, infrastructure/DNS/RBAC/egress evidence, and an
  authorized synthetic-only rehearsal.
