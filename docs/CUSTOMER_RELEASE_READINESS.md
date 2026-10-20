# Customer release readiness

Last reviewed: 2026-10-03

This is the current release-readiness ledger for the Cosmaneura application and
its integration with `cosmaneura-scribe`. Historical flight plans remain useful
design evidence, but they do not prove that the current revision is deployable.
Local results below describe the working tree, including uncommitted changes;
repeat the required gates for the final immutable release candidate.

## Required release evidence

| Area | Current repository evidence | Release status |
| --- | --- | --- |
| Core Python | Pyright reports 0 errors and warnings; root Ruff lint/format checks and template lint pass | Verified locally |
| Backend test partitions | Application: 2,862 passed, 457 skipped. Infrastructure: 4,961 passed, 10 skipped, 689 subtests passed | Verified across local partitions; skipped capabilities still require qualification |
| Core web | Unit tests, lint/type checks, production build, and high-severity dependency audit pass | Verified locally |
| SOAP notes | Generation, source grounding, review, edit, versioning, save-status, recovery, and long-transcript suites pass | Verified locally |
| Session prep | Reviewed-source isolation, authorization, runtime, Companion receiver, replay, and recovery suites pass | Verified locally |
| Scribe | Python and TypeScript tests, lint, type checks, builds, protocol contracts, native capture, recovery, and full-stack mock-provider evidence pass | Verified locally |
| Database | Full migration chain and focused additive/retained rollback rehearsals pass; Alembic check reports no new upgrade operations on disposable PostgreSQL | Verified locally |
| Billing implementation | Checkout, portal, webhook, entitlement, seat-lock, bootstrap, and invitation-race behavior is covered by automated tests | Verified locally |
| Stripe environment | Account ownership, live/test mode, product and price IDs, portal configuration, webhook endpoint/signing secret, and one complete checkout-to-webhook-to-portal transaction | **Required before customer release** |
| Identity environment | Production Clerk instance, redirect/origin configuration, organization claims, and a fresh-provider plus returning-provider smoke test | **Required before customer release** |
| Scribe environment | Reviewed runtime flags, managed identities, private networking, PostgreSQL migrations, storage/key custody, signed clients, and a synthetic capture-to-note round trip | **Required before clinical activation** |
| Observability | Production health checks plus confirmed Sentry/PostHog delivery with PHI-safe payload inspection | **Required before customer release** |
| UX acceptance | Keyboard, responsive, empty/loading/error/retry, terminology, and cross-browser review of the customer-critical journeys | In progress |

## Customer-critical journeys

Each release candidate must be exercised from a clean account and from an
existing account. Record the exact candidate SHA and environment with the
evidence; do not use production PHI as test data.

1. Sign up, complete provider onboarding, accept the current legal terms, and
   reach the notes workspace.
2. Create or select a patient and appointment, including a patient whose date of
   birth is unknown and one whose date is later added.
3. Upload or capture an encounter, observe processing and recovery states, open
   the transcript, generate a SOAP note, edit it, save it, reload it, and confirm
   version history and source grounding.
4. Open session prep, confirm only finalized notes from the correct provider and
   patient appear, answer prep questions, reconnect/replay safely, and verify
   revocation removes access immediately.
5. Start checkout for the intended practice and seat count, complete payment,
   observe the signed webhook update entitlements, open the billing portal, and
   verify cancellation or delinquency produces the documented access behavior.
6. Invite a clinician, accept once, reject duplicate/racing acceptance, revoke
   access, and confirm seat count and billing state remain consistent.
7. Exercise the support/view-as boundary and confirm it is visible, audited,
   capability-limited, and cannot record or mutate clinical data unexpectedly.

## Release commands

Run commands from their repository roots with the pinned toolchains and clean
dependency installs. The authoritative command definitions remain the root
`Justfile`, `web/package.json`, and the Scribe `Makefile`/`package.json` files.

Core minimum gates:

```sh
just lint
just js_build
.venv/bin/pytest tests --ignore=tests/infrastructure --ignore=tests/integration
.venv/bin/pytest tests/infrastructure
pnpm --dir web test
pnpm --dir web typecheck
pnpm --dir web audit --audit-level high
```

Scribe minimum gates:

```sh
make lint
make types
make test
npm test
npm run lint
npm run typecheck
npm run build
npm audit --audit-level high
```

Run the repository's full-stack and native-client qualification commands when
their owned disposable services and platform SDKs are available. A skipped test
is not release evidence unless the skipped capability is explicitly out of the
release scope.

