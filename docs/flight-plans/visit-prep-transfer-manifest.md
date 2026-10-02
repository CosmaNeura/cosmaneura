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

After the recoverable synthesis worker, the branch was reconciled with Core
`master` at `a06f04253b529de7270f61ce95ad3151c41f871d`. The upstream native
provider optimization uses private proof of already-held row locks to reduce
access queries; it is not a new authorization basis. Visit Prep retains the
ordinary fresh-access path and does not adopt that optimization. This merge
changes no Visit Prep schema, rollout settings or routes and had no conflicts.

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
| Encrypted immutable synthesis artifacts | Current Visit Prep encryption, aggregate retention and immutable response guards; Session Prep source/profile uniqueness pattern; new implementation | Generate additive revision `1cc41ced28aa` after `fe53eb110e32`. Store the brief and exact selected patient-source snapshot only in an explicit encrypted envelope. Bind metadata to question/schedule/credential lineage, contribution basis and generator/prompt/model profile. Serialize writes on the parent; reject stale basis, duplicate source/profile, mutation, standalone deletion and truncation. Preserve parent retention/legal holds and new-patient support without a fabricated chart. No runtime publication, reader or worker is mounted. |
| Owner-authorized synthesis publication and read domain | Current locked Visit Prep owner/source package and explicit artifact codec; Session Prep retry/publication pattern; new implementation | Share one transaction-bound package loader. Reacquire current owner authority, exact source hash, complete selected wording, attribution, review and lineage before publishing. Deduplicate the first valid source/profile artifact without overwrite. Read only the current artifact and return value-free missing/stale status for prior output without decrypting it. Commit content-free Core audits before any result. No route, build lease, worker or model call is mounted. |
| Durable synthesis queue and source-selection leases | Current Session Prep short-lease and bounded-retry pattern; Visit Prep owner/package authorization and encryption | Generate additive revision `2f87498bb9fa` after `1cc41ced28aa`. Keep one build per preparation/profile with an encrypted, context-bound original requester, source version, epoch, three-attempt limit and 90-second lease. Recheck owner access, identity, current answers and the lease before returning model sources. Superseded work is requeued under a new epoch; stale workers cannot release a newer claim. No model caller, publication worker or API is mounted. |
| Central usage accounting for chart-free synthesis | Existing Core `AgentModelUsageEvent` ledger and locked Visit Prep source/lease loader | Generate revision `54bedaad381a` after `2f87498bb9fa`. Add an explicit batch origin with canonical preparation/owner references, without creating a person, chart, authority identity or conversation. Commit one immutable call intent per fenced attempt before model work and append an exact-replay result afterward. Preserve the old agent-turn authorization and required lineage. No worker or model call is mounted. |
| Recoverable synthesis worker and atomic publication | Current Session Prep queue-drain and metadata-only job patterns; Visit Prep queue, generator, publication and central ledger | Connect source selection, committed call intent, bounded generation and durable result recording. Reacquire exact current sources, owner authority and live claim before committing artifact, ready job and Core audit together. Register argument-free, default-off drain and health tasks with no Celery autoretries. Report expired unmatched intents as uncertain without fabricating results. No provider synthesis API or dashboard display is mounted yet. |
| Provider synthesis request and current read API | Existing owner package authorization, source serializer, durable queue and artifact reader | Add bounded owner-only POST/GET operations. Request only the displayed source hash; return current sources, cited brief and build state from one locked/audited snapshot. Exclude old-source job state and stale brief payload. Recheck access after audit flush. Preserve the old source-only endpoint, regenerate the current SDK, and test actual HTTP-to-worker publication and independent PostgreSQL races. |
| Current cited brief on the provider dashboard | Existing source-package dashboard, current synthesis SDK and no-store support-view clearing | Read sources and synthesis atomically on explicit selection. Display a bounded interpretation, patient/caregiver and review status, missing/skipped topics, exact supporting words and current source references. Queue only an explicit exact-source request and re-read; never automatically retry an uncertain POST. Clear content on reload, concealment, access change or support denial. Test current, stale, empty and failed output plus desktop/mobile worker publication and lost-acknowledgment recovery. |
| Minimal invitation and dormant SMTP adapter | CAB `01ad97536c4f6730e30cd8a03dd31cdb07a918fb`, `chase/acs_email.py` and adapter tests; Core's existing mailer/SMTP transport | Retain one recipient, sanitized errors and acceptance-versus-delivery distinction. Replace arbitrary message inputs and insurance filtering with a closed plain-text Visit Prep template. Use existing Core SMTP, not the CAB ACS dependency or credentials. Require TLS and certificate validation; reject console/file/null sinks. Bound one attempt to 30 seconds. SMTP message ID is provenance, not idempotency. Preserve Apache-2.0 in `docs/licenses/cab-apache-2.0.txt`. |
| Dormant email outbox expand | CAB at the same pinned revision, `scheduler_store.py` irreversible claim/uncertain-state pattern; new Core model and generated migration | Generate `ed9dec654c8c` after `54bedaad381a`. Bind one invitation per credential to owner, question and schedule lineage. Separate encrypted authorization evidence from the transient invitation secret, capped at 24 hours. Fence one submission with a database-owned 90-second lease; never return it to the send lane. Clear the secret at terminal outcomes, preserve parent retention/legal holds and refuse destructive downgrade. No command, worker or email route is connected. |

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

The synthesis contract is published as draft PR 581 at `0d408537`; it remains
unmerged. The subsequent artifact expand adds immutable encrypted storage and
an explicit codec. Decoding compares the complete selected-source manifest and
revalidates the brief; changed source version, wording, attribution or review
state cannot silently reuse a prior envelope. Ciphertext is excluded from ORM
serialization/repr; no transparent decryption or plaintext artifact column is
introduced. Mechanical validation is not clinical verification or authorization.

The expanded synthetic regression passed 720 backend/browser checks with 52
existing framework warnings. Its one skipped case requires an explicit
pre-expand migration target and passed separately over a populated disposable
database at `fe53eb110e32`. All prior aggregate, schedule, credential, original
response and contribution columns remained unchanged through the expand.
Real independent-connection races prove one sequential artifact under concurrent
same-source inserts and stale-publication rejection after a correction or
quarantine commits. The explicit truncate trigger is tested after a committed
insert; transaction-level pending-FK rejection is not substituted for it.
Historical retained-owner storage does not depend on public credential expiry,
and parent retention/legal holds control aggregate-only cleanup. Scoped lint,
format and focused implementation types passed. No frontend or release flag
changed.

Artifact storage is published as draft PR 582 at `e3565e6e`; it remains unmerged.
The next increment adds dormant owner publication/read services using a shared
transaction-bound package loader. Publication compares the full current selected
source snapshot, not merely caller-provided response counts or a digest, and
sets artifact metadata from the locked Core rows. Corrections, review, original
completion, schedule or credential changes reject superseded model output.
Retry reuses the first validated artifact rather than overwriting it. Reads
recheck current owner access and the complete artifact metadata and encrypted
manifest. Prior source/profile artifacts produce missing/stale status without
decrypting or returning the old brief; the current patient-source package
remains available. Final database time is checked before the required Core audit
commit. No API, frontend, build/lease or worker calls these functions yet.

The focused new publication selection passed 42 tests against disposable
PostgreSQL and network-isolated Redis. Real independent-connection tests cover
one publication plus one replay under concurrent retries; committed corrections
or withdrawn BAA observed after a genuine lock wait; and correction blocked
until an existing brief read and audit commit. Tests also cover new-patient and
caregiver/partial sources, no invented content for all-skipped input, changed
wording or attribution with unchanged counts, profile mismatch, malformed
ciphertext, false stored completion metadata, audit rollback, encryption failure,
retained owner access after public invitation expiry and expired entitlement at
the final database clock. These remain synthetic domain results, not clinical
quality, an operational worker or a dashboard synthesis demonstration.

The durable queue now supports authorized same-source requests, bounded attempts,
short leases and current-source selection without requiring a chart. Request
retries do not reset attempts, backoff or a running lease. An expired claim can
be reclaimed, while the old worker cannot select sources or release the new
claim. Changed answers, patient review or credential rotation before selection
requeue current sources under a new epoch without returning old answers. Access,
identity or quarantine denial ends the claim. The requester envelope is encrypted
and bound to the preparation, owner and generation profile; job records contain
no patient text or invitation secret. Successful requests/source selections
require content-free Core audits. Operational job state is mutable and disposable
through parent cleanup; it is not a clinical artifact or a second usage ledger.

The focused queue, migration and independent-connection selection passed 45
checks. One populated-expand case requires an explicit prior migration target
and passed separately: all original sources and the immutable encrypted brief
were unchanged. Real PostgreSQL races cover concurrent request deduplication,
one live claim, skip-locked polling and current sources/BAA rechecked after an
actual owner-lock wait. Other checks cover encrypted requester context, final
lease expiry, profile changes, all-skipped input and parent legal holds. Scoped
lint/format and implementation types passed. These are dormant queue results,
not evidence of model execution, usage accounting or worker publication.

The expanded backend/browser regression passed 804 checks with 52 existing
framework-deprecation warnings. Its two migration-target cases are skipped in
the full graph run; the build expand passed separately over the prior populated
graph, and the artifact expand has its separately recorded rehearsal. No
frontend source, model adapter, release control or external service changed.

Central usage recording now uses an explicit batch variant in the existing
ledger. Original agent writes default to `agent_turn`, retain required
interaction/tenant/actor/person lineage and use the unchanged authorization and
conversation-phase service. Batch records instead carry locked Core owner and
preparation references; they do not fabricate or bind a chart or person.
Database checks keep the two shapes separate. Existing update/delete guards
remain, and a populated batch ledger rejects truncation.

