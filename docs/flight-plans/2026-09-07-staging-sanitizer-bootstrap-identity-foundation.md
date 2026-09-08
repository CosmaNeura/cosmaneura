# Staging sanitizer bootstrap identity foundation

This Tier 2 slice is stacked on the exact merged bootstrap-binding-plan
checkpoint recorded in the pull request. It models one default-off,
separately approved user-assigned managed identity named
`cosmaneura-staging-restore-bootstrap-job` in the existing rehearsal control
resource group.

## Isolation and blast radius

The identity is owned by the dedicated
`infra-staging-sanitizer-bootstrap-identity/` root and the independent
`staging-sanitizer-bootstrap-identity.tfstate` backend key. The root reads the
exact pre-existing `cosmaneura-staging-rehearsal-control` resource group and
contains no Azure resource other than the one bootstrap UAMI. It therefore
cannot create, update, or destroy the broader rehearsal control plane,
federated credentials, RBAC assignments, jobs, networks, storage, or database
resources. The exact subscription, tenant, resource group, location, identity
name, and complete tag key set are constants rather than caller-controlled
inputs.

The root creates the identity only when the enable and exact-plan-approval
switches are both true. It requires an exact 12-character rehearsal ID and an
integer lifetime from 1 through 24 hours, matching the restore envelope's
maximum lifetime. The exact cleanup deadline is derived from the immutable
plan timestamp. The resource itself rechecks that deadline against apply time,
so a saved plan cannot create the identity after expiry. `prevent_destroy`
rejects disabling or removing the managed identity through this foundation;
teardown requires a later bounded change with authenticated absence proofs.

## Deliberate non-authority

The identity is outside the persistent provision and cleanup job-identity map.
This slice adds no federated credential, RBAC assignment, Container Apps
environment or job, compute attachment, token acquisition, provider or
database call, PostgreSQL administrator, workflow binding, runtime authority,
sanitizer authority, or production change. Its non-secret output reports
desired-state identifiers while keeping exact-ID authentication and every
authority or attachment claim false.

A future reviewed rung must authenticate the live resource, prove there are no
federated credentials or role assignments, and attach it only to one
single-use bootstrap job. The later bootstrap lifecycle must hold external
stability authority from the first provider mutation through final
administrator-absence readback, close the database session before teardown,
prove all job and database authority absent, and only then remove this
identity.

## Review findings resolved

The first independent review rejected the shared-root design for three High
risks. This redesign resolves them by attaching the apply-time expiry check to
the UAMI itself, protecting an existing UAMI with `prevent_destroy`, and moving
the identity into an isolated state whose only Azure-managed resource is that
UAMI. A second review identified redirectable subscription/tenant variables
and case-insensitive collisions between caller tags and lifecycle tags. The
final design removes all three inputs and pins their values in source. Both
earlier reviews are negative evidence, not acceptance; only a new independent
review of the remediated exact head and green CI can satisfy the gate.

Before any apply, recovery is a code-only revert. After an approved apply,
recovery is a separately reviewed teardown plan that first authenticates
absence of all federation, RBAC, job attachment, PostgreSQL administrator, and
active execution state, then removes only this exact identity. No automation in
this slice applies either plan.
