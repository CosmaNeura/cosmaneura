# Staging sanitizer bootstrap-identity teardown evidence authenticator

This Tier 2 slice is based on protected `master` `0e307bb0aeb631fed82c89b1a6fbf0d810b06254`. It authenticates only the reviewed retrospective evidence from the founder-applied teardown of rehearsal `21506aaeec3c`.
It performs no network, credential, provider, database, workflow, deployment, or mutation action.

## Exact evidence boundary

The policy pins the spent plan and decoded-plan digests, pre-apply review, four owner-only post-apply artifacts (including raw isolated state), exact bootstrap-identity IDs, state lineage `a0c8b939-2fb2-0604-3e70-d925aeee5950` serial 3, the sole retained activation-guard tombstone, five passing OpenTofu checks, six Container Apps jobs, and five PostgreSQL servers.
It requires zero identity, direct or inherited RBAC assignment, bootstrap job attachment, or bootstrap database administrator match. The caller may select only an absolute 0700 directory containing exactly four single-link 0400 files.

The evidence was captured from `2026-09-08T17:42:35Z` through `2026-09-08T17:42:44Z`, 154–163 seconds after the hard cleanup target. That overrun is explicit and cannot become current-live-state proof.
Independent `codex-auto-review` response `resp_03523e3973d13b5f006aa0708f1c508193bb230fa1f162fb91` records `POST-APPLY VERIFIED` for the exact pinned bundle.

## Output and remaining gate

A successful result is sealed, process-local, immutable, non-copyable, non-pickleable, tamper-evident, and publicly redacted. Its stable teardown binding is `sha256:12d56cafbc2edbd5ca8e1a1fee5f7b07e37ed32140eba7fad3bb129e9c6224e4`.
It proves only historical deletion, negative-authority observations, and the state tombstone. Current live state, identity creation or attachment, provider/database mutation, workflow integration, and execution remain false.

The process-local provenance registry and verifier are closure-bound, weak-reference lifetime-bounded, and bind the exact ordered value snapshot. An authenticated value inherited by a forked process is invalid, and changing lookalike module globals cannot invalidate an issued value or authenticate a forged copy.
Code with permission to replace the module, class methods, or public entry point inside the same interpreter is outside this artifact's trust boundary and must not share the verifier process.

Public activation remains terminal at `activation.sanitizer_bootstrap_job_attachment_unavailable`. A later reviewed slice must establish fresh live authority and the bounded single-use job lifecycle; this artifact cannot recreate or authorize the deleted identity.
Recovery is code-only revert because this slice changes no external state.
