# Non-production backup and restore rehearsal

Status: **PROCEDURE ONLY — NOT YET EXECUTED**
Scope: synthetic data in an explicitly approved non-production Azure environment
Production use: prohibited
Owner-policy revision: 2026-08-28

This procedure produces recovery evidence without touching production. A configured backup window, an Azure “earliest restore date,” or a successful control-plane restore operation is not sufficient on its own. Recovery is proven only when the restored service is isolated, least-privilege access works, forbidden access fails, and the application validates the synthetic fixture within an owner-approved RTO/RPO.

John Clemente, CEO, is the primary incident, Azure/platform, and database/recovery point of contact by role and name only. That designation is not execution approval and does not supply secondary coverage, staffed hours, response targets, or a protected contact mechanism. A rehearsal remains blocked until a separately named operator, observer, cleanup approver, secondary incident contact, and approved RTO/RPO are recorded.

## Safety contract

Stop before any mutation unless every item is true:

- The subscription and resource group are on a written non-production allowlist. The resource group must not be `cosmaneura-prod`.
- The source database is a non-production server containing synthetic data only.
- The target name begins with the approved unique prefix, for example `restore-drill-`, and does not already exist.
- The source and target Azure resource IDs, subscription, tenant, region, and UTC timestamp are copied into the change record and reviewed by a second operator.
- Target networking is isolated. No `0.0.0.0` through `255.255.255.255` rule is allowed.
- A database/recovery operator, observer, cleanup approver, secondary incident escalation contact, and protected contact route are named. John Clemente’s primary POC designation alone does not satisfy this separation.
- The evidence directory is outside the repository, access-restricted, and contains no secret, DSN, token, row value, object key, filename, email, NPI, transcript, note, or request/response body.
- The rehearsal has a maximum lifetime and cleanup window. Cleanup is a separate reviewed action targeted only at the unique drill resource.

If any guard is false or ambiguous, stop. Do not “test carefully” against production.

## Targets and measures

The business owner must supply targets before the run:

| Measure | Approved target | Start | Stop | Measured result |
|---|---|---|---|---|
| RPO: maximum acceptable committed-data loss | UNAPPROVED | Synthetic fixture commit timestamp | Selected restore timestamp / newest validated fixture | NOT MEASURED |
| Infrastructure RTO | UNAPPROVED | Restore command accepted | Restored server reports ready | NOT MEASURED |
| Application recovery RTO | UNAPPROVED | Restore command accepted | Synthetic application workflow passes on restored server | NOT MEASURED |
| Cleanup deadline | UNAPPROVED | Restored server ready | Isolated drill resources removed after evidence acceptance | NOT MEASURED |

Record both wall-clock UTC timestamps and Azure operation timestamps. Do not backfill a target after seeing the result.

## Retention and cleanup boundary

The minimum-retention schedule is an owner proposal, not a deployed control or permission to use real PHI in this rehearsal. The full proposal is in `docs/PUBLIC_PHI_INFRASTRUCTURE_BASELINE.md`. Restoration must not reset the deletion clock of an underlying record or derivative.

- Synthetic drill fixtures and isolated restored drill resources must be deleted within 7 days at most; a shorter run-specific cleanup deadline takes precedence.
- PHI-free operational logs are proposed for 90 days. Accepted minimum-necessary legal/compliance rehearsal evidence is proposed for 7 years, without fixture bodies or source clinical payloads.
- Backup copies are proposed for 30 days. Backup expiry does not prove record-level deletion, and a restored copy must remain subject to the original record schedule.
- Raw audio, video, and provider payloads are not valid restore fixtures. Their proposed rule is deletion after successful transcript/integrity validation with a six-hour hard retry ceiling; identifiable raw transcripts are proposed for 14 days and drafts for 30 days.
- The finalized-clinical-record schedule remains unresolved pending counsel and applicable state requirements. No rehearsal may invent or extend it.

The evidence approver must record the applicable class, planned deletion timestamp, and deletion verification method before the restore begins. A documented legal hold requires separate legal/privacy authority and cannot be inferred from an incident or failed cleanup.

## Phase 0 — preflight and immutable evidence

