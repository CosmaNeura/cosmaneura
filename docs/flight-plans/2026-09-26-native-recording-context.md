# Native recording context

Tier 2. This additive read supplies the admitted Scribe tenant, session and
workspace for device-only native key custody. Core account tenant references
are a separate namespace and cannot be used for audio authentication data.

Reuse the existing capture admission authorization, Scribe creator/consent
review receiver, and post-response authorization check. Reject legacy sessions,
completed processing states, changed scope and uncertain replies. Return only
namespace identifiers and recording state. The response does not authorize
capture, renew a lease, reserve a chunk, create a key, or mutate any record.

Release Core first, then connect the native key factory and queue owner in
Scribe. Existing clients and existing key custody are unchanged. Native
continuous recording remains disabled until verified renewal, packet ownership,
durable saving and recovery are connected. Recovery of this read is to stop
using it; preserve all prior recordings and keys. No migration or new service.

Validation covers scope selection, configured-session restriction, creator and
consent reauthorization, completed-session rejection, uncertain responses and
existing creator-review behavior. Owner policy uses exact-commit local tests
and implementer review for merge; no independent-review claim.
