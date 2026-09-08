# Staging sanitizer bootstrap-identity teardown evidence authenticator

This Tier 2 slice is based on protected `master`
`8c5e32e1197e83892c0487be2e259d621d276324`. It authenticates only the
reviewed retrospective evidence from the founder-applied teardown of rehearsal
`21506aaeec3c`. It performs no network, credential, provider, database,
workflow, deployment, or mutation action.

## Exact evidence boundary

The policy pins the spent plan and decoded-plan digests, the pre-apply review,
the three owner-only post-apply artifacts, exact bootstrap-identity IDs, state
lineage `a0c8b939-2fb2-0604-3e70-d925aeee5950` serial 3, the sole retained
activation-guard tombstone, five passing OpenTofu checks, six Container Apps
jobs, and five PostgreSQL servers. It requires zero identity, direct or
inherited RBAC assignment, bootstrap job attachment, or bootstrap database
administrator match. The caller may select only an absolute 0700 directory
containing the exact three single-link 0400 files.

The evidence was captured from `2026-09-08T17:42:35Z` through
`2026-09-08T17:42:44Z`, 154–163 seconds after the hard cleanup target. That
overrun is explicit and cannot become current-live-state proof. Independent
`codex-auto-review` response
`resp_0d0eabaf192e71b1006aa04a9ca2fc8190843be3319976c892` records
`POST-APPLY VERIFIED` for the exact pinned bundle.

## Output and remaining gate

A successful result is sealed, process-local, immutable, non-copyable,
non-pickleable, tamper-evident, and publicly redacted. Its stable teardown
binding is
`sha256:aa2f3f6630ad58ff5ed89aa712ef60d56b46b750d939d62be361aab142a4d283`.
It proves only the historical deletion, negative-authority observations, and
state tombstone. Current live state, identity creation or attachment,
provider/database mutation, workflow integration, and execution remain false.

Public activation remains terminal at
`activation.sanitizer_bootstrap_job_attachment_unavailable`. A later reviewed
slice must establish fresh live authority and the bounded single-use job
lifecycle; this artifact cannot recreate or authorize the deleted identity.
Recovery is code-only revert because this slice changes no external state.
