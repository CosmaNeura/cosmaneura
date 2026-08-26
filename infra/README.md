# Infrastructure

## Provider-first Agent API role reconciliation

`infra/scripts/reconcile-agent-api-role.sh` is read-only by default. It verifies
the exact staging provider UAI, preserves `Scribe.Transcript.Write`, and reports
whether `AgentApi.Access` and its provider assignment exist. The public UAI is
deliberately outside this activation.

```bash
infra/scripts/reconcile-agent-api-role.sh --status
infra/scripts/reconcile-agent-api-role.sh --apply --confirm-staging
infra/scripts/reconcile-agent-api-role.sh --revoke --confirm-staging
```

`--apply` idempotently creates the application role when absent and assigns it
only to the pinned provider staging UAI. `--revoke` removes only that assignment;
it retains the disabled-by-no-assignment role definition and leaves the existing
Scribe role unchanged. Run `--status` after either operation and retain its
value-free output with the staging change record.

Microsoft Graph does not expose an ETag for this application in the staging
tenant. Before its narrow `appRoles` PATCH, the script therefore takes an
immediate second complete role snapshot and refuses on any drift; it then
verifies the exact complete postcondition. This minimizes but cannot eliminate
the non-atomic interval, so do not run concurrent application-role changes
during reconciliation.

## V7 incomplete-turn reconciliation

The status command reports only counts, states, and opaque interaction refs:

```bash
python -m app.cli authority-ingestion-status --older-than-minutes 15
```

Accepted canonical turns without a final phase are reconciled one exact turn
at a time. The command is staging-only and dry-run by default. Mutation needs
both explicit flags and writes only the fixed safe `unavailable` final,
idempotency receipt, and platform-authoritative audit fact; it never sweeps or
prints conversation content.

```bash
python -m app.cli reconcile-stale-canonical-turn \
  --turn-ref conversation_turn_<opaque> --older-than-minutes 15
python -m app.cli reconcile-stale-canonical-turn \
  --turn-ref conversation_turn_<opaque> --older-than-minutes 15 \
  --apply --confirm-staging-only
```

OpenTofu / Terraform configuration for the cosmaneura production environment.
State lives in the `cosmaneuratfstate` Azure storage account (see
`local.tfstate` in `main.tf`).

## How changes get to production

1. **Open a PR that touches `infra/**` or `Procfile`.**
   The `.github/workflows/infra_plan.yml` workflow runs automatically:
   - `tofu init -backend=false`, `tofu fmt -check`, `tofu validate`
   - A provider-mocked static plan using only reviewed dummy inputs
   - A summary in the workflow run; it does not post to the PR

   The pull-request job is intentionally secretless and cannot read the
   production backend, Azure identity, 1Password, or application secrets.
   An independent agent must review the source changes and static-plan result;
   that evidence is not a GitHub approval. A real production-state plan is
   intentionally available only on trusted `master`:
   the founder account (`JohnClem`) runs **Terraform Production Plan** manually
   after merge. The workflow rejects another original or rerun actor, fails if
   its checkout is no longer the exact current `master` commit, and records its
   run ID, commit, canonical plan hash, and immutable attestation digest in the
   job summary. The upload action's digest is normalized to exactly
   `sha256:<64 lowercase hex>` before it is displayed or copied to a step
   output. It validates the Recall webhook signing credential's `whsec_` plus
   canonical base64 shape and minimum length without printing it.

2. **Apply explicitly after reviewing the protected plan.**
   Copy the successful **Terraform Production Plan** run ID and its `sha256:`
   artifact digest, then the founder manually runs **Terraform Apply** on
   `master` with that ID as `production_plan_run_id` and digest as
   `production_plan_artifact_digest`:
   - `tofu init`
   - verify the run's exact workflow name/path, attempt, manual event,
     completed/success state, `master` branch, and head SHA against the current
     `master` SHA
   - download only its nonsecret attestation and verify the exact artifact name
     and GitHub artifact digest
   - recompute `tofu plan` against current production state and require exact
     canonical plan, configuration, provider-lock, state-lineage, and
     state-serial equality
   - recheck that `master` has not advanced while planning
   - apply that freshly saved, digest-matched plan with
     `tofu apply -auto-approve`

   A merge or push never starts a production apply. Another actor cannot start
   or rerun the production plan or apply job. If `master` advances at any
   point, obtain a new protected plan run for the new exact commit. Concurrency
   remains serialized so two explicitly approved applies cannot race on the
   state file.

3. **Deploy the application only after the operational callback gate.**
   For a held infrastructure/application release, first send a real
   provider-signed Recall event containing no PHI to the production endpoint.
   Confirm HTTP 200 and the expected metadata-only receipt, with no body,
   signature, or provider identifier in logs. No required production
   environment reviewer is configured. The founder self-authorizes the manual
   **Build & Publish (manual)** run, must verify that evidence, and must check
   `provider_signed_recall_callback_verified`. Never paste the callback body,
   signature, URL parameters, or provider IDs into GitHub inputs or comments.

