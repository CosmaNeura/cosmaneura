# East US private data path and workload-clone plan

**Plan date:** 2026-08-14
**Approval state:** design and false-by-default code prepared; no deployment or cutover approved

## Executive decision

The parallel East US environment must not clone production settings wholesale. It first receives a
private bridge to the current North Central US data services, then a manual synthetic probe. Full
workloads move only after their data, queue, singleton, and rollback behavior has a separately
approved plan.

The bridge is intentionally temporary. The target data plane is East US PostgreSQL after regional
capacity is granted, East US Azure Managed Redis, and storage whose regional migration and
managed-identity access are reviewed independently.

## Read-only production inventory

| Service | Current setting | Migration implication |
| --- | --- | --- |
| PostgreSQL 16 | North Central US, Burstable B1ms, public access, no private endpoint, 32 GiB, 30-day backup, no HA or geo-redundant backup | Add an East US private endpoint as a bridge; do not disable public access while the old environment is live |
| Azure Cache for Redis 6 | North Central US Standard C0, public access, no private endpoint, TLS 1.2 | Bridge privately, then migrate to Azure Managed Redis before the retiring service is disabled |
| Uploads storage | North Central US LRS, public network allowed, shared keys allowed, blob public-access capability enabled, no network rules | Add only the Blob private endpoint first; application managed-identity work gates removal of shared keys/public access |
| Deidentified-transcripts storage | North Central US LRS, public network allowed, not owned by the platform Terraform state | Inventory its Health Data service contract separately; do not adopt it silently into this state |
| Container registry | North Central US Basic, public network, admin user enabled | Basic cannot use Private Link; synthetic and new workloads use managed-identity `AcrPull` |
| Private endpoints | None in `cosmaneura-prod` | All bridge endpoints are new and linked only to the parallel VNet |

No secret values, keys, database contents, cache contents, or blobs were read for this inventory.

## Bridge resources

`deploy_parallel_private_data_endpoints` is false by default. When both it and the parallel
environment flag are enabled in a future reviewed plan, the platform state defines ten Azure
resources:

1. `private-endpoints` subnet at `10.43.1.0/24` with private-endpoint network policies disabled.
2. PostgreSQL private DNS zone, VNet link, and private endpoint using group
   `postgresqlServer`.
3. Redis private DNS zone, VNet link, and private endpoint using group `redisCache`.
4. Uploads Blob private DNS zone, VNet link, and private endpoint using group `blob`.

OpenTofu also records one no-cost `terraform_data` guard so enabling the data bridge without the
parallel environment fails explicitly. The existing services remain unchanged: this slice does
not disable public access, delete PostgreSQL firewall rules, rotate credentials, change connection
strings, move data, or upgrade ACR.

The private endpoints live in East US and can reach the North Central services over Azure Private
Link. Their standard service hostnames resolve to private addresses only inside the linked parallel
VNet. The live non-VNet environment continues resolving the public addresses for rollback.

## Synthetic network job

`deploy_parallel_network_smoke_job` is also false by default. Its image is a tiny standard-library
Python probe with a digest-pinned base image. It is built into the existing registry with an
immutable commit tag and pulled through the dedicated Scribe workflow identity's `AcrPull` role.
It has no secrets, database URL, Redis URL, storage key, tenant ID, or customer identifier.

One manual execution performs exactly three checks:

1. Resolve the configured Scribe FQDN and fail unless every answer is RFC1918 or IPv6 ULA.
2. Call `/healthz` over HTTPS and require `200`.
3. POST an empty JSON body to `/v1/consents` without a token and require `401`.

Logs contain only a fixed result code and status numbers. They never contain the configured URL,
hostname, resolved address, headers, response body, token, or PHI. The job has no schedule and
scales to zero between explicitly started executions.

Future build command, not approved for execution:

```bash
az acr build \
  --registry cosmaneuraprodregistry \
  --image cosmaneura/platform-network-smoke:<git-commit-sha> \
  --file Dockerfile \
  infra/network_smoke
```

An enabled job plan should show one Azure role assignment, one Container Apps Job, and one no-cost
state guard. Image build, job deployment, and job start each require separate approval.

## Design-plan evidence

The following unsaved, targeted, `-refresh=false` plans were run on 2026-08-14 to validate the
resource graph without querying or changing the proposed resources. They are design checks, not
apply artifacts and not substitutes for the full refreshed plans required by the approval gates.

- Parallel foundation plus private data bridge: **16 add, 0 change, 0 destroy**. This comprises
  five foundation resources, ten bridge resources, and one no-cost bridge guard.
- Parallel foundation plus manual smoke job: **9 add, 0 change, 0 destroy**. This comprises five
  foundation resources, the not-yet-deployed Scribe workflow identity, its `AcrPull` assignment,
  the manual job, and one no-cost smoke guard.

The smoke plan uses the live development Scribe VNet ID and internal FQDN only as a connectivity
test target. Development remains non-production. The workflow identity does not currently exist in
Azure and is therefore an explicit prerequisite rather than an assumed deployed dependency.

## Workload clone matrix

