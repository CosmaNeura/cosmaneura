# Flight plan: promote canonical staging candidates and stabilize Auth/IAM

## Outcome and boundaries

- Problem and measurable outcome: replace branch-tip staging deployment with
  the canonical sequence `approved source SHA -> staging-only immutable image
  digest -> staging tests/evidence -> explicit production promotion`. Staging
  remains persistent. A run accepts one full 40-character candidate SHA,
  proves that it is the exact merge commit of a founder-selected merged PR into
  protected `master`, tests and deploys that SHA rather than moving `master`,
  and records enough evidence to reproduce or recover the staging change. The
  founder's exact dispatch is the candidate authorization; it is not presented
  as an independent GitHub approval.
- First candidate: merged PR #230 at
  `8fa02a71f4b880cd43f4fccc8c81c92a0d635623`, used to rehearse and then catch
  staging up through the four known migrations. This is deliberately not the
  current `master` tip.
- Later candidate: the final merge commit of PR #238 after it is updated
  normally onto the exact frozen post-workflow `master`, all checks pass, and
  an independent reviewer records review of that exact final SHA. The resulting
  merge SHA becomes the Auth base SHA supplied to the separate Zoom workstream.
- In scope: the staging-candidate workflow and focused tests; the GitHub
  `Staging` environment branch-policy transition; a non-production,
  synthetic-only migration rehearsal; staging configuration and identity
  verification; immutable build/deployment evidence; and the later staging-only
  Auth/IAM validation sequence.
- Explicit non-goals: changing production, production data, production secrets,
  or any production deployment control; invoking a production deployment;
  contacting pilot users; deploying an unmerged branch; moving `master` or
  treating its tip as an implicit candidate; modifying or deploying Zoom PR
  #239 or #241; merging or deploying Scribe PR #243; merging Session Prep
  staging PR #246; or activating the work from PR #244 or #245. Their controls
  remain off even if their dormant source is present in a later candidate.
- Affected users, services, environments, and data classes: authorized
  operators and synthetic QA users in persistent staging; the staging API,
  worker, scheduler, PostgreSQL database, registry, GitHub environment, Clerk
  test instance, dedicated Zoom app, Recall account, and Stripe test-mode
  configuration. No real patient data or production resource is permitted.

## Risk and dependencies

- Tier and reason: **Tier 2**. This changes a protected deployment boundary,
  executes schema/data migrations, changes the current BAA version, and later
  carries authentication/session behavior.
- Irreversible or high-blast-radius decisions: an onboarding, invitation,
  clinician-seat, Stripe Checkout, or BAA v2.3 write creates durable evidence
  that must not be discarded. A mistaken GitHub environment policy can expose
  credentials to the wrong ref. A wrong database, registry, Azure identity, or
  vendor account can cross an environment boundary.
- Dependency-ordered PR stack and safe holds:
  1. Freeze Core feature merges at the reviewed baseline. Merge only the
     canonical staging workflow change after required checks and independent
     exact-final-SHA review. No deployment is authorized by that merge.
  2. Verify the new workflow registration on protected `master`, all candidate
     guards, and absence of queued or running staging deployments. The existing
     staging environment policy remains unchanged at this hold.
     Before either policy change or dispatch, remove the live legacy Azure
     federated credential whose subject is `refs/heads/staging`; the deployment
     workflow rejects any identity that has more than the sole environment
     subject `repo:CosmaNeura/cosmaneura:environment:staging`.
  3. Rehearse the four migrations from an isolated, verified synthetic-only
     restore using the exact PR #230 source. Build and hash a credentialless
     rehearsal archive from that SHA and load it directly into the isolated
     runner; do not publish the canonical candidate tag. This proves the source
     and migration contract, not the bytes of the later canonical image. A
     failed or unverifiable rehearsal holds every later step.
  4. After an action-time confirmation, switch the GitHub `Staging` environment
     policy from the old `staging` ref to `master` and verify the read-back. Do
     not dispatch in the same authorization step.
  5. After a separate action-time confirmation, dispatch PR #230's exact SHA,
     verify the immutable digest and evidence, and hold staging at that known
     production-source baseline.
  6. Update PR #238 normally onto the then-current frozen `master`; never
     rebase or force-push it. Rerun protected checks, obtain independent review
     of the exact final SHA, merge normally, and record the merge SHA as the
     Auth base SHA. A merge does not authorize deployment.
  7. Complete the real synthetic Clerk smoke, including the separate
     action-time OTP confirmation. Only after another explicit staging-run
     confirmation may the merged Auth SHA be dispatched to staging.
