# Public-PHI incident response

Status: **DRAFT — PRIMARY POC ASSIGNED; SECONDARY/COVERAGE/REHEARSAL REQUIRED**
Scope: public paid-pilot infrastructure and operations
Telemetry rule: PHI-free metadata and aggregate evidence only
Owner-policy revision: 2026-08-28

This runbook is for suspected confidentiality, integrity, availability, authentication, billing/webhook, workload, dependency, backup/restore, capacity, or support incidents. It does not grant permission to change production. Emergency access and production containment still require the organization’s authorized incident commander and change procedure unless immediate action is required by an already approved break-glass policy.

Professional credential/licensure status and NPI are authorization-neutral. Neither gates continued platform use or real-PHI capabilities, and neither is authentication, identity, tenant, or entitlement proof. NPI is not required at signup/onboarding. Actual access gates are verified identity, valid practice membership and role, owner authority where applicable, current agreements and BAA, active subscription and entitlement, tenant isolation, feature-specific consent, and rollout state. CAB is advisory only; 837P/claim submission/clearinghouse enrollment and CAB-driven EHR writes are outside the pilot. A professional-status or NPI mismatch or absence is therefore not an access-control incident unless a future specifically approved workflow defines a separate, non-authorization data-quality rule.

## Deferred Azure Incident Response Advisor design reference

