# Flight plan: move Core DeID calls to a workload identity

## Outcome and boundaries

- Problem and measurable outcome: the production Celery worker currently calls
  Azure DeID with a shared app-registration secret. Move that call to the
  existing `cosmaneura-prod-health-services` user-assigned identity, grant only
  `DeID Realtime Data User` on the exact `AnonymizerAPI` resource, and then
  retire the shared credential after a measured observation window.
- In scope: the Core worker credential selection, its Container App identity,
  the one DeID role assignment, a read-only activation preflight, tests, and
  the later removal of the legacy credential from this workload.
- Explicit non-goals: applying infrastructure, deploying an image, mutating
  Azure or 1Password, changing the DeID service or patient data, and guessing
  at staging resources.
- Affected surface: production transcript de-identification by the Celery
  worker. De-identified output behavior and provider-facing note behavior do
  not change.

## Risk and dependencies

- Tier: Tier 2 because this changes production workload identity on a PHI
  processing path.
- High-blast-radius decision: enabling the new mode before the exact identity,
  attachment, and RBAC scope exist would stop transcript de-identification.
- Dependency order: merge the default-off compatibility code and declarative
  identity prerequisites; obtain an attested production plan; apply only the
  prerequisite rung; deploy the compatible image while legacy auth remains
  selected; run synthetic checks; then activate managed identity in a separate
  reviewed change. Each rung can be held indefinitely.
- Cross-repo ordering: N/A for production Core. Staging is blocked pending
  discovery of its distinct DeID service, worker identity, and synthetic-only
  validation path; production identifiers must never be copied into staging.

## Current deployed state

- Review baseline: `master` commit
  `c2e33419ea2304b5c083adf1ae147664484dc9c2` on 2026-08-26.
- Read-only Azure inspection found the production worker with no managed
  identity and the existing health-services identity unattached. That identity
  holds `DeID Batch Data Owner` at the container registry, a scope and action
  set unrelated to the realtime API call.
- `AnonymizerAPI` is an existing
  `Microsoft.HealthDataAIServices/deidServices` resource in the production
  resource group. The legacy application identity has broader
  subscription-scoped DeID access and active client secrets.
- That same application registration is also the configured Scribe and Agent
  API audience. Retiring its DeID secrets and Azure RBAC must not delete the
  registration or its application roles/audience contract.
- Reconciliation decision: replace the ineffective ACR role assignment with a
  realtime-only assignment on the exact DeID resource and attach the existing
  identity only to the worker. Retain the client secret temporarily for an
  explicit rollback window.
- Previous known-good boundary: the current client-secret path remains selected
  by default and is not removed in the prerequisite rung.

## Conditional controls

- `enable_deid_managed_identity` is an infrastructure rollout control, not a
  user feature flag. Its stable default is `false`; only the founder may approve
  changing the source-controlled default in the activation PR.
- `false` selects `client_secret`; `true` selects `managed_identity`. Managed
  mode pins the exact user-assigned client ID and does not fall through to an
  ambient developer, CLI, or service-principal credential.
- Removal condition: remove the control and legacy code only after the
  observation and credential-revocation rungs complete.
- Schema/data sequence: N/A; this changes no schema or stored data.

## Rollout and promotion

| Rung | Change and hold point | Required evidence | Recovery |
| --- | --- | --- | --- |
| 0 | Merge compatibility code with the control default-off. Do not deploy or apply from the feature branch. | Focused Python tests, OpenTofu format/validate, provider-mocked plans, independent review of the final commit. | Revert the PR; deployed behavior is unchanged. |
| 1 | From exact `master`, run the protected production plan, then complete the separately privileged RBAC bootstrap and state reconciliation below. The ordinary production workflow cannot write `Microsoft.Authorization` role assignments. Its final apply may attach the UAI and set the default-off environment only after the role replacement is adoption-only. Keep `client_secret` selected. | Pre/post state snapshots; privileged operator and expiry; exact old/new assignment IDs; an attested final plan with no RBAC create/delete and no unrelated replacement; post-apply live configuration. | Before the final apply, remove the newly created exact-scope assignment and restore the protected state snapshot if reconciliation cannot finish. After attachment, apply the previous attested configuration; the legacy credential remains usable. |
| 2 | Deploy the compatible image while `client_secret` remains selected. Record its exact 40-character commit tag and registry manifest digest. | Healthy worker revision and one synthetic, non-PHI realtime DeID request with expected output and metadata-only success logs that identify `client_secret` mode. | Redeploy the previous known-good digest. |
| 3 | In a separate reviewed PR, set `enable_deid_managed_identity=true` and set `deid_managed_identity_compatible_worker_image_tag` to the exact tag accepted in rung 2; obtain a fresh protected plan. Its read-only preflight must verify the exact UAI ID, DeID resource ID and endpoint, worker attachment, client-ID pin, active healthy image tag, and effective realtime role before planning the switch. | Successful plan/apply, healthy worker, synthetic success with `managed_identity` credential-mode telemetry, and no credential-fallback or authorization failures. | Set the control false through the protected plan/apply path while the legacy secret is retained. |
| 4 | Observe for at least one normal de-identification cycle and 24 hours without authorization failures. Inventory every consumer of the legacy app registration. | Success/error counts only; no transcript, note, token, endpoint query, or patient identifiers in evidence. | Return to rung 3 rollback while the credential remains valid. |
| 5 | Revoke both active legacy secrets, remove the expired credential, remove the legacy subscription-wide DeID role, and then remove old secret injection/configuration in a separate reviewed cleanup. Preserve the app registration because it also defines API audience and application-role contracts. | Secret consumer inventory is empty, managed path remains healthy, and Azure/1Password/runtime checks show no legacy credential. | Create a new narrowly scoped emergency credential only through an approved incident change; never restore subscription-wide access as routine rollback. |

