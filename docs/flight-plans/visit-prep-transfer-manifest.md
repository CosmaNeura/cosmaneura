# Visit Prep transfer manifest

Execution started October 1, 2026 on `codex/visit-prep-integration` in a fresh
checkout of `CosmaNeura/cosmaneura`. The base and remote `master` were verified
at `bc9da3478de0e03bacac0b81495ea43956c51279`. The source is the same repository,
at `d80a37012526d96991db25920797457c392231a8` in the old Visit Prep checkout.
Existing dirty checkouts have not been adopted or changed.

The integration branch was subsequently synchronized with Core `master` at
`1b105a1798dc659be9f216c5ea8a24606ac7b354`. Its current-BAA settings change
was retained, and the Visit Prep editor now fails closed when access data is
absent. A second synchronization retained Core `master` at
`9f5c61122934c1eb5a32868be9088945183fab37`, including its practice-owner
transfer and orphaned-owner recovery behavior. Administrative owner transfer
does not reassign existing Visit Prep clinical ownership, schedules or patient
credentials. Core deliberately retains the former owner's active workforce
access; separate explicit offboarding still denies patient access. No protected
branch was changed.

After the provider source/dashboard increments, the integration branch was
reconciled with Core `master` at `c5cd21ec2e09758a335c739f339a40c6a2649e99`.
The upstream change narrows unpublished native-Scribe status enrichment while
retaining its fresh authorization. It changes no Visit Prep routes, storage,
authority gates or migration ancestry; the merge had no conflicts.

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
| Current Core ownership compatibility | Current `practice_owner_transfer.py` and a new isolated integration test | Exercise the real transfer service against owned PostgreSQL. Verify the previous clinician's preparation lineage is unchanged, the new administrative owner cannot rotate that clinician's invitation, and separate offboarding closes patient access without deleting original records. Refresh PostgreSQL activity snapshots in the expiry race test so an early cached observation cannot mask the real row lock. |
| Attributable correction and review storage | New work for the approved patient correction/review requirement; current aggregate and response guards | Generate additive migration `4ad34f59136c` after `e161ca7c8448`. Preserve original answers and append encrypted corrections or content-free review events. Bind each event to the confirmed patient/caregiver role, current credential and exact original-response count plus prior event sequence. Recheck expiry after database locks; retain legal-hold and aggregate-cleanup rules. This dormant structure adds no patient read, correction API, UI or model call. |
| Current contribution and correction/review commands | New work using the recovered interview authorization, question validation and encryption primitives | Add one internal current-contribution projection with original wording/source IDs, effective corrections, current applicability and separate completion/review evidence. Add domain commands that reacquire current authority and confirmation evidence, append encrypted corrections, reject stale bases and preserve exact retries. Reviews require every applicable topic to have an answer or explicit skip. Return value-free receipts. No public correction/read route or patient review UI is mounted. |
| Answers to newly revealed conditional topics | New work for the approved correction/review loop | Generate `fe53eb110e32` after `4ad34f59136c`. Add a nullable exact-version question reference and closed `topic_answer` event shape. Record answer/skip and later revisions in the same immutable contribution sequence, preserving the first additional source separately from the original interview. Keep original response and retention guards unchanged. |
| Gated patient contribution read and revision API | New work using the contribution domain commands and existing public privacy boundary | Add three POST operations for saved-answer review, correction and confirmation of the exact current contribution. Recheck the current invitation, recipient first name and confirmed role on every operation. Expose only applicable topics and their initial/current wording; omit internal source IDs and retained hidden answers. Corrections and reviews return value-free receipts. All three consume the existing confirmation-attempt limits. |
| Patient saved-answer review and correction UI | New work using the existing patient public client, branded interview frame and one-topic form | Add an explicit saved-answer review step before or after original completion. Re-enter recipient evidence before disclosure; display current wording, caregiver attribution, skips and retained initial wording. Freeze uncertain requests and retry the exact payload/UUID; reload Core applicability after corrections. Permit newly relevant topics to be answered or skipped and confirm the exact current contribution without clinician-review claims. Clear displayed answers on focus loss and discard them on support-view/current-authority denial. |
| Provider source package and preparation list | New work using the current locked contribution projection, owner authorization and Core audit store | Add bounded owner-only pagination and a current-answer package with source version, original and revised wording, skips, missing topics and caregiver attribution. Exclude hidden conditional answers from the wire. Recheck locked identity, workforce authorization, BAA, entitlement, rollout, optional chart links and quarantine. Reject support-view disclosure. Retained provider reads do not depend on the public patient gate or invitation expiry. Record content-free read audits. No model synthesis or bot read capability is introduced. |
| Provider package dashboard | New `VisitPrepPackages.tsx`, existing current account SDK, support-view change events and dashboard route | Read preparations and answers only on explicit selection. Display patient/caregiver report, current words, missing/skipped topics, original wording and source provenance. Clear content before reload, on focus loss, account/access changes or support-view hard block; discard late replies. Reuse the generated SDK and no-store requests. A local synthetic harness exercises the actual component and real API at desktop/mobile sizes, replacing only account authentication/bootstrap. |
| Patient-source synthesis contract and dormant model adapter | Archived brief behavior at Core `b164ad7a8121ea08794d09f832d8ca7b7ee23f92`; current Session Prep generator/worker patterns at `c5cd21ec2e09758a335c739f339a40c6a2649e99`; new implementation | Preserve the concise headline, reported information and discussion priorities, but replace legacy Intake/chart assumptions with current Visit Prep sources. Add bounded sections, exact supporting quotes, current response/revision references and separate missing/skipped metadata. Use Core's existing Azure client with strict schema, zero automatic retries and bounded timeout. Require a usage-recorder callback before returning generated content. No route, job, artifact persistence or live model call is introduced. |

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