One call intent commits before model work, with the current source, epoch,
attempt and lease. Repeating that intent does not authorize another call.
Results append without mutating the intent and support only exact replay.
Failed or unavailable usage is explicit. Results can still record an already
attempted call after access, source or lease changes, but grant no disclosure or
publication authority. An unmatched intent indicates uncertainty after a crash,
not a zero-token success. Operational reconciliation of such intents belongs in
the forthcoming worker; no new burn-rate dashboard is introduced.

The accounting checks cover chart-free patient and partial-caregiver sources,
privacy, exact retries, malformed facts, source/authority denial, transaction
rollback and immutable database guards. Independent PostgreSQL connections
prove one intent per attempt and one result under concurrent exact retries.
The populated rehearsal preserves all prior usage columns and original sources,
artifacts and jobs; the unchanged agent service still records and replays after
the expand. Final commit-specific test totals are recorded in the draft PR.
No frontend source, model adapter or release control changes in this increment.

The recoverable worker now connects the existing generator to the central ledger
and atomic publication. The model call runs after the intent commits and without
owner or patient database locks. Publication requires the immutable successful
result for that exact source, lease, epoch and attempt; merely returning from a
callback cannot bypass the database proof. Recorded unavailable token counts
remain explicitly unavailable, while a missing result denies publication.
The worker rechecks current sources and authority, then commits the encrypted
brief, ready job state and content-free audit in one transaction. It checks the
lease and time-dependent authority again after the audit flush.

Corrections or review during generation retain accounting for the attempted
call but requeue current sources without rebasing its output. Revocation,
quarantine or withdrawn authority denies publication. A lost publication
acknowledgment leaves one durable artifact and ready job; the next drain does
not repeat the model call. Expired leases recover through the existing
three-attempt queue policy, not Celery's automatic retries. Expired unmatched
usage intents remain uncertain even if a later attempt finishes. Metadata-only
health reads expose that uncertainty; they do not guess results or token counts.

Argument-free drain and health tasks are registered in Core's existing scheduler
at five-second and sixty-second intervals. Both return before accessing storage
when the existing feature flag is disabled or configuration is invalid. No new
environment setting, deployment, channel activation or live model request is
introduced by that worker increment. The provider API is described below;
dashboard presentation, bounded patient AI clarification and delivery reuse
remain separate implementation work.

The focused worker/publication selection passed 95 synthetic checks, including
four independent-connection worker scenarios and the existing Celery privacy
checks. They prove unlocked model work, concurrent publication exclusion,
atomic artifact/job/audit visibility, correction blocking only during publication,
and lost-acknowledgment recovery. Other checks cover chart-free partial/caregiver
input, all-skipped completion without a model call, explicit unavailable tokens,
refusal/truncation/invalid output, bounded attempts, exact durable usage proof,
in-flight authority/source changes, rollback and final lease/access expiry.
Scoped lint, formatting and implementation typechecks passed. Final expanded
regression totals and exact tested revision belong in the draft review increment.

The provider synthesis API now accepts only a strict current-source hash in a
request body bounded to 1 KiB before JSON parsing. It never accepts a model
brief, owner identity, answers or broker payload from the browser and never
calls a model inline. Exact-source retries preserve attempts, backoff and the
running lease; the worker owns generation. An explicit current-source request
can recover an inconsistent ready job with no artifact.

The synthesis read returns current patient sources and the brief together from
one authorized transaction, with source/artifact version and model/prompt
provenance. Missing, stale, queued, running and failed states are distinct;
old-source work is not advertised as current and stale ciphertext is not
decrypted. A final authorization check after the audit flush denies disclosure
if time-dependent access expires during that wait. The original source-only
endpoint keeps its wire contract, using the same explicit source serializer.
Support-view, wrong-owner, quarantine and current access denials remain private.

HTTP acceptance uses the actual router, queue, worker and central ledger with
an invented SDK response, including partial caregiver and all-skipped paths.
Independent-connection HTTP tests cover concurrent requests producing one build,
changed authority/source while waiting on owner locks, and an atomic source/brief
read serialized with correction. The aggregate health tests now compare ready
counts against the prior committed synthetic baseline, as failed counts already
did; they do not require an otherwise empty integration database. Final exact
commands and totals are recorded in the draft review. The current SDK is
generated from the tested internal OpenAPI; no frontend source is changed yet.

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

The dashboard now loads the current sources and brief together rather than
combining separate reads. Its pre-visit brief is labeled as AI interpretation;
patient/caregiver attribution, partial status and recipient confirmation are
supplied by Core, not the model. Each item expands to the exact supporting
current wording and source reference. Skips and missing topics remain explicit,
and all-skipped input produces an honestly empty brief. Original wording and
the complete source package remain separately available below it.

Generation requires an explicit current-source request. The component clears
its old snapshot before that request and then reads the atomic package again.
An uncertain acknowledgment requires an explicit reload rather than an
automatic POST retry. Pending/running work is read-only; failed work preserves
Core's cooldown. Scope changes, focus loss, concealment, access denial and
support-view changes discard both sources and brief, including late request
or read responses. No browser persistence, automatic model polling or
patient-content Markdown/HTML rendering is introduced.

Desktop and mobile acceptance use the actual component, router, durable queue,
worker, encrypted artifacts and central ledger with invented SDK output. They
exercise explicit generation, cited current wording, patient correction hiding
an older brief, a committed request/publication with a lost acknowledgment,
GET recovery without another model call, and actual BAA withdrawal. Synthetic
screenshots were visually inspected with no horizontal overflow. This verifies
the technical dashboard journey, not real-account access, model quality,
clinician usefulness, external delivery or deployment. Final revision-specific
regression/build results belong in the draft review.

Bounded AI clarification, remaining CAB delivery integration,
CosmaBot receiver/adapter, Acuity and SMS/reminders remain implementation work.
Their source inventory is in the approved plan. The synthesis contract, adapter,
artifact storage, owner publication/read domain, durable queue, central accounting
and gated worker/API/dashboard described above are implemented. Clinical-quality
qualification and the remaining delivery/agent integrations are not complete.
Pin selected external revisions and record each adaptation here.

Deployment, actual patient messages, cohort activation and workspace retirement
remain separate release decisions. All current runtime release controls remain
off; local evidence is synthetic and does not establish live-service readiness.

The first CAB-derived email increment now supplies the closed invitation template,
dormant Core SMTP adapter and additive outbox storage. CAB `main` was freshly
verified at `01ad97536c4f6730e30cd8a03dd31cdb07a918fb`; its email adapter and worker
are unchanged from the separately inspected desktop commit `e3c335d32b43`.
No unrelated CAB checkout content, configuration, credentials, billing policy or database was copied.
The copied mailbox-validation behavior retains CAB's Apache license; changed-file
notices describe the adaptation.

The email contains only fixed preparation guidance and its private invitation
link. It has no patient name, appointment details, answers, diagnoses, attachments,
tracking pixels or arbitrary message context. The SMTP adapter uses the existing
dependency but rejects non-SMTP, non-TLS or certificate-unvalidated configuration.
This is a fail-closed prerequisite, not a change to existing mailer configuration.
Timeouts and provider errors are sanitized uncertainty, never proof of non-send.
The provider message ID contains only the random operation UUID and sender domain;
SMTP does not promise provider deduplication or confirmed delivery.

The generated outbox expand changes only its new table and guards. Autogenerated
comment drift from unrelated tables was removed. Database checks enforce immutable
intent, current credential/schedule at acquisition, one submission lane, explicit
terminal state and removal of encrypted invitation payload on finalization. The
24-hour maximum secret lifetime still requires the upcoming cleanup worker; merely
storing expiry does not delete ciphertext. Durable authorization-envelope validation,
durable consent/standing-approval resolution, atomic invitation enqueue, worker
dispatch, status UI and operational reconciliation are still implementation work.

Synthetic checks exercise the actual Core mailer with SMTP replaced at the provider
boundary and network access denied, plus real PostgreSQL claim races, revocation
after a parent-lock wait, named truncate rejection and legal-hold cleanup. A
populated rehearsal preserves existing encrypted answers, synthesis artifacts,
queue state and central usage byte-for-byte through `54bedaad381a` to the new expand.
These are storage/transport-boundary checks, not an end-to-end dispatched invitation
or external email acceptance. Final tested revision and totals belong in the draft PR.

The next email increment adds explicit invitation encryption and decryption in
`visit_prep_email_payload.py`. Its closed envelope binds the bearer and recipient
to the outbox ID, preparation, owner, question version, schedule, credential
verifier, operation UUID, template, expiry and separate authorization ciphertext
fingerprint. Duplicate JSON keys, extra message fields, changed trusted origins,
cross-bound payloads and sensitive exception chains are rejected. Recipient and
authorization fingerprints use separate keyed domains, not plain contact hashes.

The stored-row boundary explicitly decrypts the closed recipient snapshot and
checks matching lineage, inactive preparations, revoked credentials, expiry and
the submission lease. It never reads a chart, sends a message or mutates a row.
The enqueue command must allocate the outbox TypeID before sealing the payload;
Core's ordinary ID default runs only at INSERT. Real synthetic PostgreSQL checks
verify that a sealed payload survives insertion and can be recovered against the
stored recipient and verifier, without a canonical patient or appointment.

This is integrity validation, not runtime authorization. The upcoming command
and worker still must lock current scope and latest lineage, resolve durable
current consent/approval, enforce channel/cohort/recipient gates, dispatch once,
clear expired secrets and expose honest outcomes. Fingerprinting opaque
authorization ciphertext does not validate the evidence inside it or grant
permission to send. No route, worker registration, configuration, schema or
release control changes in this increment. Final commit-specific checks and
implementer review belong in its stacked draft PR.

