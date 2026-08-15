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

The client is deliberately not connected to a staff route yet. The current platform has provider
ownership but not the canonical practice-tenancy model required by the architecture. Enabling a
human workflow before that mapping exists would incorrectly treat a doctor record as a practice.

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
6. Add the canonical practice mapping and staff-facing workflow, then enable the platform client.

Rollback disables the platform feature flag first. Never fall back to an unauthenticated call or a
long-lived credential.
