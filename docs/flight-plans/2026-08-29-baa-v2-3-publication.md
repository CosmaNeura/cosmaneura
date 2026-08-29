# Flight plan: publish BAA v2.3 without claim-submission scope

## Outcome and boundaries

- Problem and measurable outcome: publish the counsel-approved
  `CN-BAA-CUST-CW v2.3`, issued and approved 2026-08-29, with Exhibit A item
  (b) narrowed from v2.2 by removing only “claims preparation and submission
  support,” and retaining every other substantive term.
- In scope: the immutable Core v2.3 HTML asset and digest, the additive
  `baa_version` record, current-version clickwrap enforcement, onboarding
  version text and versioned PDF link, exact document-identity binding on the
  acceptance request, focused tests, and coordinated public HTML/PDF
  publication in `CosmaNeura/cosmaneura-web`.
- Explicit non-goals: changing or deleting v2.2 or any historical acceptance;
  changing negotiated organization BAAs; adding claim preparation,
  submission, 837P, clearinghouse, EHR-write, billing, or other product
  capability; enabling public acquisition; or authorizing a deployment.
- Affected users, services, environments, and data classes: self-service and
  other clickwrap practices must accept v2.3 before PHI-bearing product access
  resumes. Practices covered by an effective negotiated BAA are unchanged.
  Core and the public website are affected when separately promoted. Source
  and synthetic verification contain no PHI.

## Risk and dependencies

- Tier and reason: **Tier 2** because this changes a legally operative
  agreement, a PHI-access prerequisite, and a version-bound contract across
  two repositories.
- Irreversible or high-blast-radius decisions: every v2.2 asset, version row,
  and acceptance remains immutable. A v2.3 acceptance is also immutable and
  makes rollback to v2.2 legally unsafe without new counsel direction.
- Dependency-ordered PR stack; each PR can be held safely:
  1. In `CosmaNeura/cosmaneura-web`, branch
     `legal/baa-v2-3-assets-20260829` publishes immutable versioned v2.2 and
     v2.3 assets while the canonical aliases remain on v2.2. Hold after merge
     until `/baa-v2-3.pdf` is live and its digest and rendered content pass.
  2. In Core, branch
     `legal/baa-v2-3-remove-claims-submission-20260829` adds v2.3, makes it
     current, links onboarding directly to `/baa-v2-3.pdf`, and requires
     clickwrap practices with only v2.2 to re-accept. Hold until the web
     receiver is verified and the complete Core deployment delta is approved.
  3. In `CosmaNeura/cosmaneura-web`, branch
     `legal/baa-v2-3-public-activation-20260829` moves `/baa` and `/baa.pdf` to
     v2.3. Hold until the Core v2.3 response and synthetic acceptance record
     are verified.
- Cross-repo contract/version and receiver-before-sender order: the immutable
  versioned PDF is the receiver. Core sends users to that exact v2.3 URL and
  must not activate before it is live. Canonical public aliases activate last.

## Current deployed state

- Evidence observed during the 2026-08-29 readiness review: Core production
  runs source `ad25836b9707e9bc8986422686df516386fee6e5` from image digest
  `sha256:83aded8b4a96a9f9c6bf8b9dc82b60db568481cfa67972bebe7362727b3fec15`.
  Core `master` and this branch base are
  `8f409f06137a33158f6c6f64f3df6eba9d9e51af`; no corresponding ACR tag was
  observed. Production currently serves and records BAA v2.2.
- Difference from `master` and deployed-only behavior: production is behind
  `master`; a Core promotion would carry that complete reviewed delta, not
  only this BAA change. The production workflow also requires a qualifying
  current-head Terraform Apply and provider-callback attestation.
- Reconciliation decision: **pending**. Do not blindly replace the live image.
  Record the full live-to-`master` delta and obtain the normal founder
  promotion decision before Core activation.
- Previous known-good artifact and data-recovery boundary: the production
  source/image digest above plus the immutable v2.2 asset digest
  `13448bf42105db05e36e566f2f8ea0190926223e9dbb03efb8778cf8cfc7332e`.

## Feature flag / release control

- No feature flag applies. Legal-version selection and the PHI access gate
  must fail closed and must not be bypassable by a user-visible flag.