The next dormant expand, generated as `10b7993b911d` after `ed9dec654c8c`,
adds separate physician email-policy approval and recipient email-consent
event streams. The adaptation follows CAB's separation of policy authority,
contact/consent provenance and runtime submission guard, inspected at the
pinned CAB revision above. Core owns these records; no CAB database or policy,
trusted-source credential, SMS exemption or billing rule was imported.

Each stream is append-only, has a current sequence and operation key, and
stores provenance only in a bounded encrypted envelope. Consent additionally
binds one preparation, recipient digest, source-event key and effective time;
it does not require a fabricated chart. Database clocks are read after scope
locks. A late withdrawal remains recordable on a revoked preparation, and an
older grant cannot restore permission after that withdrawal. Physician
approval never creates recipient consent. Role labels and ciphertext alone
do not prove identity, caregiver authority or authentic source evidence.

Independent PostgreSQL transactions exercise competing first events, a grant
waiting on revocation, and actual elapsed expiry while waiting for a lock.
The populated expand snapshots every column of nine prior patient, synthesis,
central-usage and email tables and requires byte-for-byte preservation.
Autogenerated unrelated table/comment changes were removed. No prior patient
retention trigger, route, worker or release control changes.

Evidence deletion and truncation are denied in this dormant rung. Consent uses
a restrictive parent reference rather than silently disappearing with unused
preparation cleanup. This is temporary protected custody, not a completed
retention policy: preparations carrying this new evidence cannot yet be purged.
A qualified consent-evidence retention/hold/cleanup path is required before
capture or dispatch activation. Current-authority capture commands,
patient/trusted-source consent intake, atomic invitation enqueue and worker
submission remain implementation work. No
schema row is permission to send. Final commit-specific validation and review
belong in the next stacked draft PR.

The evidence and resolution increment adds explicit closed encrypted manifests
for physician policy approval and recipient communication consent. Each binds
its own event ID, owner, sequence, operation and expiry to the stored row.
Consent also binds the preparation, question version, recipient, role and
source-event provenance. Duplicate JSON keys, extra fields, coercion, mismatched
metadata and private exception chains are rejected. The policy fingerprint
tracks the actual fixed invitation text and existing outbox limits rather
than accepting an authored policy hash as current permission.

The resolver locks current Core owner scope, preparation, latest schedule and
credential, and both latest authority streams. It rechecks current channel,
public-link and cohort controls, identity, membership, BAA and entitlement;
linked appointments/patients remain optional but must match when present.
An older grant cannot substitute for the latest withdrawal, expired evidence
or changed policy. Recipient contact is explicitly decoded through the same
closed boundary used by invitation binding. Time-dependent authority is checked
after source verification and all lock waits.

No consent-source adapter is installed by default. A trusted Core verifier
must independently establish authentic source, exact intent and actor or
caregiver authority; a role label, ciphertext or booking connection proves
none of those. Missing or unavailable verification fails closed. A verifier
cannot release the caller's transaction or savepoint and still return authority.
The result remains transaction-bound, not a transferable send capability.

Synthetic PostgreSQL tests use an explicitly invented complete source intent,
not real consent. Independent connections exercise committed withdrawals,
revocation, BAA withdrawal, actual elapsed expiry and caller-held withdrawal
fences. These checks verify the dormant resolver, not consent capture or a
dispatched invitation. No schema, frontend, route, job registration, external
service or release-control change is included. Qualified evidence retention,
capture adapters, atomic enqueue, dispatch, cleanup and status UI remain open.
Final tested revision, regression results and implementer review are recorded
in the stacked draft PR.

The physician policy-decision command now captures explicit approval or
withdrawal in `visit_prep_email_policy.py`. It adapts the pinned CAB policy
fingerprint and operation-replay pattern to Core's current owner locks,
encrypted evidence and existing authoritative audit store. The future
authenticated route must derive owner and subject from its principal; this
domain command does not accept an actor inside the decision payload or create
an authority identity. No new dependency, schema, route or job is introduced.

Each new decision confirms the displayed policy fingerprint, explicit expiry
and current stream sequence. The command commits evidence and a content-free
audit together, rechecking current Core authority and database time after lock
waits and before commit. A changed template requires new approval; withdrawing
the previous template does not approve its replacement. Exact operation replay
returns the original receipt and current stream sequence without another event
or audit. Neither replay nor a stale new operation restores a withdrawn grant.
Expired historical receipts remain receipts, not renewed permission.

Synthetic PostgreSQL checks cover atomic audit failure, divergent retries,
current identity/BAA/seat/entitlement denials, actual elapsed expiry, and
independent competing decisions and withdrawal waits. Captured policy evidence
is consumed by the existing locked resolver, which still denies missing
recipient consent and denies physician withdrawal. The consent-source fixture
in that integration check is explicitly invented, not qualified patient consent.

This completes the dormant physician decision domain, not its dashboard control
or an invitation send. Its commit-specific checks and implementer review are
recorded in the stacked draft PR. Email and public release controls remain off.

The next domain increment separately captures an explicit email grant or
withdrawal from a currently confirmed patient/caregiver invitation holder.
`visit_prep_email_consent.py` uses the existing current-bearer and owner locks,
append-only encrypted consent stream, and Core audit store. Opening a link,
confirming a name, answering questions, physician approval and declining email
remain distinct actions. The fixed draft disclosure is email-only and optional;
its actual text, role, purpose, deadline and separate recipient/caregiver
attestations are bound to the capture evidence. Grants cannot extend past the
current invitation or shown visit, and completion prevents a new grant while
allowing withdrawal through a still-valid link.

Evidence and its content-free source audit commit together using database time.
Exact retries preserve the original receipt and report the latest sequence;
neither replay nor a stale fresh operation restores a later withdrawal. The
source reader verifies the actual stored evidence, original credential and exact
capture audit. It reports invitation-bearer attribution and explicit attestations,
not independent patient identity or legal caregiver verification. No qualified
source verifier is installed in the sending resolver. A later link rotation
preserves historical attribution but never revives the old bearer.

Synthetic PostgreSQL checks exercise patient and caregiver choices, divergent
and uncertain retries, atomic audit failure, elapsed expiry, competing choices,
and committed withdrawal/BAA/rotation/wrong-recipient changes during real lock
waits. They do not establish legally qualified consent or an external send.
Draft disclosure and evidence retention/hold/cleanup qualification, public and
physician controls, trusted prior-consent intake, atomic enqueue, dispatch and
delivery status remain open. Consent captured after opening a link cannot
authorize the first booking email; that requires separately verified prior
consent. No schema, mounted route, job, dependency or release control changes
in this increment. Final revision-specific validation belongs in its draft PR.

The physician email-policy control now has an authenticated API and a dashboard
panel in the existing Visit Prep workspace. The API derives scope and subject
from the current account, locks Core authority, displays the actual fixed
invitation with a nonworking link placeholder, and commits a content-free read
audit before disclosure. Its states distinguish no approval, approval at the
last read, withdrawal, expiry and changed policy. Decisions require one canonical
operation key, the displayed policy and stream sequence, and an explicit
timezone-aware expiry for approval. There is no recipient, actor or ownership
override in the request.

The dashboard requires explicit review and a UTC expiry. Withdrawal targets the
previously decided policy even when the current template changed. A decision
receipt is never displayed as current approval; the physician must reload Core
state after success or an uncertain acknowledgment. The screen does not retry
mutations automatically, persist policy state in the browser, or enable sending.
Scope, access, focus, concealment and support-view changes clear displayed state
and invalidate late replies.

Owned PostgreSQL tests verify committed withdrawal and BAA changes during real
lock waits, elapsed policy/seat expiry, atomic audit failure, strict/redacted
requests, current-owner denials and exact lost-acknowledgment replay. Desktop and
mobile acceptance use the real component and API, including stale decisions,
an actual committed approval with its HTTP acknowledgment discarded, read-only
recovery and BAA denial. Synthetic screenshots were visually inspected with no
horizontal overflow. No consent or email-delivery rows were created. Final
commit-specific regression and build results belong in the draft PR.

This completes the physician policy control, not invitation delivery. Qualified
consent disclosure and retention, patient consent controls, trusted prior-consent
intake, atomic enqueue, dispatch, cleanup and delivery status remain open. Email,
SMS and public release controls remain off; no schema, dependency, external
provider, deployment or cohort activation changes are included.

The patient email-choice API now provides an explicit current-state read and a
separate grant/withdrawal operation in the existing bearer-authorized public
router. Both require the first name and role matching the preparation's original
confirmation, using the same check as patient answer review. This is recipient
confirmation, not verified patient identity or legal caregiver authority. Reads
commit a content-free Core audit before returning the current sequence,
disclosures, deadline and optional choice status. They disclose no email address,
patient answers, internal source IDs or bearer credentials.

The public capture adapter checks confirmation while holding the same authority
and preparation locks as the immutable decision write. Exact operation replay
returns the original receipt and latest sequence without restoring a later
withdrawal. Completion removes the new-grant option while preserving read and
withdrawal through a still-valid link. Expiry, rotation, wrong-recipient quarantine,
current owner authority and elapsed database time remain checked before return.
Lost acknowledgments require a fresh read rather than a new decision key.

Public reads and decisions share the existing eight-attempt confirmation budget
and bounded, redacted request handling. The new independent
`VISIT_PREP_EMAIL_CONSENT_CAPTURE_ENABLED` control defaults off: enabling patient
interviews does not also enable draft consent capture. The response explicitly
labels a grant as recorded, its source as invitation-bearer attribution, sending
authority as not qualified, and delivery as not connected. No qualified verifier
is installed and no job is enqueued.

