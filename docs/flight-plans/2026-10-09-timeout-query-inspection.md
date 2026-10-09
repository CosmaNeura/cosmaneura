# Inspect each executed timeout query once

Tier 2. Recording preparation repeatedly walks the same compiled relational
query within its timeout callbacks. Classify the same statement once in its
actual SQLAlchemy execution context, then retain only that boolean for the
remaining callbacks of that one execution. Every connection, cursor, transaction,
scope, deadline and cancellation check still runs. A different compiled object
or statement is reclassified. Adapters retain individual inspections. No cached
compiled-query permission, result, row or transaction evidence is introduced.

Keep SQL text, queries, original locks, receipts, policy and setter behavior.
Generated relational reads retain the existing conservative classification;
functions, text, literal SQL and writes still reset timeout correspondence.
Qualify real PostgreSQL cancellation, unsafe functions, transaction/savepoint
changes, successive executions of the same compiled query, complete recording
receipts and exact locks. Pair local profiles; local timings include
instrumentation and do not establish the five-second device target.

Release only the merged, exact-source qualified Core staging image to existing
roles under the existing authorized latency release path. Configuration, secrets,
schema, identity, grants, cohorts and Scribe receiver images remain unchanged.
Recovery restores the preceding immutable Core image. Use only fictional visits
and virtual audio. Production and physical recording are outside this plan.
