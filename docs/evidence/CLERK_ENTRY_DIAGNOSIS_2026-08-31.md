# Clerk entry fail-loud diagnosis — 2026-08-31

## Decision

This is a diagnosis-only change. No Cosmas code or configuration defect was
proved, so this branch changes no runtime behavior.

The password path is explained at the live Clerk credential boundary: the
unlocked row had no password, so that path cannot reach any practice,
agreement, domain, or entitlement decision. The historical
Google/user-visible failure is not reconstructed because Clerk has no
attributable failed event and no browser trace was captured:

- The managed-email Clerk row was verified, not banned, and not locked, but it
  had no password credential (`password_enabled=false`). An email-and-password
  attempt therefore could not authenticate that row. "Unlocked" does not mean
  that a password exists.
- At 16:58:45 EDT on 2026-08-31, Clerk recorded a successful `oauth_google`
  sign-in for the managed-email row. The user card reported that Google social
  account as newly added at the same time. This establishes when the current
  Google connection completed, but it does not establish why an earlier Google
  attempt may have failed.
- At 16:58:47–16:58:48 EDT, the same pseudonymous Cosmas account received HTTP
  200 from `/internal/v1/view-as/status`, `/internal/v1/settings`,
  `/internal/v1/practice`, and
  `/internal/v1/practice/organization-access`. The Google sign-in therefore
  crossed the Clerk session and Cosmas identity-bootstrap boundaries without a
  persistent backend rejection. These practice-gate-exempt responses do not
  prove the user's practice, agreement, domain, or entitlement state. They
  prove server-side bootstrap, not that the user saw a rendered screen.

Production Clerk also contains a separate, older personal-email row with the
same human name. It is not a second row for the managed login email: the exact
managed address occurs once among all 47 live users. The older row has a
different verified primary email and a different Clerk subject, its Google
account was linked about eleven months ago, and its last sign-in was in October
2025. Do not merge these rows. A merge is neither required by this diagnosis
nor authorized by it.

The incident report places the user-visible onset on Friday or Saturday,
2026-08-28 or 2026-08-29, and reports another failed interval at 11:00–12:10 EDT
on 2026-08-31. The live state changed after that Monday interval. The later
successful sign-in does not rewrite the incident: the PHI-safe Cosmas log query
returned no bootstrap request for the affected pseudonymous account and no
fixed identity-failure event during that interval. It does establish that the
correct managed-email row can now create a Google session and reach the Cosmas
bootstrap.
The dashboard inspection was read-only and did not create that session or
modify either user.

The same HMAC account also had HTTP 200 bootstrap traffic outside the reported
window, including 09:52, 13:43, and 14:33 EDT on 2026-08-31. An already-open
browser can retain a valid session and poll status while a fresh sign-in on a
different browser is unavailable. These rows rule out a persistent session or
identity-bootstrap rejection; they do not prove access-state booleans or which
person was looking at which rendered screen.

## Pseudonymous evidence

No email address, raw Clerk subject, token, session ID, device ID, IP address,
or patient information is recorded here.

| Evidence | Pseudonymous reference / result |
| --- | --- |
| Normalized managed email | `sha256:d07527cbd35cb3833260f035de69a0109b50218eef5f179a31e00e2c3ca36521` |
| Managed-email Clerk subject | `sha256:bcd88c83e9887129d9a1b24a3aa74159dc62966b77655c7f961dd94890c9b8be` |
| Separate personal-email Clerk subject | `sha256:4dd8d2ded2ec6b8e5bb58b90ce12a8e5fffa68f1fecb0bd654c469fdedcf57d5` |
| Cosmas access-log account | `hmac:1d9d0260c7ae` |
| Reported failure window | 2026-08-31 11:00–12:10 EDT: zero fixed bootstrap-path rows for that HMAC reference |
| Successful existing-session traffic outside the window | 2026-08-31 09:52, 13:43, and 14:33 EDT: fixed bootstrap paths returned HTTP 200 |
| Later Clerk result | 2026-08-31 16:58:43 EDT `sign_in.created`; 16:58:45 EDT `session.created` and `sign_in.completed`; strategy `oauth_google` |
| Later Cosmas result | 2026-08-31 16:58:47–16:58:48 EDT: status/settings/practice/organization-access all HTTP 200 |

Clerk has no subject-bound failed event that reconstructs the exact
Friday/Saturday click, browser screen, or Google identity selection. The Clerk
production log does contain one unbound
`oauth_authorization.failed` event at 05:35:15 EDT on 2026-08-28 with reason
`oauth2idp_patch_fosite_state_non_invalid_state_error`. That event has no actor,
subject, or IP in Clerk, so it cannot honestly be attributed to this user and
is not used as proof.

## Why no Cosmas code cause is proved

