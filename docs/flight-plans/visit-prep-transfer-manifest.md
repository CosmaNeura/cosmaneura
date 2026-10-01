# Visit Prep transfer manifest

Execution started October 1, 2026 on `codex/visit-prep-integration` in a fresh
checkout of `CosmaNeura/cosmaneura`. The base and remote `master` were verified
at `bc9da3478de0e03bacac0b81495ea43956c51279`. The source is the same repository,
at `d80a37012526d96991db25920797457c392231a8` in the old Visit Prep checkout.
Existing dirty checkouts have not been adopted or changed.

The integration branch was subsequently synchronized with Core `master` at
`1b105a1798dc659be9f216c5ea8a24606ac7b354`. Its current-BAA settings change
was retained, and the Visit Prep editor now fails closed when access data is
absent. No protected branch was changed.

The approved scope is in [the integration plan](visit-prep-integration.md).
Transfers use committed source and preserve the repository's existing license.

| Recovered behavior | Source files | Destination and adaptation |
| --- | --- | --- |
| Question definitions, normalization, seeded content, publication validation | `app/services/visit_prep_question_sets.py` | Extract pure rules into `visit_prep_question_definitions.py`. Require five topics and an unconditional final `story`; preserve `default.v1` content and fingerprint. |
| Physician-owned drafts, immutable versions, default selection, replay and locking | Same service; `tests/services/visit_prep_question_sets_test.py`; `tests/integration/visit_prep_question_publish_lock_test.py` | Recover lifecycle service and tests. Check current membership access state and use the current locked-seat factory. Re-export definitions for existing callers. |
| Provider question API | `app/routes/visit_prep.py`; `tests/routes/visit_prep_question_sets_test.py` | Register under current internal API; reuse existing Visit Prep gates. Retain non-disclosing denials and explicit serialization; add no-store responses and suppress sensitive exception chains. Adapt FastAPI nested-route inspection. |
| Question editor | `web/app/components/visit-prep/QuestionSetSettings.tsx` and its tests | Mount in current settings using server eligibility; label the final open-ended topic and validate it. Generate SDK from the current backend rather than copying old generated code. |
| Additional narrative coverage | New work based on John's October 1 clarification | Test all three physician prompt examples, block conditional/choice/list publication, preserve the old version through a real API edit/publish cycle. |
| Repeatable validation | Current authority suite's literal synthetic configuration | New owned PostgreSQL runner and test bootstrap. Apply the full current migration graph, block Python network connections, avoid repository-wide Clerk cleanup and database truncation, and optionally export current OpenAPI. |
| Preparation aggregate, immutable schedules and credentials, retention and legal holds | `app/models/visit_prep.py`; aggregate and clinical-retention migrations; model tests at the pinned source | Generate new migration `0b6ed6c49dd4` against current Core. Preserve owner/chart lineage, encrypted recipient envelope, verifier-only credentials and database guards. Freeze seven-year retention at first confirmation. Refuse destructive downgrade. |
| Preparation creation, replay, rotation, revocation, rescheduling and optional chart linking | `app/services/visit_preps.py`; command and lock tests | Separate pure command contract from persistence; refresh and lock current owner authority. Revalidate the five-topic published version before creation. Return plaintext credentials only after a successful commit and never on replay. |
| Provider preparation API | `app/routes/visit_prep_preparations.py`; route tests | Register in current internal API with current rollout and authority gates. Keep bearer material in a fixed patient-origin URL fragment; redact errors and enforce no-store and no-referrer responses. |
| Physician preparation workspace | `VisitPrepCreator.tsx`, schedule helper and tests at the pinned source | Add a server-gated dashboard route using the current SDK. Discard sensitive state on access or identity change. Keep copied links out of rendered DOM and storage; clearly mark the workflow synthetic-only until patient completion is restored. |
| Immutable patient answer and skip storage | `app/models/visit_prep_response.py`; response migration and model/lock tests at the pinned source | Generate `e161ca7c8448` after the current aggregate migration; reuse its existing lineage key. Preserve encrypted envelopes, keyed request fingerprints, ordering and current-credential guards. Database time is checked after the root lock. Refuse downgrade; test direct truncate rejection and approved synthetic aggregate cleanup. |
| Patient confirmation and deterministic interview lifecycle | `app/services/visit_prep_interviews.py`; service tests at the pinned source | Split the bounded input/state contract; restore confirmation, conditional topics, answer/skip, resume, replay and explicit completion. Add current seat/organization locks, owner tombstones, strict envelope versions and five-topic revalidation. Keep the independent public gate off. Corrections and AI clarification are not yet implemented. |
| Public rate limits and interview concurrency checks | `app/services/visit_prep_rate_limits.py`; rate-limit and interview lock tests at the pinned source | Recover fixed-window network, credential and confirmation limits with HMAC-only Redis keys and bounded store timeouts. Adapt lock tests to the current seat factory. Exercise the real Lua script in a disposable networkless Redis container, without connecting Python to shared Redis. |
| Public interview API | `app/routes/visit_prep_interviews.py`; public route tests at the pinned source | Mount four bearer-authorized operations outside account authentication. Bound request bodies, forbid extra/coerced fields, reject query credentials and slash redirects, return fixed generic errors, and apply no-store/no-referrer through CORS preflights and failures. Suppress exception causes and arbitrary error headers. Two new full-app HTTP tests exercise patient/caregiver five-topic completion, skips, retries, resume and credential rotation using real PostgreSQL and Redis. This is API evidence, not a patient UI demonstration. |
| Wrong-recipient reporting | New work using the recovered aggregate's existing revocation and quarantine fields | Add a fifth public operation. Revoke unused invitations, quarantine confirmed or answered preparations for review, and retain original encrypted answers and retention terms. Only the same current credential can replay its own acknowledgment; reads and writes stay denied. Real PostgreSQL races cover simultaneous reports, confirmation and answer submission. Full-app HTTP checks cover reports before and after confirmation. |
| Patient browser journey | `QuestionForm.tsx` and `InterviewFrame.tsx` at archived Core commit `0d92405fbe1b363d89ed23922de16d6922d7bb10`; new public API client and journey component | Adapt the narrow branded frame and one-topic screen to the current Visit Prep aggregate. Capture the credential in memory and remove its fragment before account SDKs or telemetry evaluate. Mount outside account providers; keep the bearer out of DOM, storage and request URLs. Restore patient/caregiver confirmation, answer/skip, conditional barriers, unconditional story, resume, explicit completion and wrong-recipient reporting. Preserve the original UUID and payload when acknowledgment is lost. Clear sensitive state on page departure, current-authority denial or browser support-context changes. |
| Patient telemetry and support-view isolation | Current Core telemetry scrubbers and `deny_view_as_browser_activity` | Do not initialize telemetry on the patient page; suppress patient events if Sentry was initialized before client-side navigation. Add credential redaction and prevent public request spans. Reuse signed-session support-view denial before database dispatch; account cookies do not authorize patient access. Real full-app HTTP tests verify nondisclosing denials without confirmation or revocation. |
| Built desktop and mobile acceptance | New `visit_prep_patient_browser_test.py` using existing Playwright and the owned runner | Route every browser request in process to the real app; block external requests and expose no server. At 1440×1000 and 390×844, verify caregiver confirmation, all five topics, skips, a committed answer with lost acknowledgment and same-key replay, final story, completion and encrypted original storage. Check no horizontal overflow, account bootstrap, telemetry requests, credential in URLs/DOM, or patient text in browser persistence. Synthetic screenshots were visually inspected. This does not qualify corrections, AI, the provider package or external delivery. |

