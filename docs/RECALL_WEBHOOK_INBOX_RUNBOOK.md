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
- Only the API receives `RECALL_WEBHOOK_SECRET`. Worker, scheduler, Flower, and
  migration workloads must have neither the environment entry nor the Azure
  secret object. The production deploy live guard checks names and references
  without querying values.
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
  `/external/v1/recall/zak/{token}` and redact its query string. Zoom client
  logs contain only hashed provider references and bounded operation/status
  fields, never provider paths, response bodies, or raw doctor/meeting IDs.
- Payload ciphertext is deleted immediately after success and no later than six
  hours after receipt. Expired work is dead-lettered without payload.
- Recall Create Bot keeps `recording_config: null` for manual control. POST
  Start Recording receives exactly `{"retention":{"type":"timed","hours":6}}`.

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
4. Verify the production API-only secret live guard. It must report `verified`;
   it must not print or fetch a secret value.
5. Verify the scheduler is running the checked-in `scheduler` Procfile command
   and that the 30-second `external_webhook_inbox_reconcile` beat entry fires.
6. Inspect Celery active/reserved/scheduled tasks and wait until legacy
   `app.jobs.webhooks.perform` work is empty. Do not purge Redis queues. The
   compatibility consumer converts any already-queued legacy payload into the
   PostgreSQL inbox before handling it.
7. Verify `GET /internal/v1/admin/webhook-inbox/health` as an authenticated
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
   pre-arm guards. Review the resulting Terraform plan before merging, then
   apply and deploy once. Changing a protected environment variable alone is
   intentionally rejected and must never be used to activate only one workload.
3. Send provider-owned non-PHI validation events. Confirm HTTP 200, one inbox
   receipt per delivery, payload purge after success, and no raw identifier or
   body in application/Sentry/Celery logs.
4. Do not add Desktop SDK webhook subscriptions during this cutover. They are a
   separate, reviewed change after the desktop upload adapter exists.

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
