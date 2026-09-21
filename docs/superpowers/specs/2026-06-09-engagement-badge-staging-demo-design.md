# Engagement Role Badge + Staging Demo Data — Design

> **Historical record — superseded; not operational authorization.** Staging
> deployment is now founder-triggered and manual, not push-triggered. Staging
> has not been established as synthetic-only/non-PHI, so this document does not
> authorize seed data, database access, or deployment. Follow the root
> `README.md`, `AGENTS.md`, and current workflow instead.

**Date:** 2026-06-09
**Status:** Approved (brainstorming) — pending spec review before planning
**Target environment:** `staging` only (must not touch production)
**Goal:** Let the boss see the engagement-engine classification in the product — a colored
badge with the patient's communication role under their name on a note — using seeded demo
data under the `dev@cosmaneura.com` account, since staging has no real scored encounters.

---

## 1. Overview

The engagement engine v2 (already on the `staging` branch) classifies a patient's
communication / decision-making role into one of three: **Self-Directed**, **Collaborative**,
**Directed** (CPS `active` / `collaborative` / `passive`). This work makes that visible in the
notes UI and seeds enough demo data on staging to demonstrate it.

Two deliverables:

1. **Frontend badge** — render the role as a colored pill with a tooltip, below the patient
   name on the note detail screen. Backend already serves the data.
2. **Demo seed** — an idempotent, reversible script that creates 6 fully-formed fake
   encounters under `dev@cosmaneura.com` in the staging database, each with a fixed role.

Out of scope: the sidebar/list badge (detail view only), the notes-list API change, running
the real LLM scoring, and anything touching production.

---

## 2. Backend (no change required)

Confirmed during exploration — the note detail endpoint already returns the role:

- `GET /notes/{appointment_id}` → `NoteDetail`, which includes
  `engagement_role: EngagementRole | None`, populated by
  `EngagementRole.get(appointment_id=appointment_id)` (`app/routes/internal.py`).
