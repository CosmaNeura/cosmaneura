# Recall and Zoom webhook inbox runbook

This runbook covers the Phase 1 durable-ingress cutover. It is intentionally
not a production-readiness declaration. Recall bot/recording-to-tenant bindings
remain in Redis with a 24-hour TTL; replacing them with durable,
server-authoritative bindings is a Phase 2 release gate.

## Verified Recall workspace contract

- Workspace: **CosmaNeura — US West (Oregon)**, API v1.11.
- API base: `https://us-west-2.recall.ai/api/v1`.
- Existing callback: `https://app.cosmaneura.com/external/v1/webhooks/recall`.
- A signing secret exists in the Recall dashboard. Treat it as endpoint-scoped
  for legacy workspaces; its value must never enter source, logs, plans, or
  non-API workloads.
- Current subscriptions are `recording.done`, `transcript.done`,
  `transcript.failed`, and `transcript.processing` only.
- Recall exposes `sdk_upload.complete`, `sdk_upload.failed`,
  `sdk_upload.recording_ended`, `sdk_upload.recording_started`, and
  `sdk_upload.uploading`, but this workspace is **not subscribed** to them.
  Desktop SDK rollout must add and validate those subscriptions separately.
  The API explicitly rejects every unimplemented Recall event, including all
  `sdk_upload.*` events, so a dashboard subscription cannot silently activate
  desktop capture or fall back to the Zoom bot appointment heuristic.

Do not copy the generic Desktop SDK example's `us-east-1` base URL into this
environment. The configured and tested region is `us-west-2`.

## Security and durability invariants

- The API verifies the Recall/Svix signature against the exact bounded raw body
  before decoding JSON. The timestamp window is five minutes and rotation
  signatures are accepted.
- Zoom also fails closed when its signing secret is missing and uses the same
  256 KiB streaming body limit.
- Only the API receives `RECALL_WEBHOOK_SECRET`. Worker, scheduler, and
  migration workloads must have neither the environment entry nor the Azure
  secret object. The production deploy live guard checks names and references
  without querying values.
- Pull-request workflows receive no production/vault/Recall/Azure credentials
  and no OIDC permission. Infrastructure PR checks use a backend-disabled,
  provider-mocked plan; the real production-state plan is a separate,
  manual-only workflow on `master`, protected by the production environment.
- A verified, projected payload is Fernet-encrypted in PostgreSQL and committed
  before broker publication. Headers, signatures, callback URLs, provider
  delivery IDs, and full provider responses are not stored.
- Raw provider delivery/resource IDs are transformed with keyed,
  context-separated HMAC-SHA-256 pseudonyms. Full pseudonyms back indexes and
  receipts; logs contain only shortened pseudonyms, bounded state, counts, and
  error class names. The minimum resource IDs needed to finish a handler exist
  only inside the short-lived encrypted payload. The versioned key ring keeps
  the active Fernet encryption key separate from the stable pseudonym root so
  ciphertext keys can rotate without breaking correlation or deduplication;
  retained encryption keys preserve reads during the six-hour payload window.
  Bare-key configuration deliberately continues writing legacy Fernet tokens
  during the code rollout so old replicas can still read refreshed Zoom OAuth
  credentials. After all replicas understand the versioned format, migrate
  `ENCRYPTION_KEY` to the JSON key ring while retaining the existing Fernet key
  and the compatibility-derived pseudonym root; never generate a replacement
  pseudonym root after receipts exist.
- HTTP access logs render every Recall ZAK callback as
  `/external/v1/recall/zak/{token}`. All access logs use developer-defined
  route templates and omit query strings and client addresses; unmatched
  routes are labeled `unmatched`. Zoom client logs contain only hashed provider
  references and bounded operation/status fields, never provider paths,
  response bodies, or raw doctor/meeting IDs. ZAK callback bearers expire after
  five minutes and are consumed atomically on their first request.
