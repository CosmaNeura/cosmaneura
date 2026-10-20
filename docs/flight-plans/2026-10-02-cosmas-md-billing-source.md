# Cosmas MD billing source preparation

Tier 2; base 03689fe24115d37af4479171d3e6b7a982cf68e5. First bounded receiver-first Core outcome: save an attributed, immutable billing operation for an explicitly confirmed current note. The browser submits only identity-neutral comparison hashes, an operation UUID and confirmed visit context. CAB transport/header presentation follows separately; this slice reports not configured and makes no network/model call.

Authority: ratified architecture origin/main 6b879150205f275612d1a3fd2318530dfa331857, sections 2–4, ADR0023 and ADR0025. The new delegated CAB transport is not yet ratified or activated. Core owns identity, current practice, source and completion. Existing Session Prep/Sovereign, clinical note contents, provider access exceptions and deployment controls are unchanged.

Default off. A private, closed server policy admits only exact source scope/content hashes classified synthetic or deidentified. No browser policy/classification flags, practice-wide synthetic inference, credential verification or invented clinical defaults. Empty policy exposes no per-note button. Explicit source refresh remains honest about unavailable configuration.

Projection is versioned ordered-note Markdown. Preserve original ordered-section digest and projected-text digest; omit only the exact standalone legacy recording-duration-generated paragraph, record the exclusion, and never infer clinician time. No transcript is copied. Completion attests the projected source for coding, not an upstream signature. All clinical context is explicitly confirmed.

Write boundary: current verified Clerk actor, active actual practice/member, effective access and ownership are rechecked under Practice→Doctor→Appointment→Patient→Note locks. No view-as. Same UUID plus exact source/context replays; different input conflicts. New operations allocate a monotonic family revision and save actor, purpose, original ordered source, projection, admission provenance and completion in one immutable row/transaction. Reads reauthorize and reject changed/superseded source. Prepared receipts confer no CAB/model authority.

Expand rung f5efcbb27acd follows 16a6bf2d1539; generate the ID with secrets.token_hex(6), as allowed by the repository recipe. One append-only table, no backfill. Rehearse with only owned synthetic local PostgreSQL and cached dependencies. Ordinary tests/conftest.py performs Clerk cleanup/configured migrations; do not invoke it against ambient configuration. Use tests/billing_header with --confcutdir to bypass global hooks, an output-owned OpenAPI bootstrap, and pin candidate imports. Focused projection/validation, current-source, exact retry, permission, rollback, immutability and migration graph checks are required. Record actual outcomes/limitations in local evidence.

Recovery: turn off the source capability; retain immutable evidence and additive schema. No destructive downgrade. Rehearsal creates local prerequisite model tables and applies only the new actual Alembic expand rung; it does not replay the complete historical Core migration graph. The complete graph is checked separately without a database.

No hosted configuration, deployment, vendor credentials, live calls, remote data or external source admission. The UI is a following separate commit; preserve web working changes while staging only this backend scope.
