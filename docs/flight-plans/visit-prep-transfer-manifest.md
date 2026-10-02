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
or an invitation send. Real patient/trusted-source consent capture, qualified
evidence retention, atomic enqueue, dispatch, cleanup and delivery status remain
open. Email and public release controls remain off. Final commit-specific checks
and implementer review belong in its stacked draft PR.
