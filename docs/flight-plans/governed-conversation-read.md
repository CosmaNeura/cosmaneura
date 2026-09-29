# Governed recent conversation and accepted-turn recovery

Tier 2: additive provider PHI read/acceptance contract. No schema, dependency,
cohort, Scribe, runtime configuration, or infrastructure change.

Exact base: `64422836d3c8cd589b9548ff829cc57c5f850d5a`.

## Deployable outcome

The current authorized encounter can recover up to six recent canonical chat
turns, including accepted-but-pending status. A lost final HTTP response can be
read from canonical storage without reexecuting model/tool work. A separate
server-only tool supplies eligible prior messages to follow-up answers and binds
that memory to canonical final acceptance. Sovereign and host wiring land in
separate receiver-first changes; old callers remain valid.

`contracts/schemas/governed-conversation-v1/README.md` freezes the wire fields,
limits, errors, and source dependency semantics. UI hydration is never model
context. No browser history or patient/record selector crosses either receiver.

## Safety and acceptance

Fresh current authority validates the owning doctor/practice/cohort, exact
record binding, actor, tenant and workload. Historical normal token/session
expiry permits reconnect recovery; explicit revocation does not. Canonical
phase hashes and direct/inherited context references prevent missing evidence
from looking like independent content. Current source validation and bounded
NOWAIT source locks remain in the final transaction through commit. No model
operation runs under a database transaction. Source-busy failure rolls back
before returning a nonretrying 409.

History receipts retain their original snapshot cutoff; a late pending final
cannot enter that snapshot. Original clinical dependency expiry is propagated
through generic follow-ups. Stale answers are omitted from inference and never
returned as assistant display text. This is bounded recent encounter memory,
not longitudinal chart history or an alternate clinical record store.

Validate real routes on owned fictional PostgreSQL: lost-final/pending recovery,
exact encounter/workload denial, fresh-mount recovery and revocation, empty
snapshot and dropped refs, A→B→C source change/expiry/deletion, byte/pair limits,
nonordinary presentations, source contention and transaction cleanup; rerun
existing canonical and PR529 source-fence cases. Migration triggers are outside
the local SQLModel rehearsal; no migration is introduced.

## Rollout and recovery

Deploy reviewed merged Core receiver first, then reviewed Sovereign consumers,
then host/panel wiring. Preserve current runtime settings and separate Scribe
workloads. A receiver deployment alone does not enable browser hydration or
model memory. To recover a failed rollout, restore the previously retained
exact image/revision through the canonical release procedure; do not mutate
canonical turns, source notes, or receipt rows. Code review and local proofs
are not live workflow acceptance; verify selected fictional encounter flows
after the combined release.
