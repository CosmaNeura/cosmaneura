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
- Production promotion is founder-only and manual. `Build & Publish (manual)` permits
  the production job only for a manual run of the exact `master` ref when both
  the original actor and any rerun actor are `JohnClem`; ordinary pushes,
  including documentation-only pushes, cannot enter that job. The run records
  founder self-authorization; it is not independent review.
- Canonical staging deployment is founder-only and manual. Its control-plane
  workflow runs from the exact current protected `master`. The legacy
  `staging`-ref deployment path has been removed, so `Deploy Staging Candidate`
  is the only staging deployment path; the `staging` branch itself is retired
  separately and carries no deploy path either way. Each
  canonical run requires a full immutable candidate SHA that is both
  contained in protected `master` and the exact merge commit of the
  founder-selected merged PR. It tests and deploys that candidate, not the
  moving `master` tip, and reverifies provenance before protected credentials,
  image publication, migrations, and Azure mutation. This source gate does not
  prove that staging is synthetic-only or non-PHI.
- The production job still builds and publishes during the promotion run
  instead of promoting a previously verified immutable artifact. Treat that
  source-to-artifact separation as an open remediation item. Preserve the
  existing Terraform Apply, provider-callback, ancestry, and current-`master`
  gates, and never claim a required environment reviewer exists.
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
  The tracked `change-budget` workflow enforces at most 2,000 changed lines and
  50 changed files. Keep that exact context required in the `master` ruleset;
  until the ruleset is configured, reviewers must require decomposition.
- Every PR records its tier, tests, exact recovery, independent agent-review
  evidence, and only the conditional controls that apply.
- Documentation-only changes must not build or deploy production artifacts.

## QA bots

QA covers these repositories:

- `CosmaNeura/cosmaneura` (Core)
- `cosmaneura-scribe`
- `cosmaneura-web`
- `cosmaneura-os` (Cosmas OS / CosmaBot)

### How a handoff runs

QA runs through GitHub issues labeled `qa-handoff`, one issue per QA bot.

- Current bot: QA Tester Bot, label `qa-bot-1`, cases `QA-100` through
  `QA-199`.
- `QA-200` and above are reserved for a future bot 2.

The coordinator posts `HANDOFF` pinned to the full source commit, deployed
revision or build ID, and immutable artifact digest for each component under
test; use the image digest for container workloads. Verify that identity using
release evidence matched to read-only running-workload metadata.
`GET /healthcheck` checks availability only: it returns `{"status":"ok"}`, not a
build or commit. A healthy response or the current repository tip does not
establish the deployed identity. If that identity cannot be verified, report
`BLOCKED`. The bot posts `ACK`, runs its cases, and posts `RESULT` (`PASS`, `FAIL`,
or `BLOCKED`) with uploaded evidence.

### Practice and shared tools

Each bot has its own login and its own test practice. A bot leaves every other
bot's practice and the shared demo site untouched. Shared tools (auth, settings
export) are owned by one bot at a time. The owning bot posts `RELEASED` in the
issue when it is done.

### Targets

- Exercise `https://staging-app.cosmaneura.com` only.
- On production `https://app.cosmaneura.com`, a page-load check is the only
  allowed check. No login and no actions.

### Hard rules

- No production logins and no production deploys.
- Never run `Deploy Staging Candidate` or any deploy or rollback workflow.
- QA bots post results only on handoff issues. They do not comment on, approve,
  or merge pull requests.
- Synthetic data only. No PHI.
- Stripe test mode only.
- No real clinician Zoom.
- Never contact clinicians or MCD.
- Sign-in and Zoom-join results are status-only. They are never called
  "fixed".

### Known limits

- CI is off.
- Stripe checkout may fail because of a Clerk permission issue.
- Recording-start timing needs a real iPhone and is out of scope.
