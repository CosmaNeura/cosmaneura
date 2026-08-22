# Private Scribe workflow client

The master platform can create Scribe consent evidence and capture sessions without changing human
login. Staff continue to use Clerk, patients continue to use the existing patient identity service,
and neither browser credential is forwarded to Scribe. The web Container App calls Scribe using a
dedicated user-assigned managed identity and a short-lived Entra application token.

## Boundary

The client sends only opaque appointment, practitioner, and patient references; modality; bounded
participant regions; and the consent evidence fields defined by the governance contract. It cannot
send a tenant or scheduling provider in the body. The practice tenant UUID is a required method
argument and travels in `X-Tenant-ID` after the platform has resolved it from authenticated server
context.

The client is connected only to the authenticated desktop-capture launch route. That route accepts
a bounded, fresh all-party consent attestation for the virtual-visit pilot; it does not accept
tenant, clinician, patient, appointment, consent-record, or capture-session authority in its body.
The server resolves those identifiers through the canonical appointment and attested practice
membership. The practice TypeID's UUID is the Scribe tenant UUID.

Before calling Scribe, the platform durably reserves an `awaiting_consent` capture session, an
exclusive appointment source lease, and an attempt-fenced operation. A competing launch key cannot
reach Scribe. A stale same-key attempt may reclaim the reservation and safely reuse the same
domain-separated Scribe idempotency keys; stale completions cannot overwrite the newer attempt.
Scribe sessions are explicitly created with `ingest_mode=external_async_transcript`. Deterministic
preparation failures close the lease only with never-started proof. Ambiguous outcomes retain an
uncertain session and lease, plus any bounded observed Scribe identifiers, for reconciliation.

If a launch ticket expires before PKCE begins, a new browser idempotency key may replace the
attempt only when the server proves that the original ready session has the exact same consent
binding and no authorization code, desktop token, segment, start operation, or external resource
ever existed. The old lease is first closed with durable never-started proof; only then may a new
session be reserved and sent to Scribe. A changed attestation or any evidence of start remains a
conflict.

Stale or ambiguous pre-Scribe reservations are recoverable by replaying the exact request with the
same browser idempotency key after its attempt lease expires. The server intentionally does not
persist the raw attestation or browser key, so the scheduled maintenance worker does not recreate
these Scribe POSTs. If the client never retries, the fenced reservation remains visible for health
monitoring and manual review rather than being guessed closed. Pilot clients must retain and retry
the same launch request until they receive a terminal response.

## Passwordless configuration

OpenTofu creates and attaches `azurerm_user_assigned_identity.scribe_workflow` to the web Container
App. In Entra, assign that identity's enterprise application the Scribe API application role
`Scribe.Workflow.Access` through Microsoft Graph using Azure CLI or Microsoft Graph PowerShell.
This is an application-role assignment, not an Azure subscription IAM role. Follow Microsoft's
[managed-identity app-role guide](https://learn.microsoft.com/entra/identity/managed-identities-azure-resources/assign-app-role-managed-identity-powershell).
Do not create a client secret.

The Container App receives these non-secret values:

```text
SCRIBE_WORKFLOW_ENABLED=false
SCRIBE_SERVICE_BASE_URL=https://<private-scribe-fqdn>
SCRIBE_SERVICE_SCOPE=api://<scribe-api-application-id>/.default
SCRIBE_MANAGED_IDENTITY_CLIENT_ID=<platform-scribe-workflow-identity-client-id>
SCRIBE_SERVICE_TIMEOUT_SECONDS=15
```

The platform requests the `.default` scope. Scribe validates the resulting token against the API
application client ID UUID, plus the exact platform client ID and role. The Scribe API app
registration must issue v2 access tokens and add `idtyp` as an access-token optional claim.

This outbound scope is intentionally distinct from the inbound handoff setting
`SCRIBE_API_AUDIENCE`, which must contain the master platform API application ID. Migrating the
outbound Entra registration means updating only `SCRIBE_SERVICE_SCOPE` while the workflow remains
disabled, validating the resulting token's Scribe audience and role, and then following the private
network rollout below. Never copy the Scribe API application ID into `SCRIBE_API_AUDIENCE`.

## Network prerequisite

The currently deployed platform Container Apps environment is not VNet-integrated, while Scribe is
inside an internal VNet-only environment. Identity alone therefore does not create a route between
them. Keep `enable_scribe_workflow = false` until a reviewed, non-destructive networking plan
connects the platform runtime to the Scribe VNet and private DNS. Do not make Scribe public as a
workaround.

The approved design is a false-by-default parallel East US environment with same-region VNet
peering and Scribe private-DNS resolution. Its implementation and review gates are in
[`PLATFORM_SCRIBE_NETWORK_PLAN.md`](PLATFORM_SCRIBE_NETWORK_PLAN.md). No foundation deployment,
workload clone, DNS change, or cutover is approved by that design.

## Rollout

1. Review the OpenTofu plan for the new platform managed identity and its attachment. Apply with
   the Scribe workflow feature flag off.
2. Configure the Scribe API app registration and application role, then assign it to the platform
   identity using the output client/principal IDs.
3. Establish private network reachability and DNS between the two Container Apps environments.
4. Enable Scribe's inbound verifier first. Confirm missing tokens return 401 and the wrong caller or
   role returns 403 using synthetic data only.
5. Run one synthetic consent/session request from the platform Container App and verify tenant
   isolation.
6. Verify the canonical practice mapping and staff-facing desktop workflow with synthetic data,
   then enable the platform client only through the reviewed release gate.

Rollback disables the platform feature flag first. Never fall back to an unauthenticated call or a
long-lived credential.
