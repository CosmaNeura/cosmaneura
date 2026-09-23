# Combined Scribe and Session Prep staging release

Tier 2: clinical source provenance, cross-service authority, additive schema and staging deployment controls. Owner authorization in the September 23 staging takeover request covers local fixes, reviewable PRs, local checks, independent Cursor review after the final substantive push, merges with a recorded narrow override, migrations and staging deployment. GitHub Actions remains disabled. Production and administrative/compliance work are excluded.

## Source and integration

Core starts at master `986b2c717fe65e390a5b6be404ac61ef20380232` and imports the preserved, committed combined checkpoint `593237f4054965792c0ae3858d2e82d0153e376d` (substantive source `028a224ffa5cdd89bb4465382a39251e9cdeadbc`). Its large capture import has no reusable finer committed Core history. Split models/migrations, contracts, transport, authority, admission, sources, notes, edits, gateway, tests, routes, dashboard and release controls into dependent PRs within the repository budget. Deploy only the complete merged stack.

Current Core18 application/migration files match all 294 files extracted from the preserved Core17 image. Core18 corrected async dependencies already present in the combined checkpoint. The separate Scribe API/receiver/native revisions contain later work that is not in the newest stable committed Scribe head `56cc215b0ececfe12375c78c50efd84f897e2b10`. Preserve those running immutable service images and installed clients; do not rebuild them from an older source or deploy uncommitted working files. No active working checkout is changed or other Codex task contacted.

Sovereign is separate and stateless. Reconcile the provider mount with its reviewed main at `33459e6`; preserve the public mount unless compatibility requires an explicit reviewed change. Core web, worker and scheduler must all receive one exact combined Core image.

## Observed staging and recovery

Staging: `https://staging-app.cosmaneura.com`.

- Core web: `cosmaneura-staging-app--0000018`, `scribe-beta/core@sha256:8fefe5d01540d0633a957fc59567e3bb27b0cdbe43330568903c2d4e7fb54bdf`.
- Worker/scheduler: suffix `r1789668418a1-ace8197`, `cosmaneura/cosmaneura@sha256:a2c2a736651b7d24965da0866836c0de027e3589dcecdf825d4dc2e5a51d42cc`.
- Scribe API: `--0000003`, `scribe-beta/api@sha256:b6560a83449d0bc8a98d3b97c99c48776126e0c568b2c27ec969928e58f240cf`; speech and translation jobs use this same image.
- Scribe receiver: `--0000007`, `scribe-beta/receiver@sha256:bfd1c23ac58204089d5fcdd7d66e72aece51e0fd9e37aa85f1fd7e5098296fea`.
- Sovereign provider: `--gpt41-20260825`, `sha256:e37370a7746a1618927a88987c77c192f0891f5e4b87d24a349a234eaaeb649d`.

Core and Scribe are in `cosmaneura-staging`; Sovereign is in `cosmaneura-sovereign-staging`. All changes target subscription `bab2a709-1fcb-4c87-a9fc-802f7477ec13`. Keep exact observed recovery revisions/images and configuration evidence for each workload; the baseline is already split and cannot be represented honestly by one digest.

## Fixes and retained limits

A retained synthetic admission has an existing matching Scribe consent but Core reports `consent_unconfirmed`. A replay with the old 15-second live timeout failed in transport; the same idempotent request after the service warmed returned the existing consent in 0.6 seconds with matching fields. This narrows the failure to transport/startup timing but does not prove cold start is the sole cause. Keep Scribe API warm at one staging replica. Align Core's phase deadline with its supported 60-second HTTP budget and keep the lease longer. Record only bounded error codes. Preserve consent/session keys and null-attestation recovery; never create replacement consent to conceal an ambiguous result.

Dashboard notes retain exact per-note transcript association and explicit provider review. Only reviewed current notes feed a later Session Prep; corrections invalidate an older result until the replacement is reviewed. Background Recall/upload jobs cannot overwrite a capture-owned note. For a retained capture without a note, the dashboard explains that the provider must resume in Scribe or use a new visit for a separate upload. Automatic transfer/reassignment of an abandoned capture is deferred. Older merged multi-segment Recall notes still expose their latest linked segment. Device microphone and native background behavior require the installed device interaction and must be reported separately from browser/API checks.

## Schema and verification

Actual staging Core head is `c8526f5e8d80`; candidate head is `56f2b01ac226`, joining the provider branch `de9260834611`. Rehearse this actual path and the provider-only path with owned synthetic local databases. Verify original rows and constraints/indexes/triggers are preserved, and restore a synthetic candidate dump into a separate database. No live clinical export or destructive downgrade is authorized. Recovery switches application revisions while keeping additive schema and records intact.

Run application/capture/authority/transport/Session Prep tests, per-note browser tests, frontend type/build checks, release-controller/publisher/deployer tests, secret/dependency scans and independent Cursor review after final substantive pushes. Reuse unchanged dependency audit evidence with exact lockfile binding. A later substantive push requires renewed review.

