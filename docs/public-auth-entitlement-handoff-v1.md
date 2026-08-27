# Public auth and entitlement handoff v1

- **Capability:** canonical provider onboarding and capability entitlement.
- **Contract:** `contracts/public-entitlements/v1/capability-entitlement.schema.json`;
  version `public-capability-entitlements.v1`.
- **Receiver:** Core. The branch/commit and SHA-256 digest travel with the
  reviewed handoff because a file cannot safely identify its own commit.
- **Consumers / next tasks:** paid-pilot billing, provider home, SOAP, Intake
  Prep, Session Prep, Scribe, and CAB. Consumers must pin the reviewed Core SHA.
- **Actor and tenant:** the normalized Clerk principal resolves one persisted
  Doctor and one practice membership on the server. Client-supplied actor,
  email, domain, channel, practice, role, or capability assertions confer no
  authority.
- **Idempotency:** the read is side-effect free. Onboarding and membership writes
  use their durable resource key plus an `Idempotency-Key`; an exact replay
  returns the same logical result and conflicting reuse returns `409`.
- **PHI classification:** policy and opaque actor/practice references only; no
  patient data, clinical content, identity claims, email addresses, tokens, or
  raw payment data.
- **Request/response:** authenticated `GET /internal/v1/capabilities`; no actor
  or tenant input; response conforms to the canonical JSON Schema.
- **Failure/retry:** `401` missing/expired session, `403` denied identity or
  membership, `409` identity collision, and `503` unavailable policy dependency.
  Reads may retry; failures never preserve a stale allowed decision.
- **Audit:** legal acceptance, credential review, invitation, membership state,
  and seat mutation events are metadata-only. Read decisions are observable via
  aggregate reason-code metrics without actor identifiers.
- **Compatibility:** v1 remains additive for the paid pilot. Breaking changes
  require v2, receiver-first deployment, and explicit consumer restacking.
- **Flag / safe default:** unfinished capabilities are off. Missing or unknown
  state denies access. A synthetic sandbox is explicit and cannot reach PHI.
- **Synthetic fixtures:** `contracts/public-entitlements/v1/synthetic-credential-pending.json`.
- **Activation order:** Core schema/migration and receiver → billing quantity
  consumer → provider-home and product consumers → cohort activation.
- **Rollback:** turn rollout controls off, retain additive schema/evidence, and
  revert receiver code only after all consumers return to the prior contract.
- **Unresolved decisions:** credential-verification method, authorized reviewer,
  review SLA, and pending-credential-at-trial-expiry conversion policy. No v1
  field or default invents those answers.
