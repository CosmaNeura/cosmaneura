# Publish the freshly checked preparation row once

Tier 2. Admission and recording-window context setup ended with two complete
encounter lookups in succession, with no service call or await between them.
Retain the current row checked after the last synchronous service boundary and
use it for the server-only preparation response. This removes one full lookup
transaction from each setup request: admission five to four, window four to
three. Do not convert the existing SQL attribution into desktop seconds.

The initial lookup, exchange guard and issuance, subsequent lookup, mount
resolution and subsequent lookup remain unchanged. Admission still resolves the
binding and checks the current encounter afterward. No authorization result or
transaction spans I/O. Public mounts keep their final enriched lookup, and
ordinary operations keep their fresh post-I/O publication check. Request
validation still forbids operations and opening nonces in preparation requests.
No credentials reach devices; native receiver account checks remain unchanged.

Validate both preparation kinds, including revocation after exchange, mount and
binding boundaries; binding mismatch; real committed revocation; closed policy
transactions; and unchanged ordinary mount/read and cancellation behavior. Run
the real SQL comparison on the task-owned fictional PostgreSQL database, then
the applicable local checks and implementer review of the exact final commit.
Do not merge or release on the strength of offline substitutes alone.

This draft is stacked on merged PR 721, which is not yet deployed. Release only
the reviewed immutable merged source through the existing image-only staging
controller after capacity, source qualification and current workload checks.
Current recovery remains Core 718, digest
`sha256:92bdb2e4c5f1261c0a0a4738aef42da0d57f630f0daff75b1c2ae7909246fa73`.
No production, schema, configuration, grant, identity, dependency or audio
capture change. The 5–7 second consent-to-ready goal remains open until repeated
installed measurements and the authorized virtual audio checks pass.
