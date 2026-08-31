# Existing-user entry regression — fail-loud diagnosis (2026-08-31)

## Decision

This is a proven Cosmas authorization regression, not a Clerk lockout.

The affected users can authenticate, but the production practice gate currently
prevents them from entering PHI-bearing product routes. An active legacy-pilot
grant was originally a complete, revocable access basis. PR #188 changed it to
entitlement-only and began requiring a practice membership, practice
authorization, and an in-force BAA in addition to the still-valid grant.

Production evidence proves the impact is cohort-wide:

- the aggregate pilot audit reports 47 active grant lineages, zero revoked
  lineages, zero integrity errors, and a valid cohort;
- all 47 active grants belong to existing Doctors whose onboarding was
  previously completed;
- all 47 grants predate the PR #188 behavior change;
- all 31 active managed-domain Doctors have a valid active grant, but the
  current resolver denies 29 with `practice_membership_required` and two with
  `baa_not_in_force`;
- the same 29/2 result occurs with the entitlement check disabled, so billing
  and Stripe are not the cause.

The repair in this PR restores the historical access basis for every existing,
completed-onboarding Doctor with an active exact-account pilot grant. It is not
hard-coded to either reported user or to an email domain. Doctor deletion,
grant revocation, and revoked or inactive membership remain higher-priority
denials. Accounts without a valid grant still use the normal membership,
authorization, BAA, and entitlement gates.

This PR is not live. Until a human separately names a merge and a later
production Build & Publish, affected users may complete Clerk authentication
but still cannot enter the product.

## What fails, where, and why

The observed sequence is:

1. Clerk verifies a passwordless email-code or Google sign-in and creates a
   session.
2. Cosmas binds the session to the existing Doctor using the stable Clerk
   subject.
3. Practice-exempt bootstrap routes return 200.
4. `/internal/v1/notes` reaches the default practice gate.
5. `resolve_doctor_access()` rejects the Doctor despite an active exact
   legacy-pilot grant.
6. The product route returns 403. Founder support-view maps that denial to
   `target_access_pending` and displays:

   > Account not ready
   >
   > This account is not ready for interactive access yet. No onboarding, BAA,
   > billing, or account form is available while reviewing this state.

That exact screen is a support-view interstitial, not a Clerk page and not
necessarily the screen the target user sees. It is nevertheless diagnostic:
`app/services/view_as.py` emits `target_access_pending` only after the target
identity resolves but product access does not. The frontend deliberately
collapses membership, practice authorization, BAA, and entitlement denials
into that one message.

PR #188, merged at 09:52 EDT on 2026-08-28, changed
`app/services/doctor_access.py` from:

- active exact grant -> allowed after disabled/revoked tombstone checks

to:

- active exact grant -> entitlement only, and only when its practice snapshot
  matches a current membership;
- membership + organization/workforce authorization + BAA still mandatory.

The changed code reached production in revision `982d552` at approximately
11:14 EDT on 2026-08-29. It can explain failures from that deployment forward.
There is no attributable browser or Clerk event that proves what happened
before that deployment, so an earlier Friday report is not rewritten as this
same event.

The live database state is exactly the state the new logic rejects. The
managed-domain cohort has 29 grant holders with no membership and two with
authorized memberships but no BAA recorded in force. This is not an individual
password, Google-account, email-domain, billing, or capture problem.

## Pseudonymous incident evidence

No raw email address, deterministic email digest, Clerk subject, session
identifier, token, IP address, or patient information is recorded here.

| Evidence | `hmac:1d9d0260c7ae` | `hmac:c1017665b46d` |
| --- | --- | --- |
| Clerk account state | Verified, unlocked, unbanned, passwordless | Verified, unlocked, unbanned, passwordless |
| Successful Clerk path | Google session completed at approximately 16:58 EDT | Google session completed at approximately 17:25 EDT |
| Cosmas result | `/notes` 403; status/settings/practice recovery routes 200 | `/notes` 403; status/settings/practice recovery routes 200 |
| Exact access decision | `practice_membership_required` | `practice_membership_required` |
| Active grant | Yes; exact identity and retained digest match | Yes; exact identity and retained digest match |

Both Google external-account records predate this incident. Their dashboard
timestamps changed after successful sign-in; that update time must not be
reported as the connection-creation time.

One incident also included repeated email-code requests before Google
succeeded. A newly requested Clerk code invalidates an older code, so only the
newest message should be used. That is a separate credential-flow issue and
does not explain the later Cosmas 403.