Synthetic full-app HTTP checks and independent PostgreSQL lock tests cover
patient/caregiver decisions, withdrawal, stale and divergent retries, atomic audit
failure, a real committed decision with lost acknowledgment, support-view denial,
strict requests, private errors, shared rate limits and current authority changes
during lock waits. Final revision-specific regression and implementer review
belong in the stacked draft PR. This API increment does not include the patient
screen. Disclosure and evidence retention qualification, prior-consent intake,
enqueue/dispatch, cleanup and delivery status remain open. It cannot authorize
the first booking email from consent captured after opening the link. No schema,
external dependency, deployment, real message or cohort activation was changed.

The patient email-choice screen now uses the real public choice API from the
existing interview page. It is optional, mounts only after an explicit choice,
and performs no automatic consent read or decision. Returning without deciding
preserves the unsaved interview draft and reloads current interview progress.
The five physician topics, including the unconditional free-form story, remain
independent of email permission.

Before showing choices, the screen requests the recipient first name and role
again. Granting requires the displayed disclosure, explicit recipient control
attestation, a separate caregiver attestation when applicable, and a future UTC
expiry within Core's current limit. Declining or withdrawing requires neither
grant attestation nor expiry; withdrawal remains available after completion
through a valid invitation. The client rejects overbroad responses, invented
identity or sending authority, inconsistent sequence/status/deadline states,
and malformed receipts.

A decision uses a frozen payload and one UUID. The screen clears its displayed
snapshot before sending and does not retry the decision automatically. After a
receipt or uncertain acknowledgment, the patient must explicitly confirm again
to read current state. Focus loss and concealment clear choice state and
attestations; late replies are discarded. Parent support-view or access denial
clears the entire patient page and its private credential. No choice, recipient
name or credential is written to browser storage.

Built desktop/mobile acceptance covers patient and caregiver choices, returning
without choosing, an actual committed grant with its acknowledgment discarded,
read-only recovery, interview completion, withdrawal and current BAA denial.
Owned PostgreSQL and network-isolated Redis preserve real confirmation counters.
This demonstrates the screen and API integration, not qualified consent or
operational delivery. Final revision-specific validation belongs in its draft PR.
The shared SPA index still links a Google Fonts stylesheet. Browser tests abort
that exact inherited public URL before networking and reject every other
external URL; they do not establish that the release page has no third-party
font request. That public-page dependency remains a release-qualification item.

The capture, email/SMS and public release controls remain unchanged and default
off. Qualified disclosure, evidence retention/hold/cleanup, prior-consent intake,
invitation enqueue/dispatch/status, bounded patient AI clarification, CosmaBot,
Acuity and SMS/reminders remain open. Consent collected through an already-opened
link cannot authorize that first invitation email.

The owner-scoped email handoff command now atomically records an encrypted
invitation intent and its content-free audit in the existing outbox. Its locked
helper does not commit and can be composed into a future booking/creation
transaction; the current creation and scheduling paths do not call it yet.
The command derives recipient contact from the preparation, verifies the
actual current bearer and credential generation, and independently rechecks
current owner authority, policy approval and qualified recipient consent.
No consent-source verifier is installed by default. Missing verification,
disabled controls, changed context or divergent request intent denies new work.

The allocated outbox ID, schedule/credential lineage, requester, deadline and
the two exact evidence manifests are encrypted together. The transient bearer
and mailbox are sealed in the separate existing payload and cross-bound to
that evidence. The deadline is at most 24 hours and cannot outlive either
permission or the credential. Source verification is repeated after encryption
and before any command writes; final authority/time checks follow the audit.
No source callback runs after these writes. Any pre-commit failure rolls back
both the outbox and its audit.

One credential permits one invitation operation. A repeated exact operation
returns its existing receipt without extending the deadline, restoring a
terminal secret or refreshing evidence. A new key for the same credential or
changed bearer/origin is rejected. Historical replay still requires current
owner access but does not require current sending permission: its explicit
replayed disposition is not authority to dispatch. Lost commit acknowledgment
recovers by replaying that same intent, never by generating a fresh operation.

Real synthetic PostgreSQL tests cover concurrent identical and competing keys,
withdrawals and revocation/rotation committed while enqueue waits, elapsed
expiry, caller rollback, final-check failure after audit, and lost commit
acknowledgment. They also verify the retained invitation decrypts against the
stored lineage, new credentials require their own actual bearer, and terminal
receipts cannot reopen submission. No transport, model or live service is called.

This completes the atomic existing-invitation handoff, not email delivery.
Worker dispatch/status/secret cleanup, creation/booking integration, qualified
prior-consent intake and evidence retention/hold/cleanup remain open. The
patient capture and channel/public controls remain unchanged and default off.
The broader patient AI, CosmaBot, Acuity, SMS/STOP/reminder and release milestones
remain in scope. Exact validation and implementer review belong in the stacked PR.

The dormant email worker now commits an irreversible submission claim before
invoking a transport. It then reacquires Core authority and compares the actual
current approval and qualified consent with the exact encrypted enqueue
manifests. Credential, schedule, recipient, owner, origin, policy and database
time must remain current. The worker holds the existing withdrawal fences
through the bounded transport attempt and atomic terminal audit. No job, route,
transport or qualified consent-source adapter is registered by default.

Only the original successfully acknowledged claim invocation may proceed.
Its process-local one-use guard is shared by shallow copies and dataclass
replacement; claims are not serialized to a broker or exposed through an API.
The durable outbox state never returns to the send lane. Losing acknowledgment
of a committed claim prevents any provider call. A crash, an unrecognized
transport result, a provider exception or an expired submission remains
uncertain and is never automatically resent. This is a single application
submission attempt, not a promise of exactly-once external delivery. Provider
acceptance is recorded as accepted, never delivered.

Secret maintenance remains available with release and sending controls off.
It selects due or obsolete work rather than letting older active invitations
fill the batch. Queued invitations are suppressed after expiry, revocation,
wrong-recipient quarantine, completion, credential rotation, consent withdrawal
or physician approval withdrawal. Expired submission claims become uncertain.
Terminal state, transient payload erasure and the content-free audit commit
together. Authorization ciphertext, immutable consent evidence, patient words
and legal holds remain untouched. Evidence retention and aggregate purge still
require separate qualification.

Independent synthetic PostgreSQL checks observe committed claims from a
separate connection, race two actual workers, and verify withdrawals and other
authority changes committed during real lock waits. They also hold a withdrawal
through the fake provider call, inject acknowledgment loss after actual SQL
commits, and prove terminal storage failure cannot replay the original or a
copied claim. The real Core mailer is exercised with only a fake SMTP function;
no message or credential leaves the owned test boundary. Atomic audit failure,
transaction-releasing source callbacks and private provider failures are covered.
Final exact-revision regression and implementer review belong in the draft PR.

This completes the dormant submission and transient-secret maintenance domain,
not operational email delivery. Creation/booking composition, qualified prior
consent, evidence retention, worker registration, dashboard delivery status,
queue monitoring and controlled provider acceptance remain open. Consent
collected after opening a link cannot authorize its first booking invitation.
No schema, dependency, deployment, real messaging or release control changed.
Bounded patient AI clarification, matching Core/CosmaBot packages, Acuity,
SMS/STOP/reminders and clinical/operational release acceptance remain full-goal
requirements. The original workspace has not been retired.

## Invitation status on the provider dashboard

The authenticated owner can now explicitly read the latest invitation intent,
including before the patient opens the interview. The closed internal GET
contract supplies recorded state and bounded timestamps, flags an obsolete
credential or inactive preparation context, and distinguishes expired sending
and submission windows from a recorded terminal outcome. It exposes no mailbox,
bearer, ciphertext, consent evidence, answers or provider operation identifier.
No decryption, enqueue, maintenance, retry or transport call occurs on a read.

Current normalized owner, subject, practice, BAA, seat and cohort authority is
checked under the existing locks and again after the content-free read audit.
An audit or acknowledgment failure returns no snapshot. Public and channel
controls do not prevent an otherwise authorized owner from reading the retained
record. Support view and withdrawn owner authority deny the read.

The dashboard loads status only on explicit request. Selecting it clears a
previous clinical package; selecting a clinical package closes status. Scope,
preparation, access and visibility changes discard old and late snapshots.
Provider acceptance is not delivery, an uncertain attempt must not be resent,
and a failed read does not establish a failed email. The panel explicitly states
that delivery automation is not connected. No browser persistence or polling
is introduced.

Focused synthetic verification passed 34 backend/API/browser checks and 50
frontend checks. Actual PostgreSQL waits cover BAA withdrawal, rotation,
expiry and a worker's committed acceptance; final seat expiry rolls back the
read audit. Desktop and mobile browser cases use the built actual components,
real API and fake transport, without real messages. Screenshots were inspected
with no horizontal overflow. Read-only integration fixtures close only their own
invented pending outbox rows between tests. Exact final regression and revision
evidence belongs in the draft PR.

This reuses current Core authority, outbox, audit, generated SDK and dashboard
patterns; no new cross-repository code is copied. No schema, dependency, worker
registration or release control changes. Qualified prior-consent intake,
evidence retention, creation/booking composition, dispatch registration,
monitoring and controlled provider acceptance still remain. The full goal also
retains patient AI clarification, shared Core/CosmaBot packages, Acuity,
SMS/STOP/reminders and clinical/operational release acceptance.

## Shared package read within a receiver transaction

