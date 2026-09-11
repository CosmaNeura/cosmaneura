# Staging restore private-registry foundation

Status: source implemented; 22 secretless test runs pass; independent review
and CI pending. No apply or image publication.
Risk: **Tier 2**, private network and managed-identity authorization boundary.
Base: protected Core `85f2dae47af996bf69ec2a9a5a47a54517b4bf9a`.

## Deployable outcome and boundaries

Add a default-off, dedicated Premium Azure Container Registry in the existing
East US rehearsal control group. Bind its registry private endpoint to the
already modeled `10.76.0.64/27` private-service subnet, its exact private DNS
zone/link to the control VNet, and registry-scoped AcrPull only to the two
existing unfederated job identities. This supplies the infrastructure receiver
needed by the later digest-pinned runtime image and job-template bindings.

The slice creates no image, publisher/push identity or grant, job, Container
Apps environment, Key Vault, secret, evidence account, restore, migration,
marker write, retention control, deployment or workflow activation. The
runtime policy and both aggregate implementation blockers remain disabled.
No ordinary resource is added to the restore target group. Existing marker
resources, retained namespaces, identities, assignments and policy are kept.

## Required configuration and approval boundaries

- Require paired default-false registry-enable/exact-plan-approval inputs,
  the existing approved network foundation, and both action-time CIDR checks.
- Pin existing provider versions; never register Azure providers implicitly.
- Require Premium, public network disabled, admin and anonymous pull disabled,
  no public-IP rules or trusted-service network bypass, and dedicated data
  endpoints so image layers can follow the private endpoint/DNS path.
- Use only the `registry` private-link group, the existing service-endpoint
  subnet, `privatelink.azurecr.io`, and a non-registering control-VNet link.
- Grant registry-scoped built-in AcrPull to job UAMIs only. No dispatcher,
  publisher, system identity, password, admin, ambient credential or broad
  subscription grant is introduced.
- Names, global availability, Premium cost, provider registration, subnet
  capacity and the exact saved-plan delta must be accepted before human apply.
  Source booleans are not evidence or a substitute for operator approval.
- The pinned provider does not expose `role_assignment_mode`. Require live
  `LegacyRegistryPermissions` readback before treating AcrPull as effective;
  an ABAC-enabled registry fails acceptance. The exact enum is documented in
  the [Microsoft ARM registry reference](https://learn.microsoft.com/en-us/azure/templates/microsoft.containerregistry/registries).

## Validation and rollout

1. Inspect the pinned provider schema, then implement the resource/guard/output
   contract without a backend or Azure credentials.
2. Test default dormancy, both one-sided approvals, missing base/network/CIDR
   prerequisites, exact private resources/roles, and unchanged retention and
   runtime blockers with provider mocks. Run format, validate, all root tests,
   diff/change-budget checks and independent exact-final-commit review.
3. Merge normally. Separately refresh current state, complete connected-network
   inventory and resource/name availability. Prepare a private saved plan that
   distinguishes the existing network-foundation delta from the registry delta.
4. After independent plan review, the human performs the exact approved apply.
   No agent apply, production command or short-lived handoff is started while
   the operator is away.
5. Read back exact registry posture, effective job grants, endpoint/NIC/DNS
   registry and regional data records, and preservation of the empty target.
   Private pull/image provenance and runtime binding remain later acceptance
   gates; mock tests and infrastructure existence do not prove those gates.

## Recovery

Before apply, hold or revert the source with both new inputs false. After
creation, preserve the registry and reconcile by reviewed roll-forward; never
destroy an image store or remove grants blindly by switching a flag off.
No irreversible retention or production promotion is authorized by this rung.

## Local validation record

OpenTofu 1.12.5 with locked AzureRM 4.73.0 and AzAPI 2.1.0: recursive format
check, configuration validation and all 22 provider-mocked runs passed with an
isolated backend-disabled data directory and no Azure credentials. Ten new
runs cover registry approval/prerequisite rejection, name validation and the
exact private resource/role contract; existing base/network/retention runs pass.
The pinned OpenTofu supports resource-wide overrides, not per-instance ones:
job and dispatcher fixture IDs differ, but the two job instances share the
job fixture OID. Source review and later live readback must verify each exact
job OID; mocks are not provider-fidelity evidence.
