# Scribe service-only warmup on authorized app opening

Tier 2 · Owner: John · Local candidate; savings remain unmeasured.

## Scope and current request

The native receiver catalog already makes the authenticated Core encounter-list
request when encounter creation is enabled. After the existing normal Clerk,
provider/tenant, body, deadline and output-size guards succeed, list and patient
search responses may ask the optional lifespan credential owner to warm its one
fixed deployment-owned service scope. No endpoint, native build, RPC, patient
JWT, consent decision, source grant, key, session or microphone work is added.
The route passes no request, result, query, bearer or principal to the owner.
Other encounter actions, failed requests and absent lifecycle ownership do not
warm or create a legacy credential. SDK scope, identity chain, expiry and cache
semantics remain vendor-owned and unchanged.

## Ownership, priority and cancellation

Reuse remains default off through the existing configuration. With an owner,
app-opening warm work is coalesced to one empty-context parent-loop task, sharing
the same official SDK and serialization lock. It retains no returned token.
Warmup has a fixed two-second soft acquisition deadline. Expiry cancels its exact
task once and retains ownership until cleanup joins; resistant SDK cleanup may
exceed that deadline and is never reported as bounded success.

A foreground credential request cancels pending/in-flight warm work once and
joins its cleanup before acquiring the same SDK. It still calls official
get_token under its current capture deadline. New warm work is skipped while a
foreground operation is pending, and shutdown refuses new warm requests,
cancels/joins all owned work, then closes the SDK once. The existing retained
closed-owner fence remains in place. No periodic refresh loop is introduced.
A simultaneous cold Record may therefore still pay cold token acquisition or
joined cancellation latency; only measured workflow_token phases can establish
savings. Native Clerk token_ms and fresh patient/source checks are unaffected.

## Verification and release

Use synthetic credentials, local ASGI transports and the official installed
Azure SDK's public memory-only transport. Verify success-only route ordering,
unauthorized/disabled/invalid/expired/oversize/refused responses, absent and
closed owners, exact fixed-scope/no-context behavior, coalescing, foreground
priority, timeout, cancellation-before-start, resistant cleanup and lifespan
shutdown. Prove a cold warm call followed by separate private worker SDK cache
reuse and vendor refresh without inspecting or replacing SDK cache internals.
Run existing owner, capture, deadline, transport and workflow regressions plus
lint/type checks. No actual identity/network/audio/device action is involved.

Root owns final pushed-head independent review, immutable Linux qualification
and the existing authorized staging activation. This implementation introduces
no additional configuration or privilege. Retain the previous qualified image
for recovery; the optional reuse selector is not a security rollback. Measure
actual consent-to-ready and Record-to-native-ready afterward; no under-five-second
claim follows from this candidate or its synthetic tests.
