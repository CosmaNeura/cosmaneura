# Plan bullet ordering — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land item 3 of issue #33 — within each condition's Plan bullets, list patient-actionable items before doctor-actionable items.

**Architecture:** Single-PR prompt-text change adding one sub-bullet to instruction #3 in `app/jobs/summarize.py::build_structured_prompt` (no renumbering, no new numbered item). One new live-LLM eval test in `tests/job/summarize_test.py` using an in-memory single-condition (diabetes) transcript with a mix of patient and doctor todos. Verification falls back to a manual one-off script in `/tmp/` because the live eval-judge call returns 404 on staging Azure OpenAI (`gpt-4.1-mini-2025-04-14` deployment missing) — same workaround as PR #60.

**Tech Stack:** Python 3, pytest, Azure OpenAI Chat Completions (gpt-4.1-2025-04-14 for note gen), existing ActiveModel/SQLModel ORM, existing `tests/eval.py` LLM-as-judge helper.

**Spec:** `docs/superpowers/specs/2026-05-28-plan-bullet-ordering-design.md`

**Branch:** `feat/note-plan-bullet-ordering` (already created from master at `cbf0c50`, the merge of PR #60).

---

## File Structure

**Modify:**
- `app/jobs/summarize.py` — add one new sub-bullet inside instruction #3 (currently at `:235-240`). Insert it BEFORE the last sub-bullet ("Do not create subsections within the fields...") so the new rule applies inside the existing condition-grouping context.
- `tests/job/summarize_test.py` — append one new test function at end of file (after `test_subjective_excludes_doctor_interpretation` which is currently the last test).

**Do not modify:**
- `app/models/*.py` — no schema changes.
- `tests/seeds.py` — reuse existing seeded `Ocie Dosie` + Test SOAP Note schema + example note pattern; in-memory transcript only.
- `tests/eval.py` — existing `eval_n_of_m` helper is sufficient.
- Items 1+2 prompt instructions (currently numbered 8 and 9) — unchanged.

---

## Local prerequisite for running evals + manual verification

Same as PR #60. Two prerequisites:

1. **Azure OpenAI creds in shell.** Direnv won't load them because the `.envrc` checks `op whoami` which fails in 1Password desktop-app integration mode. Bypass with explicit `op read` calls:

```bash
VAULT="a747na7sgxngfa7gczsqqwekfq"
export AZURE_OPENAI_API_KEY=$(op read "op://$VAULT/azure-openai-staging-runtime/password")
export AZURE_OPENAI_ENDPOINT=$(op read "op://$VAULT/azure-openai-staging-runtime/website")
# verify both have non-zero length before continuing
echo "key=${#AZURE_OPENAI_API_KEY} ep=${#AZURE_OPENAI_ENDPOINT}"
```

If `op read` fails with "authorization timeout", touch the 1Password desktop biometric prompt and re-run. Run the two `op read` calls **sequentially** (not in parallel via `$(...) $(...)`) because parallel calls fight for the auth prompt.

2. **The live eval-test pass step will 404.** The judge model `gpt-4.1-mini-2025-04-14` (`tests/eval.py:8`) isn't deployed on staging. `generate_clinical_note` itself succeeds (uses `gpt-4.1-2025-04-14`). So we use the manual `/tmp/manual_verify_plan_order.py` script for baseline + post-fix verification.

If you don't have Azure creds at all, complete Tasks 1, 3, and 4 (file edits + lint + push) and skip the manual verification steps in Tasks 1 and 3. Note the gap in the PR description.

---

## Task 1: Write the plan-ordering eval test

**Files:**
- Modify: `tests/job/summarize_test.py` — append a new test at end of file.

- [ ] **Step 1: Append the test**

Open `tests/job/summarize_test.py` and append at the very end of the file (after the closing `eval_n_of_m(...)` of `test_subjective_excludes_doctor_interpretation`):

```python


# Issue #33 item: in the Plan section, within each condition's bullets,
# the patient-actionable items (medications to take, lifestyle changes,
# self-monitoring) should appear before the doctor-actionable items
# (labs to order, referrals to send). Seeds a transcript with one
# condition (diabetes management) and a clear mix of both kinds of
# todos, asserts the ordering via eval_n_of_m.
def test_plan_bullets_sort_patient_before_doctor():
    patient = Patient.where(Patient.name == "Ocie Dosie").one()
    assert patient is not None

    appt = Appointment.where(Appointment.patient_id == patient.id).one()
    assert appt is not None

    schema = DoctorNoteSchema.where(
        (DoctorNoteSchema.doctor_id == appt.doctor_id)
        & (DoctorNoteSchema.note_type == "Test SOAP Note")
    ).one()
    assert schema is not None

    example_note = (
        DoctorNoteExample.select()
        .where(DoctorNoteExample.doctor_note_schema_id == schema.id)
        .order_by(DoctorNoteExample.updated_at.desc())
        .limit(1)
        .one()
    )

    transcript = AIVisitTranscript(
        doctor_id=appt.doctor_id,
        appointment_id=appt.id,
        patient_id=patient.id,
        duration=180,
        transcript_data=[
            TranscriptEntry(
                startTime=0,
                endTime=5000,
                speaker="Doctor",
                content="How have your blood sugars been running since our last visit?",
            ),
            TranscriptEntry(
                startTime=5000,
                endTime=15000,
                speaker="Patient",
                content="They've been creeping up. Mornings have been around 170 and after dinner sometimes hits the 230s. I've been pretty consistent with the metformin but I think dinner is what's tripping me up.",
            ),
            TranscriptEntry(
                startTime=15000,
                endTime=25000,
                speaker="Doctor",
                content="Okay, that's higher than where we want to be. I'm going to add a second medication -- empagliflozin, 10mg once daily in the morning. Start that tomorrow.",
            ),
            TranscriptEntry(
                startTime=25000,
                endTime=33000,
                speaker="Doctor",
                content="I also want you to check your blood sugar twice a day, once fasting and once two hours after dinner, and keep a log so we can review it next time.",
            ),
            TranscriptEntry(
                startTime=33000,
                endTime=42000,
                speaker="Doctor",
                content="And try to avoid late-evening meals -- ideally finish dinner by seven so your body has time to bring the numbers down before bed.",
            ),
            TranscriptEntry(
                startTime=42000,
                endTime=50000,
                speaker="Doctor",
                content="On my end I'll order an HbA1c lab so we have a current number, and I'll send a referral over to nutrition counseling so they can help with meal planning.",
            ),
            TranscriptEntry(
                startTime=50000,
                endTime=55000,
                speaker="Patient",
                content="Sounds good, I'll start the new medication tomorrow.",
            ),
        ],
    )

    def create_clinical_note() -> str:
        return json.dumps(
            generate_clinical_note(
                transcript=transcript,
                example_note=example_note,
                note_schema=schema,
            )
        )

    criteria = (
        "In the 'Plan' section of this clinical note, the patient-actionable "
        "bullets -- items the patient must do themselves (taking the new "
        "medication empagliflozin daily, checking blood sugar twice a day, "
        "avoiding late-evening meals) -- must appear BEFORE the "
        "doctor-actionable bullets -- items the doctor will do (ordering the "
        "HbA1c lab, sending the nutrition referral). The bullets do not need "
        "to be labelled as patient or doctor; the test is about their reading "
        "order in the Plan section. If a doctor-actionable bullet appears "
        "above any patient-actionable bullet, the test fails."
    )

    eval_n_of_m(call_llm=create_clinical_note, criteria=criteria, n=1, m=2)
```

- [ ] **Step 2: Verify the test is collected and pytest sees it**

```bash
PYTHON_ENV=test uv run pytest tests/job/summarize_test.py::test_plan_bullets_sort_patient_before_doctor --collect-only -q
```

Expected: exactly one test reported as collected.

- [ ] **Step 3: Establish manual baseline against the current prompt**

Fetch Azure creds per the prerequisite section above. Then create the manual verification script at `/tmp/manual_verify_plan_order.py`:

```python
"""Manual verification: call generate_clinical_note on a diabetes-management
transcript with mixed patient/doctor todos and print the Plan section so we
can eyeball the bullet order."""

import sys
sys.path.insert(0, ".")
import os
os.environ["PYTHON_ENV"] = "test"

from app.jobs.summarize import generate_clinical_note
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.appointment import Appointment
from app.models.doctor_note_example import DoctorNoteExample
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.patient import Patient

patient = Patient.where(Patient.name == "Ocie Dosie").one()
appt = Appointment.where(Appointment.patient_id == patient.id).one()
schema = DoctorNoteSchema.where(
    (DoctorNoteSchema.doctor_id == appt.doctor_id)
    & (DoctorNoteSchema.note_type == "Test SOAP Note")
).one()
example_note = (
    DoctorNoteExample.select()
    .where(DoctorNoteExample.doctor_note_schema_id == schema.id)
    .order_by(DoctorNoteExample.updated_at.desc())
    .limit(1)
    .one()
)

transcript = AIVisitTranscript(
    doctor_id=appt.doctor_id,
    appointment_id=appt.id,
    patient_id=patient.id,
    duration=180,
    transcript_data=[
        TranscriptEntry(startTime=0, endTime=5000, speaker="Doctor", content="How have your blood sugars been running since our last visit?"),
        TranscriptEntry(startTime=5000, endTime=15000, speaker="Patient", content="They've been creeping up. Mornings have been around 170 and after dinner sometimes hits the 230s. I've been pretty consistent with the metformin but I think dinner is what's tripping me up."),
        TranscriptEntry(startTime=15000, endTime=25000, speaker="Doctor", content="Okay, that's higher than where we want to be. I'm going to add a second medication -- empagliflozin, 10mg once daily in the morning. Start that tomorrow."),
        TranscriptEntry(startTime=25000, endTime=33000, speaker="Doctor", content="I also want you to check your blood sugar twice a day, once fasting and once two hours after dinner, and keep a log so we can review it next time."),
        TranscriptEntry(startTime=33000, endTime=42000, speaker="Doctor", content="And try to avoid late-evening meals -- ideally finish dinner by seven so your body has time to bring the numbers down before bed."),
        TranscriptEntry(startTime=42000, endTime=50000, speaker="Doctor", content="On my end I'll order an HbA1c lab so we have a current number, and I'll send a referral over to nutrition counseling so they can help with meal planning."),
        TranscriptEntry(startTime=50000, endTime=55000, speaker="Patient", content="Sounds good, I'll start the new medication tomorrow."),
    ],
)

note = generate_clinical_note(transcript=transcript, example_note=example_note, note_schema=schema)
print("=" * 80)
print("PLAN SECTION:")
print("=" * 80)
print(note.get("Plan", "(no Plan section)"))
```

Run it:

```bash
PYTHON_ENV=test uv run python /tmp/manual_verify_plan_order.py 2>&1 | tail -30
```

Expected: the Plan section likely has the doctor-actionable items intermixed with or before the patient-actionable ones, since the current prompt has no rule about this ordering. Document the baseline in your notes. If the model already happens to produce the correct order, that's fine — the test still acts as a regression check.

- [ ] **Step 4: Commit the failing test**

```bash
git add tests/job/summarize_test.py
git commit -m "test(summarize): eval — Plan bullets sort patient before doctor"
```

---

## Task 2: Add the within-condition sort instruction to the prompt

**Files:**
- Modify: `app/jobs/summarize.py` — instruction #3 in `build_structured_prompt` (currently `:235-240`).

- [ ] **Step 1: Locate the instruction-3 sub-bullets**

In `app/jobs/summarize.py`, instruction #3 currently reads:

```
        3. Organize the information into the appropriate sections of the clinical note schema.
          - Within each section, you should group information based on condition.
          - E.g. if the patient discusses diabetes and hypertension, and 3 steps in the plan relate to diabetes and 2 steps relate to hypertension, the plan should list the 3 diabetes steps, THEN the 2 hyptertension steps.
          - Sort based on importance of the condition. If hypertension is the patient's main concern, it should be listed first.
          - Be consistent with the order of conditions across fields. I.e. if anxiety is covered first in the plan, it should also be covered first in the assessment, subjective, and objective sections.
          - Do not create subsections within the fields for the conditions. Prefer "flat" lists of items unless absolutely necessary.
```

- [ ] **Step 2: Insert the new sub-bullet BEFORE the "Do not create subsections" line**

Insert this line (10 spaces indent + `- `, matching the existing sub-bullets):

```
          - In the Plan section specifically, within each condition's bullets, list patient-actionable items (e.g. medications to take, lifestyle changes, symptoms to monitor, follow-ups the patient must initiate) before doctor-actionable items (e.g. labs to order, referrals to send, follow-up calls the doctor must place). This makes it easier for the patient to scan their own to-dos.
```

After the edit, instruction #3 should read:

```
        3. Organize the information into the appropriate sections of the clinical note schema.
          - Within each section, you should group information based on condition.
          - E.g. if the patient discusses diabetes and hypertension, and 3 steps in the plan relate to diabetes and 2 steps relate to hypertension, the plan should list the 3 diabetes steps, THEN the 2 hyptertension steps.
          - Sort based on importance of the condition. If hypertension is the patient's main concern, it should be listed first.
          - Be consistent with the order of conditions across fields. I.e. if anxiety is covered first in the plan, it should also be covered first in the assessment, subjective, and objective sections.
          - In the Plan section specifically, within each condition's bullets, list patient-actionable items (e.g. medications to take, lifestyle changes, symptoms to monitor, follow-ups the patient must initiate) before doctor-actionable items (e.g. labs to order, referrals to send, follow-up calls the doctor must place). This makes it easier for the patient to scan their own to-dos.
          - Do not create subsections within the fields for the conditions. Prefer "flat" lists of items unless absolutely necessary.
```

No renumbering of any other items. Items 4-13 untouched.

- [ ] **Step 3: Re-run the manual verification**

```bash
PYTHON_ENV=test uv run python /tmp/manual_verify_plan_order.py 2>&1 | tail -30
```

Expected: the Plan section now lists patient-actionable bullets (taking empagliflozin daily, checking blood sugar, avoiding late meals) before doctor-actionable bullets (ordering HbA1c, sending nutrition referral).

If the order is still wrong, strengthen the sub-bullet wording — e.g. by adding "ALWAYS sort with patient items first within a condition" — and re-verify. Note any wording adjustments in the commit message.

- [ ] **Step 4: Re-run the items 1+2 manual scripts to confirm no regression**

```bash
PYTHON_ENV=test uv run python /tmp/manual_verify_pronouns.py 2>&1 | tail -5
PYTHON_ENV=test uv run python /tmp/manual_verify_subjective.py 2>&1 | tail -5
```

Expected: pronouns counter still shows she/her > 0 and they/them == 0; "costochondritis" still in Assessment and not in Subjective. If either regressed, the new sub-bullet's wording is interfering — adjust before commit.

If `/tmp/manual_verify_pronouns.py` and `/tmp/manual_verify_subjective.py` are no longer on disk (e.g. fresh shell), they need to be recreated from the PR #60 artifacts. See `docs/superpowers/plans/2026-05-28-notetaking-pronouns-and-subjective-source.md` for the source. (If skipping these verifications, document the gap.)

- [ ] **Step 5: Commit**

```bash
git add app/jobs/summarize.py
git commit -m "feat(summarize): prompt — sort Plan bullets patient-before-doctor"
```

---

## Task 3: Lint, push, and open the PR

**Files:**
- No source changes in this task.

- [ ] **Step 1: Run the Python linter**

```bash
just py_lint
```

Expected: clean. If ruff flags formatting, run `just py_lint_fix`, re-stage, and follow up with a `chore: format` commit.

- [ ] **Step 2: Verify the new test still skips cleanly without `RUN_LIVE_LLM_TESTS`**

Make sure Azure creds are still in your environment (the test file imports `AzureOpenAI` at module load time, so unsetting the creds breaks import). Then:

```bash
unset RUN_LIVE_LLM_TESTS
PYTHON_ENV=test uv run pytest tests/job/summarize_test.py::test_plan_bullets_sort_patient_before_doctor -v
```

Expected: the new test reports SKIPPED with the existing skipif reason.

- [ ] **Step 3: Push the branch**

```bash
git push -u origin feat/note-plan-bullet-ordering
```

- [ ] **Step 4: Open the PR**

```bash
gh pr create --title "feat(notes): sort Plan bullets patient-before-doctor within each condition (issue #33)" --body "$(cat <<'EOF'
## Summary
- Item 3 of #33 (Pearson + Jensen feedback): within each condition's bullets in the Plan section, the model now lists patient-actionable items (medications to take, lifestyle changes, self-monitoring) before doctor-actionable items (labs to order, referrals, follow-up calls). Patient can scan their own to-dos at a glance.
- One new live-LLM eval test in `tests/job/summarize_test.py` (test_plan_bullets_sort_patient_before_doctor) using an in-memory diabetes-management transcript with a clear mix of both kinds of todos. Gated by the existing `RUN_LIVE_LLM_TESTS=true` skipif so CI default is skip.
- No DB migration, no FE change, no new seeds, no renumbering of the prompt's numbered instructions (the new rule fits as a sub-bullet inside existing instruction #3).

## How it was verified locally
- Manual verification script (`/tmp/manual_verify_plan_order.py`, not committed) confirmed the baseline (Plan section without the new sub-bullet) and the post-fix behaviour (patient-actionable bullets before doctor-actionable bullets).
- Items 1+2 manual scripts re-run after this change — no regression in pronouns or Subjective behaviour.
- The full live-LLM eval test was NOT run end-to-end because the judge model `gpt-4.1-mini-2025-04-14` referenced in `tests/eval.py:8` is not deployed on the staging Azure OpenAI resource (404 `DeploymentNotFound`). Same blocker as PR #60. Tracked separately.

## Test plan
- [ ] CI run: the new test appears as SKIPPED (no `RUN_LIVE_LLM_TESTS` env in CI). Verified locally.
- [ ] Reviewer with judge-model access runs `RUN_LIVE_LLM_TESTS=true uv run pytest tests/job/summarize_test.py::test_plan_bullets_sort_patient_before_doctor -v` and confirms it passes at least 1-of-2. Required before merge.
- [ ] Smoke a real recent appointment through `perform()` in a dev shell and visually check the Plan section ordering.

## Spec & plan
- Spec: `docs/superpowers/specs/2026-05-28-plan-bullet-ordering-design.md`
- Plan: `docs/superpowers/plans/2026-05-28-plan-bullet-ordering.md`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5: Wait for CI green, then surface the PR URL**

Watch the CI run on the new PR. Report the URL and CI conclusion back when done.

---

## Self-review notes

- Spec coverage: item 3 of #33 → Tasks 2 (prompt edit) and 1 (eval test); deferred items 4–6 explicitly out of scope per spec.
- No renumbering required because the new rule slots inside existing instruction #3 as a sub-bullet. Items 1-13 untouched.
- The judge-model 404 workaround follows PR #60's precedent (manual verify scripts in `/tmp/`, document explicitly in PR body).
- Test transcript is single-condition (diabetes) to avoid confusion with the existing condition-grouping rule. If a multi-condition test is needed later, that's a separate test, not a strengthening of this one.