## Local infrastructure commands

The local recipes in `Justfile` (`just tofu_apply`, `just tofu_output`,
`just tofu_init`) still exist for disposable development infrastructure and
initial state-backend bootstrap. Do not use a local apply as a production
rollback or as a substitute for the founder-gated plan/apply chain. Local
production planning may be used read-only for diagnosis, but production
mutation requires a separately reviewed emergency procedure if GitHub Actions
is unavailable; no such bypass is authorized by this runbook.

## Required GitHub configuration

These need to exist for the protected apply/deploy and manual production-plan
jobs to authenticate; pull-request jobs do not receive them:

The target state configures `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, and
`AZURE_SUBSCRIPTION_ID` as **environment variables**, not repository variables.
`AZURE_CLIENT_ID` must identify that environment's Terraform-managed
`ghactions` user-assigned identity.

Verified drift on 2026-08-25: repository variables with all three Azure names
still exist, and the workflows resolve `vars.* || secrets.*`. A missing
environment value can therefore fall back to a repository value instead of
failing closed. A repository secret named `OP_SERVICE_ACCOUNT_TOKEN` also
remains even though current workflows reference only the environment-specific
production/staging token names. Inventory all consumers, then remove these
legacy repository-scoped values in a separate authorized configuration change;
do not treat their presence as production authorization.

**Environment** `production` (Settings → Environments → production):

- Required reviewers: none currently configured. The workflow itself restricts
  production plan, apply, deploy, and reruns to the founder account. Independent
  agent review remains PR evidence rather than a GitHub environment approval.
- Branch policy: `master` only.
- Environment variables `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, and
  `AZURE_SUBSCRIPTION_ID`, bound to the production `ghactions` identity.
- Environment secret `OP_PRODUCTION_SERVICE_ACCOUNT_TOKEN`: a dedicated
  least-privilege 1Password service account with read access only to the
  production items referenced by `.env.production.backend` and
  `.env.production.frontend`.
- Environment secret `RECALL_WEBHOOK_SECRET`: the production Recall/Svix
  signing credential. It must use `whsec_` plus a canonical base64 payload of
  at least 16 decoded bytes. The startup, OpenTofu, and live-scope checks report
  only a generic failure.

**Environment** `staging` (Settings → Environments → staging):