- Release control is the three-step, founder-gated sequence above. Public
  acquisition remains off until its independent launch gates are satisfied.
- Removal PR/condition: N/A; no temporary flag is introduced.

## Schema/data sequence

| Change                               | Rung                                                        | PR/branch                                               | Invariant                                                                                                                                                                                                                                                               | Recovery                                                                                                                                                                                                                  |
| ------------------------------------ | ----------------------------------------------------------- | ------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Add v2.3 and select it for clickwrap | Additive legal-version publication plus current-version cut | Core `legal/baa-v2-3-remove-claims-submission-20260829` | v2.2 asset/row/acceptances remain unchanged; v2.3 is the sole current version; the API accepts only the exact current doc ID, version, and digest presented by the browser and returns 409 for any mismatch; negotiated BAAs retain their existing effective-date rules | Before any v2.3 acceptance, the guarded downgrade may delete only the unused v2.3 row and restore v2.2 current. After any v2.3 acceptance, stop onboarding, retain all evidence, and roll forward with counsel direction. |

- PHI-safe rehearsal: use only a synthetic practice and clinician in staging.
  Verify an existing v2.2 clickwrap becomes not-in-force, access fails closed,
  v2.3 can be read and accepted, both acceptance records remain, and access is
  restored. No real customer or patient data is permitted.

## Rollout and promotion

- Ordered environments/cohorts and hold points:
  1. Review, merge, and promote the web receiver; verify the immutable v2.3
     PDF before advancing.
  2. Complete Core CI and exact-head independent review. Reconcile the full
     production drift and satisfy the existing Terraform/callback gates.
  3. Stop new clickwrap onboarding for the Core production revision change as
     an additional operational guard. The API independently rejects an open
     tab unless its submitted doc ID, version, and digest exactly match the
     current BAA. Promote through the founder-only workflow, wait for the prior
     revision to drain, and then reopen onboarding only after the canary passes.
  4. Review, merge, and promote the web activation branch; verify canonical
     `/baa` and `/baa.pdf` only after Core v2.3 is healthy.
- Health signals and measurable abort thresholds: abort on any digest mismatch,
  missing asset, non-200 legal endpoint, multiple/no current BAA rows, v2.2
  being reported as accepted for a clickwrap practice, v2.3 acceptance failing
  to preserve v2.2 evidence, frontend/backend version mismatch, or any
  unexpected authorization error.
- Canary journey: with synthetic data only, fetch `/internal/v1/baa/current`
  and require v2.3 plus the pinned digest; confirm the removed phrase is
  absent; confirm a v2.2-only owner is denied; re-accept v2.3; verify separate
  v2.2 and v2.3 acceptance records and restored access; download the exact
  versioned PDF; then verify both canonical public aliases after web activation.
- Founder-only manual production/PHI promotion record: required. This plan and
  source changes grant no deployment, migration, or customer-data authority.

## Security and recovery

- Counsel approval: the founder confirmed on 2026-08-29 that counsel approved
  removal of only the quoted claim-submission phrase. No security exception is
  created.
- Recovery: before any v2.3 acceptance, restore the preceding Core image and
  use the guarded migration downgrade to make v2.2 current. After any v2.3
  acceptance, do not downgrade, delete, rewrite, or relabel either agreement or
  acceptance; stop new onboarding, retain the additive data, and roll forward
  under counsel direction. Web recovery reactivates the recorded prior
  Cloudflare deployment without deleting immutable versioned assets.
- Observation window before irreversible cleanup: no cleanup is planned.
  Retain all legal assets and acceptance records indefinitely under the
  existing policy.

## Completion

- Evidence required: v2.2 and v2.3 asset digests; focused Core and web tests;
  migration graph/lint; rendered PDF review; exact-head independent review for
  all three branches; live versioned-PDF verification before Core; synthetic
  stale-version denial and v2.3 acceptance; and final canonical-route checks.
- Follow-up cleanup PRs and owners: none for historical legal assets. The
  founder owns promotion sequencing, drift disposition, and the production
  change record.
- Documentation and contract records: this flight plan, Core model constants,
  migration, UI label/link, asset hash tests, and the web legal-asset manifest,
  promotion runbook, and canonical route tests.
