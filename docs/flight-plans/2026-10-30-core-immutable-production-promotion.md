# Core immutable production candidate and promotion

Risk: Tier 2. Separate Core preparation from promotion in the active canonical
workflow. This source checkpoint invokes no workflow, build, credential loader,
registry, database or runtime operation; provider/public/Scribe/staging paths
and production configuration/schema/identities remain unchanged.

## Contract and sequence

The founder manually selects `prepare` (default, cannot deploy) or `promote` on
exact current master. Both retain backend/frontend acceptance, original/rerun
founder, hosted opt-in, callback and applicable Terraform Apply/ancestry/source
controls. Ordinary pushes and retired workflows cannot enter this path.

Preparation uses the existing combined frontend/Core build. Current dark
frontend admission stays unchanged; PR896 requires separate explicit composition.
Source is checked before credentials and before/after publication. Linux/amd64,
source/config identity and the compiled public file closure inside the stopped
image are verified; the application is never started. Only the fixed production
Core repository is published. No-force import creates the candidate tag; both
tag and digest manifest must have matching protected/readable metadata before
success. No `latest` update occurs; partial publication yields no accepted artifact.

The immutable seven-day GitHub artifact contains only one `candidate.json`:
source/tree/run/attempt, image/config digests, public-configuration/assets digests,
source-pinned panel identity and disabled frontend state. A self-hash or revision
label alone is never admission. Promotion authenticates exact successful manual
founder/canonical-workflow/run/attempt/source and complete successful acceptance
jobs through fixed GitHub APIs before production credentials. Deployment must
have been skipped. Trusted normal `gh api --hostname github.com` handles archive
redirects; no returned/user URL or custom forwarded auth header is consumed.
The bounded digest-checked archive is read as one closed document without extraction.

Promotion reauthenticates evidence, fresh public configuration, remote manifest/
config and both retention protections before deployment, with no build, Nixpacks,
push, import or protection write. `deploy.sh` requires full SHA plus fixed-repo
`@sha256` before any loader; source is rechecked after backend loading, then
GitHub/1Password capabilities are dropped. All migration/template/preflight,
worker/scheduler/API, readback and drain consumers use that same digest. SHA
continues to control revisions/schema contracts; migration-first and consumers-
before-API sequencing remain. The hardcoded one-time source reuse already rejects
current master and cannot serve as an alternate caller without digest admission.

## Evidence, limits and recovery

Secretless checks cover tampering, wrong actors/refs/source/run/attempt, incomplete
acceptance, archive/path/size ambiguity, source/config/digest mismatch, staging/
public targets, retention failures, stopped-image publication and digest consumers.
Actual GitHub artifact/redirect behavior, installed client contracts, production
registry protection rights, candidate build/browser behavior, configuration,
schema/IAM/TLS/eligibility and compatible recovery remain independent live gates.
Missing rights fail closed; no new IAM assumption or production-ready claim is made.
Provider-only promotion stays a separate default-off source outcome.

On refusal stop promotion, retain partial evidence and running workloads, repair
source and prepare a new exact-current-master candidate. Never substitute tags,
rebuild during promotion, widen provenance, rerun historical snapshots or delete
additive data as recovery.