After the second Core synchronization, 477 selected backend/browser tests
passed: the previous 421 checks, 55 current-Core identity and transfer-CLI
checks with mocked identity-provider responses, and the new real-database
Visit Prep transfer check. All 44 warnings were existing deprecations. The
selected frontend suite again passed 149 tests and the full frontend typecheck
passed after synchronization. No shared database or identity-provider account
was used; the runner removed only its owned synthetic PostgreSQL and Redis
resources. These results are compatibility evidence, not a live transfer or
deployment rehearsal.

The correction/review expand rung passed 506 selected backend/browser tests,
including the previous 477 checks and 29 new contribution-storage checks. Its
focused storage/migration/concurrency selection passed 39 tests. The full
empty-database graph was applied through `4ad34f59136c`; original response
guards were not changed. Tests separately verify PostgreSQL's referencing-FK
truncate rejection, the original response truncate trigger, and aggregate
truncate rejection. New contribution checks cover exact-basis races, actual
lock waits during rotation/quarantine/expiry, ciphertext preservation, role and
lineage denials, and legal-hold-protected synthetic aggregate cleanup. Changed
Python implementation/test lint, formatting and focused typechecks passed.
No frontend source changed in this rung; its browser regression used the
previously built patient assets. All 44 warnings were existing deprecations.
These are storage and regression results, not a patient correction demonstration.

The committed architecture reference was rechecked at
`cosmaneura-architecture` revision `2b542addfc6d6d38198b0dedbfd03c45ad73e551`
(ratified v3.5), including ADRs 0021 and 0026. Core remains the clinical state
owner; conversational AI belongs in Sovereign. Existing dirty architecture
files were not used as ratified policy or modified. Counsel and operational
activation requirements remain separate from synthetic implementation evidence.

The contribution projection/command increment passed 558 selected
backend/browser tests, adding 52 checks to the previous 506. The focused command,
projection, concurrency and interview selection passed 110 checks before the
last additional zero-basis corruption case; that additional case passed in the
expanded regression. Focused typechecks, lint and formatting passed. The
three service-level races use independent PostgreSQL connections: divergent
same-basis corrections, simultaneous same-key retry, and review versus
correction. Both commands deny current cohort/public-gate, BAA, entitlement,
membership and clinician-tombstone failures. Correction and review fingerprints
are keyed and bind operation, actor, source, answer and exact basis.

