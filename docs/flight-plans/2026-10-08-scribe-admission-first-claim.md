# Scribe admission first-claim transaction

Risk: Tier 2 because the refactor executes current capture authorization.
Owner authority: the all-twelve Scribe repair goal, approved backend latency
release, and September 25 local validation/implementer review merge policy.

Admission currently commits preparation, immediately reauthorizes in another
transaction, and claims the first downstream consent/session call. Combine
preparation and that first claim in one uninterrupted local transaction. Preserve
the original input validation, complete capture-owner/token/view-as/lineage and
record checks, original row lock order, immutable journal and request keys, lease
exclusion, and actual commit before external I/O. Keep separate fresh authority
before the second downstream call and before returning admitted readiness.
Ready resumption still replays the original session key to check remote consent.
Receipt preservation after access loss grants no readiness or subsequent action.

Validate using the exact marked synthetic local PostgreSQL lab. Exercise fresh
and resumed legacy/configured sessions, committed lease before network I/O,
competing claims, failed/cancelled atomic preparation, lost responses, revocation
during both remote phases, current scope denial, deadline cancellation, actual
native routes and recording-window/recovery custody. Compare real SQL statement
counts with the original split sequence. Local profiling timings cannot establish
installed desktop or phone seconds saved.

Merge only the locally tested and implementer-reviewed commit with durable PR
evidence. Deliver only its exact merged source through the existing source-bound
staging build controller, publisher and image-only adapter. Verify packaged
qualification, unchanged configuration/secrets/schema and retained recovery
revisions. Retest installed preparation on fictional visits; agent audio remains
virtual-only. Production, configuration, grants, cohort, schema, migrations and
billing behavior changes remain outside scope.

Recovery: restore the preceding immutable staging image through the supported
adapter or revert this patch. Existing journal/lease formats remain compatible;
an interrupted operation retains its original keys and bounded lease recovery.
