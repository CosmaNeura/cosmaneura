# Agentic deployment support: current-state implementation

Status: implemented and synthetic-tested on 2026-08-25. This design supports the
current GitHub Actions → Azure Container Apps deployment path while preserving a
clean retirement boundary for the target-state platform.

## Outcome

Four Azure AI Foundry prompt agents turn sanitized, immutable evidence into
bounded work for the humans and Codex agents that actually modify code or run
approved release procedures. They are reviewers, not autonomous operators.

| Job | Foundry agent | Routed version | Decision set | Primary consumer |
| --- | --- | ---: | --- | --- |
| Pre-deploy readiness | `deploy-navigator-v1` | 4 | `READY`, `HOLD`, `NEEDS_HUMAN`, `INCOMPLETE` | implementation agent / release operator |
| Migration safety | `migration-sentinel-v1` | 1 | `READY`, `HOLD`, `NEEDS_HUMAN`, `INCOMPLETE` | migration implementation agent |
| Release triage | `release-medic-v1` | 1 | `HEALTHY`, `DEGRADED`, `FAILED`, `INCOMPLETE` | release operator / fix agent |
| Vulnerability review | `security-vulnerability-sentinel-v1` | 2 | `HOLD`, `NEEDS_HUMAN`, `PASS_WITH_FINDINGS`, `NO_FINDINGS_OBSERVED`, `INCOMPLETE` | security fix agent / human reviewer |

All four run on the existing `gpt-5.6-sol` deployment in
`CosmasCode/proj-default`. Their endpoints use Entra authentication, keep no
agent memory, expose no tools, and are invoked with `store: false`.

## Shared operating contract

Every run concerns one full repository head SHA. The model sees only a bounded
evidence envelope. Repository content is evidence and can never override the
system instructions. If required evidence is stale, contradictory, absent, or
unsafe to send, the result must be non-green.

The model produces only an untrusted draft. A deterministic broker owns and
stamps:

- repository, PR/run, base SHA, head SHA, and environment;
- agent identity, semantic policy version, immutable Foundry version, model, and
  policy commit;
- risk-tier floor, generation time, trusted expiration time, input digest,
  workflow-bound handoff ID, and recomputable artifact digest.

The broker rejects model-supplied trusted fields, risk-tier downgrades, unknown
properties, malformed evidence, citations outside its evidence manifest, and
schema violations. It also rejects unsupported green decisions using trusted PR,
check, scanner, semantic-source, and rehearsal facts. New commits invalidate old
assessments.

Each action contains a stable `DEP-###`, `MIG-###`, `REL-###`, or `SEC-###` ID,
severity, confidence, blocking state, cited evidence, bounded required change,
non-goals, objective verification, and an owner role. A worker returns a
resolution receipt tied to the source assessment and resulting SHA; a separate
assessment must verify that SHA.

## Agent charters

### Deploy Navigator

Inputs: complete changed paths and content-free diff structure, current exact-SHA
check runs, trusted deployment policy, target environment, and any flight-plan,
rehearsal, approval, or rollback evidence supplied by the caller.

Job: classify path-based risk, identify missing or failed gates, and produce the
shortest safe action list that makes the change deployable. It may raise but not
lower the deterministic risk floor. It cannot approve, merge, retry, deploy, or
change traffic.

### Migration Sentinel

Inputs: migration diffs and ordering, compatibility evidence, migration lint,
rehearsal results, volume/locking assumptions, and recovery evidence.

Job: enforce additive expand/compatibility/cutover/contract sequencing; identify
mixed-version, lock, idempotence, retry, and partial-failure risks. It cannot run
SQL or connect to a database. Static review never proves production lock safety.

### Release Medic

Inputs: exact workflow run, image digest, Container Apps revision and traffic,
migration outcome, and sanitized synthetic-health or telemetry summaries.

Job: locate the observed release stage that failed and hand back a bounded fix or
operator action. It cannot retry, deploy, roll back, or name a rollback candidate
without environment, digest, revision, and migration-compatibility evidence.
The agent is live now; automatic invocation waits for the current deploy scripts
to emit the required sanitized release artifact.