- Cross-repo contract/version and receiver-before-sender order: BAA v2.3 is
  governed by `docs/flight-plans/2026-08-29-baa-v2-3-publication.md`. Before
  migration `9a2399212488`, verify read-only that the immutable versioned v2.3
  public PDF receiver is live with its approved digest. Do not alter the web
  repository or canonical aliases in this flight plan. Zoom consumes the final
  Auth merge SHA only after that SHA is reported; no Zoom change is made here.

## Current deployed state

- Source baselines at the start of this flight:
  - protected `master`:
    `6cea63e59203a8730af39497a3672b3f4598ba98`;
  - production source / first staging candidate, merged as PR #230:
    `8fa02a71f4b880cd43f4fccc8c81c92a0d635623`;
  - `staging` branch:
    `8ea64a5704868031dec66d1f81bb698137bfb29f`;
  - merge-base of the staging and production-source commits:
    `c2e33419ea2304b5c083adf1ae147664484dc9c2`.
- The staging merge commit is not an ancestor of the production-source commit,
  but its migration tree matches the merge-base. Its source migration head is
  `89c31f0ae275`; PR #230's source migration head is `9a2399212488`.
- The last successful manual staging baseline was workflow run `33667675179`
  (attempt 1), executed by `JohnClem` for exact source
  `8ea64a5704868031dec66d1f81bb698137bfb29f` from 2026-09-02 18:31Z to 18:50Z.
  Backend, Frontend, and deploy jobs succeeded, including migration preflight
  execution `cosmaneura-staging-migrate-48j36px` and migration execution
  `cosmaneura-staging-migrate-6dao9fg`. The run retained no artifact, so its
  success does not establish the live database revision or synthetic-only
  status.
- Read-only Azure reconciliation on 2026-09-02 found all three workloads
  configured with the mutable historical tag
  `cosmaneurastagingregistry.azurecr.io/cosmaneura/cosmaneura:8ea64a5704868031dec66d1f81bb698137bfb29f`.
  Resolving that tag at inspection time returned provisional digest
  `sha256:475908dbb4c6820f91ca6ccd626312ef4f7591c8555923f02949520fcd4208d7`.
  Because the workload reference is a mutable tag and there is no retained
  publication record binding the running revision to those exact bytes, this
  value is **not accepted recovery evidence**.
  Ready revisions were `cosmaneura-staging-app--r33667675179a1-8ea64a5`,
  `cosmaneura-staging-worker--r33667675179a1-8ea64a5`, and
  `cosmaneura-staging-scheduler--r33667675179a1-8ea64a5`; each was healthy and
  provisioned in Single mode, and API traffic was 100 percent on latest.
  `/healthcheck` returned HTTP 200. Candidate `8fa02a7...` had no staging tag.
- Live schema revision and synthetic-only status remain **unaccepted**. The
  database is the Ready PostgreSQL 16 staging server/database. The resolved
  staging connection contract reports `sslmode=require`, not the required
  hostname-verifying `verify-full`, so the new deployment control records
  `database_sslmode_not_verify_full` and stops before Azure mutation. Read-only
  Azure identity and configuration checks may occur before that stop. The live
  Alembic revision also has not yet been independently evidenced. Do not infer
  it from source or the successful run.
- The authorized GitHub trust cutover is complete. The `Staging` environment
  now permits only custom branch `master`, with protected-branch mode off,
  custom policies on, admin bypass off, and only the branch-policy protection
  rule. `ENABLE_STAGING_PILOT_ZOOM_SKIP=false`. The durable cutover record is
  PR #305 comment `5552118792`; both recovery capture attempts revalidated the
  exact environment policy before protected credentials.
- The staging GitHub Azure identity is client
  `45b940bf-2f4f-4b10-bcc3-4679d8855a6f`, principal
  `a94600b7-c854-4121-bbcb-a0fa7baae14a`, tenant
  `d51754d0-bba5-4810-820e-afac542844d6`, and subscription
  `bab2a709-1fcb-4c87-a9fc-802f7477ec13`. The scopes returned by the available
  read-only query were staging-only, but that scoped result is not a global
  negative proof of every tenant, subscription, management-group, and inherited
  role. An independently authorized auditor may be required to complete that
  proof. The identity now has exactly one federated credential,
  `github-actions`, scoped to the staging environment; the legacy branch
  credential was removed and read back absent. Recovery capture runs
  `33968924079` and `33971188011` exercised the real environment OIDC session.
  The latter revalidated the sole enabled subscription and the union of the
  exact six scoped role queries as the three expected staging-only assignments
  before its later read-only validation stop.
