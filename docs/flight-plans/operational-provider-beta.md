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

## Increment 2: record and practice authority

Verified Clerk token exchange can bind an allowlisted doctor to the actual
practice membership, owned appointment, and owned patient. The record binding
retains those exact identities. Every later resolution checks current access,
membership, identity, tenant, and record ownership; changing the patient or
practice does not retarget an existing token. An opaque Session Prep handle is
returned only through the authenticated Core-to-Sovereign response header. The
public five-field mount schema is unchanged.

Generated expand revision `c80db51e6a75` adds two immutable mapping tables and
three indexes, with UPDATE/DELETE rejection triggers. Apply before the receiver
code; rollback leaves these structures dormant. This increment alone does not
activate the prepared-brief worker or real-record tool response.

Local validation: 28 PostgreSQL authority/migration tests, two actual independent-
connection concurrency tests, and 131 isolated authority-kernel tests passed.
One pre-existing optional PostgreSQL migration test was skipped because its
separate historical-revision database was not configured.

## Increment 3: durable preparation and tool reads

Generated expand revision `de9260834611` adds immutable prepared artifacts,
recoverable build leases, and durable idempotent read receipts. Source text goes
to Core's existing Azure model client from the worker only; Redis carries an
argument-free periodic task. Calls are bounded to 45 seconds and 3,500 output
tokens, with at most three attempts before a five-minute cooldown. An expired
worker cannot publish, and current authority and reviewed sources are checked
again after generation and before every read/replay. Changed sources suppress
the old brief immediately. Read receipts and metadata-only authoritative audit
events commit before HTTP success. Receipts have a 24-hour replay window.

Apply the additive schema before starting the receiver/worker version. Turning
off `SESSION_PREP_PROVIDER_ENABLED` stops new work and closes the real-record
read path; application rollback leaves the three structures dormant. No live
migration or activation has occurred. Expired-receipt cleanup and queue-health
instrumentation are the next operations increment.

Local validation includes 17 worker/generator/real-endpoint tests, two actual
independent-connection worker/idempotency races, and 131 authority-kernel tests
with one existing optional migration test skipped. Model tests use local fakes.
The final combined source/authority/worker/generator/endpoint/migration run passed
63 tests. Ruff and focused Python type checks pass. Independent source-bound
review and deployed acceptance remain release gates.

## Earlier evidence

- PostgreSQL review/source/API/authority and migration graph tests: 47 passed.
- Focused frontend component tests: six passed; TypeScript passes.
- Local frontend build with inert test settings: passes. Existing sourcemap,
  CSS-minification, and chunk-import warnings remain visible in build output.
- No cloud mutation, live migration, or deployment performed for this increment.
- Independent review and final source-bound release evidence remain required.
- Runtime dependency audit found 14 affected Core registry packages; remediation
  and a repeat scan remain engineering release work. The pinned activemodel Git
  dependency is outside registry advisory coverage. Sovereign's separate runtime
  dependency scan had zero findings.