## Release decision rule

Repository-green means the candidate is eligible for environment qualification;
it does not mean the system is deployed or that Stripe, identity, private
networking, signing, telemetry, and clinical activation are configured. Release
only when every required row above has candidate-bound evidence and no
customer-critical journey has an unresolved failure.

## Local verification notes

The 2026-10-02 focused rechecks passed 18 note-source tests, 64 provider-question,
conversation-read, session-prep-operations and service-version tests (one skipped),
and 179 staging-infrastructure tests with 12 subtests. Concurrency tests now use
committed fixtures so independent PostgreSQL connections see the same records.
Cleanup filters global model metadata against the installed database tables;
SQLite-only test models are not mistaken for PostgreSQL application tables. The
combined offline/concurrency/health collection-order recheck passed all 89 tests.
Worker-health and expired-session tests share the fixture clock so their outcomes
do not depend on how long earlier tests take.
The staging readiness expression binds workload and traffic data before evaluating
its boolean conditions; unhealthy workloads and incorrect traffic remain refused.
Model comments now match the retained migrations; this required no new migration
or database data change.

On this workstation, Homebrew PostgreSQL and Redis intercept the default localhost
ports even while the Docker services are healthy. Verification uses disposable
PostgreSQL 16.9 on port 55439 and Redis 6.2.18 on port 56379, with explicit
`TEST_DATABASE_URL` and `TEST_REDIS_URL` overrides applied after `direnv exec`.
The shared development database is not the test database. Browser integration
tests are run separately and require `localias` plus usable identity configuration.

The final results use the repository's application/infrastructure partitioning.
The last combined run passed 7,818 tests but had five fixture setup errors. After
repairing cleanup, the entire application partition passed; the completed
infrastructure results above are retained from that combined run. A subsequent
single combined clean invocation has not been recorded. The 467 skipped cases
are not qualified release capabilities merely because the executed cases pass.

## Read-only staging qualification — 2026-10-02

1Password desktop-integrated vault reads succeeded even though `op whoami`
reported no signed-in session. No credential values were emitted or retained.
The public staging `/healthcheck` returned HTTP 200 and the staging sign-in
screen loaded Clerk in Development mode. These observations do not establish
authenticated journey or production identity acceptance.

GET-only Stripe checks using the staging vault item confirmed a test-mode key,
the expected account, and an active USD 89-per-clinician monthly licensed,
per-unit price. Exactly one endpoint matched the staging Stripe webhook URL;
it was enabled, in test mode, pinned to `2025-09-30.clover`, and subscribed to
the nine required lifecycle events. Signing-secret presence was checked, not
its correctness or actual webhook delivery. Deployed flags remain unverified.

**Portal compatibility issue (repaired locally, not deployed):** the pinned portal is active and test-mode, with login,
customer updates, and subscription updates disabled and safe cancellation.
However, Stripe returns an additional `subscription_pause` feature with
`enabled: false`. Core's reviewed-feature allowlist rejects this feature even
when disabled, so the deployed configuration cannot pass that validator. The
local repair accepts the optional field only with an exact boolean-false
`enabled` value; enabled/malformed pause features and all other unknown features
remain refused. A new test reproduced the refusal before repair. See the
[compatibility flight plan](flight-plans/2026-10-02-stripe-disabled-pause-compatibility.md).
No Stripe configuration or billing activation was changed during this audit.

The final combined follow-up check passed all 341 billing and organization-access
tests (299 billing service/route/transport and 42 organization service/route),
and the full frontend check passed all 546 tests across 76
files. Changed Python files pass Ruff lint/format and Pyright with the project's
Python interpreter; the frontend passes TypeScript and changed-test
Prettier/ESLint checks. Billing tests cover old responses without the pause
feature, the newly observed disabled feature, eight enabled/malformed shapes,
and continued refusal of unknown disabled features before session creation.

The staging `example.com` test signup authenticated but reached the pending
organization-roster gate. Repository routing reserves both configured managed
domains and domains present in organization/negotiated-BAA practice records;
which staging record reserved this domain has not been verified. New synthetic
tests prove that the `+clerk_test` suffix grants no exception: unreserved domains
can create an independent practice but still cannot access records before BAA
acceptance, while organization and incomplete negotiated-shell reservations
remain pending and cannot self-enroll. Browser tests verify the same
server-selected distinction. No account was added to an existing organization,
no clinical access was granted, and onboarding runtime rules were unchanged.

