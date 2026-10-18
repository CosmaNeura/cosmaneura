# ADR: server-only fresh consented admission composition

Status: proposed; Tier 2; no deployment or physical performance claim.

The existing long-idle path enters Core twice: server-only preparation context,
then prepared admission with optional preparation metadata. The second entry
repeats native-route authentication and transport while all underlying Core
policy and remote consent/session work must remain current.

Decision: add one closed fresh_admission action to the receiving Core native
route. Issue/resolve a new private context through existing exchange/mount, then
call one shared prepared implementation. Keep SameProviderCaller and all
admission/metadata service calls, SQL lock order, post-await revalidation,
_claim/_accept/_finish and cancellation/deadline journal custody unchanged.
Return the private context only to the authenticated receiver, which strips it
before device publication and preserves original monotonic epoch/single use.

A successful current mount advertises a strict singleton capability. Protocol
metadata may survive 30–60-second setup, but is never authority and never
extends context/JWT/lease TTLs. Scope is exact verified account/session/revision/
workspace/binding/origin. Bound state, generation invalidation and failure
quarantine prevent an uncertain new mutation from becoming hidden legacy retry.
The unchanged old path is selected only before new dispatch when unsupported.

Rejected: device-visible Core context; contextless generic CaptureCaller;
patient/consent claims cached from app opening; a transaction held over remote
I/O; extending 15/25/30-second or native 5-second/under-1-second limits; fallback
following a possibly mutating request; periodic background authorization.

The physical delay removed is hypothesized intermediary transport/auth entry,
not the entire preparation_context phase. Exchange/mount and fresh policy still
execute. Separate source/window combination needs its own source/lease/key
review and is outside this decision.

Architecture consulted: cosmaneura-architecture/ARCHITECTURE.md (server-resolved
record context, verified identity boundaries and operational consent gate).
The local archive lacks adr/README.md; this is disclosed in input-proof rather
than treating historical notes as current policy. No code moves between repos.

Required evidence: independent final-push review of each source, real-PG journal
and revocation/deadline cases, strict receiver mock protocol/custody cases,
qualified immutable artifacts, receiver-before-sender upgrade and sender-drained
before-receiver downgrade, then actual synthetic physical timing. Root retains
all promotion decisions; current package is nonactive.