- Observed staging runtime values include the test Clerk issuer
  `https://resolved-emu-53.clerk.accounts.dev`, staging-only Zoom callbacks,
  Recall region `us-west-2`, `STRIPE_INTEGRATION_ENABLED=false`,
  `STRIPE_BILLING_ENABLED=false`, `STRIPE_LIVE_MODE=false`,
  `DURABLE_WEBHOOK_INBOX_ENABLED=false`, `SCRIBE_HANDOFF_ENABLED=false`,
  `AGENT_API_ENABLED=true`, and `AUTHORITY_KERNEL_ENABLED=true`. Stripe flags
  belong only on the API and worker and must be absent from the scheduler;
  `ENABLE_STAGING_PILOT_ZOOM_SKIP` is a GitHub workflow variable, not a workload
  flag. The approved Clerk, Zoom, Recall, and Stripe fingerprint-set manifest
  has not yet been supplied. Its SHA-256 is a required dispatch input and its
  comparison result is required evidence, but it is not by itself proof that
  every provider account is separated from production. That separation remains
  blocked until an independent provider-side check establishes it. Absent
  runtime values for `SCRIBE_WORKFLOW_ENABLED` and
  `SESSION_PREP_SYNTHETIC_ENABLED` are not sufficient; the new control writes
  and verifies them as explicit `false`.
- Difference from `master` and deployed-only behavior: current `master` is
  ahead of `8fa02a7...` and includes work that must not be activated. Therefore
  staging is reconciled to the explicitly approved candidate, never blindly to
  moving `master`. The candidate must nevertheless be contained in the exact
  protected `master` observed by every provenance check.
- Reconciliation decision: retain persistent staging data; replace only the
  approved source/image and expected schema through the rehearsed sequence.
  Do not reset, reseed, or recreate staging as a shortcut.
- Previous known-good artifact and data-recovery boundary: before any canonical
  deployment, bootstrap an accepted recovery image without rebuilding it. Use
  independently retained publication evidence or registry manifest history to
  bind the bytes actually used by the live revisions to one digest, retain/pin
  that manifest by immutable reference, verify it remains pullable and schema
  compatible, and record the proof. Only then may its exact `sha256:` value be
  supplied as `recovery_digest`. Resolving the current value of the mutable
  historical tag is insufficient. Stop if the original live bytes cannot be
  proven, the immutable image cannot be retained and verified, or the workloads
  disagree unexpectedly.
- Recovery capture runs `33968924079` and `33971188011` both failed closed in
  initial observation and made no registry or workload mutation. The second
  run's terminal receipt is
  `sha256:4bd41b727c58acd10adcf5fea4eb30e8ceeb6e7259fd00517e6c17272bc224ca`
  (artifact `9970972665`). It isolated benign, complete Azure API projection
  differences between app-resource and active-revision templates; a bounded
  verifier correction is required before a freshly authorized capture retry.
- No isolated restore rehearsal or canonical staging-candidate deployment has
  occurred. The historical baseline run and the read-only observations above do
  not satisfy either gate.

## Feature flag / release control (reversible user-visible behavior only)

- The release control is not a feature flag. It is a founder-only
  `workflow_dispatch` launched from the exact current protected `master`, with:
  - `candidate_sha`: exactly 40 lowercase hexadecimal characters;
  - `approval_pr_number`: a merged PR targeting `master` whose exact merge SHA
    is `candidate_sha`;
  - `synthetic_only_confirmed=true` plus
    `synthetic_evidence_sha256=sha256:<64 lowercase hex>`;
  - `staging_identity_fingerprints_sha256=sha256:<64 lowercase hex>`, binding
    the dispatch to the independently approved non-secret staging vendor
    fingerprint manifest; the deploy boundary receives the same digest as
    `EXPECTED_STAGING_IDENTITY_FINGERPRINTS_SHA256`;
  - `expected_schema_before` and `expected_schema_after`: exact 12-character
    lowercase Alembic revisions;
  - `recovery_digest=sha256:<64 lowercase hex>`.
- Candidate provenance is reverified from the protected-master checkout before
  protected credentials, before image publication, and immediately before
  migrations or Azure mutation. The candidate is checked out and tested in a
  separate directory. A moving `master` is never substituted for it.
- The deploy script defaults to the pinned staging service principal. When
  Actions is unavailable, an explicit `STAGING_DEPLOYMENT_IDENTITY_MODE=founder-user`
  fallback accepts only the pinned founder's independently authenticated GitHub
  user object and Azure directory object plus username in the exact staging
  tenant and subscription, while refusing any simultaneous service-principal
  inputs. The fallback revalidates
  the clean protected-master controller, selected merged candidate PR and schema
  head before protected configuration and again immediately before mutation. It
  grants no production authority and does not weaken the workflow's default path.
