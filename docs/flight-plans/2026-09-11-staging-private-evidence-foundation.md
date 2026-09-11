# Staging restore private-evidence storage foundation

Status: local source validation passed; final-push review and CI pending.
No storage apply or publication.
Risk: **Tier 2**, private artifact-storage and authorization boundary.

## Deployable outcome and boundaries

Model one default-off, dedicated East US Standard_ZRS StorageV2 account in the
control group, Blob versioning, two empty private containers and one Blob
private endpoint in `10.76.0.64/27`. Reuse the existing exact marker Blob DNS
zone/control-VNet link; never create a duplicate zone or link the target VNet.
The account must differ from the marker account. Existing marker namespaces
and their reserved evidence container remain untouched.

The new containers are `restore-evidence-v1` and `restore-runtime-bindings-v1`.
The runtime policy requires provision-only evidence reads and both jobs' record
reads, but this rung creates **no** reader/publisher identity or role assignment.
It uploads no evidence, record, image, signature or secret. No legal hold,
immutability policy, retention lock, compute, restore, target resource, workflow
or deployment is activated. All runtime and aggregate binding gates stay false.

## Provider and private-data contract

Use existing pinned AzAPI 2.1.0 ARM APIs at `2023-05-01` for the account and
containers. An explicit ARM update configures versioning on the account's
automatically created default Blob service; it does not pretend that service
was absent or recover/adopt another account. This avoids account data-plane
probes and ListKeys/state hydration for this new store. No provider upgrade.

Require Standard DNS endpoints, HTTPS/TLS 1.2, OAuth default, shared key/public
Blob/public network disabled, no cross-tenant replication, default-deny network
ACLs with no bypass/IP/VNet/resource exceptions, Microsoft-managed account-level
Blob/File encryption with infrastructure encryption, and no HNS/NFS/SFTP/local
user paths. Export only non-secret endpoint/ID facts. Containers have no public
access; versioning is an account-level prerequisite, not immutable evidence.

The runtime-binding container must later receive independently approved locked
immutability and provider-proven create-only publisher authority. Nothing in
empty storage or versioning proves either. No broad read/write grant is added
to make verification easier. Exact-version/publisher/runtime binding stays dark.

## Guards, validation and live acceptance

- Paired default-false enable/exact-plan inputs require the base, separately
  approved network, both fresh CIDR confirmations, and a distinct account name.
- Prove default/base/network/registry/vault-only dormancy, partial-approval and
  same-name refusal, exact five managed instances, no new grants, preserved
  marker resources and false activation claims with mocks/source checks.
- Run all mandatory format/validate/mock/source/lint checks, change-budget,
  final-push independent review and CI before normal protected-master merge.
- Before planning and human apply: fresh exact identity/state/source/network,
  global name availability AND exact account-ID ARM GET absence, registered
  providers, subnet capacity, and independently reviewed saved-plan delta.
  Never adopt/update an unmanaged account. Source flags are not evidence.
- After approved human apply: exact private account posture, Blob versioning,
  two private/empty containers, endpoint/NIC/DNS, unchanged marker namespace,
  effective account/container/ancestor RBAC and empty target. Private metadata
  inventories use an independently authorized context and report counts only.

## Recovery

No agent apply or short-lived human ceremony during an absence. Before creation,
hold/revert with flags false. After creation, preserve the account/containers
with `prevent_destroy` and use reviewed roll-forward. Removing the Blob-service
update resource does not reverse its settings. Flags are not rollback; version
history and future immutable artifacts must not be deleted as cleanup.

## Recorded local source validation

OpenTofu 1.12.5 format/validate and all **46 provider-mocked runs passed** with
the locked AzureRM 4.73.0/AzAPI 2.1.0 providers, no backend and no credentials.
Twelve evidence-storage runs add eleven refusal cases and exact positive
account/service/container/private-path checks. Five existing default/base/
network/registry/vault plans now explicitly assert evidence-storage dormancy.

All **29 focused infrastructure source tests** and Ruff lint/format passed.
The whole-root declaration inventory adds exactly three ARM storage declarations
to the existing eleven snapshots, preserving every existing declaration hash
and all eight role-assignment bodies. It remains a declaration-text edit
detector, not a general HCL parser or transitive-local semantic proof. No grant
was added. Source assertions additionally pin four definitions/five instances,
container/versioning dependencies, ARM-only exports and existing DNS reuse.

Mocks and source checks do not attest to Azure API acceptance, live object
emptiness, versioning, private connectivity, effective RBAC, or apply authority.
No schema/data migration, user-visible feature flag or cross-repo rollout.
Final exact-push independent review and every mandatory CI check remain required.
