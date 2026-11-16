# Default owned SOAP template

Tier 2. Owner: John Clemente. This source change provides the patient-independent
four-section SOAP template used in the synthetic Scribe preparation, without
copying any visit, patient, audio, transcript or generated note into an account.
Its headings and conservative section guidance exactly match the manual SOAP
starting point. Guidance enters the structured note prompt; initialization never
rewrites an existing owned SOAP row, including an older blank-guidance template.

## Outcome and boundaries

An eligible account finishing onboarding receives one owned SOAP template and,
if it has no template or preference, that template becomes its default. Existing
eligible Scribe accounts initialize at activation or consent-gated capture
admission; ordinary Settings can initialize an older account explicitly. Template
catalogue and preference GETs remain reads. Existing custom SOAP, other templates,
explicit choices and saved notes are preserved.

The existing unique owner/name constraint and retained soft-deleted SOAP row are
the one-time receipt. Initialization locks the Doctor row and participates in the
caller's transaction. It never recreates a deleted SOAP template. Deletion is
owner-scoped, idempotent soft deletion; effective default reads exclude it while
saved note and example references remain valid. Users may create other templates
or upload their own examples. The native catalogue's existing 100-template limit
is preserved without blocking capture or removing custom templates.

No authentication, consent, billing, entitlement, membership, cancellation,
authority, account-deletion or capture-cohort decision is broadened. Onboarding,
activation and capture keep their existing gates. New mutation routes inherit
the internal API's centralized read-only view-as guard. The new UI controls hide
during view-as. No migration, dependency, service, production action or live
backfill is part of this PR.

The two template mutations use per-request captured SDK session tokens and the
existing guarded internal fetch. Identity/context is rechecked after token and
request-binding awaits, at network dispatch, and before response binding/reload
effects and UI callbacks. Their local status/confirmation lifetime resets for a
changed SDK user/session, Doctor, selected schema or view-as context. A newly
current eligible owner still initializes automatically. SDK/Doctor context stays
ephemeral and is not added to the owner-free initializer body or legal records;
the generated client's global authentication and other API calls are unchanged.

## Sequence and acceptance

1. Validate on the disposable, explicitly owned synthetic PostgreSQL database:
   concurrent initialization; existing custom and explicit default retention;
   durable deletion; real saved AIVisitNote/example references; ownership and
   body-injection denial; transaction rollback; normal onboarding/native hooks;
   revoked activation, consent and view-as denial.
   Require guidance parity with the manual starter and exact inclusion in the
   real structured prompt, without an example visit or model-service call.
2. Generate the complete internal OpenAPI and locked frontend client; validate
   focused initialization/deletion and Settings behavior, lint and types. Record
   the final commit, commands/results and independent after-push review.
3. Merge normally after required checks and independent review. An existing
   iPhone binary needs no change for this server/browser behavior.
4. Prepare a distinct, immutable merged staging candidate and current controller
   qualification. Reconcile the current source/image/config/schema; do not replay
   old deployment windows or initialization journals. Obtain the exact required
   founder staging authorization before any deployment or account activation.
5. Confirm on the synthetic staging account that no template upload is needed to
   start and review a visit; then delete the default and confirm it stays deleted
   through sign-in, initialization and a later visit, with saved notes retained.
   This source-only validation does not claim physical-device or live acceptance.

## Recovery

Revert the hooks, initialization/deletion endpoints and new UI through reviewed
source to stop further automatic provisioning. Retain all owned template,
preference, tombstone, example and saved-note rows. Do not erase already-created
user data, resurrect a deleted default, alter cancellation or use a destructive
schema rollback. Existing catalogue, custom creation and upload paths remain.