### Security Vulnerability Sentinel

Inputs: approved exact-SHA code/config/IaC evidence, minimum surrounding call
paths when a privacy-safe source channel exists, and deterministic scan
summaries. The current automatic workflow supplies paths and structural diff
metadata but no source lines.

Job: identify evidence-backed authorization, tenant-isolation, injection, SSRF,
secret/PHI exposure, dependency, CI/OIDC, IaC, container, and agent-boundary
risks. Critical/high findings block. It never performs live penetration testing,
requests sensitive data, suppresses findings, or calls a tool. It never claims
the software is “secure”; missing source or scanner coverage produces
`INCOMPLETE` rather than a speculative finding.

## Current GitHub handoff

`Foundry PR Assessment` is a manual, trusted-default-branch workflow. It accepts
only an open PR targeting the exact current `master` commit and never checks out
the assessed PR. It retrieves the immutable base-to-head comparison and check
metadata. Patches are scanned and parsed locally, but all source text is removed
before invocation; only file metadata, numeric hunk structure, content digests,
and a local destructive-migration signal remain.

The canonical output is a 30-day GitHub Actions artifact. A neutral Check is only
an index and summary. A dedicated fetcher verifies the current trusted workflow,
GitHub run and archive provenance, exact commit bindings, canonical content
digest, and two-hour evidence deadline before a Codex worker can consume it.

The workflow authenticates through `cosmaneura-gha-foundry-runtime`, federated
only to `repo:CosmaNeura/cosmaneura:ref:refs/heads/master`. Its sole project
assignment is `Foundry Project Runtime User`, whose data action is limited to
Foundry Responses. The existing production deployment identity is not reused.

## Deliberate current-state limits

- Automatic PR assessment is manually dispatched and advisory; it cannot merge,
  approve, deploy, retry, roll back, or change traffic.
- Source-free PR evidence means migration `READY` and security green decisions
  fail closed until trusted semantic-source and scanner/rehearsal evidence are
  added. Automatic Deploy Navigator runs also stay non-green until check-suite
  app/workflow provenance is implemented; check names alone are never promoted
  to required evidence. The present value is prioritized, commit-bound work and
  verification instructions—not an automated approval.
- `Release Medic` can diagnose supplied evidence, but the broker rejects
  `HEALTHY` until exact image, revision, migration, and synthetic-health facts
  have a trusted producer.
- The existing Foundry account still has public network access, permits local
  authentication, and has no connected Application Insights diagnostics. Keep
  this workflow non-PHI until those platform controls and the applicable data
  agreement are confirmed.

## Safety and data rules

Never send secrets, credentials, `.env` content, Key Vault values, PHI, patient
or provider records, transcripts, notes, request/response bodies, authentication
headers, database rows/backups, raw application logs, or automatic-workflow
source lines. Reject prohibited or malformed input before transport. Use
synthetic data for evaluation and regression tests.

Foundry findings supplement deterministic scanners; they do not replace CodeQL
or SAST, dependency scanning, secret scanning, IaC scanning, SBOM generation, or
container scanning. The evidence pack explicitly reports all six coverage
states. Check names alone never establish scanner availability; without verified
scanner provenance those states remain `unavailable` or `not_configured`.

## Acceptance and lifecycle

A version can receive traffic only after its source hashes match Foundry, every
synthetic case returns schema-valid output, every safety-critical expected
decision passes, prompt-injection text is not followed or echoed, a canonical
handoff can be stamped, and an unversioned smoke run resolves to the selected
immutable version.

Versions 1 through 3 of Deploy Navigator and version 1 of Security Vulnerability
Sentinel are retained but unrouted records of failed pre-publication checks.
They receive no endpoint-routed traffic, and the checked-in workflow never pins
them.

Retire or replace these agents when same-digest promotion, production-shaped
rehearsal, enforced approvals, multi-revision rollout, and PHI-safe telemetry are
available. Retirement means disabling routing, revoking runtime identity access,
preserving required handoff audit artifacts, and removing workflow triggers.