- Candidate build and registry-credentialed publication run as separate fresh
  jobs. The build job receives no protected deployment or registry credential,
  exports a content-addressed image archive, and records its SHA-256. A fresh
  publication job executes no candidate code, downloads only that verified
  archive, reverifies provenance and the archive digest before credential use,
  and publishes those same bytes. No mutable workspace crosses the boundary.
  With Actions unavailable, `infra-staging/publish-local.sh` preserves this
  separation: it accepts only a successful exact controller result, revalidates
  the reviewed build plan and founder GitHub/Azure identities, refuses an existing
  candidate SHA tag, pushes once to the existing staging registry, and verifies
  the immutable digest. It cannot invoke deployment, write `latest`, retry, or
  target production, and it removes its temporary registry credential locally.
- Preserve reviewed staging flags and record observed non-secret values before
  and after each run. Provider V7 and sovereign record grants are already
  configured `true`; this release does not toggle them. Force Session Prep,
  Scribe workflow, Scribe handoff, and durable webhook inbox to `false` on all
  workloads. Force public acquisition/billing and `STRIPE_LIVE_MODE` to `false`
  on the API and worker, and remove those Stripe variables from the scheduler.
  Require the workflow-only `ENABLE_STAGING_PILOT_ZOOM_SKIP` variable to be
  `false`; do not report it as workload evidence. Stale values must not survive.
- Removal PR/condition: N/A. Canonical immutable staging delivery is the
  continuing control. Any future redesign is another Tier-2 change.

## Schema/data sequence (schema/data work only)

| Change | Rung | PR / source | Invariant | Recovery |
|---|---|---|---|---|
| `89c31f0ae275 -> d0a4c8e2f611` add clinician seat state | Additive expand with existing-row default | PR #188, final file SHA-256 `14db2216f5afbd89676d6d596d13b4afd97f3897da28ea0f979ee79f17725895` | Membership count is unchanged; every pre-existing membership is `active`; only `active` or `inactive` is accepted; the expected practice/seat index exists | Before any new seat decision, the guarded online downgrade is technically available. After any seat evidence is written, policy requires roll-forward even though the code guard detects only inactive rows. |
| `d0a4c8e2f611 -> e7a9c3d5f102` add public onboarding evidence | Expand plus mixed-version write fence | PR #188, final file SHA-256 `75d6efa8a4633b95b643e40367dbbca2ab29fd38e1ca09fe5069f83684dde62f` | Existing practices become `managed`; `public_pilot_cohort=false`; the acquisition default is removed; legal/invitation tables start empty; constraints and append-only/owner triggers exist | Any public practice/cohort, legal acceptance, or invitation blocks downgrade. Stop acquisition, retain evidence, drain incompatible writers, and roll forward. |
| `e7a9c3d5f102 -> da6e199668a4` persist Stripe Checkout state | Additive columns plus provenance backfill | PR #217, final file SHA-256 `20929861af93cd154e294e1a2c18afc070fad392d9abcd86905200c76f03d7dc` | Practice count is unchanged; legacy Checkout/subscription contract counts exactly equal the corresponding pre-migration non-null binding counts; other new fields remain null unless explicitly exercised | A pre-existing binding can make downgrade unsafe during the migration itself. After any Checkout, trial-policy, or subscription evidence exists, disable acquisition, preserve integration/reconciliation, and roll forward. |
| `da6e199668a4 -> 9a2399212488` publish BAA v2.3 | Additive immutable legal version plus current-version cut | PR #228 within candidate #230, final file SHA-256 `b1f2db1d21bb4587b92f84f6ad846377d58db046ee7e76291e1aad213bf5a715` | v2.2 row and acceptances remain unchanged; exactly one v2.3 row has asset SHA-256 `9bdc110b6b2ffcc9f5e7aee653af499357d2f35dccf43c57d8bbcf70094ed34e`; v2.3 alone is current; no practice reference is silently rewritten | Before any v2.3 acceptance/reference, the guarded downgrade can restore v2.2. After either exists, stop onboarding, retain all legal evidence, and roll forward with counsel direction. |

- Ordered files corresponding to those four rungs are
  `migrations/versions/2026_08_27_d0a4c8e2f611_add_clinician_seat_state.py`,
  `migrations/versions/2026_08_27_e7a9c3d5f102_add_public_onboarding_evidence.py`,
  `migrations/versions/2026_08_27_da6e199668a4_persist_stripe_checkout_state.py`,
  and
  `migrations/versions/2026_08_29_9a2399212488_publish_baa_v2_3.py`.