Core's existing dashboard synthesis getter now uses
`read_visit_prep_synthesis_in_session`. The shared domain function reacquires
the same current owner authority, preparation lineage, contribution and artifact
in the caller's SQL session. It flushes the content-free synthesis-read audit
and rechecks time-dependent access without opening another session, committing,
rolling back or invoking a model. The dashboard wrapper still commits before
returning its unchanged response.

The returned snapshot is staged private data, not an authorized external
response. A future receiver must perform its own permission and final Core
authority checks and commit successfully before disclosure. Later denial or an
uncertain commit requires discarding the snapshot. This allows receiver and
package audits to share one transaction; it does not grant a CosmaBot capability
or reuse Session Prep permission.

Independent synthetic PostgreSQL checks compare the exact package, source and
artifact versions with the dashboard for complete patient and partial caregiver
reports without chart links. Observers see no read audit before the caller
commits. Enclosing denial rolls it back; a committed operation with a lost
acknowledgment discloses nothing. Actual correction and owner-authority lock
waits verify retained custody and refreshed sources. Expiry after audit staging
fails closed, and stale ciphertext is never decrypted. Existing denial tests
also exercise the shared read directly. Final revision-specific regression and
implementer review are recorded in the stacked draft PR.

Reuse is limited to the existing Core package, artifact and audit domain. No
schema, route, wire contract, identity configuration, release control or
cross-repository source changes. The distinct Core Visit Prep receiver and
matching CosmaBot adapter remain unimplemented. Their preparation-scoped
authorization must support a new patient without fabricating a chart or encounter;
the existing encounter-bound Session Prep mount does not provide that authority.
The broader patient AI, delivery, scheduling, SMS/reminder, retention and release
milestones remain open.

## Draft CosmaBot package contract

The [proposed companion read](../contracts/visit-prep-companion-v1/draft.md) and
closed request/result schemas live on this Core branch for owner review. They
are not ratified policy or an implemented receiver. The proposed read verifies
the companion workload and physician Core session independently on every call,
uses preparation-scoped ownership without requiring a chart/encounter, and wraps
the existing dashboard source/artifact projection. It grants no generation,
messaging, chart access, signing or chat-context permission.

Synthetic fixtures preserve the unconditional fifth story, original/current
wording, missing/skipped state, caregiver attribution and versioned citations.
Offline AJV checks exercise closed objects, bounds and stale-content exclusion;
Python checks compare fields with the actual Core models and reuse the existing
source/brief grounding validators, including corrections and all-skipped input.
The AJV runtime is explicitly supplied from existing local test dependencies;
no dependency is added or installed. Exact reviewed revision and test totals
belong in the draft PR.

Architecture v3.5 and the current Session Prep contract remain unchanged. Owner
ratification of the companion/preparation boundary is required before receiver
implementation. This draft does not qualify live authentication, transactional
receiver authorization or actual dashboard/CosmaBot parity. The receiver, adapter,
bot actions, patient AI, delivery/booking/SMS/reminders, retention qualification
and clinical/operational release milestones remain open. All runtime controls
remain off; no merge, deployment, patient message or workspace retirement occurs.

## Internal patient clarification rules

`visit_prep_clarification_contract.py` defines a dormant internal foundation for
the approved patient AI interaction. It selects one current answered applicable
physician topic from an already-authorized package, preserving preparation,
question-set, source, revision, contribution basis and patient/caregiver role.
Original, hidden, skipped, missing and other-topic words are not selected. The
unconditional fifth story remains in the published question set.

The initial extractive organization format accepts only exact bounded spans of
the current answer or up to two explicit patient clarification turns. Clarifying
questions come from four neutral fixed prompts, not model-authored medical
advice. Unknown fields, fabricated words, absent sources, repeated/overlapping
spans, repeated prompts and excess UTF-8/character bounds are rejected. This
mechanical grounding does not prove clinical interpretation quality or authorize
source text as instructions.

An explicit patient confirm/edit decision produces an uncommitted intent, not a
saved answer or proof of consent/identity. A fresh current-source comparison
rejects intervening corrections, review or scope changes. Keeping the original
or dismissing an unavailable AI does not alter the report. Story/list intent can
feed the existing explicit patient correction workflow; choice values remain
unchanged, and supplemental explanations require a separately qualified source
write. Current-source equality is not authorization or durable patient-action
evidence.

Synthetic checks use real Core package projections and existing patient correction
commands, including caregiver attribution, manual fallback, conditional topics,
Unicode bounds and original-word preservation. Exact reviewed revision and test
totals belong in the draft PR. No conversational model, cross-repository adapter,
public route, storage/migration, patient UI, audit/usage integration or release
control is added. Conversational ownership remains with the existing service;
Core does not acquire a second chatbot runtime. Durable clarification/action
evidence, patient UI, the ratified patient service boundary, clinical quality,
CosmaBot, booking/email/SMS/reminders and release qualification remain open.

## Patient correction within a caller transaction

`correct_contribution_in_session` stages the existing patient-owned correction
without opening another session, committing or rolling back. It reacquires the
ordinary current public-interview and recipient authority, preserves original
wording and exact correction replay, and rechecks current authority and database
time after the write. The existing patient correction API delegates to it and
still acknowledges only its own successful commit. Newly revealed conditional
answers and patient/caregiver attribution retain their existing behavior.

This permits a future clarification writer to store its exact presented proposal,
actual patient decision and correction together. A staged receipt is not a saved
answer or qualified confirmation evidence. The enclosing writer must perform its
own final authority checks after later work and discard the receipt on denial or
uncertain commit. This change does not grant conversational, bot or consent
authority or supply that writer.

Independent connections to owned synthetic PostgreSQL verify that the correction
and a synthetic transaction marker remain invisible until the caller commits,
and that later denial rolls both back. The marker is deliberately not represented
as real AI confirmation evidence. Checks cover caregiver attribution, exact
replay, final rollout/entitlement denial, competing edits held behind the caller's
locks, and stale sources or withdrawn BAA after actual lock waits. An acknowledged
commit lost to the caller produces no saved receipt and permits only exact replay.
Final revision-specific regression and implementer review belong in the draft PR.

Reuse stays within the existing Core contribution, authority and encrypted-source
domain. No schema, route, wire contract, dependency, model/provider call, release
control or cross-repository change occurs. Durable clarification/action evidence,
choice supplemental sources, patient UI, the ratified conversational boundary,
clinical quality, CosmaBot, qualified booking/email/SMS/reminders, retention,
monitoring and clinical/operational release acceptance remain required.

## Encrypted clarification proposal and decision storage

Two dormant Core tables retain an exact bounded proposal and one final decision.
The encrypted proposal binds the current single-topic answer, neutral clarification
turns, quoted reply, question identity, contribution basis, recipient role,
credential generation and model/prompt profile. A separate encrypted decision
binds the exact proposal fingerprint and explicit confirm, edit, keep-original or
dismiss choice. Plaintext, ciphertext and keyed fingerprints are excluded from
ordinary model representations and serialization.

The generated additive migration checks current preparation, credential and
source lineage under database locks. A text acceptance must reference the exact
next attributable correction for that topic; keeping or dismissing requires an
unchanged contribution basis. Database time is captured after lock acquisition.
Both streams are immutable, cannot be truncated and delete only through the
existing aggregate retention and legal-hold boundary. Recovery leaves the expand
structures dormant and uses a forward fix; no existing column or guard changes.

Synthetic PostgreSQL checks cover patient/caregiver attribution, exact encrypted
round trips, duplicate decisions, stale or different-topic sources, actual lock
waits for corrections/rotation/quarantine/expiry, rollback, named truncate guards,
retention, holds and an approved synthetic aggregate cleanup. A populated
pre-expand rehearsal compares every column of existing patient, synthesis,
usage and email streams before and after migration. Existing response-retention
tests include the new referencing tables so they still reach the original guard.
Exact final regression and reviewed revision belong in the draft PR.

These storage guards and codecs do not verify a model, authenticate a patient
action, prove browser receipt or compare correction plaintext with the accepted
proposal. The next writer must freshly authorize the patient, load the saved
proposal rather than accepting a client replacement, and commit exact decision,
matching correction and audit together before acknowledging success. Choice
supplemental sources, patient UI and the ratified conversational-service boundary
remain open. Reuse stays within Core's existing encrypted contribution domain;
no route, wire contract, dependency, provider call, runtime flag, deployment or
retirement changes. CosmaBot, booking/email/SMS/reminders, retention qualification,
monitoring and clinical/operational acceptance remain full-goal requirements.

## Patient clarification decision transaction

The dormant patient command loads an encrypted saved proposal by its ID under
ordinary current public-link and recipient authority. Its input is an explicit
confirm, edit, keep-original or dismiss choice, never replacement proposal text
or client-supplied model evidence. Confirming or editing a text topic commits the
exact matching patient correction, encrypted decision and minimized audit in one
transaction. Original patient words remain intact. Keeping or dismissing records
the choice without changing the answer. No saved receipt is returned before an
acknowledged commit.

Patient and provider paths now reuse the same metadata-only source-version
builder after their distinct authorization checks. Exact retries require the
same proposal, operation and patient choice, a matching authoritative audit and
an unchanged resulting contribution. Later corrections or review invalidate the
old receipt instead of implying that it describes today's answer. Current link,
recipient, credential, legal/entitlement and source authority are rechecked after
later transaction work using database time.

Owned synthetic PostgreSQL checks exercise all four decisions, patient/caregiver
attribution, text topic kinds, private-before-commit visibility, lost commit
acknowledgment, competing identical/different choices, mismatched correction
words, rollback and actual lock waits across correction, quarantine, credential
rotation, BAA withdrawal and expiry. A final entitlement expiry after projection
also denies and rolls back. Failure explicitly rolls back even when the request
reuses an enclosing session and its caller catches the error. Direct storage
fixtures without this command's audit cannot be replayed as qualified command
receipts. Final commit-specific results
and implementer review belong in the draft PR.

