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
- a private `StorageV2` `Standard_ZRS` marker account, three ARM-created private
  marker containers, a reserved aggregate-only `restore-evidence-v1` container,
  private Blob endpoint, private DNS zone, and VNet link; and
- an unpeered `10.76.0.0/24` control VNet with a marker endpoint subnet and an
  empty subnet reserved for a later private executor.

The target restore network remains controller-owned and ephemeral. Its contract
is `10.75.0.0/24` with `10.75.0.0/27` for the PostgreSQL private endpoint. This
root does not place an ordinary resource in the target group, because
`staging_restore_envelope.py` requires `az resource list --resource-group
cosmaneura-staging-rehearsal` to be empty before a restore. The policy and role
assignments are target-scoped extension resources. A credentialed readback must
still prove that the exact controller command returns `[]` before activation.

## Deliberate blockers

The reserved `10.76.0.32/27` subnet is not a runner, Container Apps environment,
route, peer, or proof of connectivity to an ephemeral `10.75.0.0/24` target.
`private_executor_binding_implemented` and
`trusted_evidence_binding_implemented` remain hard-coded `false`; a separate
reviewed rung must choose the runtime, attach the unfederated job UAMIs, bind the
aggregate-only evidence sink, add only the required topology, and prove private
DNS and data-plane reachability before any workflow integration or ReadOnly
account lock.

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
and its three marker-container-scoped assignments remain absent unless
`enable_marker_create_assignments=true`,
`marker_create_authorization_proven=true`, and exactly `read` plus one of `add`
or `write` is supplied from live provider-fidelity evidence. Those assignments
also require the approved legal-hold and locked-WORM phase. They never include
the evidence container. WORM and legal hold, not RBAC, must provide the
non-overwrite guarantee if `write` is required.

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
unused and non-overlapping. Only then may the plan set all three reversible
inputs:

```hcl
deploy_staging_restore_control_plane = true
target_cidr_inventory_revalidated    = true
control_cidr_inventory_revalidated   = true
```

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
   action through AzAPI for all four containers. AzureRM has no first-class
   legal-hold resource; removing this source or state does not clear a live
   hold. Pinned AzAPI 2.1.0 performs this action only on resource Create/Update;
   its Read path only restores state, so an unchanged later plan does not POST
   the action again.
2. `lock_marker_immutability_policies` sets all four containers to locked
   90-day WORM. Azure cannot unlock or delete those policies.
3. The three live-proven marker-create assignments must be installed on the job
   UAMIs and read back. The evidence container receives no marker runtime role.
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

`marker_store_binding` exposes the six exact marker role-assignment IDs and the
three marker immutability-policy IDs needed for later contract reconciliation;
create-role and policy values remain null or empty while their gates are off.
The evidence policy ID is separate. Evidence-content digests must come from
authenticated readback, not from Terraform configuration.

Nothing in a successful plan, mock test, or base apply changes the five `false`
activation flags in `restore-safe-submit-policy.json` or authorizes a restore.
