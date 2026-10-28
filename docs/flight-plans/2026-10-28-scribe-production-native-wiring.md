# Production Scribe native wiring — dark Azure preparation

Tier 2: this changes production Container Apps configuration and prepares future
patient-recording and Zoom authority cutovers. This source change performs no
Azure operation, deployment, migration, backfill, feature activation, account
change or cohort expansion. Production promotion remains a founder-only manual
action after independent final-head review and the gates below.

## Outcome and boundaries

- Add one source-controlled production rollout object. Its eleven runtime
  settings are explicitly disabled/empty by default.
- Project native device/encounter settings only to the Core API. Project the
  durable Zoom binding flag identically to API, worker and scheduler, matching
  the drained cutover in `docs/ZOOM_DURABLE_CAPTURE_BINDING_FLIGHT_PLAN.md`.
- Refuse a native-capture plan unless the separately reviewed Scribe workflow is
  enabled and at least one exact HTTPS upload origin is present. Refuse native
  subfeatures when the base capture gate is off. Refuse paths, queries,
  fragments, credentials, wildcards, whitespace and non-443 ports in the
  upload allowlist.
- Prevent generic 1Password-backed public variables from shadowing the
  source-controlled settings.
- Explicit non-goals: provisioning or activating a receiver, changing Clerk or
  Entra authentication, changing sessions/patients, editing application routes,
  changing schema/data, running production, or enabling the legacy handoff.
- Affected services if later applied: production Core API, worker and scheduler.
  Audio and clinical text remain PHI; this source-only preparation handles none.

## Risk and dependencies

- Tier: **Tier 2**, because a later apply changes production runtime revisions
  and the flags govern clinical capture and durable encounter authority.
- There is no irreversible action in this change. Applying even false settings
  creates Container App revisions, so a dark apply still requires a reviewed
  production plan and normal health hold.
- Required dependency order:
  1. Provision and qualify a production Scribe workflow receiver/API in Azure,
     dark and private, receiver before sender. Hold it safely with no Core
     caller and no cohort.
  2. Independently review and merge this default-off Core wiring. It can remain
     indefinitely dark.
  3. Rehearse the exact additive migration range and deploy one reviewed Core
     candidate with every new flag false.
  4. Qualify Core-native encounter creation separately; it does not require the
     Scribe workflow.
  5. Drain existing bots, migrate, and qualify the durable Zoom flag in staging
     before a production canary.
  6. Only after the receiver path passes end to end, activate native capture for
     a bounded production cohort. Enable notes and optional source/recovery/long
     visit settings in later holds, not in the initial capture cutover.
- The Core client contract is `app/services/scribe_workflow.py`; despite the
  “external” name, the receiver may be Azure-native and private. The current
  Core route returns a disabled client while `SCRIBE_WORKFLOW_ENABLED=false`.
  Native session, consent, operation, upload, status and transcript calls cannot
  complete without that receiver contract.

## Current deployed state

- Read-only evidence captured 2026-10-28 in
  `/private/tmp/scribe-production-readonly-review-20261028-01` observed resource
  group `cosmaneura-prod` and Core API/worker/scheduler image tag
  `7953980159bd6ce69cfbd75fe5bc5d4aee83de4e`, registry digest
  `sha256:31cf0a2b52f95da8c9e9c1e244d5e76cc84da9f051b914579e94687e115347b1`.
- The prepared source base is protected `master`
  `1abb9f057feade5f3303f9c2c464be3406742f17`. It is newer than the deployed
  image and includes the additive durable Zoom binding migration
  `2026_10_28_3d40e4e90e05_add_durable_zoom_capture_binding.py`.
- Production explicitly has `SCRIBE_WORKFLOW_ENABLED=false`,
  `SCRIBE_HANDOFF_ENABLED=false`, `DURABLE_WEBHOOK_INBOX_ENABLED=false` and
  `ZOOM_WATCHDOG_ENABLED=false`. Native capture/encounter/durable-Zoom variables
  and the upload allowlist were absent, so application defaults were false/empty.
- Production has the Core PostgreSQL, Redis, uploads storage, model/DeID
  dependencies and an attached Scribe managed identity. No separately
  provisioned production Scribe workflow receiver/API was observed. The owner
  reports no outside-Azure receiver.
- Staging has separately named Scribe API/native receiver workloads. Their
  existence is architecture evidence only; staging identities, endpoints,
  credentials and storage must never be copied to production.
- Reconciliation decision: **replace**, only through an exact reviewed candidate
  after the migration and runtime gates below. Do not assimilate staging drift or
  assume current `master` is the deployed artifact. Preserve the four explicit
  production false flags above.
- Previous known-good artifact: the exact production tag/digest above. The
  additive Zoom table may remain after image recovery; no destructive downgrade
  is planned. The actual production schema head was not queried by the read-only
  review and must be captured before promotion.

## Feature flags and release control

All values below are owned by the production rollout object and default to false
or empty:

| Runtime setting | Initial owner/cohort | Initial value |
|---|---|---|
| `SCRIBE_CAPTURE_ENABLED` | founder; no cohort | `false` |
| `SCRIBE_CAPTURE_UPLOAD_ORIGINS` | founder; no cohort | empty |
| `SCRIBE_CAPTURE_NOTES_ENABLED` | founder; no cohort | `false` |
| `SCRIBE_ENCOUNTER_PREP_ENABLED` | founder; no cohort | `false` |
| `SCRIBE_COMBINED_SESSION_PREPARATION_ENABLED` | founder; no cohort | `false` |
| `SCRIBE_CAPTURE_LONG_VISITS_ENABLED` | founder; no cohort | `false` |
| `SCRIBE_CAPTURE_WORKING_TRANSCRIPTS_ENABLED` | founder; no cohort | `false` |
| `SCRIBE_CAPTURE_RECOVERY_ENABLED` | founder; no cohort | `false` |
| `SCRIBE_CAPTURE_SOURCE_ENABLED` | founder; no cohort | `false` |
| `SCRIBE_ENCOUNTER_CREATION_ENABLED` | founder; no cohort | `false` |
| `ZOOM_DURABLE_CAPTURE_BINDINGS_ENABLED` | founder; no cohort | `false` |

