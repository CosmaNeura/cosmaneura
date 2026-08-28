# Public auth and entitlement receiver handoff v1

## Release identity and ownership

- **Capability:** public authentication, practice onboarding evidence,
  active-clinician seat semantics, and capability-entitlement contract.
- **Producer/owner:** Core Authentication workstream.
- **Receiver branch:** `codex/public-onboarding-evidence-20260827` (PR #188).
- **Consumer:** Stripe paid-pilot workstream, followed by provider home, SOAP,
  Intake Prep, Session Prep, Scribe, and CAB.
- **Contract:** `contracts/public-entitlements/v1/capability-entitlement.schema.json`,
  version `public-capability-entitlements.v1`.
- **Synthetic fixture:**
  `contracts/public-entitlements/v1/synthetic-authorized-preflight.json`.
- **Contract SHA-256:** `02f13fcf8226e6990389a085dfa3478ca4d7d4a8f9cc9f5ad3b3e676508fb865`.
- **Fixture SHA-256:** `9695b1a93b0c422467e46a93f587af2224b5b8c40b3c4951fc3bfc432e678530`.
- **Generated OpenAPI SHA-256:** `b4887500dbeba7f205a7daeef007438105a7ef77a5e813970a0cf68c71aaac98`.
- **Migration SHA-256:** `d0a4…` `14db2216f5afbd89676d6d596d13b4afd97f3897da28ea0f979ee79f17725895`; `e7a9…` `75d6efa8a4633b95b643e40367dbbca2ab29fd38e1ca09fe5069f83684dde62f`.
- **BAA v2.2 SHA-256:** `13448bf42105db05e36e566f2f8ea0190926223e9dbb03efb8778cf8cfc7332e` (unchanged).
- **Data classification:** the entitlement wire payload is PHI-free and uses
  opaque actor/practice references. Onboarding evidence retains normalized
  signer/invitee email as PII plus token digests, but no patient data, clinical
  content, NPI, raw bearer token, or raw payment data.

The exact commit and artifact digests are recorded in the immutable PR handoff
and independent review. A documentation file cannot safely pin its own commit.
Earlier credential-gated receiver SHAs and the former v1 digest are superseded.

## Authority contract

The server resolves actor and tenant from the verified session. Client-supplied
actor, email, practice, role, domain, acquisition provenance, rollout state,
subscription text, or seat count confers no authority. Public capability access
requires verified identity; active, non-revoked and authorized membership;
current Terms, Privacy and BAA acceptance; authoritative subscription
entitlement; tenant isolation; capability rollout; and feature-specific consent
at execution time. Owner authority is additionally required for owner-only
mutations.

A legacy pilot record is authorization-neutral except that a current record
whose immutable practice snapshot exactly matches the actor's active authorized
membership may satisfy the entitlement gate. It cannot replace any other gate.

Professional credential or licensure state is authorization-neutral and absent
from the wire contract and blocking UI. CosmaNeura does not claim to verify
licensure. NPI is absent from public signup, authentication, onboarding,
payment, entitlement, tenancy, and capability decisions. Nullable legacy NPI
storage remains compatibility-only and the database prohibits NPI on a
`public_self_service` acquisition.

The five capabilities are `soap_note`, `intake_prep`, `session_prep`, `scribe`,
and `cab`. Intake Prep uses exactly five versioned questions modifiable by an
authorized practice user. Session Prep is read-only. Scribe is web/PWA first.
CAB is advisory-only and cannot write an EHR, generate or submit 837P/claims,
contact a clearinghouse, enroll a payer, or contact a patient.

This boundary publishes schema and evidence only; it exposes no capabilities
HTTP endpoint yet. The later producer is an authenticated
`GET /internal/v1/capabilities` with no actor or tenant input. Reads have
idempotency `none`. Onboarding/membership mutations use `Idempotency-Key`; an
exact replay returns the same logical result and conflicting reuse returns
`409`. Named audit events are added atomically with their producers, not as
unused enum values in this boundary. Before activation, that producer must add
executable fixture and malformed-payload validation against its runtime model.

## Failure and retry semantics

- `401`: missing or expired application session; reauthenticate.
- `403`: an authority gate denied; never retain a cached allow decision.
- `404`: a sensitive invitation or resource remains undisclosed.
- `409`: prerequisite, tenant, terminal-state, idempotency, or seat conflict;
  refresh authoritative state before retrying.
- `503`, timeout, malformed response, unknown version/value, or unavailable
  policy dependency: deny and retry only safe reads with a bounded policy.

`pending_verification`, `invalid`, incomplete, expired, past-due, unpaid,
paused, canceled, malformed, and unknown subscription projections are not
entitled. Checkout Session creation alone is not entitlement. The fixed 14-day
trial begins at authoritative Checkout creation; the trial-expiry commercial
action remains unresolved and no default is encoded here.

## Migration/API compatibility and rollback

`d0a4c8e2f611` adds active/inactive seat state. `e7a9c3d5f102` then adds legal
and invitation evidence, `public_pilot_cohort`, and the authorization-neutral
`acquisition_source` discriminator. Existing practices are backfilled as
`managed`; the database default is removed in the same transaction. A
pre-receiver binary that omits the discriminator therefore fails closed rather
than creating an ambiguous or NPI-bearing practice.

Activation order is exact:

1. Keep acquisition and all public capability flags off; stop public practice
   creation and drain incompatible mutating API revisions.
2. Apply `d0a4c8e2f611`, then `e7a9c3d5f102`.
3. Deploy the reviewed receiver and verify every practice writer supplies an
   acquisition source; public self-service supplies `public_self_service` and
   cannot accept or persist NPI.
4. Maintain the established 24h05 incompatible-revision drain boundary, then
   verify no pre-receiver API or worker remains.
5. Deploy version-pinned consumers, regenerate OpenAPI/clients, and validate
   their exact contract digest. Only a separate founder decision may re-enable
   acquisition or activate a capability cohort.

Before any material state exists, an online downgrade is allowed only after all
receiver binaries are drained. Any inactive membership blocks downgrade below
`d0a4c8e2f611`. Any public-self-service practice, cohort membership, legal
acceptance, or invitation blocks downgrade below `e7a9c3d5f102`. After either
floor is crossed, recovery is: disable rollout, stop acquisition, retain the
additive schema/evidence, drain the incompatible revision, and roll forward.
Never reintroduce a database default or run a pre-fence mutating API.

## Flags and defaults

- `practice.public_pilot_cohort = false` is the database and application safe
  default.
- No capability runtime flag or endpoint is enabled by this boundary.
- Missing/unknown contract, policy, subscription, consent, tenant, or rollout
  state denies.
- Public acquisition remains operationally off until the ordered consumer
  stack and exact-head reviews are complete.

## Focused verification commands

```text
just py_lint
just db_lint
just db_migrate
uv run pytest tests/contracts/public_entitlements_contract_test.py tests/public_onboarding_evidence_guard_migration_test.py tests/services/doctor_access_test.py tests/routes/practice_test.py
just js_generate-openapi
git diff --exit-code -- web/openapi.json
cd web && pnpm exec eslint app/routes/onboarding.tsx app/routes/onboarding.organization-access.test.tsx app/lib/practice-api.ts
cd web && pnpm test --run app/routes/onboarding.organization-access.test.tsx
git diff --check
```

No merge, deployment, activation, live charge, real-PHI processing,
claim/clearinghouse/EHR action, or legal-text change is authorized by this
handoff. The byte-pinned BAA v2.2 remains unchanged; its Exhibit A wording is a
separate counsel-resolution promotion blocker.
