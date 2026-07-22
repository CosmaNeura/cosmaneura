# Session Prep + Provider Terminology — Implementation Plan

Date: 2026-07-22
Status: awaiting approval (no feature code written yet)
Target: staging first, gated to the behavioral-health pilot user

## 1. Stack verification

Verified against the codebase (corrections to initial assumptions in **bold**):

- Backend: Python **FastAPI** in `app/` — confirmed. ORM is `activemodel`
  (ActiveRecord-style layer over SQLModel/SQLAlchemy), Alembic migrations in
  `migrations/`, Celery + Redis for all background/LLM work.
- Frontend: **not a separate cosmaneura-web repo** — it lives in `web/` in this
  monorepo. React Router 7 (config-based routes in `web/app/routes.ts`),
  shadcn/tailwind, TanStack Query, API client generated from `web/openapi.json`
  via `@hey-api/openapi-ts`.
- Auth: **Clerk** — confirmed (`app/routes/dependencies/clerk.py`,
  `@clerk/clerk-react`).
- LLM: **Azure OpenAI** (`app/configuration/openai.py`), model
  `gpt-4.1-2025-04-14`, `temperature=0, top_p=0.1`, JSON mode for structured
  output. Prompts are inline Python builders, not template files. All clinical
  LLM calls run in Celery jobs (`perform` / `perform_celery` / `queue()`
  convention).
- Feature gating: **not PostHog** (PostHog is analytics-only). Gating is a
  hardcoded per-environment email allowlist — `web/app/utils/beta-access.ts`
  (frontend) mirrored by allowlists in `app/services/zoom_webhooks.py`
  (backend). Staging deploys automatically on push to the `staging` branch.
- PHI discipline: log IDs, never content (see `app/jobs/anonymize.py:100`);
  Sentry only enabled in production; transcripts are de-identified via Azure
  Health De-identification and **tombstoned after 14 days** (daily
  `transcript_cleanup` beat job).

## 2. Finding that changes Feature 1: Sovereign has no retrieval layer to reuse

The Sovereign chatbot lives in a **separate repo**
(`CosmaNeura/cosmaneura-sovereign`), not this monorepo. It is a Phase-0
standalone FastAPI service: an orchestrator that routes chat requests to three
agents (referral, concierge, engagement) with Magisterium AI doctrinal
arbitration, running on **Anthropic Claude** (BAA/ZDR path) — not Azure
OpenAI — and shipping in mock mode.

Crucially, **it contains no patient-context retrieval over Cosmas session
history**. Its "patient context" is caller-supplied: `ChatRequest.history`
(chat turns passed in per request, `app/schemas.py:37`) and
`request.metadata["health_history"]` (a free-form string the caller must
provide, `app/agents/engagement_agent.py:63`). There is no database, no
connection to Cosmas data, and no retrieval/embedding infrastructure in either
repo. In other words: Sovereign's engagement agent is *designed to be handed*
a patient history by some upstream system that doesn't exist yet.

That upstream system is exactly what PR 5 builds. In this monorepo:

- The **public intake chatbot** (`app/routes/chat.py`,
  `app/commands/intake/chat.py`, `web/app/components/chat/intake/*`) — a
  deterministic scripted Q&A flow. No LLM at chat-turn time, no retrieval.
- The **intake pre-visit summary** (`app/jobs/intake/summarize.py`) — the
  closest existing thing to a briefing generator. It assembles doctor bio +
  cleaned intake + follow-up Q&A + the patient's most recent clinical note
  into a ≤250-word summary.
- The **engagement engine** (`app/engagement/*`) — this is the Patient
  Personality Profile: per-transcript `EngagementRole` rows (communication
  role: Self-Directed / Collaborative / Directed, confidence, indicators) plus
  `CpsSelfReport` ground truth.

**Design consequence (confirmed against both repos):** since there is no
retrieval layer to reuse, we invert the reuse direction. The patient-context
assembly is built as a shared service in this repo (PR 5) — where the data and
the PHI controls live — and Session Prep consumes it directly. When Sovereign's
provider persona goes live, Cosmas exposes the same `assemble_patient_context()`
output over an internal authenticated endpoint to populate Sovereign's
`health_history` input; that integration is future work, but the service
boundary is drawn for it now. This also satisfies the "shared service
extracted as its own refactor PR first" requirement — two intake jobs already
duplicate the "last note for this patient" query and will be refactored onto
it.

