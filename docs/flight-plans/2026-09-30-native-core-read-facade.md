# Native Core read facade

Risk: Tier 2. Owner: Scribe operational improvement workstream. Core dependency first.

Native opening currently pays nine Core HTTP round trips per phase. Add a closed
`POST /service/v1/agent/scribe/native-capture` contract that composes existing
lookup, context exchange, mount resolution, binding, and read gateway functions.
Clerk stays in Authorization; the separate workload header must pass both existing
Agent API and Scribe capture authenticators. No authority or bearer is returned.

Only mount and creator_review, provider_encounter, capabilities, and
native_recording_context are allowed. All other routes retain their contracts.
Every read still checks current Core identity, tenant, record, binding and consent,
including gateway authorization after downstream I/O. Capabilities are never cached.

Register beside the agent router: its request-wide session must not be inherited.
Lookup transactions finish individually. Guard plus exchange use one short shared
transaction to preserve Practice-before-Doctor locks; resolution and binding close
before any downstream await. The owned capture deadline covers the full route.

The receiver samples its registry before workload acquisition, before dispatch,
and before publication. It cannot sample between the facade's internal phases.
Therefore mutations, consent, key binding, leases, upload, completion, notes, and
source-start remain on the old transport. Mount retains existing context/lineage/
binding metadata writes; local revocation can precede those metadata writes, but
cannot publish a read or start capture. No claim that final denial rolls back work.

Validate real router/authentication, closed bodies, cancellation, replayed scope,
and owned PostgreSQL transaction/lock release and committed revocation. Run existing
exchange, view-as, capture, SessionPrep and deadline regressions; record skips.

Deploy merged Core first, qualify both auth policies and existing endpoints, then
the dependent receiver. Roll back receiver first; additive Core can remain dormant.
No schema, data migration, infrastructure, or production promotion in this change.
Local model: 27 opening Core calls become 3; device timing remains a separate gate.
