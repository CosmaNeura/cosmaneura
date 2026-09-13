# Empty private executor environment — source-only rung

Tier 2. Default-disabled infrastructure source, not a deployment or runtime
activation. Base: `e21ba4c1fb1027de8123aca9b8aee25b8a11b612`.

## Outcome and limits

Add one empty internal Azure Container Apps workload-profiles environment on
the already modeled dedicated `10.76.0.32/27` subnet. Keep public network access
Disabled, explicit Consumption profile, and no environment identity, apps, jobs,
images, secrets, grants, extra endpoints/DNS, peering, route, NAT, firewall or
logs sink. Azure-managed platform resources are not claimed absent: the
environment creates its explicitly named infrastructure resource group.

This is not an executable private runner. Image provenance, exact job-UAMI
attachments, runtime record/evidence ingress, workload egress, verifier
containment, target reachability, broker/cleanup and workflow bindings remain
unimplemented/unproven and disabled. No plan artifact, live evidence, apply,
image publication, restore or production/PHI operation is included.

## Platform basis

- Pinned AzureRM 4.73.0 supports `public_network_access`, internal load balancing,
  explicit infrastructure RG/subnet and a Consumption workload profile:
  https://github.com/hashicorp/terraform-provider-azurerm/blob/v4.73.0/website/docs/r/container_app_environment.html.markdown
- Microsoft documents `/27` minimum for workload-profiles, versus `/23` for
  legacy Consumption-only, and Disabled public access for internal environments:
  https://learn.microsoft.com/azure/container-apps/networking

The provider page's generic larger-subnet note does not select the legacy
environment here; the explicit workload profile is essential. Provider-schema
and mock checks are not live capacity, quota or platform-availability proof.

## Acceptance order

1. Focused secretless provider-mocked checks must cover default absence, paired
   flags, base/network/fresh-CIDR requirements and exact internal/profile/subnet
   configuration. Final exact-source independent review and normal PR controls
   remain required. Do not merge while the current infrastructure plan is pinned.
2. After earlier foundations complete, use the normally merged exact source and
   a NEW separately reviewed plan. Never overlay this source on the current plan.
3. Freshly verify environment and managed-RG exact-ID absence, dedicated subnet
   occupancy/delegation, connected-network overlap, provider registration,
   workload-profile availability/quota and expected platform-managed costs and
   resources. Human exact-plan approval covers those consequences explicitly.
4. After a separately authorized human apply, read back internal/public access,
   workload profile, subnet, identities (none), platform infrastructure inventory,
   and absence of apps/jobs/grants/secrets; preserve source/state/plan evidence.
5. Later independently reviewed rungs must enforce/prove workload egress and
   containment and bind all exact job/evidence/runtime prerequisites before any
   workload can run. Internal ingress is not outbound confinement.

## Recovery

Before creation, keep both flags false or normally revert this source. After
creation, preserve the empty environment and its platform resources; flags are
not rollback. `prevent_destroy` rejects flag-off/replacement plans. Removal or
recreation, especially after jobs/evidence attach, requires separate impact
review and explicit authority. No migration, data or feature-flag rung applies.