Release-tool version 2 replaces historical candidate constants only with hash-bound plan and publication evidence. It retains fixed staging targets, a clean canonical controller on protected master, exact merged candidate PR identity, authenticated founder identity, disabled Actions, immutable archive/image binding, pre-mutation source rechecks, migration template guards, exact schema observations and per-workload recovery. Local Cursor review is published as a head-bound report; it is explicitly not a GitHub approval.

After deployment, verify browser access and synthetic recording/upload through transcript and note, explicit review, later-encounter Prep, correction invalidation, background processing and persistence across refresh/restart. Container health alone is insufficient. Preserve labs and unverified referral gates as disabled. Enable provider Prep only for existing verified testing subjects; do not use a synthetic demo fallback to claim a live provider workflow works.

## Repeatable local release

1. Fetch into isolated checkouts. Compare committed heads, current image/source/configuration receipts and actual schema. Integrate both dashboard features in Core; retain separate service targets.
2. Assemble small dependent PRs. Run focused local checks and migration rehearsal against the final tree. Push, obtain independent Cursor review for each exact head, publish the review findings/dispositions, then merge the complete stack into master. If required, narrowly disable admin enforcement for the merge and immediately restore/verify the original setting; retain before/after evidence.
3. From the clean current master controller, create a version-2 build plan. Bind the exact merged candidate/PR, controller/head-bound Cursor report, helper/archive/frontend hashes, engine identity, actual rehearsal evidence, exact public Scribe/Prep configuration and each live workload's recovery image/revision. Use a fresh two-hour plan and retain every build attempt separately.
4. Run `python -I -S -B scripts/staging_build_controller.py --check-only PLAN PLAN_SHA256`, then the controller's build invocation. Run `infra-staging/publish-local.sh PLAN PLAN_SHA256 CONTROLLER_RESULT RESULT_SHA256`. Publication consumes the verified local archive and locks the exact candidate tag.
5. Run `infra-staging/deploy.sh MERGED_SHA IMAGE_DIGEST` with founder-user mode, the same hash-bound plan, publication receipt/hash, approval PR, expected before/after schema and staging identity fingerprint set. Keep `ENABLE_PROVIDER_V7=true` and `ENABLE_SOVEREIGN_RECORD_GRANTS=true`. The publication receipt selects v2; historical invocations retain the original fixed candidate. The deployer validates recovery availability and current revisions before applying additive migrations, then updates all three Core workloads to the exact image.
6. Update a separate service only through its reviewed fixed-target release path. Verify both features in the actual provider UI with synthetic records and record immutable identities, retained limits, tests and recovery. Stop at the verified staging handoff.

Private local evidence: `/private/tmp/cosmaneura-staging-release-evidence-20260923`. Final PR review comments and release receipt supply the actual merged/deployed identities and completed verification; this flight plan does not claim deployment has happened.

### Checked runtime recovery and warm Scribe API

From the same clean merged controller, run `python scripts/staging_runtime_operations.py recover-check PLAN PLAN_SHA256 PUBLICATION PUBLICATION_SHA256` to verify all three retained revision templates and current candidate/old image identities. `recover` with the same arguments copies each old revision's complete template into a new recovery revision, scheduler then worker then web. It checks each app for intervening changes and verifies each restored image is Healthy/Provisioned and latest-ready. The additive database and captured records are retained. Recovery remains available after build-plan expiry; it accepts only the bound retained revisions and refuses an unrelated current image.

For the separate Scribe API, create a hash-bound baseline with exactly `name`, `revision`, `image` and `template_sha256`. The template hash is `staging_runtime_operations.digest(stable_template(observed_template))`; only the generated revision suffix and minimum replica count are excluded. Run `warm-check PLAN PLAN_SHA256 PUBLICATION PUBLICATION_SHA256 BASELINE BASELINE_SHA256`, then `warm` with the same arguments. This can only set the fixed existing Scribe API's minimum and maximum replica count to one. Its image and all other template fields must remain unchanged. Both runtime operations use the founder's validated management token directly through the bound Azure wrapper; no shared-cache identity substitution or production selector is permitted.

The current protection profile requires one classic approving review, the three existing required checks and admin enforcement. Ruleset 3778800 remains active with its recorded two Integration bypass actors and non-fast-forward rule. The older snapshot/build path retains its historical profile. These validators read and preserve policies; only the separately recorded narrow merge override is temporary.

Before merging, a fresh read observed that GitHub's required status checks had been removed and its required approving review count changed to zero, while admin enforcement and the ruleset stayed unchanged. This release did not make that settings change. The local-Cursor path also accepts this exact local-only profile (no required checks, zero approving reviews), retaining admin enforcement, no force-pushes/deletions, conversation resolution and the exact ruleset. It does not accept a partly changed checks/review combination. Preserve the immediately observed policy and merge without an override when it permits that; do not reinstall the superseded GitHub checks. Independent head-bound Cursor review, local checks, source and image verification remain required.