Authenticated SOAP, Scribe, and session-prep browser acceptance awaits a
synthetic provider login. Azure CLI is absent on this workstation; deployed
runtime/revision checks still need an authenticated read-only access path.

## Staging walkthrough — 2026-10-03

The synthetic Gmail test account reached independent solo-practice creation.
The browser confirmed creation of `Cosmaneura Release QA - Synthetic Practice`
and presentation of CN-BAA-CUST-CW v2.3. The user subsequently reported successful
BAA acceptance and the practice's paused-access screen, with online billing
unavailable. No patient data, payment, or billing exemption was introduced.

The user selected full Stripe test-mode qualification, not an exemption that
would skip acquisition. The current domain model has owner/workforce roles;
the billing endpoints require the practice owner. Use the existing synthetic
owner for checkout and clinical qualification, then a separate synthetic
workforce account for invitation/seat/authorization testing. Do not invent a
billing-only role or create extra practices to evade the paused-access gate.

Required order:

1. An authorized staging operator records live API/worker/scheduler revisions,
   immutable images, schema, Stripe gates and secret references, and unchanged
   Scribe/Session Prep boundaries. Local template defaults do not prove live
   flags. No canonical staging runs were returned by the current GitHub workflow
   query; that is not proof that staging has never been deployed by another path.
2. Isolate, review and merge the portal repair; the founder selects the exact
   merged candidate and performs the protected staging release. Preserve drift
   reconciliation, synthetic-only evidence and recovery requirements.
3. Complete the separate test-mode activation runbook, including price/portal,
   webhook signing and delivery, historical checkout inventory/drain evidence,
   and synthetic-state restore policy. Keep `STRIPE_LIVE_MODE=false`. The source
   template remains integration/acquisition off with no selected Stripe item.
4. Test owner checkout using only Stripe test data; verify durable webhook
   processing activates access. Test portal restrictions, invoice visibility,
   payment-failure/recovery and cancellation with observed entitlement effects.
5. While entitled, perform synthetic capture/upload, transcript processing,
   SOAP generation/edit/save/reload/history, Scribe recovery, and finalized-note
   session prep with provider/patient isolation. Qualify actual provider calls,
   not only mock-mode behavior.
6. Test a second clinician's invitation, acceptance, seat accounting, owner-only
   billing denial and revocation. Repeat critical keyboard/responsive/error
   states and verify PHI-safe operational telemetry.

No live activation or deployment was performed. Current workstation lacks Azure
CLI, and repository guardrails prohibit agent-executed Azure mutations. The
operator handoff is a real prerequisite, not a successful qualification result.

## Isolated PR handoff — 2026-10-03

