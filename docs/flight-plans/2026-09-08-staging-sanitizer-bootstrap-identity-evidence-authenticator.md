# Staging sanitizer bootstrap identity evidence authenticator

Tier 2, based on protected `master`
`9928fcc5eb5625381efdd31b0610d76191118c90`. This bounded offline slice
authenticates the exact founder-applied bootstrap-identity checkpoint for
rehearsal `21506aaeec3c`. It performs no network, credential, provider,
database, workflow, deployment, or mutation action.

## Exact evidence boundary

The checked-in policy pins the spent plan, reviewed post-apply receipt, raw and
normalized isolated state, live UAMI readback, empty federated-credential and
role-assignment arrays, the complete six-job subscription inventory, and each
job's identity projection. Evidence files must be regular, single-link,
owner-only files and must match their exact SHA-256 values. The caller may
select only the containing directory, not any filename, identifier, claim, or
digest.

The verifier requires state lineage
`a0c8b939-2fb2-0604-3e70-d925aeee5950` to advance exactly from serial 1 to 2,
with only the existing resource-group read, the bootstrap UAMI, and the
built-in activation guard. It cross-checks the exact resource, client,
principal, and tenant IDs; the complete controlled tag set; four passing
OpenTofu checks; zero federation; zero direct or inherited role assignments;
and zero bootstrap-UAMI attachments across all six observed Container Apps
jobs. Azure resource IDs are compared case-insensitively.

## Claims and expiry

A successful process-local result proves only that the reviewed retrospective
evidence and exact identity IDs are pinned. It intentionally keeps current
live-state authentication, single-use job binding, provider/database
authority, workflow integration, and execution false. An object-bound MAC with
a closure-held process key and strong issuance registry rejects forged results. The public
projection exposes only a binding digest and non-authoritative metadata—not raw
evidence or exact identity values.

The authenticator rechecks a whole-second UTC clock before and after all file
reads and refuses at or after `2026-09-08T17:40:01Z`. Its result cannot extend
the identity lifetime. A later slice must add fresh authenticated provider
readback and integrate the sealed evidence into the bootstrap binding plan
before any reviewed single-use job attachment or authority transition.

Recovery is code-only revert. The already-applied UAMI remains governed by its
separate reviewed teardown path and must never be removed by reusing the spent
creation plan.