Session Prep's LLM calls stay on this repo's existing clinical path (Azure
OpenAI, Celery); Sovereign's Anthropic/Magisterium stack is its own concern
and is not pulled in here.

**Data reality:** because raw transcripts are deleted after 14 days (PHI
policy), "full session history" in practice means: all persisted
`AIVisitNote`s for the patient + intake summary + engagement role/personality
profile + any not-yet-tombstoned recent transcript summary. Session Prep will
be built on the persistent notes, not raw transcripts.

**UI reality:** there is no standalone patient detail page. `/notes/:id` and
`/intake/:id` are appointment-scoped detail pages; `/schedule/:id` is the
upcoming-appointment (Zoom meeting) view. Session Prep will surface on
`/schedule/:id` (primary — "before an upcoming appointment") and `/notes/:id`.

**Email reality:** `mail()` exists (`app/configuration/emailer.py`) but is not
called anywhere in product code, and the only templates are placeholders. So
"emails/notifications" is a no-op for the terminology feature today; the
terminology catalog will be the single source when email ships.

## 3. Feature flags (both features)

Follow the existing pattern, one thin wrapper per feature so cohorts can
diverge later:

- `web/app/utils/session-prep-feature.ts` → `isSessionPrepEnabled(email)`
- `web/app/utils/terminology-feature.ts` → `isTerminologyEnabled(email)`

Both delegate to `beta-access.ts` initially. Add the pilot user's email to
`STAGING_BETA_EMAILS` (currently only `zoom-reviewer@cosmaneura.com`).
Backend: mirror allowlist gate on the session-prep endpoints (same pattern as
`app/services/zoom_webhooks.py`) so the API is not merely hidden by the UI.
Terminology needs no backend gate — an extra settings column is harmless if
set, and the UI entry point is flagged.

## 4. PR breakdown

Two independent tracks. Terminology (PRs 1–4) has no dependency on Session
Prep (PRs 5–7). Within each track, PRs are sequential. Each is small and
independently reviewable/revertable.

### Track A — Provider terminology

**PR 1 — backend: `Doctor.terminology` setting**
- New enum `Terminology`: `doctor_patient` (default) | `therapist_client` |
  `counselor_client`; nullable-with-default column on `Doctor` + Alembic
  migration.
- Expose in `SettingsData` and accept in `UpdateSettingsData`
  (`app/routes/internal.py:294,398`). Note: `update_settings_data` currently
  writes only `biography`; extend it without clobbering (field optional,
  only write when provided).
- Tests: model default, settings round-trip (`tests/routes/`), migration.
- Size: ~150 LOC. No user-visible change yet.

**PR 2 — frontend: terminology catalog + settings UI**
- `web/app/utils/terminology.ts`: a proper label map, not string replacement.
  Shape: `TERMINOLOGY_SETS: Record<TerminologyKey, LabelSet>` where `LabelSet`
  carries the variants the UI actually needs (`provider`, `providerTitle`,
  `patient`, `patientTitle`, `patientPlural`, `patientPossessive`, …). Adding
  a fourth set later = one new entry.
- `useTerminology()` hook: reads the doctor's setting from the cached
  `settingsData` query (`settingsDataOptions`, already prefetched in the
  authed layout) and returns the active `LabelSet`; falls back to
  `doctor_patient` when unset/flag off. Also export a pure
  `getTerminology(key)` for non-hook call sites.
- New section on `/settings` (`web/app/routes/settings.tsx`) with a select,
  saved via `updateSettingsData` — same react-hook-form/zod/toast pattern as
  `NoteCustomizationForm`. Section rendered only when
  `isTerminologyEnabled(email)`.
- Tests: catalog unit tests, hook fallback behavior, settings-section
  component test (mock client, assert payload — mirror
  `NoteCustomizationForm.test.tsx`).
- Size: ~300 LOC.

**PR 3 — frontend: swap hardcoded labels to the catalog**
- Mechanical sweep of the ~30–40 user-visible strings across ~18 files
  (routes/intake, routes/notes, routes/schedule, onboarding, settings,
  `components/notes/Header.tsx`, `UploadRecordingButton.tsx`,
  `NoteCustomizationForm.tsx`, `MessageTemplates.tsx`, `layouts/intake.tsx`,
  intake-chat welcome copy).
