# AGENTS.md — Cosmaneura core engineering guardrails (v0.2.1)

> The canonical baseline lives in `cosmaneura-architecture/deploy-kit/`.
> Preserve stricter local rules when syncing it here.

## Operating objective

Reduce deployment risk created by rapid code growth. Make changes small,
independently deployable, observable, and reversible. Documentation supports
that work; it does not replace decomposition or deployed-state reconciliation.

## Repository facts and verified commands

- This is the HIPAA-oriented core platform. The default branch is `master`.
  Production and staging run on Azure Container Apps.
- Install: `just setup` (macOS local setup; creates local configuration).
- Full test: `just py_test && just js_test`.
- Focused test: `uv run pytest <path>`.
- Lint/typecheck: `just lint`.
- Local development: `just dev`.
- Generate an Alembic revision: `just db_generate_migration "<name>"`.
- Apply migrations to local dev/test databases: `just db_migrate`.
- PR CI runs secretless Backend and Frontend jobs. Infrastructure PRs run
  secretless OpenTofu format, validation, and provider-mocked tests.

Agents never invoke production deploy, migration, or Azure mutation commands.

## Risk classes (declare one on every PR)

- **Tier 0 — routine:** localized code, test, copy, style, documentation, or a
  behavior-preserving refactor with no deployment-surface, schema/data,
  auth/session, billing, PHI, security-boundary, or external-contract impact.
- **Tier 1 — deployment-sensitive and reversible:** a dependency, runtime
  configuration, CI/build path, additive API/contract, additive schema/data
  structure, or user-visible behavior needing deliberate recovery.
- **Tier 2 — irreversible or high blast radius:** destructive or large-scale
  data work; auth/session, Stripe/billing, patient/PHI paths; security
  boundaries; production identity/network/promotion; breaking contracts; or
  broad infrastructure changes.

The following set a Tier 2 minimum: `app/security/`,
`app/configuration/clerk.py`, `app/configuration/authority.py`,
`app/configuration/service_auth.py`, Stripe/billing paths, patient-data schema,
session/token logic, destructive migrations, and production deployment or
infrastructure control. Urgency never lowers a tier. A mixed PR takes its
highest tier and should be split when concerns can deploy independently.

## Conditional controls

- A feature flag applies only to reversible user-visible behavior. It is not a
  rollback for schema, data, infrastructure, security, or refactoring work.
- A flight plan is required for Tier 2, schema/data sequences, cross-repo
  rollouts, or work released over multiple deploys/cohorts.
- A migration rung and rehearsal apply only to schema/data work. Auth, billing,
  or PHI code without a schema/data change does not invent them.
- Every code-changing PR receives independent agent review after its final
  substantive push. The reviewer cannot be the implementer. Durable PR
  evidence links the reviewed commit and records findings and dispositions. A
  later substantive push invalidates it. Agent review is not GitHub approval.

## Approval and promotion boundaries

- Preserve the repository's current GitHub PR approval settings. These
  documents add or remove no required human approvals.
- Target production policy is founder-only manual promotion from a merged
  `master` commit and an already-built artifact. The run records founder
  self-authorization; it is not independent review.
- Current tracked behavior does not yet match that target:
  `Build & Publish` can build, publish, and deploy eligible pushes to `master`
  automatically, including documentation-only pushes because it has no path
  exclusion, while production infrastructure uses separate manual
  `Terraform Production Plan` and `Terraform Apply` workflows. This adoption
  does not change those workflows. Treat manual-only artifact promotion as an
  open remediation item and never claim a required environment reviewer exists.
- Before promoting over an environment whose SHA, digest, configuration, or
  schema differs from `master`, record the drift and choose keep, assimilate,
  or replace. Never reconcile by blindly deploying `master`.
- Unless explicitly broader, security-risk acceptance permits dark development
  in a non-PHI environment only and blocks production or PHI-bearing promotion.

## Permission boundaries

**Allowed without a separate production decision**

- Create small or stacked PRs, tests, flight plans, and reversible refactors.
- Add dormant additive schema structures through an approved expand rung.
- Inspect live state read-only and prepare reconciliation options.

**Ask first**

- Change auth/session, billing, patient-record schemas, security boundaries,
  CI/deployment workflows, environment configuration, or production data.
- Add an external dependency/service or broaden a release cohort.
- Execute a production promotion, migration, backfill, or destructive cleanup.

**Never**

- Put PHI in tests, fixtures, seeds, staging, rehearsal exports, logs, PR text,
  or agent prompts; commit secrets; or force-push the default branch.
- Deploy an unmerged branch or represent prose/agent output as GitHub approval.
- Drop an additive expand structure as immediate rollback; leave it dormant.
- Archive/delete repositories or production resources under this doctrine.

## Architecture and workflow

- Vendor identity SDKs and claim parsing belong in
  `app/configuration/clerk.py` and `app/configuration/service_auth.py`; domain
  code consumes a normalized principal. Existing direct Clerk imports are known
  debt: add none, and reduce them in separate reviewable work.
- Preserve `main.py` as the production entrypoint, import-time fail-fast setup,
  separate worker/scheduler containers, and OpenAPI operation IDs.
- Never invent Alembic revision IDs. Generate them with the verified recipe;
  `tests/migrations_graph_test.py` enforces a unique acyclic single-head graph.
- Build cross-repo work receiver-first with backward-compatible contracts.
- Keep each PR to one deployable outcome, reviewable in roughly ten minutes.
- Obey the repository-specific blocking change-budget check once installed.
  Its numeric threshold belongs in a small, separately reviewed policy
  workflow; an oversized PR cannot waive its own limit by label or checkbox.
  No such tracked check exists today, so automated enforcement remains an
  adoption gap and reviewers must require decomposition meanwhile.
- Every PR records its tier, tests, exact recovery, independent agent-review
  evidence, and only the conditional controls that apply.
- Documentation-only changes must not build or deploy production artifacts.