- The four migration files are unchanged between production source
  `8fa02a7...` and starting `master` `6cea63e...`. Recompare their SHA-256 values
  with the final candidate before reusing rehearsal evidence.
- The live versioned BAA receiver returned a file SHA-256 of
  `94da2fc53777922e23ed542a034a12ffce65e10f1436754b7421c8d35544eacc`
  during read-only inspection. That transport artifact digest is distinct from
  the Core model/migration's embedded document identity
  `9bdc110b6b2ffcc9f5e7aee653af499357d2f35dccf43c57d8bbcf70094ed34e`;
  the web release evidence must explicitly map the two before the v2.3 receiver
  gate can be accepted.
- PHI-safe rehearsal status: **blocked until prerequisites are supplied**.
  `docs/NONPRODUCTION_BACKUP_RESTORE_REHEARSAL.md` is procedure-only and has
  not been executed; it still requires an independently named operator,
  observer, cleanup approver, secondary contact, and owner-approved RTO/RPO.
  `docs/MIGRATION_PLAYBOOK.md` records that no generic automated isolated
  restore/scrub/verify/TTL-cleanup path exists.
- Synthetic-only status cannot be inferred from `PYTHON_ENV=staging`, fixture
  flags, a successful baseline run, or absence of known patient rows. Existing
  database constraints govern only specific EHR and Preview appointment
  fixtures, while staging documentation records real team Clerk identities.
  Define whether approved workforce QA identities are compatible with the
  synthetic-only boundary, then use an exhaustive, reviewed verifier for every
  PHI-capable table, blob class, webhook payload, external identifier, and
  derivative. If any row/object is unclassified, stop before creating a copy.
- The rehearsal uses a uniquely named point-in-time restore of the verified
  non-production staging source. The target has no public access, Azure-services
  firewall rule, production DNS/route, application identity, vendor credential,
  or vendor egress; it has a pre-authorized TTL of no more than seven days.
  Sanitize external identifiers and credentials with a reviewed deterministic
  scrubber that preserves nullability, uniqueness, relationships, and the
  Stripe binding shapes needed by the migration. A verifier emits only counts,
  keyed digests, and pass/fail.
- Do not use `infra-staging/deploy.sh` for the restore rehearsal: it targets the
  persistent staging resource group and migration job. Build a credentialless
  image archive from exact source `8fa02a7...`, record its SHA-256, and load it
  directly into a network-contained rehearsal runner with database-only
  configuration and no external service credentials. Do not publish or reserve
  the canonical candidate tag, and do not claim this independently built
  rehearsal archive is byte-identical to the later deployment image. Require
  restored schema `89c31f0ae275`, run the aggregate duplicate preflight, assert
  the image's sole head is `9a2399212488`, then migrate to that exact revision.
- The separately stateful `infra-staging-rehearsal/` root is the dormant
  control-plane boundary. Its default plan creates nothing. A reviewed enabled
  plan may create the persistent control/empty-target groups, two OIDC
  dispatcher UAMIs, two unfederated job UAMIs, least-privilege roles, the
  target firewall-rule deny policy, and the private non-PHI marker/evidence
  store. Only dispatchers receive exact environment-subject federation; source,
  target, and marker roles bind to the job identities. The
  controller-owned target network contract is `10.75.0.0/24`; the persistent
  marker/private-executor reservation is a distinct `10.76.0.0/24`. Both must
  be revalidated against the complete live peering/address inventory at plan
  time. Reserving the executor subnet does not establish a private runner or a
  route to the ephemeral target. Locked WORM, the AzAPI legal-hold action, and
  the storage-account `ReadOnly` lock stay behind a separate explicit
  irreversible approval. The lock is additionally hard-blocked until a later
  rung binds the private executor and trusted aggregate-only evidence sink;
  the reserved subnet and identities make no runtime claim. See
  `infra-staging-rehearsal/README.md`.
- Record before/after schema dumps or digests, aggregate/HMAC state, duration,
  lock observations, exit status, and all table/index/constraint/trigger and
  row-count invariants. Run synthetic seat, onboarding, Stripe-state, and BAA
  canaries only after the clean upgrade. From the first durable canary write,
  recovery is roll-forward rather than destructive downgrade.

## Rollout and promotion

### Canonical workflow and environment-policy transition

1. Run workflow parsing plus the focused candidate-verifier and deployment
   regression suites in
   `tests/infrastructure/test_staging_candidate_verifier.py` and
   `tests/infrastructure/test_canonical_staging_deployment.py`, together with
   migration and application tests. Obtain independent review of the exact
   final workflow commit, then merge normally to protected `master`.