Reuse is limited to Core's current correction transaction, encrypted evidence,
source projection and audit store; no external code or new schema is introduced.
This is not a model qualification, browser-presentation proof, verified identity,
consent or an end-to-end AI interaction. Proposal creation/turn persistence,
choice supplemental sources, patient routes/UI and the ratified Sovereign
boundary remain required. CosmaBot, booking/email/SMS/reminders, retention
qualification, cleanup, monitoring and clinical/operational acceptance remain
in scope. No runtime flag, provider call, deployment or retirement changes.

## Patient review of saved clarification proposals

The patient API now reads the latest current undecided text-organization proposal
for one applicable physician topic. It freshly checks the invitation, recipient
first name and patient/caregiver role before loading encrypted evidence. It
compares the exact current source context and commits a content-free read audit
before returning the current wording, supplied clarification turns and proposed
wording. Hidden, missing, skipped, coded-choice, stale or already decided proposals
are not offered as current wording. A decided latest proposal does not reveal an
older alternative. No proposal or model call is created by this read.

The separate explicit decision endpoint connects the existing atomic command:
confirm, edit, keep the current answer or dismiss. Only a saved proposal ID and
patient choice enter the command; replacement model/context evidence is rejected.
Both POST routes retain header-only bearer authority, bounded bodies, strict
fields, redacted errors, no-store/no-referrer and the existing confirmation limits.
The public client uses closed validation independently of the account SDK; the
actual public OpenAPI is checked because internal SDK generation excludes it.

The saved-answer review screen offers proposal review only for answered text
topics. It shows the current answer and supplied clarification words alongside
the proposal, requires an explicit choice and retains the original answer. An
uncertain choice freezes further editing and retries the exact payload and UUID.
Acknowledged choices reload Core's current contribution; mismatched receipts,
stale views, concealment and support-view denial cannot retain or restore private
wording. When no proposal is available, manual editing remains usable.

Owned synthetic HTTP checks cover all four decisions, patient/caregiver roles,
authority and support-view denials, stale/hidden/other-topic exclusion, strict
requests, existing rate limits, audit rollback and lost acknowledgment. Actual
PostgreSQL waits exercise a competing correction and BAA withdrawal. Built
desktop/mobile acceptance uses the real component, API and encrypted storage,
including explicit confirmation/editing and exact retry after a committed choice
loses its acknowledgment. Synthetic screenshots were inspected at both sizes
without clipping or horizontal overflow. Exact final regression, build, type and
implementer-review evidence belongs in the draft PR.

This completes review and saving of already retained wording proposals, not the
end-to-end patient AI interface. The initial proposal save operation is described
below. Durable turn capture, choice supplements, the ratified Sovereign connection
and clinical quality remain open.
The read audit is server access evidence, not proof that the browser displayed
the words or that the patient understood them. First-name confirmation is not
verified identity or consent. No new schema, dependency, runtime control, model
call, message, deployment or retirement is introduced. CosmaBot, qualified prior
consent, Acuity/email/SMS/reminders, retention/cleanup, monitoring and clinical
and operational acceptance remain full-goal requirements.

## Initial clarification proposal persistence

Core now saves an initial bounded reply against the exact current answered
physician topic. The private domain operation accepts no claimed patient turns
and makes no model call. It checks current public, practice and recipient
authority before loading encrypted source words, and compares the complete
source context after acquiring the normal authority and preparation locks.
An answer correction, review event, role or version change invalidates a reply
prepared from an older source.

The proposal and its exact source, reply and model-profile provenance are stored
in the existing encrypted immutable evidence structure. A content-free authority
audit commits with the proposal before Core returns a value-free receipt.
The profile records provenance; it is not conversational-service qualification.
An identical operation retries the same proposal without a second audit. A
divergent operation reuse, superseded proposal or already decided proposal cannot
return an acknowledgment implying that it is still available for current review.
A new explicit request can retain a new proposal without mutating the earlier
evidence. The existing patient decision command now also rejects a superseded
proposal from an older review screen, even when its underlying answer is unchanged.

Initial text organization connects to the existing patient read and explicit
confirm, edit, keep or dismiss API. The original patient answer is retained and
any accepted wording is an attributable correction. A bounded clarification
request or unavailable reply can be retained without modifying the answer, but
the current review screen does not yet present a follow-up question. Organization
cannot turn a coded choice into narrative text.

Owned synthetic PostgreSQL checks cover patient and caregiver roles, exact
replay, substitutions, current original or corrected sources, declined and
superseded proposals, commit visibility, rollback in an enclosing request,
lost acknowledgment and concurrent identical requests. Real lock waits exercise
correction, wrong-recipient quarantine, BAA withdrawal, token rotation and link
expiry. A final database-time check exercises entitlement expiry during a save.
The real public API verifies initial-proposal review and all four decisions.
Exact final commands, results and implementer review are recorded in the draft PR.

No new schema, route, model runtime, transport, dependency, release control or
live service is introduced. Durable patient turns, choice supplements, the
ratified Sovereign boundary and clinical quality remain required before this is
an end-to-end patient AI interaction. CosmaBot, qualified prior consent,
Acuity/email/SMS/reminders, retention/cleanup, monitoring and clinical and
operational acceptance remain the full approved goal.

## Durable patient clarification answers

Core now records actual patient follow-up words separately from model proposals.
The patient command accepts only a saved proposal ID, explicit words and an
operation UUID. It freshly checks current bearer, recipient, practice and source
authority before decoding evidence. Only an undecided current neutral
clarification request can receive an answer. Original wording and coded choices
are unchanged; capture is not final answer acceptance, identity or consent.

The generated additive migration retains encrypted immutable turns and adds a
nullable source-turn reference to proposals. Existing proposal envelopes and
fingerprints are preserved. Root locks, current credential and contribution
checks, fresh database time, two-turn bounds, immutable guards and aggregate-only
deletion protect storage. A patient turn and content-free audit commit together;
lost acknowledgment permits only the same exact action to replay. Divergent,
superseded, decided or newly unauthorized actions cannot overwrite the evidence.

The private follow-up proposal writer reconstructs context from recorded patient
turns and their authoritative audits. Claimed or substituted words, prompts,
another preparation's turn, missing evidence and obsolete chains are rejected.
The exact turn reference is bound into the encrypted proposal manifest. Later
text organization connects to the existing patient review and atomic confirm,
edit, keep or dismiss commands, preserving the original answer. Ancestor evidence
is verified even after the accepted correction changes the current source.

Owned synthetic PostgreSQL checks exercise patient/caregiver attribution, text,
list and coded-choice capture, two turns, exact retries, competing requests,
private commit visibility, rollback and lost acknowledgment. Real lock waits
cover corrections, BAA withdrawal, wrong-recipient quarantine, credential rotation
and link expiry; a final entitlement expiry rolls back the staged turn and audit.
Retention, legal holds and mutation/truncate rejection are checked; aggregate
purge is also checked with a linked follow-up proposal. A populated pre-expand
rehearsal preserves every existing column in the selected patient and
clarification streams. Final revision-specific regression and implementer review
are recorded in the stacked draft PR.

Reuse remains within Core's existing encrypted evidence, source, correction and
audit domains. No conversational model, service grant, patient-turn route/UI,
provider transport, new dependency or release control is introduced. Follow-up
presentation/capture, choice supplements, the ratified Sovereign connection and
clinical quality remain required for the end-to-end AI experience. CosmaBot,
qualified prior consent, Acuity/email/SMS/reminders, retention/cleanup operations,
monitoring and clinical/operational acceptance remain the full approved goal.

## Patient followup capture and recovery

The saved-answer screen now explicitly reads a retained neutral follow-up and
saves the patient's response through separate private POST operations. Core
checks the current recipient, source, credential and practice before decoding
the actual encrypted turn chain, and commits an access audit before disclosure.
The closed read contract distinguishes an unanswered prompt, an answered prompt
and unavailable work. The save returns only a receipt bound to the proposal,
turn sequence and contribution basis. No model is invoked by either operation.
The existing wording-review contract remains unchanged.

An acknowledged save displays exactly the submitted words without an extra
request or changing the original answer. An uncertain save freezes editing and
retries the same words and UUID. The patient can instead explicitly check retained
evidence without resubmitting; that recovery clears the old snapshot before
loading current answers and follow-up evidence. Visibility, support-view and
source changes discard private snapshots and late responses. Coded choices show
their published labels; an explanation does not change the chosen code.

Synthetic desktop and mobile browser journeys use the actual production-bundled
UI, API, PostgreSQL and isolated Redis. They cover both patient and caregiver,
lost save acknowledgment, exact retry, subsequent source-bound wording review,
explicit acceptance, final contribution review and retained original wording.
No invented reply is described as Sovereign qualification. HTTP checks cover
two actual patient turns, unavailable and superseded work, closed requests,
private failures, lost read acknowledgment and real correction/BAA lock waits.
The test helper now uses unique documentation-range transport peers because
imported and pytest-collected module instances otherwise reuse local counters.
Runtime rate limits have not changed. Exact revision-specific regression,
type/build checks and implementer review belong in the stacked draft PR.

The two-turn test exposes a release gap rather than proving a usable full AI
session. Even without automatic post-save reads, loading answers, reading and
saving two prompts, reading and accepting wording, and refreshing answers use
the eight confirmation requests allowed in fifteen minutes. Final contribution
review is the ninth request and receives a private 429. Extra checks or retries
can reach that limit earlier. A separately reviewed continuation design must
preserve guessing limits and current authority while supporting the agreed
multi-topic interaction; simply raising or bypassing the existing limit is not
part of this increment. This is not a two-turn operational acceptance result.

