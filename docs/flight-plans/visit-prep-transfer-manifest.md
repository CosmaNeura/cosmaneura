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

Local evidence after response-storage recovery: 251 backend tests passed and 62
frontend tests passed across ten files, including the new current-BAA settings
tests. Generated SDK and full frontend typecheck passed. Backend focused
typechecks and changed-file lint/format checks passed for the implementation
increments. Existing deprecation and Downshift warnings do not establish live
readiness.
Commit-specific final evidence belongs in the corresponding draft PRs.

## Remaining recovery and integration

Patient confirmation, answer submission, resume/submit, and the patient UI are
next. Generate additive
migrations against the current graph; inspect and adapt the old aggregate and
retention triggers rather than copying obsolete migration ancestry.

Synthesis, dashboard package, bounded AI clarification, CAB delivery mechanics,
CosmaBot receiver/adapter, Acuity and SMS/reminders remain implementation work.
Their source inventory is in the approved plan; this manifest does not mark them
as transferred. Pin selected external revisions and record each adaptation here.

Deployment, actual patient messages, cohort activation and workspace retirement
remain separate release decisions. All current runtime release controls remain
off; local evidence is synthetic and does not establish live-service readiness.
