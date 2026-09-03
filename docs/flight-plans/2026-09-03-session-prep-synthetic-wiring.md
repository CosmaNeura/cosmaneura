# Flight plan: synthetic Session Prep sender wiring

## Outcome and boundaries

- Problem and measurable outcome: an eligible Core staging synthetic appointment
  can project one configured opaque `context_snapshot_ref`, and the same live
  provider/record binding can receive `session-prep:read`; all other records and
  the default configuration receive neither.
- In scope: one fail-closed server-side scope resolver, dynamic provider
  capability derivation, the additive Note Detail response field, browser-host
  context lifecycle, and synthetic integration tests.
- Explicit non-goals: real-provider or PHI use, production activation, staging
  mutation, Auth/IAM changes, environment or deployment changes, schema/data
  changes, model-provider calls, and operational-control reconciliation.
- Affected users, services, environments, and data classes: only an explicitly
  provisioned non-PHI synthetic preview provider and its fixed synthetic
  appointment in staging after a separate activation decision. Core is the
  sender; Sovereign is the receiver. Default-off behavior is unchanged.

## Risk and dependencies

- Tier and reason: **Tier 2** because this changes record-scoped auth/session
  capability derivation and is one half of a cross-repository contract.
- Irreversible or high-blast-radius decisions: none. The contract is additive,
  the static provider capability set is unchanged, and runtime exposure remains
  default-off.
- Dependency-ordered PR stack; explain how each PR can be held safely:
  1. Sovereign receiver main `92e6060` and its prepared-brief endpoint remain
     independently safe without a sender.
  2. Core base `d543e035d968d3dccc34cd03baaef90b41a4629c`, including the
     provider panel baseline and the merged synthetic-fixture/authority work,
     receives this dormant sender wiring.
  3. Core panel pin/revocation hardening corresponding to PRs #252 and #253 must
     be present in the exact promotion candidate before activation.
  4. A separate Auth/IAM and operational-control change must complete before
     any real-provider or PHI-bearing use is considered.
  Each rung can be held with `SESSION_PREP_SYNTHETIC_ENABLED=false`.
- Cross-repo contract/version and receiver-before-sender order: Core sends only
  the configured opaque `context_snapshot_ref`; Sovereign owns and validates
  the referenced immutable synthetic snapshot. Receiver `92e6060` precedes
  sender activation. No clinical content is projected by Core.

## Current deployed state

- Live SHA/digest/configuration/schema evidence by affected environment: not
  mutated or reconciled by this implementation branch. The implementation is
  based exactly on Core `d543e035d968d3dccc34cd03baaef90b41a4629c` and remains
  dark. The staging workflow currently requires the Session Prep synthetic
  feature to remain disabled.
- Difference from `master` and deployed-only behavior: this branch adds only
  dormant application/API/UI wiring and tests. It does not alter deployment or
  IAM files.
- Reconciliation decision: retain current staging behavior until the separate
  operational-control work is complete; then promote one reviewed merge commit
  whose full SHA is recorded and verified by the canonical staging workflow.
- Previous known-good artifact and data-recovery boundary: the current canonical
  staging artifact is unchanged. No data is written or migrated by this work.

## Feature flag / release control (reversible user-visible behavior only)

- Control name, stable key, owner, expiry, cohorts, and activation authority:
  `SESSION_PREP_SYNTHETIC_ENABLED`, paired with
  `SESSION_PREP_SYNTHETIC_CONTEXT_SNAPSHOT_REF`,
  `SESSION_PREP_SYNTHETIC_CLERK_SUBJECT`,
  `SESSION_PREP_SYNTHETIC_APPOINTMENT_ID`, and
  `SESSION_PREP_SYNTHETIC_FIXTURE_ID`; defaults are false/empty. The resolver
  remains incomplete unless all values are present and requires exact matches
  to the current authenticated subject, appointment, and provenance row.
  Activation is restricted to that single staging synthetic preview fixture
  after the founder's separate canonical-staging decision. No production or
  real-provider cohort is authorized.
- Removal PR/condition: remove the temporary synthetic sender only after a
  separately reviewed production-grade identity, consent, PHI, and operational
  design replaces it; otherwise leave it default-off.

## Schema/data sequence (schema/data work only)

N/A. This change adds no schema, migration, seed, or backfill. It reads the
existing `SyntheticPreviewAppointmentFixture` provenance and fixed rows.

- PHI-safe rehearsal runbook/evidence and result: focused tests use fabricated
  names, dates, identifiers, and content only. Cross-repository synthetic
  acceptance must run without real-provider credentials or PHI.

## Rollout and promotion

- Ordered environments/cohorts and hold points:
  1. Merge and verify the Sovereign receiver.
  2. Merge Core panel hardening and this default-off sender.
  3. Reconcile the exact Core merge SHA and Sovereign receiver SHA against the
     canonical staging candidate; stop on drift.
  4. Run the synthetic cross-repository matrix with the flag still constrained
     to the fixed preview fixture.
  5. Consider staging-only activation only after Auth/IAM and operational
     controls are independently complete.
- Health signals and measurable abort thresholds: abort on any context ref or
  `session-prep:read` exposure to a nonfixture record, owner mismatch, failed
  integrity check, non-staging environment, disabled/incomplete configuration,
  stale context after record switch, browser persistence, unbounded polling,
  or unexpected outbound/model call.
- Canary journey: open the exact synthetic note; verify eligibility, immutable
  snapshot resolution, bounded prepared brief, and typed response; switch to a
  second record and verify that browser A is detached and B receives no
  synthetic capability; disable or tamper with the fixture and verify immediate
  server-side capability removal; inspect URL, DOM, storage, logs, and network
  bodies for forbidden record content.
- Founder-only manual production/PHI promotion record, or current control gap:
  production and PHI use are explicitly blocked. A later exact staging SHA must
  use the founder-only canonical workflow; this plan grants no deployment
  authority.

## Security and recovery

- Open security acceptances, scope, expiry, and promotion impact: none accepted
  here. Real-provider/PHI use remains blocked pending separate Auth/IAM and
  operational-control completion. On the pinned Core base, browser cleanup does
  not revoke A's server token during an A-to-B switch; A remains bounded by its
  short token TTL and synthetic-only scope. PR #253's server-side revocation
  hardening is therefore a required pre-activation dependency, not an optional
  follow-up.
- Recovery move for behavior, image, infrastructure, schema/data, and data
  damage as applicable: keep or restore
  `SESSION_PREP_SYNTHETIC_ENABLED=false`; revert the additive Core sender commit
  if needed. No database, infrastructure, or data recovery is required.
- Observation window before irreversible cleanup: N/A; no irreversible cleanup.

## Completion

- Evidence required to call the outcome complete: focused Python and React
  suites pass; generated OpenAPI/client artifacts match; synthetic tests cover
  default-off, wrong environment/record/owner, tamper/deletion, capability
  revocation, record switching, stale context, and browser-data leakage; an
  independent agent reviews the final substantive diff.
- Follow-up cleanup PRs and owners: activation, exact-SHA staging promotion,
  Auth/IAM, and operational controls remain separate owner-approved work.
- Documentation and contract records to update: this flight plan, generated
  OpenAPI/client contract, and the eventual exact staging promotion record.
