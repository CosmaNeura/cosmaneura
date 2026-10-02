# Visit Prep migration and integration plan

Approved by John — October 1, 2026

This plan moves useful Visit Prep work onto a fresh branch of the current Cosmaneura Core repository. The product delivers a discrete package of information from the patient to the provider before the visit, so the first five minutes can focus on the patient’s priorities rather than reconstructing the reason for the visit. Its secondary purpose is to establish a patient-facing AI interaction and make the resulting content useful to authorized downstream agents.

John approved this plan on October 1, 2026 and directed implementation, with one explicit clarification: one of the five physician-owned topics must invite an open-ended, free-form patient narrative. Implementation is authorized on the fresh integration branch. Deployment, real patient messaging and retirement retain their separate approval gates.

## Product outcome and proposed scope

The unit of delivery is a Visit Prep package, not a chatbot transcript. It should contain the patient’s own responses, a concise synthesis, questions or priorities for the visit, unanswered areas, and enough provenance for a provider or agent to distinguish patient report from model interpretation.

The five physician-owned topics are visit reason, prescribed medications, adherence, barriers when relevant, and an open-ended patient narrative. The final topic is unconditional free-form text, never a choice list. The physician can phrase it as “Tell me your story,” “Tell me about your medical history,” or “Tell me what happened since the last time you saw me.” Preserve the patient's own words alongside any synthesis. Multiple versioned question sets, one physician default, caregiver attribution, skip/resume, and a new patient without an existing chart remain requirements.

The approved AI direction is a bounded assistant within those physician-owned topics: it may help clarify or organize an answer, and the patient can correct or confirm its interpretation before submission. It must not independently introduce a broad clinical interview, diagnose, give treatment instructions, or imply real-time provider monitoring. Free-form narrative entry does not depend on the AI being available. Keep conversational model ownership consistent with the platform architecture; recovering deterministic question capture does not authorize a parallel chatbot runtime in Core.

Retain the five published question identities during clarification. Bind clarification turns and patient-approved corrections to the relevant answer and its revision history rather than generating an unrelated question set. This is additional product work beyond the old fixed-question implementation and needs its own tests.

The primary provider experience remains a scannable package on the Cosmaneura dashboard. CosmaBot will use the same authoritative package and lifecycle through APIs. Patient answers should improve downstream assistance without becoming signed clinical facts, executable instructions, or permission to take external actions.

This updates the older plan’s blanket exclusion of downstream consumption. It does not require combining Visit Prep and Session Prep generation: Visit Prep summarizes this patient’s pre-visit contribution; Session Prep retains its separately governed chart context. Both can be presented and used with their source labels intact.

## Repository destination and preservation

The current Visit Prep checkout already uses `CosmaNeura/cosmaneura` as its remote. The move is from stale local checkouts and branches to a clean, current branch in that same main platform repository.

Approved destination:

