# Public-PHI infrastructure and operations baseline

Status: **NOT READY FOR PUBLIC-PHI ACTIVATION**
Evidence snapshot: 2026-08-27 (America/New_York)
Scope: Priority 4 only; Azure production and staging metadata, repository IaC, and operational controls
Data used: redacted resource metadata and synthetic test requirements only

This document is an activation gate, not an authorization to apply infrastructure. The reconciliation that supports it was read-only. No production infrastructure, credential, network rule, deployment, or workload was changed.

## Executive decision

Public-PHI activation remains blocked. The live estate has configured database backups and some encrypted transport and alert evaluation, but it still relies on public data-service endpoints, a production PostgreSQL allow-all firewall rule, server-admin application database access, shared-key storage and Redis access, inline Container Apps secrets, incomplete diagnostics and alerts, and untested recovery. Production PostgreSQL high availability and geo-redundant backup are disabled. Alert receipt, escalation, and restore success have not been demonstrated.

The gate score is **1 of 16 complete (6%)**. The score is intentionally binary: a partial control receives no activation credit. The completed configuration gate is verified PostgreSQL backup configuration. The NPI/CAB owner policy is authoritative but remains partial until the canonical onboarding, security, data-inventory, and retention controls are shown to match it. Backup configuration does **not** count as recovery evidence.

The next safe action is to provide a controlled staging-plan runner with its existing 1Password service-account credential, approve this worktree's reviewed `.envrc`, and regenerate the full staging plan. The provider-mocked module plan is clean, but it does not substitute for a credentialed state refresh. The accepted staging plan must contain no unreviewed delete or replace action and is followed by synthetic alert-delivery and restore rehearsals. Production remains unchanged until their evidence is accepted and the named owners below are assigned.

## Owner policy boundary: NPI, CAB, and claims

- NPI is not required during signup or onboarding.
- NPI is not an authentication factor, identity proof, credential-proof control, or compensating security control.
- CAB is advisory billing intelligence only.
- 837P generation or submission, claim submission, clearinghouse enrollment, and CAB-driven EHR writes are out of scope for the public paid pilot.
- Future NPI collection is allowed only just in time for a specifically enabled payer-facing standard transaction or integration. Before collection, its purpose, lawful/contractual basis, authorized users, storage location, retention, deletion, disclosure path, and integration owner must be documented and approved.
- No infrastructure activation gate may be waived because an NPI was collected or verified.

This policy must remain consistent in the security/control matrix, data inventory, retention schedule, onboarding contract, and any future payer integration design.

