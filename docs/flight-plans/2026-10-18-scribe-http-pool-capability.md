# Independent Scribe HTTP transport capability

Tier 2 · Owner: John · Staging candidate; performance unmeasured.

## Scope

The current configured workflow always installs the parent-loop HTTP pool.
This change makes that transport an explicit, default-off capability through
`SCRIBE_WORKFLOW_HTTP_POOL_ENABLED`. SDK service-identity reuse remains governed
by its existing independent selector. With pooling off, the workflow factory
uses its existing worker-owned HTTPX client and can still borrow the reviewed
parent-loop Azure SDK owner. Official `get_token` is called for each logical
service request; caching and refresh remain vendor-owned. The existing
authenticated catalog/list service-only warmup remains independent of transport.

Only workflow configuration and server registration change at runtime. This
selector is a deployment transport capability, not a rollback for security,
schema or identity contracts. Human Clerk fresh-token policy, current provider,
tenant, patient, encounter, consent, source and committed-receipt checks, all
authority lifetimes, SQL ownership, cancellation and shutdown fences stay intact.
No audio, admission or permission work is moved to app opening. There are no new
dependencies, endpoints, schemas or migrations.

## Evidence and validation

Pooling and SDK reuse have not been established as the cause of observed
admission failures. The actual restored 602 image has also shown variable
latency. Its latest 1.683–1.773 second Mac native-ready trial followed a
4.953–5.187 second consent-to-Record gap and proves the prepared path only.
No speedup or immediate-under-five result follows from this change.

Execute the actual server registration expressions against synthetic ASGI
lifespans without importing its external bootstrap. Check absent/false/true and
malformed configuration, disabled/incomplete workflow, independent identity and
HTTP combinations, no eager token acquisition, and service-only warmup with no
pool. Exercise real private capture routes through both transports, fresh token
calls, worker/context isolation, deadline cleanup and closed-owner refusal.
Retain the official SDK cache/refresh, warmup, legacy HTTP contract, security and
deadline suites. All host tests use existing local dependencies and synthetic
transports, with the isolated authority conftest; PostgreSQL integration skips
remain explicit.

## Artifact and promotion

Root owns Git, final-push independent review, qualification and staging actions.
Build a new immutable candidate directly on the proven 602 image, not the SDK
275 or unqualified warmup 0fcc artifact. Bind its cumulative runtime overlay to
the exact reviewed merge and all predecessor source proofs. Audit every changed
runtime file, preserved installed module/import and dependency, full base Config
except revision label, and original RootFS layers plus one closed source layer.
Use the authoritative deduplicated warm/SDK/pool/timing/deadline/gate test union
and existing dependency-only driver; require actual network-disabled Linux
execution and exact owned cleanup before publication. A previous qualified
artifact or host test result is not a newly executed Linux result.

Keep the HTTP capability absent/default false and preserve the intended SDK
selector. Verify the existing web image-only scope, account/identity/env/resources/
scale, literal migration false and six neighboring workloads. Retain exact old602
for recovery. Founder-approved staging rollout does not permit production,
credential changes, new grants or unmerged source. Preserve any failed output and
refuse ambiguous mutation retries. Retest prepared and immediate-consent physical
paths separately with native input/stop proof and valid clock bounds.