`SCRIBE_WORKFLOW_ENABLED` and `SCRIBE_HANDOFF_ENABLED` remain outside this
rollout and false. A later receiver PR must name its exact Azure resource,
private HTTPS origin, Entra audience/scope, managed-identity grant, artifact
digest, upload host and recovery procedure before proposing workflow activation.

Remove the temporary rollout object only after every enabled behavior has a
stable operational owner, alerting, recovery rehearsal and a separately reviewed
cleanup PR. There is no automatic expiry that may silently enable behavior.

## Schema/data sequence

| Change | Rung | Invariant | Recovery |
|---|---|---|---|
| Durable Zoom binding table already in `master` | expand, before compatible image | empty additive table; no authority inferred or backfilled | retain table and disable flag |
| This environment wiring | dark runtime | all eleven settings false/empty | reapply false values; retain schema |
| Durable Zoom cutover | activation after drain | API/worker/scheduler use one flag value and one compatible image | stop new authorizations, drain, then switch all three off |
| Native capture cutover | activation after receiver | exact receiver, identity, consent and upload origin all verified | stop new admissions, drain sessions, switch capture and subfeatures off |

Before production, rehearse the exact deployed-schema-to-candidate migration on
synthetic/non-PHI data and record schema heads, row counts, duration, locks and
rollback boundaries. This PR adds no migration or data operation.

## Safest Azure-native receiver path

1. Promote the already exercised Scribe workflow implementation as dedicated
   production Azure Container Apps, rather than adding workflow behavior inside
   the Core web process under deadline pressure. Use immutable digest-pinned API
   and receiver artifacts in a production registry.
2. Give the workflow API private/internal ingress. Admit only the existing Core
   Scribe user-assigned identity through a dedicated Entra app role/audience and
   `/.default` scope. Do not add a shared secret or public unauthenticated route.
3. Use a dedicated production storage/container and retention policy for capture
   audio. Add only the exact HTTPS origin returned in signed upload receipts to
   `SCRIBE_CAPTURE_UPLOAD_ORIGINS`; never use `*`, a container path, a SAS URL or
   the Core web origin as a substitute.
4. Keep the native device receiver independently authenticated by current Clerk
   session plus the existing Core workload boundary. Keep
   `SCRIBE_HANDOFF_ENABLED=false`; it is a separate legacy transcript-admission
   path, not a prerequisite for the native client facade.
5. Qualify receiver health, identity denial, consent denial, one synthetic
   upload/transcript/note, retention, restart and rollback before changing Core's
   workflow gate. Receiver-first allows safe holds without exposing a sender.

This is the smallest path consistent with the current contract. A future fully
in-process Core workflow would require replacing `ScribeWorkflowClient` and its
session/upload persistence contract, which is a separate architecture change and
is not a safe same-day wiring assumption.

## Rollout and promotion

1. Independent final-head review; provider-mocked plans; exact non-secret diff.
2. Capture a fresh read-only production inventory, schema head, active bot/session
   count, exact image tags/digests and environment-name/value hashes. Resolve all
   drift before any apply.
3. Produce a saved production plan. It must show only the intended environment
   projection/revisions for the dark rung; no identity, ingress, secret, storage,
   database or unrelated resource replacement.
4. Founder manually applies the dark rung. Hold API, worker and scheduler until
   ready revisions, health probes, aggregate error rate and queue depth match the
   pre-change baseline. No user journey is expected to change.
5. Separately apply the additive migration and compatible image with flags off,
   in the repository's reviewed order. Verify exact source/image/schema readback.
6. Run one approved synthetic canary for each independently enabled feature.
   Record native Record-to-first-PCM and ready latency, save/reopen/playback,
   exact dashboard receipt, Zoom join/capture/processing timing, durable binding,
   note grounding and terminal cleanup.
7. Hold after each gate. Any unconfirmed consent/authority, duplicate bot,
   unexpected revision, wrong upload host, missing note, playback failure, worker
   backlog or aggregate error regression aborts the rung.

## Security and recovery

- No staging secret, identity, endpoint, account or synthetic credential may be
  promoted. No secret value belongs in plan or review output.
- Behavior recovery: stop new admissions/authorizations, drain in-flight native
  sessions and bots, then set the affected gates false. Do not flip the durable
  Zoom reader while old-path bots remain active.
- Image recovery: restore the exact previous digest only after confirming its
  compatibility with the additive schema. Keep the additive table and evidence.
- Infrastructure recovery: retain a dark receiver for the observation window;
  remove identities/ingress/storage only through a later reviewed teardown.
- Data recovery: do not delete, rewrite or backfill capture authority to recover.
  Ambiguous outcomes remain closed and require operator review.

## Completion evidence

- Independent review of the final source and flight plan.
- `tofu fmt -check -recursive`, `tofu validate`, and every provider-mocked plan
  pass using the lockfile without a production backend.
- Fresh saved production plan/readback proves only intended dark settings and no
  drift replacement.
- Receiver resources, immutable artifacts, managed-identity authorization,
  exact upload origin and retention have separate reviewed evidence.
- Current-source staging and production synthetic canaries pass their named gates.
- The founder's manual promotion record names candidate SHA/digest, schema head,
  configuration digest, cohort, timestamps, results and recovery owner.
