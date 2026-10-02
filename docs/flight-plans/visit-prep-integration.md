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

This increment remembers one current invitation in that browser. A replaced invitation or missing cookie does not silently inherit access from an email, phone number or shared IP. Cross-device/contact continuity remains work under the full approved goal. No deployment, live patient communication, release activation or workspace retirement is authorized by this increment.

### Patient explanations for selected answers

Patients and caregivers can explicitly save, edit or withdraw a free-form explanation for an answered coded topic. The explanation is encrypted, attributed and retained as separate append-only contribution evidence tied to the exact current answer revision. It does not change the selected code or overwrite the original answer. Changing or skipping that answer removes the prior explanation from the current package, even if the new revision selects the same code; earlier evidence remains retained. Explanations do not add a sixth physician topic.

The patient review screen and authorized provider package show the current explanation separately. Its source identifier is distinct from the coded answer, so synthesis can cite the patient's actual words without pretending they came from a choice label. Saving or withdrawing an explanation changes package freshness and invalidates prior review. The new public mutation uses the established recipient-confirmation, current-authority, request-limit, origin and private-response boundaries. Lost acknowledgments reuse the same request key; replaying an older save after a choice change cannot attach the old explanation to the new revision.

Migration `6c5c8f775016` expands the existing contribution event table after `65dee3dec2d0` with a nullable revision reference and explanation guards. It does not rewrite original response ciphertext or remove records. Its rollback policy is a forward fix, not dropping retained evidence. Synthetic PostgreSQL qualification covers preserved originals, separate source attribution, updates and withdrawal, stale writes, concurrent choice/review/explanation commands, exact replay and denied direct mutation. A populated forward-expansion rehearsal passed with existing encrypted answers unchanged. Full regression evidence belongs with the final review commit.

This increment is deterministic patient entry and source review, not live AI analysis. Confirming an AI-proposed explanation for a coded answer, CosmaBot integration, cross-device/contact continuity and operational delivery qualification remain unfinished milestones. The synthesis privacy boundary is described below; future conversational and bot analysis must use equivalent Core protections. All model and delivery release gates remain inactive; no real patient data, live model call, external message, deployment or retirement was used for qualification.

### Protected synthesis input and output

Visit Prep synthesis now uses Core's existing Azure de-identification service and configured identity, with the stable `2024-11-15` Surrogate operation. One framed request carries only the current physician prompts, current answers and separately attributed explanations. It excludes retained original or hidden answers and does not add record IDs, credentials, contact metadata or timestamps. Patient-entered identifiers within that text go to the approved de-identification boundary, not directly to the reasoning model. Original patient wording remains available through the authorized source package.

The optional bounded Core call disables retries and request-body logging, uses connection/read timeouts, and rejects results that return at or beyond the 12-second analysis budget. These transport settings and elapsed-time checks are not a guarantee that identity acquisition or an unresponsive SDK can be interrupted at exactly 12 seconds; the existing job limits remain the outer execution boundary. Malformed framing, oversized input/output, provider errors, invalid protected text or a late result stop analysis. There is no raw-input fallback. Empty, all-skipped input requires neither provider nor model calls.

The worker holds no patient database locks during de-identification. It then rechecks current sources, practice authority and its lease before recording the central model-usage intent and calling the existing bounded Azure model client. Model output must cite wording present in both the protected input and the original current response. Changed word spans are conservatively excluded from quotes and narrative, so a substituted name or date cannot become a patient fact merely by citing an unchanged symptom phrase. This is mechanical grounding, not clinical validation or proof of complete identifier detection.

Encrypted synthesis artifacts retain the exact protected input and protection profile alongside the original source manifest and brief. Publication, replay and authorized reads enforce the same validation. New generator/prompt versions prevent older unprotected-profile artifacts from being treated as current; retained records are not rewritten or deleted. There is no schema migration or public API shape change in this increment.

Synthetic qualification exercises the actual Azure SDK serialization and stable operation through a local simulated transport, including a non-retried provider failure. It also covers failed protection before any model call or usage intent, current-source lineage, separate explanations, concurrent corrections and authority changes, substituted-value rejection, and encrypted evidence recovery. Full regression results are recorded with the final review commit. No real patient information or live provider was used. Live Surrogate framing behavior and representative clinical fidelity remain operational acceptance work before activation, along with protected conversational clarification and subsequent bot reasoning.

## Companion authentication implementation October 2 2026

