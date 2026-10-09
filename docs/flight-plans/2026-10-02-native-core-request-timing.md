# Native Core request timing — Tier 2 observational change

Slow native opening currently spends most measured wire time in Core. The
existing access log exposes only total request time. This change adds numeric
observations to the native facade so attribution precedes any cache or policy
optimization. It does not change credentials, token verification, JWKS cache
lifetime, retries, deadlines, grants, consent, SQL or response contracts.

The implementation is dormant unless the process environment has both
`PYTHON_ENV=staging` and `SCRIBE_NATIVE_REQUEST_TIMING=true` exactly. Production,
test and development cannot enable it. The existing PyJWKClient constructor
and cache options remain identical; its subclass times `super().fetch_data()`
only inside an active native observer. Clerk verification is timed as one
opaque call, without patching its internal downloader.

Each native POST emits one `native_core_request_timing` JSON line containing
only bounded, finite numeric totals and fixed phase names. It bypasses logging
processors which append account/request context. No request IDs, claims,
credentials, key material, SQL text/parameters, paths, provider URLs, response
contents or exception values are collected. There are no response headers.

Phases: `workload_agent`, `workload_capture`, `clerk_auth`, `entra_jwks`,
`policy_lookup`, `policy_exchange`, `policy_mount`, `policy_binding`, `operation`
and `downstream`. Each has duration, call count and failure count. Nested totals
overlap and must not be added. `downstream` counts existing client invocations
inside the unchanged deadline wrapper; it does not claim HTTP retry counts.
`operation` includes both Core and remote work. SQL cursor duration/count/error
hooks exclude pool acquisition, pre-ping, raw cursor deadline SET statements,
fetch/materialization and commit. The route total excludes outer middleware
and final public-send time in the main app. These are attribution limits, not
claims of complete CPU/SQL/network tracing.

The capture boundary deliberately starts a fresh Context. Standalone routing
passes only the numeric ledger through a private ASGI scope and rebinds it in
the owned worker; no identity or authorization ContextVar is copied. Main
middleware starts the ledger inside that worker. A lock protects sync/async
updates. Closing freezes the ledger and late child callbacks are ignored.
Unexpected timing or logging failures cannot mask source failures.

## Delivery and recovery

1. Merge only the tested, reviewed immutable source. Build/deploy separately
   through the supported staging controller, preserving workload config and
   immutable recovery image. Current Core30 remains unchanged by this PR.
2. Keep the flag absent for ordinary delivery. Enabling it is a separately
   reviewed staging configuration action for a bounded synthetic run. Never
   enable production or broaden users/data to obtain measurements.
3. Root operator collects only matching closed JSON lines; discard unrelated
   log lines in memory. Compare exact native requests before recording audio.
4. Stop the observation by restoring the prior flag value; the code may remain
   dormant. If the code itself requires recovery, return the web image to its
   exact retained prior digest through the reviewed image-only controller.
   No schema migration, data cleanup, queue reset or identity operation applies.

Validation includes disabled behavior, actual native auth dependency thread
transfer, standalone/outer boundaries, no inherited identity context,
cancellation and late updates, JSON privacy, actual SQL errors, original auth
ordering, actual client counts, and PyJWK same-key/unknown-key/cache behavior.
Tests are offline and synthetic; they do not establish physical <4s acceptance.
