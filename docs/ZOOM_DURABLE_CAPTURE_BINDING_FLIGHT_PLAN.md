# Durable Zoom capture authority

Tier 2: additive patient/encounter authority and a default-off runtime cutover.
This change does not authorize a new host, cohort, meeting, recording or production
deployment. The existing per-visit authorization and Zoom recording consent remain
required. Source tests and dark deployment are not live acceptance.

The existing bot path stores provider-recording-to-encounter authority only in
24-hour Redis keys. Losing those keys prevents a signed recording/transcript
callback from completing safely. `ZoomCaptureBinding` retains the exact consumed
meeting, doctor, appointment, patient and occurrence in PostgreSQL. Only keyed,
context-separated provider bot/recording digests are stored; provider IDs, URLs,
credentials and clinical text are absent from this table.

The flag `ZOOM_DURABLE_CAPTURE_BINDINGS_ENABLED` defaults to `false`. With it off,
launch, ingestion and terminal-status behavior remain on the existing path. With
it on, a normal authorized launch publishes the bot binding before recording and
adds only the newly confirmed recording. Callback authority comes from that
binding, never from provider metadata or bot-scoped Redis fallback. The adapter
revalidates the original consumed occurrence and unchanged tenant/patient ownership.
Signed terminal status is retained there with denial/failure precedence.

## Expand and cut over in staging

1. Independently review the final immutable source, migration and focused real
   PostgreSQL evidence. Reconcile any intervening Core work before selecting the
   merged candidate and image. Keep the flag off.
2. Apply only the generated additive migration through the supported staging
   migration path. It creates one empty table and its constraints; no existing
   encounter is backfilled and no authorization is inferred from older recordings.
3. Deploy the reviewed compatible source with the flag off, worker/scheduler
   before API. Verify actual source/image, schema head and workload readiness.
4. Drain existing Zoom captures, callback receipts and live bots normally. Do not
   switch a capture that started on the old path to the required-binding reader.
5. Under a separately reviewed staging activation intent, set the flag to `true`
   consistently on the existing staging API, worker and scheduler. Confirm that
   every running revision uses the same reviewed image and flag. Preserve all
   other configuration and the staging-only Recall workspace/endpoint/key.
6. Run one newly approved synthetic meeting through normal account refresh,
   linked-visit authorization, host/participant joins, native recording consent,
   actual synthetic capture and normal meeting end. Verify the exact durable bot
   and recording digests, immutable consumed authority, signed callbacks,
   canonical transcript receipt, current grounded note, dashboard reload and
   cleanup. Record actual join/capture/processing elapsed times. Current R8
   Start/Join approval remains required; do not use a direct bot or callback API.
7. The focused PostgreSQL checks demonstrate correct ingestion/status when Redis
   binding state is unavailable. Do not destroy staging Redis or its shared keys
   to manufacture live proof. Keep that failure-mode evidence separate from the
   actual complete staging meeting qualification.

## Recovery

Stop new authorizations and drain started bots normally before switching the flag
off on all three existing workloads. Retain the additive table and its data;
rollback never drops or rewrites consumed authority. A recording started under
the old path has no durable binding and cannot be adopted by the flag-on reader.
The current flag-on producer still publishes the original Redis compatibility
keys, so a drained coordinated flag rollback can retain existing adapter behavior.
An ambiguous bot/start outcome keeps its original durable teardown obligation;
never create another bot or substitute an older recording to recover.

## Production decision

Production remains a separate founder-only manual promotion. The candidate must
first pass actual current-source staging acceptance and the production environment
gates in `CUSTOMER_RELEASE_READINESS.md`. Prepare an additive expand rung and the
same drained flag cutover for the existing production bot cohort only; do not copy
staging credentials, connect synthetic accounts in production or broaden hosts.
The improved `zoom_source_fidelity_v1` note policy is still staging-only in this
change. Its production activation is separate reviewed work after its live gate.
Generating every active doctor template is existing behavior and is not changed by
this authority cutover. Optional intermediate UI status work is also out of scope.