John authorized the architecture amendment. Architecture v3.6 and ADR 0028 are
ratified at `6b879150205f275612d1a3fd2318530dfa331857` through
[architecture PR 21](https://github.com/CosmaNeura/cosmaneura-architecture/pull/21).
The narrow exception permits Core-owned chart-free Visit Prep display in CosmaBot,
not a parallel clinical store, conversational runtime or generic tool permission.

This Tier 2 authentication increment adds a separate default-off
`COMPANION_VISIT_PREP_ENABLED` policy using the existing configured companion
identity. Only that app-only Entra caller with `AgentApi.Access` and the correct
tenant, audience and issuer is admitted. Physician verification is a designated
Clerk adapter using only the configured issuer's public signing keys. Both
adapters return normalized identity and verified credential time bounds for the
receiver's final database-time check. Neither accepts the browser test bearer,
cookies, support impersonation or a pending physician session.

Public signing-key reads send no credential and use bounded transport with no
redirect, proxy environment or retry. Existing Scribe, Sovereign and Session Prep
auth paths are unchanged. These transport limits do not replace the receiver's
15-second outer deadline or prove immediate interruption of a blocked thread.

Receiver-first sequence remains: qualify this auth foundation; implement the
caller-owned package/audit transaction and private wire boundary; then implement
and qualify the CosmaBot display adapter. No route, model call, schema migration,
environment mutation, live processing or activation is added by this increment.
Recovery is to leave the new flag unset/off and revert this additive commit;
existing auth behavior remains unchanged. Credential expiry, persisted view-as,
current reviewed connection and practice/preparation authority must still be
checked by the package receiver before commit and disclosure. Application work
stays on the integration branch until ready; deployment remains founder-only.

Local synthetic signed-token and existing identity/workload regressions and
implementer review are recorded at the final commit in the stacked draft PR.
This is not independent review, GitHub approval or operational acceptance.

## Core package receiver implementation October 2 2026

The next increment implements `POST /service/v1/companion/visit-prep/package`.
The route verifies both signed identities, resolves the current reviewed
provider connection, and uses the dashboard's same-session package reader.
Chart-free patient and caregiver preparations require no invented encounter.
Current answers remain available when the protected brief is missing or stale.

The package read and content-free companion-open audit commit together. The
receiver rechecks current authority and credential expiry after lock/audit waits;
failed, uncertain or late operations do not return the staged package. The outer
HTTP boundary replaces arbitrary errors/headers with the fixed private wire and
does not expose the endpoint in browser OpenAPI. No model or delivery is invoked.

One focused owned-database run passed the complete signed-JWT HTTP path and
transaction recovery checks (37 checks). Implementer review, type checks and
the exact commit are recorded in the stacked draft PR. This is branch-level
qualification, not activation or live cross-client acceptance. Recovery remains
flag-off plus revert of this additive increment. The matching CosmaBot display
adapter is now implemented in draft PR 93; actual live shared-client acceptance
remains unfinished. Application merges and deployment remain outside this increment.

## Preparation picker implementation October 2 2026

The next paired branch increment replaces manual preparation references in
CosmaBot with an explicit owner-scoped picker. Core shares its dashboard list
domain under the existing companion dual-auth/current-connection boundary.
The list includes only invitation first name/role, appointment timing, lifecycle,
chart-link status and selector metadata. It includes no contact details, patient
answers or model output. Package opening independently rechecks current access.

The picker paginates older preparations and clears private selection metadata
on connection/scope/focus changes. Names are invitation identifiers, not verified
patient identity. Branch contract details are in the existing companion contract;
canonical architecture reconciliation of this additive list wire remains a
release gate. Recovery is flag-off and revert of the additive selector increment.
No migration, environment activation, external service call or real message is
part of this work. Connected patient-to-provider/device/delivery acceptance and
broader governed bot actions remain open; the full delivery goal is unchanged.

## Patient submission handoff October 2 2026

Patient completion now stages a durable synthesis job in the same transaction
as the completed interview. Confirming updated answers stages the replacement
source version. Unconfirmed edits do not trigger analysis. This closes the gap
where a provider had to request the initial brief manually; explicit provider
generation remains available for its existing incomplete or unreviewed sources.

The implementation reuses Core's queue, protected analysis, usage ledger and
atomic publication. It adds no model call to the patient request, no text to
broker jobs, no chart writes and no duplicated package store. The encrypted job
envelope identifies automatic work separately from provider requests, while
remaining compatible with existing envelopes. A worker rejects automatic work
if the patient changes their contribution without confirming it. The separate
`VISIT_PREP_AUTO_SYNTHESIS_ENABLED` control defaults off and is also checked by
the worker before analysis and publication. No environment setting was activated.

Focused synthetic qualification covers initial patient and caregiver completion,
retries without duplicate jobs or changed leases, confirmed revisions, unconfirmed
edits, stopping queued automatic work and rollback after a late gate failure.
The connected check runs the existing worker with an invented AI transport and
compares versions one and two through the dashboard projection and actual signed
Companion endpoint. This is not live AI, full CosmaBot transport, native-device
acceptance or messaging qualification. Exact local evidence belongs in the
stacked draft PR. The broader delivery and governed bot work remain unfinished.

Recovery is to disable automatic handoff and revert this additive increment.
Retain existing queue records; disabling does not delete sources or artifacts.
Patient submissions continue through the existing flow with automatic handoff
off. No schema, dependency, deployment or real recipient is part of this increment.

## Patient clarification protection October 2 2026

Core now selects one current patient-authorized topic for conversational work,
including follow-up words reconstructed from saved patient-command evidence.
Protection reuses the existing Core clinical de-identifier and verified text
frames, with no raw fallback. The Sovereign payload contains only the protected
prompt, patient words, role, topic kind and at most two follow-up turns. Record
identifiers, contacts, credentials and other topics are excluded.

The paired Sovereign branch adds a generator using its existing metered model
client. It asks only unused neutral follow-up codes or returns exact patient
excerpts; it supplies no new clinical prose. The generator requires the dormant
clarification switch, Azure model configuration, BAA attestation and an active
canonical usage context with a durable receipt for its call. These checks are
controls, not proof of service coverage or patient authority.

Core maps accepted excerpts only through unchanged text blocks, accounting for
offsets shifted by de-identification. Substituted identifiers and surviving
fragments inside changed words are rejected. An invented-source check exercises
protection, mapping, saved proposal and explicit patient confirmation through
the existing correction command. Existing batch protection remains supported.

Those private functions are now connected by the branch increment described
below. The companion display contract itself still grants no analysis permission.

## Patient requested AI action October 2 2026

The patient review screen now has a default-hidden “Help me clarify this answer”
action for answered topics. Core selects that current topic, applies its
clinical protection, commits a one-use analysis request and central usage intent,
and sends only the protected words to Sovereign. A separate signed Core workload
must hold `VisitPrep.Clarify`; a provider token or generic anonymous chat cannot
enter this receiver. Sovereign claims the request back in Core before inference.
The claim rechecks current patient confirmation, contribution and expiry and
binds the exact protected payload. It creates no provider session or patient chart.

Sovereign reuses its actual model client. Core accepts content-free usage facts
only from the configured Sovereign workload. The returned suggestion must refer
to that committed successful usage result. Core freshly checks the answer again,
grounds exact excerpts and saves the encrypted proposal before the browser can
read it. Patients accept, edit, keep or dismiss through the existing explicit
decision flow. Saved follow-up answers can request another bounded suggestion;
they remain separate words until patient acceptance. Selected-answer explanation
acceptance is connected in the next increment described below.

Exact retries recover a saved proposal without another model call. An uncertain
attempt is not automatically retried; the patient can continue with the saved
answers. Current-source changes and unrecorded usage block proposal publication.
The new Core `VISIT_PREP_PATIENT_AI_ENABLED`, browser
`VITE_VISIT_PREP_PATIENT_AI_ENABLED` and existing Sovereign clarification control
all default off. Branch development has provisioned no workload identity, role,
model service, environment setting or deployment.

Generated migration `dd93e7a689f1` expands the existing ledger and adds a
preparation-owned request fence after `6c5c8f775016`. It retains original answers
and old ledger origins. Its recovery is forward-only: leave the schema dormant,
turn off the actions and revert application code if necessary. The flag is not
a schema rollback. The new wire/capability needs canonical architecture
reconciliation and controlled model/service/privacy qualification before release;
the accepted read-only companion ADR is not that qualification.

Focused local evidence uses real Core storage and signed receiver admission with
invented de-id/model transports. Live model quality, actual service coverage,
cross-device/contact continuation, Acuity/SMS/reminder completion, broader
CosmaBot actions and operational acceptance remain unfinished. No live patient
data, message, merge, deployment or retirement is part of this increment.

## AI explanations for selected answers October 2 2026

Patients and caregivers can now request help for an answered choice topic as well
as written answers. Core protects the current explanation and any actual saved
follow-up words. The selected option is context only: neither model nor Core may
copy its label into proposed explanatory prose. Without explanatory words, the
bounded assistant can ask a neutral follow-up within the same physician topic.

Explicit acceptance or editing appends an encrypted explanation through the
existing contribution stream, in the same transaction as decision and audit.
The selected code and original answer remain unchanged. Earlier explanations
are retained; changing even to the same code revision, editing an explanation or
withdrawing it invalidates an old proposal. The explanation changes the shared
package version and remains separately attributed in dashboard and bot sources.

Generated migration `208271feaef7` follows `dd93e7a689f1` and expands only the
existing decision guard to admit an exact-next explanation for a choice. It adds
no table, contact copy or alternate clinical store, and preserves existing
immutable-source checks. Recovery remains disabling the actions and reverting
application code while retaining the guard and evidence; use a forward fix for
schema recovery. Existing text-proposal envelope bytes remain readable.

This increment does not activate a model, invitation transport or environment.
Controlled clinical/model/service qualification, cross-device continuation,
Acuity booking, SMS/reminders and broader governed CosmaBot actions remain open.

## Provider email workflow implementation October 2 2026

The dashboard creator now offers an explicit email handoff behind the default-off
browser control. It queues the current invitation through the authenticated Core
API, retaining neither contact details nor bearer credentials in browser storage.
Core derives the patient origin from its own configuration and uses the existing
encrypted outbox. Exact retries recover the original operation; an uncertain
acknowledgment must not become a replacement invitation or a new submission.

The argument-free scheduled worker uses Core's existing bounded SMTP adapter,
current physician approval and a verifier for the actual recorded patient or
caregiver email choice. `VISIT_PREP_PATIENT_EMAIL_SOURCE_QUALIFIED` defaults false
and is not itself consent, identity proof or service approval. The worker rechecks
current permission and centrally managed quiet hours and spacing before its single
submission. Queued work waits during quiet hours without retaining the bearer in
the browser. Separate maintenance clears expired or obsolete transient secrets,
including after sending is disabled; retained patient and consent evidence stays.

Synthetic desktop and mobile journeys exercise the actual creator and email
controls through the provider API and scheduled worker. They show queue state and
email-provider acceptance, never confirmed recipient delivery. No live SMTP call,
environment setting, deployment or real patient message was used. There is no
schema migration in this increment. Recovery is disabling the browser action and
email send/source controls, leaving secret maintenance active, and reverting the
additive route and worker code if required.

This connects provider-initiated email after an actual patient email choice. It
does not solve the first booking invitation: consent collected after opening a
link cannot authorize that first contact. Acuity booking and prior-contact consent
provenance, SMS transport and STOP processing, automated reminders, cross-device
continuation, broader CosmaBot actions and operational acceptance remain open.

## Acuity booking handoff implementation October 2 2026

The next stacked branch adds a default-off route for signed Acuity scheduled
notifications. It verifies the signature against the original bounded request
bytes, then retrieves the current appointment through Acuity's fixed API origin.
The adapter keeps only the recipient contact and appointment timing needed for
preparation. It discards form answers and notes, creates no patient chart, and
uses an explicit calendar-to-practice-and-physician mapping rather than guessing
ownership from an email address. The calendar's configured timezone governs the
schedule; a supplied offset must agree with that timezone.

The preparation uses the physician's published default five-topic set. Creation
and a booking receipt commit in one isolated transaction, using existing Core
ownership, practice, BAA and entitlement checks. A connection-scoped pseudonymous
booking reference and database uniqueness fence allow duplicate notifications to
recover the same preparation. The receipt is immutable and contains no contact
details or invitation bearer. A changed or reassigned booking cannot silently
create another preparation through this scheduled-only lane.

No delivery is queued. John reported that permission for appointment-related
messages is not known to exist outside possible initial booking information.
Treat that permission as unverified: booking alone is not evidence of consent,
and a later patient choice cannot authorize the first invitation. A verified
prior-contact consent source or explicit booking field and disclosure remains
necessary before automatic email or SMS invitations can be activated. No Acuity
account, credentials, webhook registration or environment setting was changed.

Generated expansion `b3f91f1acb40` follows `208271feaef7` and adds only the booking
receipt with ownership and immutability guards. Recovery retains this schema and
evidence: disable the source and revert the additive route and adapter if needed,
using a forward fix for schema faults. A feature flag is not a database rollback.
Synthetic qualification covers the actual signed route, current-source projection,
dashboard list, duplicate and concurrent delivery, source rejection, transaction
rollback and isolation, database immutability, and a populated predecessor expansion.
Exact results and reviewed commit are recorded in the stacked draft PR.

This is preparation creation, not complete booking automation. Native Acuity
notifications provide no unique version for all future changes. Reschedule,
cancellation, reassignment and changed-contact handling still need an authoritative
lifecycle lane; invitation recovery, initial consent, ingress operational limits,
and actual source/service qualification remain activation gates. This branch is
stacked on the completed email workflow. The synchronization increments below
retain the subsequent Core/CAB history through fetched `master`
`49895cb66a305558393a0862a590ee313b60c194`, including its separate migration branch.
No merge, deployment, live patient data or real message is part of this increment.

## Current Core synchronization October 2 2026

Six bounded, stacked synchronization increments preserve the already-merged Core
billing source, button, CAB bridge, display contract, inline panel and runtime
wiring alongside Visit Prep. They retain Git ancestry through fetched `master`
`49895cb66a305558393a0862a590ee313b60c194`; no shared history is rewritten.
Generated no-op merge revisions `e47551a9d77e` and `c3aa7a31f3ae` join both published
migration branches without changing their parents. The latter is the single head.
The real graph and affected routes run in owned synthetic PostgreSQL; populated
expansion checks preserve both Visit Prep and CAB evidence. CAB and Visit Prep
provider components coexist, and the locally regenerated browser client typechecks.

The checked-in browser API snapshot now contains all 15 Visit Prep paths and their
schemas alongside current Core/CAB. The client regenerates from that snapshot;
an actual-application parity check protects against lost paths, and the complete
synthetic app builds and typechecks. Runtime configuration tests use fake vault and cloud
commands; neither environment template nor deployment script was run against a
service. Exact commits and scoped validation are recorded in the stacked drafts.
No feature activation or default-branch merge follows from this synchronization.
Retain additive schemas and evidence on recovery; use forward fixes, not drops.

## Booking lifecycle transaction implementation October 2 2026

The existing physician reschedule and revoke commands now delegate to staging
functions that retain the same ownership, lineage and state checks but never
commit. A booking handler can join the lifecycle write and its source receipt in
one caller-owned transaction, recheck source authority, then commit both together.
A failed receipt or final gate rolls back schedule and credential changes; no
replacement bearer may leave the handler before successful commit acknowledgment.
Physician commands retain their original public behavior and commit boundary.

This increment changes no route, delivery policy, stored schema or service
configuration. The signed scheduled-only handler is still unchanged: normalized
current-source change/cancel handling and retained lifecycle receipts follow next.
Synthetic qualification proves independent readers see no staged changes and both
successful commit and failed-handoff rollback preserve expected lineage. Recovery
is reverting the additive transaction refactor; no database rollback is required.

### Provider source projection

The Acuity adapter now supplies a provider-neutral Visit Prep booking projection
and a current-source recheck to the shared creation transaction. Raw appointment
and calendar identifiers, transport and source hashing stay inside the adapter.
Its digest and snapshot bytes preserve the existing receipt format, so this
refactor neither duplicates nor rewrites prior preparations. The projection is
not a patient-roster identity or a new scheduling product. It still handles only
scheduled notifications; source change/cancel processing is the next increment.
No schema, invitation, channel or service activation changes here.

### Retained lifecycle receipts

Generated expansion `ac4d76856637` follows the combined head `c3aa7a31f3ae` and
adds an append-only booking event table. Each event references its original
booking receipt, preparation owner, exact schedule and credential. Database
guards require the next source snapshot and applied current lifecycle; mismatched,
stale or unapplied receipts fail the transaction. Events retain keyed digests,
opaque references and timing, not contacts, original vendor payloads or bearers.
Existing preparation, booking and patient records are not rewritten or backfilled.
Recovery retains the additive schema and evidence and uses a forward fix. This
storage rung alone activates no notification, message or scheduling source.

### Current booking change handler

The signed `/external/v1/visit-prep/acuity/changed` lane now accepts Acuity's
changed, rescheduled and canceled notifications. It fetches the current appointment
rather than treating the action or request hash as current state. A database-time
observation taken before provider I/O rejects a late response behind a newer
committed change; Acuity must retry and re-fetch. Current snapshots are compared
with the latest retained event, so duplicate delivery is a replay and a genuine
return to an earlier schedule remains a new change.

An eligible reschedule uses the existing schedule and credential rotation and
updates the dashboard's current timing. Current cancellation accepts an
identity-only tombstone and revokes access. A changed recipient revokes the original
preparation without transferring its answers or changing its contact snapshot.
Each applied change and its event commit together, with current source and provider
authority checked after writes. No bearer is returned and no message is queued.
Synthetic checks preserve an in-progress patient's original encrypted answer and
initial confirmation, and cover current-state action mismatch, replay, late source
responses, malformed sources and rollback. Exact evidence belongs in the draft PR.

This does not complete booking automation. Returning-patient continuity for
unchanged-recipient reschedules is implemented below; replacement invitation
recovery, changed-recipient recovery and calendar reassignment remain open.
Unknown-booking cancellation ordering is implemented below. The existing
scheduled-only endpoint is retained. No source,
release or channel control is activated; recovery disables the source and reverts
the additive handler while retaining booking events and schemas.

### Completed booking rescheduling

The booking lifecycle lane can now reschedule a completed preparation without
changing the original answers, contribution history, initial confirmation,
completion timestamp or retention deadline. It appends a schedule and credential
generation and retains the prior credential's reschedule revocation. Only the
internal booking lane may recover an expired invitation; the public provider
command still rejects expiry. Retention limits, wrong-recipient quarantine and
revocation remain enforced.

When automatic synthesis is enabled, the same transaction queues a refreshed
package from already-confirmed completed answers. Unreviewed corrections do not
trigger analysis. The existing worker protects model inputs and publishes the
current source version. A late gate or storage failure rolls back the schedule,
credential, booking event, synthesis job and queue audit together.

Generated forward migration `3a7854eb39e0` changes only the completed-state
clauses of two lineage guards. An owned, populated predecessor rehearsal proves
retained patient and booking rows are unchanged and the other guard clauses are
preserved. Synthetic qualification also covers package refresh, replay, expiry,
invalid lineage, immutability and matching refreshed packages through the actual
signed Companion endpoint and dashboard projection. Exact reviewed commit and local results belong
in the stacked PR. The following increment restores remembered access across
eligible reschedules; delivery of replacement invitations remains unfinished.

Recovery retains schemas and evidence: disable automatic synthesis and the
booking source, revert the additive application increment if necessary, and use
a forward fix for a schema fault. No environment control, live source account,
message transport, deployment or real patient data was used or activated.

### Returning patient access after rescheduling

A patient or caregiver who already affirmed their identity can resume the same
preparation after an unchanged-recipient reschedule without another identity
prompt. A still-valid encrypted browser confirmation follows the retained chain
of rescheduled credentials. Reopening the original invitation, opening the new
invitation in that browser, and returning without a link all use the current
preparation. An authorized resume renews the confirmation to the current
credential expiry without changing the initial affirmation or attribution.

Every use rechecks current practice and physician authority, retention, schedule,
credential expiry and quarantine under the existing canonical locks. The old
link alone remains revoked. Explicit credential rotation, cancellation and
recipient revocation cannot be crossed, even if a later reschedule occurs.
An invitation for another preparation does not select the remembered answers.
Email, phone number and shared IP are not access credentials. The cookie contains
no raw invitation bearer, contact detail or patient answer; its envelope remains
compatible with existing confirmations. There is no new schema or clinical store.

Focused synthetic acceptance uses the actual patient components on desktop and
mobile for both patient and caregiver, including two reschedules, reopening,
explanations and confirmed corrections. Those updates are checked through the
provider's authoritative package. The real signed booking-change route is also
covered. Exact final results and reviewed commit belong in the stacked draft PR.

Missing cookies, an unrenewed expired confirmation, cross-device recovery and
changed-recipient replacement invitations remain unfinished. Continuity is
bounded to 128 retained credential generations per request. This increment does
not grant outreach consent or queue a message. Recovery reverts this additive
application change while retaining the existing credentials and evidence.
Release controls remain off; no service account, environment configuration,
live model, patient message, deployment or retirement is changed.

### Cancellation before preparation creation

An authenticated current booking cancellation is now retained even when no
preparation exists yet. Both the scheduled-only creator and current-change
creator capture database time before fetching the source and check the latest
unmatched cancellation under the current practice and owner locks. A delayed
active response cannot create an invitation after a newer cancellation; the
source must be fetched again. A genuinely fresh restored appointment can create
one preparation, so the cancellation is not a permanent ban on that booking.

Repeated current cancellation observations append a new fence rather than
reusing an older timestamp. Evidence contains only the connection, pseudonymous
booking and snapshot digests, owner scope, sequence and observation times. It
contains no patient name, contact, answers, invitation bearer or chart link.
Once a preparation exists, its existing booking-event and revocation lane remains
authoritative. A source or authority failure rolls back the new evidence.

Generated expansion `0b78b19b8d6e`, following `3a7854eb39e0`, adds only this
append-only evidence table and its own guard. The populated predecessor rehearsal
preserves retained patient, package, booking and audit rows plus existing guard
definitions. Signed synthetic checks cover delayed responses through both entry
points, repeated cancellation, fresh restoration, scope separation, immutability
and rollback. Exact final results and reviewed commit belong in the draft PR.

Recovery retains the evidence and expansion, disables the booking source, and
uses a forward fix for schema faults. Reverting to an older creator removes this
ordering protection, so the source must stay disabled during such a rollback.
Evidence cleanup must be qualified before operational activation. This increment
does not provide messaging consent, replacement invitations, SMS or reminders.
No account, environment control, live source, message or deployment is changed.

### Separate patient SMS choices

Patients and caregivers can now explicitly allow or withdraw Visit Prep invitation
and reminder texts through an optional screen, using their existing remembered
confirmation. The UI displays the permission deadline in local time; Core limits
it to the current invitation and appointment. Rescheduling preserves the original
choice without extending its deadline. Declining texts does not block answers,
and a missing mobile number makes SMS permission unavailable without collecting
a replacement number in this screen. Email and SMS permissions remain independent.

Generated expansion `5789bd0d277e` adds a separate append-only SMS consent stream
after `0b78b19b8d6e`, with encrypted, purpose-bound capture evidence and atomic,
content-free audit. Exact retries recover the original decision without restoring
a grant after later withdrawal. The current authority, recipient, disclosure,
sequence and deadline are rechecked before commit. The populated predecessor
rehearsal preserves existing records and guard definitions.

Server `VISIT_PREP_SMS_CONSENT_CAPTURE_ENABLED` and browser
`VITE_VISIT_PREP_SMS_CONSENT_CAPTURE_ENABLED` default off. This is capture evidence,
not verified phone control, qualified consent or initial-booking outreach authority.
It does not install an SMS transport, sender approval, provider STOP callback,
phone-wide suppression, delivery receipts or reminder dispatcher. Those remain
required before activation. Recovery disables capture and reverts the additive
application change while retaining consent records and schema; use a forward fix
for schema faults. Consent retention and legal-hold cleanup require qualification.
No live message, account, environment control, model or deployment is changed.

### Sender wide SMS opt out storage

Core now retains terminal STOP evidence independently of any preparation, patient
chart, practice or invitation. The scope is the canonical adapter namespace,
provider account resource and sender number, paired with a keyed recipient digest.
Preparations and practices sharing that sender share the suppression. Credential
and connection rotation must retain the same canonical resource identity; moving
a number to another provider or account requires suppression migration before
activation, not a fresh empty registry. No browser grant or reschedule clears STOP.

The optional patient screen reports a phone-wide opt-out when sender routing is
configured and rejects new preparation-specific grants. Historical consent remains
visible and exact retries retain their original receipt without clearing STOP.
Withdrawal and patient answers remain available; email choices remain independent.
Absent routing is honestly reported as not connected, and malformed partial
routing fails closed. Routing contains no transport credentials and enables no send.

Generated expansion `79601b0047dc` follows `5789bd0d277e`, adding only the STOP
table, lookup index and its own immutability guard. Evidence and its content-free
audit commit atomically after a trusted Core adapter verifies the exact normalized
source twice. The shared recipient transaction lock also fences concurrent checks;
contention and stale isolation levels deny rather than return an unsafe negative.
STOP admission does not depend on send or patient consent-capture flags. Retained
fields contain no raw number, message text, name, answer or provider credential.

No authenticated provider STOP adapter or public callback is installed by this
increment. Synthetic verifiers prove domain behavior, not provider authentication.
Actual SMS transport, callback admission and retry wiring, sender approval, delivery
receipts and reminder dispatch remain required. Future workers must hold this same
transaction through their final suppression check; absence of STOP is not consent
or sending authority. STOP record retention and legal-hold cleanup require separate
qualification and must not silently restore messaging. Recovery leaves the schema
and suppression evidence in place and uses a forward fix; no destructive downgrade.
All messaging remains disabled. No live account, message, deployment or model is used.

### Separate durable SMS queue

Core now has a separate SMS outbox for booking invitations and the 48-hour unopened
and 12-hour unfinished reminders. It uses current appointment timing and distinct
SMS history rather than inferring SMS state from email. A sender and recipient
fence also applies spacing across preparations that share the sender. Quiet hours
and at least four-hour spacing remain centrally controlled and read-only to providers.
Messages contain a private link and fixed administrative wording, not patient answers
or clinical facts. The approval fingerprint covers the actual templates and policy.

Queue admission requires separately qualified SMS standing approval, the exact
current SMS consent and source verification, current owner and invitation authority,
and no sender-wide STOP. No verifier is installed by default. Patient-screen
self-attestation does not supply independent qualification or permission for the
first booking contact. That initial-contact source integration remains unfinished.
Exact retries recover the retained receipt without creating a new intent.

Generated expansion `950a65bbfa96` follows `79601b0047dc`. It adds only the SMS
outbox, indexes and its own guards. Retained encrypted evidence and content-free
audit commit atomically; contact and link ciphertext has an absolute 24-hour ceiling,
bounded further by consent, approval, invitation and appointment deadlines.
The populated predecessor rehearsal preserves existing patient answers, synthesis,
consent and all prior tables and guards. Recovery retains this expansion and evidence,
keeps sends disabled and uses a forward fix for schema faults.

The irreversible submission states and recipient fence support the separate worker
increment; storage alone does not dispatch a text. Real SMS transport, authenticated
STOP callbacks and receipts, initial booking consent, automated source wiring,
retention and operational qualification remain required. No live service, message,
model, account, deployment or release configuration is changed.

### SMS submission and secret maintenance

The separate Core worker commits a single submission claim before invoking a
trusted SMS transport. It rechecks current consent, approval, invitation, timing
and STOP after decrypting the private payload, keeping the sender and recipient
transaction fence through the effect. Claims contain no contact, answer or link
and have process-local, single-use custody; they are not broker or browser payloads.

The worker records provider acceptance, never delivery without a confirmed receipt.
An exception, unknown response, late response or expired submission lease is
uncertain and cannot be automatically resent. A change between claim and dispatch
suppresses the effect. The adapter must enforce its 30-second attempt budget;
the worker rejects a late result but does not itself interrupt an arbitrary adapter.

Secret maintenance works with sends disabled. It clears expired and obsolete
queued payloads, including STOP and changed consent, and records uncertainty for
expired submission claims without deleting retained evidence. Its candidate query
targets cleanup-due rows so healthy queued work does not hide older required cleanup.
This is internal worker behavior, not a scheduled production job or a claim that
monitoring is operational. Real transport and qualified source adapters, job wiring,
callback retry and delivery receipts remain unfinished activation requirements.

Synthetic acceptance covers the actual queue and worker, unfinished-visit reminder,
current changes between claim and effect, cross-preparation spacing, independent
STOP contention during transport, one-use custody, uncertain outcomes and disabled
secret maintenance. No real SMS service, account, credential, message, model,
environment setting or deployment was used. Recovery disables sending, preserves
the existing expansion and evidence, and runs secret maintenance before retiring
the worker code; an uncertain claim is never made eligible for resend by rollback.

### SMS status on the provider dashboard

This Tier 2 increment adds an owner-only, audited SMS status read and dashboard
snapshot for the booking text and both reminders. It shows the latest recorded
intent per step across invitation generations, marks obsolete contexts and elapsed
deadlines, and distinguishes acceptance from confirmed delivery. It does not load
encrypted contact, link or consent payloads, grant sending permission, enqueue,
reconcile or dispatch work. Current Core owner, practice, workforce and BAA authority
remain required, even with patient and channel controls disabled.

Qualification uses actual metadata and worker transitions, closed API/SDK parity,
explicit dashboard reads, desktop/mobile rendering, denied ownership and support
contexts, and invalidation during pending reads. Recovery reverts this additive
route and display; existing SMS intent/evidence and submission behavior remain
unchanged. There is no schema, service, dependency or release-control change.
Real transport, authenticated callbacks and receipts, initial-contact consent,
automation and operational acceptance remain open; a status snapshot supplies none.

Final local qualification passed 49 backend, contract and browser checks, including
the SMS queue/worker and existing email dashboard, plus 80 focused UI checks.
The complete synthetic app build and frontend typecheck passed. Desktop and mobile
screenshots were visually inspected. Existing framework deprecation, source-map
and future-flag warnings remain; no live-provider acceptance is claimed.

### Durable physician SMS policy decisions

This Tier 2 additive expansion captures explicit physician approval or withdrawal
of the fixed SMS invitation and reminder policy for the current canonical sender.
It reuses Core's email policy authorization, encrypted evidence and exact-retry
patterns without importing email consent or granting patient permission. The
append-only stream and content-free audit share a transaction. Renewed approval
is required after sender or template change; withdrawal and historical replay
do not restore sending authority.

The queue and worker can compare their standing-approval snapshot with the latest
durable decision under current owner locks. A separate trusted SMS sender-source
verifier is still required; policy capture does not qualify a provider account,
patient consent, first booking outreach or actual delivery. No accepting default
verifier, public API, dashboard decision control or runtime job is installed by
this storage/domain increment. Those remaining composition paths are separate
work, not demonstrated by a successful synthetic verifier.

Generated expansion `85d330017454` follows `950a65bbfa96` and adds only the
approval stream and its own guard. The populated predecessor rehearsal preserved
every existing table's rows and Visit Prep guard definitions, including completed
patient answers, the published package and accepted SMS evidence. The new durable
choice was then consumed by the actual queue and worker with synthetic source
qualification. Final local qualification passed 88 backend checks, plus that
separate migration rehearsal. Checks cover withdrawal before dispatch, exact
retry, concurrent decisions, real owner-lock waits followed by BAA withdrawal or
expiry, private-evidence rejection, atomic rollback and raw SQL guards. Changed
production Python passed lint, formatting and typecheck. Existing framework
deprecation warnings remain; these results are not live-provider acceptance.

Recovery keeps sends disabled and retains decisions and schema; revert additive
application code and use a forward schema fix. Approval-evidence retention and
legal-hold cleanup must be qualified before activation. No live service, message,
model, environment or deployment is changed.

### Physician SMS policy API

The authenticated owner API now reads the fixed SMS policy and records approval
or withdrawal through the durable decision service. The three previews come from
the actual templates, replacing the invitation with a non-working private-link
placeholder. It selects no patient or contact. Central timing remains read-only;
unset sender routing or timing is explicit and supplies no dispatch authority.

The decision contains an expected sender digest as a review binding, not an
authorization claim. The locked service compares fresh approvals with current
routing, while withdrawals and exact retries bind the original retained decision.
A stale tab cannot approve a replacement sender. Both operations require current
Core owner authority, deny support view-as, reject extra fields and queries, bound
request bytes and return no-store, no-referrer responses with fixed errors. A read
and its content-free audit commit together; an unavailable read reveals no snapshot.

Final local qualification passed 97 backend, authority, registration and contract
checks. The actual internal OpenAPI export adds only the SMS policy path and four
closed schemas; existing paths and schemas are semantically unchanged. The cached
frontend generator consumed that export and full frontend typecheck passed.
Changed production Python passed lint, formatting and typecheck. These checks use
invented owners and isolated PostgreSQL, not live identity or messaging services.
Dashboard decision controls are a separate consumer increment. Real transport,
source qualification, initial-contact permission, automation and operational
acceptance remain required. No schema or release control changes in this API step.
Recovery reverts its additive route, registration and display service while
retaining policy evidence and the existing expansion. No send is enabled.

### Physician SMS policy dashboard

The dashboard now has separate SMS policy controls alongside the email policy.
An explicit load displays the three fixed text previews, current approval state
and centrally managed, read-only timing. Approval requires review and an explicit
future UTC expiry. The request binds the reviewed sender and policy; a stale
sender review cannot authorize its replacement. Withdrawal uses the retained
decision even after routing changes or disappears. No patient, contact or working
invitation is selected, and no consent, queue entry or text is created.

The display clears on owner scope, access, view-as, page visibility and focus
changes, rejecting pending responses from the prior scope. A decision clears its
preview and inputs before submission. A lost acknowledgment is reported as an
unknown outcome and requires a fresh read, never an automatic replacement request.
The UI validates the closed API receipt and continues to describe delivery as
not connected, regardless of recorded physician approval.

Final local qualification passed 27 backend and desktop/mobile browser checks
against the actual owner API, including existing email policy behavior, plus 55
focused SMS/email UI checks. The complete synthetic app build and full frontend
typecheck passed. Changed files passed formatting and Python lint. Desktop and
mobile screenshots were visually inspected; no horizontal overflow or script
execution was observed. Existing framework deprecation, source-map and future-flag
warnings remain. These are synthetic results, not live identity or SMS acceptance.

Recovery reverts the additive SMS display and its route composition, retaining
policy records and the API expansion. No schema, runtime service, dependency or
release setting is changed. Real transport, sender/source qualification, initial
booking permission, automatic invitations and reminders, callbacks, cleanup and
operational acceptance remain required before activation.

## Source references

- [Original Visit Prep requirements, issue 135](https://github.com/CosmaNeura/cosmaneura/issues/135) and [approved old-stack plan](https://github.com/CosmaNeura/cosmaneura/blob/d80a37012526d96991db25920797457c392231a8/docs/flight-plans/visit-prep.md).
- Core source inspected at `bc9da3478de0e03bacac0b81495ea43956c51279`: [current engineering/release rules](https://github.com/CosmaNeura/cosmaneura/blob/bc9da3478de0e03bacac0b81495ea43956c51279/AGENTS.md), [Session Prep runtime](https://github.com/CosmaNeura/cosmaneura/blob/bc9da3478de0e03bacac0b81495ea43956c51279/app/services/session_prep_runtime.py), and [companion receiver](https://github.com/CosmaNeura/cosmaneura/blob/bc9da3478de0e03bacac0b81495ea43956c51279/app/routes/companion_session_prep.py).
- Archived reusable UI/brief commits in Core: `0d92405f` for patient interview UI, `b164ad7a` for structured brief, and `092f6aa0` for dashboard answers. These are adaptation candidates, not current production components.
- CAB source inspected at `01ad97536c4f6730e30cd8a03dd31cdb07a918fb`: [ACS email transport](https://github.com/CosmaNeura/Cosmas-CAB-Tool/blob/01ad97536c4f6730e30cd8a03dd31cdb07a918fb/cosmaneura_billing/cosmaneura_billing/chase/acs_email.py), [worker](https://github.com/CosmaNeura/Cosmas-CAB-Tool/blob/01ad97536c4f6730e30cd8a03dd31cdb07a918fb/cosmaneura_billing/cosmaneura_billing/chase/worker.py), [scheduling rules](https://github.com/CosmaNeura/Cosmas-CAB-Tool/blob/01ad97536c4f6730e30cd8a03dd31cdb07a918fb/cosmaneura_billing/cosmaneura_billing/chase/scheduling.py), and [adapter tests](https://github.com/CosmaNeura/Cosmas-CAB-Tool/blob/01ad97536c4f6730e30cd8a03dd31cdb07a918fb/cosmaneura_billing/tests/test_acs_email.py).
- [CosmaBot architecture](https://github.com/CosmaNeura/cosmaneura-os/blob/main/docs/COSMABOT_ARCHITECTURE.md), [Session Prep connection contract](https://github.com/CosmaNeura/cosmaneura-os/blob/main/docs/core-session-prep.md), and [internal preparation work](https://github.com/CosmaNeura/cosmaneura-os/blob/main/src/cosmas_os/application/prep_work.py). These links track main; pin the selected revisions before transfer.
- [Scheduling companion discovery contract](https://github.com/CosmaNeura/cosmaneura-scheduling/blob/main/docs/adr/0035-companion-read-only-discovery.md) and [CosmaBot clinician test harness](https://github.com/CosmaNeura/cosmaneura-os/pull/65).
- Acuity public [webhook signature and retry contract](https://developers.acuityscheduling.com/docs/webhooks) and [current appointment response](https://developers.acuityscheduling.com/reference/get-appointments-id), inspected October 2, 2026. These document the adapter protocol, not permission to activate a practice account or contact a patient.