- Explicitly out of scope: code identifiers and generated-client fields
  (`patient_name`, `patientListOptions`, …), legal prose pages
  (privacy/terms/documentation).
- Public intake chat (`routes/chat/intake.tsx`, "Your doctor is
  requesting…"): the page is unauthenticated, so the label must come from the
  owning doctor — add `terminology` to the chat question response in
  `app/routes/chat.py` (tiny backend touch) and use it there.
- Tests: a rendered-label test per major surface with a non-default set
  (therapist/client) to lock in the wiring.
- Size: large-ish diff but trivially reviewable (label substitutions only).

**PR 4 — backend: terminology in generated content**
- Thread the doctor's terminology into the LLM prompt builders so generated
  notes and intake language use the right words:
  - `app/jobs/summarize.py` — `build_structured_prompt`,
    `_pronoun_instruction`, `_subjective_instruction`,
    `_ordered_instructions`, `build_action_item_prompt`,
    `augment_clinical_note` ("…spent in patient care…").
  - `app/jobs/intake/generate_questions.py` and
    `app/jobs/intake/summarize.py` — prompt wording and the deterministic
    test-fallback strings.
- Mechanism: small `terminology_terms(doctor)` helper returning the term pair
  used in f-string prompts, defaulting to doctor/patient. Stored transcript
  speaker labels ("Doctor:"/"Patient:") stay as-is (they're data, produced at
  transcription time); prompts get one line explaining the label mapping when
  a non-default set is active.
- Tests: prompt-builder unit tests asserting the terms appear (offline, no
  LLM); optional live-eval addition behind `RUN_LIVE_LLM_TESTS`.
- Size: ~250 LOC.

### Track B — Session Prep

**PR 5 — refactor: shared patient-context service (no behavior change)**
- New `app/services/patient_context.py`:
  - `latest_note_for_patient(doctor_id, patient_id)` — extracts the
    join currently duplicated in `app/jobs/intake/generate_questions.py`
    (~line 221) and `app/jobs/intake/summarize.py` (~line 111), and fixes the
    open `# TODO This should be time-limited` in one place (cap lookback,
    e.g. 18 months — confirm).
  - `assemble_patient_context(doctor_id, patient_id, limit_notes=N)` →
    structured `PatientContext` (recent visit notes newest-first with
    appointment dates, latest intake summary, latest `EngagementRole` +
    `CpsSelfReport` if present).
- Refactor both intake jobs onto `latest_note_for_patient`. Existing intake
  tests must pass unchanged.
- This service is the layer the Sovereign chatbot should consume when it
  lands.
- Size: ~200 LOC.

**PR 6 — backend: Session Prep model, job, endpoints**
- Model `SessionPrep` (TypeID `session_prep`): `doctor_id`, `patient_id`,
  `appointment_id` (nullable), `status` (pending/completed/failed),
  `content` (JSON: `talking_points: list[str]` (3–5),
  `open_threads: list[str]`, `personality_note: str | None`),
  `model`/`generated_at` provenance. Content is PHI → same handling as
  `AIVisitNote` (DB only, never logged).
- Job `app/jobs/session_prep.py` following the `perform` /
  `perform_celery` / `queue()` convention. Prompt (inline builder, same file):
  built from `assemble_patient_context()`, hard brevity constraints — 3–5
  talking points ≤ ~15 words each, open threads from the most recent note
  only, **total ≤ ~120 words** (half the intake summary's 250; brevity is the
  core requirement), JSON mode (`response_format={"type":"json_object"}`),
  `temperature=0, top_p=0.1`, `DEFAULT_MULTI_MODAL_MODEL`. Uses the
  terminology terms if PR 1 has landed (soft dependency; defaults otherwise).
- Content shape informed by a scan of pre-session-brief features in
  behavioral-health EHRs (SimplePractice Note Taker, Sessions Health AI
  Assist, Ensora) and MBC/risk-documentation practice:
  - `alerts: list[str]` — optional, rendered first and visually distinct:
    safety/risk mentions (e.g. SI documented in a prior note) and medication
    changes pulled from prior notes. Competitors call this "clinical alerts";
    risk continuity is the one thing a brief must never bury. Derived at
    generation time from existing note content — no new data model.
  - `open_threads` maps to the last note's plan/action items (the platform
    already generates per-visit action items — "actions for the patient" ≈
    homework), so continuity comes from data we already persist.
  - Computed (not LLM) fields: days since last session; count of visits in
    context window.
