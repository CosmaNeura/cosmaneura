# Priority 3 public onboarding and entitlement receiver handoff v2

Status: the exact implementation receiver is published in draft PR
[#199](https://github.com/CosmaNeura/cosmaneura/pull/199). This packet authorizes
downstream review and restacking only. It does not authorize a merge, deployment,
cohort activation, credential approval, or real-PHI processing.

## Release identity

| Field                      | Value                                                                                                                                                     |
| -------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Capability                 | Core-owned public authentication, practice onboarding, active-clinician seat policy, and server-authoritative capability decisions                        |
| Governing architecture     | ADRs 0022–0024 in [architecture PR #10](https://github.com/CosmaNeura/cosmaneura-architecture/pull/10), commit `5451df63e2fbeaf386ca5517d762e6d1359b053c` |
| Runtime contract           | `contracts/public-entitlements/v2/capability-entitlement.schema.json`                                                                                     |
| Runtime version            | `public-capability-entitlements.v2`                                                                                                                       |
| Runtime schema SHA-256     | `a51843ed0810a2b43fd16cf6e66eb2830717c8381aa7fea3f8d05b9e55cb1bad`                                                                                        |
| Compatibility contract     | `contracts/public-entitlements/v1/capability-entitlement.schema.json` remains byte-pinned                                                                 |
| V1 schema SHA-256          | `a33ba095ea284575fc8004ecc7b2e05b6aa60c911f5c03add3a0ea1f7ef9e578`                                                                                        |
| Code implementation parent | `27e480ee4898c985aa64d38578afa474c045b643` on `codex/public-onboarding-11-review-hardening-20260827`                                                      |
| Implementation PR          | [#199](https://github.com/CosmaNeura/cosmaneura/pull/199), based on PR #196 / `dccbf9dc8eee36a6eee52ed2ed17c2ddd7d41c8b`                                  |
| BAA                        | `app/assets/baa/baa_cn-baa-cust-cw_v2.2.html`, unchanged SHA-256 `13448bf42105db05e36e566f2f8ea0190926223e9dbb03efb8778cf8cfc7332e`                       |

The documentation commit cannot safely name its own SHA. Consumers must pin the
externally attested final PR #199 head that contains this packet, not the code
parent above. That immutable reviewed SHA must travel with this packet when the
receiver is handed off.

### Ordered receiver stack

Each PR targets the preceding branch and stays independently within the change
budget. Consumers pin the externally attested final PR #199 head, not a
superseded aggregate candidate or the code parent alone.

| Order | PR                                                                                                        | Head SHA                                                                                        |
| ----- | --------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| 1     | [#185](https://github.com/CosmaNeura/cosmaneura/pull/185) contract freeze                                 | `3bde3ae155423947d870f636ee907135949fafcb`                                                      |
| 2     | [#188](https://github.com/CosmaNeura/cosmaneura/pull/188) evidence schema                                 | `a12ac36ca7f08ab4ae582829a19c0befd3b85a5b`                                                      |
| 3     | [#189](https://github.com/CosmaNeura/cosmaneura/pull/189) capability policy                               | `c23e0576a0acd6383cf463db0e7c51fbd54af50e`                                                      |
| 4     | [#190](https://github.com/CosmaNeura/cosmaneura/pull/190) atomic seats                                    | `54ad87c1e367158ec18b23612ccd6012df35a421`                                                      |
| 5     | [#191](https://github.com/CosmaNeura/cosmaneura/pull/191) web auth boundary                               | `b0d37d92fe6dc2ecb5eb03d303f58afd6823371d`                                                      |
| 6     | [#192](https://github.com/CosmaNeura/cosmaneura/pull/192) public route core                               | `0cb5724ac17668ba9bc1032a81f04bb3f0d7a6ef`                                                      |
| 7     | [#198](https://github.com/CosmaNeura/cosmaneura/pull/198) route contract                                  | `2a8f0b5e5f5a84a81a68082a435f9e00b3dbb3c8`                                                      |
| 8     | [#193](https://github.com/CosmaNeura/cosmaneura/pull/193) NPI-free API                                    | `bf0adab35c2da19e7b2dda8c950f95a8b5265c7c`                                                      |
| 9     | [#194](https://github.com/CosmaNeura/cosmaneura/pull/194) public UI                                       | `a6dbdd8febb75b7076e77c0481ee74c180c92d87`                                                      |
| 10    | [#195](https://github.com/CosmaNeura/cosmaneura/pull/195) browser proof                                   | `5516fd68facee8edbb5edf0f09fc26e1f57f3ed3`                                                      |
| 11    | [#196](https://github.com/CosmaNeura/cosmaneura/pull/196) fail-closed activation                          | `dccbf9dc8eee36a6eee52ed2ed17c2ddd7d41c8b`                                                      |
| 12    | [#199](https://github.com/CosmaNeura/cosmaneura/pull/199) review hardening, v2 contract, and this runbook | final reviewed head supplied externally; code parent `27e480ee4898c985aa64d38578afa474c045b643` |

## Canonical receiver contract

Authenticated clients read `GET /internal/v1/capabilities`. The route accepts no
actor, practice, role, or capability selector. Core resolves the normalized
identity, Doctor, membership, and practice server-side. Client claims, email or
domain, redirects, rollout flags, subscription strings, and seat counts confer no
authority. A consumer grants only when the relevant server decision is
`allowed`; an unknown version, value, missing fact, dependency failure, or stale
cached decision denies.

V2 preserves the v1 actor, gate, seat, capability, and reason-code fields while
making two formerly prose-only rules machine-verifiable:

- every `rollout: disabled` decision is denied, has `data_mode: none`, includes
  `rollout_disabled`, and cannot include `allowed`; and
- `cab.operation_mode` is exactly `advisory_only`.

The five capability keys are `soap_note`, `intake_prep`, `session_prep`,
`scribe`, and `cab`. Session Prep is read-only. Scribe is web/PWA first, with
other supported clients deferred. Intake Prep's downstream content contract is
exactly five versioned questions, editable only by an authorized practice user.
CAB supplies billing intelligence for human review only: it authorizes no EHR
write, 837P generation or submission, claim or appeal submission, clearinghouse
or payer enrollment, or patient contact.

V1 remains byte-pinned for historical receivers; it is not the runtime response
version after this receiver. A v1-only consumer must fail closed until it adds
v2 validation. V2 receivers use
`contracts/public-entitlements/v2/synthetic-credential-pending.json` in CI and
must not reconstruct policy outside Core.

The public onboarding read remains resumable in this order:
`practice → baa → legal → checkout → credential → complete`.
`checkout_started` is display evidence, not entitlement. Invitation responses
always contain the fields `invite_token` (nullable) and
`reused_idempotency_key` (boolean), matching the regenerated OpenAPI/client
contract.

### Subscription and Checkout boundary

The public paid-pilot commercial decisions remain `$89` per active clinician per
month, card collected during app-owned onboarding Checkout, and a fixed 14-day
trial beginning when an authoritative hosted Checkout Session is created.
Priority 1 owns creation of that billing clock, carrying the exact end to Stripe,
webhook verification, reconciliation, and cancellation. This receiver does not
duplicate or alter that seam.

Core grants the paid subscription gate only from a verified `active` projection,
or `trialing` with a future `trial_ends_at`. Both require trimmed Stripe IDs with
`cus_` and `sub_` prefixes and a positive authoritative `stripe_state_version`.
A Checkout Session or timestamp alone does not entitle. `pending_verification`,
`invalid`, unknown or malformed states, incomplete, expired, past-due, unpaid,
paused, and canceled states deny. An explicit internal billing exemption can
satisfy the gate, so clients still consume the capability decision rather than
reimplementing this rule.

Canonical in-process billing helpers are:

```python
from app.services.public_entitlements import (
    active_clinician_seat_count,
    has_valid_stripe_subscription_binding,
    MAX_ACTIVE_CLINICIAN_SEATS,
)
```

Priority 1 must restack on the exact implementation receiver, use these helpers,
and validate the v2 schema before the pair can be promoted. The
pending-credential-at-trial-expiry policy remains undecided and must not be
inferred.

### Credential and NPI boundary

Credential states are exactly `pending`, `approved`, `rejected`, and
`needs_review`. Every new member starts `pending`; only an independently audited
`approved` state may satisfy the real-PHI gate. No public approval endpoint or
self-approval path exists. The verification method, authorized reviewer, and SLA
remain TBD, so reviewer authorization is deliberately unconfigured and approval
attempts fail closed.

Public signup and onboarding do not render, collect, validate, transmit, or
persist NPI. The public form, request type, backend request schema and persistence
path, OpenAPI, generated client, identity, payment, credential, entitlement, and
tenant-authority paths are NPI-free. Nullable legacy model/storage remains only
for compatibility, and a database constraint prohibits NPI on public-onboarding
practices.

NPI is public registry data, not authentication, identity or licensure proof,
credential approval, or access control. A future approved payer-facing
HIPAA-standard transaction or integration may collect the appropriate NPI only
just in time after purpose, minimum data, retention/deletion, audit, activation,
and rollback are documented. NPPES is not integrated by this receiver.

### Atomic 20-seat policy

An active clinician seat is a persisted `PracticeMember` with
`seat_state = active` and no `access_revoked_at`; the owner counts. Pending,
expired, or revoked invitations and inactive or access-revoked memberships do
not count. `MAX_ACTIVE_CLINICIAN_SEATS == 20`, and
`active_clinician_seat_count(session, practice_id)` is authoritative.

Public invitation acceptance, managed roster claim, legacy join, and every
deactivate/reactivate path serialize on the Practice row and record the Stripe
quantity reconciliation obligation in the same transaction. The 21st activation
returns `409` before commit. Existing over-cap data can only move monotonically
toward compliance: `22 → 21` commits with no invalid Stripe target and retained
sync-required evidence; reaching 20 publishes target 20. A zero-seat transition
similarly retains a no-target cancellation-required obligation instead of
posting Stripe quantity zero.

## Mixed-version migration and rollback boundary

The minimum safe mutating API artifact after the evidence migration is this PR
#199 receiver series at or after code commit
`d8bf0ba21a5cf1adc3f98ca4b4208d9d6f852756`; the exact release artifact is the
externally attested final PR #199 head containing this packet. Before enabling
public acquisition, drain every older or incompatible mutating API revision and
verify that only this receiver series can write Practice rows. Do not run a
pre-gate mutating binary after the migration, even though omitted-discriminator
inserts are expected to fail closed.

The evidence migration temporarily uses `false` only to backfill existing
managed practices, then removes the `public_onboarding_required` database
default in the same transaction. The discriminator remains `NOT NULL`. Every
approved new writer supplies it explicitly; a pre-gate binary that omits it
fails the insert instead of silently creating a managed/NPI-bearing row during a
migrate-before-code window.

After the migration, rollback to a pre-gate mutating API binary is prohibited;
after the first public practice or onboarding evidence exists, schema downgrade
is also prohibited. The downgrade locks the affected tables and refuses to
discard populated public, legal, invitation, or credential evidence. Recovery
is to stop acquisition and disable rollout, drain the incompatible revision,
keep the additive schema and audit rows dormant, and roll forward a reviewed
receiver. Do not bypass the discriminator, relax the public-practice NPI
constraint, or repair the incident by reintroducing a database default.

Execution-time capability checks also protect asynchronous PHI work. Recall
dispatch/download/session-start/ZAK issuance, Zoom registration/session-start
and OAuth credential persistence, example-note and lab extraction,
transcription, engagement scoring, medication extraction, and Zoom meeting sync
re-evaluate the canonical `soap_note` decision before work. The OAuth callback
checks both before provider exchange and again under the locked Doctor row
before storing returned credentials. Turning rollout off therefore stops queued
or late work; enqueue-time authorization alone is never sufficient. Cleanup and
teardown remain available so denial does not strand external resources.

## Failure, data, and retry semantics

- `401` means the application session is missing or expired; reauthenticate.
- `403` means identity, membership, credential, entitlement, or rollout denied;
  never preserve a cached allow decision.
- `404` intentionally hides a missing invitation/resource.
- `409` means a prerequisite, tenant boundary, terminal invitation,
  idempotency, or seat-cap conflict; refresh authoritative state rather than
  blind-retrying.
- Any timeout, malformed/unknown enum, unavailable policy dependency, or
  non-`200` capability response is non-entitled. Only safe reads receive bounded
  retries.

Capability responses are authorization metadata, not clinical content, but
their opaque actor/practice references remain confidential. They contain no
email, raw identity claims, patient data, transcript, note, bearer token, or raw
payment data. Invitation bearer tokens are sensitive and Core persists only a
digest; legal acceptance, credential review, invitations, membership state, and
seat mutations retain metadata-only audit evidence.

## Validation evidence and remaining gates

Current local evidence on exact code implementation parent
`27e480ee4898c985aa64d38578afa474c045b643`:

- full backend excluding only the host PostgreSQL patch-pin assertion: `1,761`
  passed, `35` skipped, `1` deselected in `43.77s`;
- focused delayed-execution suites: `31` passed, `3` skipped, plus `20`
  Zoom OAuth tests passed;
- PostgreSQL concurrency suites: `4` passed;
- frontend: `256` tests passed across `42` files;
- `just py_lint`, `just js_lint`, `just db_lint`, direct and configured ESLint
  for `PublicCheckoutStep.tsx`, production frontend build, OpenAPI regeneration,
  and `git diff --check` passed; and
- post-regeneration scans found no NPI in the public form, handwritten request,
  backend public request/persistence path, OpenAPI, or generated TypeScript
  client. V1, v2, and BAA hashes matched the values above.

The workstation runs PostgreSQL `16.15`; source pins `16.9`, which is the sole
deselected host-version assertion. As of 2026-08-28, PR #199's Backend,
Frontend, and change-budget checks are green; CI Backend includes `db_lint` and
the full `py_test` run. Independent final-SHA review remains required before
consumer handoff.

No live Clerk browser journey, production deployment, Azure mutation, cohort
activation, credential approval, or real-PHI exercise is claimed. The approved
BAA v2.2 bytes are unchanged; its Exhibit A phrase “claims preparation and
submission support” remains a counsel-resolution promotion blocker and is not
reinterpreted as CAB claim-submission authority.

## Exact next action

Complete independent review of the externally attested final PR #199 SHA that
contains this packet. Then have Priority 1 restack its
Checkout/webhook/reconciliation work onto that exact receiver tip, replace any
private binding or seat-count predicate with the canonical helpers, validate v2 digest
`a51843ed0810a2b43fd16cf6e66eb2830717c8381aa7fea3f8d05b9e55cb1bad`, and rerun
the integrated subscription and `22 → 21 → 20` reconciliation matrix.

Production readiness remains blocked on that ordered restack; final PR checks
and reviews; a live Clerk test-tenant journey; credential reviewer, method, and
SLA decisions; pending-credential-at-trial-expiry policy; counsel disposition of
the BAA phrase; and an explicit founder-controlled activation decision.
