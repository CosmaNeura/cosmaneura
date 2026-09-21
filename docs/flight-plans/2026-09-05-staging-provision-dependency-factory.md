# Flight plan: dormant provision dependency factory

## Outcome and boundaries

- Tier **2** replaces the trusted executor's four caller-supplied filesystem
  paths with a zero-argument public entry point and a dormant, pathless
  dependency factory.
- The private execution seam retains the authenticated broker receipt,
  validated provision launch record, and authenticated runtime-record carrier.
  It calls the one-shot private evidence reader once and validates the returned
  exact sealed `PrivateProvisionInputs` against those same authorities.
- The evidence bundle is passed as a detached in-memory byte mapping to
  `validate_bundle_snapshot_bytes`. The GitHub trusted root is used only from
  `AuthenticatedRuntimeRecordMaterial`; the executor cannot reread or override
  it through a path, URI, environment value, or caller argument.
- Workflow wiring, Terraform, Azure changes, restoration, sanitization,
  cutover, and production/PHI promotion are explicit non-goals.

## Risk and dependencies

- Every one of the nine child policy documents is loaded and its exact policy
  ID/SHA-256 pin is checked before any reader, verifier, provider, transport, or
  safe-submit runtime is constructed. Executable validation then requires all
  upstream policies to be enabled; the checked-in dormant policies refuse.
- One exact `_NonRegressingClock` instance is shared by receipt/runtime ingress,
  evidence ingress, manifest validation, server authentication, provider
  observation, attestation, composition, final launch freshness, and
  safe-submit. The runtime-record and evidence-attestation verifiers are
  separate one-shot instances with separate profiles.
- The executor snapshots and repeatedly rechecks receipt bytes/SHA, launch
  bytes/URI/SHA, execution binding, six evidence names/order/digests,
  attestation digest, HMAC digest, and authenticated-root digest. Mutable maps
  are detached and checked after validation so dependency mutation cannot alter
  the sealed source carrier.
- Failures are bounded stage codes and cannot expose input bytes, keys, tokens,
  URIs, provider bodies, or dependency exception text.

## Current deployed state

- The work is based on exact `master`
  `4b85a5b4ffb4ebe4e8be0bbcf45b46066b73a1cf` and changes source/contracts/tests/
  documentation only.
- `PUBLIC_ENTRYPOINT_INTEGRATED` is false. The public zero-argument entry point
  refuses before reading a policy, environment variable, filesystem object, or
  network endpoint, and legacy path keyword arguments fail with `TypeError`.
- All trusted-executor, runtime-adapter, private-runner, workflow, and restore
  activation flags remain false. No live resource or protected environment is
  changed.

## Feature flag / release control

- This is not reversible user-visible behavior. Activation remains controlled
  by the existing closed-world policy gates and a later separately reviewed
  workflow/private-runner integration slice.

## Schema/data sequence

N/A: no schema, migration, data write, restore, or sanitization runs in this
slice.

## Rollout and promotion

- Hold this commit dormant until the configured runtime/evidence verifier pins,
  private network and RBAC evidence, safe-submit policies, private runner, and
  workflow bindings are independently reviewed.
- A later activation change must first prove the zero-argument executor fails
  closed in the deployed image, then run only an explicitly approved non-PHI
  rehearsal. Production promotion remains a separate intentional decision.

## Security and recovery

- Recovery is a code-only revert. No cloud state or data needs rollback because
  this slice makes no live call and has no executable public path.
- Any authority mismatch, expiry, clock regression, carrier mutation, repeated
  one-shot use, policy-pin mismatch, or adapter construction failure refuses
  before safe-submit.

## Completion

- Required evidence: focused and broad infrastructure tests, Ruff check and
  format verification, Pyright, Python compilation, policy-hash verification,
  diff-limit checks, and independent review of the exact final local commit.
- Commit locally only. Do not push, open a pull request, merge, dispatch a
  workflow, run Terraform/Azure operations, or activate restoration.
