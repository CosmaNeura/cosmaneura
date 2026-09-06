# Flight plan: controller receipt v2 isolation inventory authority

Date: 2026-09-06

Risk: **Tier 2 — dormant security-boundary observation authority**

Stacked parent: isolation-semantics candidate
`2c1a6130347068d8913408823b77c6fd13c4137b`.

## Outcome and boundaries

Implement a sealed, one-shot authority that authenticates the seven isolation
fields required by controller receipt v2 at one bounded point in time. Its only
public input is a sealed database-observation authority, which transitively
reauthenticates the production, ARM, controller, and runtime authorities.

The observer obtains one provision-job managed-identity ARM token and issues
exactly four internally derived GETs, in order:

1. target PostgreSQL server `firewallRules`;
2. target VNet `virtualNetworkPeerings`;
3. target resource-group `publicIPAddresses`;
4. target resource-group generic `resources` inventory.

It accepts only exact final URLs, status 200 JSON framing, bounded bodies, no
pagination, and one attempt. There are no caller selectors, proxy use,
redirects, retries, polling, disk writes, Azure mutations, Terraform changes,
workflow wiring, receipt construction, receipt publication, or consumers.

## Inventory semantics

The first three collections must be empty. The resource inventory must contain
the five top-level resources already authenticated by the sealed ARM authority:
the restored PostgreSQL server, VNet, private endpoint, network interface, and
private DNS zone. A private DNS virtual-network link and private-endpoint DNS
zone group are allowlisted but optional in this collection because existing
provider fixtures demonstrate that Azure resource-group inventory does not
consistently enumerate nested resources. The subnet is deliberately absent
from the resource-group inventory allowlist.

`unknown_resource_count=0` means no non-allowlisted resource was present in the
exact, non-paginated `/resources` response for the pinned target resource
group. It does **not** claim a complete inventory of the Azure subscription,
resources outside that group, hidden provider state, or execution history.
Nested-resource absence is not inferred when Azure omits those resources from
the generic collection.

Every returned item has a syntactically valid resource ID, exact subscription
and resource-group scope, matching hierarchical name and type, expected
location, and a case-insensitively unique ID. Provider order is normalized
before the inventory digest. Storage coverage is
`Microsoft.Storage/storageAccounts`; Redis coverage is
`Microsoft.Cache/Redis` and `Microsoft.Cache/redisEnterprise`, compared using
ASCII case-insensitive semantics. No ownership-tag claim is added because the
sealed upstream output does not bind tags for every allowlisted resource.

The authority emits built-in integer zero values for firewall rules, VNet
peerings, public IPs, unknown resources, target-RG storage accounts, and
target-RG Redis resources. `public_access=Disabled` is inherited only through
the sealed ARM observation. These are point-in-time facts, not evidence that a
Blob or Redis copy never occurred.

## Risk and dependencies

This slice is held dark and independently reversible. The policy records the
implementation as present while executable, live ARM, workflow, builder, and
consumer gates remain false. It pins the exact raw hashes of the controller,
runtime-binding, production-authority, ARM-observation, and
database-observation policies; no upstream policy points back to this leaf.

Before any live rehearsal, separately reviewed infrastructure must grant the
provision identity the narrow target-resource-group inventory read action (or
bind a dedicated observer identity), and provider evidence must confirm the
four collection contracts. This change does not grant that access.

## Rollout and recovery

1. Merge only after exact-final-head independent review and protected CI.
2. Keep the observer unwired while receipt builder/writer/consumer slices are
   developed and reviewed independently.
3. Perform a synthetic-only approved rehearsal after RBAC and provider-contract
   evidence are accepted; abort on any nonempty forbidden collection,
   pagination, missing required top-level resource, or unknown resource.
4. Production remains founder-only and manual after staging, Auth, and Zoom
   acceptance gates.

Recovery before activation is a source revert. No deployed resource, database,
immutable record, or environment changes in this slice. Once activated, stop
the caller and preserve raw in-process evidence for diagnosis; do not weaken an
allowlist or reinterpret a failed observation.

## Completion evidence

- Focused upstream plus new tests; Ruff, format, Pyright, and compileall pass.
- Canonical policy hash and all upstream pins match exact reviewed bytes.
- Change budget remains at or below 2,000 lines and 50 files.
- Independent review records the exact final commit and tree.
- Protected CI passes after the final push; no deploy job may run from the PR.