Canonical governance is captured in architecture PR [CosmaNeura/cosmaneura-architecture#10](https://github.com/CosmaNeura/cosmaneura-architecture/pull/10), commit `5451df63e2fbeaf386ca5517d762e6d1359b053c`, including ADR 0022 (purpose-bound just-in-time NPI), ADR 0023 (CAB is advisory and does not submit claims), and ADR 0024 (public trial starts at checkout with card). The PR was observed open on 2026-08-27, so merge status remains an activation check; the owner's stated policy applies to this work regardless. No Priority 4 infrastructure adds an NPI onboarding dependency or a CAB 837P, claim-submission, clearinghouse, or EHR-write path.

## Core entitlement coordination handoff

- **Capability and receiver:** Core remains the server-side identity, tenant, credential-state, and entitlement authority. The receiver is open Core PR [#185](https://github.com/CosmaNeura/cosmaneura/pull/185), observed at commit `3bde3ae155423947d870f636ee907135949fafcb`.
- **Canonical contract:** `contracts/public-entitlements/v1/capability-entitlement.schema.json`, version `public-capability-entitlements.v1`, observed SHA-256 `a33ba095ea284575fc8004ecc7b2e05b6aa60c911f5c03add3a0ea1f7ef9e578`. Consumers must pin the accepted Core SHA because the PR remains open.
- **Consumer and boundary:** this infrastructure work consumes only the fail-closed capability decision and does not redefine onboarding, actor, tenant, role, credential review, billing state, or product entitlements. No infrastructure activation bypasses a denied or unavailable Core decision.
- **Actor/tenant and idempotency:** caller-supplied actor or tenant data is never infrastructure authority. The entitlement read remains side-effect free; infrastructure changes use an exact reviewed plan digest/change record rather than an application idempotency key.
- **PHI and schema:** operational evidence is control-plane metadata, fixed event codes, and aggregates only. This work adds no API schema and no NPI, claim, clinical-content, or EHR-write data path.
- **Failure/retry and audit:** an unavailable/unknown entitlement stays denied; retries must not reuse a stale allowed result. Deployment/activation events are recorded by commit, plan digest, environment, fixed resource actions, approval, and rollback state without actor identifiers.
- **Compatibility, flag, and fixtures:** observability is additive and default-off. Synthetic contract fixtures and PHI-free signals are the only permitted acceptance inputs; real-PHI access remains gated separately.
- **Activation order and rollback:** accept and deploy the Core receiver before any dependent public capability, then complete staging infrastructure rungs and evidence. Disable the relevant rollout/observability flag and reverse only the current infrastructure rung on failure; do not weaken the Core decision.
- **Unresolved decisions:** credential-verification method/reviewer/SLA, support ownership, BAA/service scope, retention, RTO/RPO, HA/geo posture, and the remaining network/identity cutovers are still blockers.

## Read-only live reconciliation

The following statements describe observed Azure control-plane metadata. Secrets, connection strings, tokens, object names, query text containing user data, and receiver identities were not collected into this record.

### Deployed posture

| Area | Production | Staging | Activation consequence |
|---|---|---|---|
| PostgreSQL network | Public access enabled; firewall permits Azure services and `0.0.0.0` through `255.255.255.255`; no delegated subnet or private DNS | Public access enabled; firewall permits Azure services plus exact ACA egress `130.131.32.65`; no private endpoint | Production allow-all is an activation blocker. Staging source drift could recreate allow-all. |
| PostgreSQL capacity/recovery | PostgreSQL 16, `B_Standard_B1ms`, 32 GB, autogrow enabled, 30-day backup retention; earliest observed restore point `2026-07-29T12:02:46Z`; HA disabled; geo backup disabled | PostgreSQL 16, `B_Standard_B1ms`, 7-day backup retention; earliest observed restore point `2026-08-22`; HA disabled; geo backup disabled | Backup metadata exists, but no restore-mode target was observed in either scoped resource group and no successful restore, approved RTO/RPO, or launch HA decision exists. |
| PostgreSQL authentication | Password authentication enabled; Microsoft Entra authentication disabled; workloads consume a database secret | Same control family | Application runtime and schema migration are not proven as separate least-privileged identities. |
| Redis | Azure Cache for Redis 6.0 Standard C0; TLS 1.2 minimum; non-TLS port disabled; public network enabled; access-key URL | Same control family | TLS is positive, but public/key-based access and the service migration path remain blockers. |
| Upload storage | TLS 1.2 minimum and HTTPS-only; account allows blob public access and shared-key access; public network enabled; network default action `Allow`; LRS; containers themselves report no public access | Same control family | Private containers do not compensate for the account-level public/shared-key/default-allow posture. |
| Storage retention | Lifecycle deletion after 10 days for `uploads` and `example-notes`; test containers excluded | Same source policy | Test-container retention is undefined. Lifecycle deletion is not a backup. Business-record retention is not approved. |
| Container Apps environment | Public environment, no VNet integration, zone redundancy disabled, logs sent to Log Analytics | Same control family | Private access cannot be enforced before an additive parallel path and synthetic workload proof. |
| Workload identities | App has the Scribe workflow user-assigned identity. Worker has no deployed identity despite source expecting the health-services identity. Scheduler has none. | App, worker, and scheduler have none | Source/deployed identity drift and residual secret authentication block least privilege. |
| Migration job | No managed identity; consumes the database URL and a broad set of unrelated application secrets | Same control family | Dedicated migration identity and secret minimization are not established. |
| Secret management | Container Apps secret references are inline; no observed Key Vault URL/identity references; no production or staging Key Vault resource was reconciled | Same control family | Rotation, revocation, and source/state exposure controls are incomplete. |
| Registry | Basic registry with admin account enabled; Container Apps use registry credentials | Same control family | Managed-identity pull must be planned and proven before admin access is retired. |
| Diagnostics | No Azure Monitor diagnostic setting observed on PostgreSQL, Redis, upload storage, Container Apps environment, app, worker, or scheduler. Container Apps environment logs still flow to Log Analytics through its environment configuration. | No equivalent evidence | Dependency/capacity diagnostics are incomplete. Data/query/object logs must not be enabled merely to close this gap. |
| Alerts | One enabled action group with one email receiver; two scheduled-query alerts for task failures and Zoom sync. The alerts have fired, but notification receipt was not evidenced. No metric or activity-log alerts were observed. | No equivalent alert baseline observed | Firing history proves evaluation only, not delivery or response. Coverage is insufficient. |
| Log retention | Production Log Analytics retention observed at 30 days; no daily cap; public ingestion and query endpoints | Not used as activation evidence | Telemetry retention and endpoint decisions need privacy/operations approval. Alerts must remain PHI-free. |

### Source-to-deployed drift

| Drift | Source state | Deployed state | Risk and required disposition |
|---|---|---|---|
| Staging PostgreSQL firewall | The base revision declared allow-all and omitted the exact ACA egress. This review branch removes allow-all, requires the operator to supply the freshly observed IP, checks it against refreshed scheduler outbound addresses, and uses an import block to adopt the live rule. | Live staging has an exact `aca-staging-egress` rule instead of allow-all | The source fix is not deployed. A credentialed refreshed plan must prove the guard, adoption, and zero replacement before approval. |
| Production worker identity | Source assigns the health-services user-assigned identity | Live worker identity is `None`; expected de-identification auth-mode metadata is absent | Treat the source change as unapplied. Validate a non-production plan and workload before any production proposal. |
| Legacy workload | Current source no longer represents the legacy `app-jmon` Container App | `app-jmon` remains deployed | Determine ownership and traffic before a separately approved retirement. Do not delete it as incidental drift cleanup. |
| Staging workloads | App, worker, and migration job are at least partly managed by imperative deployment scripts rather than the reviewed staging IaC | Resources exist live | Import or otherwise establish ownership before relying on an IaC plan as complete drift evidence. |
| Parallel private path | Source contains disabled-by-default parallel VNet/private endpoint/workload scaffolding | Live environments remain public and non-VNet-integrated | This is intentional dormant source, not deployed hardening. Keep it additive until synthetic acceptance succeeds. |
| Alerts and diagnostics | This review branch adds a default-off phase-one module with metric-only data-service diagnostics, one role-mailbox action group, four aggregate fixed-event log alerts, five dependency/capacity metric alerts, and one PostgreSQL administrative-failure activity alert | Two production log alerts exist; data-service diagnostic settings are absent | The module is not deployed and is not complete alert coverage. Add verified backup-rehearsal freshness, Container Apps restart/capacity, queue age/depth, scheduler heartbeat, database/Redis load, and storage-capacity signals; then prove the real staging plan, notification path, and response. |
| Database role model | Source includes a staged application-role cutover foundation. This branch adds a code-level, default-on startup migration gate so a later staged cutover can disable runtime DDL, but deliberately does not propagate the variable in current deployment configuration | Live workloads use the server-admin database URL | The gate is a prerequisite, not least privilege. Add the flag only in the dedicated identity rung, then prove separate runtime DML and migration DDL identities before removing the administrative fallback. |
| Storage/Redis credentials | Source and deployments rely on account/access keys | Live workloads reference those secrets | Add managed identity/RBAC as a dual-access bridge; retire keys only after synthetic proof and an observation window. |

## PHI-capable data inventory and retention decisions

“Unverified” means an activation blocker, not permission to retain indefinitely.

| Data class | Expected location | PHI status | Current retention evidence | Required decision/control |
|---|---|---|---|---|
| Clinical uploads and example notes | Blob containers `uploads`, `example-notes` | PHI-capable | Lifecycle rule deletes objects after 10 days | Confirm the 10-day business/legal requirement, soft-delete/versioning posture, backup need, legal hold exception, and deletion verification. |
| Synthetic test objects | Blob test containers | Synthetic only by policy | No lifecycle deletion observed | Enforce synthetic-only use and approve a short automatic deletion window. Never place PHI in test containers. |
| Application, identity, entitlement, appointment, note, billing-advisory, and workflow state | PostgreSQL | PHI-capable or sensitive | Database backup retention is 30 days in production and 7 days in staging; record-level retention is unverified | Approve a record-retention schedule by data class. Backup retention is not record retention. |
| CAB advisory output | PostgreSQL and application responses | PHI-capable when linked to a patient/workflow | Unverified | Keep advisory-only; define retention and deletion with the underlying encounter record. Do not use for 837P/claim submission. |
| NPI | Not required or expected in signup/onboarding | Regulated identifier; not an authentication secret | Collection is out of scope | Do not collect. A future payer integration needs just-in-time purpose and retention approval before schema/UI work. |
| Payer-facing standard transaction or CAB write payload | Not enabled | PHI-capable | Not applicable | 837P, claim submission, clearinghouse enrollment, and CAB-driven EHR writes remain out of scope. New activation review required. |
| Redis cache/broker material | Redis | Treat as PHI-capable until payload inspection proves otherwise | TTL, persistence, and rehydration behavior unverified | Inventory key classes, minimize payloads, set TTLs, and prove synthetic rebuild/failover behavior. |
| Application and platform telemetry | Log Analytics and Azure Monitor | Must be PHI-free | Production workspace retention observed at 30 days | Permit fixed event codes, route/status classes, aggregate counts, latency, capacity, and opaque correlation IDs only. Prohibit note text, transcript text, filenames, object keys, email, NPI, tokens, and request/response bodies. |
| Application, database, storage, Redis, registry, and third-party credentials | Container Apps/Azure control plane and external secret source | Sensitive, not PHI by design | Inline secret references observed; rotation evidence absent | Assign owner and interval per credential, prove dual-secret/rollback behavior, and move supported access to managed identity/Key Vault references. |

## Activation gate sheet

Only `PASS` gates count toward the 6% score. `PARTIAL` and `BLOCKED` both prevent activation.

| # | Gate | Status | Evidence required to pass | Owner role |
|---:|---|---|---|---|
| 1 | Cloud/vendor agreements, BAA, and covered-service configuration review | BLOCKED | Signed/accepted agreements plus a service matrix for Azure and every PHI-capable vendor covering enabled product/SKU, region, retention/training settings, subcontractor boundary, and responsibility owner | Compliance/legal + security |
| 2 | NPI/CAB/claims scope boundary | PARTIAL | Owner policy in this document and matching canonical onboarding/security/data-retention controls | Product + privacy |
| 3 | Approved data inventory and retention/deletion schedule | BLOCKED | Signed decisions for database rows, blobs, caches, logs, backups, legal hold, test data, and future NPI | Privacy + data owner |
| 4 | Encryption and key ownership | PARTIAL | Evidence for encryption in transit/at rest across every store; key/secret owner, rotation interval, and revocation test | Security/platform |
| 5 | Production database private/tightly restricted access | BLOCKED | Private path or exact necessary egress only; no steady-state allow-all; rollback proof | Platform |
| 6 | Separate least-privileged runtime and migration identities | BLOCKED | Grants diff, negative DDL test for runtime, successful synthetic migration, rotation and rollback evidence | Database/platform |
| 7 | Storage private/default-deny and managed-identity access | BLOCKED | Account public blob disabled, network default deny/private path, shared key disabled after dual-access proof, negative access tests | Platform |
| 8 | Redis private access and credential posture | BLOCKED | Approved Managed Redis/private endpoint path, managed identity where supported, TLS and negative public/key tests | Platform |
| 9 | Secret and registry credential lifecycle | BLOCKED | Key Vault/managed-identity design, owner/rotation register, tested revocation, registry admin retirement | Security/platform |
| 10 | PHI-free diagnostics and telemetry | BLOCKED | Non-production plan and sample output proving aggregate/metadata-only collection; retention and access review | Observability/privacy |
| 11 | Alert coverage, delivery, and routing | BLOCKED | Synthetic evaluation, notification receipt, acknowledgement, escalation, and resolution timestamps for auth, billing/webhook, backlog, upload/transcription/note, dependency, capacity, and recovery signals | Operations/support |
| 12 | PostgreSQL backup configuration | PASS | Observed 30-day production and 7-day staging retention and available restore window metadata | Database/platform |
| 13 | Successful restore and approved RTO/RPO | BLOCKED | Non-production synthetic restore report with measured RTO/RPO and application validation; targets approved by owner | Database + business owner |
| 14 | HA and geo-recovery disposition | BLOCKED | SKU/region feasibility and cost plan; owner selects HA/geo design or records time-bounded risk acceptance after restore proof | Business + platform |
| 15 | Incident response rehearsal | BLOCKED | Named incident roles and a synthetic tabletop/technical drill using `PUBLIC_PHI_INCIDENT_RESPONSE.md` | Security + operations |
| 16 | Support ownership and public-pilot coverage | BLOCKED | Named primary/secondary, coverage hours, escalation SLA, action-group membership, receipt test, and customer-communication owner | Support + operations |

## Security and control matrix

| Threat/control objective | Required preventive control | Required detective/recovery evidence | Current decision |
|---|---|---|---|
| Account impersonation or cross-tenant access | Canonical authentication plus organization/entitlement enforcement; least privilege at each service boundary | Fixed-code aggregate auth/authz alerts and negative cross-tenant tests | BLOCKED pending canonical evidence and alert rehearsal. NPI is explicitly not a control. |
| Public data-service exposure | Private endpoints or exact required egress, default-deny storage, no steady-state PostgreSQL allow-all, no legacy public/key path after cutover | Read-only network inventory and negative unapproved-network tests | BLOCKED; current production data paths remain public. |
| Runtime schema mutation or credential overreach | Separate runtime DML and migration DDL identities; startup migration disabled only after dedicated-job proof | Grants diff, runtime DDL denial, migration success, rotation and rollback | BLOCKED; the source gate is only a prerequisite. |
| PHI leakage through telemetry | Fixed event codes and aggregate metrics only; no payload/object/query logging | Sample alert payload privacy review plus retention/access decision | BLOCKED; source queries are aggregate-only but have not run in staging. |
| Undetected service/workflow failure | Auth, billing/webhook, backlog, clinical pipeline, dependency, capacity, and recovery alerts to an owned route | Signal, evaluation, receipt, acknowledgement, escalation, resolution timestamps | BLOCKED; provider-mocked plan is not delivery evidence. |
| Data loss or regional outage | Tested backups/restores plus approved RTO/RPO and HA/geo disposition | Isolated synthetic restore and application validation | BLOCKED; backup configuration exists but recovery is unproved. |
| Payer/EHR integration scope creep | No NPI collection during signup/onboarding; CAB advisory only; 837P/claim/clearinghouse/EHR-write paths disabled | Future integration-specific purpose, retention, disclosure, owner, and activation review | IN SCOPE AS A BOUNDARY, not a security compensating control. |

## HA and disaster-recovery decision

Current PostgreSQL HA and geo-redundant backup are disabled. The current `B_Standard_B1ms` posture is not accepted as the public-PHI launch decision.

Recommended disposition:

1. Measure actual non-production restore RTO/RPO with the companion rehearsal.
2. Produce a non-production plan and cost estimate for a production-eligible SKU with zone-redundant HA when Azure region/SKU support permits it. Any resize or replacement must be isolated from additive network/observability changes.
3. Separately decide whether cross-region recovery is required. Geo-redundant backup, paired-region restoration, data residency, and failover operating ownership must be considered together.
4. The business owner and platform owner must either approve the HA/geo design or sign a time-bounded risk acceptance with an explicit outage tolerance, expiry, and compensating recovery evidence.

No recovery, HA, or geo-resilience claim may be made from backup-retention metadata alone.

## PHI-free observability contract

Permitted alert dimensions are fixed event code, component, environment, deployment/revision identifier, HTTP status class, dependency name, aggregate count, duration, capacity percentage, and opaque correlation ID. Alert queries must aggregate before projecting results.

Prohibited telemetry includes clinical note or transcript text, prompts/completions, filenames or blob paths, patient/user names, email addresses, NPI, payment details, session/cookie/header values, database query text or parameters, access keys, tokens, DSNs, and request/response bodies.

Data-service diagnostics should begin with metrics only. PostgreSQL query logs and storage object read/write/delete logs require a separate privacy review because they can reveal SQL or object identifiers. A diagnostic setting existing is not evidence that its payload is safe.

## Required alert families

- Authentication and authorization: aggregate invalid-session, forbidden, tenant-boundary, and access-denied event codes; no identifiers in the notification.
- Billing and webhook: aggregate signature, delivery, processing, inbox-backlog, and subscription-state transition failures. CAB remains advisory; no claims-submission alert family is in scope.
- Worker/backlog: task failures/retries, queue age/depth, dead-letter or operator-attention counts, and missing scheduler heartbeat.
- Upload/transcription/note: aggregate upload, transcription, extraction, and persistence failures.
- Dependency health: PostgreSQL availability/connection failures, Redis errors/load/memory, storage availability, and third-party circuit/error state.
- Capacity: PostgreSQL CPU/memory/storage, Redis memory/server load, Container Apps replica/restart/request saturation, storage capacity and availability.
- Backup/restore: failed administrative operations, retention drift, rehearsal overdue, and restore validation failure.

Existing task-failure and Zoom-sync alert firing history is useful evaluation evidence, but notification delivery, acknowledgement, and on-call handling remain unproved.

The module in this branch is only a phase-one baseline. Its PostgreSQL administrative-failure alert is not restore proof, and it does not yet cover rehearsal freshness, Container Apps restart/capacity, queue age/depth, scheduler heartbeat, PostgreSQL connection/CPU/memory, Redis server load, or storage capacity. Those signals need verified PHI-free telemetry and owner-approved thresholds before gate 11 can pass.

## Named operational ownership required

Before alert delivery is tested, replace each placeholder with a person or staffed team and record an alternate:

| Responsibility | Primary | Secondary | Evidence needed |
|---|---|---|---|
| Public-pilot incident commander | UNASSIGNED | UNASSIGNED | Roster acknowledgement and coverage hours |
| Azure/platform responder | UNASSIGNED | UNASSIGNED | Action-group receipt and acknowledgement test |
| Database/recovery operator | UNASSIGNED | UNASSIGNED | Restore rehearsal participation |
| Application/workflow responder | UNASSIGNED | UNASSIGNED | Synthetic alert triage proof |
| Security/privacy lead | UNASSIGNED | UNASSIGNED | Incident and breach assessment tabletop |
| Customer support liaison | UNASSIGNED | UNASSIGNED | Customer escalation and handoff drill |
| Customer communications approver | UNASSIGNED | UNASSIGNED | Approved message templates and contact tree |
| Compliance/legal escalation | UNASSIGNED | UNASSIGNED | BAA and notification-decision workflow |

An email receiver configured in Azure is not support ownership. Evidence must show receipt, acknowledgement, escalation, and after-hours coverage.

## Migration and rollback rungs

Each rung is separately reviewed. A saved plan must be created immediately before an approved apply, and its JSON action list and SHA-256 digest must be attached to the change record. Any unexpected create, update, delete, or replace action stops the rung. Production actions are outside the authorization of this baseline.

| Rung | Prerequisites | Intended effect and exact plan allowlist | Observation/evidence | Rollback | Approval |
|---:|---|---|---|---|---|
| 0 — evidence/source | Clean worktree; redacted Azure snapshot | Documentation, tests, backward-compatible runtime migration switch, and default-off IaC only; no apply | IaC validation, static tests, source/live drift table, provider-mocked plan | Revert source commit | Code owners |
| 1 — staging drift adoption | Fresh live egress check; required egress variable matches refreshed scheduler output; source/state owner | Import only the existing `aca-staging-egress` rule, remove the stale allow-all declaration, and update only explicit TLS/anonymous-blob settings; zero replacement | Credentialed refreshed action list; exact-IP connectivity and unapproved-network denial | Abort before mutation and retain the corrected source. If state adoption partially succeeds, reconcile only the exact state address while leaving the safe live rule untouched. Never restore or recreate the allow-all declaration. | Platform + database |
| 2 — staging telemetry | Named non-production receivers; PHI-free query review; activation flag and owner slug persisted in the reviewed deployment variable source; mailbox injected by the protected runner | Create/update only reviewed staging diagnostic settings, action group, and metric/log/activity alerts; no data/network/workload replacement | Synthetic signal → evaluation → receipt → acknowledgement → resolution timestamps; sample payload review | Set the persisted observability feature flag to false in a separately reviewed rollback plan or revert only allowlisted resources | Operations + privacy |
| 3 — staging restore | Approved synthetic fixture; isolated target naming/network; restore operator | Create only an isolated non-production restored PostgreSQL server and its restricted access dependencies | Companion rehearsal evidence, measured RTO/RPO, negative access tests, application validation | Keep source untouched; after evidence approval, delete only the uniquely named drill target under a separate cleanup record | Database + operations |
| 4 — database identities | Successful restore; code-level startup migration gate; grant design | Add only staging migration/runtime roles, secret references, and `AUTO_MIGRATE_ON_STARTUP=false` to the affected workload configurations; do not drop admin access in this rung | Exact revision allowlist, runtime DML success, runtime DDL denial, migration success, restart and rotation tests | Restore the prior staging DSN and default-on startup behavior for a bounded window; revoke the new role only after connection drain | Database + application |
| 5 — managed-identity bridge | Identity/RBAC plan; no key rotation | Add only staging identities, role assignments, and dual-access configuration for storage/registry and supported services | Synthetic upload/read/delete, registry pull, job and worker restart tests; key usage telemetry | Revert workload auth mode to existing key while the unrotated key remains valid | Security + platform |
| 6 — parallel private path | Approved address/DNS plan; no overlap; acceptance workload | Create only additive parallel VNet, subnets, private endpoints/DNS links, and synthetic probe/workload | DNS resolves private addresses; HTTPS/TLS and dependency probes; no production traffic change | Remove only new parallel resources after evidence capture; current public path remains | Network + platform |
| 7 — staging cutover/hardening | Rungs 1–6 pass; observation window and rollback owner | Update staging workload route/auth; then disable public/shared-key access only for proven services; no replacement | Synthetic end-to-end, negative public/key tests, alert health, restart/scale tests | Re-enable the last known staging access path and exact egress rule; restore previous revision/auth mode | Change authority |
| 8 — production storage safety attributes | All staging evidence accepted; container inventory confirms no anonymous dependency; approved production plan digest and rollback owner | In-place updates only to set public-blob nesting off and make existing HTTPS/TLS settings explicit on the production uploads and state accounts; no network/shared-key/traffic/secret change, delete, replace, or workload revision | Positive authenticated upload/state operation, negative anonymous access, metrics, and observation window | Restore only the account-level nested-public setting if a verified dependency requires emergency recovery; retain HTTPS/TLS | Explicit production storage change approval |
| 9 — production additive telemetry foundation | Rung 8 accepted; durable enable flag/owner; protected mailbox; approved production plan digest | Add only the reviewed diagnostics, action group, and alert resources; no workload, data, network, secret, delete, or replace action | Continuous health plus synthetic delivery/acknowledgement/escalation with zero routing effect | Persist the enable flag as false in a separately reviewed plan and remove only the added telemetry resources | Explicit production observability approval |
| 10 — production cutover | Separate exact plan per service; named command/rollback operator; current backup and restore evidence | One service boundary at a time: workload identity/route first, observe, then retire old access. Destructive replacement isolated into its own proposal | Synthetic PHI-free acceptance, negative legacy-access tests, delivery/ack proof, business monitoring | Reverse only the current service rung: prior revision/route/auth/firewall; stop before next service | Explicit production activation approval |

## Exact plan evidence and contract

Evidence produced from this review branch:

- Production IaC validation passed with AzureRM `4.73.0`; only pre-existing/deprecation warnings were reported.
- Staging IaC validation passed with its pinned AzureRM `4.26.0`.
- The isolated provider-mocked observability module plan passed with only additive actions—one action group, three metric-only diagnostic settings, four aggregate fixed-event log alerts, five dependency/capacity metric alerts, and one PostgreSQL administrative-failure activity alert. The exact rerun count and digest are recorded in the companion evidence file. The test asserted all four queries end in only `failure_count = count()`.
- The repository-level provider-mocked plan suite passed all three runs, including default-off and observability-enabled cases.
- A credentialed full staging refresh/plan was attempted with synthetic routing values and failed before producing an acceptable plan because this fresh worktree was not approved by `direnv` and no 1Password provider/service-account credential was available. Its incomplete saved file is **invalid and must not be applied**. No Azure mutation occurred.

The mocked result proves graph shape and provider validation only. No non-production plan backed by refreshed Azure state has been accepted, and no production plan was requested or generated. Before any apply is proposed, the change record must include:

- repository commit SHA, tool/provider versions, workspace/backend identity, environment, UTC timestamp, and operator;
- saved binary plan outside the repository with restrictive permissions and its SHA-256 digest;
- machine-readable action list containing every resource address, action (`create`, `update`, `delete`, or `replace`), and replacement path;
- explicit statement that secret values and sensitive outputs were not printed or attached;
- comparison against the rung allowlist above, with zero unreviewed actions;
- separate approval for any delete, replacement, database SKU/HA change, public-access change, credential retirement/rotation, or workload traffic change;
- post-plan drift refresh immediately before apply, invalidating the plan if source, state, or live configuration changed.

Observability activation inputs must be durable: persist `enable_public_phi_observability=true` and the approved support-owner slug in the reviewed staging deployment variable source, and inject the role mailbox through the protected runner. A one-off `-var` activation is prohibited because a later default plan would otherwise propose deletion. Rollback must be a separate reviewed plan that deliberately persists the flag as false.

An exit code, “plan succeeded,” or aggregate add/change/destroy counts alone are insufficient evidence.

## Current blockers and next approvals

1. Compliance/legal and security must attach the Azure and PHI-capable vendor BAA/covered-service configuration matrix.
2. Privacy and data owners must approve retention/deletion for database rows, blobs, cache/broker payloads, logs, backups, test containers, and any future just-in-time NPI.
3. Operations must name primary/secondary owners and prove alert delivery and acknowledgement with synthetic, PHI-free signals.
4. Database/platform owners must approve and execute the non-production restore rehearsal, then approve RTO/RPO targets.
5. Platform/database/application owners must adopt and prove the new startup-migration switch, then finish the least-privilege role split in staging before administrative DSNs can be retired.
6. Platform/security must validate managed-identity and parallel private-path rungs in staging before proposing public/shared-key retirement.
7. Business and platform owners must approve a PostgreSQL HA/geo design or a dated risk acceptance after restore evidence exists.
8. An explicit production change approval, exact saved plan, rollback operator, and observation window are required for each production rung. None is granted by this document.

Companion procedures:

- `docs/evidence/PUBLIC_PHI_PLAN_EVIDENCE_2026-08-27.md`
- `docs/NONPRODUCTION_BACKUP_RESTORE_REHEARSAL.md`
- `docs/PUBLIC_PHI_INCIDENT_RESPONSE.md`
- `docs/POSTGRES_APPLICATION_ROLE_CUTOVER.md`
- `docs/EAST_US_DATA_PATH_PLAN.md`
- `docs/PLATFORM_SCRIBE_NETWORK_PLAN.md`
