# Infrastructure

OpenTofu / Terraform configuration for the cosmaneura production environment.
State lives in the `cosmaneuratfstate` Azure storage account (see
`local.tfstate` in `main.tf`).

## How changes get to production

1. **Open a PR that touches `infra/**` or `Procfile`.**
   The `.github/workflows/infra_plan.yml` workflow runs automatically:
   - `tofu init`, `tofu fmt -check`, `tofu validate`
   - `tofu plan` against the live state
   - Posts (or updates) a sticky PR comment with the plan output

   Reviewers must see and approve that plan before merging.

2. **Merge to master.**
   `.github/workflows/infra_apply.yml` runs automatically:
   - `tofu init`
   - `tofu plan` (sanity check immediately before apply)
   - `tofu apply -auto-approve`

   Concurrency is serialized via a workflow-level concurrency group so
   two merges in quick succession don't race on the state file.

3. **If something goes wrong**, you can re-trigger apply manually:
   - GitHub → Actions → Terraform Apply → "Run workflow" on master.

## When to apply locally instead

You should almost never need to. The local recipes in `Justfile`
(`just tofu_apply`, `just tofu_output`, `just tofu_init`) still work
and are the right tool when:

- You need to bootstrap the state backend from scratch.
- CI is unavailable and there's a production incident that requires a
  hotfix faster than a CI cycle.
- You're iterating on terraform changes that you don't intend to
  commit yet (use `tofu plan` only — never `tofu apply` from local on
  uncommitted code).

When you do run apply locally, push the resulting changes to a PR
immediately so the state in the cloud and the source tree stay in
sync.

## Required GitHub configuration

These need to exist for the workflows to authenticate; they were set up
when the existing `build_and_publish.yml` workflow shipped:

**Repository secrets / vars** (under Settings → Secrets and variables → Actions):

- `AZURE_CLIENT_ID` (App registration client id used for OIDC)
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`
- `OP_SERVICE_ACCOUNT_TOKEN` (1Password service account; read access to
  the production vault)

**Environment** `production` (Settings → Environments → production):

- Required reviewers (optional): adds a manual approval gate before
  `tofu apply` runs. Recommended for high-stakes changes.
- Branch policy: `master` only (already configured). This is what
  causes PR-triggered runs to skip the `apply` job — by design.

**Federated credentials** on the Azure AD App Registration: needs TWO
entries so both workflows can authenticate to Azure via OIDC.

| Workflow | Used by | Subject claim |
| --- | --- | --- |
| `infra_apply.yml` (master pushes, deploy) | `environment: production` | `repo:CosmaNeura/cosmaneura:environment:production` |
| `infra_plan.yml` (PR runs, read-only) | `pull_request` event | `repo:CosmaNeura/cosmaneura:pull_request` |

The first credential already exists (the `build_and_publish` deploy
job uses it). Add the second one if `infra_plan.yml` runs fail with
an AADSTS70021 / "no matching federated identity record" error.

To add the credential: Azure Portal → Microsoft Entra ID → App
Registrations → (your app) → Certificates & secrets → Federated
credentials → Add. Scenario: GitHub Actions deploying Azure
resources. Entity type: Pull request.

## Deterministic infrastructure/application ordering

A push to `master` that changes `infra/**`, `.env.production.backend`,
`Procfile`, or the Terraform Apply workflow starts the automatic Terraform
Apply but deliberately holds the
production application deploy. This prevents two independent workflows from
creating competing Container App revisions.

For a change that includes both infrastructure and application code:

1. Merge only after reviewing the PR's Terraform plan.
2. Wait for **Terraform Apply** on the merge commit to finish successfully.
3. Copy that successful run's numeric ID from its Actions URL.
4. Manually run **Build & Publish** on `master`, supplying the ID as
   `terraform_apply_run_id`. The workflow verifies the run, its ancestry, and
   that no later production-infrastructure change exists before it deploys the
   current immutable commit image.

Application-only pushes keep their existing automatic behavior, but the deploy
first resolves the latest successful Terraform Apply and performs the same
ancestry/no-drift verification. An unknown push comparison fails closed and
requires the same manual run.
Terraform Apply and the production deploy additionally share one
non-cancelling concurrency group, so a later application-only push queues
instead of mutating Container Apps during an in-flight apply.

## Provider record-grant secret scope

`SOVEREIGN_RECORD_GRANT_SECRET` is a capability-bearing key used only by the
HTTP API to mint short-lived synthetic Preview record grants. OpenTofu exposes
it only to `cosmaneura-prod-app`; it is excluded from the worker, scheduler,
job-monitor, and migration job. Staging's deployment script enforces the same
API-only split for `cosmaneura-staging-app`. Staging remains off by default;
provision its separate 1Password item first, then set the staging GitHub
environment variable `ENABLE_SOVEREIGN_RECORD_GRANTS=true` only for the
controlled staging deploy. Returning it to `false` removes the environment
reference and stale app-level secret from every staging workload.

## PostgreSQL application role cutover

The default-off role preparation and versioned Container Apps secret operator
are documented in
[`../docs/POSTGRES_APPLICATION_ROLE_CUTOVER.md`](../docs/POSTGRES_APPLICATION_ROLE_CUTOVER.md).
They do not authorize a live cutover. The runbook records the required
deployment/Terraform freeze, startup-Alembic behavior, rollback order,
source-controlled old/v2 secret selection, adoption-only live-state guard,
cluster-database ACL audit, and value-free migration preflight. The current
inline-secret model places encrypted values in protected OpenTofu state; moving
them to Key Vault references remains a separate architecture change.
OpenTofu owns the four long-running apps; the deploy script owns the already-
provisioned migration job and now fails rather than creating it implicitly.

## Scribe workflow identity

The web API has a dedicated user-assigned managed identity for platform-to-Scribe calls. Its client
and principal IDs are non-secret outputs; the principal receives only the Entra application role
`Scribe.Workflow.Access`. The feature remains false by default. The current platform Container Apps
environment has no VNet integration, so private network reachability must be designed and reviewed
before enablement. See [`../docs/SCRIBE_WORKFLOW.md`](../docs/SCRIBE_WORKFLOW.md).

## Parallel platform network

The review-only East US replacement foundation is defined behind
`deploy_parallel_vnet_environment = false`. The default configuration creates nothing. It never
clones a Container App, binds a custom domain, changes DNS, or moves traffic. See
[`../docs/PLATFORM_SCRIBE_NETWORK_PLAN.md`](../docs/PLATFORM_SCRIBE_NETWORK_PLAN.md) for the paired
platform/Scribe plans and approval gates.

The separately gated private bridge and manual synthetic probe are also false by default:

```hcl
deploy_parallel_private_data_endpoints = false
deploy_parallel_network_smoke_job      = false
```

They are documented in [`../docs/EAST_US_DATA_PATH_PLAN.md`](../docs/EAST_US_DATA_PATH_PLAN.md).
The bridge does not disable current public endpoints, rotate credentials, or clone a production
workload.
