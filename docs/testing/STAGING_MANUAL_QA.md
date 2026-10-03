# Reusable staging manual QA accounts

This workflow prepares `biller@cosmaneura.com` (existing **owner** role,
billing and clinical access) and `doctor@cosmaneura.com` (existing **workforce**
role, clinical access). Neither is an application administrator. The owner is
the account for a tester who needs all ordinary customer functionality.

This is not a billing-only role or an authorization bypass. Only synthetic data
is permitted. Do not run the ordinary test database seeds against staging.

## Operator preparation

Use the reviewed, merged seed PR's exact commit. Staging deployment remains
founder-only; this runbook does not authorize deployment or Azure mutation.
First verify the actual staging database host/name, synthetic-only status, and
Clerk test instance independently against the environment inventory. Do not
derive the target pins from the credentials you are trying to verify.

Inject the following through a private operator environment (for example,
1Password). Never put passwords, keys, or database URLs in command arguments,
shell history, committed files, PR evidence, or screenshots:

- `PYTHON_ENV=staging`, `AUTO_MIGRATE_ON_STARTUP=false`
- `QA_SYNTHETIC_ONLY=yes`
- `DATABASE_URL` and the application's other normal runtime configuration
- `CLERK_PRIVATE_KEY` beginning `sk_test_`
- `QA_CLERK_KEY_SHA256`: SHA-256 fingerprint of the independently verified
  staging Clerk secret (not a hash of whatever key happens to be loaded)
- `STRIPE_LIVE_MODE=false`, test-mode `STRIPE_SECRET_KEY`
- `QA_BILLER_PASSWORD`, `QA_DOCTOR_PASSWORD`: distinct, generated passwords of
  at least 16 characters, stored in the staging vault for authorized testers

The default command is entirely offline and makes no writes:

```sh
python scripts/seed_staging_manual_qa.py accounts
python scripts/seed_staging_manual_qa.py accounts --apply \
  --database-host VERIFIED_STAGING_HOST --database-name VERIFIED_STAGING_DATABASE
```

An existing unmarked address is refused, not adopted or reset. Resolve an
address collision through the identity administrator; never relabel a real
account as synthetic. A rerun preserves existing passwords. The provider's
normal password checks remain enabled. Clerk Backend API-created emails are
[verified by default](https://clerk.com/docs/reference/backend/user/create-user);
this behavior is allowed only for these marked test identities in staging.

## One-time customer setup (performed by the human tester)

1. Sign in as `biller@cosmaneura.com`, using the staging-vault password.
   Create a **group** practice named exactly
   `Cosmaneura Manual QA - Synthetic Practice`. If the domain is reserved for
   managed organization access, stop: the seed does not override that policy.
2. Complete the real agreements yourself; the seed never signs a BAA.
3. Complete Stripe **test-mode** Checkout. If online billing is unavailable,
   resolve deployed billing configuration before proceeding; do not comp the
   practice. Confirm the authoritative webhook opens access.
4. Invite `doctor@cosmaneura.com` through the practice UI. Sign in as that user,
   accept the invitation and workforce attestation, and finish onboarding.
5. Capture the exact practice ID from trusted operator inspection. Seed inputs:

```sh
python scripts/seed_staging_manual_qa.py fixtures --apply \
  --database-host VERIFIED_STAGING_HOST --database-name VERIFIED_STAGING_DATABASE \
  --practice-id practice_EXACT_QA_ID
```

Both accounts must already have normal clinical access, verified identity,
the expected roles in the same practice, BAA, and an active/trialing subscription.
The script adds one labeled patient and two visits per account. Patient data
is provider-scoped; the two accounts do not share clinical records. Repeating
the command does not reset or move visits, alter notes, or reset billing.

## Tester walkthrough

Use the seeded previous visit to record a fictional consultation, generate and
edit a SOAP note, and exercise save/reopen/version behavior. Use the upcoming
visit for session prep after creating the prior visit's source through the app.
Exercise Scribe capture, stop, review, and handoff through the supported UI;
the seed does not fabricate audio, processed jobs, note versions, or prep grants.
Follow the application's consent prompts and use no real patient information.

As owner, test Checkout, portal, payment failure/recovery, seat invitations,
and cancel/reactivation with Stripe's test tools. As workforce, verify billing
controls are unavailable and another provider's records are inaccessible.
Cross-repo Scribe access and feature flags must be configured and verified
separately; creating a Core identity does not prove the integration works.

## Recovery and evidence

Failure output is deliberately redacted. Check private operator configuration,
ownership collisions, onboarding, domain policy, and access gates. If Clerk
creates only one identity before failing, rerun safely; no password is changed.
Fixture insertion is transactional. No destructive reset command is provided:
disable identities using the normal administrator process, retain legal/audit
history, and use a separately approved scoped cleanup if needed.

Record the reviewed commit, verified target, operator, execution time, phase,
and pass/fail without secrets or patient content. A successful seed is not a
release qualification or proof of webhook delivery.