2. Verify the new workflow is registered, is `workflow_dispatch` only, and has
   no push deployment path. Require both `github.actor` and
   `github.triggering_actor` to be `JohnClem`, the workflow ref to equal the
   current protected `master`, and non-cancelling staging concurrency.
3. Verify that tests, candidate build, registry-credentialed publication, and
   deployment use fresh jobs. The unprivileged build job must export one
   content-addressed image archive and hash; the publication job must start
   clean, execute no candidate code, verify that archive, and publish the same
   image bytes. Neither a runner workspace nor a locally rebuilt image may
   bridge the credential boundary.
4. Require a workflow-wide terminal evidence job with dependencies on every
   preceding job and `always()` semantics. It receives no protected environment
   or deployment credentials and records all available authorization, test,
   build/archive, publication, provenance, identity, migration, deployment, and
   smoke conclusions even when an early job fails or is skipped. The terminal
   gate must remain failed unless every required conclusion and evidence field
   is present and accepted.
5. Read and retain the complete non-secret GitHub `Staging` environment policy,
   custom branch policy ID, reviewer/wait-timer settings, and a canonical
   SHA-256 of the response. Verify no staging deployment is queued or running.
   Do not claim that a required environment reviewer exists unless the read
   proves it.
6. **HOLD — ask the founder immediately before changing the GitHub environment
   setting or mutating the Azure federation.** After confirmation, re-read all
   preconditions and enter one bounded, fail-closed change window: remove only
   the identified legacy branch-subject Azure federated credential; set the
   GitHub environment variable `ENABLE_STAGING_PILOT_ZOOM_SKIP` to `false`; and
   update custom policy ID `45380333` from exact `staging` to exact `master`.
   Never allow both refs. Read back the Azure credential set, variable, complete
   environment policy, and their digests before leaving the window. Do not
   dispatch under this same confirmation.
7. If any mutation or read-back fails, do not dispatch. Prefer the secure
   fail-closed state with no legacy branch credential. Restore policy ID
   `45380333` to its exact recorded `staging` value only if that is required for
   recovery and separately confirmed; verify every resulting state and record
   the incomplete transition for investigation. Never recreate the legacy
   branch-scoped Azure credential merely to make an old workflow run.
8. **HOLD — ask separately immediately before each staging dispatch.** Recheck
   the protected-master workflow SHA, candidate inputs, policy, no in-flight
   run, approved vendor-fingerprint-set digest, and independently accepted
   immutable recovery digest after confirmation. A prior confirmation does not
   authorize a retry or a later Auth deployment.

### First catch-up candidate: PR #230

1. Use candidate SHA `8fa02a71f4b880cd43f4fccc8c81c92a0d635623`,
   approval PR `230`, expected schema before `89c31f0ae275`, expected schema
   after `9a2399212488`, the accepted synthetic evidence digest, the approved
   vendor-fingerprint-set digest, and the newly bootstrapped and independently
   accepted immutable recovery digest.
2. Require change-budget and workflow-policy checks to pass for the exact
   protected-master controller SHA. Separately run Backend, Frontend, migration
   graph/lint, all three focused migration guard tests, BAA asset tests, and the
   isolated restore rehearsal against exact candidate `8fa02a7...`; retain the
   original PR #230 check evidence as historical provenance rather than
   misattributing controller-only checks to the candidate.
3. Quiesce onboarding, BAA, billing, and other mutating synthetic traffic before
   migration. The old image can no longer create practices after the
   acquisition default is removed, and it does not contain the newly current
   BAA v2.3 asset. Keep the quiescent interval bounded, deploy workers and API
   in the reviewed order, and prove incompatible revisions have drained before
   reopening synthetic traffic.
4. During the separately confirmed canonical dispatch, require the candidate's
   staging tag to be absent, publish the exact credentialless build archive,
   resolve it to one immutable registry digest before persistent-staging
   migration, and use that same digest for migration and every workload. Reject
   an existing candidate tag, a digest mismatch, `latest`, a production
   registry, or different digests among those steps. The post-deploy smoke is
   the first evidence against the exact published bytes; the earlier isolated
   rehearsal is source- and schema-contract evidence.

### Staging-only service identity and configuration checklist

An approved, non-secret staging vendor-fingerprint manifest and its SHA-256 are
required inputs. Evidence compares each observed value to that exact manifest.
This allowlist is necessary but does not establish separation from production;
an independent provider-side account/configuration check must establish that
negative boundary before the gate can pass.