“Azure Incident Response Advisor” is an internal CosmaNeura design name, not a Microsoft product or a deployed control. If separately approved and implemented, the default-off service may use [Microsoft Foundry Agent Service](https://learn.microsoft.com/en-us/azure/foundry/agents/overview) and an [Azure Monitor secure-webhook action](https://learn.microsoft.com/en-us/azure/azure-monitor/alerts/action-groups#secure-webhook) to correlate sanitized, PHI-free evidence and draft one bounded recovery proposal.

This runbook creates no agent, webhook, queue, identity, alert route, model access, or execution authority. Any v1 is advisory-only, read-only, and simulation-only. It must receive only normalized PHI-free envelopes, keep incoming alert-provider payloads in memory only until rejection or normalization, use no workload credentials or mutation tools, and remain additional to independent human notification. It cannot lower severity, approve or execute an action, declare recovery or reportability, close an incident, communicate externally, or replace the named incident commander. Its output is untrusted advisory evidence subject to deterministic policy and human approval; it earns no activation-gate credit until separately implemented and rehearsed.

## Assign before activation

| Role | Primary | Secondary | Required capability |
|---|---|---|---|
| Incident commander | John Clemente, CEO | UNASSIGNED | Declares severity, assigns actions, maintains decision log |
| Azure/platform responder | John Clemente, CEO | UNASSIGNED | Network, identities, Container Apps, storage, Redis, monitoring |
| Database/recovery responder | John Clemente, CEO | UNASSIGNED | Access/grants, backups, isolated restoration |
| Application/workflow responder | John Clemente, CEO | UNASSIGNED | Auth, webhook, worker, uploads, transcription, note pipeline |
| Security/privacy lead | John Clemente, CEO | UNASSIGNED | Exposure assessment, evidence handling, notification analysis |
| Support liaison | John Clemente, CEO | UNASSIGNED | User reports, safe status collection, handoffs |
| Customer communications approver | John Clemente, CEO | UNASSIGNED | Status and direct-customer messages |
| Compliance/legal escalation | John Clemente, CEO | UNASSIGNED | Contract/BAA and notification obligations |
| Scribe/Core entitlement liaison | UNASSIGNED | UNASSIGNED | Cross-surface entitlement/onboarding impact without duplicating ownership |

John Clemente, CEO, is designated by role/name only as the primary point of contact for the first eight functions. This does not supply a contact mechanism, concurrent-role coverage, or production-change authority. Activation still requires independent secondary coverage, staffed hours, acknowledgement and response targets, protected contact mechanisms, escalation timers, action-group receipt, and tested alternates. The separate Scribe/Core liaison is unassigned. Do not place personal contact details in this repository.

## Severity and paging

| Severity | Examples | Initial acknowledgement target | Required leadership |
|---|---|---|---|
| SEV-0 | Confirmed or strongly suspected PHI, authentication-secret, or token exfiltration; active cross-tenant access; destructive production corruption; inability to account for affected data | Use approved emergency target; currently UNAPPROVED | Incident commander, security/privacy, platform, legal immediately |
| SEV-1 | Public data-service exposure beyond approved rule; production authentication outage; database/Redis/storage unavailable; failed recovery during an outage; broad webhook/workflow failure | Use approved urgent target; currently UNAPPROVED | Incident commander plus service owner and support |
| SEV-2 | Degraded capacity, elevated task failures/backlog, isolated upload/transcription/note failures, alert-delivery failure, missed backup/rehearsal signal | Use approved operational target; currently UNAPPROVED | Operations/service owner |
| SEV-3 | No-user-impact control drift, documentation gap, non-production rehearsal issue | Next staffed support window after owner assignment | Control owner |

The primary point of contact is assigned, but secondary coverage, targets, contact routes, and alternates are unapproved, so the support gate remains blocked. Severity may be raised as evidence changes; never lower it merely because an alert stopped firing.

## PHI-free evidence rules

Allowed in tickets, pages, timelines, and shared incident channels:

- UTC timestamps, Azure resource IDs, environment, region, revision/deployment IDs;
- fixed event codes, component/dependency name, HTTP status class, aggregate count, duration, utilization percentage;
- opaque incident and correlation IDs that cannot be reversed to user data;
- plan/state lineage and serial, commit SHA, tool/provider versions, and evidence-file digest;
- configuration metadata such as public/private access, TLS minimum, firewall rule count/range, identity type, backup retention, HA/geo mode.

Forbidden:

- note/transcript/upload content, prompt/completion text, filenames, blob paths or object keys;
- names, email addresses, NPI, patient/provider identifiers, appointment details, or payment data;
- request/response bodies, HTTP headers, cookies, session values, tokens, passwords, access keys, DSNs, connection strings, webhook secrets;
- SQL query text/parameters, row dumps, full cache keys/values, or screenshots containing them.

If sensitive evidence is essential, use the approved restricted evidence system and record only a reference in the incident timeline. Never paste it into chat, source control, an alert annotation, or a broadly accessible ticket.

## Retention during an incident

Incident response does not authorize indefinite preservation or broad export. The owner-proposed minimization schedule remains unapproved and must be implemented and evidenced before activation: raw audio, video, and provider payloads are retained only for the minimum time needed for successful transcript and integrity validation, with a six-hour hard retry ceiling that is a maximum rather than a target; identifiable raw transcripts after 14 days; drafts after 30 days; PHI-free operational logs after 90 days; backups after 30 days; synthetic data after 7 days; and approved minimum-necessary legal, billing, or compliance evidence after 7 years. The finalized-clinical-record schedule remains dependent on counsel and applicable state requirements.

A documented legal hold may pause a specific normal deletion only with legal/privacy authority, scope, custodian, review date, and release decision. It does not permit copying raw content into ordinary tickets or logs. Preserve PHI-free hashes, receipts, timestamps, correlation IDs, and control-plane metadata where those prove incident actions without preserving source content. The baseline’s proposal is not enforcement evidence and does not close the retention gate.

## Response sequence

### 1. Detect and acknowledge

1. Record alert/report source, UTC receipt time, environment, fixed event code/signal, and affected component.
2. Prove the environment from resource ID/subscription metadata before taking action. Do not assume a resource name means staging.
3. Assign the designated primary incident commander or an authorized delegate, severity, scribe, platform/service owners, and next update time.
4. Confirm the alert receiver acknowledged it. Portal firing history alone is not notification evidence.

### 2. Bound impact without examining content

Use aggregate and control-plane evidence first:

- first/last observed timestamps and aggregate failures/requests;
- affected environments, revisions, dependencies, regions, tenants as opaque counts only;
- authentication/authorization event-code rates and status classes;
- firewall/public-network/private-endpoint/DNS and identity assignment metadata;
- PostgreSQL connection/availability/storage/capacity, Redis error/load/memory, storage availability/capacity, Container Apps restart/replica/request metrics;
- backup retention/restore-window/operation state and diagnostic/alert delivery health.

Do not run broad row/object/log exports to “see what happened.” Security/privacy must approve any content-level review in a restricted system.

### 3. Contain using the smallest reversible rung

The incident commander chooses one bounded action at a time, records expected effect and rollback first, and observes before proceeding. Examples, subject to approved production authority:

- route or disable a single affected application revision while preserving the prior known-good revision;
- disable a compromised workload identity/secret at the consumer boundary, then revoke/rotate through the approved dual-credential procedure;
- restrict a specific firewall rule or route after verifying the necessary workload egress/private path;
- disable a single exposed storage container/path or identity rather than the entire account when scope permits;
- pause a webhook consumer or worker queue using an existing safe operational control while preserving idempotent events;
- isolate an affected non-production restored resource from all workload routes;
- preserve relevant database backup/restore metadata and PHI-free logs under the approved retention schedule or a documented legal hold; never delete evidence merely to conceal or simplify containment.

Do not improvise a production network cutover, credential rotation, database replacement, schema rollback, storage shared-key disablement, Redis flush, or destructive cleanup without the exact command/resource, owner, expected effect, observation window, and rollback path. Never remove the only known connectivity path during diagnosis.

### 4. Preserve evidence and decisions

Maintain an append-only timeline containing:

- UTC time, actor role, observation, hypothesis, decision, action, expected result, observed result, and rollback status;
- redacted control-plane output and aggregate metrics/log results;
- source commit, deployed revision, exact approved plan digest/action list when applicable;
- incident/chat/ticket references, but not sensitive payloads;
- evidence-file SHA-256 digests and restricted-system references.

Preserve relevant PHI-free Log Analytics and Azure activity/audit evidence only through the approved schedule or a documented legal hold. Do not extend retention, reset an underlying data clock through restoration, or export data broadly without privacy/legal review.

### 5. Recover and validate

1. Restore service through the last known-good, least-privilege path.
2. Run synthetic end-to-end validation for verified identity, valid practice membership/role, owner authority where applicable, current agreements/BAA, active subscription/entitlement, tenant isolation, feature-specific consent, rollout state, upload, transcription, note generation/persistence, billing/webhook advisory flow, worker/backlog, and dependency health as applicable. Professional credential/licensure status and NPI are not authorization inputs.
3. Run negative tests for the failed boundary: invalid identity, forbidden DDL, public network, stale key, cross-tenant access, or unsigned webhook.
4. Confirm alert evaluation, delivery, acknowledgement, and resolution.
5. For data recovery, use `docs/NONPRODUCTION_BACKUP_RESTORE_REHEARSAL.md` as prior evidence. A production restore requires a separate approved disaster-recovery procedure and authority.

No service is declared recovered solely because Azure reports `Succeeded` or an HTTP health endpoint returns 200.

### 6. Communicate and close

Security/privacy and legal determine whether the event is a security incident, privacy incident, contractual notification, or reportable breach. Engineering does not make that determination from log counts alone.

Customer/support messages must state verified impact, affected window, current mitigation, safe customer action, and next update time. Do not speculate, include PHI, expose security details, or describe CAB output as a submitted claim. Do not use NPI collection/verification as a remediation step.

Closure requires:

- cause and impact bounded with evidence;
- containment and recovery validated, including negative tests;
- all temporary access, credentials, routes, and diagnostic changes inventoried with removal owners/dates;
- alert delivery and support handoff confirmed;
- follow-up items assigned with priority/due date;
- security/privacy/legal and incident commander approvals;
- post-incident review and activation-gate updates.

## Scenario playbooks

### PostgreSQL public access or suspicious connection activity

1. Verify environment, firewall ranges, public-network setting, DNS/private endpoints, workload egress, and aggregate connection failures/sessions.
2. Treat the production allow-all rule as known critical exposure, not evidence of compromise by itself.
3. Do not remove it until the required workloads have a proven exact-egress/private path and rollback owner; emergency restriction still needs a connectivity impact decision.
4. If compromise is suspected, preserve audit/activity evidence, isolate affected workload identity, and involve security/privacy before content queries.
5. Validate with synthetic runtime access, runtime DDL denial, migration identity, and unapproved-network denial.

### Storage public/shared-key exposure

1. Verify account-level public access, network default action, containers, role assignments, shared-key state, and aggregate storage metrics.
2. Private container access is not proof that the account posture is safe.
3. Contain the narrowest exposed path/identity. Do not disable the only workload credential until managed-identity access is proven.
4. Rotate a suspected key only through the approved dual-key/consumer inventory procedure; validate synthetic upload/read/delete and negative stale-key access.

### Redis exposure, overload, or loss

1. Verify public network, TLS, identity/key use, memory, server load, errors, connections, and the cache/broker classification.
2. Never flush production Redis as an investigative action.
3. For cache-only loss, rebuild from the authoritative source using the proven synthetic procedure. For broker/state loss, preserve queues and validate idempotent redelivery/dead-letter handling.
4. Managed Redis/private-path migration is a planned rung, not an incident-time improvisation.

### Authentication, entitlement, or tenant-boundary signal

1. Use fixed auth/authz event codes and aggregate status classes; do not inspect tokens or user payloads in shared systems.
2. Bound affected revisions/routes/tenant count. Check verified identity, valid practice membership/role, owner authority where applicable, current agreements/BAA, active subscription/entitlement, tenant isolation, feature-specific consent, and rollout state. Coordinate with the canonical Core entitlement/onboarding owner rather than creating a parallel contract.
3. Revoke only the implicated authentication session/token or workload secret when possible and preserve unrelated access. “Credential” here never means professional credential/licensure status.
4. Professional credential/licensure status and NPI are authorization-neutral; their absence or mismatch is not an authentication signal or containment control.

### Billing/webhook or CAB advisory failure

1. Verify signature-validation, delivery, processing, inbox/backlog, idempotency, and entitlement transition event codes.
2. Pause/replay only through the approved idempotent path and preserve event ordering.
3. Do not infer that CAB advisory output was submitted as a claim or written to an EHR; 837P, clearinghouse enrollment, claim submission, and CAB-driven EHR writes are out of scope.

### Upload, transcription, note, or worker backlog

1. Use aggregate event codes, queue age/depth, task failure/retry, dependency health, and capacity.
2. Do not place upload names, transcript/note content, prompts, or third-party payloads in alerts/tickets.
3. Pause the smallest consumer stage if continued processing risks corruption or disclosure. Preserve only PHI-free idempotency receipts and replay metadata after raw content reaches successful transcript/integrity validation or its six-hour retry ceiling.
4. Validate recovery with synthetic objects and notes only.

### Backup or restore failure

1. Preserve backup/restore operation metadata and alert-delivery evidence.
2. Never test a speculative restore against or over an existing production server.
3. Use an isolated, uniquely named target and the approved recovery procedure. Escalate immediately if RTO/RPO is at risk.
4. Backup window metadata without application validation is not recovery evidence.

### Alerting or support-routing failure

1. Treat missing notification/acknowledgement as an operational incident even if the monitored service is healthy.
2. Confirm signal generation, query/metric evaluation, action-group routing, receiver delivery, acknowledgement, and escalation separately.
3. Use an approved secondary channel while repairing the primary. Record the coverage gap and affected alerts.

## Rollback record

Before each containment/recovery change, record:

```text
Incident ID / UTC:
Exact environment and resource ID:
Current verified state:
Proposed single change:
Expected security/service effect:
Preconditions and dependencies:
Observation signal and window:
Rollback command/action and operator:
Stop conditions:
Approval authority:
Observed result:
Rollback invoked/completed:
```

## Rehearsal and activation evidence

Before public-PHI activation, run a synthetic tabletop plus one technical drill. At minimum exercise: production-vs-staging identity verification, PHI-free triage, a reversible containment proposal, alert delivery/acknowledgement/escalation, Core entitlement coordination, recovery validation, support/customer handoff, and evidence handling. No live production change is required or authorized for the rehearsal.

This runbook remains `DRAFT` until independent secondary owners, severity and response targets, coverage hours, protected communication paths, break-glass authority, and rehearsal evidence are approved. The primary role/name designation alone does not close those gates.