## Why the fix covers all existing users

The compatibility predicate uses durable account state, not an email string:

1. the Doctor is not disabled;
2. no revoked grant tombstone exists;
3. any existing membership is neither revoked nor inactive;
4. the exact subject/email grant is active and passes its retained digest
   checks;
5. the Doctor had already completed onboarding.

The production aggregate is 47 of 47 active pilot Doctors satisfying that
existing-user predicate. It includes all 31 managed-domain Doctors. Forty-four
of the 47 intentionally have no practice snapshot, so requiring a snapshot or
manufacturing a membership would not restore the documented grandfather
cohort.

A fresh or pre-login account does not become authorized merely because a grant
row exists. A normal account without an active grant remains fail-closed at the
existing practice gates. This PR does not create memberships, alter a BAA,
grant billing, change a role, or edit Clerk.

The public self-service authorization contract remains unchanged. This is a
compatibility exception for a completed-onboarding Doctor with an active exact
legacy grant, not a way for a new public account to replace membership or BAA
evidence with a grant. The blanket entitlement-only language introduced by
#188 in `contracts/public-entitlements/v1/README.md` and
`docs/public-auth-entitlement-handoff-v1.md` governs the new public path but
conflicts with the older grandfather rollout for this closed cohort. This
diagnosis and its executable tests supersede that language only for the narrow
completed-onboarding legacy-grant case. Those public/billing-stack files remain
untouched by instruction.

The runtime predicate intentionally does not hard-code a date, email domain, or
list of users. A future privileged issuance of an active exact grant to a
Doctor who already completed onboarding would receive the same legacy access;
grant creation remains a separate reviewed administrative action. The
production statement that all currently eligible accounts predate #188 is an
audited fact about today's 47 rows, not a hidden date check in the code.

## Founder reproduction

Reproduce and verify without impersonating an affected live user:

1. In Clerk, open an affected user from the private incident handoff. Confirm
   the account is verified, unlocked, unbanned, and passwordless. Do not unlock,
   add a password, reset MFA, or edit connected accounts.
2. Read the existing Clerk and application events. A successful attempt has
   `sign_in.created`, `session.created`, and `sign_in.completed`, followed
   by 200 responses from practice-exempt bootstrap routes and a 403 from
   `/internal/v1/notes`. That combination means login succeeded and the
   product gate failed.
3. The founder's already-observed support-view result is the exact **Account
   not ready** interstitial above. Starting or changing support-view is an
   audited state change and is not required to repeat the diagnosis.
4. Run the existing aggregate-only command in the production application
   image:

   `python -m app.cli audit-pilot-access`

   Require `base_count=47`, `active_count=47`, `revoked_count=0`,
   `integrity_error_count=0`, `cohort_integrity_valid=true`, and
   `state=active`. The command prints no identities.
5. Evaluate access in read-only aggregate form. Set
   `TARGET_EMAIL_DOMAIN` privately in the shell, then run this inside the
   production application image. It emits counts and denial labels only:

   ```python
   import json
   import os
   from collections import Counter

   from app.models.doctor import Doctor
   from app.services.doctor_access import resolve_doctor_access
   from app.services.pilot_access_grandfather import active_pilot_access_grant

   domain = os.environ["TARGET_EMAIL_DOMAIN"].strip().lower().lstrip("@")
   doctors = [
       doctor
       for doctor in Doctor.all()
       if doctor.deleted_at is None
       and (doctor.email or "").lower().endswith(f"@{domain}")
   ]
   decisions = [resolve_doctor_access(doctor) for doctor in doctors]
   no_entitlement = [
       resolve_doctor_access(doctor, require_entitlement=False)
       for doctor in doctors
   ]
   print(
       json.dumps(
           {
               "account_count": len(doctors),
               "active_grant_count": sum(
                   active_pilot_access_grant(doctor) is not None
                   for doctor in doctors
               ),
               "completed_onboarding_count": sum(
                   doctor.onboarding_completed_at is not None
                   for doctor in doctors
               ),
               "decisions": dict(
                   Counter(
                       decision.denial_reason or "allowed"
                       for decision in decisions
                   )
               ),
               "decisions_without_entitlement": dict(
                   Counter(
                       decision.denial_reason or "allowed"
                       for decision in no_entitlement
                   )
               ),
           },
           sort_keys=True,
       )
   )
   ```

   Before this patch, require 31 accounts, 31 active grants, 31 completed
   onboardings, and the current 29 `practice_membership_required` / two
   `baa_not_in_force` split in both decision maps. Do not print emails,
   subjects, database IDs, or patient data.