- [QA seed PR #671](https://github.com/CosmaNeura/cosmaneura/pull/671), commit
  `dd936f2cc22d1015e660187014b4f08b3b20714f`, is a separate draft. The owner
  (`biller@cosmaneura.com`) and workforce (`doctor@cosmaneura.com`) workflow has
  28 passing offline tests, scoped lint/type checks and independent review with
  no blocking findings. No identities or fixtures were seeded in staging.
  Human agreements, actual Stripe test Checkout and workforce onboarding remain
  prerequisites. No CI checks were reported at the most recent inspection.
- [Portal compatibility PR #672](https://github.com/CosmaNeura/cosmaneura/pull/672),
  commit `8275615b7a0a5d4771cac985c363d5d44633a55f`, isolates the disabled-pause
  repair from this dirty audit worktree. Its clean-base verification passed 338
  combined billing/access tests, 541 frontend tests across 75 files, the
  production-bundled test-target build, scoped Ruff/Pyright and diff checks.
  Independent final-commit review found no blockers (static review, not an
  independent suite rerun). No CI checks were reported at latest inspection.
  These smaller counts reflect the isolated base, which does not include the
  additional tests in the main audit worktree. Build/test warnings remain;
  generated client used the tracked OpenAPI schema. The PR is draft pending
  review/CI and human release decisions. No staging deployment occurred.
- The user supplied another Codex window's October 3 report that
  `dev@cosmaneura.com` has completed onboarding, active Cosmas MD membership and
  pilot access. This agent has not independently verified that identity's live
  access. Pilot access is a clinical-test shortcut, not Stripe qualification.
  The actual Chrome walkthrough still showed the Gmail QA account and paused
  billing screen. Switching to the existing dev account requires its privately
  supplied password; do not create a replacement or reset it as part of QA.
- Native Scribe package/TestFlight availability remains unverified here. The
  other window reported no usable tester download link and a pending invitation;
  native capture and handoff remain incomplete acceptance evidence.

Next operator actions: reconcile deployed revisions/configuration and release
the reviewed merged portal candidate, qualify real Stripe test lifecycle, and
provide the supported Scribe tester packages. Next tester action: sign into the
existing clinical dev account, then exercise synthetic SOAP and session prep.

## Staging preparation and verification — 2026-10-04

Current protected remote master is `49895cb66a305558393a0862a590ee313b60c194`;
the dirty main audit worktree was preserved. Two new isolated draft PRs prepare
work without deploying or changing live accounts:

- [Stripe maintenance preparation PR #673](https://github.com/CosmaNeura/cosmaneura/pull/673),
  SHA `55cc51040bb89d53c543f458310271231eaac2ca`: existing staging vault item,
  integration true, new Checkout false, live mode false. Production and clinical
  gates unchanged. 51 deployment tests plus 66 subtests, scoped Ruff/Pyright and
  diff checks passed. Independent review found no blockers and independently
  passed 10 deployment contracts plus four activation-preflight contracts.
  Fresh vendor/runtime evidence, inclusion of PR #672 and release reconciliation
  remain prerequisites. This draft is not deploy-ready.
- [Watchdog test repair PR #674](https://github.com/CosmaNeura/cosmaneura/pull/674),
  SHA `5818a497cb5e7cfd5310cc7c3b1f43b921ec9037`: an unchanged-master failure
  counted two valid secretless Terraform scenarios as an error. Test-only repair
  checks every secrets override, including indented run-scoped overrides, and
  rejects enabled/missing/duplicate declarations. Ten focused tests and scoped
  checks pass. Independent final review confirmed the earlier indentation finding
  was resolved using mutations of the actual second scenario. No Terraform or
  deployment/runtime configuration changed.

Read-only GitHub inspection confirmed repository-level Core Actions is disabled,
despite the canonical workflow listing being active. Classic branch protection
currently has no required status checks, zero required approving reviews, admin
enforcement and conversation resolution enabled. No settings were changed;
other applicable rulesets must still be inspected before merge. Core AGENTS'
canonical workflow instructions and the combined Scribe/Prep local-founder
release flight plan conflict. Do not re-enable/dispatch Actions or apply default
deployment configuration blindly; resolve the approved merged controller and
preserve deployed clinical capabilities and per-workload recovery first.

Scribe current working-tree checks (existing installed dependencies, not a clean
release artifact):

- JavaScript: 475 passed, 32 native-opt-in tests skipped; lint/types pass.
- Python: 870 passed, 345 skipped, six deprecation warnings; Ruff/Pyright pass.
  No PostgreSQL test URL was supplied, so database-backed cases are not qualified
  by this run. Initial synthetic TLS loopback denial was resolved by an approved
  outside-sandbox rerun, not a code workaround.
- Build passes with required exact HEAD-format DEPLOY_SHA. Working tree remains
  dirty; the output is a compile check and must not be shipped as a verified
  source-attested artifact.
- Actual Swift synthetic pipe tests: 59 passed, covering recording/stop, ownership,
  encrypted storage and SIGKILL/WAL recovery. These overlap JS counts and are not
  additional unique coverage. Invented audio/temporary custody and a loopback mock
  server were used; no physical audio, Keychain or staging service was exercised.
  Receipt retained at `/private/tmp/cosmaneura-scribe-qa-pipe-20261004-unrestricted/result.json`.

The [full staging handoff](https://chatgpt.com/space/page_e1630e1d001081919f8bdc499713c306)
now separates preparation from operator actions and includes manual/automated
acceptance for SOAP, Stripe, Scribe and Session Prep. It is private; no access was
granted to John. Repository copy is in `docs/handoffs/2026-10-04-john-staging-stripe-testing.md`.
Page content was read back; visual rendering was not inspected. Live rollout,
signed Stripe delivery, authenticated clinical walkthroughs and supported native
distribution remain outstanding. No staging data, credentials or deployment
were changed in this preparation pass.

## Release path analysis and database qualification — 2026-10-04

The technical local path is present on merged protected master
`49895cb66a305558393a0862a590ee313b60c194`. Version-2 build-controller plans use
independent local review; `staging_build_source.py` requires Actions disabled and
checks the complete protection/ruleset profile. The publisher and founder-user
deployer derive candidate, image and schema from the bound plan/publication,
not their historical hard-coded fallback. The deployer overlays the reviewed
Scribe/real-provider Prep configuration and permits workflow=true only in v2;
synthetic Prep and legacy handoff remain false. The source therefore supports
the existing combined-release procedure. No new controller implementation or
Actions re-enablement is proposed.

Recommendation to John: confirm the existing local-founder version-2 procedure
for the exact reviewed merged synthetic candidate and reconcile AGENTS' older
Actions-only instruction through review. Source availability and passing local
contracts do not resolve policy authority by themselves. Fresh live inputs,
per-workload recovery, source/identity review and release authorization remain
required. Agents did not execute build/publication/deployment controllers or
Azure commands; no actual release plan was invented.

Local pure contracts at that exact master: 115 tests plus 164 subtests passed
across build source/provenance, build controller, release contract, runtime
recovery and build inputs. Test responses were synthetic, not live-provider
qualification. Fresh read-only GitHub queries confirmed that master SHA and
repository Actions disabled. The handoff now recommends the local procedure
instead of instructing an unapproved workflow dispatch.

Scribe full Python verification with a fresh isolated PostgreSQL 16.9 database:
**1,209 passed, six skipped, 40 deprecation warnings**. The normal fixture
rehearsed migrations and used its non-superuser/non-bypass application role.
This closes 339 of the earlier 345 skipped cases. Command (local synthetic
credentials, never a staging URL):

```sh
COSMANEURA_SCRIBE_TEST_DATABASE_URL=postgresql+asyncpg://scribe_qa:synthetic_local_only@127.0.0.1:55448/scribe_release_qa_test \
  CI=true .venv/bin/pytest -ra
```

The remaining Node wire skip was subsequently tested with
`SCRIBE_TEST_NODE=/Users/shawncoe/.nvm/versions/node/v24.15.0/bin/node`:
`tests/test_provider_core_transport.py::test_actual_typescript_provider_client_uses_server_transport`
passed. Five other opt-in cases remain unqualified: configured Core receiver,
creator-review Core wire, two browser trials and provider-workspace wire. These
need explicit isolated Core/browser harnesses; enabling their flags without
those fixtures would not be meaningful evidence. Do not add the separate Node
result to describe a rerun of the entire suite.

All results use the existing Scribe working tree and installed dependencies;
the five pre-existing modified files were preserved. No code changes or new
code PRs were made in this pass. The owned QA container and its synthetic volume
were removed after completion; the fixtures can recreate them. Existing Docker
containers and live staging were untouched. Page content was read back and the
local handoff synchronized; rendered Page layout remains uninspected.

## Additional isolated quality gates and rehearsals — 2026-10-04

Two new independently reviewed draft PRs are open; neither is merged or deployed:

- [Scribe PR #263](https://github.com/CosmaNeura/cosmaneura-scribe/pull/263),
  `d388cc91f3e5b3795224f917d6048764f0325270`, Tier 1. Four files patch
  JavaScript development dependencies and run two Node test suites with Node,
  instead of letting Vitest miscollect them. Current-main operational-lab scripts
  and required Node/npm versions are preserved. Clean npm 11.5.2 install,
  **852 tests passed, 44 existing opt-in tests skipped**, lint, types, provider
  build, synthetic capture build, preview API build, generated API and
  preview-bundle checks passed. Synthetic builds use this exact committed
  DEPLOY_SHA; nothing was published. An isolated npm 12 resolver was needed once
  during lock generation after npm 11 crashed; the final lock refresh and clean
  install passed with required npm 11.5.2. [Independent final-head review](https://github.com/CosmaNeura/cosmaneura-scribe/pull/263#issuecomment-5985660422)
  found no blockers. That head retains three related moderate
  Capacitor/Xcode/uuid reports; the stacked cleanup below resolves them.
  This does not qualify Python dependencies or other repository security alerts.
- [Core PR #675](https://github.com/CosmaNeura/cosmaneura/pull/675),
  `23a2aa30cae2765ff878c0c73e2a67e896cb4438`, Tier 0, one test file only.
  The older creator-review fixture relied on an ambient database session and
  reused main-loop HTTP probe connections in the private worker loop. A fresh
  request-owned session and receiver HTTP client restore the intended fixture
  without weakening production isolation. Accepted and rejected wire cases now
  each pass; authority, foreign-scope denial, lost reply, identical-key replay,
  restart and consent revocation assertions remain. Ruff, format, scoped Pyright
  and diff checks pass. [Independent final-head static review](https://github.com/CosmaNeura/cosmaneura/pull/675#issuecomment-5985674930)
  found no blockers; execution passes are implementer-provided evidence.

Stacked draft [Scribe PR #264](https://github.com/CosmaNeura/cosmaneura-scribe/pull/264),
`eabd617f58d23266584b01f05df9e1c49546bc19`, is Tier 1 and remains unmerged.
It changes three files: a scoped Xcode UUID 11.1.1 override, its lock entry, and
two Node regression tests for actual Capacitor/Xcode resolution, bounded UUID
buffer handling and temporary round-trip editing of the real iOS project.
Clean pinned npm 11.5.2 install, 854 JavaScript tests (44 opt-in skips), lint,
workspace types, provider/synthetic builds, preview API build, generated API
and preview-bundle checks passed. npm audit returned zero advisories for this
branch, not for Python or the default branch. Fresh npm 11 resolution crashed
in Arborist; npm 12 generated the authoritative UUID metadata, narrowed onto
the existing lock graph and validated by pinned npm 11 clean install. Engine
enforcement was restored unchanged. [Independent exact-head review](https://github.com/CosmaNeura/cosmaneura-scribe/pull/264#issuecomment-5985964925)
found no blockers. Revert that commit and clean-install for recovery; that
reintroduces the older UUID tooling advisory. No deployment or native/device
qualification was performed.

Additional actual local HTTP/PostgreSQL/browser runs:

- Rendered review: two tests passed, exercising desktop 1280-pixel and mobile
  390-pixel layouts, encrypted synthetic capture, lost committed reply, reload
  recovery and consent revocation. Screenshots were inspected; assertions check
  no horizontal overflow. Receipt:
  `/private/tmp/cosmaneura-scribe-browser-qa-20261004.xml`.
- Configured Core receiver: accepted and rejected each passed, including the
  existing canonical SOAP path. Receipts:
  `/private/tmp/scribe-configured-core.qa-20261004-accepted.xml` and
  `/private/tmp/scribe-configured-core.qa-20261004-rejected.xml`.
- Corrected creator-review receiver: accepted and rejected each passed. Receipts:
  `/private/tmp/scribe-core-review.qa-20261004-fixed2-accepted.xml` and
  `/private/tmp/scribe-core-review.qa-20261004-fixed-rejected.xml`.
  Initial failed attempts remain retained and are not counted as passes.

The remaining provider-workspace fixture now passes separately through draft
test-only [Core PR #676](https://github.com/CosmaNeura/cosmaneura/pull/676),
`f64e560b2f731066e1c1d1d024d8d6a3fb886f31`, paired with clean Scribe
`eabd617f58d23266584b01f05df9e1c49546bc19`. The repeatable runner creates new
private PostgreSQL 16 databases, uses a socket-only cluster and stops only that
cluster afterward. Final receipts: `/private/tmp/scribe-configured-core.provider.yfkkphh0`.
Two mobile/desktop browser cases and both Scribe receivers passed; consent,
encrypted upload/decryption, translation review, unsupported-plan suppression,
Core edit/save/version persistence and reopen without recording are asserted.
Each receiver verifies one encrypted chunk and 48,000 decrypted frames with
offline providers. Screenshots inspected at 390/1280 pixels show no overflow.
Ruff/format, scoped Pyright, Node syntax and diff checks passed. Normal
collection safely skips both opt-in cases; it is not qualification evidence.
[Independent final-head review](https://github.com/CosmaNeura/cosmaneura/pull/676#issuecomment-5986260227)
found no blockers. The final result records both source SHAs as clean and the
private database stopped; it is not immutable build provenance. Initial failed
fixture attempts are retained separately and are not counted as passes.
This closes the remaining local fixture gap through separate runs.
The full Python suite was not rerun with these fixtures; do not sum overlapping
results into a new suite total. Identity, native entry and AI provider results
were synthetic fixtures, not authenticated staging or physical-device evidence.
Complete UI/accessibility, real provider, native capture, signed distribution,
Stripe test transactions and deployed SOAP/Prep acceptance remain outstanding.

Owned QA containers `cosmaneura-core-wire-qa-20261004` and
`cosmaneura-scribe-browser-qa-20261004` and their anonymous synthetic database
volumes were removed after completion. XML receipts and screenshots remain;
fixtures recreate the databases. Existing containers, original dirty code,
Actions settings, staging accounts and deployments were untouched. The handoff
Page and local copy were updated; Page rendering remains uninspected.