1. `web/app/routes/sign-in.tsx` delegates every sign-in method to the same Clerk
   `<SignIn>` component. Cosmas has no Google-versus-password account-selection
   branch.
2. `web/app/configuration/client.ts` obtains a token from the resulting Clerk
   session. `app/routes/dependencies/clerk.py` verifies that token, and
   `app/routes/dependencies/doctor.py` binds the Doctor only by its stable Clerk
   `sub`. Google and password on one Clerk row are therefore one Cosmas account.
3. Backend dependency order is Clerk session, Doctor binding, then practice
   access. The settings, practice, organization-access, and onboarding routes
   are exempt from the normal practice gate in
   `app/routes/dependencies/practice_gate.py` so that a signed-in user can reach
   recovery and setup.
4. Missing membership, organization authorization, or agreement sends a
   successfully authenticated user to onboarding. A lapsed entitlement sends
   a practice-ready user to Settings. Neither can suppress a Clerk sign-in
   event.
5. A different Clerk subject that presents an email already owned by a Doctor
   returns the explicit HTTP 409 account-recovery path. No such response was
   observed in the later successful path.

Current master does contain a separate fail-loud risk: the root support-view
guard wraps `/sign-in/*`, including the Google callback. A failed Clerk
bootstrap or failed `/internal/v1/view-as/status` can replace the sign-in UI
with a support-status error, and a never-settling Clerk bootstrap can leave the
loading screen in place. The incident supplied no screen or network evidence
for that branch, while the live credential evidence above directly explains
the password path and records a later successful Google callback.
It is therefore not a proved incident cause and is not changed here.

## Founder reproduction and discriminator

Use a clean browser profile and preserve the Network panel. Never copy tokens,
raw identifiers, emails, or response bodies into an issue or PR.

1. In the production Clerk dashboard, search for the affected managed email
   from the private incident handoff. Locally normalize and hash it if needed;
   its SHA-256 must match the managed-email reference above. The raw address
   must not be copied into this PR or a log query.
2. Read only the user card. Confirm `locked=false`, `banned=false`, the one
   primary email is verified, `password_enabled=false`, and Google is the one
   external account. Do not unlock, set a password, reset MFA, or edit the
   account.
3. Treat the Clerk choices distinctly:
   - Password: this row has none, so a password attempt is expected to fail at
     Clerk before Cosmas.
   - Email verification code: this is enabled for the live instance and is not
     the same thing as a password.
   - Google: choose the managed Workspace identity. A completed attempt must
     produce `sign_in.created`, `session.created`, and `sign_in.completed` for
     the managed-email subject.
4. If Google returns to `/sign-in/sso-callback`, keep the Network log and look
   for these fixed bootstrap calls; loader and render concurrency does not
   guarantee their order:
   - `/internal/v1/view-as/status`
   - `/internal/v1/settings`
   - `/internal/v1/practice`
   - `/internal/v1/practice/organization-access`
5. Classify any failure without changing data:

   | Observation | Boundary |
   | --- | --- |
   | No Clerk `sign_in.completed` | Pre-session path; Cosmas backend has not received an authenticated session. Use the visible state below to distinguish Clerk from the browser guard |
   | Endless `Loading application...` | Clerk browser bootstrap has not settled |
   | `Support-view status unavailable` | Clerk bootstrap failed, or an existing session could not complete the support-view preflight |
   | Settings HTTP 403 with `Not authenticated`, or verified missing/malformed Authorization evidence | The bearer credential was absent or rejected before identity binding |
   | Settings HTTP 401 | Clerk token/JWKS validation |
   | Settings HTTP 403 plus `managed_organization_identity_verification_failed` | Live primary-email evidence or stored-email equality failed |
   | Settings HTTP 403 plus `pilot_access_identity_verification_failed` | Pilot access identity verification failed |
   | Settings HTTP 409 plus `account_identity_recovery_required` | Different Clerk subject collided with an existing Doctor email |
   | Settings HTTP 410 | Local account/tombstone disabled |
   | Settings HTTP 200 and `practice_ready=false` | Auth succeeded; follow the practice/organization path |
   | Settings HTTP 200 and `billing_recovery_required=true` | Auth succeeded; follow the entitlement path in Settings |

6. Correlate only timestamp, fixed route, HTTP status, request ID, and HMAC or
   SHA-256 references. A successful Clerk event plus the four HTTP 200 results
   above is the founder-believable entry proof used for this diagnosis.

## Scope and recovery

- Risk tier: **Tier 0, documentation only**. No auth/session, entitlement,
  account, schema, infrastructure, or runtime behavior changes.
- No Clerk user was unlocked, edited, merged, or reset. No email was sent.
- No production build, publish, deployment, Terraform operation, environment
  change, or feature-flag change occurred.
- Recovery for this documentation-only change is `git revert <commit>`.
- Merge is not production promotion. Any merge and any later Build & Publish
  remain separate, human-named decisions.
