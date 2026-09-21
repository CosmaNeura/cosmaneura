# Engagement Role Badge + Staging Demo Data — Implementation Plan

> **Historical record — superseded; do not execute its staging push, secret,
> seed, or database instructions.** A push to `staging` now validates only;
> staging deployment is founder-triggered and manual. Staging has not been
> established as synthetic-only/non-PHI, and any schema/data work requires a
> separately reviewed migration rung and rehearsal. Use the root `README.md`,
> `AGENTS.md`, and current workflow as the operational source of truth.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the engagement-engine role as a colored, tooltipped badge under the patient name on the note detail screen, and seed 6 demo encounters under `dev@cosmaneura.com` on staging so it's demoable.

**Architecture:** Frontend-only badge (the `/notes/{id}` API already returns `engagement_role`). A new `EngagementBadge` component, modeled on `StatusBadge`, rendered inside the inline `NoteHeader` in `web/app/routes/notes/detail.tsx`. A standalone, idempotent, reversible Python seed script writes demo rows directly to the staging DB. The frontend ships via the `staging` branch (`deploy-staging` job); nothing touches production (`master`).

**Tech Stack:** React Router + TypeScript + Tailwind/shadcn (web), Vitest + @testing-library/react (web tests), Python + activemodel/SQLModel (backend models + seed), 1Password CLI for staging DB access.

---

## Branch & environment

- All work happens on the **`staging`** branch (it already carries the engagement engine v2 + its migrations). Confirm with `git branch --show-current` → `staging` before starting.
- The seed connects to the **staging** database only. Production is never touched.

---

## Task 1: Verify staging preconditions (read-only)

Confirms the demo is even possible before building anything: the `dev@cosmaneura.com` doctor exists, the `engagement_role` table is deployed, and the doctor has a `DoctorNoteSchema` (needed to attach notes).

**Files:**
- Create: `tmp/check_staging_preconditions.py` (throwaway, deleted at end of Task 1)

- [ ] **Step 1: Find the staging DB connection-string 1Password reference**