Original interview completion is retained as history. A correction can make an
unanswered conditional topic applicable; the projection then reports the
revised contribution incomplete and refuses a new review. Old hidden answers
remain retained but are explicitly inapplicable. A later correction or original
answer makes earlier review evidence stale. No original answer or retention
guard changes. No frontend, public wire contract, model, provider-package read
or external delivery is added by these domain commands.

The revealed-topic increment passed 25 focused tests covering additional
answer/skip, first-source preservation, later revision, hide/reveal, exact retries,
divergent/stale bases, caregiver attribution, quarantine, encryption failure,
strict event shapes and lineage, immutable storage, real concurrency and legal
holds. The expanded backend/browser regression passed 582 checks with the same
44 existing warnings before the last additional parent-change race; that race
passed in the focused selection. Final commit-specific regression evidence is
recorded in the review increment.

The generated `fe53eb110e32` migration was also rehearsed over a populated,
owned synthetic database at `4ad34f59136c`. All prior original-response and
correction/review columns remained byte-for-byte unchanged, existing v1 keyed
fingerprints remained valid, and a newly revealed topic could be answered and
reviewed after the upgrade. The runner's new exact-revision option is limited to
an existing local migration and still creates only a disposable database.
Recovery leaves the additive structure and sources intact and uses a forward
fix; downgrade refuses deletion. No public correction route, patient review UI,
AI call, package read or delivery is activated by this increment.

## Remaining recovery and integration

The patient contribution API increment passed 608 selected backend/browser
checks, including 19 new full-app HTTP scenarios and six additional current
authority read cases. All tests used disposable synthetic PostgreSQL and
networkless Redis. The 49 warnings were framework deprecations. Changed-file
lint, formatting and focused typechecks passed. OpenAPI checks verify the new
POST-only bearer routes, required confirmation bodies and mutation idempotency
headers. The existing desktop/mobile browser regression still uses the earlier
patient assets and does not demonstrate browser review or correction.

Saved responses require the current invitation plus the first name and role
matching the original recipient confirmation. This is the existing recipient
check, not identity verification. Bearer-only GET requests disclose no answers.
Read and mutation requests share the existing eight-attempt, 15-minute
confirmation buckets; no release control or limit was relaxed. Independent
synthetic HTTP journeys have separate transport peers so their legitimate
network counters do not contaminate one another.

The patient review UI passed 168 focused frontend tests across fourteen files,
including ten new review/recovery cases, eight strict public-client cases and
one patient-route support-view clearing case. Full frontend typecheck passed
after generating the existing account SDK with the repository-pinned 0.97.3
generator; the older locally installed generator output was superseded and no
generated files changed. Formatting, public-client lint and browser-test
typechecks passed. The production-bundled test build used invented settings;
no deployment was performed.

Four built-browser acceptance scenarios passed against the real app and
disposable PostgreSQL/Redis: original-story correction and adherence correction
that reveals a previously hidden barrier, each on desktop and mobile. They
exercise lost acknowledgments and exact retries, original encrypted wording
preservation, caregiver attribution, a newly relevant explicit skip, current
review confirmation, no credential/answer persistence, and support-view
clearing. Synthetic review screenshots were visually inspected at both sizes,
with no horizontal overflow. These checks qualify deterministic review, not AI,
a provider package or delivery.

The authenticated provider source package API now supplies the current patient
contribution with exact question, response and revision provenance. Its stable
source version changes on corrections, recipient review, original completion,
schedule/credential context or canonical links, but never hashes plaintext
answers. The bounded invitation list contains metadata only. Support view,
revocation, wrong-recipient quarantine, stale sign-in identity and current
authority denials prevent disclosure. Database-owned time is rechecked after
locks and before disclosure; successful reads require a committed Core audit.
This is a patient-source package, not the planned AI synthesis artifact.

