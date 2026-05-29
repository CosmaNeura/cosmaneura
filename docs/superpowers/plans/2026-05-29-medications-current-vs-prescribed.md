# Medications: current vs newly-prescribed — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land item 4 of issue #33 — the Medications section must list both current and newly-prescribed medications, with the newly-prescribed ones tagged inline so the reader can tell them apart.

**Architecture:** Single-PR prompt-text change inserting a new numbered instruction (item 10) into `app/jobs/summarize.py::build_structured_prompt`. Renumbers existing items 10-13 to 11-14. One new live-LLM eval test in `tests/job/summarize_test.py` using an in-memory transcript with two current meds + one newly-prescribed. Manual verification via a one-off script in `/tmp/`.

**Tech Stack:** Python 3, pytest, Azure OpenAI Chat Completions (gpt-4.1-2025-04-14 for note gen).

**Spec:** `docs/superpowers/specs/2026-05-29-medications-current-vs-prescribed-design.md`

**Branch:** `feat/note-medications-current-vs-prescribed` (already created from PR #61's HEAD).

---

## File Structure

**Modify:**
- `app/jobs/summarize.py` — insert new item 10 between current items 9 and 10 in the numbered block of `build_structured_prompt`. Renumber items 10-13 → 11-14.
- `tests/job/summarize_test.py` — append one new test function at end of file.

**Do not modify:**
- `app/models/*.py` — no schema changes.
- `tests/seeds.py` — reuse existing seeded patient + Test SOAP Note schema.
- `tests/eval.py`.
- Other prompt instructions or sub-bullets (items 1-9, items 3's sub-bullets) — unchanged.

---

## Local prerequisite

Same as PR #61. Azure creds via explicit sequential `op read` calls; live eval-judge call will 404 on staging so use the manual verification script.

```bash
VAULT="a747na7sgxngfa7gczsqqwekfq"
export AZURE_OPENAI_API_KEY=$(op read "op://$VAULT/azure-openai-staging-runtime/password")
export AZURE_OPENAI_ENDPOINT=$(op read "op://$VAULT/azure-openai-staging-runtime/website")
echo "key=${#AZURE_OPENAI_API_KEY} ep=${#AZURE_OPENAI_ENDPOINT}"
```

---

## Task 1: Write the medications eval test

**Files:**
- Modify: `tests/job/summarize_test.py` — append a new test at end of file.

- [ ] **Step 1: Append the test**

Open `tests/job/summarize_test.py` and append at the very end (after the closing `eval_n_of_m(...)` of `test_plan_bullets_sort_patient_before_doctor`):

```python


# Issue #33 item: the Medications section should distinguish current
# medications (what the patient is already taking) from newly-prescribed
# medications (what the doctor adds at this visit). Seeds a transcript
# where the patient lists two current meds at intake and the doctor
# prescribes a third in response to new heartburn complaints; asserts
# the Medications section tags omeprazole inline as newly prescribed.
def test_medications_section_distinguishes_current_from_prescribed():
    patient = Patient.where(Patient.name == "Samwise Gamgee").one()
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
        duration=150,
        transcript_data=[
            TranscriptEntry(
                startTime=0,
                endTime=4000,
                speaker="Doctor",
                content="Before we get started, can you remind me what medications you're currently taking?",
            ),
            TranscriptEntry(
                startTime=4000,
                endTime=14000,
                speaker="Patient",
                content="Sure, I take lisinopril 10mg once a day in the morning, and atorvastatin 20mg at bedtime. That's it.",
            ),
            TranscriptEntry(
                startTime=14000,
                endTime=20000,
                speaker="Doctor",
                content="Great. And what brings you in today?",
            ),
            TranscriptEntry(
                startTime=20000,
                endTime=32000,
                speaker="Patient",
                content="I've been getting a lot of heartburn lately, especially at night. It's been going on for about a month now, maybe two or three times a week.",
            ),
            TranscriptEntry(
                startTime=32000,
                endTime=40000,
                speaker="Doctor",
                content="Any difficulty swallowing, weight loss, or blood in the stool?",
            ),
            TranscriptEntry(
                startTime=40000,
                endTime=46000,
                speaker="Patient",
                content="No, none of those. Just the heartburn.",
            ),
            TranscriptEntry(
                startTime=46000,
                endTime=58000,
                speaker="Doctor",
                content="Let's start you on omeprazole, 20mg once daily about thirty minutes before breakfast, for the next two months. We'll see if that takes care of it.",
            ),
            TranscriptEntry(
                startTime=58000,
                endTime=62000,
                speaker="Patient",
                content="Sounds good, thank you.",
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
        "The 'Medications' section of this clinical note must (a) list "
        "lisinopril and atorvastatin as the patient's current medications, "
        "and (b) list omeprazole AND clearly tag it as newly prescribed at "
        "this visit. Acceptable inline labels for omeprazole include "
        "'(new this visit)', '(NEW)', '(prescribed today)', "
        "'(initiated today)', '(started today)', or any similar phrase that "
        "tells the reader this med was added at this visit. If the "
        "Medications section omits omeprazole, or includes omeprazole but "
        "does NOT distinguish it from the current medications via an inline "
        "label, the test fails."
    )

    eval_n_of_m(call_llm=create_clinical_note, criteria=criteria, n=1, m=2)
```

- [ ] **Step 2: Verify the test is collected**

```bash
PYTHON_ENV=test uv run pytest tests/job/summarize_test.py::test_medications_section_distinguishes_current_from_prescribed --collect-only -q
```

Expected: exactly one test collected.

- [ ] **Step 3: Establish manual baseline**

Create `/tmp/manual_verify_medications.py`:

```python
"""Manual verification: call generate_clinical_note on a transcript with
two current meds + one newly-prescribed and print the Medications section."""

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

patient = Patient.where(Patient.name == "Samwise Gamgee").one()
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
    duration=150,
    transcript_data=[
        TranscriptEntry(startTime=0, endTime=4000, speaker="Doctor", content="Before we get started, can you remind me what medications you're currently taking?"),
        TranscriptEntry(startTime=4000, endTime=14000, speaker="Patient", content="Sure, I take lisinopril 10mg once a day in the morning, and atorvastatin 20mg at bedtime. That's it."),
        TranscriptEntry(startTime=14000, endTime=20000, speaker="Doctor", content="Great. And what brings you in today?"),
        TranscriptEntry(startTime=20000, endTime=32000, speaker="Patient", content="I've been getting a lot of heartburn lately, especially at night. It's been going on for about a month now, maybe two or three times a week."),
        TranscriptEntry(startTime=32000, endTime=40000, speaker="Doctor", content="Any difficulty swallowing, weight loss, or blood in the stool?"),
        TranscriptEntry(startTime=40000, endTime=46000, speaker="Patient", content="No, none of those. Just the heartburn."),
        TranscriptEntry(startTime=46000, endTime=58000, speaker="Doctor", content="Let's start you on omeprazole, 20mg once daily about thirty minutes before breakfast, for the next two months. We'll see if that takes care of it."),
        TranscriptEntry(startTime=58000, endTime=62000, speaker="Patient", content="Sounds good, thank you."),
    ],
)

note = generate_clinical_note(transcript=transcript, example_note=example_note, note_schema=schema)
print("=" * 80)
print("MEDICATIONS SECTION:")
print("=" * 80)
print(note.get("Medications", "(no Medications section)"))
print()
meds_text = (note.get("Medications") or "").lower()
print(f"lisinopril in Medications:   {'lisinopril' in meds_text}")
print(f"atorvastatin in Medications: {'atorvastatin' in meds_text}")
print(f"omeprazole in Medications:   {'omeprazole' in meds_text}")
import re
tag_present = bool(re.search(r'(new|prescribed|initiated|started)\b', meds_text))
print(f"newly-prescribed tag present somewhere in Medications: {tag_present}")
```

Run it:

```bash
PYTHON_ENV=test uv run python /tmp/manual_verify_medications.py 2>&1 | tail -20
```

Expected baseline (master + items 1-3): the Medications section likely lists lisinopril and atorvastatin (because the patient stated them) and may or may not include omeprazole. If omeprazole appears, it is almost certainly NOT tagged as newly prescribed — that's the gap we're closing. Document what you see.

- [ ] **Step 4: Commit the test**

```bash
git add tests/job/summarize_test.py
git commit -m "test(summarize): eval — Medications distinguishes current from newly-prescribed"
```

---

## Task 2: Add the Medications instruction to the prompt

**Files:**
- Modify: `app/jobs/summarize.py` — `build_structured_prompt` numbered block.

- [ ] **Step 1: Locate the current numbered block**

After PR #61 lands, the numbered items are 1-13. After this PR they should be 1-14. Specifically the current state is:

```
7. Ensure that all information ...
8. Pronouns ...
9. Subjective section ...
10. Ensure all action items ...
11. Imitate the style ...
12. Respond only in JSON ...
13. Take a deep breath ...
```

- [ ] **Step 2: Insert new item 10 between current items 9 and 10**

After current item 9 (Subjective section) and before current item 10 (action items), insert (8 spaces leading indent to match):

```
        10. Medications section: when this section exists in the schema, list both medications the patient is already taking (from "Patient:" lines) AND medications the doctor is prescribing or initiating during this visit. Tag newly-prescribed items inline with "(new this visit)" so they can be distinguished from the patient's existing medication list at a glance. Do not put newly-prescribed meds only in the Plan section -- the Medications section should reflect the post-visit medication list as a whole, with new additions flagged.
```

- [ ] **Step 3: Renumber the trailing items**

The items that were 10, 11, 12, 13 become 11, 12, 13, 14 respectively. Only the leading digit changes.

Final state of the trailing items:

```
10. Medications section: ... [NEW]
11. Ensure all action items ...      [was 10]
12. Imitate the style ...            [was 11]
13. Respond only in JSON ...         [was 12]
14. Take a deep breath ...           [was 13]
```

- [ ] **Step 4: Re-run the manual verification**

```bash
PYTHON_ENV=test uv run python /tmp/manual_verify_medications.py 2>&1 | tail -10
```

Expected: omeprazole now appears in the Medications section with an inline "new this visit" / "(NEW)" / "(prescribed today)" / similar tag. Both lisinopril and atorvastatin appear without such a tag (or explicitly tagged as "current" — either is OK).

If omeprazole still isn't tagged, strengthen the instruction wording (e.g. require the literal string "(new this visit)") and re-verify.

- [ ] **Step 5: Re-run items 1-3 regression checks**

```bash
PYTHON_ENV=test uv run python /tmp/manual_verify_pronouns.py 2>&1 | tail -5
PYTHON_ENV=test uv run python /tmp/manual_verify_subjective.py 2>&1 | tail -5
PYTHON_ENV=test uv run python /tmp/manual_verify_plan_order.py 2>&1 | tail -15
```

Expected: pronouns still 3× she/her with 0× they/them; Subjective still excludes costochondritis; Plan section still orders patient-actionable bullets before doctor-actionable. If any of the items-1-3 scripts isn't on disk (fresh shell), recreate it from the previous plans' source.

- [ ] **Step 6: Commit**

```bash
git add app/jobs/summarize.py
git commit -m "feat(summarize): prompt — Medications tags newly-prescribed inline"
```

---

## Task 3: Lint, push, and open the PR

**Files:**
- No source changes in this task.

- [ ] **Step 1: Run the Python linter**

```bash
just py_lint
```

Expected: clean.

- [ ] **Step 2: Verify the new test still SKIPs without `RUN_LIVE_LLM_TESTS`**

```bash
unset RUN_LIVE_LLM_TESTS
PYTHON_ENV=test uv run pytest tests/job/summarize_test.py::test_medications_section_distinguishes_current_from_prescribed -v
```

Expected: SKIPPED.

- [ ] **Step 3: Push the branch**

```bash
git push -u origin feat/note-medications-current-vs-prescribed
```

- [ ] **Step 4: Open the PR**

```bash
gh pr create --title "feat(notes): Medications section tags newly-prescribed inline (issue #33)" --body "$(cat <<'EOF'
## Summary
- Item 4 of #33 (Jensen feedback): the Medications section now lists both current and newly-prescribed medications, with newly-prescribed items tagged inline (e.g. "(new this visit)") so they can be distinguished at a glance from the patient's existing medication list.
- One new live-LLM eval test in \`tests/job/summarize_test.py\` (\`test_medications_section_distinguishes_current_from_prescribed\`) using an in-memory transcript with two current meds + one newly-prescribed. Gated by the existing \`RUN_LIVE_LLM_TESTS=true\` skipif.
- Prompt numbered instructions: new item 10 inserted; old items 10-13 shifted to 11-14. No DB migration, no FE change, no new seeds.

## How it was verified locally
- Manual verification script (\`/tmp/manual_verify_medications.py\`, not committed) used to baseline + confirm.
- Items 1-3 regression scripts re-run after this change — no regression.
- Live-LLM eval-judge unverified (staging Azure OpenAI judge-model 404, same blocker as PRs #60, #61). Reviewer with judge-model access should run the test before merge.

## Test plan
- [ ] CI: new test reports SKIPPED.
- [ ] Reviewer with judge-model access runs \`RUN_LIVE_LLM_TESTS=true uv run pytest tests/job/summarize_test.py::test_medications_section_distinguishes_current_from_prescribed -v\` and confirms it passes at least 1-of-2.
- [ ] Smoke a real recent appointment through \`perform()\` and visually check the Medications section labels new prescriptions.

## Spec & plan
- Spec: \`docs/superpowers/specs/2026-05-29-medications-current-vs-prescribed-design.md\`
- Plan: \`docs/superpowers/plans/2026-05-29-medications-current-vs-prescribed.md\`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5: Surface PR URL and CI status**

Watch CI; report URL + result.

---

## Self-review notes

- Spec coverage: Medications-section instruction (Task 2), eval test (Task 1), lint+push+PR (Task 3). Deferred items 5+6 of #33 explicitly out of scope per spec.
- Renumbering walks 1-13 → 1-14 (only the digits of items 10-13 change).
- Test transcript uses Samwise (different seeded patient from item 3's Ocie Dosie) so the two tests don't share fixture state.
- Manual-verification script pattern matches items 1-3.
