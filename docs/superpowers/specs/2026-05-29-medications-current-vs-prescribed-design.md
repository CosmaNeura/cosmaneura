# Design — Medications section: distinguish current from newly-prescribed

**Issue:** #33 (item 4 of 6)
**Date:** 2026-05-29
**Author:** Oscar (with Claude)

## Background

Fourth item in issue #33, requested by Jensen (5/5/25): "notes should distinguish between current medications and medications to be prescribed." Today the Medications section of a generated note is a flat list — there is no signal indicating which entries the patient was already on versus which the doctor newly added at this visit. Jensen needs to see at a glance what changed on her end, since that's the clinically actionable delta.

Items 1+2 shipped in PR #60 (`cbf0c50`). Item 3 is in flight as PR #61 — this work stacks on top.

## Constraints

- **Per-doctor schemas.** `DoctorNoteSchema.fields` varies per doctor; some schemas have a `Medications` field, some don't. Any change must degrade gracefully when the field is absent: the model just has nothing to render. We're tightening guidance, not adding a required field.
- **"No subsections within fields" rule already in the prompt.** Instruction #3 already says "Do not create subsections within the fields for the conditions. Prefer 'flat' lists of items unless absolutely necessary." That rule was about organising by condition. Carving Medications into "Current" + "Newly Prescribed" subsections would directly contradict it. Use **inline tagging** of newly-prescribed items instead.
- **Plan section overlap.** The Plan section already mentions newly-prescribed meds (e.g. "Start empagliflozin 10mg daily"). We're not removing them from Plan; the Medications section needs the SAME information labelled differently so a reader looking at Medications alone gets the full picture.
- **Live-LLM eval gating** + **judge-model 404 on staging** — same as PRs #60, #61. Verification falls back to a manual one-off script.

## Approach

Single-PR prompt-text change adding one new numbered instruction (item 10) between the existing items 9 (Subjective sourcing) and 10 (action items). Items 10-13 shift to 11-14. One new live-LLM eval test in `tests/job/summarize_test.py` using an in-memory transcript where the patient lists two current medications at intake and the doctor prescribes a third during the visit.

### 1. Prompt change

In `app/jobs/summarize.py::build_structured_prompt`, insert a new numbered item between current item 9 (Subjective) and current item 10 (action items):

```
10. Medications section: when this section exists in the schema, list both medications the patient is already taking (from "Patient:" lines) AND medications the doctor is prescribing or initiating during this visit. Tag newly-prescribed items inline with "(new this visit)" so they can be distinguished from the patient's existing medication list at a glance. Do not put newly-prescribed meds only in the Plan section -- the Medications section should reflect the post-visit medication list as a whole, with new additions flagged.
```

Renumber the trailing items: old 10 → 11 (action items), old 11 → 12 (imitate), old 12 → 13 (JSON), old 13 → 14 (deep breath).

### 2. Eval test

Add `test_medications_section_distinguishes_current_from_prescribed` to `tests/job/summarize_test.py`. The transcript: patient mentions two current meds (lisinopril, atorvastatin) at intake; doctor prescribes a third (omeprazole) for new heartburn complaints.

Criteria for `eval_n_of_m`:

> "The 'Medications' section of this clinical note must (a) list lisinopril and atorvastatin as the patient's current medications, and (b) list omeprazole AND clearly tag it as newly prescribed at this visit (acceptable phrasings include '(new this visit)', '(NEW)', '(prescribed today)', '(initiated today)', or similar inline labels). If the Medications section omits omeprazole, or includes omeprazole but does NOT distinguish it from the current medications, the test fails."

Gated by the existing `pytestmark` (same skipif as items 1-3).

### 3. Manual verification fallback

`/tmp/manual_verify_medications.py`: small one-off script that constructs the same in-memory transcript, calls `generate_clinical_note`, and prints the Medications section so we can eyeball whether newly-prescribed items are tagged. Used to establish baseline and confirm fix. Not committed.

### 4. Out of scope

- Items 5 (plan-bullet reasons) and 6 (ROS vs Subjective) of #33 — separate PRs.
- Adding a `prescribed_at_visit` boolean to any model. The distinction lives in the prompt, not the data layer.
- Restructuring `DoctorNoteSchema` to enforce a Medications field. Doctors choose their own schemas.

## Risks and rollback

- **Risk: model tags wrong items as new.** If the patient says "I just started taking lisinopril last week", is that "current" or "newly prescribed"? The instruction defines "newly prescribed" as initiated *during this visit*. Mitigation: the wording emphasises "prescribing or initiating during this visit"; meds the patient started outside the visit are current.
- **Risk: model double-lists prescribed items as both current and new.** Unlikely given the explicit "tag inline" framing, but possible. The eval criteria catches this case (it explicitly requires a distinguishing tag).
- **Risk: Plan/Medications duplication looks redundant.** Reviewer may notice both sections mention the new med. That's intentional — Plan describes the action (start it, dose, frequency); Medications shows the post-visit list. The redundancy is a feature for clinical scanning.
- **Rollback:** revert one commit. No DB migration, no FE change.

## Affected files

- `app/jobs/summarize.py` — insert new item 10 in `build_structured_prompt`, renumber 10-13 → 11-14.
- `tests/job/summarize_test.py` — append one new test function.
- No `tests/seeds.py` change.