1. Record the repository commit, runbook revision, Azure CLI/OpenTofu versions, tenant/subscription identifiers, non-production resource group, region, source server resource ID, and operator/observer roles.
2. Read the source server metadata and record only: version, SKU/tier, storage size/autogrow, backup retention days, earliest restore date, public-network setting, HA mode, geo-backup mode, and tags.
3. Read and record firewall/private-endpoint/DNS metadata. Fail preflight if the drill would require an allow-all rule.
4. Read current alerts and diagnostic settings. Confirm that restore failure, dependency health, capacity, and synthetic application failure can be observed without payload content.
5. Record a SHA-256 digest for each redacted evidence file. Do not save CLI responses that contain administrator credentials or connection strings.

Expected preflight result: a second operator can identify the exact non-production source and target without seeing application data or secrets.

## Phase 1 — create the synthetic recovery fixture

1. Generate a unique fixture ID locally, such as a UUID. The fixture must not resemble a patient, clinician, NPI, email, account, or claim.
2. Through the normal non-production application/database path, commit:
   - one synthetic parent record;
   - a deterministic set of related synthetic records;
   - a fixed non-sensitive marker suitable for hashing.
3. Record only the fixture ID, schema/application version, expected row counts, and SHA-256 of the canonical synthetic marker. Do not record row bodies.
4. Record the database-confirmed commit timestamp in UTC. Wait until the chosen point-in-time restore timestamp is later than the commit and within the advertised restore window.
5. Perform a read through the normal runtime identity and record pass/fail, duration, aggregate row counts, and marker hash.

This timestamp is the RPO reference point.

## Phase 2 — point-in-time restore to an isolated target

The operator must first run a read-only identity guard and have the observer compare the returned subscription/resource group/source ID with the approved record. Then use Azure’s PostgreSQL Flexible Server point-in-time restore operation with placeholders similar to:

```text
az postgres flexible-server restore \
  --resource-group <NONPROD_RESOURCE_GROUP> \
  --name <restore-drill-UNIQUE> \
  --source-server <NONPROD_SOURCE_SERVER> \
  --restore-time <UTC_TIMESTAMP_AFTER_SYNTHETIC_COMMIT>
```

The exact command must be generated from the installed Azure CLI help and peer-reviewed immediately before execution. Do not paste a production resource ID into a reusable script or shell history.

1. Record T0 when Azure accepts the restore request.
2. Poll only control-plane provisioning state and dependency metrics; do not query row content while provisioning.
3. Record T1 when the restored server reports `Succeeded` and the intended network configuration is verified.
4. Before application access, confirm there is no allow-all firewall rule, no unintended public endpoint, and no production DNS/traffic route. Apply only the already approved isolated non-production access plan if restore inheritance does not meet those controls.
5. Confirm the restored server is not registered as a production dependency and no production workload can resolve or reach it.

Any unexpected public access, production route, resource replacement, region, SKU, or identity stops the rehearsal and invokes the incident procedure.

## Phase 3 — validate recoverability

Use a restored-server migration identity for schema inspection/migration and a separate runtime identity for application validation. If that separation is not available, mark the rehearsal blocked; do not use the production server-admin identity as proof of least-privilege recovery.

1. Record the restored schema revision. If a forward migration is required, execute the reviewed migration against the drill target and record its duration and exit status.
2. With the runtime identity:
   - verify connection and expected schema access;
   - compare aggregate fixture row counts;
   - recompute and compare the synthetic marker hash;
   - run a minimal synthetic create/read/update workflow;
   - run the dependency health check used by the public-pilot workload.
3. Prove negative access:
   - runtime identity cannot create/drop/alter schema objects or assume the migration/owner role;
   - an unapproved network source cannot connect;
   - a stale/invalid database secret cannot authenticate;
   - restored server is not reachable through a production application route.
4. Generate one approved PHI-free synthetic recovery/application alert. Record evaluation, receiver delivery, acknowledgement, escalation if configured, and resolution timestamps. A portal firing history without receiver proof fails this step.
5. Record T2 when the application validation completes. Calculate:
   - infrastructure RTO = T1 − T0;
   - application RTO = T2 − T0;
   - observed RPO from the fixture commit/restore timestamps and newest validated fixture.

If fixture validation fails, preserve control-plane/aggregate evidence, isolate the target, and invoke incident response. Do not query or export broad tables to debug the drill.

## Phase 4 — blob recovery decision and drill