- Sentry drops request targets, unmatched/custom transaction names, span
  descriptions/data/tags, exception text, locals, and identity context before
  transmission. Celery publishes fixed
  redacted argument representations, has no result backend, emits no task
  events, and reduces framework logs to fixed metadata. No web-based Celery
  administration surface is deployed. Application-owned task lifecycle logs
  contain only a hashed task reference, task name, state, retry count, and
  exception class. Worker `conf`/`report` inspection from an approved internal
  operator context returns fixed privacy-mode metadata because extension
  configuration can contain URLs that Celery's built-in censor does not
  recognize; ping, safe task inspection, and revoke remain available there.
- Payload ciphertext is deleted immediately after success and no later than six
  hours after receipt. Expired work is dead-lettered without payload.
- Recall Create Bot keeps `recording_config: null` for manual control. POST
  Start Recording receives exactly `{"retention":{"type":"timed","hours":6}}`.
- Zoom bot pause and release transitions are serialized per meeting. A durable,
  encrypted release intent owns the raw bot/meeting identifiers; the delayed
  merge-window task carries only that internal delivery ID. A failed provider
  stop retains the active bot state and does not arm expiry; a failed leave
  retains the bot handle and raises for health visibility. Redis loss cannot
  erase the release command, and neither `meeting.ended` nor merge-window
  expiry may clear ownership until Recall confirms the leave and any requested
  dashboard status update succeeds.
- Participant membership uses occurrence-scoped HMAC references and signed
  Zoom `event_ts` monotonic ordering. A participant callback cannot establish a
  new occurrence ahead of authoritative `meeting.started`.

## Two-release cutover

`DURABLE_WEBHOOK_INBOX_ENABLED` is source-locked to `false` in the plan, apply,
and deployment workflows. Secret presence never implicitly activates the new
path. A protected environment variable with any non-empty value other than
`false` aborts apply/deploy; it cannot pre-arm activation outside review.

### Release A: compatible code, migration, and recovery

1. Keep `DURABLE_WEBHOOK_INBOX_ENABLED=false`.
2. Apply the PostgreSQL migration.
3. Before applying staging infrastructure, check whether
   `cosmaneura-staging-scheduler` already exists outside state. Import it as
   `azurerm_container_app.scheduler` (or create it through a reviewed plan);
   never apply into a same-name unmanaged resource. Then deploy API, worker,
   and the single-replica scheduler. Staging deployment stops before migrations
   if any required workload or the checked-in Procfile scheduler command is
   absent.
4. For production application deployment, run migrations first; update and
   verify the worker and scheduler before exposing the API revision. Stop the
   rollout at the first failed verification. Confirm no Flower/Job Monitor
   resource or deployment path is introduced.
5. Verify the production API-only secret live guard. It must report `verified`;
   it must not print or fetch a secret value.
6. Verify the scheduler is running the checked-in `scheduler` Procfile command
   and that the 30-second `external_webhook_inbox_reconcile` beat entry fires.
7. From an approved internal operator context, inspect Celery
   active/reserved/scheduled task names and states and wait until legacy
   `app.jobs.webhooks.perform` work is empty. Arguments, results, and exception
   details remain deliberately unavailable. Do not purge Redis queues. The
   compatibility consumer converts any already-queued legacy payload into the
   PostgreSQL inbox before handling it.
8. After every old worker revision is gone, inventory and
   remove only legacy Celery result keys (`celery-task-meta-*` and
   `celery-taskset-meta-*`) under an approved change record. Never use
   `FLUSHDB`, and do not touch queue, unacked, redbeat, session, or application
   keys. Run one non-PHI probe task and confirm that no result key reappears.
9. Verify `GET /internal/v1/admin/webhook-inbox/health` as an authenticated
   admin. Pending/retry age and dead-letter/uncertain counts must remain zero.