| Surface | Required value-minimized proof |
|---|---|
| Clerk | Backend and frontend keys resolve to the same approved test issuer; private/public modes are `sk_test_`/`pk_test_`; issuer and credential fingerprints differ from production; only approved synthetic QA users are used. Do not print keys, subjects, emails, sessions, or OTPs. |
| Zoom | Credentials resolve from the dedicated staging app; approved non-secret app/account fingerprint differs from production; OAuth callback and success URLs use `staging-app.cosmaneura.com`; the configured webhook points to the exact staging Zoom route. Do not change dashboard settings or use PR #239/#241. |
| Recall | API and webhook-signing credentials resolve from the staging source, differ from production by approved fingerprint, use the approved region/base URL, and the webhook secret exists only on the API workload. Use no real meeting, recording, or participant. |
| Stripe | `STRIPE_LIVE_MODE=false`; acquisition/billing remain off unless separately approved; disabled workloads have no stale Stripe secrets. If maintenance integration is enabled, accept only a test/restricted key and provider-confirmed test account/price/webhook objects with `livemode=false`. Never create a charge. |
| PostgreSQL | Exact staging server resource, hostname, database `staging`, port, TLS `verify-full`, connected-server identity, and expected Alembic revision. A vault reference alone is insufficient. |
| Registry | Exact staging ACR and repository; candidate tag absent before publication; verified content-addressed image-archive SHA-256 before credentials; one matching registry `sha256:` digest afterward; independently accepted immutable recovery image recorded before mutation. Admin-credential use remains an acknowledged control gap. |
| Azure/GitHub identity | Exact approved staging tenant, subscription, resource group, federated principal, and OIDC subject `repo:CosmaNeura/cosmaneura:environment:staging`; no production role/scope. Compare source IDs to fresh control-plane results, not names. Rehearse the complete query set from the real environment OIDC session. If that principal cannot enumerate every tenant/subscription/management-group and inherited assignment needed for global negative proof, require an independently authorized auditor to supply it. |
| URLs and flags | Backend, frontend, cookie domain, allowed hosts, Zoom redirects, Recall base, and any Sovereign origin are staging-only and HTTPS. Record only allowlisted non-secret flags and reject unknown or newly enabled values. Stripe flags are `false` only on API/worker and absent on scheduler; `ENABLE_STAGING_PILOT_ZOOM_SKIP=false` is workflow-policy evidence, not workload evidence. |

### Auth/IAM candidate from PR #238

1. Keep the Core feature-merge freeze. PR #238's audited pre-update head is
   `c0cbce2c4e5fe2f53108eec27395f73c281e3638`, based on
   `a77f9f6e6940f9dd5bdc340a25f14f05c02cc9b7`; it is not a release candidate.
2. After the workflow merge, record the exact frozen `master` SHA. Merge that
   SHA normally into PR #238 and push with a normal fast-forward update. Do not
   rebase or force-push. Update its flight plan/evidence for the new base.
3. Require Backend, Frontend, change-budget, CodeQL if reported, all focused
   Auth/session tests, and independent review of the exact final PR head. Any
   substantive push invalidates the review.
4. Complete the real synthetic Clerk email-code smoke. Exercise interval tick,
   window focus, visibility restoration, token refresh, sign-out/sign-in,
   active, inconsistent, and unavailable support-view states; disabled,
   revoked, and inactive users; wrong tenant/practice; current and stale BAA
   states; and approved MCD access. Retain only pass/fail, timestamps, status
   classes, and opaque evidence digests.
5. **HOLD — ask for action-time confirmation immediately before entering the
   QA OTP.** Never record, log, echo, or reuse the OTP. A successful automated
   test is not a substitute for this real smoke.
6. Merge PR #238 only when every gate passes. Record its exact merge commit as
   `AUTH_BASE_SHA`; report that SHA to the Zoom chat and nothing from #239 or
   #241. The merge itself authorizes no deployment.
7. **HOLD — ask immediately before dispatching the Auth candidate to staging.**
   Repeat the canonical provenance, configuration, synthetic-only, recovery,
   schema, digest, workload, and smoke gates. Production remains untouched.

- Health signals and measurable abort thresholds: require exactly one source
  SHA/digest; the expected schema transition; healthy/provisioned exact
  workload revisions; drained older worker/API revisions; HTTP 200 health;
  unchanged allowlisted flags; zero unexpected schema/data differences; zero
  wrong-environment identities or URLs; zero real-data or external side
  effects; and every specified synthetic/Auth smoke passing. One mismatch,
  timeout, unknown value, partial migration, or unavailable proof aborts.
