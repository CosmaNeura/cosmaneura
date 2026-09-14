# Private synthetic-probe publisher network — dormant candidate

September 14, 2026. Tier 2: network/security and future recurring costs.
One outcome: a separately reviewable, default-disabled network module with
mocked tests. The staging root does not call it. No Azure plan, resource, runner,
provider registration, publication, runtime test or deployment is performed.

## Candidate and boundaries

The module proposes a separate East US resource group and unpeered
`10.77.0.0/24` VNet. It does not reuse the existing executor, vault, database,
mailbox or evidence network. Four explicitly distinct subnets are proposed:

| Role | Subnet | Use |
| --- | --- | --- |
| Firewall | 10.77.0.0/26 | AzureFirewallSubnet |
| Management | 10.77.0.64/26 | AzureFirewallManagementSubnet |
| Runner | 10.77.0.128/27 | GitHub.Network/networkSettings delegation only |
| Registry | 10.77.0.160/27 | One additional endpoint to the exact existing ACR |

A separate `privatelink.azurecr.io` zone is linked only to this new VNet, to
avoid modifying the existing executor's zone/link. The module does not manage
the existing registry's configuration, public-network switch, grants or content.
The private-endpoint connection is still a real Azure authorization/change that
requires the later exact resource plan; it is not implied approved.

The candidate uses Azure Firewall Basic and two Standard public IPs required
for its data/management configurations. These are firewall addresses, not
runner static IPs, registry public exposure or inbound/DNAT permission.
The runner NSG denies all inbound traffic. Outbound it admits dedicated registry
subnet TCP443, platform DNS, and Internet TCP443 routed through the firewall;
an explicit final deny overrides ordinary Azure NSG defaults. The UDR has
one 0.0.0.0/0 VirtualAppliance hop and BGP route propagation disabled.
The firewall has only an HTTPS application-rule allowlist, no network or NAT
allow rules. Input is bounded to256 exact lowercase DNS names, no wildcards,
URLs, IP literals, oversized labels or automatic API-driven expansion.

This is a configuration candidate, **not proved workload containment**.
Platform DNS remains usable, Azure platform/IMDS flows require separate live
assessment, and permitted hosts could themselves offer upload/exfiltration
surfaces. No publisher credential, workload or PHI is admitted by this PR.
The network does not authenticate workflow identity or constrain what a
permitted registry/HTTPS service accepts. Future transport/identity policy must
address those boundaries; never relabel this network as full runtime isolation.

## Source gates and exclusions

Both enable and exact-network-plan-approved inputs default false. Partial flags
or missing enabled bindings refuse; inputs alone do not authorize creation.
These booleans are not cryptographic evidence of independent plan review.
The caller must separately establish human authorization and exact saved-plan
identity/lifetime. Every readiness/acceptance output stays false.

No provider blocks, root integration, data sources, executables, provisioners,
UAMI, role assignments, network peerings, GitHub runner/configuration, workflow,
upload, image entrypoint or human consumer are added. Existing provider pins
remain OpenTofu1.12.5, AzureRM4.73.0 and AzAPI2.4.0.
GitHub NetworkSettings uses the pinned provider's 2024-04-02 schema.

Future integration must explicitly disable AzAPI automatic registration with
`skip_provider_registration=true` and AzureRM registrations with `none`.
The existing staging root has the latter but not the former; it is unchanged
and cannot be treated as an approved publisher integration. Registering
GitHub.Network is a distinct human prerequisite if it is absent.

NetworkSettings creation depends on runner NSG/rules, route association,
firewall rules and the private endpoint. No organization runner may be attached
until fresh live readback independently proves the actual route/NSG/DNS and
organization binding. Azure dependencies establish order, not acceptance.

## Feasibility evidence and decisions still needed

Official GitHub docs support organization owners on Team, Linux larger runners
and East US. Standard runners are not supported for this Azure private-network
path. Runner IPs must remain dynamic. Candidate concurrency is one, not selected
or created. The old example IP addresses closed on/after July1,2026; do not use
them. Prepare a fresh, exact reviewed DNS list from current GitHub meta and the
actual publisher dependencies. No concrete production allowlist is included.

Read-only organization membership showed an active admin, but the current CLI
token lacks the runner/network-configuration scopes for inventory. Existing
configuration/capacity is unknown, not presumed absent. Do not broaden token
permissions or use an alternate route to circumvent that refusal. A later
owner-led setup choice is required.

Public prices observed September14 (USD, not a spending authorization):

- Azure Firewall Basic East US: $0.395/hour deployment plus $0.065/GB processing.
- Base alone is $2.37 for6hours or $9.48 for24hours.
- GitHub Linux larger runners: 2-core advanced $0.006/minute; 4-core $0.012/minute.
- Public IPs, private endpoint/DNS, transfer, other meters and taxes are extra.
  The price is not an all-in quote or a provider-enforced spend cap.

Actual SKU/capacity, exact permitted destinations, provider/organization binding,
recurring budget and stop/cleanup procedure need a grouped human decision.
No service account, runner or billing setting has been changed. Firewall costs
continue while provisioned even when no workflow is running; source flags and
GitHub runner inactivity do not constitute cost shutdown.

## Verification and remaining execution gates

Fourteen new provider-mocked runs cover disabled/default gates, invalid binding
and domain inputs, non-authorizing supplied inputs and positive network wiring.
The positive fixture uses distinct synthetic ARM IDs per subnet; it does not
prove Azure accepts those resource operations. Assertions bind actual configured
subnet roles, delegation, firewall/route/NSG wiring, exact input domains, separate
DNS/endpoint wiring and false readiness outputs.
Eight additional Python scope/CI guards cover the exact resource inventory,
absence of executable/ambient integration, no root call and ordered admission.
The existing secretless infra workflow includes these files through
`infra-staging-rehearsal/**` and runs all HCL tests. It is not modified.
Local tests are focused; unchanged passing suites are not manually replayed.

Independent final-source review and pushed-head CI are required before a
separately authorized normal protected merge. No existing approval setting
is changed. A later source integration must add a reviewed exact publisher
plan/readback/consumer contract; the completed foundation's old consumer,
plan, receipt and claim cannot authorize this new resource set.

Before any cloud action: resolve exact source/protected-master provenance,
source and plan/handoff independent reviews, current identity evidence,
state/network drift, recurring cost decision, and human Terminal execution.
Keep the ten-minute handoff and exact four-hour plan lifetime. No receipt
starts before current human presence and readiness; no expired/changed artifact
is reused. Actual private image transfer/readback, live isolation, recovery
rehearsal, staging/Clerk and Zoom with30-minute observation remain separate.

## Recovery and references

Before integration, recover with a normal source-only revert of this dormant
module, its tests and this document. No data migration or user-facing flag applies.
After resources exist, disabling the module is not rollback or safe cleanup:
resource-group/VNet prevent_destroy blocks casual teardown, and child resources
may have distinct lifetimes. A reviewed human cleanup/retention plan must stop
costs without deleting unrelated or evidence resources. No automatic cleanup.

References (public evidence, not execution permission):

- https://docs.github.com/en/organizations/managing-organization-settings/configuring-private-networking-for-github-hosted-runners-in-your-organization
- https://docs.github.com/en/enterprise-cloud@latest/admin/configuring-settings/configuring-private-networking-for-hosted-compute-products/about-azure-private-networking-for-github-hosted-runners-in-your-enterprise
- https://docs.github.com/en/billing/reference/actions-runner-pricing
- https://prices.azure.com/api/retail/prices
