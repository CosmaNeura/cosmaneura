# Flight plan: controller receipt v2 isolation semantics

Date: 2026-09-05

Risk: **Tier 2 — security-boundary and breaking dormant contract correction**

Protected-master parent: merged database-observation checkpoint
`3f55d42ea329f02749061b889e75bd01760d69a1` (PR #318).

## Outcome and boundary

Before any controller receipt v2 producer or live consumer exists, replace the
ambiguous historical claims `blob_copied=false` and `redis_copied=false` with
two provider-observable, point-in-time facts:

- `target_resource_group_storage_account_count=0`
- `target_resource_group_redis_resource_count=0`

Both values are integers, not booleans. Their scope is exactly the target
resource group pinned by `fixed_bindings.target_resource_group_id`. Storage
coverage is `Microsoft.Storage/storageAccounts`; Redis coverage is both
`Microsoft.Cache/Redis` and `Microsoft.Cache/redisEnterprise`. Resource type
comparison is ASCII case-insensitive.

These fields do not claim that no process ever read, wrote, transferred, or
copied Blob or Redis data. Proving a whole-execution negative would require a
separately reviewed containment and durable execution-provenance authority.
The existing runtime-image, identity, egress, and trusted-executor policies do
not provide that authority.

## Compatibility decision

Keep `schema_version=2` as a pre-activation semantic correction. The checked-in
contract states that its producer and provider-observation authorities are
unimplemented, its runtime consumer is unintegrated, and it is non-executable.
The production authority likewise asserts that no controller receipt exists.
Therefore there is no live or external v2 receipt to preserve.

If an external v2 receipt is later discovered, stop and supersede this change
with a new schema version instead of interpreting that receipt under the new
field meanings.

## Hash and policy dependency order

1. Revise the controller receipt v2 policy, validator field set, and tests;
   rotate its raw SHA-256 and policy-binding SHA-256.
2. Re-pin the controller policy in the runtime-binding policy; rotate the
   runtime raw SHA-256 and its policy-binding SHA-256.
3. Re-pin both controller and runtime raw/binding hashes in the production
   authority policy; rotate its raw SHA-256.
4. Re-pin controller, runtime, and production hashes in the ARM-observation
   policy; rotate its raw SHA-256.
5. Re-pin controller, runtime, production, and ARM hashes in the database-
   observation policy; rotate its raw SHA-256.
6. Re-pin the runtime policy in the trusted-executor policy; rotate both the
   trusted-executor raw SHA-256 and activation-normalized template SHA-256.

All corresponding validator constants and focused tests change in the same
slice. Existing external runtime records, signatures, launch records, or broker
receipts would need regeneration after this cascade, but none is activated or
relied upon by this dormant change.

## Deferred observation authority

A later isolated slice must authenticate the new counts before the receipt
builder exists. It will consume sealed production and ARM authorities and use
bounded, exact-scope ARM collection reads. It must reject pagination, duplicate
or malformed resource IDs, a wrong subscription/resource group, unrecognized
types, incomplete inventory, authority drift, and stale observations. Provider
contract evidence must establish collection completeness and visibility.

The target job currently lacks the generic target-resource-group inventory read
action needed for this observation. That later slice must bind an exact,
read-only `Microsoft.Resources/subscriptions/resourceGroups/resources/read`
grant at the target resource group, or introduce a separately bound observer
identity. No such infrastructure or authorization change occurs here.
`target_provisioner` remains a no-op; RBAC is a separate reviewed prerequisite.

## Safety, verification, and recovery

- Preserve every activation, workflow, live-provider, mutation, builder,
  writer, and consumer gate in its current false or unwired state.
- Leave the legacy v1 envelope and sanitizer receipt semantics unchanged.
- Run focused controller/runtime/production/ARM/database/trusted-executor
  regressions, formatting, Ruff, Pyright, compileall, canonical-policy hash
  checks, and the repository change-budget check.
- Obtain independent review of the exact final commit before merge.
- Recovery is a source revert before activation. No deployed state, provider
  resource, database, immutable record, or workflow run is changed by this
  slice.