Legacy Recall tasks generally used a payload-derived event ID, while new
deliveries use the signed webhook ID. Their inbox rows therefore cannot always
converge. The create-transcript and canonical-ingest operation receipts are the
side-effect fence during this rolling-deploy window.

### Release B: activation

1. Confirm Release A workers and scheduler are healthy and the legacy queue is
   drained.
2. Open a separate activation PR that changes the checked-in plan, apply, and
   deployment workflow bindings together from `false` to `true` and updates the
   pre-arm guards. Review the secretless static plan before merging, then
   inspect the authoritative protected apply plan/result before allowing the
   held application deployment. Changing a protected environment variable
   alone is intentionally rejected and must never activate only one workload.
3. Send provider-owned non-PHI validation events. Confirm HTTP 200, one inbox
   receipt per delivery, payload purge after success, and no raw identifier or
   body in application/Sentry/Celery logs.
4. Do not add Desktop SDK webhook subscriptions during this cutover. They are a
   separate, reviewed change after the desktop upload adapter exists.
5. Keep Zoom clinical capture disabled until activation gate D2 supplies each
   `ZoomMeeting` with an exact appointment plus an unexpired occurrence digest.
   This runtime deliberately contains no producer for that authorization and
   fails closed when it is absent, mismatched, expired, or already consumed.

When the flag is false, ordinary verified callbacks return HTTP 200 after
enqueueing the hardened compatibility bridge with a keyed pseudonymous event
reference and no request headers. The compatibility consumer converges that
work into the encrypted PostgreSQL inbox before invoking a handler. Zoom URL
validation remains available. This is the Release A migration-safety path, not
the durable-ingress activation state.

## Recovery and alerts

The scheduler republishes due internal delivery TypeIDs, reclaims expired
leases using an attempt fence, purges expired ciphertext, and recovers pending
downstream fan-out. Broker publication is at-least-once; the canonical ingest
receipt and the existing downstream uniqueness/latest-transcript guards absorb
duplicate jobs. A health alert is emitted when there is any dead letter, any
ambiguous or failed provider mutation, or actionable work older than five
minutes.

Admin endpoints return metadata only:

- `GET /internal/v1/admin/webhook-inbox/health`
- `POST /internal/v1/admin/webhook-inbox/{delivery_id}/redrive`

Redrive is allowed only for a dead letter whose encrypted payload is still
present and unexpired. Never paste payloads, signatures, provider IDs, meeting
URLs, or exception text into an incident ticket. Use the internal delivery
TypeID and bounded error code.

## Stop/rollback conditions

After durable ingress or authorized capture has accepted traffic, a database
downgrade is **not** a rollback mechanism. Never downgrade revisions
`dc296f605553` (durable webhook inbox), `a81c9d7e4f20` (visit-note uniqueness),
or `e4b9a63f210d` (Zoom capture authorization) as an incident response. First
disable ingress and new capture at their source and through the reviewed
feature flags, stop new mutations, drain in-flight workers without purging
queues, and preserve or back up the inbox, receipts, authorization records,
and related audit evidence. A source/flag rollback must retain the migrated
tables, constraints, and evidence needed for replay, reconciliation, and
forensic review.

Deploy the reviewed source rollback that returns the feature flag to `false`
across every workload, and investigate if any of these occur:

- scheduler missing or its Procfile command differs;
- oldest actionable age exceeds five minutes;
- dead-letter or uncertain-operation count is nonzero;
- API-only secret guard fails;
- raw provider identifiers or clinical content appear in logs;
- migration graph/lint, signature vectors, encryption roundtrip, crash-boundary,
  lease-fencing, replay, or retention tests fail.

Do not declare the boundary ready for active patient traffic until durable
provider-resource/tenant and exact encounter bindings (with no time-window
appointment fallback), deployment of the explicit versioned key-ring secret,
disposable-PostgreSQL race tests in CI, provider reconciliation for ambiguous
operations, and end-to-end staging validation are complete.