Reuse stays within Core's existing patient evidence, audit, private client and
review components. No schema, dependency, service grant, model call, provider
message, deployment or release-control change is introduced. Choice supplements,
the Sovereign connection, clinical quality, CosmaBot receiver/adapter, qualified
prior consent, Acuity/email/SMS/reminders, retention/cleanup operations,
monitoring and clinical/operational acceptance remain full-goal requirements.

## Appointment relative delivery timing

The new pure timing module evaluates the agreed booking email and SMS,
T−48-hour unopened SMS and T−12-hour unfinished SMS. The quiet-hours and
daylight-saving helpers and test concepts are adapted from CAB
`01ad97536c4f6730e30cd8a03dd31cdb07a918fb`,
`cosmaneura_billing/cosmaneura_billing/chase/scheduling.py` and
`cosmaneura_billing/tests/test_chase_scheduling.py`. Modified-source notices
and the retained Apache-2.0 license remain with the implementation. No CAB
application, insurance policy, dependency, credentials or database is imported.

Visit Prep thresholds use elapsed UTC hours before the appointment rather than
CAB's 0/1/3/5 local-calendar-day cadence. Quiet hours and minimum channel spacing
are explicit inputs; no enabled default policy is introduced. Retries check
current local quiet hours, with real instants through repeated hours, spring
gaps, non-hour transitions and skipped calendar days. The timing function never
returns a moment before the due/current instant or permits work at or after the
appointment or credential deadline.

Short-notice invitations omit thresholds already past when the current
invitation became effective. On outage recovery, booking comes first and the
later reminder replaces an obsolete T−48 reminder; quiet-hour delays cannot
release both together. Existing current-generation intents prevent new intents
for the same step, including failed and uncertain outcomes. Pending and uncertain
submissions hold subsequent work on that channel. Potential submissions from
prior generations still impose spacing after rotation; obsolete queued work
does not become current. The future durable scheduler must independently cancel
that obsolete work and serialize intent creation.

Tests cover the timing boundaries and hourly recovery invariants, plus real
Core creation, patient/caregiver confirmation and all-skipped completion,
rotation, reschedule and cancellation observed from an independent PostgreSQL
connection. Those lifecycle checks found that Core does not currently retain
every first-party open: `get_interview` does not prove an invitation unopened.
The test-only projection therefore supplies unknown open evidence. Unknown
evidence omits the unopened reminder; actual confirmation/progress prevents it.
An authoritative first-party open event remains required for the full cadence.
Rotation preserves prior aggregate interaction history rather than resetting it.

The result is timing advice, not a send capability, consent grant or dispatch
authorization. No schema, API, worker, job registration, provider adapter,
environment setting or release control changes. The separately reviewed
current-evidence loader, durable scheduling/dispatch and race checks, prior
consent, SMS/STOP, Acuity integration, first-party open instrumentation,
monitoring and operational acceptance remain implementation requirements.
Exact tested revision, commands and implementer review belong in the draft PR.

## First party invitation open evidence

Core now records creation coverage and the first successful first-party interview
resume in its existing append-only authority audit store. The creation marker
commits with the new aggregate and credential, never on a creation replay or a
historical backfill. The resume event commits with the actual resolved interview
snapshot, after current bearer, owner, practice, legal, entitlement, rollout and
expiry checks. Authority is rechecked after audit persistence, which may wait.
No patient-data table, route, frontend request, identity/session change or release
control is introduced.

The bounded reader distinguishes an instrumented invitation with no flow event,
a retained first open, and unknown historical evidence. Direct confirmation or
completion proves interaction but does not manufacture a resume event. The
original flow event survives rotation and rescheduling; a later review can occur
after direct completion without restarting delivery. Invalid or duplicate
evidence cannot prove an invitation unopened. Readers must already hold current
Core scope and preparation/lineage locks; this projection grants no authority.

The event records only fixed labels, database timestamps and canonical opaque
owner, preparation, schedule and credential references. It contains no names,
contacts, answers, bearer, bearer hash, request content, invented person identity
or consent. A successful bearer request is not evidence that the intended patient
read the screen, and neither tracking pixels nor analytics are used.

Owned synthetic PostgreSQL checks cover patient/caregiver visits, actual creation
replay, historical unknown evidence, direct confirmation/completion, exact
retries, concurrent opens, lost acknowledgment, enclosing rollback, malformed
evidence, rotation and rescheduling. Actual lock waits exercise BAA withdrawal,
rotation and wrong-recipient reporting; expiry after a real audit flush rolls
back the observation. Real HTTP and production-bundled desktop/mobile patient
journeys assert the retained first flow event. Exact revision-specific results
and implementer review belong in the stacked draft PR.

Recovery leaves existing audit evidence retained. Removing the writer must also
disable any future unopened-reminder consumer. After an uninstrumented interval,
old coverage cannot be reused as proof of continuous observation; keep affected
invitations unknown until explicitly reconciled. No audit deletion or fabricated
backfill is a rollback. Durable scheduling/dispatch, qualified prior consent,
SMS/STOP, Acuity, session continuation, Sovereign/CosmaBot, cleanup, monitoring and
clinical/operational qualification remain full-goal requirements. All controls
stay off and no external message is sent.

## Current cadence evidence and timed email handoff

The new Core planner loads the current owner, preparation, schedule and
credential under the established transaction locks. It reads actual first-party
flow evidence and all retained email intent metadata, including older pending,
accepted and uncertain submissions. It checks canonical appointment/patient
links when present and supports an unlinked preparation without inventing a
chart. The history query selects no private envelopes; no contact, answer,
bearer or consent is decrypted by the planner. Missing lineage, a mismatched
schedule or excessive history denies planning rather than hiding records.

The timed email command connects that plan to the existing atomic outbox
handoff in the same transaction. Quiet hours and spacing return a wait without
creating an intent. New due work still requires the existing qualified consent
source, current scope and channel controls. Original exact-key replay checks
remain in force after rotation, revocation, quiet-hour changes or a lost commit
acknowledgment. Encryption and audit persistence can cross a time boundary, so
new work receives a final database-clock cadence check before commit; a failed
check rolls back the intent and its audit. No recurring job or route is added.

This connection reuses Core's existing authority, lineage, email outbox and
handoff code, together with the already-adapted CAB timing rules. No new
cross-repository copy, dependency, patient-record schema, security/session
change, provider adapter, environment setting or release control is introduced.
The planner returns timing advice, not transferable permission. SMS history is
explicitly unavailable because no authoritative SMS outbox exists yet; the
email table cannot establish that no SMS was submitted.

Owned synthetic checks cover the actual lifecycle, all six email states,
rotation/rescheduling, prior submission spacing and uncertainty, quiet-hour
waits, exact replay, competing handoffs, lost commit acknowledgment, rollback,
canonical schedule mismatch and real BAA/worker lock waits. Time-dependent
authority is rechecked after evidence reads. Bounded malformed-history and
quiet-boundary diagnostics supplement real database tests; they are not proof
of operational storage repair or real-provider qualification. Revision-specific
commands, results and implementer review belong in the stacked draft PR.

The full goal still requires durable scheduler/dispatch timing, booking-event
integration, qualified prior consent, SMS/STOP and reminder storage, callbacks,
monitoring, cleanup, session continuation, Sovereign/CosmaBot integration and
clinical/operational qualification. All runtime controls remain off. No merge,
deployment, patient message or workspace retirement occurs.

## Email cadence at claim and dispatch

The dormant email worker now requires explicit quiet hours and minimum channel
spacing before claiming work. Missing, invalid or coerced policy disables the
worker. These are additional trusted Core restrictions, not consent or a sending
capability. They are bound to process-local claim custody; dispatch with missing
or changed restrictions suppresses the claim without reopening its send lane.
No email job, provider, policy resolver or enabled default is installed.

Claim and dispatch load current Core cadence under the existing owner,
preparation, lineage and delivery locks. The full history is validated before
excluding only the candidate's exact current invitation. Older potential sends,
pending work and uncertain submissions remain in the timing decision. The
history query does not decrypt another invitation. Current source, approval,
credential and lease expiry are checked after timing reads; final claim checks
follow the real audit flush. Dispatch reevaluates timing immediately before the
bounded transport attempt while the existing authority fences remain held.

A quiet-hour or spacing wait leaves a queued intent, its operation key and its
secret unchanged. The worker checks other candidates in its existing bounded
batch. It suppresses work with no permitted appointment window. If a committed
claim is no longer permitted at dispatch, it consumes that claim, suppresses the
submission and clears the transient secret. It never returns a committed claim
to queued or retries an uncertain provider effect. The generic existing
suppression code does not expose a new policy-specific delivery status.

Reuse stays within Core's current cadence loader, timing rules, outbox and
single-submission worker. No new cross-repository transfer, schema, route,
dependency, service grant, auth/session change or release control is introduced.
Owned synthetic tests exercise schedule-local quiet hours, preserved waiting
intents, a later due candidate in the same batch, changed policy, old-generation
pending/uncertain sends and spacing, current-intent mismatch, real appointment
and source expiry during lock/query waits, claim audit rollback and value-free
history. Quiet-boundary clock diagnostics supplement those actual database
checks; they do not prove a real-provider or deployed scheduler qualification.
Exact revision-specific commands, results and implementer review belong in the
stacked draft PR.

