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

## Deploy ordering nuance

Image deploys (`bash deploy.sh <sha>` triggered by `build_and_publish.yml`)
and infrastructure applies (this workflow) run independently on the
same push. In practice:

- `tofu apply` keeps `image = ":latest"` for every container app.
- `deploy.sh` writes `image = ":<sha>"` to the same field.
- `:latest` and `:<sha>` point to the same physical image, so the diff
  is cosmetic and converges either way.

The exception is the FIRST run after a new `azurerm_container_app`
resource is added: `tofu apply` must create the container before
`deploy.sh` can `az containerapp update` its image. `deploy.sh` is
defensive about this — it skips containers that don't exist yet with
a warning instead of failing the whole deploy.
