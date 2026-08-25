# Flight plan: Visit Prep

**Status:** approved by founder on 2026-08-25

**Issue:** [#135](https://github.com/CosmaNeura/cosmaneura/issues/135)

**Owner:** John

**Target:** small, independently deployable PRs from `master`; dark until the
MCD end-to-end path is ready

## Scope

- **Problem / outcome:** Replace the disabled generated-question intake chatbot
  with a trusted asynchronous five-question preparation flow. A physician owns
  the questions, a patient answers before the visit, and the physician receives
  a scannable brief in a dedicated Visit Prep dashboard section.
- **In scope:**
  - one seeded default set per physician containing the five approved questions;
  - multiple versioned physician-owned sets, with one default and an explicit
    set choice for each prep;
  - physician-only DIY creation for v1: patient first name, delivery contact,
    visit datetime, question set, create/copy link, send, revoke, and resend;
  - a separate Visit Prep aggregate that can link to a Patient and Appointment
    when they exist but does not create incomplete Patient records;
  - first-name/caregiver confirmation, one question per screen, skip, progress,
    resume, and conditional adherence follow-up;
  - partial answers visible to the physician;
  - a structured Visit Prep brief derived only from the current prep answers;
  - provider-neutral delivery interfaces, transactional outbox, delivery state,
    and independent channel kill switches;
  - Azure Communication Services as the preferred email/SMS implementation,
    subject to BAA/HIPAA eligibility and configuration verification;
  - a normalized `appointment.scheduled` seam for the later MCD Acuity adapter;
  - MCD email plus SMS automation only after the complete dark path passes.
- **Explicitly NOT in scope:**
  - Session Prep, prior provider notes, Athena, MCP calls, or merging their data,
    prompts, jobs, storage, or UI with Visit Prep;
  - generated interview questions;
  - creating or mutating Patient records from unverified DIY contact details;
  - coordinator access until a real delegated role is modeled;
  - other calendar adapters before the MCD Acuity path;
  - registration, insurance, copay, demographics, or EHR write-back;
  - copying the TanStack/Zustand/local-storage prototype into this repository;
  - deleting the legacy intake path while a legacy link can still be valid.
- **User cohorts affected:** founder/synthetic accounts first; controlled internal
  DIY validation second; MCD is the first external customer. No MCD activation
  occurs until Acuity-triggered email and SMS are ready end to end. Manual
  physician send remains available as fallback.

## Product boundary

Visit Prep is written by the patient before the current visit. Session Prep is
provider-facing Sovereign assistance derived from prior notes and explicitly
permitted external context. They may later appear next to each other, but they
remain separate products.

- Visit Prep uses dedicated models, jobs, API contracts, and dashboard queries.
- A Visit Prep brief is not stored in `Intake.summary` and is not added to the
  existing Session Prep/patient-context input.
- The Visit Prep summarizer cannot call prior-note loaders, Athena, MCP tools, or
  Sovereign capabilities. Negative dependency tests enforce this.
- Before activation, introduce `session-prep:run` as the canonical Sovereign
  capability and retain `visit-prep:run` only as a deprecated compatibility
  alias for existing callers. John owns its removal after usage reaches zero.
  Visit Prep endpoints never authorize against either Sovereign capability.
- The first release renders Visit Prep in its own dashboard section. A later UI
  layer may compose Visit Prep above Session Prep without merging their jobs or
  provenance.

## Approved default questions

1. What is the main reason for this visit?
2. What medications are you prescribed right now?
3. Are you taking them as prescribed? (choice)
4. If not — what got in the way? (shown only when question 3 is not “yes”)
5. Tell me your health story — whatever feels relevant.

Published question-set versions are immutable. Editing a used set creates a new
version so every answer and brief retains its exact question lineage. Publishing
requires exactly five ordered question identities, validates conditional targets
and choice values, and rejects unreachable questions. A partial unique database
index enforces at most one default set version per physician.

## Risk

- **Tier:** 2. This introduces patient-data schemas, public bearer-token access,
  patient contact delivery, clinical answers, and an AI-generated clinical brief.
- **Touches auth, billing, or patient-data schemas?** Patient-data schemas and
  token lifecycle: yes. Auth provider and billing: no.
- **Migration ladder:** additive `expand` PRs first; gated writes and reads later;
  legacy removal only in a separate `contract` PR after its evidence window.
- Founder approval is recorded above. Each implementation layer remains small
  enough to review and deploy independently.

## Flags and kill switches

- **Eligibility flag:** `ff_visit_prep_202608`
- **Owner:** John
- **Expiry:** remove within 30 days after full rollout, no later than 2026-12-31
  unless this plan records a renewed date.
- **Authority:** audited, server-side practice/physician entitlement. Frontend
  navigation is not an authorization boundary.
- **Delivery gates:** `VISIT_PREP_EMAIL_SEND_ENABLED` and
  `VISIT_PREP_SMS_SEND_ENABLED`, both false by default in every environment.
- Eligibility never implies permission to send. Channel gates and cohort
  eligibility must both pass and are rechecked immediately before every provider
  call, including already-queued outbox rows.
- Non-production delivery also requires an independent server-side synthetic
  recipient allowlist or sink. A physician cohort alone cannot authorize an
  external destination.

## Identity, access, and public-link rules

- DIY creation stores an immutable per-prep snapshot of first name, encrypted
  contact details, original scheduled datetime, IANA timezone, physician display
  details, and the selected question-set version. Ownership uses an authoritative
  `practice_member_id` (or an equivalently constrained doctor/practice pair), not
  an email address. `patient_id` and `appointment_id` are optional and, when
  present, must belong to that same practice boundary.
- Current schedule is separately versioned as timezone-aware UTC plus IANA zone.
  Rescheduling preserves the original snapshot, increments the schedule and
  credential generation, rotates the token, and recalculates reminders/expiry.
  It never revives a revoked or already-expired credential generation.
- A later reconciliation operation may attach a prep to canonical records; it
  must be explicit, practice-scoped, audited, and must not rewrite lineage.
- In v1, only the authenticated physician may edit their sets or create, send,
  resend, or revoke their preps. Existing view-as sessions remain read-only.
- The later MCD system path is a separate trusted service actor. Its adapter
  verifies the source signature, enforces replay/idempotency, resolves an explicit
  calendar-provider mapping to an active practice membership, and records the
  source event, service actor, resolved owner, and decision. It may create or
  reschedule a prep but cannot edit question sets or bypass delivery gates.
- Public identity is possession of a cryptographically random 256-bit token plus
  the soft first-name/caregiver confirmation. There is no DOB gate.
- The durable verifier stores only a token hash. Creation returns plaintext once.
  A later copy or resend explicitly rotates the token and revokes the old one.
  When an async delivery needs the bearer, a bounded encrypted outbox secret may
  hold it only until terminal delivery or expiry, then purges it. Idempotency and
  uniqueness fences prevent retries from minting or sending divergent links.
- Rotate and revoke on resend, wrong-recipient report, appointment cancellation or
  reassignment, physician action, and every accepted reschedule.
- Each credential generation expires 72 hours after its current scheduled
  appointment time.
- Public responses are generic and do not reveal whether a patient, appointment,
  email, or token exists. Pages use `Cache-Control: no-store`,
  `Referrer-Policy: no-referrer`, no third-party scripts, and no token-bearing
  analytics or access logs.
- One answer per request; at most five answered question identities; free text is
  bounded to 2,000 Unicode characters per answer and 10 KiB per request. Public
  reads and writes are rate-limited by a privacy-safe combination of token hash
  and network signal.

## Retention

- Confirmation, start, skip, and completion are explicit lifecycle events. An
  all-skipped completed prep is clinical content; abandonment is never inferred
  from answer count alone.
- At first patient/caregiver confirmation, snapshot the applicable retention
  policy version, compute a concrete `retain_until`, and support legal hold.
  Partial and completed preps have the same clinical retention because any saved
  answer or skip context may affect care.
- A never-confirmed, never-started invitation is abandoned data. Revoke it at
  token expiry and delete the entire patient-linkable aggregate—including contact,
  token, outbox, delivery, and relationship identifiers—30 days after the current
  appointment. Alert on cleanup failures; 90 days is the hard ceiling.
- Wrong-recipient and unused cancelled invitations are revoked immediately and
  purged on the next cleanup run when no interaction occurred.
- If a wrong recipient interacted, move the prep to a `misdirected` quarantine:
  exclude its answers from briefs, provider clinical views, reconciliation, and
  normal retention. Preserve it only under the incident/legal disposition chosen
  by the security owner; never attach it to the intended patient's chart.
- After purge, retain only non-identifying aggregate funnel counts. Do not retain
  a patient-linkable tombstone merely for analytics.

## Schema changes

| Change | Ladder rung | PR |
|---|---|---|
| Versioned physician question sets, ordered questions, and unique default constraint | Expand | Layer 3 |
| Visit Prep aggregate, practice ownership, contact snapshot, schedule/credential generations, lifecycle, and retention fields | Expand | Layer 6 |
| Answers/skips with question-set lineage, idempotency, and uniqueness constraints | Expand | Layer 9 |
| Structured brief with source-answer snapshot, prompt/model version, and supersession lineage | Expand | Layer 13 |
| Transactional delivery outbox, bounded encrypted secret, and channel-attempt state | Expand | Layer 17 |
| Scoped external appointment/provider mapping and event idempotency | Expand | Layer 21 |
| SMS consent provenance and durable suppression state | Expand | Layer 24 |
| Gated Visit Prep reads/writes and dedicated dashboard query | Cut reads | Layers 4–16 |
| Legacy execution paths after clinical provenance is preserved | Contract | Layer 29 |

All expansion is additive. Rollback disables new creation, delivery, readers, and
writers while leaving applied structures dormant. No incident rollback drops or
downgrades an applied schema.

Brief generation is enqueued atomically with completion. A database uniqueness
fence over the prep and source-answer version prevents duplicate briefs; retries
return or supersede the same lineage rather than racing a second insert.

## Delivery decision

- Domain code targets an internal delivery interface, not an Azure SDK.
- Azure Communication Services is the preferred adapter because the product is
  already Azure-hosted. No real patient delivery begins until the exact service
  is confirmed under the applicable Microsoft agreement/BAA, credentials use an
  approved secret path, and bounce/complaint/STOP behavior is tested.
- Generic outbox code and every application process must boot and test with all
  Azure Communication Services configuration absent. Azure dependencies, resource
  configuration, provider callbacks, and channel activation land separately.
- Email is a PHI-light letter from the physician display name. SMS contains first
  name and opaque link only. Inbound clinical replies and attachments are not
  processed.
- MCD target cadence: email and SMS at booking, SMS at T–48 when unopened, and
  SMS at T–12 when unfinished. TCPA consent provenance and STOP suppression are
  required inputs to every SMS decision.
- Acuity input is translated at the boundary into a versioned internal
  `appointment.scheduled` command; no Acuity-specific payload enters core Visit
  Prep logic. Event identity is scoped by at least `(practice_id, source,
  external_appointment_id)` plus source event/version. Provider mapping targets an
  active practice membership and never resolves ownership by email.
- Before each provider call, dispatch rechecks current cohort eligibility, channel
  gate, recipient fence, consent, suppression, revocation, and appointment state.

## Rollout plan

1. Deploy each layer dark to founder/synthetic accounts; no delivery.
2. Validate physician DIY creation and copy-link with synthetic contacts.
3. Enable controlled Azure email and SMS only to founder-controlled recipients.
4. Complete the MCD Acuity-triggered synthetic journey and reminder timing tests.
5. Activate one named MCD physician cohort; observe at least five completed preps
   and 72 hours without a rollback signal.
6. Expand to the approved MCD cohort after founder review of health signals.
7. Add other practices or calendar adapters only through later flight-plan layers.

## Health signals

- invitation creation, revoke, cleanup, and delivery success/failure;
- open, start, partial-save, completion, and physician brief-open conversion;
- invalid, expired, revoked, replayed, and rate-limited bearer requests;
- answer conflict/idempotent retry rate;
- brief generation/parse failures, lineage conflicts, and completion-to-brief time;
- never-started invitation aggregates older than 30 and 90 days;
- quarantined misdirected interactions awaiting incident/legal disposition;
- public-route latency and 4xx/5xx rate;
- synthetic journey and PHI/token redaction-test results.

No metric contains identity, contact information, bearer values, raw answers,
prompts, summaries, or clinical flags.

## Rollback trigger + move

- **Immediately disable delivery and eligibility for:** cross-practice or
  wrong-patient disclosure; PHI/token telemetry; an answer applied to the wrong
  question/version; duplicate/corrupt brief; failed synthetic journey; delivery
  without consent; or a material public-route/brief-generation failure increase.
- **Move:** turn off both channel gates, remove the affected cohort, stop new
  reads/writes, and deploy a forward fix or reviewed revert. Dispatch rechecks
  those gates, so queued rows also stop. Preserve answered clinical records,
  quarantine misdirected interactions, and leave additive schema dormant.
- Contract deletion is never an incident-response move.

## Stack outline

Each numbered layer is its own reviewed, deployable PR. Every implementation PR
starts from current `master`, keeps the old path working, and ships dark unless a
later layer explicitly activates a founder/synthetic cohort.

1. **Flight plan** — this document and approved product/data boundaries.
2. **Release-control seam** — server eligibility response, physician/practice
   enforcement, channel gates, and negative authorization tests. No Visit Prep
   data path or navigation becomes active.
3. **Question-set expand schema** — models, constraints, and migration only; old
   code ignores every new structure.
4. **Question-set domain/API** — publish validation, immutable versioning,
   default selection, and idempotent lazy materialization for both existing and
   future eligible physicians. The uniqueness fence makes retries safe; there is
   no migration-time seed or production backfill.
5. **Physician question editor UI** — list/edit/version/default controls behind
   eligibility; no patient delivery.
6. **Visit Prep aggregate expand schema** — ownership, optional canonical links,
   encrypted contact snapshot, schedule and credential generations, lifecycle,
   misdirected state, and retention metadata only.
7. **Physician DIY API** — create, one-time copy/rotation, revoke, reschedule, and
   reconciliation commands; no delivery.
8. **Physician DIY UI** — create, choose set, copy link, rotate/revoke controls;
   send remains unavailable.
9. **Answer expand schema** — answers/skips, lifecycle, question lineage,
   idempotency, and concurrency constraints only.
10. **Public interview API** — token verification, confirmation, answer/skip,
    resume, complete, expiry, body limits, rate limits, and generic errors.
11. **Patient interview UI** — first-name/caregiver choice, conditional five-
    question flow, progress, resume, and truthful expired/revoked states.
12. **Legacy route compatibility** — keep `/chat/intake/:id` valid or redirect it
    without invalidating any outstanding legacy bearer.
13. **Brief expand schema** — source snapshot/version, prompt/model version,
    uniqueness, status, and supersession lineage only.
14. **Brief generation job** — transactional completion enqueue, bounded typed
    output, dedicated prompt, retry fence, and no chart/Session Prep dependency.
15. **Provider dashboard read API** — dedicated practice-scoped Visit Prep read
    model; quarantined/misdirected data is excluded.
16. **Provider dashboard UI** — separate partial-answer, brief, flag, and delivery
    section; no Session Prep data composition yet.
17. **Delivery-outbox expand schema** — outbox, attempt, and bounded encrypted
    bearer-secret structures only.
18. **Disabled outbox engine** — provider-neutral dispatch interface, atomic
    enqueue, retry/idempotency, purge, and dispatch-time gate/recipient checks.
19. **Azure email capability** — dependency and resource/configuration changes in
    their own approved PR; absent configuration remains a valid disabled state.
20. **Azure email adapter/callbacks** — synthetic sink first, signed delivery
    status, bounce/complaint handling, and independent email gate.
21. **External appointment mapping expand schema** — practice/source/external ID,
    verified provider-to-membership mapping, event versions, and uniqueness only.
22. **Acuity service adapter** — source authentication, replay protection,
    normalized commands, deduplication, cancellation/reschedule handling, and
    complete actor/decision audit.
23. **Sovereign naming boundary** — introduce `session-prep:run`, preserve the old
    capability as a measured compatibility alias, and prove Visit Prep endpoints
    never depend on either capability.
24. **SMS consent/suppression expand schema** — consent provenance, STOP status,
    phone normalization, and durable suppression structures only.
25. **Azure SMS adapter/callbacks** — verified sender/10DLC capability, synthetic
    recipients, signed status/STOP callbacks, and independent SMS gate.
26. **Reminder scheduler** — T–48 unopened and T–12 unfinished decisions, with
    schedule generation, consent, suppression, gate, and idempotency rechecks.
27. **End-to-end MCD rehearsal** — no customer activation; exercise Acuity through
    both delivery channels using synthetic recipients and failure paths.
28. **MCD activation** — one named physician cohort, then the approved MCD cohort
    only after the complete journey and observation window pass.
29. **Contract** — only after zero outstanding unexpired legacy links, a defined
    compatibility redirect window, and at least 30 days of zero legacy traffic.
    Answered legacy clinical records must first be migrated with provenance intact
    or remain in retained storage until their hold expires; contract never deletes
    them merely because the execution path is quiet.

Useful behavior and tests may be selectively ported from the old
`origin/claude/visit-prep-*` branches. Those branches predate current security and
privacy work and are not merged or rebased wholesale.

## Required validation

- Unit/property tests for question validation, conditional reachability, stable
  versions, exactly-five publish enforcement, one-default enforcement, token
  expiry/rotation/revocation, schedule generations, retention classification,
  idempotency, and concurrent submissions.
- Authorization tests for cross-practice access, disabled cohorts, physician
  ownership, view-as write denial, and hand-typed public URLs.
- Negative dependency tests proving Visit Prep cannot read prior notes, Athena,
  MCP/Sovereign context, `Intake.summary`, or Session Prep storage.
- Privacy tests proving tokens, contacts, free text, and brief content are absent
  from logs, Sentry, PostHog, access logs, HTML metadata, and referrers.
- Adversarial brief/rendering tests for prompt injection in patient text,
  unsupported clinical claims, overlong output, raw HTML, unsafe URLs, and
  Markdown/link sanitization. Patient instructions never become system policy.
- Service-actor tests for invalid source signatures, replayed/out-of-order events,
  cross-practice provider mappings, inactive memberships, reschedule generations,
  and dispatch after a channel/cohort is disabled.
- Migration rehearsal for each patient-data expand rung using verified safe data.
- Synthetic journey: create → copy link without sending → confirm patient or
  caregiver → answer/skip → resume → complete → one brief → physician opens the
  dedicated section → retention class is correct.
- Delivery journey: normalized appointment event → one prep → allowed email/SMS
  attempts → reminders obey state and consent → cancel/reschedule/revoke safely.
