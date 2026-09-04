# Staging restore rehearsal control plane

Status: **dormant source only**. No Azure or GitHub resource has been created by
this root. `deploy_staging_restore_control_plane` defaults to `false`, and the
restore workflow does not consume these outputs.

This root owns a state boundary separate from both `infra/` and
`infra-staging/`: `staging-rehearsal-control-plane.tfstate`. It models the
persistent, non-PHI prerequisites for the isolated restore controller:

- a persistent East US control resource group;
- the otherwise empty North Central US target resource group expected by the
  envelope controller;
- separate provision and cleanup dispatcher UAMIs with exact GitHub
  environment-subject federated credentials, plus unfederated provision and
  cleanup job UAMIs reserved for a later private executor;
- read-only subscription access for the dispatchers, with source-server,
  target-group, and marker-container roles assigned only to job UAMIs;
- a target-group policy that denies PostgreSQL Flexible Server firewall-rule
  resources;
- a private `StorageV2` `Standard_ZRS` marker account, six ARM-created private
  marker containers (three retained v3 containers and three v4 containers), a
  reserved aggregate-only `restore-evidence-v1` container, private Blob
  endpoint, private DNS zone, and VNet link; and
- an unpeered `10.76.0.0/24` control VNet with a `10.76.0.0/27` marker endpoint
  subnet and an inert `10.76.0.32/27` executor reservation; and
- an independently default-off private-executor network foundation that
  delegates `10.76.0.32/27` only to `Microsoft.App/environments` and reserves
  `10.76.0.64/27` only for later ACR, Key Vault, and per-rehearsal PostgreSQL
  private endpoints.

The target restore network remains controller-owned and ephemeral. Its contract
is `10.75.0.0/24` with `10.75.0.0/27` for the PostgreSQL private endpoint. This
root does not place an ordinary resource in the target group, because
`staging_restore_envelope.py` requires `az resource list --resource-group
cosmaneura-staging-rehearsal` to be empty before a restore. The policy and role
assignments are target-scoped extension resources. A credentialed readback must
still prove that the exact controller command returns `[]` before activation.

## Private-executor network foundation

The network-only rung is present in source but defaults off. It requires the
base control plane, both action-time CIDR revalidations, and two matching
switches in an independently accepted saved plan:

```hcl
enable_private_executor_network_foundation = true
private_executor_network_plan_approved     = true
```

When enabled, the plan changes the existing empty `10.76.0.32/27` reservation
only by adding the exact Container Apps environment delegation. It adds one
empty `10.76.0.64/27` subnet with private-endpoint network policies disabled.
Default outbound access is disabled on all three control subnets. The third
subnet is reserved for a future digest-pinned ACR, private Key Vault, and one
second PostgreSQL private endpoint per rehearsal so an executor never needs a
VNet peer or public route to the ephemeral `10.75.0.0/24` target.

This rung deliberately creates no Container Apps environment or job, ACR, Key
Vault, PostgreSQL endpoint, DNS zone beyond the existing marker Blob zone, NAT
gateway, route table, NSG, public IP, peering, secret, credential, or workflow
binding. Those are separate reviewed slices. A provider-mocked plan proves the
HCL shape only; live provider validation must still prove that Azure accepts a
`/27` workload-profile infrastructure subnet and the exact delegation action.

## Dormant restore runtime binding v1

`restore-runtime-binding-policy.json` and
`validate_staging_restore_runtime_binding.py` define and validate the future
private-runtime boundary; see the [runtime-binding flight
plan](../docs/flight-plans/2026-09-04-staging-restore-runtime-binding.md).
All nineteen activation assertions remain false and executable validation refuses.

- Federated dispatchers may later read/start only their exact jobs through an
  enforced no-override request; `jobs/start/action` RBAC alone is insufficient.
  Their current subscription `control_reader` assignments must be removed or
  re-homed. Only unfederated job UAMIs attach to compute. Provision's fourth
  cleanup-marker create assignment is now modeled behind the dormant gates;
  neither tombstone create grant precedes exact private-network proof,
  approved legal hold, locked WORM, and provider create-only proof.