No schema migration is needed for the question-authoring recovery: current Core
already contains the question-set models and migration `271e4439bff6`. The
complete empty-database migration through `16a6bf2d1539` was exercised locally;
the subsequent aggregate and response migrations through `e161ca7c8448` were
also exercised.

Review increments are dependency ordered: question contract and validation
support; persistence lifecycle; gated API; physician editor; aggregate schema;
command contract; preparation commands and API; physician creator; Core sync.
Each must remain
below the repository's 2,000-line and 50-file change budget. The integration
branch retains all increments until the feature is ready for the agreed merge.

Local evidence after the patient browser recovery: 421 backend and browser tests
passed, including eight selected current-Core privacy/middleware checks. The
selected frontend suite passed 149 tests across thirteen files, including
current-BAA settings, support view and patient telemetry tests. The creator's
updated synthetic-only status text passed its twelve tests and a renewed full
frontend typecheck. Production-bundled test assets built using only invented
configuration. Generated SDK and full frontend typecheck passed. Backend focused
typechecks and changed-file lint/format checks passed for the implementation
increments. Existing deprecation and Downshift warnings do not establish live
readiness.
Commit-specific final evidence belongs in the corresponding draft PRs.

## Remaining recovery and integration

Patient corrections and bounded AI clarification are next. The basic patient UI
and five public operations are wired and tested but remain gated off. Generate additive
migrations against the current graph; inspect and adapt the old aggregate and
retention triggers rather than copying obsolete migration ancestry.

Synthesis, dashboard package, bounded AI clarification, CAB delivery mechanics,
CosmaBot receiver/adapter, Acuity and SMS/reminders remain implementation work.
Their source inventory is in the approved plan; this manifest does not mark them
as transferred. Pin selected external revisions and record each adaptation here.

Deployment, actual patient messages, cohort activation and workspace retirement
remain separate release decisions. All current runtime release controls remain
off; local evidence is synthetic and does not establish live-service readiness.