The outbox still does not persist cadence policy. A qualified policy resolver
or separately reviewed persistence design and a durable scheduler are required
before installation; this worker input is not their substitute. Acuity booking,
authentic prior consent, SMS/STOP and reminder storage, callbacks, monitoring,
cleanup, usable session continuation, Sovereign/CosmaBot integration and clinical
and operational qualification remain requirements of the full goal. All controls
remain off, with no merge, deployment, live message or retirement.

## Email recovery health

The new internal health reader reports aggregate queued, submitting, accepted,
failed, suppressed and uncertain counts from the retained email outbox. One
PostgreSQL statement uses one materialized database clock and reads only metadata;
it never selects private envelopes, contacts, token verifiers or owner identifiers
into its result. It takes no sending locks, writes no audit or lifecycle state,
and refuses an ambient global transaction without flushing or releasing it.
Storage failure returns only `storage_unavailable`, not invented zero counts.
No endpoint, worker task, scheduler, runtime configuration or service grant is
registered by this increment.

Separate recovery counts identify expired queued secrets, obsolete queued
context, and submissions beyond either their lease or secret expiry. Obsolete
context includes completion, wrong-recipient quarantine, revocation, old
credential/schedule generations, expired credentials, past appointments, and
the latest consent or physician approval withdrawal. Expired and obsolete
counts can overlap. Health visibility remains available with release gates off
or BAA authority withdrawn; it grants no permission to send or disclose a
patient package and does not change retained legal holds.

Queue age is advisory, with timing explicitly `unqualified`: a quiet-hour or
channel-spacing wait is not proof of a stalled worker. Accepted remains distinct
from delivered. An overdue submission is potentially sent and cannot be retried
from these counts. Retained uncertain outcomes remain attention-worthy; failures
remain counted, but only those finalized within the last fifteen minutes affect
recovery status. This telemetry window is not a cadence, retry deadline or
activation threshold. An `ok` recovery snapshot does not prove that a consumer
or provider is running.

Owned synthetic checks use actual enqueue, claim, dispatch, consent withdrawal,
patient completion, wrong-recipient reports, rotation, rescheduling and secret
maintenance. Independent connections verify committed-only visibility and a
nonblocking health read while submission holds owner fences. Real secret expiry
and lost provider acknowledgment are exercised; exact lease and recent-failure
boundaries use explicitly labeled diagnostic clocks. Query observation verifies
one metadata read, and retained rows, encrypted evidence and legal holds stay
unchanged by health inspection. Exact final-source checks and implementer review
belong in the stacked draft PR.

Reuse stays within current Core's durable outbox, immutable lifecycle metadata
and content-free operations pattern; there is no new cross-repository transfer
or schema change. Durable cadence policy and scheduling, authentic prior consent,
Acuity, SMS/STOP/reminders, receipt reconciliation, monitoring installation and
operational alert acceptance, consent retention/aggregate cleanup, usable patient
session continuation, Sovereign/CosmaBot and clinical qualification remain open.
All release controls remain off. No merge, deployment, live message or retirement
is authorized by this increment.

## Prompt cleanup of obsolete email secrets

The existing dormant `maintain_email_secrets` service now selects queued
invitations whose appointment has passed, whose credential was revoked or
expired, or whose schedule generation is obsolete. Previously, a queued
invitation with a past appointment could retain its transient payload until the
later secret expiry even though dispatch would refuse it. A real-clock owned
PostgreSQL regression reproduced that gap before the fix.

Candidate selection reads existing lifecycle metadata and remains only a routing
hint. After the established practice, preparation and delivery custody locks,
the service refreshes current schedule and credential facts and database time
before suppressing work. An older active invitation cannot consume a bounded
batch slot needed by a past-appointment invitation. A future invitation waiting
for quiet hours is not erased. A concurrent reschedule preserves the new intent;
a regrant that makes the selection stale causes a fresh check rather than an
automatic suppression.

Only the transient invitation payload is cleared through the existing terminal
state guards and atomic content-free audit. Authorization ciphertext, original
answers, corrections, review events, brief artifacts, consent/approval evidence,
schedule/credential history, recipient snapshot and legal holds remain intact.
Cleanup works with release gates off or BAA authority withdrawn. It neither
changes clinical retention nor authorizes a consent-evidence or aggregate purge.
A past appointment alone does not finalize a live submission claim, which could
already have an external effect. Submission expiry still means uncertain, never
permission to send again.

Owned integration checks use actual rescheduling, a short future appointment,
PostgreSQL time passing, source capture/correction/review, encrypted brief
storage and legal holds. Independent connections exercise competing maintainers,
rescheduling and consent regrant during real lock waits, and committed versus
uncommitted lost maintenance acknowledgments. They also verify refusal of an
ambient unflushed audit. The regression retains historical terminal evidence;
it does not disable guards, invent old timestamps, truncate tables or call a
real provider. Exact final-source validation and implementer review belong in
the stacked draft PR.

This is a focused correction of Core's existing maintenance service, not a new
cross-repository transfer, schema, worker registration, scheduler, provider or
configuration change. Installed cleanup and monitoring, qualified consent
retention/aggregate purge, durable cadence policy, booking/email/SMS/STOP/reminder
automation, usable patient session continuation, Sovereign/CosmaBot integration
and clinical/operational acceptance remain full-goal work. All release controls
remain off; no merge, deployment, live message or workspace retirement occurs.

## Value free retention health observations

Core now has an internal `visit_prep_retention_operations.runtime_health` read
for aggregate retention and incident-review observations. It reports unused
invitations with expired but unrevoked current credentials, cleanup deadlines
reached, the ninety-day ceiling reached, and overdue unused records under active
legal hold. Confirmation establishes clinical retention even without answers or
after all-skipped completion. Quarantine is excluded from ordinary clinical
retention counts and uses its separate recorded incident disposition. Elapsed
clinical or incident deadlines are review evidence, not purge authorization.

The read uses one PostgreSQL statement and one materialized database clock. It
selects the greatest schedule and credential generations without multiplying
roots by historical generations or legal holds. Missing current lineage remains
visible rather than disappearing from counts. Results contain only fixed keys,
counts and bounded status values; no identities, incident references, contacts,
tokens, answers, briefs or encrypted envelopes are returned or selected. An
ambient transaction is refused without flushing or changing it. Storage failure
returns only `storage_unavailable`, never invented healthy zeroes or diagnostics.

Owned tests cover actual confirmation, partial and all-skipped contributions,
wrong-recipient reports, revocation reasons, rescheduling and guarded incident
dispositions. Exact thirty-day, ninety-day, credential, clinical and disposition
boundaries use explicitly labeled diagnostic clocks over real immutable metadata;
they do not age records or prove cleanup. A short incident deadline also crosses
under the actual PostgreSQL clock without erasure. Independent connections
verify nonblocking committed-only visibility during confirmation, hold placement
and the actual quarantine command. A real later transaction releases a hold
before another is placed. Completed answers, corrections, review, brief, email
consent/approval evidence and legal holds remain unchanged with release controls
off and BAA authority withdrawn. Revision-specific checks belong in the draft PR.

This implements an operations requirement using existing Core metadata. It adds
no schema, policy duration, deletion path, service grant, route, scheduler,
provider or monitor registration and copies no new cross-repository code.
`retention_qualification` remains `not_qualified`; an `ok` snapshot proves neither
cleanup execution nor operational readiness. Aggregate and consent-evidence purge
qualification, installed monitoring/cleanup, usable patient session continuation,
Sovereign/CosmaBot, booking/email/SMS/STOP/reminder automation and clinical/release
acceptance remain incomplete. Nothing is merged, deployed, sent or retired.

## Centrally managed standard reminder settings

John approved the four pending implementation decisions on October 2, with one
initial patient identity affirmation, attributable explanations and answer
updates, central read-only provider timing, and the CosmaBot read boundary. Every
patient AI analysis must use Core's existing PII/PHI safeguards. These decisions
are recorded in the integration plan; this increment implements the central
timing setting, not the remaining patient-session or companion work.

The owner-approved standard is 21:00–08:00 quiet hours in the current appointment
timezone and four hours between messages on the same channel. It uses Core's
existing configuration mechanism, with a version and fingerprint covering all
timing rules. All practices share the standard. Partial or malformed overrides
make standard timing unavailable rather than select a permissive fallback. No
provider write API, policy table, migration or environment file change is added.

Standard enqueue and worker entrypoints load the same central settings; callers
cannot supply quiet-hour or spacing overrides to those entrypoints. Actual
current schedule, consent, authority, expiry and uncertain-send checks remain in
the existing locked domains. A policy change during enqueue rolls back the new
intent; a changed/missing policy before submission consumes and suppresses the
claim without a provider call or reopening the send lane. The final check also
protects standard claims routed through the lower-level dispatch function.

The authenticated invitation-policy read displays the central version, quiet
hours, channel spacing and agreed reminder offsets as provider-read-only fields.
Existing physician invitation approval is separate from this timing display and
still never creates recipient consent. Desktop/mobile synthetic browser checks
exercise the actual components and API, including no provider timing inputs.
Owned PostgreSQL checks cover quiet waiting, actual prior-send spacing across
credential rotation, policy changes, gated sends, source-verification changes,
enqueue rollback and ambient-transaction refusal. Configuration and client tests
cover strict parsing, immutable settings, malformed overrides and private UI
failures; revision-specific results belong in the stacked draft PR.

The scheduler, authentic booking/consent adapters, SMS/STOP/callbacks, patient
continuation, confirmed coded explanations, protected Sovereign analysis,
CosmaBot receiver/adapter, retention qualification and clinical/release acceptance
remain incomplete. Settings availability is not sending authorization. Runtime
controls stay off; nothing is merged, deployed, sent to a real recipient or retired.
