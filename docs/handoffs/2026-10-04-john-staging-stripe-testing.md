# John full staging testing handoff

Prepared October 4, 2026 for John, using GitHub account `JohnClem`.

Purpose: prepare full manual and automated staging qualification of Cosmaneura SOAP notes, Stripe test payments, native Scribe and session prep. This handoff does not authorize production changes, live Stripe charges or real patient data.

Status: not ready to deploy or claim full staging qualification. Portal, seed and maintenance PRs remain unmerged. The merged version-2 local release controller is implemented and locally tested; repository-level Actions remains disabled. Recommendation: have John confirm the existing local-founder path for this release, rather than re-enable Actions. Live deployment inputs and authenticated/device acceptance remain outstanding.

## What we need from John now

John, we have prepared isolated fixes, local tests, independent reviews, and release instructions. We need your release decisions and privileged setup, not additional implementation work. Full staging testing is still blocked; this checklist does not authorize production, live charges, real patient data, or bypassing access gates.

1. **Confirm the approved staging release path.** Confirm the existing Actions-off version-2 local-founder controller for this exact synthetic staging release, and reconcile the older workflow-only policy through review. Keep Actions disabled and preserve repository protections. Return the approved procedure and operator; do not simply deploy moving master.
2. **Choose and review the integrated candidate.** Core portal PR 672 and maintenance PR 673 are the billing prerequisites; optional seed PR 671 provides owner/workforce testers without bypassing agreements, payment, invitations, or onboarding. Core PRs 674–676 supply test repairs and the browser harness. Scribe PRs 263 → 264 are stacked JavaScript fixes; PR 265 patches pytest, PR 261 patches urllib3, and PR 266 qualifies PyJWT on current main. Choose PR 266 or PR 260 with separately integrated qualification, not two competing auth releases. These PRs remain unmerged; require tests and independent review on the actual integrated candidate. Scribe PR 267 (stacked on 265) fixes the three baseline test failures and native-helper lint: 1,512 passed, 6 skipped, Ruff and Pyright clean, independently reviewed. Core PR 677 repairs the 30-file formatting gate without changing Python ASTs. Core PR 678 qualifies bounded Python tooling security updates: 977 isolated authority tests and five tooling regressions pass; 707 external-fixture tests are skipped. Core PR 679, stacked on 678, qualifies the separately authorized PyJWT 2.15.1 update without changing production auth code or identity configuration: 37 focused auth cases and 1,014 isolated authority tests pass, with 707 external-fixture skips. The installed candidate’s Python vulnerability scan reports no known findings; Git sources and default-branch alerts are not fully cleared by that result. Core still has 568 reproduced baseline type errors. Real staging identity/provider/browser/device/payment acceptance remains outstanding. These are not waived or a full clean-release claim.
3. **Authorize and perform the founder staging rollout.** Verify synthetic-only scope, current per-workload revisions/digests/schema/configuration and recovery evidence; choose how to reconcile drift. Bind the intended Scribe, session-prep, identity and real-provider settings in the reviewed plan. Use the approved controller to release the exact merged candidate. Return the rollout receipt, image digests, observed effective flags, schema and health results. We can prepare and inspect evidence but cannot substitute for your founder-authenticated rollout.
4. **Activate Stripe test billing in two stages.** Select and verify the staging 1Password Stripe item privately. First deploy integration enabled, new Checkout disabled and live mode false; verify account/price/portal, historical Checkout inventory and webhook configuration. Record the drain start, wait at least 24 hours 5 minutes and pass the activation preflight. Only then approve/deploy new test Checkout. Return sanitized activation evidence and enough owner access to perform an actual test Checkout → signed webhook → durable practice-access update. Do not mark the practice paid or grant a billing exemption as a substitute.
5. **Provide tester access and supported Scribe distribution.** Privately confirm an existing staging login and the intended synthetic practice/role; provide an owner for billing and a workforce clinician for permission checks, using the reviewed seed only if needed. Supply the supported Mac installer and its version/source/digest/staging binding; for iPhone, supply the actual Scribe TestFlight invitation. A development package or installation of the TestFlight app is not native acceptance. Shawn will obtain tester access and the Mac build tomorrow.