The dashboard now presents this source package on demand. Eight focused UI
tests cover loading, paging, wrong-package rejection, missing/revised wording,
late replies, account/access changes and support-view clearing. Two browser
acceptance scenarios use the actual component and real provider API at
1440×1000 and 390×844, with synthetic account authentication and production
CSS. Both screenshots were visually inspected; no horizontal overflow or
patient/token storage was observed. The production frontend build and full
typecheck passed. These are not live-account or deployment acceptance checks.

The combined increment passed 645 selected backend/browser tests with 52
framework-deprecation warnings, and 176 focused frontend tests across fifteen
files. The independent-connection correction race proves PostgreSQL blocks the
patient write until the provider snapshot and audit commit. A committed
revocation before the read denies disclosure. Provider source/API changed-file
lint and focused types passed; the component was linted explicitly despite the
repository's broad component ignore. The API is committed at `9c000cd8`.

The provider source API and dashboard are published as draft PRs 579 and 580;
the integration branch is backed up remotely at `e90e581b`. Both remain unmerged.

The first synthesis increment now defines a concise patient-report brief and
a dormant batch generator. Model input contains only current applicable answers
and the selected choice label; it excludes retained original/hidden wording,
chart content and separate recipient-contact, bearer and owner-identity fields.
It does not automatically redact identifiers volunteered inside free-form text.
The selection separately binds the authoritative package/source/question version,
current contribution basis, attribution and completion/review state. The model
cannot set those declarations. All-skipped input produces an empty brief without
a model request, while Core's missing/skipped state remains intact.

Each brief item requires an exact quote from its cited current source. The
validator checks source membership, quote matching, supported numeric tokens,
bounded scan length and a conservative prohibited-output filter. These are
mechanical checks, not proof of semantic accuracy, medication correctness,
complete de-identification or clinical usefulness. The original patient words
must remain separately available, and synthetic model-quality/clinician review
is still required. The schema has no diagnostic, treatment or billing output.

The adapter uses the existing configured model profile, not a new model or
service. It requests strict JSON Schema, one completion, `store=False`, a
45-second read/5-second connect timeout and no SDK retries or tools. Refusal,
truncation, invalid output, SDK failure and usage-recording failure return a
content-free error. Every attempted call must report content-free usage through
the supplied callback; this increment does not pretend that callback is already
wired into a durable ledger. The existing ledger's required chart-bound agent
lineage needs a compatible batch path for unlinked Visit Prep before activation.

The expanded owned PostgreSQL/Redis regression passed 686 backend/browser tests
with 52 existing framework-deprecation warnings, including 41 new synthesis
checks. The new tests use real patient contribution/owner reads and synthetic
responses. They cover effective revisions, hidden topics, newly revealed topic
sources, partial/caregiver attribution, all-skipped input, exact evidence,
unsupported numbers, output bounds, safe failure and mandatory usage recording.
One test exercises the actual Azure SDK with a local fake HTTP transport; no
external request occurs. Scoped Ruff/format and focused Pyright pass. Existing
frontend source and runtime flags are unchanged.

Immutable encrypted artifact/build storage, source-version revalidation at
publication, durable usage recording, the recoverable worker and dashboard
synthesis display remain the next synthesis increments. Bounded patient AI
clarification and the approved delivery reuse work remain separate work.
Storage, the internal current contribution and correction/review domain commands
now include answering or skipping newly applicable conditional topics through
the patient browser as well as the API.
Newly revealed answers have their own immutable source; they are not fabricated
original interview rows. Missing ordinary interview topics still use the original
ordered capture flow.
The basic patient UI and eight public operations are wired and tested but remain
gated off. Generate additive
migrations against the current graph; inspect and adapt the old aggregate and
retention triggers rather than copying obsolete migration ancestry.

End-to-end synthesis, bounded AI clarification, CAB delivery mechanics,
CosmaBot receiver/adapter, Acuity and SMS/reminders remain implementation work.
Their source inventory is in the approved plan. Only the synthesis contract and
dormant adapter described above are implemented; none of these complete flows
is marked ready. Pin selected external revisions and record each adaptation here.

Deployment, actual patient messages, cohort activation and workspace retirement
remain separate release decisions. All current runtime release controls remain
off; local evidence is synthetic and does not establish live-service readiness.
