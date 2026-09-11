# Staging restore private-vault foundation

Status: source implemented; 34 provider-mocked runs and nine focused source
tests pass; CI/independent review pending. No Azure apply, secret or read grant.
Risk: **Tier 2**, private security boundary and irreversible purge protection.
Dependency: the approved control/network foundation; independent of registry
activation. Initially stacked on the registry source for review sequencing.

## Deployable outcome

Model one empty Standard Key Vault in the East US control group, one `vault`
private endpoint in `10.76.0.64/27`, and `privatelink.vaultcore.azure.net` with
one non-registering control-VNet link. Require RBAC authorization, zero access
policies, public network disabled, default-deny network ACLs with no bypass or
IP/VNet exceptions, soft delete for 90 days and purge protection. Disable
deployment, disk-encryption and template-deployment access paths.

Create no secret, certificate, key, secret version, publisher or job role
assignment. The later exact-version root/HMAC publication and per-secret job
read scopes remain separate reviewed work. The HMAC reader is provision-only;
the GitHub trust-root readers are the two exact unfederated job UAMIs. No
vault-wide Secrets User grant or broad dispatcher grant substitutes for them.
No retention, marker write, compute, restore, target resource, workflow or
deployment is activated. All runtime and aggregate binding gates remain false.

## Provider-fidelity decision

AzureRM 4.73.0's [pinned Key Vault read implementation](https://github.com/hashicorp/terraform-provider-azurerm/blob/v4.73.0/internal/services/keyvault/key_vault_resource.go#L770)
calls `GetCertificateContacts` even without configured contacts and when public
access is disabled; failures may be ignored but the private data-plane call
still occurs. It also has implicit soft-deleted-vault recovery behavior.

Use the already pinned AzAPI 2.1.0 resource
`Microsoft.KeyVault/vaults@2023-07-01`, with `createMode="default"`, an exact
ARM-only body and only the non-secret `properties.vaultUri` exported. Do not
use a contact/secret/certificate resource, provider upgrade, private data-plane
probe, implicit recovery or purge. Soft-deleted/global name availability is
an explicit pre-apply check; conflicts stop the plan rather than recovering.

## Guards and validation

- All creation inputs default false. Require paired vault-enable/exact-plan
  approval, separate explicit irreversible-purge-protection approval, the
  approved network foundation, base and both fresh CIDR revalidations.
- Prove default/base/network/registry-only dormancy and all missing-approval/
  prerequisite failures with provider mocks. Assert exact ARM body, endpoint,
  DNS/link, empty access policies, no secret or grant, unchanged runtime gates.
- Extend source inventory tests to allow only this exact gated ARM vault;
  keep unimplemented compute/routing/public resources prohibited.
- Run recursive format/validate/all mock tests, focused source tests and lint,
  full mandatory CI, change budget and exact-final-push independent review.
- After merge, prepare a fresh state/connected-network/name/soft-deleted-name/
  provider-availability/subnet-capacity capture and independently reviewed plan.
  The operator must separately accept 90-day irreversible purge protection.

## Live acceptance and recovery

No agent apply. Do not start a short-lived human handoff during an absence.
After approved human apply, read back exact tenant, mode, empty policies,
private endpoint/NIC/DNS graph, all disabled public/deployment paths, purge
protection and target emptiness. Source outputs are planned identifiers only,
not proof of private GET access, exact secret versions or effective job grants.

Before creation, hold/revert source with flags false. After creation, preserve
the vault with `prevent_destroy` and reconcile by reviewed roll-forward.
Purge protection cannot be turned off and a deleted vault cannot be purged
early during retention; do not call this reversible infrastructure or treat
flag removal as rollback. No secret material enters Terraform state here.

## Local validation record

OpenTofu 1.12.5, locked AzureRM 4.73.0/AzAPI 2.1.0: configuration validation
and all 34 provider-mocked runs pass with no backend or Azure credentials.
Twelve new vault runs cover default-off approval/prerequisite failures, name
validation and the exact empty private ARM body/endpoint/DNS contract; prior
base/network/registry/retention tests remain green. All nine focused network,
registry and vault source tests plus Ruff lint/format checks pass. These checks
do not establish live provider acceptance, secret material or authorization.