The observed 10-day lifecycle deletion policy is not backup or restore evidence. Account/versioning, soft-delete, container-delete retention, immutable retention, and cross-region recovery were not proven by the reconciliation.

Before a blob recovery drill, the privacy/data owner must decide whether uploads are reproducible transient inputs, retained records, or backup-covered artifacts. Then:

1. Configure only an approved non-production recovery control through a reviewed plan.
2. Upload a content-free synthetic object whose local bytes and SHA-256 are recorded.
3. Delete or overwrite it according to the selected failure scenario.
4. Recover it using the selected version/soft-delete/backup mechanism.
5. Compare byte count and SHA-256; prove an unapproved identity/network cannot retrieve it.
6. Record measured RTO/RPO and delete the synthetic object after evidence acceptance within the approved cleanup window and no later than 7 days after creation.

Until that drill succeeds, do not claim upload/blob recovery.

## Phase 5 — Redis rebuild/failover decision

Redis must first be classified as cache-only, broker/stateful, or mixed. If authoritative data exists only in Redis, this procedure is insufficient and activation remains blocked pending a durability design.

For a cache-only design, rehearse non-production reconstruction rather than claiming backup restore:

1. Seed a deterministic synthetic source record in PostgreSQL.
2. Populate the corresponding cache with non-sensitive fixture keys and record only counts/TTL distributions.
3. Flush or replace the isolated non-production cache under an approved plan.
4. Rebuild from the authoritative source and verify aggregate counts, TTL policy, application correctness, and elapsed time.
5. Prove public/key-based access is denied after the intended private/identity cutover.

For broker or workflow state, separately prove message redelivery/idempotency, dead-letter handling, and recovery from an interruption. Never use real jobs or user payloads.

## Phase 6 — evidence acceptance and cleanup

1. Reviewer compares observed RTO/RPO with targets set before the run.
2. Security/privacy reviewer confirms the evidence and notification samples are PHI-free.
3. Operations records every failed/partial step, owner, due date, and whether it blocks activation.
4. Evidence approver authorizes cleanup of the exact uniquely named drill resources.
5. A second operator verifies the target resource ID, active connections, DNS/route absence, and evidence completeness before cleanup.
6. Remove only the isolated drill resources under a separate change record, within the approved cleanup window and no later than 7 days after creation. Do not delete shared networking, diagnostics, identities, DNS zones, or source backups as “cleanup.”
7. Perform a read-only post-cleanup inventory and record that the unique target no longer exists.

Failed cleanup does not invalidate restore evidence, but it creates a cost/security incident that must be owned until resolved.

## Evidence template

```text
Rehearsal ID:
Status: NOT RUN | PASS | FAIL | PARTIAL
Runbook commit and SHA-256:
Start/end UTC:
Operator / observer / approver roles:
Primary / secondary incident contacts:
Azure tenant/subscription (non-secret IDs):
Non-production resource group:
Source resource ID:
Unique restored target resource ID:
Region / version / SKU:
Network isolation summary:
Backup retention / earliest restore timestamp:
HA / geo-backup setting:
Synthetic fixture ID:
Fixture commit UTC / selected restore UTC:
Expected and observed aggregate counts:
Expected and observed marker SHA-256:
T0 restore accepted / T1 infrastructure ready / T2 application ready:
Measured infrastructure RTO / application RTO / RPO:
Runtime positive test:
Runtime DDL/role negative test:
Unapproved-network negative test:
Invalid database-secret authentication negative test:
Synthetic alert evaluation / delivery / acknowledgement / resolution timestamps:
Blob recovery result (or approved not-applicable reason):
Redis rebuild/failover result and classification:
Unexpected events and incident IDs:
Evidence file names and SHA-256 digests:
Target comparison and disposition:
Cleanup approval / completion / post-cleanup inventory:
Retention class / deletion due UTC / deletion verification:
Reviewer decisions and signatures/acknowledgements:
```

## Current status

No rehearsal described here has been executed. John Clemente, CEO, is named as the primary point of contact, but secondary coverage, hours, targets, and contact mechanisms are unresolved. RTO and RPO are unapproved and unmeasured. PostgreSQL backup metadata proves that restore points are advertised; it does not prove that a usable application can be recovered. Alert delivery, blob recovery, Redis reconstruction, least-privilege restored access, retention enforcement, and cleanup are also unproved and remain activation blockers.
