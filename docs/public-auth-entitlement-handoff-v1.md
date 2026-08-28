# Priority 3 public onboarding and entitlement handoff v1

Status: reviewed receiver implementation published as the ordered stack below.
Production activation remains prohibited pending the explicit gates in this
packet and the ordered Priority 1 billing restack.

## Release identity and consumers

| Field                                      | Value                                                                                                                                                                              |
| ------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Capability                                 | Core-owned public practice onboarding, active-clinician seat policy, and server-authoritative access decisions for `soap_note`, `intake_prep`, `session_prep`, `scribe`, and `cab` |
| Canonical repository                       | Core (`CosmaNeura/cosmaneura`)                                                                                                                                                     |
| Canonical contract                         | `contracts/public-entitlements/v1/capability-entitlement.schema.json`                                                                                                              |
| Contract fixture                           | `contracts/public-entitlements/v1/synthetic-credential-pending.json`                                                                                                               |
| Contract version                           | `public-capability-entitlements.v1`                                                                                                                                                |
| Policy revision                            | `2026-08-27`                                                                                                                                                                       |
| Governing architecture                     | ADRs 0022–0024 in [architecture PR #10](https://github.com/CosmaNeura/cosmaneura-architecture/pull/10), source commit `5451df63e2fbeaf386ca5517d762e6d1359b053c`                     |
| Onboarding-state version                   | `public-onboarding.v1`, defined by `PublicOnboardingState` in `app/routes/public_onboarding.py` and published in Core OpenAPI                                                      |
| Contract freeze commit                     | `3bde3ae155423947d870f636ee907135949fafcb`                                                                                                                                         |
| Contract SHA-256                           | `a33ba095ea284575fc8004ecc7b2e05b6aa60c911f5c03add3a0ea1f7ef9e578`                                                                                                                 |
| Receiver branch                            | `codex/public-onboarding-10-receiver-handoff-20260827`                                                                                                                             |
| Reviewed implementation commit             | `be05f28aec5630887d002ef2cd9fe680faee2382`                                                                                                                                         |
| Receiver PR stack                          | [#185](https://github.com/CosmaNeura/cosmaneura/pull/185), [#188–#197](https://github.com/CosmaNeura/cosmaneura/pull/188)                                                       |
| Billing consumer                           | Priority 1 branch `codex/public-paid-pilot-stripe-restacked-20260827`; its final SHA/PR remains pending the ordered restack and stacked reconciliation matrix                      |
| Other consumers                            | Tasks 5–10: provider home, SOAP, Intake Prep, Session Prep, Scribe, and CAB; each must pin the final reviewed receiver SHA before implementation or activation                     |

The `$89` price, card collection, and fixed 14-day Checkout clock are billing
configuration owned by Priority 1 and are intentionally not duplicated in this
wire schema. Core's contract only reports the verified subscription projection
and the resulting access decision.

### Ordered receiver stack

| Order | PR | Head commit | Base |
| ----- | -- | ----------- | ---- |
| 1 | [#185](https://github.com/CosmaNeura/cosmaneura/pull/185) contract freeze | `3bde3ae155423947d870f636ee907135949fafcb` | `master` / `711682eb28180c2421689d589b5a74919c454644` |
| 2 | [#188](https://github.com/CosmaNeura/cosmaneura/pull/188) evidence schema | `b781eb269f3a0ad150a7bbef0113ca51b0bb172d` | `codex/public-auth-onboarding-20260827` |
| 3 | [#189](https://github.com/CosmaNeura/cosmaneura/pull/189) capability policy | `fa9a15bd0ae2265889e5e409e0abd692db9272df` | `codex/public-onboarding-evidence-20260827` |
| 4 | [#190](https://github.com/CosmaNeura/cosmaneura/pull/190) atomic seats | `f00234f2fe4d1735f4e719176a3a226c86ca5d17` | `codex/public-onboarding-02-capability-policy-20260827` |
| 5 | [#191](https://github.com/CosmaNeura/cosmaneura/pull/191) web auth boundary | `e0238a45f1ec228ce633da21f6f05edcbfea6d5c` | `codex/public-onboarding-03-atomic-seats-20260827` |
| 6 | [#192](https://github.com/CosmaNeura/cosmaneura/pull/192) dormant routes | `45b0408304812659c08a42a89fce105336a047c0` | `codex/public-onboarding-04-web-auth-boundary-20260827` |
| 7 | [#193](https://github.com/CosmaNeura/cosmaneura/pull/193) NPI-free API | `f890927ebb86bd8dcfd764b0cce3f131e7f5fd67` | `codex/public-onboarding-05-public-route-core-20260827` |
| 8 | [#194](https://github.com/CosmaNeura/cosmaneura/pull/194) public UI | `bd481f32a66c70ddc85dd30e9b76f01793acb5a4` | `codex/public-onboarding-06-npi-free-api-20260827` |
| 9 | [#195](https://github.com/CosmaNeura/cosmaneura/pull/195) browser proof | `8b89b0a2fc1649d473861c39ec46620b8756e498` | `codex/public-onboarding-07-public-ui-20260827` |
| 10 | [#196](https://github.com/CosmaNeura/cosmaneura/pull/196) fail-closed activation | `be05f28aec5630887d002ef2cd9fe680faee2382` | `codex/public-onboarding-08-web-proof-20260827` |
| 11 | [#197](https://github.com/CosmaNeura/cosmaneura/pull/197) handoff record | this branch | `codex/public-onboarding-09-activation-20260827` |

The implementation commit above is the exact code receiver for downstream
restacking. The documentation-only top commit is recorded in PR #197 after its
final review.

## Actor, tenant, PHI, and authority

- **Actor and tenant context:** an authenticated, normalized Clerk principal is
  resolved server-side to one persisted Doctor, one PracticeMember, and one
  Practice. `GET /internal/v1/capabilities` accepts no actor, practice, role, or
  capability selector. View-as sessions, stale/unverified email bindings, absent
  memberships, and managed practices at this public endpoint fail closed.
- **Authority:** `capabilities.<key>.decision` is the grant. Clients must not
  derive access from email/domain, channel, frontend flags, Checkout redirects,
  `subscription_state`, `data_mode`, or seat counts. Protected routes and jobs
  enforce the same server-side policy.
- **PHI classification:** the successful response is non-PHI authorization
  metadata with opaque actor/practice references. It contains no email, identity
  claim, patient identifier, clinical content, bearer token, transcript, note,
  or payment data. Treat the identifiers and policy state as confidential.
  Invitation bearer tokens are sensitive request data; Core stores only their
  SHA-256 digest and must not log them. The normalized invitee email is retained
  as non-PHI account metadata and disclosed only through owner-authorized team
  management responses so an owner can identify the invitation being revoked.

## Request and response contract

Requests:

```http
GET /internal/v1/public-onboarding
GET /internal/v1/capabilities
Authorization: <normal application session>
```

Neither read has a request body or idempotency key. The onboarding response is
the resumable server state: completed/current step, managed-path marker,
practice/role, exact legal gates, Checkout-started marker, subscription and
trial projection, credential gate, and effective PHI-access result. The public
order is `practice → baa → legal → checkout → credential → complete`;
`checkout_started` is display evidence, not entitlement or step completion.

A `200` capability response must validate against the canonical JSON Schema. It
contains:

- exact `contract_version`, `policy_revision`, and generation timestamp;
- server-resolved actor, practice, role, and membership state;
- verified email, BAA, current Terms/Privacy, subscription, and credential gate
  facts;
- authoritative active-seat count, the constant maximum `20`, pending-invite
  count, and Stripe reconciliation obligation; and
- exactly five capability decisions with `decision`, `data_mode`, `rollout`,
  `operation_mode`, and stable reason codes.

Operation modes are frozen in v1: Session Prep is `read_only`, Scribe is
`web_pwa_first`, and CAB is `advisory_only`. The latter grants neither an EHR
write, 837P generation/submission, clearinghouse enrollment/submission, nor any
other claim submission. A pending SOAP response may describe
`synthetic_only` while its protected-route `decision` remains `denied`; this is
not permission to process real PHI.

The approved `CN-BAA-CUST-CW` v2.2 HTML/PDF remains byte-pinned and is not
modified or reinterpreted here. Exhibit A's phrase “claims preparation and
submission support” requires written counsel disposition against the narrower
advisory-only CAB policy before promotion; it does not authorize implementation
of claim submission in this receiver.

The `intake_prep` key authorizes access only; it does not duplicate Intake's
content schema. Task 6 must retain exactly five versioned questions and permit
wording/order changes only by an authorized practice user. A changed question
count, unversioned edit, or patient-side configuration mutation is incompatible
with this handoff and must fail closed.

### Subscription compatibility

- A hosted Checkout Session or trial timestamp alone never entitles.
- Only a verified `active` projection, or `trialing` with a present future
  `trial_ends_at`, satisfies the paid-path subscription gate, and either state
  must be bound to trimmed, non-empty Stripe customer and subscription
  identifiers beginning with `cus_` and `sub_`, plus a positive authoritative
  Stripe state version.
- A stored `active` or `trialing` value with no valid subscription binding is
  reported on the public wire as `invalid` and denies access. This keeps the
  settings read model, onboarding state, and capability resolver consistent.
- `pending_verification`, `invalid`, unknown/malformed, incomplete, expired,
  past-due, unpaid, paused, and canceled states deny. Unknown stored values are
  reported publicly as `invalid`.
- An explicitly authorized billing exemption can satisfy the internal gate;
  therefore consumers still use the capability decision rather than re-create a
  rule from `subscription_state`.
- The unresolved pending-credential-at-trial-expiry policy is not encoded as an
  entitlement and must not be inferred by consumers.

## Idempotency and mutation behavior

| Mutation                           | Stable retry identity and behavior                                                                                                                                                         |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Exact legal acceptance             | Unique practice + document kind + version + content SHA-256; an exact owner retry creates no duplicate                                                                                     |
| Create invitation                  | Required `Idempotency-Key`, scoped and HMAC-digested per practice; exact payload replay returns the same logical invitation and recoverable derived token, conflicting reuse returns `409` |
| Accept invitation                  | Single-use token digest; exact accepted replay returns the current membership and cannot reactivate a later-deactivated seat                                                               |
| Revoke invitation                  | Invitation resource identity; repeated terminal mutation cannot change retained evidence                                                                                                   |
| Deactivate/reactivate member       | Member resource plus requested state; same-state retry is a no-op and does not increment Stripe reconciliation version                                                                     |
| Legacy join / managed roster claim | Unique Doctor membership plus locked practice/roster evidence; legitimate replay returns the existing authorization and conflicting tenant membership fails                                |

Browser retries must reuse the same invitation idempotency key. Before the
request is sent, the owner UI durably records the normalized request and key in
a canonical-practice-scoped browser journal, with a Web Locks critical section
for cross-tab coordination. It retains that journal across response loss and
reload, and clears it only after the link is copied, the owner confirms it was
saved, or the owner explicitly discards the request. A conflict is not transient
and must not be retried with a new key to bypass policy. The journal never stores
the invitation bearer.

Invitation and member list responses include the normalized account email only
for the authenticated practice owner. The UI masks these values from session
replay and analytics capture. Bearer tokens appear only on the create response,
are carried in the URL fragment rather than the request URL, and persist only in
tab-scoped session storage until acceptance or explicit discard.

## Canonical 20-seat policy

An active clinician seat is a persisted PracticeMember with
`seat_state = active` and no `access_revoked_at`; the owner counts. Pending,
expired, or revoked invitations do not count. Inactive or access-revoked
memberships do not count. Core's database is authoritative and Stripe quantity
is an asynchronous mirror.

The only supported in-process count API for billing and membership callers is:

```python
from app.services.public_entitlements import (
    MAX_ACTIVE_CLINICIAN_SEATS,
    active_clinician_seat_count,
)
```

`MAX_ACTIVE_CLINICIAN_SEATS == 20`. Callers must not independently count rows.
Every activation path must, in one database transaction:

1. call `lock_practice_for_active_seat_mutation(session, practice_id)` first;
2. lock and revalidate its invitation, member, Doctor, or roster evidence;
3. call `require_active_clinician_seat_capacity(...)` immediately before a new
   activation, or `activate_existing_clinician_seat(...)` for reactivation;
4. mutate the membership; and
5. call `record_active_clinician_seat_quantity(...)` before commit.

This pattern is applied to public invitation acceptance, owner reactivation,
the remaining legacy join mutation, and managed-roster claim/reactivation. The
21st activation returns `409` before commit. The membership change and, when a
Stripe subscription exists, `stripe_quantity_sync_required`, exact target, and
monotonic sync version commit or roll back together. The billing reconciler
consumes that durable target/version; it must never let remote Stripe state
create or revive a Core membership.

Legacy migrations can expose a practice that already has more than 20 active
members. A serialized deactivation may monotonically reduce `22 → 21 → 20`:
while the authoritative count remains above 20, Core commits the reduction but
retains `stripe_quantity_sync_required = true`, publishes no invalid target,
increments the evidence version, and records the over-cap error. At 20 it
publishes target `20`. Priority 1 owns the reconciler side of this seam and must
be stacked on this receiver: it must reject every provider quantity outside
`1..20`, preserve the no-target evidence above 20, perform no Stripe write, and
prove this exact recovery sequence before the pair is merge-ready.

Every counted-seat removal follows the same transaction rule, including managed
roster revocation and provider account closure. If closure leaves zero active
seats, Core retains `stripe_quantity_sync_required = true`, a null target, a
monotonic evidence version, and an explicit cancellation-required error; it
never asks Stripe to accept quantity `0`. Priority 1 must keep that no-target
state fail-closed and route the subscription through the separately authorized
cancellation workflow. This receiver must not be promoted alone: the billing
implementation on its base still recomputes a quantity for no-target markers.
Promotion requires Priority 1 restacked on the exact reviewed receiver SHA and
an integrated proof that neither an over-cap count nor zero produces a Stripe
quantity write.

## Credential fail-closed boundary

The v1 states are exactly `pending`, `approved`, `rejected`, and
`needs_review`. Every new membership is `pending`. `pending`, `rejected`, and
`needs_review` deny real-PHI capabilities; only a future independently evidenced
`approved` state may satisfy this gate.

No public credential mutation endpoint exists. Database constraints and
triggers reject pre-approved inserts, unaudited transitions, self-review, and
credential evidence that is incomplete. Because the verification method,
authorized reviewer, and SLA are still undecided, the current database trigger
also rejects every attempted CredentialReviewAudit insertion, including one
from a different Doctor, with `credential reviewer authorization is not
configured`. This deliberate hard stop prevents any new approval until a
reviewed, additive policy migration configures the real reviewer authority; do
not bypass or weaken it in an API.

### NPI collection and NPPES boundary

Owner decision, 2026-08-27: public signup, practice onboarding, legal
acceptance, Checkout, trial activation, seat management, credential state, and
entitlement must work with NPI absent. NPI is public registry data, not
authentication, licensure or credential proof, owner authority, tenant
membership, or an access-control signal. A valid NPI must never approve a
credential state or unlock a real-PHI capability.

The [CMS NPPES Read API](https://npiregistry.cms.hhs.gov/api-page) is therefore
not part of this onboarding or credential receiver. The public form, request
schema, persistence path, capability schema, Clerk, Stripe, and analytics do
not collect or receive NPI. Existing nullable database storage remains solely
for legacy compatibility; a database constraint prevents it from being
populated on a public-onboarding practice.

The API investigation establishes only future technical feasibility: NPPES is a
read-only, provider-reported public registry with daily updates, and CMS warns
that issuance of an NPI does not validate licensure or credentialing. Any future
customer NPI collection must be just-in-time for a separately approved and
enabled payer-facing HIPAA-standard transaction or integration that demonstrably
requires the appropriate Type 1 and/or Type 2 identifier. That future decision
must define purpose, legal/contractual basis, validation, minimum data,
retention/deletion, audit, activation, and rollback before implementation.

No NPPES integration or automatic credential transition is implemented in this
receiver. The credential verification method, authorized reviewer,
authoritative evidence, and SLA remain outstanding; NPI alone is excluded from
that decision.

## Failure, retry, and audit

- `401`: missing or expired application session; reauthenticate, then repeat the
  read.
- `403`: direct-actor or live verified-email check failed; do not use a cached
  allow decision.
- `404`: invitation token/resource is absent or intentionally undisclosed.
- `409`: onboarding prerequisite, managed/public path mismatch, idempotency
  conflict, terminal invitation, cross-tenant membership, or seat cap; refresh
  authoritative state and do not blind-retry.
- Any dependency error, timeout, malformed/unknown enum, missing policy fact, or
  non-`200` capability response is non-entitled. Retry only safe reads with
  bounded backoff; never preserve a stale `allowed` decision.

Durable metadata-only evidence includes append-only exact legal acceptance,
credential-review audit structure, and these PracticeAccessAudit actions:
`public_invitation_issued`, `public_invitation_accepted`,
`public_invitation_revoked`, `clinician_seat_deactivated`, and
`clinician_seat_reactivated`. Managed claims retain
`organization_access_granted`. Audit payloads use internal references, reason,
and timestamps; they must not include invite tokens or clinical content.

## Compatibility, fixtures, and safe default

- Compatibility window: Core retains v1 for the entire paid-pilot window and
  until every named consumer has migrated. There is no calendar sunset yet.
  Additive response fields require tolerant consumers; removed fields, changed
  meanings, new required capability keys, or enum changes require a v2 schema,
  receiver-first deployment, dual-support window, and explicit consumer
  restacking.
- Consumers pin both the reviewed Core implementation SHA and the schema
  digest, validate the fixture in their CI, and treat unknown versions or values
  as denied. They never copy the policy into another repository.
- Synthetic fixture:
  `contracts/public-entitlements/v1/synthetic-credential-pending.json`.
  Contract and behavior coverage lives in
  `tests/contracts/public_entitlements_contract_test.py`,
  `tests/services/public_capabilities_test.py`,
  `tests/services/public_entitlements_test.py`,
  `tests/routes/public_onboarding_test.py`, and
  `tests/integration/active_clinician_seat_cap_test.py`. The route suite includes
  20 fresh-account persisted-boundary runs, and the PostgreSQL integration suite
  exercises concurrent twentieth-seat acceptance, cross-account same-domain
  exclusion, and same-account multi-tab idempotency. The live Clerk browser
  journey is implemented in `tests/integration/onboarding_test.py`; execution
  requires external Clerk test credentials and the local HTTPS harness.
- Safe default: legacy/managed practices stay on their existing path;
  `public_onboarding_required` selects the public evidence boundary and
  `public_pilot_cohort` is server-owned. SOAP rollout is enabled but remains
  subject to every gate; Intake Prep, Session Prep, Scribe, and CAB default
  `disabled`. Missing facts, missing rollout entries, unknown states, and policy
  dependency failures deny. No production or real-PHI activation is authorized
  by this packet.
- Public-to-managed conversion is not implicit. Organization configuration for
  a `public_onboarding_required` practice, including multi-practice setup, fails
  before any link or audit write. A separately designed and reviewed conversion
  transition must reconcile public legal, credential, billing, and membership
  evidence before that flag can change.

## Validation evidence

- Fresh post-regeneration non-integration backend suite: `1740` passed, `35`
  skipped, and the one local PostgreSQL version assertion was deliberately
  deselected because the workstation runs `16.15` while source pins `16.9`; no
  functional test failed.
- Database-only PostgreSQL integration suite: `7/7` passed, including competing
  twentieth-seat activations, invitation acceptance, same-domain and
  same-account practice creation, closure locking, and Stripe state races.
- Fresh frontend suite after regenerating OpenAPI and the ignored generated
  client: `255/255` passed. React Router type generation, TypeScript, ESLint,
  Prettier, and the production build passed. Build output contained only
  pre-existing sourcemap/CSS warnings and the expected absent Sentry upload
  token in local validation.
- Python Ruff formatting/lint and Pyright passed. Migration graph, public
  evidence downgrade guards, schema snapshot, route partitioning, and the 20
  persisted cold-start boundary cases are included in the green backend run.
- Static checks confirm the public form, handwritten request type, backend
  request schema/persistence, generated OpenAPI, and generated TypeScript client
  contain no NPI field. The frozen capability contract remains
  `a33ba095ea284575fc8004ecc7b2e05b6aa60c911f5c03add3a0ea1f7ef9e578`;
  BAA v2.2 remains byte-identical to `origin/master` at
  `13448bf42105db05e36e566f2f8ea0190926223e9dbb03efb8778cf8cfc7332e`.
- The Clerk browser journey remains source-implemented but was not executed
  against a live Clerk test tenant because external test credentials and the
  local HTTPS/localias harness were unavailable. It remains an activation gate,
  not an inferred pass.

## Activation and rollback

Activation order:

1. review the additive Core schema/migration and v1 receiver with product
   rollouts dark; do not merge it independently of the billing compatibility
   step below;
2. restack Priority 1 onto the exact replacement receiver SHA and verify
   Checkout/webhook projection plus the stacked `22 → 21 → 20` seat
   reconciliation against this contract;
3. restack provider-home and product consumers and run the shared fixture;
4. select and implement the credential reviewer/method/SLA policy in a separate
   reviewed migration and operator workflow;
5. complete synthetic, security, migration, and operational evidence; then
   separately approve cohort/production activation.

Rollback disables rollout/cohort controls first and stops new acquisition at
the billing kill switch. Consumers roll back before the Core v1 receiver. Keep
the additive tables, credential evidence, invitation history, legal acceptance,
and audit rows dormant; the migration intentionally refuses a populated
downgrade. Reconcile any durable Stripe quantity obligation rather than deleting
or editing membership evidence.

Subscription cancellation is handed off to the authenticated Stripe Billing
Portal; Core continues to consume the verified webhook projection. Account or
practice closure is intentionally not automated in this change because legal
retention and ownership-transfer policy are not approved. Until that policy is
decided, the bounded path is authenticated support escalation: lock access,
stop acquisition or cancel through the portal, preserve legal/audit records,
and do not hard-delete clinical or onboarding evidence.

## Unresolved decisions and downstream pins

- credential-verification method: **TBD**;
- authorized credential reviewer/authority source: **TBD**;
- credential-review SLA: **TBD**;
- pending-credential-at-trial-expiry conversion policy: **TBD** and owned with
  Priority 1/product approval;
- public-to-managed conversion transition and evidence mapping: **TBD**; current
  organization configuration refuses the transition;
- named production approvers and live Clerk/Stripe/Azure/legal gates: **TBD**;
- counsel disposition on BAA v2.2 Exhibit A's “claims preparation and submission
  support” language under the advisory-only CAB policy: **TBD** and a promotion
  blocker; the byte-pinned agreement is unchanged;
- final Priority 1 consumer SHA/PR and the exact SHAs for Tasks 5–10: pending
  receiver-first restacking.