- The checked-in policy excludes resulting image/record/signature digests. A
  post-publication signed record binds the image and pre-binding job templates.
  All four fields in exact provision/cleanup job-configuration pointers are compared field-for-field before record validation. Its separately reviewed
  create-only publisher authority is not live-bound or configured here. Both jobs use exact
  UAMIs to pull that digest only from private ACR; tags, admin/password,
  anonymous, and ambient/default credentials are barred.
- Both jobs read digest-in-path record/signature objects from locked, create-only
  private `restore-runtime-bindings-v1`; provision alone reads versioned evidence
  from `restore-evidence-v1` in an account distinct from markers, plus the HMAC key. Both read the
  exact root from private Key Vault. Egress allows only MI, ARM, evidence/binding/
  marker Blob, Key Vault, PostgreSQL, and VNet DNS; provision runs two offline
  `gh` checks and cleanup one under network and PID/cgroup containment.
- A second PostgreSQL private endpoint in `10.76.0.64/27` plus private DNS is only
  the target network path; authenticated v2 receipt and provision-UAMI Entra DB auth remain
  required. Cleanup validates record/signature, authenticates receipt/ownership, observes/creates and reads back the
  tombstone, deletes only receipt-bound resources, then proves all absent; shared subnets remain.

This contract/validator PR activates no workflow, provisions no compute, runs
no Azure apply, publishes no image, restores or mutates no data, performs no
cutover, and authorizes no production/PHI action. It contains no live evidence.

## Deliberate blockers

The optionally delegated `10.76.0.32/27` subnet is not a runner, Container Apps
environment, route, peer, or proof of connectivity to an ephemeral
`10.75.0.0/24` target. The empty `10.76.0.64/27` subnet is not an endpoint.
`private_executor_binding_implemented` and
`trusted_evidence_binding_implemented` remain hard-coded `false`; a separate
reviewed rung must create the runtime and private services, attach the
unfederated job UAMIs, bind the aggregate-only evidence sink, add only the
required DNS, and prove private DNS and data-plane reachability before any
workflow integration or ReadOnly account lock.

The controller currently validates federation through `az ad app
federated-credential list`, while this root deliberately creates UAMI-bound
federated credentials through Azure Resource Manager. Activation must first
prove that query against these identities or change the controller to verify
the UAMI ARM resource directly. Do not add Graph permissions merely to disguise
an identity-model mismatch.

The OIDC-federated identities are dispatcher/control-plane identities, not
private-executor or data-plane identities. Only the separate job UAMIs receive
source, target, or marker roles, and those UAMIs have no federated credentials.
They are not usable by a workload until the later executor rung attaches them.

The minimum Blob permission for conditional BlockBlob creation is intentionally
unsettled. The known read-only role is modeled immediately. The create/read role
and its three ordinary marker-container-scoped assignments remain absent unless
`enable_marker_create_assignments=true`,
`marker_create_authorization_proven=true`, and exactly `read` plus one of `add`
or `write` is supplied from live provider-fidelity evidence that demonstrates
conditional create/read success and overwrite/delete denial. They also require
`private_marker_network_posture_proven=true`, exact private account/Blob
endpoint/DNS/VNet-link configuration, and the approved legal-hold and locked
90-day WORM phase. Because the AzAPI action's read is state-only,
`marker_legal_hold_readback_proven=true` must independently attest that the
legal hold is still active at action time. They never include the evidence
container. Provision's one `provision_cleanup` assignment remains present with
read-only authority while create grants are disabled; under the same activation
gates it uses create/read with no read-only duplicate. The output remains one
logical `provision_cleanup` ID, for exactly two read plus four create assignments
in the enabled desired state. The saved plan and credentialed readback must
account for any replacement interval. WORM and legal hold, not RBAC, must
provide the non-overwrite guarantee if `write` is required.

The GitHub environments, environment reviewers and branch policies are not
managed here. The existing workflow is not changed to use either UAMI or the
marker account. Operator, observer, cleanup approver, secondary contact, RTO,
RPO, complete server-scope evidence, and the trusted-evidence binding remain
open gates.