Verify the proposed repair in CI and, if separately authorized, with an
approved non-human staging smoke account before any later publication:

1. An existing completed-onboarding Doctor with a valid active grant and no
   membership is allowed with authorization basis `pilot_grant`.
2. The same is true when a legacy membership exists but no personal/practice
   BAA is recorded, matching the grandfather contract.
3. A disabled Doctor, revoked grant, revoked membership, or inactive membership
   remains denied.
4. A completed-onboarding Doctor without a grant remains denied by the normal
   practice gate.
5. A new or incomplete-onboarding synthetic grant does not bypass onboarding.
6. An approved non-destructive pilot smoke account reaches one product view,
   and support-view no longer reports `target_access_pending`.

Record only fixed route, status, timestamp, counts, and HMAC/SHA-256 references.
Never copy tokens, raw identifiers, email addresses, or response bodies into
the PR.

## Last-week PR and deployment audit

Window: 2026-08-24 00:00 EDT through 2026-08-31. The audit found 34 merged PRs:
26 to `master` and eight staging-only merges. The deployed commit graph had no
unmatched direct push.

| Area | PRs | Relevance |
| --- | --- | --- |
| Auth, session, account state | #160, #162, #164, #182 | #160 introduced the root support-view guard and can obscure a pre-session browser failure, but it does not produce the proven post-session `/notes` denial. #162 promoted it; #164 is schema preparation; #182 updates dependencies. |
| Authorization, legal, entitlement | #188, #217, #218, #219, #221, #222, #228 | #188 is causal. #217–#222 are billing/Stripe work; disabling entitlement produces the same denials, so they are excluded. #228 changes clickwrap-current-version behavior; 29 accounts fail before BAA evaluation and the other two have no BAA in force rather than a stale edition. |
| Unrelated product behavior | #223, #227 | These changes do not touch Clerk, sessions, Doctor binding, or practice access and cannot be reused for login. |
| Deployment, infrastructure, and policy/docs | #157, #158, #161, #163, #165, #166, #167, #169, #171, #175–#181, #183, #184, #187, #216, #220 | Audited for deployment or authentication effects; none changes the access decision that emitted these 403 responses. |

Production ran `9b98c69` from late 2026-08-24 until the manual deployment on
2026-08-29. Revision `982d552`, containing #188 and #228, completed deployment
at approximately 11:14 EDT on 2026-08-29. Other reviewed runs in the period
performed validation or staging work and did not create a separate production
revision.

## Verification

- 310 affected-consumer backend tests pass (three skipped), including the full
  protected practice-route suite, Doctor access decisions, API-key
  authorization, jobs, callbacks, and a new authenticated
  `/internal/v1/notes` regression proving that an existing grant user reaches
  the product without manufacturing a membership or BAA.
- The tests preserve denials for a disabled Doctor, a revoked grant, a revoked
  or inactive membership, a digest-mismatched grant, an incomplete-onboarding
  grant, and a normal account without a grant.
- Ruff check, Ruff format check, and `git diff --check` pass for the changed
  files.
- Required GitHub PR checks must be green before handoff; no passing check
  authorizes merge or publication.

## Scope, safety, and recovery

- No user-specific exception, domain allowlist, password, MFA, account merge,
  unlock, or Clerk mutation is included.
- No practice, membership, BAA, billing, patient, visit, capture, or webhook
  record is changed.
- No file owned by the separate capture/visit-bind task, and no protected
  onboarding, BAA-model, billing-stack, internal-route, infrastructure, or
  production-configuration file is changed.
- `resolve_doctor_access()` is shared by interactive routes and durable-job
  authorization. Restoring the existing-user grant basis therefore has a
  deliberate transitive effect wherever that central decision is consumed; it
  does not alter any capture dispatch, meeting join, callback, or visit-binding
  logic.
- No email was sent.
- No Build & Publish, deployment, Terraform, flag flip, production mutation, or
  user impersonation was performed. Production work was limited to read-only
  Clerk/log inspection and aggregate authorization queries.
- Recovery is a normal revert of this PR's runtime commit.
- Merge is not promotion. This PR neither performs nor authorizes a merge or
  later production publication.