| Workload | Earliest safe clone | Required isolation and acceptance evidence |
| --- | --- | --- |
| Network smoke job | After network, peering, DNS, and image plans pass | Synthetic only; private DNS, `200`, and fail-closed `401` |
| Web API | After private database/storage paths and Redis decision | No custom domain; zero customer traffic; synthetic DB/storage/Scribe flow; managed identity attached |
| Worker | After a queue migration plan | Must not consume the live Celery queue in parallel or double-execute tasks |
| Scheduler | At traffic cutover only | Singleton: old and new schedulers must never both enqueue periodic work |
| Job monitor | After target Redis exists | Read-only validation first; no public exposure or reused registry password |
| Database migration job | Only against an approved target database | Saved migration plan, backup/restore evidence, one manual execution, reversible schema boundary |

The current four production applications remain in their North Central environment throughout
synthetic validation. No `app.cosmaneura.com` or `chat.cosmaneura.com` certificate or DNS binding is
defined on the parallel environment.

## Redis target decision

Microsoft has announced retirement of Azure Cache for Redis. Existing Basic, Standard, and Premium
instances remain available through September 30, 2028 and are disabled starting October 1, 2028.
New instance creation for existing customers is scheduled to be blocked October 1, 2026. Therefore
the East US target is Azure Managed Redis with Private Link—not another Azure Cache for Redis.

The initial sizing envelope is Balanced B0 or B1 with two nodes for production availability. The
current East US retail meters are $0.016/node-hour for B0 and $0.032/node-hour for B1, or roughly
$23.36–$46.72 per 730-hour month for two nodes. Final sizing requires current Redis memory,
operations, connection, latency, Celery queue-depth, and session-TTL measurements; no cache is
created by this plan.

The migration must distinguish disposable cache/session keys from Celery broker state. A bounded
maintenance window or a verified queue-drain protocol is required before switching workers. Dual
workers against old and new brokers are not an acceptable shortcut.

## Cost envelope

Retail estimates below use Microsoft's USD Retail Prices API on 2026-08-14 and exclude negotiated
sponsorship pricing, taxes, and application/data volume:

| Item | Current retail meter | Approximate monthly fixed cost |
| --- | --- | ---: |
| Standard load balancer managed for the external Container Apps environment | $0.025/hour | $18.25 |
| Standard static public IPv4 | $0.005/hour | $3.65 |
| Three private endpoints | 3 × $0.01/hour | $21.90 |
| VNet, subnets, private DNS links, role assignment, state guards | No fixed compute meter | $0.00 |
| Manual smoke job at its five-minute timeout | 0.25 vCPU + 0.5 GiB active consumption | Less than $0.01/run |

Expected fixed foundation-plus-bridge networking is approximately **$43.80/month**, before usage.
Usage meters include same-region VNet peering at $0.01/GB ingress and egress, Private Link data at
$0.01/GB ingress and egress for the first PB, load-balancer data at $0.005/GB, private DNS queries,
and Log Analytics. The East US Log Analytics meter includes the first 5 GB/month and is $2.30/GB
afterward; the 0.5 GiB daily cap limits a 30-day month to about 15 GiB, or roughly $23 maximum paid
ingestion if the cap is hit every day.

Requery retail meters and inspect the subscription's actual negotiated prices immediately before
approval. The future production Scribe stack, East US PostgreSQL, Azure Managed Redis, application
replicas, storage migration, backups, and cross-region data transfer are not included in $43.80.

## Approval gates and rollback

1. **Private bridge plan:** full refreshed plan; expected bridge/guard additions only; no changes or
   destroys. Applying remains unapproved.
2. **Probe image:** review digest, source, unit tests, and image scan; build remains unapproved.
3. **Probe job plan:** expected job/role/guard additions only; no schedule; applying remains
   unapproved.
4. **Synthetic execution:** confirm private DNS and fail-closed auth, then delete or retain the
   scale-to-zero definition; starting remains unapproved.
5. **Data target:** approve East US PostgreSQL and Azure Managed Redis plans, backups, and migration
   mechanics.
6. **Workload clone:** deploy web first without customer traffic; workers and scheduler remain off.
7. **Cutover:** move a bounded traffic slice only after security/performance acceptance.
8. **Hardening:** once rollback no longer requires the old environment, disable public data access,
   remove allow-all firewall and shared-key/admin credentials, and verify managed-identity access.

Before workload cloning, rollback removes only the smoke job, role assignment, three endpoints,
three zone links, three zones, and endpoint subnet through their owning Terraform state. The
source services and live runtime are untouched. After a future cutover, traffic and singleton jobs
return to the old environment before any data rollback is attempted.

## Authoritative Azure references

- [PostgreSQL Flexible Server private endpoints](https://learn.microsoft.com/en-us/azure/postgresql/network/how-to-networking-servers-deployed-public-access-add-private-endpoint)
- [Azure Cache for Redis retirement and migration guidance](https://learn.microsoft.com/en-us/azure/azure-cache-for-redis/cache-whats-new)
- [Azure Managed Redis Private Link](https://learn.microsoft.com/en-us/azure/redis/private-link)
- [ACR private endpoints and Premium requirement](https://learn.microsoft.com/en-us/azure/container-registry/container-registry-private-endpoints)
- [Azure Private Link pricing](https://azure.microsoft.com/en-us/pricing/details/private-link/)
- [Azure virtual network pricing](https://azure.microsoft.com/en-us/pricing/details/virtual-network/)
- [Azure Container Apps pricing](https://azure.microsoft.com/en-us/pricing/details/container-apps/)
