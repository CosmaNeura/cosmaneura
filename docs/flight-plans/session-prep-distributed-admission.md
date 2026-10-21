# Session Prep distributed admission

Tier 2: provider authority, canonical state and service boundary. This Core-only
receiver increment is built on merged PR795's fresh expiry/revocation check
after interaction lock waits and PR794's cancellation regression test.

Across ten Sovereign replicas, local provider buckets cannot bound aggregate
model work. Core will admit only the dedicated configured Sovereign workload's
provider `chat_text` user phases: at most ten accepted phases in sixty seconds
and two accepted phases without a final in a 195-second reservation window.
Counts use existing canonical metadata, trusted signed workload identity and
current Core actor authority. This is an immutable reservation, not a renewable
lease or proof that a vendor cancelled/refunded work.

Before authority row locks, a stored-token lookup chooses only a namespaced
actor/workload transaction advisory lock. `pg_try_advisory_xact_lock` never
waits. Then unchanged full authority checks, fresh post-lock expiry/revocation,
canonical payload validation and exact receipt/conflict binding run first.
Only a genuinely new authorized provider chat interprets a busy lock or counts
recent phases. Replays cost no new phase and remain available when a lock is
busy. A revoked context cannot become a rate response. Count and append share
one READ COMMITTED transaction and PostgreSQL `clock_timestamp()` after lock
waits; no transaction-start clock, new table, migration or external limiter.
A collision conservatively asks the user to wait one second. Capacity/burst
responses return fixed content-free 429 metadata and bounded Retry-After.

The final phase still requires every existing source/history/authority fence.
New dedicated provider-chat finals older than 195 seconds fail closed; exact
stored-final replay is checked before that time fence. Public conversation,
provider UI actions, Scribe and companion workloads preserve their current
behavior. Configured staging provider/Scribe/companion identity sets were
pairwise disjoint in a read-only projection; external identity sharing by
CosmaBot is unobserved and is not an additional permission guarantee.

Validate the actual same-provider stored-record path on independently connected
sessions in this task's explicitly bound synthetic PostgreSQL lab: burst and
concurrent reservation races, rollback/no rejected phase, free replay under
contention, conflict binding, revocation winning over rate, final slot release,
clock-based expiry and non-provider receiver isolation. Run focused existing
conversation, capture and post-lock expiry guards. No reset, truncate, schema
operation, external SDK, browser or live identity is part of these tests.

Rollout is receiver first, then the separately reviewed Sovereign sender with a
180-second total deadline (165 work +15 closure) and truthful 429 handling.
Older senders fail closed on 429 before new model work, but cannot establish the
new total deadline; operational deadline acceptance requires both increments.
The parent task owns independent final-head review and staging release. Core
recovery uses the prior immutable image, with existing canonical rows retained;
reverting removes aggregate admission and is not acceptance of the beta limit.
No configuration, dependency, schema, UI, Scribe, public app, workflow or
production deployment change is included. Monitor delivery/global alerts and
Clerk logout remain separate unproven acceptance items.
