# Flight plan: additive Scribe authentication on existing production PostgreSQL

## Outcome and authority boundary

Prepare false-default Core-owned infrastructure source that can add Microsoft
Entra authentication to the existing production PostgreSQL Flexible Server while
retaining its current password authentication. It also defines a separately
gated, temporary Entra administrator binding for the exact Scribe-owned database
migration identity.

Exact Core base: `7d44619f88abab401195ef7ae0bb8aa247b4dc64`.

This source change does not authorize an OpenTofu plan/apply, identity creation,
administrator grant, database creation, migration execution, credential rotation,
application activation, or production traffic. No secret belongs in the binding.

## Exact boundary

- Server: `cosmaneura-prod-postgres` in `cosmaneura-prod`.
- Subscription: `bab2a709-1fcb-4c87-a9fc-802f7477ec13`.
- Tenant: `d51754d0-bba5-4810-820e-afac542844d6`.
- Existing password authentication remains `true` in every phase.
- Entra authentication defaults to `false`.
- The migration administrator defaults to absent and may resolve only:
  `/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/resourceGroups/cosmaneura-scribe-production/providers/Microsoft.ManagedIdentity/userAssignedIdentities/cosmaneura-scribe-production-database-migrations`.
- The live identity's resource, client, and principal IDs must all match the
  reviewed binding before administrator registration can plan.

## Ordered rollout (separate approvals)

1. Merge source with both flags false. Confirm a refreshed production plan is a
   no-op for PostgreSQL authentication and has no administrator resource.
2. After a separately approved Scribe dark apply creates the migration UAMI,
   read its exact identifiers and independently review the binding.
3. Set additive Entra authentication and the temporary administrator flag true.
   Review a saved plan that changes only server authentication from
   password-only to password-plus-Entra and adds that one exact administrator.
4. Apply only with founder production authorization. Re-read server auth and the
   administrator from Azure before any database job starts.
5. Run the separately reviewed Scribe migration job once. Verify the isolated
   `cosmaneura_scribe` database, runtime role, grants, and wrong-identity denial.
6. Set only the temporary administrator flag false. Review and apply its removal;
   keep Entra and password authentication enabled for runtime and rollback.

## Abort conditions

Abort on server replacement, password-auth disablement, administrator-login or
password rotation, firewall/network/database changes, a different identity or
tenant, more than one administrator operation, a live Scribe workload, or any
unrelated resource change. Never place a token, password, database URL, or
patient data in the plan evidence.

## Recovery

Before migration, remove the temporary administrator and leave existing password
authentication intact. After Scribe roles exist, remove only the temporary
administrator; keep additive Entra auth until the Scribe runtime is explicitly
decommissioned. Disabling Entra is a later reviewed Core change, not an automatic
rollback. Preserve the isolated database and shared production server.