- Roadmap note (out of scope, recorded for later): measurement-based care
  scores (PHQ-9/GAD-7) trended across sessions are the industry-standard
  prep signal (APA-endorsed MBC). Cosmas doesn't collect them yet; if intake
  later adds standardized instruments, `assemble_patient_context()` is where
  the trend line plugs in.
- Endpoints on `internal_api_app`:
  - `POST /internal/v1/session-prep` `{patient_id, appointment_id?}` →
    creates a pending row, queues the job, returns the row (generate on
    demand; regenerating replaces the previous prep for that
    patient+appointment).
  - `GET /internal/v1/session-prep/{patient_id}?appointment_id=` → latest
    prep + status (this is what the frontend polls).
  - Both gated by a backend email allowlist (staging/prod sets, mirroring the
    zoom_webhooks pattern).
- Logging: IDs and status only — never prompt or content
  (`anonymize.py:100` convention). Errors to Sentry carry IDs only.
- Tests: deterministic offline fallback
  (`should_use_test_openai_fallback()` pattern → canned prep), job
  idempotency, endpoint auth + allowlist gate, polyfactory factory for
  `SessionPrep`; optional live eval asserting output length/format behind
  `RUN_LIVE_LLM_TESTS`.
- Size: ~450 LOC.

**PR 7 — frontend: Session Prep UI**
- `SessionPrepCard` component: "Prep for session" button → `POST`, then
  TanStack Query `refetchInterval` polling (~3s, capped) on the `GET` until
  `completed` (small, better UX than the existing full-page-reload pattern;
  no new infra). Renders the outline: talking points as a tight bullet list,
  open threads beneath, optional one-line communication-style note.
  Regenerate action included.
- Surfaces:
  - `/schedule/:zoom_meeting_id` (upcoming appointment view) — shown when the
    meeting's soft-linked `appointment_id` resolves to a patient; hidden
    otherwise (v1).
  - `/notes/:id` (appointment/patient detail) — same card for that
    appointment's patient.
- Gated by `isSessionPrepEnabled(email)`.
- Tests: component test with mocked client (pending → completed transition,
  render of talking points), feature-flag test.
- Size: ~300 LOC.

### Sequencing & staging rollout

1. Land PR 1 → 2 → 3 → 4 (Track A) and PR 5 → 6 → 7 (Track B); tracks can
   proceed in parallel.
2. Add pilot user's email to `STAGING_BETA_EMAILS` (and the backend
   session-prep staging set) — one-line changes inside PRs 2/6 or a separate
   trivial PR.
3. Push to `staging` branch → auto-deploy; pilot user validates both features;
   prod enablement later = adding their email to the prod allowlists.

## 5. Open questions (blocking none of PRs 1–5)

1. ~~Sovereign chatbot location~~ — resolved: `CosmaNeura/cosmaneura-sovereign`,
   reviewed 2026-07-22; no retrieval layer exists there (see §2), so PR 5 is
   the shared layer and Sovereign consumes it later.
2. ~~Pilot user's email~~ — resolved: pilot cohort for both features is
   `john@cosmaneura.com`, `dev@cosmaneura.com`, `greg@mycatholicdoctor.com`,
   `kathleen@mycatholicdoctor.com` (internal + external close-hold users);
   added to the staging allowlists in PRs 2/6.
3. **Public intake chat terminology** — pending. Restated: the public,
   unauthenticated intake page patients see says "Your doctor is requesting
   information about your health". Should it follow the owning provider's
   terminology setting (e.g. "Your therapist is requesting…")? Requires
   exposing the provider's terminology on the public chat API response.
   Recommend yes.
4. ~~Note-lookback window~~ — resolved: 18 months, approved 2026-07-22.
5. **Schedule view without a linked patient** — v1 hides the prep card;
   alternative is a patient picker. Confirm v1 behavior.