## Review and plan procedure

Pull-request checks are credential-free and must use the provider mocks:

```sh
tofu -chdir=infra-staging-rehearsal init -backend=false -input=false
tofu -chdir=infra-staging-rehearsal fmt -check -diff -recursive
tofu -chdir=infra-staging-rehearsal validate -no-color
tofu -chdir=infra-staging-rehearsal test -test-directory=tests -no-color
```

Before any credentialed plan, refresh the complete VNet and peering inventory
and independently confirm that both `10.75.0.0/24` and `10.76.0.0/24` remain
unused and non-overlapping. Only then may the base plan set all three reversible
inputs:

```hcl
deploy_staging_restore_control_plane = true
target_cidr_inventory_revalidated    = true
control_cidr_inventory_revalidated   = true
```

The private-executor network foundation additionally requires its two matching
switches shown above. Setting either without the other fails the activation
guard. If the base control plane is already deployed, the foundation delta must
show only the Container Apps delegation update and the new private-endpoint
subnet. In a combined first apply, those must be the only resources attributable
to the foundation. Neither plan may contain a replacement, peering, public
ingress, compute, endpoint, role, or secret attributable to this rung.

A read-only Azure preflight on 2026-09-03 reported the default storage name
`cosmaneurarehearsalctl` globally available. Availability is transient and must
be checked again immediately before a credentialed plan or apply.

An authorized operator must initialize the checked-in backend and save a plan
for independent review. No automation in this change applies it. The plan must
be additive, use the exact subscription and names, preserve an otherwise empty
target resource inventory, and contain no production resource or assignment.

## Irreversible retention phase

Do not enable retention controls during the reversible base apply. After the
account, containers, private endpoint, role behavior, and readback all pass,
prepare a separate plan with action-time approval. The three switches are
ordered and independently default off:

1. `enable_marker_legal_holds` invokes the Azure Storage `setLegalHold` ARM
   action through AzAPI for all seven retained containers. AzureRM has no
   first-class legal-hold resource; removing this source or state does not clear
   a live hold. Pinned AzAPI 2.1.0 performs this action only on resource
   Create/Update; its Read path only restores state, so an unchanged later plan
   does not POST the action again.
2. `lock_marker_immutability_policies` sets all seven retained containers to
   locked 90-day WORM. Azure cannot unlock or delete those policies.
3. The four live-proven marker-create assignments must be installed on the job
   UAMIs and read back only after exact private marker-network proof. The
   provision cleanup grant replaces its prior read-only grant, yielding six
   logical assignments with no duplicate. The evidence container receives no
   marker runtime role.
4. `enable_marker_readonly_lock` is last, but cannot pass this rung's
   preconditions while the private-executor and trusted-evidence bindings remain
   unimplemented. Once a later reviewed rung implements both, the lock still
   requires the prior legal hold, WORM, and marker-assignment gates.

All retention controls require `irreversible_marker_controls_approved=true`. Locked-policy,
legal-hold, and management-lock resources also use `prevent_destroy`. Recovery
for the reversible phase is a reviewed destroy of this isolated state before
retention is enabled. After retention is enabled, recovery is roll-forward;
clearing legal holds or removing the management lock requires its own explicit
operator procedure, and locked WORM remains irreversible. Do not edit the
AzAPI legal-hold resource after ReadOnly is installed: AzAPI updates perform the
action, and the account lock intentionally blocks such management-plane writes.

`marker_store_binding` exposes the six exact logical marker role-assignment IDs and the
three marker immutability-policy IDs needed for later contract reconciliation;
`provision_cleanup` names either its dormant read grant or its activated
create/read replacement. Other create-role and policy values remain null or
empty while their gates are off.
The evidence policy ID is separate. Evidence-content digests must come from
authenticated readback, not from Terraform configuration.

Nothing in a successful plan, mock test, or base apply changes the five `false`
activation flags in `restore-safe-submit-policy.json` or authorizes a restore.
