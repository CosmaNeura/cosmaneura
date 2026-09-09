# Staging rehearsal base partial-apply evidence — 2026-09-04

Classification: non-PHI infrastructure metadata. This record contains no raw
state, credential, token, storage key, database value, or restored data.

## Disposition

An apply of the separately stateful `infra-staging-rehearsal/` reversible base
was stopped after repository policy was found to prohibit agents from invoking
Azure mutation commands. No later apply or Terraform state mutation was run.
The base is coherent but incomplete and does not authorize a restore.

- Reviewed source and plan base: Core `73256c24f26ebd68a7fb97443af92bdc8cbfe944`
- Original saved-plan SHA-256:
  `1e5690746621b553e1fc00ecfc43213a86daa126cff19ae62309c4c781e27228`
- Original actions: 39 creates, zero updates, zero deletes
- State lineage: `76b6712e-5781-adbd-9fb1-cf66db4f4e28`
- Post-interruption state serial: 7
- Protected serial-7 state snapshot observed at `2026-09-05T02:43:27Z`:
  SHA-256 `4291c5852310f7e17b4d85fb6b1dbc1aeb3bf1bb29bff8f4bb60319478566ce5`
- State instances: 28 total; 26 Azure objects and two `terraform_data`
- State lease at readback: available and unlocked
- Tainted instance: only `azurerm_storage_account.marker[0]`
- Rejected no-refresh plan SHA-256:
  `8454c51efdc14ef39590c525424f9e6945c99e3d41d880e24f6e08d847a7c00d`
- Rejected actions: eleven creates, 27 no-ops, and one storage-account
  delete/create replacement

Both saved plans are stale. Neither is authorized for apply. The no-refresh
plan is additionally destructive and must never be applied.

## Read-only live reconciliation

The following state-owned objects were confirmed live again at
`2026-09-05T02:56:50Z`:

- control and target resource groups;
- control VNet, exact `10.76.0.0/27` marker subnet, and inert undelegated
  `10.76.0.32/27` executor reservation;
- private Blob DNS zone and control-VNet link;
- four user-assigned identities;
- two exact GitHub environment federated credentials on dispatcher identities;
- five custom roles and five narrowly scoped assignments;
- the target-group firewall-rule deny policy definition and assignment; and
- the private `Standard_ZRS` marker storage account.

The target resource group returned exactly `[]` from `az resource list`. The
marker account reported `Succeeded`, public network disabled, Shared Key
disabled, Blob public access disabled, OAuth default enabled, HTTPS/TLS 1.2,
infrastructure encryption enabled, and network default `Deny` with bypass
`None`. No private endpoint was present.

Canonical readback projections were sorted, compacted, and hashed without
persisting credentials or data values:

- empty target-resource inventory SHA-256:
  `37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570`;
- control-resource inventory SHA-256:
  `7c270714ed4b2c0a2e30377479e2924887230a3e3266546c9f1259c6015eca8c`;
- allowlisted marker-account posture SHA-256:
  `040cf2bb0dd1b4a3a242bae7047cff59f6797d4ca0a5f6027c8cf2f527129fd9`;
  and
- empty private-endpoint inventory SHA-256:
  `37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570`.

The raw state remains access-controlled and untracked. These committed hashes
bind the observation without publishing state, principal IDs, or provider
material.

The exact missing instances are:

- `azapi_resource.marker_container["restore-cleanups-v3"]`
- `azapi_resource.marker_container["restore-cleanups-v4"]`
- `azapi_resource.marker_container["restore-evidence-v1"]`
- `azapi_resource.marker_container["restore-intents-v3"]`
- `azapi_resource.marker_container["restore-intents-v4"]`
- `azapi_resource.marker_container["restore-submissions-v3"]`
- `azapi_resource.marker_container["restore-submissions-v4"]`
- `azurerm_private_endpoint.marker[0]`
- `azurerm_role_assignment.marker_provision_cleanup[0]`
- `azurerm_role_assignment.marker_read["cleanup_intent"]`
- `azurerm_role_assignment.marker_read["cleanup_submission"]`

