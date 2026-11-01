# Core immutable production candidate and promotion

Risk: Tier 2. Separate Core preparation from promotion in the active canonical workflow. Source only: no workflow/build/credential/registry/database/runtime operation; provider/public/Scribe/staging and production configuration/schema/identities are preserved.

## Contract and sequence

The founder selects `prepare` (default, cannot deploy) or `promote` on exact current master. Both retain acceptance, original/rerun founder, hosted opt-in, callback and applicable Terraform Apply/ancestry/source controls. Pushes and retired workflows cannot enter this path.

Preparation uses the existing combined frontend/Core build. The default remains dark. PR896's existing frontend admission contract now runs in preparation and fresh promotion: true requires the founder's exact reviewed current release SHA and fixed production origins; the public builder, publisher and promoter verify its run-local receipt. Source is checked before credentials and before/after publication. Linux/amd64, source/config identity and compiled public file closure inside the stopped image are verified; no app starts. Only the fixed production Core repository is published. No-force import creates the candidate tag; tag and digest must match protected/readable metadata. No `latest` update occurs; partial publication yields no accepted artifact.

The immutable seven-day GitHub artifact contains only one `candidate.json`: source/tree/run/attempt, image/config digests, public-configuration/assets digests and source-pinned panel identity. Schema1 remains the unchanged dark contract; an enabled schema2 candidate carries the actual admission receipt bound to its original authenticated producer source/run/configuration. Fresh promotion validates a separate current-run admission and requires identical configuration and frontend state. Neither receipt enables Core records, provider flags, IAM or production readiness. A self-hash or revision label alone is never admission. Promotion authenticates exact successful manual founder/canonical-workflow/run/attempt/source and complete successful acceptance jobs through fixed GitHub APIs before production credentials. Deployment must have been skipped. Trusted normal `gh api --hostname github.com` handles archive redirects; no returned/user URL or custom forwarded auth header is consumed. The bounded digest-checked archive is read as one closed document without extraction.

Promotion reauthenticates evidence, fresh public configuration, remote manifest/config and both retention protections before deployment, with no build/Nixpacks/push/import/protection write. `deploy.sh` requires full SHA plus fixed-repo `@sha256` before any loader; source is rechecked after backend loading, then GitHub/1Password capabilities are dropped. All migration/template/preflight, worker/scheduler/API, readback and drain consumers use that same digest. SHA controls revisions/schema contracts; migration-first and consumers-before-API sequencing remain. Hardcoded one-time source reuse already rejects current master and cannot serve as an alternate caller without digest admission.

## Evidence, limits and recovery

Secretless checks cover tampering, wrong actors/refs/source/run/attempt, incomplete acceptance, archive/path/size ambiguity, source/config/digest mismatch, staging/public targets, retention failures, stopped-image publication and digest consumers. Actual GitHub artifact/redirect behavior, installed client contracts, production registry protection rights, candidate build/browser behavior, configuration/schema/IAM/TLS/eligibility and compatible recovery remain independent live gates. Missing rights fail closed; no IAM assumption or production-ready claim is made. Provider-only promotion stays a separate default-off source outcome.

On refusal retain evidence/workloads, repair source and prepare a new exact-master candidate. Never substitute tags, rebuild during promotion, widen provenance, rerun historical snapshots or delete additive data as recovery.

This isolated composition is based on reviewed PR90247e (including PR901d833) and PR89653c, not merged/current-master production authority. Final source integration/review/CI and actual production inputs remain required. Any amendment of CorePR902 requires reviewed alignment of providerPR98's exact Core-head pin; no provider consumer, workflow or live activation is changed here. No database migration rung applies to this source-only contract composition.


## Current M2 private source integration (2026-11-01)

The existing production candidate, saved-image, runtime-binding and frontend-admission outcomes are composed onto approved protected M2 `a9bf0d70bfd4f346de3b43419074c5f032d28958`; the agent-role outcome has the same M2 base. The eleven M2 rollout-repair paths and all current Session Prep, Scribe, application and migration source are retained. The retry-safety test keeps both the production immutable-image expectation and the M2 canonical-input refusal fixture with its original assertions.

The canonical audit retains the exact 107 paths and self-exclusions. Its inventory, source-base annotation and verifier manifest anchor are rederived from the actual private combined Git tree. Verification covers the shared retry-safety fixture and fixed-control integration; prior unchanged production checks retain their original source scope. This is local source preparation. Public source admission and checks, founder-selected image/configuration/schema contracts and a separate production activation approval remain required.
