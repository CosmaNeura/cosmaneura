# Production Agent API provider identity assignment

Risk: Tier 2. This checkpoint adds an offline planner and verifier for one
production `AgentApi.Access` assignment. It does not query or mutate Azure or
Microsoft Graph, create an app role, deploy an image, change configuration, or
authorize production promotion. Any later identity assignment remains a
separate founder-approved operation.

## Fixed production boundary

The planner accepts only the following production authority and workload
names:

- subscription `bab2a709-1fcb-4c87-a9fc-802f7477ec13`;
- Core resource group `cosmaneura-prod` and Agent API container app
  `cosmaneura-prod-app`;
- Sovereign resource group `cosmaneura-sovereign-prod`;
- provider container app `cosmaneura-sovereign-prod-app`; and
- public container app `cosmaneura-web-prod-app`.

The exact production Entra tenant, Agent API application object, resource
service principal, audience, app-role IDs, and provider/public user-assigned
identity bindings are not present in source. An operator must obtain those
values from read-only production metadata and place them in the private input.
The planner refuses missing, malformed, contradictory, or inferred bindings.
Staging identifiers must never be copied into this input.

The proposed Core rollout values are part of the same input. The Agent API
audience must equal the verified resource application ID, and the proposed
Sovereign client ID must equal the verified provider managed-identity client
ID. The public identity is a separate required binding and must remain excluded
from `AgentApi.Access`.

## Private read-only snapshot

Prepare one normalized JSON input from read-only Azure Resource Manager and
Microsoft Graph responses. The file must be a regular, non-symlink file, no
larger than 256 KiB, owned by the invoking operator and readable only by that
owner (mode `0600`), and contain no
access token, client secret, chart identifier, patient data, or PHI. Record an
exact candidate source SHA and a canonical UTC observation time no more than 30
minutes old.

The before snapshot must contain all of the following:

1. The exact account authority and fixed production resources listed above.
2. Complete app-role collections for both the Agent API application and its
   resource service principal. Follow every Graph page before normalization;
   an incomplete collection is a refusal.
3. The existing enabled application roles `AgentApi.Access` and
   `Scribe.Transcript.Write`, with matching IDs and definitions on the
   application and resource service principal. The planner never creates or
   repairs an app-role definition.
4. The provider and public user-assigned identities, their corresponding
   `ManagedIdentity` service principals, and each container app's exact runtime
   identity binding.
5. The complete Agent API resource service principal's inbound
   `appRoleAssignedTo` collection, plus the complete provider and public
   service principals' outgoing `appRoleAssignments` collections. Follow all
   pages. The two views of each relevant assignment must agree.

These complete collections bind the plan to all existing Scribe and unrelated
grants. The provider may have zero or one exact `AgentApi.Access` assignment;
duplicates are refused. The public identity must have none in both inbound and
outgoing views.

Run the offline planner with the repository tool:

```bash
python infra/production_agent_api_identity_plan.py --input /private/path/plan.json
```

Successful output contains fixed state labels and SHA-256 digests only. It
does not print tenant IDs, object IDs, client IDs, principal IDs, role IDs,
resource paths, identity names, timestamps, source SHAs, raw responses, or
validation details. A failure emits only the fixed refusal message. Treat the
returned plan digest as the immutable review handle for any separate founder
operation.

## Separate founder operation

If the plan reports `create_required`, a later founder-reviewed procedure may
create exactly one application-role assignment with this tuple:

- principal: the verified provider managed-identity principal;
- resource: the verified Agent API resource service principal; and
- role: the verified `AgentApi.Access` application role.

That procedure is deliberately not implemented by this tool. It must use the
same private reviewed bindings and must not alter app-role definitions,
container-app identities, the public Sovereign identity, existing Scribe
assignments, or any unrelated grant. A `no_change` result authorizes no write.

## Post-operation verification

Create a fresh complete read-only after snapshot, copy the reviewed plan
digest into `expected_plan_sha256`, and run the same command in `verify` mode.
Verification succeeds only when:

- all authority, application-role, identity, service-principal, and runtime
  identity facts are unchanged;
- the provider has exactly one matching `AgentApi.Access` assignment in both
  inbound and outgoing views;
- the public identity still has no matching assignment in either view; and
- every Scribe and unrelated assignment is byte-for-byte preserved across the
  complete collections.

The verifier permits no delta when the before snapshot already contained the
provider assignment. Any stale evidence, plan-digest mismatch, missing page,
identity drift, unexpected assignment, or changed Scribe grant fails closed.

## Recovery and stop conditions

The planner and verifier perform no operation, so their recovery is to discard
the private input and evidence digest. They do not establish live readiness by
themselves.

If a separately approved assignment operation partially fails, stop before
changing Core or Sovereign configuration and capture fresh complete read-only
snapshots. Do not retry from stale evidence. If the exact provider assignment
was created but cannot be verified, leave workloads disabled and prepare a new
founder-reviewed reconcile-or-revoke plan. Revocation is a separate Tier 2
decision: never remove an existing Scribe or unrelated assignment as rollback.

Production activation remains blocked until the exact provider assignment has
verified, the public identity remains excluded, the Core audience and provider
client binding match the verified identities, and the broader Session Prep
production activation flight plan is satisfied.

## Local validation

No command below contacts Azure, Microsoft Graph, or a deployment service:

```bash
uv run pytest --confcutdir=tests/infrastructure \
  tests/infrastructure/test_production_agent_api_identity_plan.py -q
uv run ruff check infra/production_agent_api_identity_plan.py \
  tests/infrastructure/test_production_agent_api_identity_plan.py
uv run pyright infra/production_agent_api_identity_plan.py \
  tests/infrastructure/test_production_agent_api_identity_plan.py
git diff --check
```

Before merge, record an independent review of the final exact commit. Before
any production identity write, repeat the read-only snapshot, review the exact
plan digest, and obtain the founder's explicit authorization for that operation.
