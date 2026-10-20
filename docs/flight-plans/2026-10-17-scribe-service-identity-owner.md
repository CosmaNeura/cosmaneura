# Scribe service-identity lifecycle candidate

Tier 2 · Owner: John · Candidate only; no deployment performed or measured savings.

## Intent and boundaries

The current workflow creates and closes an official async DefaultAzureCredential
per capture HTTP request. The installed SDK owns its managed-identity token cache
and async transport. A private `asyncio.run` capture worker cannot share that
transport directly with another worker loop. This candidate keeps one official
credential on the ASGI lifespan loop and lends request-local, fixed-scope access
to it. Construction performs no token acquisition.

Only deployment-owned managed-identity configuration and its one service scope
enter this adapter. The SDK continues to decide token refresh and expiry. Each
workflow operation still calls `get_token`; no patient JWT, source grant, capture
key, consent result, or custom token cache is shared. Fresh Entra/Clerk checks,
tenant/encounter ownership, consent, source validation, one-use grants, deadlines,
SQL boundaries, request headers, and response validators remain unchanged.

The optional `SCRIBE_WORKFLOW_IDENTITY_REUSE_ENABLED` control defaults off.
Missing lifecycle ownership and explicit test credential factories retain the
existing per-request credential path. A present owner with wrong loop, client,
scope, closed state, or expired request budget fails closed instead of falling
back. Vendor construction remains in the configuration adapter boundary.

## Implementation and failure ownership

Install the owner in the existing application lifespan. Private capture workers
receive a context-bound borrower for that request's budget. Empty-context parent
tasks serialize official SDK calls; only an immutable AccessToken returns to the
worker. Cancellation joins that exact operation, including SDK cleanup, before
the private worker can release its slot. Cancel-before-start and queued requests
are fenced. Closing one borrower does not close the owner or another borrower.
Shutdown refuses new work, cancels and joins owned operations, and closes the SDK
exactly once on its original loop. Resistant cleanup remains owned; no timeout
is reported as successful cleanup. No raw SDK exception text is transferred.
The closing/closed owner remains in application state during and after shutdown
so a late capture request is rejected before starting a private worker; it never
falls back to a new per-request credential. A later lifespan replaces it with
its own fresh owner. Test this through actual routes during token cleanup,
during resistant SDK close, after shutdown, and in the next lifespan.

## Verification and release gates

Use only synthetic fake credentials and local ASGI/MockTransport tests: distinct
worker loops and requests, fixed scope/client refusal, empty context, queued
cancellation, deadline during SDK work, borrower close isolation, owner drain,
cancel-before-start, close idempotence/error, and real workflow/route integration.
Run existing transport, workflow, deadline and timing contract regressions plus
type/lint/format checks. No live identity request, audio, device or cloud action
is part of candidate verification. Keep performance claims pending actual
`workflow_token` phase evidence.

Include the installed official SDK through its public, memory-only managed-
identity transport: two separately closed worker loops first acquire then reuse
the SDK cache, and a third refreshes after only SDK clock references advance.
No vendor cache is inspected or replaced. A pre-existing workflow timeout
fixture also needs the already-supported HTTPX `transport=None` keyword in its
fake factory; preserve its initial failure and retain the timeout assertion.

Root coordinates an exact pushed-head independent review and any separately
qualified immutable release. Recovery uses the previous qualified image; the
optional selection control is not a substitute for a security rollback. No
credential/account/IAM/config deployment change is authorized by this document.

## Cold start and app-opening preparation

With reuse enabled, SDK construction alone leaves the first service-token call
cold. Later calls may reuse the SDK's own cache and transport; this has no
measured savings yet. The user's existing authorization covers readiness work
for the same internal staging identity and audience. Root owns activation after
source review and package qualification, setting the optional reuse control only
for that existing workflow configuration and retaining the previous image.

If actual phase evidence identifies service-token cold acquisition as dominant,
a service-only startup prewarm can call the official SDK's fixed service scope
on this same owner loop before accepting work. It must use the same serialized,
tracked operation and bounded cancellation/join lifecycle, ignore the token
outside the SDK's own cache, and never resolve/mint patient, encounter, consent,
session, source, key or capture authority. That is a distinct implementation
step, absent from this candidate. Client app-opening Clerk/native identity
warmup remains its current separate SDK path; this Core owner is not a cache for
those patient-bound credentials.
