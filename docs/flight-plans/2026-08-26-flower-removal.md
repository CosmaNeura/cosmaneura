# Flight plan: permanently remove the Flower job monitors

## Outcome and boundaries

- Problem and measurable outcome: production and staging retain disabled
  Flower Container Apps after emergency containment. Remove the resources,
  their obsolete credentials and deployment references so a later deployment
  cannot make the administrative UI public again.
- In scope: the production `job-monitor` Container App, its GitHub deployment
  identity role assignment, its generated password, the unmanaged staging
  `job-monitor` Container App, the obsolete staging 1Password item, and all
  source/deployment paths that refer to Flower.
- Explicit non-goals: changing the API, worker or scheduler runtime; changing
  Redis or database credentials; replacing operational telemetry; deploying a
  new application image; or restoring Flower under a different hostname.
- Affected users, services, environments and data classes: platform operators
  lose an already-disabled administrative UI. Provider workflows and clinical
  data are not in the deletion set.

## Risk and dependencies

- Tier: Tier 2 because the rollout deletes production infrastructure and a
  capability-bearing credential.
- The production deletion must use the protected plan/apply workflows on the
  exact merged `master` commit. A full plan that contains any unreviewed change
  is an abort condition, even though the local targeted plan contains only the
  three expected destroys.
- Staging has split ownership: its obsolete 1Password login remains in staging
  OpenTofu state, while the disabled Container App is present in Azure but not
  in that state. Treat these as two independently verified deletion steps.
- Cross-repository ordering: N/A. Observability replacement is an operational
  follow-up and does not require this administrative surface to remain.

## Current deployed state

- Source baseline: `master` at
  `c2e33419ea2304b5c083adf1ae147664484dc9c2`.
- Verified 2026-08-26: production and staging job monitors have no ingress or
  FQDN, minimum replicas are zero, and no active revision is returned.
- Production state still contains exactly:
  `azurerm_container_app.job-monitor`,
  `azurerm_role_assignment.github_container_app_2_operator`, and
  `random_password.job_monitor_flower`.
- A refresh-disabled targeted production plan contains 0 additions, 0 changes
  and exactly those 3 deletions.
- Staging state contains `onepassword_item.job_monitor_login`, but not its live
  Azure Container App. The staging credential plan must be regenerated with an
  authorized 1Password service-account session.
- Reconciliation decision: delete the disabled residual resources and obsolete
  credentials. Do not re-enable or re-import Flower.

## Feature flag / schema/data sequence

- Feature flag: N/A; a security boundary must not depend on a user-visible
  feature flag.
- Schema/data change: N/A. No database or patient-data mutation is involved.

## Rollout and promotion

1. Obtain independent review of the exact source commit and merge it through a
   pull request. Confirm CI, the secretless infrastructure plan and the job
   monitor regression tests pass.
2. Reverify both live Container Apps remain non-routable, at zero minimum
   replicas and without active revisions. Abort if either serves traffic.
3. Manually run `Terraform Production Plan` from the exact current `master`.
   Review the complete plan, not only its summary. Continue only if the Flower
   resources are the intended deletions and every other change is independently
   approved.
4. Manually run `Terraform Apply` with the reviewed plan run ID and attestation
   digest. Do not run an application deployment merely for this removal.
5. Initialize the staging stack with its approved backend and 1Password
   service-account identity, save and review a plan that removes only
   `onepassword_item.job_monitor_login`, then apply that saved plan.
6. Delete the contained, unmanaged staging Container App using its exact
   resource group and name after a second read confirms it is still inactive.
7. Verify both Azure resources return `ResourceNotFound`, neither state retains
   Flower objects, no deployment script names `app-jmon`, and API, worker,
   scheduler and queue telemetry remain healthy.

Hold points are after the PR, after each saved plan, and between production and
staging deletion. Abort for unexpected plan actions, active monitor traffic,
failed worker/scheduler health, state-lineage drift or missing authorization.

## Security and recovery

- The current containment is the safe holding state. If a deletion step fails,
  stop and leave the other contained resource disabled while state is
  reconciled; do not re-enable ingress or scale.
- Capture owner-only pre-change state snapshots and the safe plan metadata
  required by the protected workflows. Never retain or publish plan binaries,
  state files, environment dumps or credential values in GitHub artifacts.
- There is no data rollback. The monitors are stateless and already inactive.
  If an unforeseen operational dependency is discovered, recover monitoring
  through Azure/Celery telemetry. Recreating Flower requires a new reviewed
  design with private access and a new credential; the deleted passwords must
  not be restored.
- Observe API, worker and scheduler health plus queue backlog for at least 30
  minutes after production deletion before staging cleanup.

## Completion

- Both Container Apps and all three credential/state objects are absent.
- Source, deploy scripts and dependency locks cannot recreate or invoke Flower.
- Negative tests pass and the post-change Azure/state checks are retained in
  the change record without secret-bearing output.
- Follow-up cleanup: remove the transitional `hashicorp/random` provider and
  lock entry in a separate reviewed PR after the production destroy has been
  successfully recorded.
