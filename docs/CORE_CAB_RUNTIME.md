# Core CAB runtime configuration

The billing button uses the existing provider session and a separate server-to-server channel. This wiring is disabled by default. It creates no identity, assigns no role, admits no source and performs no deployment.

## Operator inputs

Edit the exact target's .env.staging.backend or .env.production.backend in a reviewed source change. Defaults overwrite ambient shell values; do not put patient identifiers or policy contents in the env file. There is no new browser flag or workload token.

| Runtime input | Required value |
| --- | --- |
| CORE_CAB_AUTHORIZE_ENABLED | false until the dedicated incoming callback policy is complete |
| CORE_CAB_AUTHORIZE_TENANT_ID | Existing Entra tenant UUID |
| CORE_CAB_AUTHORIZE_AUDIENCE | Existing dedicated Core authorizer API audience UUID |
| CORE_CAB_AUTHORIZE_CALLER_ID | Exact existing CAB workload client UUID; assigned Core.CabOperation.Authorize on that audience |
| CORE_CAB_BRIDGE_ENABLED | false until both directions and the receiver are ready |
| CORE_CAB_ASYNC_ENABLED | false until the durable async receiver and Core action expand are accepted; true requires the bridge and authorizer |
| CORE_CAB_CONTINUATION_ENABLED | false until explicit continuation is accepted; true requires async, bridge and authorizer |
| CORE_CAB_ORIGIN | Fixed CAB workload HTTPS DNS origin, without path/query/userinfo, port443 or omitted |
| CORE_CAB_AUDIENCE | Existing CAB workload API audience UUID; Core requests api://AUDIENCE/.default |
| CORE_CAB_MANAGED_IDENTITY_CLIENT_ID | Explicit existing user-assigned identity available to this Core API runtime and allowed by CAB |
| BILLING_SOURCE_POLICY_JSON | Exact private source policy, read only from the target environment's selected vault item |

CORE_CAB_SOURCE_POLICY_ITEM_ID is a deployment-only selector, not a tenth runtime permission. Leave it empty while no cohort is admitted. After an operator creates/reviews the environment-local item, set its exact item ID/name (letters, digits, dot, underscore or hyphen); its policy-json field supplies the document. Production uses only OP_PROD_VAULT_UID, staging only OP_STAGING_VAULT_UID. Empty selection makes no optional vault request. JSON is captured as data, never evaluated as shell code.

The document has exactly schemaVersion (core.billing-source-policy.v1), Boolean enabled, clerkIssuer, and admissions. Each admission contains exactly admissionId, practiceId, doctorId, appointmentId, patientId, noteId, contentSha256, dataClass, reviewRef, expiresAt. Use actual Core IDs and the ordered-section content hash from the current source contract, not the projected-text hash. Classification is synthetic or deidentified; no generic operational/PHI class is accepted. An enabled policy's issuer must equal the existing target Clerk issuer derived from its protected publishable key. At most 128 distinct exact notes are admitted; enabled entries must have future timezone-aware expiry. Keep the document and review record private.

## Release order

1. Record current Core/CAB source, image, configuration and schema; select reviewed immutable target artifacts. Receiver first: configure the CAB-only workload receiver, explicit Core identity/practice/source projection, and separate model admission if Generate is required.
2. Reconcile actual Core Alembic ancestry, then apply the required expands through the existing authorized migration path: 16a6bf2d1539 → f5efcbb27acd → 211aefdf709b for preparation/actions, then 4c5b17148cff → 56789df3c14c for async/continuation. Start at the actual supported head; do not repeat earlier rungs or stamp past unknown migrations. These are additive records with no backfill. Local synthetic dump/restore evidence is not hosted schema or backup evidence.
3. Deploy code with the defaults off. This PR adds no identity attachment or Entra registration. The selected existing managed identity must already be attached/available under its own reviewed identity change; do not silently reuse Scribe, Session Prep or a browser identity. The callback is /internal/cab/v1/operations/{operationId}/authorize and requires app-only tokens, not Clerk tokens.
4. Supply the exact two-way settings and reviewed target-local source policy in a separate activation change. Core startup captures configuration; a normal runtime revision is required. Release the compatible CAB receiver before enabling async or continuation. Continuation requires explicit provider consent, remains within the original 360-second/source-admission deadline, and cannot resume lapsed authority. Status is read-only and cannot renew authority or start another inference. Source review can be staged before enabling the transport. Never infer model approval from a configured read/storage channel.
5. Keep existing protected deployment and configuration-preservation checks. New API values/policy bytes participate in staging's effective fingerprint; an older release fingerprint cannot authorize the changed configuration. Bind a new approved configuration record rather than bypassing preservation. Verify the first admitted provider flow and an unavailable-source control in the authorized target.

OpenTofu owns production API settings. Staging's deployer projects the same settings only to its API and reconciles stale references on other workloads. The policy is a secret reference, excluded from public config, workers, scheduler and migration job. No new settings are baked into frontend assets. Existing protected state/encrypted-secret handling is unchanged.

## Recovery and local evidence

For a full channel shutdown, set authorizer, bridge, async and continuation flags false and clear the policy item selector (or revoke its exact admissions in a reviewed policy change). To stop continuation alone, set its flag false; to disable async also set continuation false. Redeploy through the normal control path. References and an unowned staging policy secret are removed only after workload reconciliation; immutable Core source/action/continuation records and CAB headers remain. Preserved-configuration promotions never delete the secret because retained revisions may still reference it. Explicit revocation is a separate reviewed configuration change, not an image-only cleanup. Do not drop expands or delete historical evidence.

The standard-library scripts/core_cab_runtime_config.py validates the resolved environment, or JSON on stdin with --stdin, and prints no source or credential values. Both protected backend templates run it; the staging deployer repeats it before provider work. Local tests inject synthetic JSON/fake vault and Azure functions; they do not authenticate against a vendor or prove hosted identity readiness. Existing source/action database and end-to-end synthetic browser proofs are retained separately.