- Environment variables `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, and
  `AZURE_SUBSCRIPTION_ID`, bound to the separate staging `ghactions` identity.
- Environment secret `OP_STAGING_SERVICE_ACCOUNT_TOKEN`: a different
  least-privilege 1Password service account with read access only to the
  staging items referenced by `.env.staging.backend` and
  `.env.staging.frontend`.
- Do not define either environment-specific token as a repository secret and
  do not configure a fallback `OP_SERVICE_ACCOUNT_TOKEN`. The currently
  observed legacy repository secret is drift awaiting separate removal;
  production and staging must not be able to read one another's vault items.

### Environment-local 1Password prerequisites

Protected builds intentionally have no shared-, development-, or
other-environment vault fallback. Before the first protected build after this
change, an authorized secrets administrator must copy or provision the fields
that formerly came from a non-environment vault inside **each target
environment's own vault**:

- Production must satisfy both `.env.production.backend` and
  `.env.production.frontend` using only the production vault.
- Staging must satisfy both `.env.staging.backend` and
  `.env.staging.frontend` using only the staging vault.

| Environment vault | Required environment-local item fields |
| --- | --- |
| Production | `sentry-runtime/backend-dsn`, `sentry-runtime/frontend-dsn`, `assemblyai-runtime/api-key` |
| Staging | `sentry-runtime/backend-dsn`, `sentry-runtime/frontend-dsn`, `assemblyai-runtime/api-key` |

Copy or provision the values through the approved 1Password administration
path; do not put them in a ticket, pull request, command output, or workflow
log. Also verify that every pre-existing target-local item referenced by that
environment's backend and frontend files is present. Confirm item and field
names and the environment service account's read grants without displaying
field values. This repository does not migrate the values. Do not start a
production or staging protected build until that environment's complete item
set and grants are in place. Missing or inaccessible fields make `op inject`
fail the build before image publication; there is deliberately no fallback to
a shared, development, production-from-staging, or staging-from-production
vault.

Sentry release credentials are deliberately not resolved during the container
build. Source-map release upload stays disabled until the build has a reviewed,
non-persisting BuildKit secret channel. In particular, do not add
`SENTRY_AUTH_TOKEN`, `SENTRY_ORG`, or `SENTRY_PROJECT` to either protected
frontend env file or to Nixpacks `--env` arguments, and do not publish the
ephemeral JavaScript builder image as a registry cache. Consequently,
`sentry-release` is not a protected-build prerequisite and must not be copied
into either build path merely to re-enable the old behavior.

Common setup installs the exact 1Password CLI package version pinned in
`.github/actions/install-op/action.yml` and verifies its official SHA-256 before
execution. Updating that version requires a reviewed checksum update from the
official 1Password package index.

**Federated credentials** on the Azure AD App Registration are only for jobs
that use a protected deployment environment.

| Workflow | Used by | Subject claim |
| --- | --- | --- |
| `infra_apply.yml` (manual attested-plan apply) | `environment: production` | `repo:CosmaNeura/cosmaneura:environment:production` |
| `infra_production_plan.yml` (manual plan on master) | `environment: production` | `repo:CosmaNeura/cosmaneura:environment:production` |
| `build_and_publish_manual.yml` (production deploy) | `environment: production` | `repo:CosmaNeura/cosmaneura:environment:production` |
| `build_and_publish_manual.yml` (staging deploy) | `environment: staging` | `repo:CosmaNeura/cosmaneura:environment:staging` |

The three production rows share the production environment subject, so one
matching production federated credential is sufficient. Staging requires its
own federated credential for the distinct staging environment subject and
should use a staging-scoped Azure identity. Do not add a `pull_request`
federated credential; PR workflows are deliberately prevented from obtaining
Azure tokens.

### OIDC credential reconciliation hold

The current Azure credential inventory was checked before this change and is
not yet identical to the Terraform model above. **Do not run a production or
staging apply as the bootstrap mechanism.** Reconcile the live credentials and
Terraform state under a separate reviewed change record first:

1. Production currently has only the stale `ref:refs/heads/master` credential
   on its user-assigned identity. An Azure administrator must create the exact
   `repo:CosmaNeura/cosmaneura:environment:production` credential out of band,
   verify a no-mutation OIDC login from the protected `production`
   environment, and then import/adopt that credential into the intended
   Terraform resource before any protected production plan or apply.
2. Staging already has an environment-subject credential named
   `github-actions-staging-env` as well as stale branch-subject credentials.
   Do not apply the currently named `github-actions` Terraform resource
   directly: Azure can reject a duplicate issuer/subject pair. Under the same
   reviewed record, decide and document whether to import/adopt the existing
   environment credential or rename the Terraform resource/configuration,
   reconcile state, and verify a no-mutation OIDC login from the protected
   `staging` environment.
3. Only after exact environment-subject ownership and state binding have been
   reviewed and verified may the stale branch-subject credentials be removed.
   Archive value-free evidence of credential names, issuer, subject, audience,
   state addresses, approvals, and login results. Never leave a branch-subject
   credential as a fallback: it bypasses the environment-review binding.

Until those steps are complete, both protected deployment paths are on an
operational hold even though the committed Terraform configuration models the
desired final subjects. No secret, token, or credential value is needed in the
change record.

## Deterministic infrastructure/application ordering

A push to `master` that changes `infra/**`, `.env`,
`.env.production.backend`, `.env.production.frontend`, the direnv/Just
toolchain used to resolve them, `Procfile`, the shared setup action, or a
production plan/apply/publish workflow deliberately holds the production
application deploy. No production apply is started by the push. This prevents
unreviewed state mutation and competing Container App revisions.

For a change that includes both infrastructure and application code:

1. Merge only after reviewing the PR's secretless static-plan result.
2. Manually run **Terraform Production Plan** on the exact current `master`,
   review its production-state plan, and copy its numeric run ID and the
   `sha256:` attestation digest from the run summary. The sensitive saved plan
   stays on the ephemeral runner; the uploaded one-day artifact contains only
   hashes, exact run/commit metadata, the provider-lock digest, and state
   lineage/serial.
3. Manually run **Terraform Apply** on `master`, supplying the ID as
   `production_plan_run_id` and the digest as
   `production_plan_artifact_digest`. The workflow verifies the exact protected
   run, attempt, commit, immutable artifact, and artifact digest. It then
   recomputes a saved plan and requires exact canonical plan, configuration,
   provider-lock, and state-identity equality before applying that saved file.
4. Send and verify the real provider-signed non-PHI Recall callback described
   above.
5. Copy the successful apply run's numeric ID from its Actions URL.
6. Manually run **Build & Publish (manual)** on `master`, supplying the ID as
   `terraform_apply_run_id` and checking
   `provider_signed_recall_callback_verified`. The workflow verifies the apply
   run, its ancestry, and that no later production-infrastructure change exists
   before it deploys the current immutable commit image.

Application-only pushes still run validation and build jobs, but they cannot
enter the production deploy job. The founder must manually dispatch **Build &
Publish (manual)** on the exact current `master`; the deploy first resolves the latest
successful Terraform Apply and performs the same ancestry/no-drift
verification. An unknown push comparison fails closed and requires the same
manual run.
Terraform Production Plan, Terraform Apply, and the production deploy share
one non-cancelling concurrency group, so a later application-only push queues
instead of mutating Container Apps while plan evidence is generated or an
apply is in flight.

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

Provider-only V7 staging pilot boundaries and operator checks are documented in
[`../docs/V7_PROVIDER_STAGING_PILOT.md`](../docs/V7_PROVIDER_STAGING_PILOT.md).
