# Staging sanitizer bootstrap identity evidence authenticator

Tier 2, based on protected `master` `9928fcc5eb5625381efdd31b0610d76191118c90`.
This bounded offline slice authenticates the exact founder-applied bootstrap
identity checkpoint for rehearsal `21506aaeec3c`. It performs no network,
credential, provider, database, workflow, deployment, or mutation action.

## Exact evidence boundary

The checked-in policy pins the spent plan, reviewed post-apply receipt, isolated
state, live UAMI readback, empty federation/role arrays, complete six-job
inventory, and every job identity projection. Files must be regular,
single-link, owner-only, and exact-hash matched. The caller selects only the
containing directory—not a filename, identifier, claim, or digest.

The verifier requires lineage `a0c8b939-2fb2-0604-3e70-d925aeee5950` to advance
exactly from serial 1 to 2 with only the resource-group read, bootstrap UAMI,
and activation guard. It cross-checks exact IDs, tags, four passing OpenTofu
checks, zero federation or roles, and zero bootstrap-UAMI attachments across
all six jobs. Azure resource IDs are compared case-insensitively.

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
the identity lifetime. The sealed retrospective evidence is now an exact
retained input to the bootstrap binding plan. A later slice must still add
fresh authenticated provider readback before any reviewed single-use job
attachment or authority transition.

Recovery is code-only revert. The already-applied UAMI remains governed by its
separate reviewed teardown path and must never be removed by reusing the spent
creation plan.