Run:
```bash
op item list --account cosmaneurainc.1password.com --vault Staging 2>&1 | grep -iE "postgres|database"
```
Expected: a "Staging Postgres Credentials" or "Azure staging Postgres Database" item. Then inspect its field labels (values redacted):
```bash
op item get "Staging Postgres Credentials" --account cosmaneurainc.1password.com --format json \
  | python3 -c "import sys,json;d=json.load(sys.stdin);[print(f.get('label')) for f in d.get('fields',[])]"
```
Note the field that holds the full connection string (mirror of prod's `python-connection-string`). Record the `op://Staging/<item>/<field>` reference for use below.

- [ ] **Step 2: Write the precondition check script**

Create `tmp/check_staging_preconditions.py`:
```python
import os
os.environ["PYTHON_ENV"] = "development"  # SAFETY: never look like staging/prod -> no auto-migrate

import activemodel
from sqlalchemy import text
from activemodel.session_manager import get_session
from app.configuration.database import database_url

activemodel.init(database_url())  # direct connect, no migration

with get_session() as s:
    doc = s.exec(text("SELECT id FROM doctor WHERE email = 'dev@cosmaneura.com'")).first()
    print("dev@cosmaneura.com doctor:", "FOUND" if doc else "MISSING")

    table = s.exec(text(
        "SELECT to_regclass('public.engagement_role')"
    )).scalar()
    print("engagement_role table:", "EXISTS" if table else "MISSING")

    if doc:
        sch = s.exec(text(
            "SELECT id FROM doctor_note_schema WHERE doctor_id = :d LIMIT 1"
        ), {"d": doc[0]}).first()
        print("doctor_note_schema for dev:", "FOUND" if sch else "MISSING (seed will create one)")
```

- [ ] **Step 3: Run it against staging**

Run (substitute the `op://` reference found in Step 1):
```bash
cd /Users/oscarortega/work/cosmaneura-ink/cosmaneura
op_inject_source() { local f; f=$(mktemp); op inject > "$f" 2>/dev/null; source "$f"; rm -f "$f"; }
set -a
source .env >/dev/null 2>&1; source .env.shared >/dev/null 2>&1
source .env.staging.backend >/dev/null 2>&1
DATABASE_URL="$(op read 'op://Staging/<STAGING_PG_ITEM>/<FIELD>' --account cosmaneurainc.1password.com 2>/dev/null)"
PYTHON_ENV=development
set +a
.venv/bin/python tmp/check_staging_preconditions.py
```
Expected: `dev@cosmaneura.com doctor: FOUND` and `engagement_role table: EXISTS`.

**Decision gate:**
- If the doctor is MISSING → stop; the dev account must sign in to staging first (report back to user).
- If the table is MISSING → the `staging` branch must be deployed (which runs the engagement migrations) before seeding; do Task 4's deploy first, then return here.

- [ ] **Step 4: Clean up the throwaway check**

Run: `rm -f tmp/check_staging_preconditions.py`

---

## Task 2: `EngagementBadge` component (TDD)

A presentational pill, modeled on `web/app/components/shared/StatusBadge.tsx`, with a color + plain-language tooltip per role.

**Files:**
- Create: `web/app/components/shared/EngagementBadge.tsx`
- Test: `web/app/components/shared/EngagementBadge.test.tsx`
- Reference (read, do not change): `web/app/components/shared/StatusBadge.tsx`, `web/app/components/ui/tooltip.tsx`

- [ ] **Step 1: Write the failing test**

Create `web/app/components/shared/EngagementBadge.test.tsx`:
```tsx
import { render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { EngagementBadge } from "./EngagementBadge"

describe("EngagementBadge", () => {
  it("renders the role name", () => {
    render(<EngagementBadge roleKey="collaborative" roleName="Collaborative" />)
    expect(screen.getByText("Collaborative")).toBeInTheDocument()
  })

  it("applies a distinct color class per role", () => {
    const { container: c1 } = render(
      <EngagementBadge roleKey="self_directed" roleName="Self-Directed" />,
    )
    const { container: c2 } = render(
      <EngagementBadge roleKey="directed" roleName="Directed" />,
    )
    // the two roles must not share the same root className
    expect(c1.firstElementChild?.className).not.toEqual(
      c2.firstElementChild?.className,
    )
  })

  it("exposes a description for the role (accessible tooltip text)", () => {
    render(<EngagementBadge roleKey="directed" roleName="Directed" />)
    // description is rendered into the DOM (tooltip content / title)
    expect(
      screen.getByText(/clinician.*decide|decide.*for/i),
    ).toBeInTheDocument()
  })

  it("falls back gracefully for an unknown role key", () => {
    render(<EngagementBadge roleKey={"???" as never} roleName="Mystery" />)
    expect(screen.getByText("Mystery")).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd web && pnpm exec vitest run app/components/shared/EngagementBadge.test.tsx`
Expected: FAIL — `Failed to resolve import "./EngagementBadge"` (module doesn't exist yet).

- [ ] **Step 3: Confirm the tooltip primitive's exports**

Run: `grep -nE "export (function|const) (Tooltip|TooltipTrigger|TooltipContent|TooltipProvider)" web/app/components/ui/tooltip.tsx`
Expected: the four shadcn exports. (If `TooltipProvider` is absent, wrap with the available provider, or use the `title`-attribute fallback shown in Step 4's comment.)

- [ ] **Step 4: Implement the component**

Create `web/app/components/shared/EngagementBadge.tsx`:
```tsx
import { cn } from "@/lib/utils"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"

export type EngagementRoleKey = "self_directed" | "collaborative" | "directed"

type RoleMeta = { description: string; badge: string; dot: string }

const ROLE_META: Record<EngagementRoleKey, RoleMeta> = {
  self_directed: {
    description: "Wants full information and to lead the decision themselves.",
    badge: "bg-success/10 text-success",
    dot: "bg-success",
  },
  collaborative: {
    description: "Wants to share the decision with the clinician.",
    badge: "bg-primary/10 text-primary",
    dot: "bg-primary",
  },
  directed: {
    description: "Prefers the clinician to lead and decide.",
    badge: "bg-warning/10 text-warning",
    dot: "bg-warning",
  },
}

const FALLBACK: RoleMeta = {
  description: "Communication style estimate.",
  badge: "bg-muted text-muted-foreground",
  dot: "bg-muted-foreground",
}

export function EngagementBadge({
  roleKey,
  roleName,
  className,
}: {
  roleKey: EngagementRoleKey
  roleName: string
  className?: string
}) {
  const meta = ROLE_META[roleKey] ?? FALLBACK

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            className={cn(
              "inline-flex cursor-default items-center rounded-full px-2.5 py-0.5 text-xs font-medium",
              meta.badge,
              className,
            )}
          >
            <span className={cn("mr-1 h-1.5 w-1.5 rounded-full", meta.dot)} />
            {roleName}
          </span>
        </TooltipTrigger>
        <TooltipContent>
          <p className="max-w-xs">{meta.description}</p>
          <p className="text-muted-foreground mt-1 text-[10px]">
            Communication aid — not a diagnosis.
          </p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
```
Note: `TooltipContent` renders the description into the DOM, satisfying the test's `getByText`. If `web/app/components/ui/tooltip.tsx` lacks `TooltipProvider`, drop the provider wrapper and add `title={meta.description}` on the `span` instead (still satisfies the test via the accessible name — adjust the test's third case to `getByTitle` if so).

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd web && pnpm exec vitest run app/components/shared/EngagementBadge.test.tsx`
Expected: PASS (4 tests).

- [ ] **Step 6: Typecheck**

Run: `cd web && pnpm exec tsc --noEmit`
Expected: no errors in `EngagementBadge.tsx`.

- [ ] **Step 7: Commit**

```bash
git add web/app/components/shared/EngagementBadge.tsx web/app/components/shared/EngagementBadge.test.tsx
git commit -m "feat(notes): EngagementBadge component (role pill + tooltip)"
```

---

## Task 3: Render the badge under the patient name in `NoteHeader`

`NoteHeader` is defined inline in `detail.tsx`. Add an optional `engagementRole` prop and render `EngagementBadge` directly under the name. Pass `loaderData.engagement_role` at the call site. When absent, render nothing.

**Files:**
- Modify: `web/app/routes/notes/detail.tsx`

- [ ] **Step 1: Confirm the loader exposes `engagement_role`**

Run: `grep -nE "engagement_role|engagementRole" web/app/configuration/client* web/openapi.json | head`
Expected: `engagement_role` present in the generated client / openapi (it is part of the `NoteDetail` schema). If missing from the generated client, regenerate first: `cd web && pnpm run openapi`.

- [ ] **Step 2: Import `EngagementBadge` in `detail.tsx`**

Add to the imports block (near the `StatusBadge` import):
```tsx
import { EngagementBadge } from "~/components/shared/EngagementBadge"
import type { EngagementRoleKey } from "~/components/shared/EngagementBadge"
```

- [ ] **Step 3: Add the prop to `NoteHeader` and render the badge under the name**

In `detail.tsx`, change the inline `NoteHeader` signature and body. Replace:
```tsx
function NoteHeader({
  status,
  title,
  dataFields,
}: {
  status: Status
  title: string
  dataFields: Record<string, string>
}) {
  return (
    <div className="mb-6 flex items-start justify-between">
      <div className="text-left">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-semibold">{title}</h1>
          <StatusBadge status={status} />
        </div>
        <div className="mt-1 space-y-1">
```
with:
```tsx
function NoteHeader({
  status,
  title,
  dataFields,
  engagementRole,
}: {
  status: Status
  title: string
  dataFields: Record<string, string>
  engagementRole?: { role_key: string; role_name: string } | null
}) {
  return (
    <div className="mb-6 flex items-start justify-between">
      <div className="text-left">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-semibold">{title}</h1>
          <StatusBadge status={status} />
        </div>
        {engagementRole ? (
          <div className="mt-2">
            <EngagementBadge
              roleKey={engagementRole.role_key as EngagementRoleKey}
              roleName={engagementRole.role_name}
            />
          </div>
        ) : null}
        <div className="mt-1 space-y-1">
```

- [ ] **Step 4: Pass `engagement_role` at the call site**

In the `NoteDetailPage` component, find the `<NoteHeader ... />` usage and add the prop:
```tsx
<NoteHeader
  title={loaderData.patient.name}
  status={hasNotes ? "completed" : "not-started"}
  engagementRole={loaderData.engagement_role}
  dataFields={{
    "Appointment Date": standardDateFormat(
      loaderData.appointment.appointment_date,
    ),
  }}
/>
```

- [ ] **Step 5: Typecheck**

Run: `cd web && pnpm exec tsc --noEmit`
Expected: no errors. (If `loaderData.engagement_role`'s generated type doesn't match `{ role_key; role_name }`, narrow the prop type to the generated type instead of casting.)

- [ ] **Step 6: Build to confirm the route compiles**

Run: `cd web && pnpm run build`
Expected: build succeeds.

- [ ] **Step 7: Commit**

```bash
git add web/app/routes/notes/detail.tsx
git commit -m "feat(notes): show engagement role badge under patient name on note detail"
```

---

## Task 4: Demo seed script (idempotent + reversible)

A standalone script that creates 6 complete demo encounters under `dev@cosmaneura.com`, each with a fixed role. Re-runnable (no duplicates) and removable (`--delete`).

**Files:**
- Create: `playground/seed_engagement_demo.py` (committed — reusable + reversible)

- [ ] **Step 1: Write the seed script**

Create `playground/seed_engagement_demo.py`:
```python
"""Seed demo encounters with engagement roles under dev@cosmaneura.com (staging).

Idempotent: re-running does not duplicate (rows tagged created_by_system='seed-demo'
and patient names prefixed '[Demo] '). Reversible: `--delete` removes all demo rows.

Run against STAGING only, with secrets injected and DATABASE_URL overridden to the
staging connection string. PYTHON_ENV is forced to development so no migration runs.

    .venv/bin/python playground/seed_engagement_demo.py          # seed
    .venv/bin/python playground/seed_engagement_demo.py --delete # teardown
"""

import os
import sys

os.environ["PYTHON_ENV"] = "development"  # SAFETY: no auto-migration

import activemodel
from activemodel.session_manager import get_session
from sqlalchemy import text

from app.configuration.database import database_url
from app.engagement.cosmas_engagement.version import (
    ENGINE_VERSION,
    FRAMEWORK_VERSION,
    RUBRIC_VERSION,
)
from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.engagement_role import ConfidenceBand, EngagementRole
from app.models.patient import Patient

from datetime import datetime, timezone, timedelta

DEMO_TAG = "seed-demo"
DEV_EMAIL = "dev@cosmaneura.com"

# 6 encounters: 3 collaborative, 2 self_directed, 1 directed.
ENCOUNTERS = [
    # (patient_first_name, role_key, role_name, cps_role, dc, ie, concern, confidence)
    ("Avery",  "collaborative", "Collaborative", "collaborative", 52, 58, 20, 0.58),
    ("Jordan", "collaborative", "Collaborative", "collaborative", 48, 50, 35, 0.51),
    ("Riley",  "collaborative", "Collaborative", "collaborative", 55, 60, 15, 0.62),
    ("Sam",    "self_directed", "Self-Directed", "active",        82, 78, 25, 0.71),
    ("Quinn",  "self_directed", "Self-Directed", "active",        76, 70, 18, 0.66),
    ("Parker", "directed",      "Directed",      "passive",       22, 30, 55, 0.49),
]

DIALOGUE = {
    "collaborative": [
        ("Patient", "I'd like to hear the options and decide together with you."),
        ("Doctor",  "Let's go through them. Here's what I'd recommend and why."),
        ("Patient", "That makes sense. What matters most to me is staying active."),
    ],
    "self_directed": [
        ("Patient", "Can you walk me through the evidence? I want to make the final call."),
        ("Doctor",  "Of course. Here are the trade-offs and the studies behind them."),
        ("Patient", "Thanks. I've researched this and I'd prefer the second option."),
    ],
    "directed": [
        ("Patient", "Whatever you think is best, doctor. Just tell me what to do."),
        ("Doctor",  "We'll start with this medication and check back in two weeks."),
        ("Patient", "Okay, I trust you. I'm a little worried but I'll follow your advice."),
    ],
}

NOTE_BODY = {
    "subjective": "Patient presents for routine follow-up. See transcript.",
    "objective": "Vitals within normal limits.",
    "assessment": "Stable. Demo encounter.",
    "plan": "Continue current plan; follow up in two weeks.",
}


def _band(conf: float) -> ConfidenceBand:
    if conf >= 0.66:
        return ConfidenceBand.high
    if conf >= 0.40:
        return ConfidenceBand.moderate
    return ConfidenceBand.low


def _distribution(role_key: str) -> dict:
    base = {"self_directed": 0.2, "collaborative": 0.2, "directed": 0.2}
    base[role_key] = 0.6
    # normalize the remaining 0.4 across the other two
    others = [k for k in base if k != role_key]
    for k in others:
        base[k] = 0.2
    return {k: round(v, 4) for k, v in base.items()}


def get_dev_doctor() -> Doctor:
    doc = Doctor.get(email=DEV_EMAIL)
    assert doc, f"Doctor {DEV_EMAIL} not found on this DB — sign in on staging first."
    return doc


def get_or_create_note_schema(doctor: Doctor) -> DoctorNoteSchema:
    existing = DoctorNoteSchema.where(DoctorNoteSchema.doctor_id == doctor.id).first()
    if existing:
        return existing
    return DoctorNoteSchema(
        doctor_id=doctor.id,
        note_type="SOAP (demo)",
        fields={
            "subjective": "Subjective",
            "objective": "Objective",
            "assessment": "Assessment",
            "plan": "Plan",
        },
    ).save()


def delete_demo(doctor: Doctor) -> None:
    appts = list(
        Appointment.where(
            Appointment.doctor_id == doctor.id,
            Appointment.created_by_system == DEMO_TAG,
        ).all()
    )
    appt_ids = [a.id for a in appts]
    n = 0
    with get_session() as s:
        for aid in appt_ids:
            for er in EngagementRole.where(EngagementRole.appointment_id == aid).all():
                s.delete(er)
            for note in AIVisitNote.where(AIVisitNote.appointment_id == aid).all():
                s.delete(note)
            for tr in AIVisitTranscript.where(
                AIVisitTranscript.appointment_id == aid
            ).all():
                s.delete(tr)
        s.commit()
    for a in appts:
        for p in Patient.where(
            Patient.id == a.patient_id, Patient.doctor_id == doctor.id
        ).all():
            if p.name.startswith("[Demo] "):
                p.delete()
        a.delete()
        n += 1
    print(f"deleted {n} demo encounters")


def seed(doctor: Doctor) -> None:
    schema = get_or_create_note_schema(doctor)
    base_date = datetime.now(timezone.utc)
    created = 0
    for i, (first, role_key, role_name, cps, dc, ie, concern, conf) in enumerate(
        ENCOUNTERS
    ):
        patient_name = f"[Demo] {first} Demo"
        # idempotency: skip if this demo patient already has a tagged appointment
        existing_patient = Patient.where(
            Patient.doctor_id == doctor.id, Patient.name == patient_name
        ).first()
        if existing_patient:
            print(f"skip {patient_name} (already seeded)")
            continue

        patient = Patient(
            doctor_id=doctor.id,
            name=patient_name,
            date_of_birth=datetime(1980 + i, 1, 1, tzinfo=timezone.utc),
        ).save()

        appointment = Appointment(
            doctor_id=doctor.id,
            patient_id=patient.id,
            appointment_date=base_date - timedelta(days=i + 1),
            external_id=f"{DEMO_TAG}-{i}",
            created_by_system=DEMO_TAG,
        ).save()

        transcript = AIVisitTranscript(
            doctor_id=doctor.id,
            appointment_id=appointment.id,
            patient_id=patient.id,
            duration=600,
            transcript_data=[
                TranscriptEntry(
                    speaker=spk, content=txt, startTime=0, endTime=1000
                )
                for spk, txt in DIALOGUE[role_key]
            ],
        ).save()

        AIVisitNote(
            doctor_id=doctor.id,
            appointment_id=appointment.id,
            ai_transcript_id=transcript.id,
            doctor_note_schema_id=schema.id,
            note=dict(NOTE_BODY),
        ).save()

        EngagementRole(
            appointment_id=appointment.id,
            doctor_id=doctor.id,
            ai_transcript_id=transcript.id,
            role_key=role_key,
            role_name=role_name,
            cps_role=cps,
            confidence=conf,
            confidence_band=_band(conf),
            channel="enacted",
            decision_control=float(dc),
            information_engagement=float(ie),
            expressed_concern=float(concern),
            decision_control_confidence=0.7,
            information_engagement_confidence=0.7,
            concern_flag=concern >= 60,
            distribution=_distribution(role_key),
            baseline_source=DEMO_TAG,
            model=DEMO_TAG,
            engine_version=ENGINE_VERSION,
            framework_version=FRAMEWORK_VERSION,
            rubric_version=RUBRIC_VERSION,
        ).save()
        created += 1
        print(f"seeded {patient_name} -> {role_name}")
    print(f"done: {created} new encounters")


def main() -> None:
    activemodel.init(database_url())  # direct connect, no migration
    doctor = get_dev_doctor()
    if "--delete" in sys.argv:
        delete_demo(doctor)
    else:
        seed(doctor)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Syntax-check the script**

Run: `.venv/bin/python -m py_compile playground/seed_engagement_demo.py && echo OK`
Expected: `OK`.

- [ ] **Step 3: Commit the script**

```bash
git add playground/seed_engagement_demo.py
git commit -m "feat(demo): idempotent/reversible engagement demo seeder for staging"
```

---

## Task 5: Deploy to staging, run the seed, verify end-to-end

**Files:** none (operational).

- [ ] **Step 1: Push the `staging` branch to deploy the frontend**

Run:
```bash
git push origin staging
```
Expected: push succeeds; the `Build & Publish` workflow runs the `deploy-staging` job (`environment: staging`). Confirm:
```bash
gh run list --workflow=build_and_publish.yml --branch=staging --limit 1
```
Expected: a run in progress / queued. Wait for it to complete green before the manual check.

- [ ] **Step 2: (If Task 1 found the table MISSING) confirm migrations ran**

After the staging deploy completes, re-run the Task 1 precondition check. Expected now: `engagement_role table: EXISTS`. Do not seed until it does.

- [ ] **Step 3: Run the seed against staging**

Run (same env setup + `op://` reference as Task 1 Step 3):
```bash
cd /Users/oscarortega/work/cosmaneura-ink/cosmaneura
op_inject_source() { local f; f=$(mktemp); op inject > "$f" 2>/dev/null; source "$f"; rm -f "$f"; }
set -a
source .env >/dev/null 2>&1; source .env.shared >/dev/null 2>&1
source .env.staging.backend >/dev/null 2>&1
DATABASE_URL="$(op read 'op://Staging/<STAGING_PG_ITEM>/<FIELD>' --account cosmaneurainc.1password.com 2>/dev/null)"
PYTHON_ENV=development
set +a
.venv/bin/python playground/seed_engagement_demo.py
```
Expected: `seeded [Demo] ... -> Collaborative/Self-Directed/Directed` lines and `done: 6 new encounters`.

- [ ] **Step 4: Verify idempotency**

Run the same command again.
Expected: six `skip ... (already seeded)` lines and `done: 0 new encounters`.

- [ ] **Step 5: Manual end-to-end check**

Log in to the staging app as `dev@cosmaneura.com`. Open the notes list → the six `[Demo] …` patients appear. Open one of each role:
- Badge renders under the patient name.
- Colors differ across the three roles (Collaborative / Self-Directed / Directed).
- Tooltip shows the role description on hover.
- (Sanity) any pre-existing note without a role shows no badge.

- [ ] **Step 6: Report status to the user**

Summarize: deploy result, seeded counts, and the verification outcome. Mention the teardown command (`… seed_engagement_demo.py --delete`) for when the demo is over.

---

## Notes for the implementer

- **Never push this work to `master`.** The badge ships only via `staging`; the seed writes only to the staging DB.
- The `op://Staging/...` connection-string reference is discovered in Task 1 Step 1 — use the same one in Task 5.
- If `pnpm` is unavailable, use the package manager the repo uses (`corepack enable` / check `web/package.json` `packageManager`).
- Teardown after the demo: `… seed_engagement_demo.py --delete` removes all `seed-demo`-tagged rows.
