# Staging controller receipt v2 content contract

Date: 2026-09-05

## Scope

This Tier-2 slice defines the dormant, closed-world controller receipt v2
policy and a pure validator for exact intent, submission, and receipt bytes.
It pins the policy in the runtime contract while leaving
`authenticated_controller_receipt_bound=false`.

The validator rejects non-canonical JSON, duplicate and unknown fields,
missing fields, invalid digests and timestamps, cross-record mismatches,
identity drift, incorrect Azure resource types or parents, wrong target group,
incorrect FQDN or database role, isolation drift, and a false pass result. A
single injected UTC clock is sampled once per validation or freshness call.

The content contract binds the safe-submit and restore-envelope policies,
rehearsal authorization, evidence, intent, submission, restore request, launch
and runtime references, workflow/source/candidate identity, source and target
servers, restore time, job/UAMI identity, the fifteen runtime fields, resource
set digest, cleanup deadline, isolation result, and pass result.

## Trust boundary

`ValidatedControllerReceiptV2Content` means only that this module accepted the
exact bytes. Its in-process seal records validator origin; it is deliberately
named as validated content and always exposes `provenance_authenticated=false`.
It is not a runtime authority, source authentication, provider observation, or
proof that a claimed digest came from the named upstream system.

The future record-source policy fixes an intent -> submission -> receipt chain,
deterministic `{rehearsal_id}.json` names, private managed-identity reads,
create-only immutable storage, strong framing and digest metadata, and no
caller path, URI, mapping, listing, redirect, proxy, or ambient credential.
Those requirements are design constraints only in this slice.

Receipt integrity remains inspectable after expiry for cleanup. Provision use
has a separate freshness check against the receipt and cleanup deadlines.
Launch and evidence expiry authorize their historical operations and therefore
do not invalidate a receipt produced after a legitimately long restore.

## Deferred activation work

- PR-B: reviewed private-WORM reader and sealed source-authentication carrier.
- PR-C: producer plus authenticated ARM/network/database observations and
  runtime/sanitizer/cleanup consumption.
- Later activation: configured identities/resources, RBAC and WORM evidence,
  private-network proof, workflow integration, rehearsal, and explicit gates.

This slice adds no producer, reader, credentials, I/O selector, workflow,
infrastructure, deployment, restore, sanitizer, cleanup, or live-state change.