**Ready for us to resume live testing means:** approved rollout evidence is available; owner and workforce logins reach their correct workspaces; test Checkout and signed webhooks work; real Scribe/session-prep runtime bindings are verified; supported tester packages are available. We will then run and record SOAP, Stripe, Scribe and session-prep acceptance with synthetic data. A reported account, merged PR, local mock, build, or pilot exemption is not a pass.

This Page remains private unless explicitly shared; editing it does not deliver it to John or grant him access. Send him the content or share it through your normal process.

## Current evidence and missing work

- [Portal fix PR 672](https://github.com/CosmaNeura/cosmaneura/pull/672) is draft and open, with no merge commit and no CI checks reported at the October 4 inspection. Reviewed branch commit: `8275615b7a0a5d4771cac985c363d5d44633a55f`. This is not a deployable merge SHA.

- Its October 3 local evidence: 338 combined billing/access tests and 541 frontend tests passed; production-bundled test-target build and scoped lint/type checks passed. Existing build/test warnings remain. [Independent static review](https://github.com/CosmaNeura/cosmaneura/pull/672#issuecomment-5969853880) found no blockers; it is not human approval or deployed qualification.

- [QA seed PR 671](https://github.com/CosmaNeura/cosmaneura/pull/671) is also draft and unmerged, with no CI checks reported. Its 28 offline tests and [independent review](https://github.com/CosmaNeura/cosmaneura/pull/671#issuecomment-5969462294) do not establish live identity or billing readiness. No accounts or data were seeded.

- No staging deployment, Azure mutation, billing activation, or complete Checkout-to-webhook transaction was performed by this audit.

The actual browser session was `release-qa-20261002+clerk_test@gmail.com`, in “Cosmaneura Release QA - Synthetic Practice”. Its BAA was accepted, but access was paused and online reactivation unavailable. Do not create another practice to evade this gate or fabricate payment/consent.

A separate Codex window reported that `dev@cosmaneura.com` has active Cosmas MD membership, completed onboarding and clinical pilot access. This window has not independently verified it. John should privately provide the existing password and verify access; do not reset or replace the account. Pilot access is not Stripe qualification.

## Decisions and prerequisites before deployment

- [ ] Preserve the currently observed merge policy. On October 4, Core repository Actions was disabled, classic branch protection had no required statuses and zero approving reviews, and admin enforcement and conversation resolution remained enabled. Inspect all applicable rulesets before merging; do not fabricate statuses, re-enable Actions or invent a required human approval. Relevant local checks and independent final-SHA review remain necessary.

- [ ] Review and merge PR 672 using the normal protected-master process.

- [ ] Review maintenance configuration [PR 673](https://github.com/CosmaNeura/cosmaneura/pull/673), head `55cc51040bb89d53c543f458310271231eaac2ca`. It pins the existing staging Stripe item and sets integration true, acquisition false, live false. Include PR 672 in the eventual merged candidate; no rollout is authorized by this draft.

- [ ] Independently verify staging is synthetic-only and establish its current API, worker and scheduler revisions, image digests, database revision, traffic and effective configuration.

- [ ] Record any drift from the intended release and choose keep, assimilate or replace. Do not blindly overwrite live staging with repository defaults.

- [ ] Bind and verify Scribe, Prep, identity and provider settings in the version-2 plan. The default path forces workflow off; version 2 deliberately overlays the reviewed runtime group, requires Scribe capture/workflow and real provider Prep enabled, and retains synthetic Prep and legacy handoff off. Do not infer deployed values or broaden the existing provider cohort.

- [ ] Verify the selected local operator host has the exact pinned Python, GitHub CLI, Azure CLI, Docker and jq tooling required by the merged publisher/deployer. Verify founder identity, protected master, disabled Actions and the complete current protection/ruleset profile. No settings changes are part of this preparation.

- [ ] Collect and review the actual workflow inputs in the table below. Retain the underlying evidence privately; a hash is not a replacement for review.

Technical reconciliation: merged master contains the Actions-off version-2 [build controller](https://github.com/CosmaNeura/cosmaneura/blob/49895cb66a305558393a0862a590ee313b60c194/scripts/staging_build_controller.py), [publisher](https://github.com/CosmaNeura/cosmaneura/blob/49895cb66a305558393a0862a590ee313b60c194/infra-staging/publish-local.sh) and [deployer](https://github.com/CosmaNeura/cosmaneura/blob/49895cb66a305558393a0862a590ee313b60c194/infra-staging/deploy.sh). Source verification requires repository Actions disabled and accepts the bounded local-review protection profile. Version 2 derives the candidate/image/schema from the hash-bound plan and receipt, then applies its reviewed Scribe/Prep public configuration. Core AGENTS still calls Actions the only staging path; do not silently edit or bypass that policy. Recommendation: John confirms the existing local-founder procedure for this exact release and reconciles that documentation through review. No Actions re-enablement or deployment-controller change is proposed.

## Configure test billing without opening Checkout

The October 4 protected-master [staging configuration](https://github.com/CosmaNeura/cosmaneura/blob/49895cb66a305558393a0862a590ee313b60c194/.env.staging.backend) still exports both Stripe flags false and leaves the item selector empty. Draft PR 673 prepares maintenance only. Source defaults are not deployed-state evidence; merging the portal repair alone does not activate billing.

The prepared maintenance PR uses the following values; fresh vendor verification and review are still required:

```text
STRIPE_INTEGRATION_ENABLED=true
STRIPE_BILLING_ENABLED=false
STRIPE_LIVE_MODE=false
STRIPE_BILLING_ITEM_ID=6r5z4p4qzvluw7luh72zfbmbui
STRIPE_API_VERSION=2025-09-30.clover
```

Select the staging Stripe vault item containing `secret-key`, `webhook-secret`, `account-id`, `price-id` and `portal-configuration-id`. Keep actual credentials in 1Password and the approved runtime secret mechanism, not Git, chat, command arguments, screenshots or shell history.

Verify the actual test account, intended price, pinned portal restrictions and staging webhook signing secret. The earlier GET-only audit confirmed a test-mode account, active USD 89-per-clinician monthly price and one enabled matching webhook endpoint with the nine required lifecycle events. It did not verify signing-secret correctness, delivery or deployed flags. Recheck before activation.

Confirm the portal meets the [updated configuration requirements](https://github.com/CosmaNeura/cosmaneura/blob/8275615b7a0a5d4771cac985c363d5d44633a55f/docs/stripe-billing-activation.md#L16). Do not enable subscription pause, customer plan/quantity updates or unintended no-code portal entry points.

## Verified inputs for each deployment

These are the canonical [workflow input contracts](https://github.com/CosmaNeura/cosmaneura/blob/49895cb66a305558393a0862a590ee313b60c194/.github/workflows/deploy_staging_candidate.yml#L5), not authorization to dispatch. If the approved local-founder controller is selected instead, collect that controller's complete hash-bound plan/publication/recovery contracts; do not translate guessed values into this table.

| Input | Required value and source |
| --- | --- |
| `candidate_sha` | Full lowercase 40-character SHA of the selected reviewed PR’s merge commit, contained in protected master. Must include the portal repair in its ancestry. Missing until that PR is merged. |
| `approval_pr_number` | The merged master PR whose exact merge commit equals `candidate_sha`. If selecting the later configuration PR, use that PR—not automatically 672. |
| `synthetic_only_confirmed` | True only after verifying staging migration and smoke evidence contains synthetic data only. |
| `synthetic_evidence_sha256` | `sha256:` plus 64 lowercase hexadecimal characters, from reviewed sanitized-restore/synthetic evidence. Not collected here. |
| `staging_identity_fingerprints_sha256` | Hash of the independently reviewed staging Clerk/Zoom/Recall/Stripe-test fingerprint set, in the workflow’s canonical format. Not collected here. |
| `expected_schema_before` | Actual reviewed staging Alembic revision, 12 lowercase hexadecimal characters. Not collected here. |
| `expected_schema_after` | Reviewed migration head for the selected candidate, same format. Do not assume it equals the current schema merely because the portal patch has no migration. |
| `recovery_digest` | Verified immutable recovery image digest, `sha256:` plus 64 lowercase hexadecimal characters, matching the current recovery workloads. Not collected here. |

Do not substitute the branch commit, a moving master tip, a random hash, or guessed schema/digest. A newer protected-master control change can affect configuration independently of the older candidate code; record both revisions.

## Recommended local release procedure

1. John confirms the existing Actions-off version-2 local-founder release path for this exact synthetic staging release. Preserve the current protection profile and founder-only authority. This handoff does not itself grant that approval.

2. Review/merge the bounded PRs and select the exact merged candidate including portal repair PR 672 and maintenance configuration PR 673. Observe live per-workload source/images/configuration/schema and decide how to reconcile every drift item before selecting recovery.

3. Assemble the fresh hash-bound version-2 plan using the merged controller: exact candidate/PR, controller and independent local review, authenticated identity, source/helper/frontend/native hashes, rehearsal evidence, real-provider runtime group and each Core workload's actual recovery image/revision. No fabricated plan values or historical schema/digest substitutions.

4. Follow the merged [combined release runbook](https://github.com/CosmaNeura/cosmaneura/blob/49895cb66a305558393a0862a590ee313b60c194/docs/flight-plans/scribe-session-prep-staging-release.md). First use the build controller's `--check-only` and `--verify-source` phases with the reviewed plan/hash. They do not publish or deploy. Only the explicitly authorized invocation may run the credential-free build; retain the controller result and every attempt.

5. The founder-authenticated operator publishes that verified archive with `infra-staging/publish-local.sh`, retaining the publication receipt/hash. Deploy through `infra-staging/deploy.sh` in `founder-user` mode with the same version-2 plan and receipt, exact merged SHA/image digest, expected schemas and identity fingerprint set. Do not invoke the historical fixed-candidate path or issue an ad hoc container update.

6. Record immutable candidate/controller/image identities, plan/publication hashes, observed schemas/configuration and readiness for web, worker and scheduler. Verify the planned Scribe/Prep settings were applied and Checkout remains closed while test-mode subscription maintenance operates. Health alone is insufficient; use the genuine synthetic acceptance checklist below.

The selected candidate may require additive migrations even though the portal/configuration PRs have none. Recovery retains additive schema and records and restores each verified workload's complete retained template; do not collapse a split baseline into one invented digest. Agents must not invoke deployment or Azure mutation commands.

## Drain old Checkout sessions and verify activation

Follow the [billing activation runbook](https://github.com/CosmaNeura/cosmaneura/blob/8275615b7a0a5d4771cac985c363d5d44633a55f/docs/stripe-billing-activation.md#L83).

Record `DRAIN_STARTED_AT` in UTC as the later of acquisition being disabled and the last old revision leaving service. Keep integration and webhooks enabled. The current runbook requires at least **24 hours and 5 minutes**; an empty session list does not waive that interval.

Inventory every current and retired price that could identify a Cosmaneura Checkout URL in the expected test account. Reconcile completed legacy subscriptions that lack a practice reference; do not assign them by guess. Inject `STRIPE_SECRET_KEY`, `STRIPE_ACCOUNT_ID`, `STRIPE_PRICE_ID` and `STRIPE_LEGACY_PRICE_IDS` privately. Empty legacy inventory is valid only after verifying there were no other historical prices.

From the reviewed checkout, using the project’s Python toolchain, run this read-only preflight only after the assertions are true:

```sh
python scripts/stripe_checkout_activation_preflight.py \
  --operator-ref "$BILLING_CHANGE_REF" \
  --drain-started-at "$DRAIN_STARTED_AT" \
  --confirm-acquisition-disabled \
  --confirm-old-code-stopped \
  --confirm-price-inventory-complete
```

Set `BILLING_CHANGE_REF` to the approved change record. Staging must omit `--live-mode`; do not put a secret value into the command.

Retain the aggregate JSON and exit status. Require exit 0, correct account/mode and price inventory, all confirmations true, `drain_satisfied: true`, zero eligible and ambiguous Cosmaneura open sessions, and `ready_for_acquisition: true`.

If eligible sessions remain, stop and review the exact dry-run plan. An authorized operator may use the runbook’s bounded `--apply --confirm-plan-sha256` procedure, which expires sessions and rescans. It is a Stripe mutation, not part of the read-only command above. Never clear local Checkout state, cancel subscriptions, or expire ambiguous sessions as a shortcut.

## Enable Checkout and prove the customer journey

Only after passing activation evidence, prepare the second reviewed configuration rollout:

```text
STRIPE_INTEGRATION_ENABLED=true
STRIPE_BILLING_ENABLED=true
STRIPE_LIVE_MODE=false
```

Use the reconciled approved release process again, with fresh candidate/input evidence. Preserve integration and webhook maintenance. Confirm deployed effective flags and the pinned test account, rather than inferring success from a merged configuration file.

Then use a verified practice owner and synthetic data only:

- [ ] Checkout opens for the intended practice and seat count, using Stripe test payment data and requiring a payment method.

- [ ] Successful payment/trial produces an authentic signed webhook; processing updates the correct practice’s durable subscription and entitlement state.

- [ ] The paused-access screen clears and clinical tools become available.

- [ ] The billing portal opens with the pinned restrictions.

- [ ] Test payment failure/recovery and cancellation; observe the documented access behavior.

- [ ] Invite/accept/revoke a workforce clinician, verify seat accounting and denial of owner billing controls.

- [ ] Complete synthetic SOAP generation, editing, saving, reopening and version history, then finalized-note session prep with provider/patient isolation.

No billing exemption, fabricated paid status, seeded BAA or manually completed processing job substitutes for this evidence. The Gmail practice needs real test billing. The dev pilot account can support a separate clinical walkthrough but does not establish owner billing permissions.

## Test accounts and native Scribe handoff

PR 671 is optional for a repeatable second scenario: `biller@cosmaneura.com` uses owner permissions including billing and clinical access; `doctor@cosmaneura.com` uses workforce clinical permissions. Neither is a superadministrator. After review/merge, follow its [seed runbook](https://github.com/CosmaNeura/cosmaneura/blob/dd936f2cc22d1015e660187014b4f08b3b20714f/docs/testing/STAGING_MANUAL_QA.md). Normal agreements, real test Checkout, invitations and onboarding still apply. Resolve any managed-domain reservation through its proper access path; do not adopt existing unmarked addresses.

Native Scribe is a separate dependency. Another window reported a pending iPhone TestFlight invitation and no usable supported Mac tester download. John needs to provide the supported packages/invitation and verify the native staging connection before recording/handoff acceptance. Installing Apple TestFlight alone is not a Scribe invitation. Do not assume this billing rollout enables Scribe or session-prep runtime flags.

## Work we can complete without John deploying

We prepare and test isolated PRs, independent reviews, configuration changes, synthetic fixture scripts, test scenarios and sanitized evidence. We can run read-only vendor/state checks when access is available and perform authorized synthetic browser/device tests once the environment is usable. John need not write code or manually run every test.

John retains the release decision and founder-authenticated rollout, any separately authorized live data/configuration operation, and approval or distribution access for supported Mac/iPhone tester packages. We cannot substitute local mocks for these dependencies.

## Full staging acceptance

Record every case as pass, fail, blocked or not run. Include tested source/image/build versions, platform, opaque synthetic test references and sanitized evidence. Never count a skipped or mocked test as deployed end-to-end success.

| Area | Manual staging acceptance | Automated evidence required |
| --- | --- | --- |
| SOAP | Fictional visit with distinctive facts; generate grounded SOAP, edit, save, reopen, check history and persistence. Retry a processing failure without duplicate notes; another provider/patient cannot see it. | Generation/source-selection, note ownership, save/recovery/history and cross-provider isolation contracts on the exact integrated candidate; authenticated staging smoke still required. |
| Stripe | Owner Checkout with test data, correct seats, signed webhook and durable access update; restricted portal; payment failure/recovery, cancellation and invitation/revocation seat changes. Workforce cannot use owner billing controls. | Billing routes/services, replay/idempotency, entitlement/seat isolation, deployment wiring and activation preflight; real signed test delivery must be observed, not mocked. |
| Scribe | Supported Mac and iPhone builds show staging/account; consent before recording, timer advances, explicit stop/upload, transcript arrives once and grounds SOAP. Reopen/restart and interrupted-upload recovery retain the same encounter; permission denial has a clear recovery path. | Consent, tenant scope, session state, transcript/handoff, encrypted queue and native process/crash-recovery tests. Installed microphone/system audio, iPhone interruptions and production-provider connection remain distinct device tests. |
| Session prep | Review the current SOAP, open a later visit, generate prep from the correct prior reviewed note with no future/foreign source. Edit the source and verify invalidation until replacement review; refresh/reopen retains the correct state. | Source grounding, reviewed-note selection, invalidation, async retry, runtime authority and provider/patient isolation contracts; run the genuine provider workflow, not a synthetic demo fallback. |

Across all four areas check keyboard focus, clear loading/error/retry states, responsive layouts, consistent navigation and no loss of entered work. Capture timing and whether refresh is needed. UI excellence is not established by lint/build success.

## October 4 local verification

The merged release path's local contracts passed 115 tests plus 164 subtests across source provenance, build controller, plan/publication binding, runtime recovery and build inputs. Tests used invented provider responses, not live Azure. Evidence binds master `49895cb66a305558393a0862a590ee313b60c194`; fresh read-only GitHub checks confirmed that SHA and repository Actions disabled. The recommendation resolves the technical choice, not the remaining founder decision or live inputs.

Maintenance PR 673: 51 tests and 66 subtests passed; scoped Ruff, formatting, Pyright and diff checks passed. Independent reviewer additionally passed 10 deployment-contract and 4 activation-preflight tests with no blockers. The wider run reproduced one watchdog fixture assertion failure on unchanged master: two valid disabled declarations now exist in separate Terraform scenarios. A separate test-only repair is prepared in draft [PR 674](https://github.com/CosmaNeura/cosmaneura/pull/674), with 10 focused tests and scoped lint/types passing. It checks each override separately, including nested scenarios, and rejects enabled, missing or duplicate flags. Do not claim a completely green master.

Earlier dirty-tree checks passed 475 JavaScript tests with 32 native-opt-in skips. A newer isolated Scribe branch, draft [PR 263](https://github.com/CosmaNeura/cosmaneura-scribe/pull/263), now passes clean npm install, 852 JavaScript tests (44 existing opt-in skips), lint, types, provider/synthetic capture builds, preview API build, generated API and preview-bundle checks. Exact committed head: `d388cc91f3e5b3795224f917d6048764f0325270`. [Independent final-head review](https://github.com/CosmaNeura/cosmaneura-scribe/pull/263#issuecomment-5985660422) found no blockers. The PR patches development-tool dependencies and runs two Node suites with their correct runner instead of miscollecting them in Vitest. Those three related moderate Capacitor/Xcode/uuid reports are resolved on the stacked draft [PR 264](https://github.com/CosmaNeura/cosmaneura-scribe/pull/264), exact head `eabd617f58d23266584b01f05df9e1c49546bc19`. Its clean pinned-npm install, 854 JavaScript tests (44 opt-in skips), lint, types, provider and synthetic builds, preview API build, generated API and preview-bundle checks passed. The scoped override preserves Xcode’s CommonJS interface; two new regression tests verify patched buffer handling and round-trip the real iOS project in a temporary copy. [Independent exact-head review](https://github.com/CosmaNeura/cosmaneura-scribe/pull/264#issuecomment-5985964925) found no blockers. npm audit reports zero advisories for this branch; Python and default-branch alerts are not qualified by that scan. Neither dependency PR is merged or deployed. The earlier Swift synthetic pipe suite passed 59 selected tests using invented audio, temporary custody and a loopback mock server, not physical audio, Keychain or staging. These results overlap and must not be summed as unique coverage; local builds are not published or deployed artifacts.

The earlier full Scribe Python/PostgreSQL run passed 1,209 tests, with six opt-in skips and 40 deprecation warnings; it rehearsed migrations and a non-bypass application role and closed 339 earlier database skips. Separate runs now qualify the TypeScript provider wire, both desktop/mobile rendered review trials, configured Core capture with accepted/rejected SOAP paths, and creator-review Core wire with accepted/rejected decisions. Draft test-only [Core PR 675](https://github.com/CosmaNeura/cosmaneura/pull/675), head `23a2aa30cae2765ff878c0c73e2a67e896cb4438`, corrects that older fixture's ambient-session and cross-loop HTTP-client assumptions; [independent static review](https://github.com/CosmaNeura/cosmaneura/pull/675#issuecomment-5985674930) found no blockers. Initial failed fixture attempts remain recorded, not counted as passes. The provider-workspace fixture is now qualified separately through draft test-only [Core PR 676](https://github.com/CosmaNeura/cosmaneura/pull/676), exact head `f64e560b2f731066e1c1d1d024d8d6a3fb886f31`, paired with clean Scribe `eabd617f58d23266584b01f05df9e1c49546bc19`. Both 390-pixel and 1280-pixel browser cases passed, as did each actual Scribe receiver. They check consent, encrypted upload/decryption, translation review before note preparation, suppression of an unsupported plan, Core editing/version persistence and reload without new recording. The repeatable private PostgreSQL 16 runner stops its own cluster and retains receipts; the final source-state receipt confirms both clean revisions and shutdown. [Independent exact-head review](https://github.com/CosmaNeura/cosmaneura/pull/676#issuecomment-5986260227) found no blockers. These results close the remaining local fixture gap, not authenticated staging or real-provider acceptance. The entire Python suite was not rerun with those fixtures, and totals must not be added as a new full-suite result. All inputs were local and synthetic; identity, native recording and AI responses were fixtures, not staging/device/provider qualification. Desktop/mobile screenshots were inspected and the tests checked no horizontal overflow; this is not complete UI/accessibility acceptance. The two owned QA containers and synthetic volumes were removed; XML receipts and screenshots are retained. No staging data or deployment was changed.

Core SOAP/Prep and billing local evidence remains recorded in the release-readiness ledger and PRs. Its earlier large test totals refer to the dirty audit tree, not the currently deployed staging image. Full deployed manual/automated qualification is outstanding.

## Stop conditions and recovery

Stop if the environment is not demonstrably synthetic-only, credentials/mode point outside staging, CI or provenance fails, deployed drift is unexplained, schema/recovery inputs do not match, the drain is incomplete, sessions are ambiguous, or the rollout would unintentionally disable an existing capability.

If activation fails, close new acquisition first while keeping integration/webhooks operating so existing subscriptions reconcile. Disabling acquisition does not invalidate an already-issued hosted Checkout URL. Follow the reviewed recovery process; retain patient, billing and legal/audit history. Do not delete records or overwrite the database as rollback.

Before handing staging back to Shawn, provide: deployment run URLs and exact revisions/digests; observed flags and schema; private references to reviewed identity/synthetic evidence and activation-preflight output; a pass/fail checklist for Checkout, webhook, access, portal and lifecycle tests; privately supplied account credentials; and supported Scribe package/invitation status.

This handoff prepares execution; it is not a deployment approval or a claim that staging is ready.
