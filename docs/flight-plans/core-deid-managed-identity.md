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
| 1 | From exact `master`, run the protected production plan and apply only the UAI attachment plus exact-resource realtime role replacement. Keep `client_secret` selected. | Attested plan; no unrelated replacement; read-only live preflight reports five checks verified. | Apply the previous attested configuration; the legacy credential remains usable. |
| 2 | Deploy the compatible image while `client_secret` remains selected. | Healthy worker revision and one synthetic, non-PHI realtime DeID request with expected output and metadata-only success logs. | Redeploy the previous known-good digest. |
| 3 | In a separate reviewed PR, set `enable_deid_managed_identity=true`; obtain a fresh protected plan. Its read-only preflight must verify the exact UAI ID, DeID resource ID, worker attachment, client-ID pin, and realtime role before planning the switch. | Successful plan/apply, healthy worker, synthetic success, and no credential-fallback or authorization failures. | Set the control false through the protected plan/apply path while the legacy secret is retained. |
| 4 | Observe for at least one normal de-identification cycle and 24 hours without authorization failures. Inventory every consumer of the legacy app registration. | Success/error counts only; no transcript, note, token, endpoint query, or patient identifiers in evidence. | Return to rung 3 rollback while the credential remains valid. |
| 5 | Revoke both active legacy secrets, remove the expired credential, remove the legacy subscription-wide DeID role, and then remove old secret injection/configuration in a separate reviewed cleanup. Preserve the app registration because it also defines API audience and application-role contracts. | Secret consumer inventory is empty, managed path remains healthy, and Azure/1Password/runtime checks show no legacy credential. | Create a new narrowly scoped emergency credential only through an approved incident change; never restore subscription-wide access as routine rollback. |

- Abort on any unexpected plan action, identity mismatch, role broader than
  realtime user, role scope other than the exact DeID resource, failed
  synthetic request, worker restart loop, or authorization error.
- Use synthetic text only. Do not place transcripts, notes, PHI, credential
  values, or raw provider errors in test or change evidence.
- Production plan, apply, and deployment remain founder-only and manual under
  the repository deployment doctrine.

## Security and recovery

- The activation guard is read-only and value-free. When activation is off it
  performs no Azure call, so pull-request tests remain secretless.
- Managed mode uses `ManagedIdentityCredential` with the exact client ID. It
  intentionally has no automatic client-secret fallback.
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
