# Flight plan: preserve Clerk sign-in during support-view rechecks

## Outcome and boundaries

- Problem and measurable outcome: an already-rendered Clerk sign-in or sign-up
  control must remain mounted while routine support-view status checks are
  pending, so a background interval, focus, or visibility check cannot replace
  an in-progress authentication factor.
- In scope: the signed-out `/sign-in` and `/sign-up` guard lifecycle and its
  regression tests.
- Explicit non-goals: changing Clerk configuration, authentication methods,
  account recovery, authorization, tenant membership, BAA or entitlement
  policy, patient intake, Zoom/Recall behavior, or production infrastructure.
- Affected users, services, environments, and data classes: all users of the
  Core web sign-in and sign-up routes; frontend only; no schema or data change.

## Risk and dependencies

- Tier and reason: Tier 2 because the change is adjacent to authentication and
  the audited support-view security boundary.
- Irreversible or high-blast-radius decisions: none. The single behavior change
  is reversible by reverting the PR commit and redeploying the prior image.
- Dependency-ordered PR stack: PR #238 is independent and must remain a
  separate merge and rollback unit from Zoom/Recall PR #239, Zoom schema PR
  #241, Scribe PR #243, and Session Prep work.
- Cross-repo contract/version and receiver-before-sender order: N/A; no API or
  cross-repository contract changes.

## Current deployed state

The environment observations below are historical September 2 evidence, not
current deployment attestations. See the September 13 source-update checkpoint
at the end of this plan for the latest source-only reconciliation.

- Production evidence on 2026-09-02: API, worker, and scheduler are Running and
  provisioned successfully on image SHA
  `8fa02a71f4b880cd43f4fccc8c81c92a0d635623`; `/healthcheck` and `/sign-in`
  return HTTP 200. Production is behind current `master` after Session Prep PR
  #244 merged as a separate dark receiver.
- Staging evidence on 2026-09-02: deployed image
  `22eaf3ca9d64a07ad662ff6a3a9066d3b393a081`; the `staging` branch is materially
  behind `master`. Do not combine broad staging reconciliation with this auth
  change merely to obtain a smoke environment.
- Difference from `master` and deployed-only behavior: current `master`
  `a77f9f6e6940f9dd5bdc340a25f14f05c02cc9b7` contains the separately approved,
  dark Session Prep contract receiver from PR #244 and is not yet deployed.
  PR #238 adds only the guarded frontend mount-preservation behavior and tests
  on top of that source state.
- Reconciliation decision: retain staging temporarily and exercise the exact
  PR candidate locally with staging's public Clerk configuration. Reconcile
  the staging branch separately under its own reviewed plan. Hold production
  promotion until the Session Prep owner confirms that deploying the already
  merged dark receiver in the same promotion preserves its rollout boundary.
- Previous known-good artifact and data-recovery boundary: production image
  `8fa02a71f4b880cd43f4fccc8c81c92a0d635623`; no data recovery is required.

## Feature flag / release control

- N/A. This is a narrow corrective behavior change on public identity routes,
  and rollback is an exact code revert plus redeployment. The initial, active,
  unavailable, inconsistent, and cross-tab support-view checks remain
  fail-closed.

## Schema/data sequence

- N/A. There is no schema, migration, backfill, or production-data mutation.

## Rollout and promotion

1. Require all protected PR checks and independent review against the final
   pushed SHA.
2. Build the exact candidate with production bundling and staging's public
   Clerk configuration.
3. Exercise a non-human Clerk development identity: reach the email-code step,
   wait longer than the 30-second recheck interval, trigger focus and visibility
   rechecks, and require the same code and same mounted control to remain valid.
4. Confirm automated negative cases still hide identity controls on an active,
   unavailable, inconsistent, or cross-tab support-view result, and that public
   patient intake remains fail-closed.
5. Merge through protected `master`; merge alone does not deploy.
6. Founder manually promotes exact current `master`, then verifies health,
   public sign-in rendering, one approved MCD pilot journey, and aggregate
   authentication/application error signals.

- Abort on any remount/new-factor behavior, failed code, loss of fail-closed
  behavior, unexpected identity or tenant resolution, 5xx increase, or drift
  from the reviewed SHA.

## Security and recovery

- Founder source-merge authorization: in the latest follow-up on 2026-09-02,
  `JohnClem` approved PR #238 as an incremental auth-stability improvement and
  asked us to complete its flight plan, review, and real Clerk smoke. Source
  merge remains conditional on green protected checks, the exact-final-SHA
  review, and a successful smoke.
- Production/compliance release acceptance: pending. The source-merge approval
  does not authorize production promotion of current `master`, because it also
  contains separately owned, not-yet-deployed Session Prep PR #244. Require a
  coordinated release decision for that receiver plus the post-merge gates in
  this plan before promotion.
- This approval does not accept changes to tenant authorization, grandfathered
  pilot policy, Zoom capture, Scribe, Session Prep, or a broader cohort.
- Recovery: revert PR #238, promote the resulting exact `master` SHA, verify
  `/healthcheck` and `/sign-in`, and confirm that the previous image remains
  available until the observation window closes.
- Observation window: at least one completed approved MCD sign-in plus review
  of aggregate auth/application failures before pilot communication.

## Completion

- Required evidence: green protected checks; successful exact-candidate build;
  successful email-code stability smoke; exact-final-SHA independent review;
  founder production workflow record; production health and pilot canary.
- Follow-up: separately add timeout/retry recovery for a rejected cached Clerk
  bootstrap promise. Do not expand PR #238 to include it.
- Pilot communication must describe this as sign-in stability hardening, not a
  Zoom fix or a change to account, tenant, BAA, billing, or patient data.

## September 13 source-update checkpoint

- The latest read-only GitHub check found protected `master` at
  `e21ba4c1fb1027de8123aca9b8aee25b8a11b612`, and PR #238 still open at
  `c0cbce2c4e5fe2f53108eec27395f73c281e3638` with automatic merge disabled.
- The source update is a normal merge of that exact master into the existing
  Auth branch, prepared in an isolated local checkout. No rebase, force-push,
  default-branch write, environment mutation, deployment or OTP entry is part
  of this step.
- The two Auth implementation/test files are byte-for-byte unchanged from the
  previously reviewed PR head. The three PR paths had no upstream overlap;
  the merge was conflict-free. The PR remains its own merge/rollback unit.
- Existing September 2 green checks are historical. Current-head protected CI
  and independent review must be recorded against the resulting pushed commit;
  they cannot be inferred from the prior head or this document. Unchanged
  passing local tests are not rerun merely to refresh their timestamps.
- Real Clerk development-identity email-code stability smoke is still
  incomplete. A mock, a reached code-entry screen, or a green CI run does not
  satisfy the same-code, same-mounted-control, >30-second and focus/visibility
  requirements. Do not enter an OTP without the required live human action.
- Staging private-foundation preparation is a separate workstream. Its prior
  plan and ten-minute handoff expired; its next human plan has not succeeded.
  This source update neither accepts that plan nor authorizes staging cutover.
- Zoom #239 and stacked #251 remain untouched pending the accepted exact Auth
  base. Preserve the receiver-first, default-off watchdog rollout and its
  synthetic callback, consent, idempotency, revocation and notification gates.
- No source merge, live staging acceptance, outside-party contact, broader
  cohort, or production promotion is authorized by this checkpoint. Existing
  protected merge, independent-review and founder deployment boundaries remain.
