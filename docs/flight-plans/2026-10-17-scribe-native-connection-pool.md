# Core to Scribe connection reuse — Tier 2

Immediate physical Mac87 recording took 5.661–5.748 seconds. The current Core
capture route creates a private event loop and HTTP client for every request;
closing that client also closes its TCP/TLS connections. This change permits
connection reuse on Core's existing ASGI loop. It does not claim a measured
latency improvement or complete the under-five-second physical acceptance.

The qualified starting source is Core
`6881aacdec032a220ee6ea69f275eca33c3bb424`, tree
`69c21627486a035c8c3c07599e3d1d4aa77e1702`. This PR has one outcome: share the
HTTP transport for capture-worker calls to the configured Scribe HTTPS origin.
There is no schema, contract, dependency, native, SQL, credential, configuration,
permission, consent, authority, expiry, cache-policy or recording change.

## Ownership and failure behavior

The existing FastAPI lifespan creates the transport after the ASGI loop starts;
no client or loop is created at module import or before a worker fork. Existing
startup/shutdown callbacks and lifespan state are preserved. The pool permits
eight connections, four idle connections, and 30 seconds of idle keepalive.
This idle socket lifetime grants no authority or token lifetime.

The existing capture boundary passes only a pool reference and cancellation
budget into its private worker. Each request constructs and closes its own
credential and HTTP client. Every existing `get_token` call, header, payload,
claim, policy, key, SQL statement, receipt and current-state check remains in
that worker. The raw HTTP transport task runs in an empty Context; no worker
SQL/session/identity ContextVar enters the parent loop. It retains no cookie
jar, credential, default authorization header, claim or response cache.

Requests remain fixed to the configured HTTPS origin with trust_env and
redirects disabled, no transport retry, and unchanged per-request timeouts.
The bridge collects and closes the complete raw response before passing bytes
back to the worker. Its 32 MiB plus 8192-byte outer buffer ceiling matches the
existing capture boundary; all smaller decoded operation/model bounds remain.
Socket extensions and live async streams never cross event loops.
The returned memory-only body yields at most 64 KiB of raw bytes per chunk,
so content decoding cannot inflate the entire compressed envelope in one
chunk before the existing worker checks its decoded byte limit.

Cancellation, expiry, disconnect and shutdown cancel the exact pending network
operation. The private worker keeps its existing admission slot until that
operation and response closure join. Repeated cancellation cannot skip cleanup.
A transport from a different or closed loop fails closed, with no replacement
connection fallback. A shutdown closes admission, joins owned operations and
closes the pool once. A resistant cleanup remains owned; it is never reported
as completed merely because a timer or process shutdown has begun.

## Validation and delivery

Offline synthetic tests exercise actual capture routes, client factories,
lifespan callbacks, fresh token calls, header/cookie isolation, concurrent
private loops, httpcore HTTP/1.1 keepalive reuse using its fake network backend,
response bounds, wrong-loop refusal, expiry, cancellation at EOF and resistant
response closure. Existing capture/deadline/native authority suites run through
the isolated authority-kernel bootstrap. PostgreSQL-only cases remain explicitly
unexecuted when no disposable PostgreSQL fixture is available; no live service
or record is used to validate transport reuse.

Independent review must cover the final substantive pushed commit. Merge alone
does not deploy. Root separately binds a merged Core candidate and reviewed
release operator, reconciles the actual staging image/config/schema, and
preserves the immutable prior image. The same receiver and app builds continue
to work because there is no wire-contract change. The first live validation is
a new consented synthetic trial with existing timing metadata and confirmed
native stop; compare both immediate and already-prepared Record paths.

If verification or the physical trial fails, stop promotion and restore the
exact retained Core web image through the reviewed image-only controller.
No migration, backfill, queue reset, identity change, data deletion or new
permission is part of recovery. Worker/scheduler need no behavioral change.
