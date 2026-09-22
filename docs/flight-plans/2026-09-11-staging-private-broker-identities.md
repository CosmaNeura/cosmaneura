# Staging private restore broker identity foundation

Status: local source validation passed; final-push review and CI pending.
No Azure apply or broker activation.
Risk: **Tier 2**, identity separation for future one-shot restore dispatch.

## Outcome and boundaries

Model exactly two independently default-off, unfederated user-assigned managed
identities in the persistent East US control group: provision and cleanup
brokers. The existing closed-world runtime contract already requires six
distinct runtime identities: two callers, two brokers and two jobs. This source
adds only the missing broker identity objects, not their eventual authority.

Names are fixed as `cosmaneura-staging-restore-provision-broker` and
`cosmaneura-staging-restore-cleanup-broker`, with action-keyed resources/outputs.
Existing dispatcher/job names, federation, assignments and grants are unchanged.
Do not re-home the current dispatcher subscription-reader grants in this slice;
their later removal/reconciliation remains a separate live-binding prerequisite.

There is no new federation, role/assignment, secret, signing key, endpoint,
compute attachment, job read/start, mailbox write, network, workflow, restore
or production action. No Auth/Clerk/IAM policy is changed. Every broker live-proof
and runtime flag remains false. Object creation alone is not OIDC authentication,
job-control authority, signed receipt handling, or a usable broker.

## Source and approval guards

Use the existing locked AzureRM 4.73.0 provider and the existing control group.
Matching default-false enable/exact-plan inputs require the base and both fresh
CIDR confirmations through the root activation guard. No private-network flag
is required merely to model identity objects; no network or compute claim is
made. Neither broker enablement nor any storage/service approval enables the
other foundations. The two identities use `prevent_destroy` so later pinned
client/principal bindings cannot be silently replaced or removed by a flag flip.

## Validation and live acceptance

- Prove default/base/network/service plans contain no broker identities; reject
  missing/partial approvals and missing base/CIDR prerequisites. Positive mock
  plans prove two exact action-keyed names/location/parent, unchanged old IAM,
  no service activation and false live-authority outputs.
- Resource-wide mock IDs do not prove per-instance identity uniqueness. Exact
  live resource/client/principal IDs must be distinct across all six identities
  and bound to the immutable runtime record before any broker gate becomes true.
- Source edit detectors preserve existing `identity.tf`, exact identity/federation
  declaration inventory and existing role/AzAPI snapshots. They are not general
  HCL/transitive-local semantic proof; deliberate changes need renewed review.
- Run format/validate, all mocked plans, focused source/Ruff, change budget,
  exact-final-push independent review and mandatory CI before protected merge.
- Before planning AND human apply: fresh exact source/state/operator identity,
  complete network/CIDR check, exact identity-ID authenticated absence and
  independently reviewed saved plan. Reject adoption, replacement and any new
  authority beyond two empty identity objects. No timed window during absence.
- After human apply: exact name/resource/client/principal/tenant binding, six-way
  separation, zero broker federation, no assigned role or compute attachment,
  unchanged existing identities/federation/grants and empty target. Reconcile
  both direct and inherited effective authority; a source zero-grant claim does
  not prove live absence. Retain only protected digests/counts in public reports.

## Recovery and remaining gates

Before creation hold/revert with flags false; after creation retain the identities
and use reviewed roll-forward. Do not delete/recreate principals or broaden
permissions to make a test pass. Later broker compute, exact-job read/start,
create-only mailbox/signing authority, replay consumption, OIDC, no-override/
no-retry semantics and signed handoff remain separately reviewed/evidence-bound.
Human-only applies and founder-only staging/production remain mandatory.
No schema/data migration, user-facing feature flag or cross-repo rollout.

## Recorded local validation

OpenTofu 1.12.5 format/validate and all **69 provider-mocked runs passed** with
the locked AzureRM/AzAPI providers, no backend and no Azure credentials. Nine
broker runs cover seven refusals, exact two-identity shape and coexistence with
all private services while registry grants stay on jobs. Eight existing plans
assert broker dormancy. No mock claims six-way live principal/client uniqueness.

All **41 focused source tests** and Ruff lint/format pass. Four new negative
identity/federation cases reject extra declarations or silent redirection of the
existing caller federation. The unchanged old `identity.tf` SHA-256 is
`57e0842c734f7c789e9ed27fd7afe7b0325805fb51ef9848c6aa20eec8d4dff3`;
all seventeen existing role/AzAPI declaration snapshots remain unchanged.
