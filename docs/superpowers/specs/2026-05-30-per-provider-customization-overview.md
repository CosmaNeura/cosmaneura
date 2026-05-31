# Per-Provider Note Customization — Overview & Decomposition

**Date:** 2026-05-30
**Issue:** #33 (notetaking improvements → provider self-service customization)
**Status:** Design approved; specs written for all four sub-projects.

## Background

Issue #33's notetaking improvements (PRs #60–64) were implemented by editing the
**global** numbered instruction block (1–16) inside
`app/jobs/summarize.py::build_structured_prompt`. That block applies to **every
provider and every note type**. The boss clarified this is *not* what we want: a
prompt change must **not** automatically migrate to other users / note types. The
roadmap goal is to let each provider **customize their own AI output on the
platform**, without us editing prompts on their behalf.

The architecture is already substantially per-provider:

- `DoctorNoteSchema` (`app/models/doctor_note_schema.py`) is per-doctor + per-note-type
  (`doctor_id` FK, user-generated `note_type`, `fields: dict[str, str]` of
  field→instruction). Providers upload example notes; the system extracts fields.
- `build_structured_prompt(transcript, example_note, note_schema)` injects the
  doctor's bio, their schema's fields, and an example note — **plus** the global
  1–16 block. The global block is the only fully-global part.

So "correcting the direction" means: move the **configurable behaviors** out of the
global block and into per-note-type configuration that the provider controls, while
keeping genuinely universal **house rules** fixed.

## Design decisions (approved 2026-05-30)

1. **Goal:** full self-service — backend + API + provider-facing UI.
2. **Primitive:** hybrid — structured presets/toggles for the known #33 behaviors
   **plus** a free-text escape hatch.
3. **Granularity:** per note-type (one configuration per `DoctorNoteSchema`).
4. **Defaults:** toggles default to the **current** (post-#33) behavior. Backwards
   compatible — no existing user sees an output change at deploy. Providers opt to
   change theirs; their change affects only their own note-type.
5. **House rules stay fixed:** JSON-only output, no speculation, professional
   language, condition grouping, action-item coverage, style imitation, step-by-step.

## What is configurable vs fixed

Configurable presets (each maps to a shipped #33 instruction):

| Preset | Type | Default | Maps to global instruction |
|---|---|---|---|
| `subjective_source` | enum `patient_only` / `patient_and_doctor` | `patient_only` | #9 |
| `pronoun_style` | enum `infer_from_transcript` / `neutral` | `infer_from_transcript` | #8 |
| `plan_ordering` | enum `patient_then_doctor` / `as_discussed` | `patient_then_doctor` | #3 sub-bullet |
| `flag_new_medications` | bool | `true` | #10 |
| `require_plan_reason` | bool | `true` | #11 |
| `ros_distinct_from_subjective` | bool | `true` | #12 |
| `custom_instructions` | text \| null | null | escape hatch (appended) |

Fixed house rules (always emitted, not configurable): instructions #1, #2, #3 (the
condition grouping/ordering/consistency body — *minus* the patient-then-doctor
sub-bullet, which becomes `plan_ordering`), #4, #5, #6, #7, #13, #14, #15, #16.

## Sub-projects

| # | Spec | Depends on | Summary |
|---|---|---|---|
| SP1 | `…-sp1-backend-foundation-design.md` | — | `NoteTypeCustomization` model + config-driven prompt assembly; defaults reproduce current output |
| SP2 | `…-sp2-api-design.md` | SP1 | CRUD endpoints for a note-type's customization |
| SP3 | `…-sp3-frontend-ui-design.md` | SP2 | Provider-facing per-note-type customization editor |
| SP4 | `…-sp4-eval-harness-design.md` | SP1 (parallel to SP2/SP3) | Per-config eval harness; regression that defaults == current; isolation proof |

Each sub-project gets its own implementation plan and PR. SP1 ships with **zero
behavior change** (defaults), which is the safest possible first step and the one
that structurally resolves the "no auto-migration" concern.

## "Eval set" / "prompt change regression" — clarification

An **eval set** is test transcripts + criteria graded by a judge model
(`tests/eval.py`, `tests/job/summarize_test.py`, gated by `RUN_LIVE_LLM_TESTS`).
**Prompt-change regression** = re-running those evals after a prompt change to
confirm prior behavior still holds. This is a **test-only** mechanism: it never
changes production output and never migrates anything between users. Because the
existing evals were written against the global prompt, SP4 makes them per-config.
