# Private Scribe workflow client

The master platform can create Scribe consent evidence, ordinary chunk-upload sessions, and dormant
external-transcript sessions without changing human login. Staff continue to use Clerk, patients
continue to use the existing patient identity service, and neither browser credential is forwarded
to Scribe. The web Container App calls Scribe using a dedicated user-assigned managed identity and
a short-lived Entra application token.

## Boundary

The client sends only opaque appointment, practitioner, and patient references; modality; bounded
participant regions; and the consent evidence fields defined by the governance contract. It cannot
send a tenant or scheduling provider in the body. The practice tenant UUID is a required method
argument and travels in `X-Tenant-ID` after the platform has resolved it from authenticated server
context.

The client is deliberately not connected to a staff route yet. The current platform has provider
ownership but not the canonical practice-tenancy model required by the architecture. Enabling a
human workflow before that mapping exists would incorrectly treat a doctor record as a practice.

## Dormant external transcript session contract

`create_external_transcript_session` calls `POST /v1/external-transcript-sessions` through the same
managed identity, scope, verified `X-Tenant-ID`, and idempotency boundary as the existing workflow
methods. Its request contains only opaque appointment, practitioner, patient, consent-record, and
modality values. The model rejects tenant, ingest-mode, name, date-of-birth, transcript, scheduling
provider, and all other unknown fields.

Scribe, not Core or a capture client, selects `ingest_mode=external_async_transcript`. Core requires
that exact mode in a successful response; a missing or different mode is treated as an invalid
remote response. Ordinary `POST /v1/sessions` remains pinned to `chunk_upload`, while its response
model accepts an omitted mode from the older receiver for rolling compatibility.

An opaque Scribe admission `404` is a rejected workflow call, not a reason to challenge or replace
the user's Clerk session. The method is not called by any route, job, dashboard, desktop, browser,
meeting-bot, or mobile code in this packet. It adds no third authentication path and cannot create
a Scribe session under repository defaults.

The shared workflow contract v1.1 is proposed in
`CosmaNeura/cosmaneura-architecture#12`; the dark receiver is
`CosmaNeura/cosmaneura-scribe#64`. Both are blocking dependencies before this method can be used.
Later Core work must establish a canonical practice mapping, founder-issued beta grant,
practice-authorized membership, encounter access, immutable consent, and creator-only visibility
before adding a user-triggered call site.

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
SCRIBE_SERVICE_TIMEOUT_SECONDS=60
```

The platform requests the `.default` scope. Scribe validates the resulting token against the API
application client ID UUID, plus the exact platform client ID and role. The Scribe API app
registration must issue v2 access tokens and add `idtyp` as an access-token optional claim.

The Terraform default owns the deployed Container App environment value. The matching application
default covers local and other non-Terraform entry points; both deliberately default to 60 seconds,
and Terraform rejects values above 60. This allows one Scribe scale-from-zero start without adding
an automatic mutation retry. If a timeout leaves success unknown, the caller must replay the
byte-identical request with its original idempotency key.

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
6. Add the canonical practice mapping, beta grant, encounter authorization, and creator-only
   visibility before connecting a staff-facing external-transcript workflow.
7. Activate only the separately approved synthetic cohort; do not infer that enabling the ordinary
   workflow client also authorizes external-transcript admission.

Rollback disables the platform feature flag first. Never fall back to an unauthenticated call or a
long-lived credential.