- The React loader for `web/app/routes/notes/detail.tsx` therefore already has
  `loaderData.engagement_role` available (verify the loader passes the field through; if the
  loader's type/selection omits it, add it — this is the only possible backend-adjacent touch).

`EngagementRole` relevant fields for display: `role_key`, `role_name`, `cps_role`,
`confidence`, `confidence_band` (`Low|Moderate|High`).

---

## 3. Frontend — `EngagementBadge`

**New component:** `web/app/components/shared/EngagementBadge.tsx`, modeled on the existing
`StatusBadge.tsx` (rounded pill, leading colored dot, `text-xs font-medium`).

- **Props:** `{ roleKey: "self_directed" | "collaborative" | "directed", roleName: string,
  summary?: string, className?: string }`.
- **Label:** `roleName` (e.g. "Collaborative").
- **Color per role** (distinct, using theme tokens / a small fixed palette):
  - `self_directed` → green-ish (e.g. `success`)
  - `collaborative` → blue/primary (e.g. `primary`)
  - `directed` → amber/secondary (e.g. `warning`)
  - The dot + background tint follow the role color, same structure as `StatusBadge`.
- **Tooltip:** on hover, a short plain-language explanation of the role (sourced from the
  role's `short_label`/`summary`, e.g. "Wants to decide together"). Use the existing tooltip
  primitive in `web/app/components/ui/` (e.g. shadcn `Tooltip`); if none exists, fall back to a
  native `title` attribute. A one-line product disclaimer ("communication aid, not a
  diagnosis") may be appended to the tooltip.

**Placement:** in the `NoteHeader` used by `detail.tsx` (the block that renders
`title={loaderData.patient.name}` with status + dataFields). Render `EngagementBadge`
**directly under the patient name**. When `loaderData.engagement_role` is `null`, render
nothing (clean degradation — existing notes without a score look unchanged).

No change to the top `Header` bar component; only the in-content `NoteHeader`.

---

## 4. Demo seed script

**A standalone, idempotent, reversible script** (not part of the app build), following the
safe production-access pattern already used this session:

- Loads the **staging** secrets via 1Password, overrides `DATABASE_URL` with the staging
  connection string, forces `PYTHON_ENV=development`, and **never calls
  `setup()`/`configure_database()`** (no auto-migration). Direct `activemodel.init` + inserts.

**Preconditions (verified, script aborts with a clear message if unmet):**
1. A `Doctor` with `email == "dev@cosmaneura.com"` exists in staging.
2. The `engagement_role` (and related) tables exist on the staging DB — i.e. the engagement
   migrations from the `staging` branch have been deployed/applied there.

**What it creates** — 6 encounters under that doctor, each a complete chain:
`Patient` → `Appointment` → `AIVisitTranscript` → `AIVisitNote` → `EngagementRole`.

- **Role mix:** 3 `collaborative`, 2 `self_directed`, 1 `directed` (so all three badge colors
  appear; roughly realistic).
- **EngagementRole values** are seeded directly (deterministic), with coherent fields:
  `role_key`/`role_name`/`cps_role` per role, a plausible `confidence` + matching
  `confidence_band`, plausible `decision_control`/`information_engagement`/`expressed_concern`
  and their confidences, a normalized `distribution`, `channel="enacted"`,
  `baseline_source="seed-demo"`, and `model="seed-demo"` / engine+framework+rubric versions
  from the engine constants.
- **Transcripts/notes** carry short, realistic fake dialogue + a short note body so the detail
  screen looks populated (no PHI — synthetic patients).

**Idempotency & reversibility:**
- Demo rows are tagged: `Appointment.created_by_system = "seed-demo"` and patient names use a
  recognizable prefix (e.g. `"[Demo] <name>"`).
- Re-running skips encounters that already exist (match on the tag + a stable per-encounter
  key), so it never duplicates.
- A `--delete` flag removes all `seed-demo`-tagged demo data (engagement_role, notes,
  transcripts, appointments, patients) for clean teardown.

---

## 5. Deployment & sequence

1. **Verify preconditions** on staging (dev doctor exists; `engagement_role` table present).
2. **Frontend → `staging` branch:** commit `EngagementBadge` + the `NoteHeader` change; push to
   `staging`. This triggers the `deploy-staging` job (`environment: staging`) and deploys only
   to staging — production (`master`) is untouched.
3. **Run the seed** once against the staging DB.
4. **Verify** by logging in as `dev@cosmaneura.com` on staging: open notes → badge appears
   under the patient name; the six notes show the three role colors; tooltips read correctly;
   a note without a role shows no badge.

**Isolation guarantee:** the badge code lands on `staging` (never `master`), and demo data goes
only to the staging DB and is tagged for one-command teardown.

---

## 6. Testing / verification

- **Frontend:** a small unit/render test for `EngagementBadge` (correct label, color class, and
  tooltip text per role; renders nothing when role is absent). Follow existing web test
  conventions.
- **Seed:** running it twice produces exactly 6 demo encounters (idempotent); `--delete` leaves
  zero `seed-demo` rows. Run read-back assertions after seeding (counts per role).
- **End-to-end:** manual check on deployed staging as `dev@cosmaneura.com` (step 4 above).

---

## 7. Risks / notes

- **Staging migration dependency:** if the engagement migrations aren't applied on staging yet,
  seeding fails the precondition check — resolve by deploying the `staging` branch first.
- **Tooltip primitive:** if no shadcn `Tooltip` exists in the web app, use the native `title`
  fallback rather than adding a new dependency.
- **Loader field:** verify `detail.tsx`'s loader actually forwards `engagement_role`; if its
  response type is narrowed, widen it (smallest possible change).
- **Demo realism:** seeded data is clearly tagged `[Demo]` / `seed-demo` so it's never confused
  with real patients and is trivially removable.