- Canonical evidence record, stored outside the repository with restrictive
  permissions, contains: workflow and run IDs; dispatch actor and triggering
  actor; protected-master SHA; candidate SHA and approval PR; provenance checks
  at every boundary; test/check conclusions; source registry/repository/tag and
  immutable digest; migration file hashes; schema before/after; sanitized
  aggregate/HMAC data invariants; migration timing/result; previous recovery
  digest; workload names and revisions before/after with resolved digests;
  allowlisted non-secret flags and URLs; environment-policy before/after
  digests; approved vendor-fingerprint-set digest and comparisons;
  content-addressed build-archive hash; job conclusions including failures and
  skips; health and smoke results; UTC timestamps; and cleanup result. It
  contains no secret, token, OTP, email, patient/user identifier, DSN, payload,
  or PHI.
- Founder-only production/PHI promotion record: outside this flight. Staging
  evidence may support a later explicit production decision, but no agent runs
  production deployment. The existing production workflow, environment,
  ancestry, Terraform Apply, provider-callback, actor, and rerun controls remain
  byte-for-byte unchanged. If production still rebuilds instead of promoting
  the tested staging digest, retain that source-to-artifact gap explicitly.

## Security and recovery

- Open security acceptances: global staging synthetic-only proof, an approved
  sanitizer/verifier, restore roles and RTO/RPO, least-privilege migration and
  runtime identities, registry admin credentials, and full staging vendor
  account separation remain gates. No dark-development or non-production
  exception authorizes PHI or production promotion.
- Behavior recovery: turn off only the affected reversible staging behavior,
  stop new writes, preserve evidence, and use the previous compatible image
  digest. Do not activate excluded features as a workaround.
- Image recovery: the workflow validates the supplied `recovery_digest` before
  mutation and records each old workload revision/digest. Redeploy only that
  immutable staging digest when its schema compatibility is proven.
- Partial publication recovery is manual and fail-closed. If any registry
  manifest or candidate tag may have been created before publication evidence
  completes, record the discoverable tag/digest and stop before migration or
  deployment. Do not automatically delete, overwrite, retag, or retry it. A
  separately authorized operator must reconcile the exact registry state and
  attach the result before a fresh dispatch can be considered.
- Schema/data recovery: before any new durable evidence and only after all old
  and new writers are drained, guarded online downgrade may be considered on
  the isolated rehearsal. Live staging defaults to roll-forward. After any new
  onboarding, Stripe, seat, or BAA evidence, never downgrade, delete, rewrite,
  or relabel it; isolate the failing behavior and deploy a reviewed forward fix.
- Data damage: stop all synthetic traffic, keep the affected target isolated,
  preserve value-minimized evidence, and use the approved non-production
  recovery procedure. Never restore or copy production data for this flight.
- GitHub environment-policy recovery: before dispatch, a failed transition is
  reversed by atomically renaming the same verified policy ID from `master`
  back to its captured `staging` value and proving the full prior-policy digest.
  After a canonical run has begun, do not reopen the legacy staging deployment
  path as an application rollback; keep dispatch blocked and repair through a
  separately reviewed control change.
- Observation window before irreversible cleanup: no schema or legal evidence
  cleanup is planned. Keep excluded flags off and observe the complete agreed
  staging window, including Auth refresh/focus/visibility/sign-out-in behavior
  and scheduled workload health, before any later production proposal. The
  owner records the duration before dispatch; it is not shortened after seeing
  results.

## Completion

- The outcome is complete only when the workflow change is merged and reviewed,
  the environment policy is verified as exact `master`, the isolated migration
  rehearsal passes, PR #230 is deployed by immutable digest after explicit
  confirmation, every staging identity/configuration check and synthetic smoke
  passes, PR #238 is updated/reviewed/merged, its real Clerk smoke passes with
  OTP confirmation, and its staging deployment passes after a separate
  confirmation.
- Required final report: exact workflow/master, candidate, PR #238 final and
  merge, deployed source, migration, image, recovery, and workload revision
  SHAs/digests; check names and conclusions; environment-policy evidence;
  schema/data invariants; non-secret flags; smoke matrix; unresolved risks; and
  the exact `AUTH_BASE_SHA` for the Zoom chat.
- Follow-up cleanup PRs and owners: separately implement a reusable
  restore-target allowlist, deterministic sanitizer, exhaustive synthetic-only
  verifier, and TTL cleanup automation; separate staging runtime and automated
  test Clerk instances; move database/runtime and registry access to
  least-privilege identities; and close production source-to-artifact promotion
  only through an independently reviewed Tier-2 plan.
- Documentation and contract records to update: the protected release change
  record, immutable evidence manifest, staging environment-policy record,
  migration rehearsal report, PR #238 flight plan/review evidence, and the
  Zoom handoff containing only the final `AUTH_BASE_SHA`.