No container, marker-create assignment, legal hold, WORM policy, management
lock, executor delegation/foundation, compute, route, peering, restore, data
copy, migration, staging cutover, or production action occurred.

## Root cause and source correction

AzureRM 4.73.0 defaults `features.storage.data_plane_available` to `true`.
During refresh, `azurerm_storage_account` therefore attempted Queue/static-site
availability reads using a data-plane route that is intentionally unavailable:
the account rejects public traffic and Shared Key, and the operator host has no
private path. The provider returned `KeyBasedAuthenticationNotPermitted`.

The source correction sets `data_plane_available=false`. Exact provider source
confirms this bypasses Queue/static-site data-plane reads while retaining ARM
reads for the account and configured Blob-service properties. This root has no
Queue or static-site block, and its containers already use AzAPI. Merely setting
`storage_use_azuread=true` would change authentication without creating private
network reachability and is not the remedy.

The marker account also gains `prevent_destroy` so an interrupted apply cannot
turn taint into an incidental replacement. An intentional reversible-phase
decommission must first remove that guard in a separately reviewed source
change.

## 2026-09-09 serial-8 normalization addendum

The approved human untaint advanced the backend to serial 8. The protected
post-untaint snapshot has SHA-256
`4a62747650a8961cd48f716adc7f65abbed60997ecc7c00920c40346fec0a98b`,
retains the exact lineage and 28-instance inventory, and contains no tainted or
deposed instance.

The first normal full-refresh recovery plan was refused. Its desired resource
changes remained exactly 28 no-ops plus eleven creates, with zero updates,
deletes, or replacements, but the provider reported ten state normalizations:
one policy assignment, five role definitions, the marker storage account, two
subnets, and the control VNet. The storage normalization hydrates provider-
sensitive values that must never be persisted as plan JSON or submitted for
review. Neither `-refresh=false` nor a broader recovery envelope is permitted.

A bounded live diagnostic subsequently proved that a refresh-only plan can
contain exactly those ten normalizations, one non-secret output creation, and
zero `resource_changes`; it was not applied. The exact saved refresh plan still
requires the merged closed-world validator, a separately merged human-only
consumer, digest-bound independent review, and explicit human execution. Its
only permitted transition is state serial 8 to serial 9 with zero Azure
resource actions.

## Recovery acceptance

On 2026-09-09, the independently reviewed refresh-only plan was consumed by
the human operator. The protected backend advanced to serial 9 with the exact
same 28-instance inventory, no tainted or deposed instance, and the apply
reported zero resources added, changed, or destroyed. The protected serial-9
snapshot SHA-256 is
`8abb6c30c94614b0b701da63112f726742ab9eaef3f98782f1abcf51394bb363`.

The first serial-9 base-recovery plan (SHA-256
`2f16bcb6fdeddeca55987084511361f030c7bd5648435d7487461eb540f7ab36`)
was rejected during independent review and was never applied. The review found
that the validator needed exact executable snapshots, a closed process
environment, exact create-value/unknown-field contracts, and full semantic
equality between the protected and plan-embedded states. That plan is not an
authorized recovery artifact; generate a fresh plan only after the hardening
change is protected on `master`.

Follow `infra-staging-rehearsal/README.md` exactly. A human operator must own
both state transitions. Completion first requires the independently reviewed,
zero-resource-action refresh-only plan to advance state from serial 8 to serial
9. Only then may the full-refresh recovery validator create a plan bound to the
protected serial-9 snapshot and exact reviewed source. That later plan must
pass independent review with exactly 28 no-ops, eleven creates, zero drift,
zero updates, zero deletes, and zero replacements. A second digest-bound
validation against a fresh state pull is required immediately before each
human apply. Any other result stops recovery.
