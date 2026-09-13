# Flight plan: synthetic Session Prep sender wiring

## Outcome and boundaries

- Problem and measurable outcome: an eligible Core staging synthetic appointment
  can receive `session-prep:read` on its live provider/record-bound mount while
  generic Note Detail JSON and the browser host receive no Session Prep context
  handle; all other records and the default configuration receive no capability.
- In scope: one fail-closed server-side scope resolver, dynamic provider
  capability derivation, removal of the rejected Note Detail/browser handle
  path, appointment-keyed browser-host lifecycle, and synthetic integration
  tests.
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
- Irreversible or high-blast-radius decisions: none. The rejected, unpromoted
  Note Detail field is removed, the static provider capability set is unchanged,
  and runtime exposure remains default-off.
- Dependency-ordered PR stack; explain how each PR can be held safely:
  1. Sovereign receiver must derive its configured synthetic context handle
     server-side after resolving the record-bound mount and must accept a
     browser `/session-prep` body containing only the client turn ID.
  2. Reviewed Core base `bba1840c5da85e9fc781cf671899e16643e32fc8`,
     including the provider panel baseline, merged synthetic-fixture/authority
     work, and non-overlapping staging-inventory hardening, receives this dormant
     sender wiring.
  3. Core panel pin/revocation hardening corresponding to PRs #252 and #253 must
     be present in the exact promotion candidate before activation.
  4. A separate Auth/IAM and operational-control change must complete before
     any real-provider or PHI-bearing use is considered.
  Each rung can be held with `SESSION_PREP_SYNTHETIC_ENABLED=false`.
- Cross-repo contract/version and receiver-before-sender order: the browser
  receives only the server-filtered `session-prep:read` welcome path and sends
  no context handle. Sovereign derives its separately pinned synthetic handle
  server-side only after resolving the current mount; the handle crosses into
  Core only in the dedicated, workload-authenticated Session Prep POST body.
  The corrected Sovereign receiver and panel pin must precede sender activation.
  No clinical content is projected by Core Note Detail or the host bridge.

## Current deployed state

- Live SHA/digest/configuration/schema evidence by affected environment: not
  mutated or reconciled by this implementation branch. The implementation is
  based exactly on Core `d543e035d968d3dccc34cd03baaef90b41a4629c` and remains
  dark. The final reviewed branch is reconciled to exact Core base
  `bba1840c5da85e9fc781cf671899e16643e32fc8`. The staging workflow currently
  requires the Session Prep synthetic feature to remain disabled.
- Difference from `master` and deployed-only behavior: this branch adds only
  dormant server-side authority wiring and tests. It does not add a Note Detail
  field, expose the configured handle to browser code, or alter deployment or
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
  1. Merge and verify the corrected Sovereign receiver and browser panel, which
     derive the configured handle server-side and omit it from browser requests.
  2. Merge Core panel hardening and this default-off sender.
  3. Reconcile the exact Core merge SHA and Sovereign receiver SHA against the
     canonical staging candidate; stop on drift.
  4. Run the synthetic cross-repository matrix with the flag still constrained
     to the fixed preview fixture.
  5. Consider staging-only activation only after Auth/IAM and operational
     controls are independently complete.
- Health signals and measurable abort thresholds: abort on any context handle in
  Note Detail JSON, host context, URL, DOM, browser persistence, or browser POST;
  on `session-prep:read` exposure to a nonfixture record, owner mismatch, failed
  integrity check, non-staging environment, or disabled/incomplete
  configuration; or on stale context after record switch, unbounded polling, or
  unexpected outbound/model call.
- Canary journey: open the exact synthetic note; verify generic hydration has no
  context handle while the server-filtered welcome advertises Session Prep;
  verify immutable snapshot resolution, bounded prepared brief, and typed
  response; switch to a second record and verify that browser A is synchronously
  detached and B receives no synthetic capability; disable or tamper with the
  fixture and verify immediate server-side capability removal; inspect URL, DOM,
  storage, logs, browser requests, and generic Note Detail JSON for the handle
  and forbidden record content.
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
  default-off, wrong environment/record/owner, second valid fixture,
  tamper/deletion, capability revocation, record switching, synchronous teardown,
  absent generic JSON/host context, stale content, and browser-data leakage; an
  independent agent reviews the final substantive diff.
- Follow-up cleanup PRs and owners: activation, exact-SHA staging promotion,
  Auth/IAM, and operational controls remain separate owner-approved work.
- Documentation and contract records to update: this flight plan, generated
  OpenAPI/client contract, and the eventual exact staging promotion record.