- Repository: [CosmaNeura/cosmaneura](https://github.com/CosmaNeura/cosmaneura).
- Base: freshly fetched `origin/master`, the actual default branch. Record its exact SHA when work begins; do not branch from a local directory merely because its name contains “master.”
- Integration branch: `codex/visit-prep-integration`.
- Proposed new checkout: `/Users/jw/Desktop/Cosmas/cosmaneura-visit-prep-integration`, subject to the required filesystem access. It must be outside the retiring VisitPrep workspace.
- Source checkout: `/Users/jw/Documents/ChatGPT/VisitPrep/cosmaneura-visit-prep`, currently at `d80a37012526d96991db25920797457c392231a8`.
- Canonical implementation plan: `docs/flight-plans/visit-prep-integration.md` in the new branch. This workspace copy records the approval and is retained as migration provenance.

The existing desktop checkout `/Users/jw/Desktop/Cosmas/cosmaneura-visit-prep` contains substantial uncommitted work, including Session Prep and authority changes. The older `cosmaneura` checkout in this workspace is also dirty. Neither will be reset, overwritten, used as a clean base, or automatically included in the transfer. Inventory relevant uncommitted changes and ask before adopting overlapping work.

The integration branch should contain small, reviewable commits and be reconciled with current `master` at each milestone. Record each upstream synchronization and rerun affected tests. Once the branch is shared, avoid rewriting its history without agreement. Keep release controls off until the approved activation gate; keep the default branch unchanged until the work is ready for the agreed merge sequence.

“Live on the branch” initially means runnable, demonstrable code with an isolated local synthetic preview. Current Core rules prohibit deployment of an unmerged branch and restrict canonical staging to selected merged candidates. A cloud branch preview would need a separately agreed release-policy path; this plan does not authorize bypassing those rules or replacing another feature’s operational-test deployment.

## Evidence and reuse decisions

Source readiness is not deployment readiness. The following inventory is based on source and recorded test evidence, not a fresh qualification of live services.

| Source and observed state | Reuse decision | Necessary adaptation |
| --- | --- | --- |
| Old Visit Prep feature stack. Question APIs/editor, preparation aggregate and commands, creator UI, response storage, interview service, rate limits and tests exist. Only the plan, release controls and question schema have landed in current Core. | Selectively port the missing behavior and its tests. Start with the clean committed stack. | Use current authorization, transactions, application registration and frontend conventions. Generate migrations against the current graph; do not duplicate already-merged tables or import obsolete migration parents. |
| Archived Visit Prep patient UI and brief work: `QuestionForm`, `InterviewFrame`, `AnswerList`, structured-brief generation and tests. | Adapt the useful UI and synthesis behavior. | Replace legacy Intake storage/routes with the new Visit Prep aggregate. Do not copy the TanStack prototype application or revive legacy credential assumptions. |
| Current Core Session Prep: `session_prep_runtime.py`, worker, generator, operations and race tests; current dashboard recovery components. | Reuse durable generation and UI lifecycle patterns. Extract small common helpers only where warranted. | Keep Visit Prep sources, artifacts, authorization and retention distinct. Chart retrieval must not become a prerequisite for a new patient’s package. |
| CAB Card Chase: `chase/acs_email.py`, `scheduler_store.py`, `worker.py`, `worker_config.py`, `scheduling.py`, policy fingerprinting and corresponding tests. | Adapt the narrow email transport, durable dispatch, approval checks, quiet-hours handling, sanitized errors and uncertain-send recovery. | Replace insurance-specific policy, templates, direct-contract gates, data models and cadence with Visit Prep rules. Do not import the CAB application or database. |
| CAB SMS policy and sequence support. The inspected real transport and worker support email only. | Reuse channel/consent concepts and relevant tests; treat an SMS provider adapter as an unresolved implementation item. | Verify any newer SMS branch or local implementation before selecting it. Still require consent provenance, STOP processing, suppression, delivery receipts and operational acceptance. |
| CosmaBot Visit Prep role, encounter-source review, Session Prep brief adapter and explicit reviewed-context chat selection. | Extend the existing bot role and adapter pattern; do not create a second Visit Prep bot or clinical store. | Add a distinct Visit Prep contract and capability. Existing Session Prep permission is not permission to create or send Visit Prep. Address unlinked/new-patient preparations explicitly. |
| Scheduling current-member discovery, appointment choices, booking identity and idempotency patterns. | Integrate through normalized appointment events and existing authorized identity mappings. | Acuity remains the first agreed customer adapter unless this review changes that priority. Calendar reads alone are not a booking-event feed. |
| CosmaBot virtual clinic, clinician workflow and recovery tests. | Add Visit Prep cases using real UI components and isolated synthetic data. | UI simulations supplement, but do not replace, real database, model-boundary, email/SMS and cross-client acceptance tests. |

CAB’s email adapter uses an outbox-owned operation UUID, disables engagement tracking, sanitizes SDK errors, and reports “accepted,” not “delivered.” Its worker preserves uncertain submissions rather than blindly sending again. These are valuable patterns. CAB’s insurance-term filter is not a general clinical-data detector: Visit Prep messages need narrow, allowlisted template fields and separate tests preventing answers, diagnoses and bearer secrets from leaking into logs or inappropriate message content.

CAB’s [email-only activation plan in PR 38](https://github.com/CosmaNeura/Cosmas-CAB-Tool/pull/38) remains open and explicitly does not authorize activation. Its recorded synthetic tests are useful evidence, not proof of a live messaging service or reusable production credentials.

For every transfer, keep a short manifest recording source repository, exact commit, selected files/behavior, destination, adaptation, tests and attribution; preserve applicable license notices. Prefer an existing supported service contract when one really exists; otherwise copy/adapt the small component with its tests. Do not create a new shared cross-repository package merely to begin this feature. Never transfer credentials, environment files, patient data, deployment state or unrelated changes.

## Shared package and API design

Define these contracts early, before committing to the dashboard layout or bot integration:

1. **Preparation lifecycle.** Owner/practice, optional canonical patient and appointment links, question-set version, appointment time/timezone, credential generation, invitation and interview state, and delivery state. Creation must work without manufacturing a patient record.
2. **Patient contribution.** Ordered answer/skip records, original wording, patient versus caregiver attribution, timestamps, corrections and confirmation state. Incomplete and wrong-recipient submissions cannot appear as complete verified information.
3. **Synthesis artifact.** A versioned concise brief, visit priorities, medication/adherence report, barriers, uncertainties and source references. Bind it to the exact response and question versions plus model/prompt version. Clearly label model interpretations and speculative flags.
4. **Authorized commands and reads.** Separate create/update/revoke/send/status/read-package capabilities. Require current practice and physician authority; service-triggered booking automation uses its own bounded authorization. Use stable idempotency and replay behavior for retries.
5. **Downstream consumption.** A versioned Core read contract supplies the permitted package and provenance, not database access. Recheck authorization, supersession and wrong-recipient quarantine before disclosure. Propagate corrections or revocation so old bot results are not silently treated as current.

Both dashboard and CosmaBot should read the same package ID and version. A patient correction produces an attributable new version; an agent must not silently overwrite the patient’s words or the provider’s record. Treat patient text as source data, never as instructions to invoke tools or bypass system controls.

CosmaBot currently centers on an encounter. Its new Visit Prep path must handle a preparation not yet linked to an encounter through an explicitly authorized preparation context, or clearly report that the operation is unavailable. It must not invent a chart link to fit the existing UI.

Initial downstream integration is CosmaBot. Subsequent explicit adapters may expose patient-reported context to Scribe, separately labeled context alongside Session Prep, and patient-stated priorities/barriers to Follow-up. CAB may consume appropriately reviewed evidence later, but a patient report or model synthesis is not an automatic billing or coding determination. These later integrations should not block the first useful patient-to-provider workflow.

## Delivery and reminder design

Bring the CAB reuse work forward so invitation delivery is developed alongside the main journey, rather than discovered after the dashboard is finished.

The minimum delivery boundary is provider-neutral: a Core transaction records an invitation and outbox intent; a worker rechecks current authorization, consent, suppression, schedule generation and channel gates before dispatch. Link secrets remain encrypted for only the bounded delivery window. Neither dashboard nor bot calls the provider directly.

Adapt CAB’s email implementation first behind a synthetic sink and explicit email gate. Build or integrate SMS behind its separate gate once a concrete supported adapter is verified. Use Visit Prep-specific physician-letter/email and minimal SMS templates. Borrow mechanics, not CAB’s insurance language or 0/1/3/5-day cadence.

Preserve the previously agreed booking email plus SMS, T−48-hour SMS for unopened preparation, and T−12-hour SMS for unfinished preparation, subject to this review. Define “opened” using the first-party preparation flow rather than email tracking pixels. Specify behavior for short-notice bookings, quiet hours, daylight-saving changes and two reminders becoming due together; do not send a burst of overdue messages. Completion, cancellation, reassignment, wrong-recipient reports, opt-out and token rotation must cancel obsolete work.

Show distinct queued, provider-accepted, delivered when confirmed, failed, suppressed and uncertain states. A lost provider response is not evidence that nothing was sent. Add authenticated, idempotent receipt/callback handling and an operator recovery path without promising exactly-once delivery across external providers.

## Proposed implementation sequence

Each milestone ends with a working demonstration and a concise evidence update. These are delivery increments, not a new stack of prerequisite documents.

| Milestone | Work after approval | Exit evidence |
| --- | --- | --- |
| 1. Establish the current branch | Refresh Core, inventory dirty sources without changing them, create the clean integration branch, copy the approved plan, record source/reuse manifest, and run the current baseline locally. Pin package/command contracts and the agreed patient AI behavior. | Correct remote/base SHA, preserved old work, repeatable local startup, baseline failures separated from new failures, synthetic contract fixtures. |
| 2. Recover the patient journey | Port question authoring, preparation creation, credential/response lifecycle and patient UI. Adapt current authority and migration patterns. Add agreed clarification/confirmation behavior without expanding beyond the approved interview scope. | Physician creates a preparation; patient or caregiver opens, answers/skips, resumes and submits; new-patient and wrong-recipient cases work. Copy-link demonstration is available. |
| 3. Deliver the first useful package | Adapt synthesis to current Core worker infrastructure and surface partial answers plus the completed package on the dashboard. Develop CAB email/outbox adaptation alongside this milestone. | Traceable patient-to-provider package, no duplicate brief under retries, meaningful first-five-minute clinician review, and email flow verified against the synthetic sink. |
| 4. Connect CosmaBot and delivery | Add the Core Visit Prep receiver before the matching CosmaBot adapter. Extend the existing role with authorized preparation/status/package actions. Qualify controlled email delivery separately from synthetic tests. | Dashboard and bot show the same version; changed permissions/context deny access; new-patient behavior is explicit; delivery acceptance and uncertainty are honestly displayed. |
| 5. Complete customer automation | Integrate the approved booking adapter, SMS transport, consent/STOP/suppression, reminder scheduling, reschedule/cancel handling, cleanup and operational alerts. | Full synthetic MCD journey plus controlled provider tests, including duplicate events, stale schedules, outages and recovery. Manual fallback remains usable. |
| 6. Qualify release and retire old workspace | Reconcile with current Core and deployed service contracts, run regression and usability checks, prepare reviewable merge increments and the receiver-first release plan. Complete the approved cohort rollout before retiring redundant local work. | Exact tested commits/artifacts, accepted migration and rollback evidence, cross-client acceptance, approved activation, preserved source history and explicit retirement scope. |

Milestone 4 requires a small coordinated code change in the CosmaBot repository as well as Core. This means repository integration work, not contacting or interrupting another Codex chat. The existing no-cross-task-coordination instruction remains in force.

## Acceptance and safety checks

The primary product test is whether the package helps a provider begin the visit already understanding the patient’s main concern, intended agenda, medication/adherence report and relevant barriers. Do not claim a measured five-minute saving merely because the interface works.

Proposed acceptance checks:

- A provider can scan a completed package in roughly 45 seconds and accurately describe the patient’s main priorities. Patient-origin statements are traceable to their actual answers; model inference is distinguishable and there are no material invented facts in the reviewed test cases.
- Patients can correct the AI’s interpretation if that interaction is approved. Skipped information remains missing rather than filled in. Caregiver reports and partial preparations are visibly labeled.
- The same preparation can be opened on the dashboard and through CosmaBot with matching version and source references. A change in patient, practice, user, permission or source version cannot disclose or reuse the wrong package.
- Test interrupted interviews, concurrent requests, expired/rotated/revoked links, duplicate completion, stale workers, model failure, all-skipped completion and a new patient with no chart.
- Rehearse additive migrations on isolated synthetic PostgreSQL; verify the current single-head graph. Run real concurrency tests and the relevant frontend, contract, lint, type and build checks. Reuse test cases, not historical pass counts.
- Test message retries and uncertain submission, duplicate callbacks, STOP/consent withdrawal during queued work, cancellation/rescheduling, timezone boundaries and obsolete reminder suppression.
- Keep retention/legal hold, quarantine, encrypted contact/answers, public rate limits, no-store/no-referrer, token redaction and cleanup requirements. Core’s existing public-proxy and maintenance-lock concerns must be checked against the current implementation before activation.
- Measure invitation dispatch, first-party open/start/completion, package generation, provider opening before the appointment and provider usefulness feedback. Keep clinical content and bearer credentials out of operational metrics. Add queue age, generation failure and cleanup/delivery failure signals.

Use current repository validation and release rules. Some repository documents describe disabled GitHub Actions, so a historical or absent CI result must not be reported as fresh validation. Code review must be tied to the final source revision; any review requiring another Codex task needs explicit user permission under the standing isolation instruction.

Before an external rollout, preserve the existing gates unless this review explicitly revises them: synthetic owner testing, controlled send testing, the complete synthetic MCD path, named-physician beta of at least five preparations observed for at least 72 hours, then approved MCD activation. Neither borrowing CAB code nor having a working bot grants a BAA, channel approval, new service identity or permission to contact real patients.

## Completion and retirement

The migration is complete when all selected work, tests, provenance and ongoing documentation live in the current integration branch; both delivery surfaces use the same service; the agreed operational acceptance is satisfied; and no build or runtime depends on the old local workspace.

Before retiring anything, produce a retained/discarded manifest and preserve relevant uncommitted work and unique history. Confirm the destination is backed up remotely through an approved push and that documentation no longer relies on files available only in the retiring checkout. Legacy public links and retained records must remain supported through their agreed compatibility/retention window.

Retirement means ending development in and eventually archiving the obsolete Visit Prep checkout/project with explicit approval. It does not mean deleting the shared `CosmaNeura/cosmaneura` repository, deleting unrelated CAB or Session Prep work, removing production resources, or recursively deleting the current workspace. No retirement action is authorized by approving ordinary implementation milestones.

## Approved decisions and remaining gates

John approved the overall sequence on October 1, including the shared package/agent contract, early CAB delivery adaptation, and the five topics with an explicitly open-ended narrative. The initial preview remains local and synthetic. Acuity-first, booking email plus SMS, the two reminder rules and existing MCD acceptance gates remain in scope.

The branch starts from current Core and receives selective transfers, not a bulk merge. Cloud deployment, real patient messaging, clinical cohort activation and retirement still require their separate decisions. The September 25 owner policy in `/Users/jw/Desktop/Cosmas/AGENTS.md` permits applicable local checks and implementer review; independent review is not a default gate and cross-task isolation remains in force. The current user instruction to retain the work on a branch until ready takes precedence over the general merge-by-default policy.

### Owner implementation decisions October 2 2026

John approved the following four implementation decisions, with these modifications. They authorize branch work and synthetic qualification, not deployment or activation.

- Patients affirm their identity once. Secure remembered confirmation must support re-engagement through the original invitation and established email/phone/browser continuity without repeated identity prompts. Contact values and a shared IP are not independent access credentials. Preserve invitation expiry, revocation, wrong-recipient quarantine and current practice authority; do not merely raise guessing limits.
- Patients may explain and update their answers. Preserve original responses and attributable revisions. Store confirmed explanations alongside the exact coded-answer revision, encrypted and separately attributed; do not silently replace a selected code with narrative. Include changes in package freshness.
- Message timing is a central, standard practice setting managed by Cosmaneura and read-only to providers, not a provider-editable policy or a new per-preparation policy table. Scheduling and dispatch must consume the same versioned settings. John approved 21:00–08:00 quiet hours in the current appointment timezone and at least four hours between messages on the same channel. The future first-party scheduling system may replace the settings source. Malformed or partial overrides disable standard dispatch; having valid timing settings does not enable a release/channel gate or grant consent.
- Implement the proposed CosmaBot package-read contract with independent workload and current physician authentication, explicit physician action, chart/encounter-free preparation support, the same Core package and provenance, and no durable companion clinical copy. This is the first read-only increment, not a narrowing of the approved subsequent agent capabilities.

Every AI analysis of patient information, including clarification, synthesis and later bot reasoning, must follow the current Core PII/PHI rules: minimum necessary input, the existing de-identification boundary and approved model/provider controls, no credentials or contact metadata in prompts, value-free logging, and centralized usage evidence. A companion display read does not invoke a model. Failure to establish the protected model boundary must stop analysis while preserving deterministic patient entry and authorized source review.

### Remembered patient confirmation implementation

The branch was reconciled with Core `03689fe24115d37af4479171d3e6b7a982cf68e5` before this increment. Patients confirm the recipient name and role once in a browser for the current invitation. An encrypted, host-only Secure/HttpOnly/SameSite cookie remembers that confirmation without retaining the invitation bearer, contact details, name, answers or IP. A clean reload or reopening the original invitation uses the cookie; repeated answer review, clarification, edits and email choices no longer request the name. This is remembered recipient affirmation, not independently verified identity or channel sending authority.

Each operation rechecks the original credential generation, confirmation timestamp and role, database-time expiry, schedule, quarantine and current physician/practice authority under the existing locks. Cookie-only access requires a non-simple continuation header and the configured origin boundary; malformed or ambiguous requests cannot substitute a remembered cookie. Initial confirmation guessing limits stay unchanged. Returns use the ordinary request limits instead of repeatedly consuming the identity-guessing bucket. Same-tab invitation changes clear the previous patient state, remove the new fragment from the address bar and reload the matching preparation rather than reusing another invitation's content.

Focused synthetic qualification passed 118 backend/browser checks and 99 patient UI checks. These include actual desktop/mobile components, real browser cookie persistence, patient/caregiver journeys, preserved original answers, optional consent and lost acknowledgments, malformed/duplicate/cross-origin headers, expired/rotated/revoked invitations, wrong-recipient quarantine and current practice-authority denial. No real patient data, model call or message transport was used. The complete app builds with invented settings without loading local environment files; existing framework source-map/future-flag warnings remain. The full feature regression is recorded against the final commit in the PR evidence. There is no schema/data migration in this increment.

For repeatable isolated browser acceptance, build from `web/` with `env -i PATH=/opt/homebrew/bin:/usr/bin:/bin NODE_ENV=production COSMANEURA_BUILD_TARGET=test node node_modules/@react-router/dev/bin.js build . --config scripts/visit-prep-synthetic-build.config.mjs --mode test`, then run both `node scripts/build-visit-prep-patient-harness.mjs` and the existing `node scripts/build-visit-prep-package-harness.mjs`. A complete-app rebuild removes earlier test bundles, so both harnesses must be rebuilt afterwards. The patient harness bundles the actual components and substitutes an inactive support-view subscription only in that synthetic harness; the complete-app browser tests and service tests still exercise the real support-view boundary. Run backend tests through `scripts/run_visit_prep_checks.py --with-redis`, never a shared database/service configuration.

This increment remembers one current invitation in that browser. A replaced invitation or missing cookie does not silently inherit access from an email, phone number or shared IP. Cross-device/contact continuity, code-bound narrative explanations, actual protected AI analysis, CosmaBot integration and operational delivery qualification remain work under the full approved goal. No deployment, live patient communication, release activation or workspace retirement is authorized by this increment.

## Source references

- [Original Visit Prep requirements, issue 135](https://github.com/CosmaNeura/cosmaneura/issues/135) and [approved old-stack plan](https://github.com/CosmaNeura/cosmaneura/blob/d80a37012526d96991db25920797457c392231a8/docs/flight-plans/visit-prep.md).
- Core source inspected at `bc9da3478de0e03bacac0b81495ea43956c51279`: [current engineering/release rules](https://github.com/CosmaNeura/cosmaneura/blob/bc9da3478de0e03bacac0b81495ea43956c51279/AGENTS.md), [Session Prep runtime](https://github.com/CosmaNeura/cosmaneura/blob/bc9da3478de0e03bacac0b81495ea43956c51279/app/services/session_prep_runtime.py), and [companion receiver](https://github.com/CosmaNeura/cosmaneura/blob/bc9da3478de0e03bacac0b81495ea43956c51279/app/routes/companion_session_prep.py).
- Archived reusable UI/brief commits in Core: `0d92405f` for patient interview UI, `b164ad7a` for structured brief, and `092f6aa0` for dashboard answers. These are adaptation candidates, not current production components.
- CAB source inspected at `01ad97536c4f6730e30cd8a03dd31cdb07a918fb`: [ACS email transport](https://github.com/CosmaNeura/Cosmas-CAB-Tool/blob/01ad97536c4f6730e30cd8a03dd31cdb07a918fb/cosmaneura_billing/cosmaneura_billing/chase/acs_email.py), [worker](https://github.com/CosmaNeura/Cosmas-CAB-Tool/blob/01ad97536c4f6730e30cd8a03dd31cdb07a918fb/cosmaneura_billing/cosmaneura_billing/chase/worker.py), [scheduling rules](https://github.com/CosmaNeura/Cosmas-CAB-Tool/blob/01ad97536c4f6730e30cd8a03dd31cdb07a918fb/cosmaneura_billing/cosmaneura_billing/chase/scheduling.py), and [adapter tests](https://github.com/CosmaNeura/Cosmas-CAB-Tool/blob/01ad97536c4f6730e30cd8a03dd31cdb07a918fb/cosmaneura_billing/tests/test_acs_email.py).
- [CosmaBot architecture](https://github.com/CosmaNeura/cosmaneura-os/blob/main/docs/COSMABOT_ARCHITECTURE.md), [Session Prep connection contract](https://github.com/CosmaNeura/cosmaneura-os/blob/main/docs/core-session-prep.md), and [internal preparation work](https://github.com/CosmaNeura/cosmaneura-os/blob/main/src/cosmas_os/application/prep_work.py). These links track main; pin the selected revisions before transfer.
- [Scheduling companion discovery contract](https://github.com/CosmaNeura/cosmaneura-scheduling/blob/main/docs/adr/0035-companion-read-only-discovery.md) and [CosmaBot clinician test harness](https://github.com/CosmaNeura/cosmaneura-os/pull/65).
