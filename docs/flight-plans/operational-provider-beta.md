# Operational provider beta — Core engineering sequence

Started 2026-09-23 from protected master `ddfe047c`. Tier 2: provider authority,
note sources, additive patient-data structures, and a paired Sovereign rollout.
GitHub Actions is disabled; run tests, scans, and builds locally. Administrative
and compliance work is outside this sequence. Preserve existing access controls.

## Increment 1: clinician-reviewed note sources

Add explicit revision-bound review through the existing authenticated note UI.
The signed-in doctor must own the note, appointment, and patient; view-as and
stale browser bindings cannot write. A stale displayed digest cannot be reviewed.
The immutable review snapshot is an input-eligibility record, not an EHR signature.
Generated notes do not become eligible automatically. Source reads independently
recheck scope, current content, and supersession; cap selection at five notes in
the previous 548 days and 18,000 characters per source.

Expand rung: generated revision `443f26a5dfe7` adds one table, four indexes, foreign
keys, uniqueness/check constraints, and an UPDATE rejection trigger. No existing
data is rewritten. Rehearse on isolated PostgreSQL 16 using synthetic records.
Rollback the application and leave this additive structure dormant. Deleting
review snapshots is a separate retention operation, not immediate rollback.

The cohort requires both `SESSION_PREP_PROVIDER_ENABLED=true` and an exact
`SESSION_PREP_PROVIDER_CLERK_SUBJECTS` allowlist. Defaults expose no review action.
This increment does not yet deliver a real-record prepared brief.

## Remaining increments and release order

1. Bind the existing authority kernel to current provider/practice/record scope;
   issue server-only context handles, rechecking authorization on every read.
2. Store immutable briefs and source lineage in PostgreSQL, generate through the
   existing worker, and persist idempotent tool-read receipts. Recheck source
   revocation before replay or serving a previously generated brief.
3. Deploy the compatible receiver before the Sovereign sender, after exact
   source/image/config/schema reconciliation and local acceptance checks.
4. Prove failure recovery and rollback; release Session Prep and bounded clinician
   assistance only. Labs and referral-directory results remain out of beta.

## Evidence recorded so far

- PostgreSQL review/source/API/authority and migration graph tests: 47 passed.
- Focused frontend component tests: six passed; TypeScript passes.
- Local frontend build with inert test settings: passes. Existing sourcemap,
  CSS-minification, and chunk-import warnings remain visible in build output.
- No cloud mutation, live migration, or deployment performed for this increment.
- Independent review and final source-bound release evidence remain required.