- Abort on any unexpected plan action, identity mismatch, role broader than
  realtime user, role scope other than the exact DeID resource, failed
  synthetic request, worker restart loop, or authorization error.
- Use synthetic text only. Do not place transcripts, notes, PHI, credential
  values, or raw provider errors in test or change evidence.
- Production plan, apply, and deployment remain founder-only and manual under
  the repository deployment doctrine.

## Rung 1 privileged RBAC bootstrap and state reconciliation

The protected production workflow authenticates as `cosmaneura-gha-prod`. Its
resource-group Contributor and deployment roles permit resource reads and
writes but explicitly do not permit `Microsoft.Authorization/*/Write` or
`Microsoft.Authorization/*/Delete`. It therefore cannot replace the current
Terraform-tracked ACR-scoped role assignment. Do not discover this during an
apply and do not temporarily grant the workflow subscription Owner.

Use a separately named, strongly authenticated privileged operator for one
change window. Record the ticket, operator object ID, start/expiry time, exact
source commit, and all value-free command results. Before any state operation,
save an access-restricted `tofu state pull` snapshot. Then:

1. Read the health-services identity and exact `AnonymizerAPI` resource IDs.
   Inventory all of that identity's Azure role assignments with a privileged
   subscription-wide read and record only assignment IDs, roles, and scopes.
2. Create `DeID Realtime Data User` for that identity on the exact DeID resource
   using `--assignee-object-id` and
   `--assignee-principal-type ServicePrincipal`. Verify its assignment ID,
   principal, role, and scope before continuing.
3. Remove `azurerm_role_assignment.health_services_deid_access` from state only;
   import the verified new assignment ID at that same address. These are state
   reconciliation operations, not an Azure apply.
4. Delete the obsolete ACR-scoped `DeID Batch Data Owner` assignment by its
   previously recorded exact assignment ID. Re-run the privileged full
   assignment inventory and require only the intended DeID access.
5. Run a fresh protected production plan. It must show no role-assignment
   create, delete, or replacement. Only then may the ordinary workflow apply
   the reviewed worker identity attachment and default-off environment.
6. End the temporary privileged session/eligibility and retain the state
   snapshots, assignment metadata, plan, operator, and timestamps with the
   change record.

Abort on an unexpected principal, role, scope, assignment count, state address,
or plan action. If failure occurs before the final apply, delete only the newly
created exact-scope assignment by its verified ID and restore the protected
state snapshot; do not guess at state or role targets.

## Security and recovery

- The activation guard is read-only and value-free. When activation is off it
  performs no Azure call, so pull-request tests remain secretless. When on, its
  role query is scoped to the exact DeID resource and includes inherited
  assignments, which the resource-group-scoped production runner can read. A
  separate privileged full inventory in rung 1 proves there is no unrelated
  assignment elsewhere in the subscription.
- Managed mode uses `ManagedIdentityCredential` with the exact client ID. It
  validates that ID as a canonical UUID at startup and intentionally has no
  automatic client-secret fallback.
- The guard requires the live healthy worker revision to run the exact reviewed
  compatible commit tag and compares the configured DeID endpoint with the
  live `AnonymizerAPI` service URL. Metadata-only request logs identify the
  selected credential mode so the synthetic activation check cannot succeed
  silently through the retained client secret.
- The legacy secret is a temporary rollback control, not the finished state.
  Its revocation is blocked until consumer inventory confirms that no other
  workload depends on the shared registration.
- No production mutation or data change is authorized by this document.

## Completion

- The worker runs with the exact health-services identity and succeeds against
  `AnonymizerAPI` using only `DeID Realtime Data User` at that resource.
- The shared application secrets and subscription-scoped DeID assignment are
  gone, and the worker no longer receives legacy DeID credential values.
- Staging has its own reviewed identity/resource design and synthetic evidence,
  or remains explicitly blocked and disabled.
- The final substantive commit has independent agent review tied to that exact
  commit, with findings and dispositions recorded in the PR.
