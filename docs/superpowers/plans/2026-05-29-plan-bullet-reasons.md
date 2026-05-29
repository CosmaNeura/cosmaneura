# Plan bullets must include reason — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land item 5 of issue #33 — every bullet in the Plan section must include a stated reason ("for X" / "to X") so a doctor picking up the chart later understands the rationale.

**Architecture:** Single-PR prompt-text change inserting a new numbered instruction (item 11) into `app/jobs/summarize.py::build_structured_prompt`. Renumbers existing items 11-14 to 12-15. One new live-LLM eval test in `tests/job/summarize_test.py` using an in-memory lumbar-strain transcript with several distinct actions. Manual verification via a one-off script in `/tmp/`.

**Tech Stack:** Python 3, pytest, Azure OpenAI Chat Completions.

**Spec:** `docs/superpowers/specs/2026-05-29-plan-bullet-reasons-design.md`

**Branch:** `feat/note-plan-bullet-reasons` (already created from PR #62's HEAD).

---

## Local prerequisite

Same as previous PRs. Azure creds via sequential `op read`; live eval-judge call still 404s on staging.

```bash
VAULT="a747na7sgxngfa7gczsqqwekfq"
export AZURE_OPENAI_API_KEY=$(op read "op://$VAULT/azure-openai-staging-runtime/password")
export AZURE_OPENAI_ENDPOINT=$(op read "op://$VAULT/azure-openai-staging-runtime/website")
echo "key=${#AZURE_OPENAI_API_KEY} ep=${#AZURE_OPENAI_ENDPOINT}"
```

---

## Task 1: Write the plan-reasons eval test

**Files:**
- Modify: `tests/job/summarize_test.py` — append new test at end of file.

- [ ] **Step 1: Append the test**

Append (after the closing `eval_n_of_m(...)` of `test_medications_section_distinguishes_current_from_prescribed`):

```python


# Issue #33 item: every Plan bullet must include a stated reason -- the
# "why" of the action -- using "for X" or "to X" phrasing. Seeds a
# lumbar-strain transcript with four distinct actions (ibuprofen, heat
# application, lifting restriction, MRI order), each of which should
# pick up an explicit reason.
def test_plan_bullets_include_reason():
    patient = Patient.where(Patient.name == "Ghengis Khan").one()
    assert patient is not None

    appt = Appointment.where(Appointment.patient_id == patient.id).first()
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
        duration=120,
        transcript_data=[
            TranscriptEntry(
                startTime=0,
                endTime=4000,
                speaker="Doctor",
                content="What's been bothering you?",
            ),
            TranscriptEntry(
                startTime=4000,
                endTime=14000,
                speaker="Patient",
                content="I've had really bad lower back pain for about two weeks now. It started after I helped a friend move some furniture.",
            ),
            TranscriptEntry(
                startTime=14000,
                endTime=22000,
                speaker="Doctor",
                content="Any numbness, tingling, or weakness in the legs? Any change in bowel or bladder function?",
            ),
            TranscriptEntry(
                startTime=22000,
                endTime=28000,
                speaker="Patient",
                content="No, none of that. Just the pain in my lower back, kind of a deep ache.",
            ),
            TranscriptEntry(
                startTime=28000,
                endTime=40000,
                speaker="Doctor",
                content="Okay, that sounds most consistent with a lumbar strain from the lifting. Let me give you a few things. Take ibuprofen 600mg three times a day with food.",
            ),
            TranscriptEntry(
                startTime=40000,
                endTime=50000,
                speaker="Doctor",
                content="Apply a heating pad to the area for about 20 minutes, two or three times a day.",
            ),
            TranscriptEntry(
                startTime=50000,
                endTime=58000,
                speaker="Doctor",
                content="And no heavy lifting at all for the next two weeks. I really mean it.",
            ),
            TranscriptEntry(
                startTime=58000,
                endTime=70000,
                speaker="Doctor",
                content="I'm also going to order a lumbar MRI just to be safe -- given how long the pain has been going on, I want to make sure there isn't a disc issue we're missing.",
            ),
            TranscriptEntry(
                startTime=70000,
                endTime=74000,
                speaker="Patient",
                content="Okay, that all makes sense.",
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
        "Every bullet in the 'Plan' section of this clinical note must "
        "include an explicit reason for the action -- a 'for X' or 'to X' "
        "clause explaining the purpose. Acceptable: 'Order MRI to rule out "
        "disc pathology', 'Take ibuprofen 600mg three times a day for pain "
        "control', 'Avoid heavy lifting to prevent re-injury'. UNACCEPTABLE: "
        "bullets that state only the action with no reason, e.g. 'Order MRI', "
        "'Take ibuprofen 600mg three times a day', or 'Apply heating pad for "
        "20 minutes'. If ANY Plan bullet lacks a stated reason -- including "
        "common-sense actions like 'follow up in 2 weeks' which must say "
        "what the follow-up is FOR -- the test fails. Reasons must be "
        "clinically grounded (related to the patient's complaint or the "
        "standard indication for the action), not invented."
    )

    eval_n_of_m(call_llm=create_clinical_note, criteria=criteria, n=1, m=2)
```

- [ ] **Step 2: Verify collection**

```bash
PYTHON_ENV=test uv run pytest tests/job/summarize_test.py::test_plan_bullets_include_reason --collect-only -q
```

Expected: 1 test collected.

- [ ] **Step 3: Establish manual baseline**

Create `/tmp/manual_verify_plan_reasons.py`:

```python
"""Manual verification: call generate_clinical_note on a lumbar-strain
transcript and print the Plan section + per-bullet 'has reason?' heuristic."""

import sys
sys.path.insert(0, ".")
import os
os.environ["PYTHON_ENV"] = "test"
import re

from app.jobs.summarize import generate_clinical_note
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.appointment import Appointment
from app.models.doctor_note_example import DoctorNoteExample
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.patient import Patient

patient = Patient.where(Patient.name == "Ghengis Khan").one()
appt = Appointment.where(Appointment.patient_id == patient.id).first()
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
    duration=120,
    transcript_data=[
        TranscriptEntry(startTime=0, endTime=4000, speaker="Doctor", content="What's been bothering you?"),
        TranscriptEntry(startTime=4000, endTime=14000, speaker="Patient", content="I've had really bad lower back pain for about two weeks now. It started after I helped a friend move some furniture."),
        TranscriptEntry(startTime=14000, endTime=22000, speaker="Doctor", content="Any numbness, tingling, or weakness in the legs? Any change in bowel or bladder function?"),
        TranscriptEntry(startTime=22000, endTime=28000, speaker="Patient", content="No, none of that. Just the pain in my lower back, kind of a deep ache."),
        TranscriptEntry(startTime=28000, endTime=40000, speaker="Doctor", content="Okay, that sounds most consistent with a lumbar strain from the lifting. Let me give you a few things. Take ibuprofen 600mg three times a day with food."),
        TranscriptEntry(startTime=40000, endTime=50000, speaker="Doctor", content="Apply a heating pad to the area for about 20 minutes, two or three times a day."),
        TranscriptEntry(startTime=50000, endTime=58000, speaker="Doctor", content="And no heavy lifting at all for the next two weeks. I really mean it."),
        TranscriptEntry(startTime=58000, endTime=70000, speaker="Doctor", content="I'm also going to order a lumbar MRI just to be safe -- given how long the pain has been going on, I want to make sure there isn't a disc issue we're missing."),
        TranscriptEntry(startTime=70000, endTime=74000, speaker="Patient", content="Okay, that all makes sense."),
    ],
)

note = generate_clinical_note(transcript=transcript, example_note=example_note, note_schema=schema)
plan_text = note.get("Plan", "") or ""
print("=" * 80)
print("PLAN SECTION:")
print("=" * 80)
print(plan_text)
print()
# Heuristic per-bullet 'has reason?' check: split by lines, look at lines
# starting with '-' or '*' or digit-period, then check for ' for '/' to '.
lines = [l.strip() for l in plan_text.splitlines() if l.strip().startswith(("-", "*", "1.", "2.", "3.", "4.", "5.", "6.", "7."))]
print(f"Found {len(lines)} bullet-like lines:")
for line in lines:
    has_reason = bool(re.search(r"\b(for|to)\b\s+\S", line, re.IGNORECASE))
    print(f"  [{'reason' if has_reason else 'NO REASON':9}] {line[:120]}")
```

Run:

```bash
PYTHON_ENV=test uv run python /tmp/manual_verify_plan_reasons.py 2>&1 | tail -20
```

Expected baseline (master + items 1-4): some bullets have a "for"/"to" reason, others don't. Document the count.

- [ ] **Step 4: Commit the test**

```bash
git add tests/job/summarize_test.py
git commit -m "test(summarize): eval — every Plan bullet must include a reason"
```

---

## Task 2: Add the Plan-reasons instruction to the prompt

**Files:**
- Modify: `app/jobs/summarize.py` — `build_structured_prompt` numbered block.

- [ ] **Step 1: Locate current items 10 and 11**

After PR #62 lands, the numbered block runs 1-14. Items 10 (Medications) and 11 (action items) are adjacent.

- [ ] **Step 2: Insert new item 11 between current items 10 and 11**

Insert (8 spaces leading indent):

```
        11. Plan section bullets MUST include the reason for each action -- the "why" -- using "for X" or "to X" phrasing. For example: "Order MRI to rule out disc pathology" rather than "Order MRI"; "Continue metformin for ongoing diabetes management" rather than just "Continue metformin"; "Avoid heavy lifting to prevent re-injury" rather than just "Avoid heavy lifting". This applies to EVERY Plan bullet, even when the rationale seems obvious -- a doctor reading the note for the first time will not have the same context. When the transcript does not explicitly state the reason, infer it from the clinical context (the patient's complaint, the assessment, the medication's indication). Do not invent reasons unrelated to the visit.
```

- [ ] **Step 3: Renumber the trailing items**

Items that were 11, 12, 13, 14 become 12, 13, 14, 15 respectively. Only the digit changes.

Final trailing items:

```
11. Plan section bullets MUST include the reason ... [NEW]
12. Ensure all action items ...                      [was 11]
13. Imitate the style ...                            [was 12]
14. Respond only in JSON ...                         [was 13]
15. Take a deep breath ...                           [was 14]
```

- [ ] **Step 4: Re-run manual verification**

```bash
PYTHON_ENV=test uv run python /tmp/manual_verify_plan_reasons.py 2>&1 | tail -15
```

Expected: every bullet shows `[reason]` in the heuristic check. If any still show `[NO REASON]`, strengthen the prompt wording (e.g. with caps "EVERY bullet, no exceptions") and re-verify.

- [ ] **Step 5: Re-run regression scripts for items 1-4**

```bash
PYTHON_ENV=test uv run python /tmp/manual_verify_pronouns.py 2>&1 | tail -4
PYTHON_ENV=test uv run python /tmp/manual_verify_subjective.py 2>&1 | tail -4
PYTHON_ENV=test uv run python /tmp/manual_verify_plan_order.py 2>&1 | tail -10
PYTHON_ENV=test uv run python /tmp/manual_verify_medications.py 2>&1 | tail -8
```

Expected: pronouns still 3× she/her with 0× they/them; Subjective still excludes costochondritis; Plan order still patient-then-doctor; Medications still tags omeprazole as new. If any prior verify script is missing from /tmp/, recreate from the corresponding plan docs.

- [ ] **Step 6: Commit**

```bash
git add app/jobs/summarize.py
git commit -m "feat(summarize): prompt — every Plan bullet states its reason"
```

---

## Task 3: Lint, push, open PR

- [ ] **Step 1: Lint**

```bash
just py_lint
```

Expected: clean.

- [ ] **Step 2: Verify new test SKIPs without RUN_LIVE_LLM_TESTS**

```bash
unset RUN_LIVE_LLM_TESTS
PYTHON_ENV=test uv run pytest tests/job/summarize_test.py::test_plan_bullets_include_reason -v
```

Expected: SKIPPED.

- [ ] **Step 3: Push**

```bash
git push -u origin feat/note-plan-bullet-reasons
```

- [ ] **Step 4: Open the PR**

```bash
gh pr create --title "feat(notes): every Plan bullet states its reason (issue #33)" --body "$(cat <<'EOF'
## Summary
- Item 5 of #33 (Jensen feedback): every bullet in the Plan section must now include the reason for the action -- a 'for X' or 'to X' clause -- so a doctor picking up the chart later can read the plan and understand why each step is there.
- One new live-LLM eval test (\`test_plan_bullets_include_reason\`) using an in-memory lumbar-strain transcript with four distinct actions (ibuprofen, heat, lifting restriction, MRI). Gated by \`RUN_LIVE_LLM_TESTS=true\`.
- Prompt numbered instructions: new item 11; old items 11-14 shifted to 12-15. No DB migration, no FE change, no new seeds.

## How it was verified locally
- Manual verification script (\`/tmp/manual_verify_plan_reasons.py\`, not committed) baselined the gap (mixed reason coverage on master+items-1-4) and confirmed every bullet has a stated reason post-fix.
- Items 1-4 regression scripts re-run after this change -- no regression.
- Live-LLM eval-judge unverified (staging Azure OpenAI judge-model 404, same blocker as PRs #60, #61, #62).

## Stack order
- PR #60 (items 1+2): MERGED on master.
- PR #61 (item 3): open.
- PR #62 (item 4): open, stacked on #61.
- **This PR (item 5)**: stacked on #62.

## Test plan
- [ ] CI: new test SKIPPED.
- [ ] Reviewer with judge-model access runs \`RUN_LIVE_LLM_TESTS=true uv run pytest tests/job/summarize_test.py::test_plan_bullets_include_reason -v\` and confirms it passes at least 1-of-2.
- [ ] Smoke a real recent appointment and visually check the Plan bullets.

## Spec & plan
- Spec: \`docs/superpowers/specs/2026-05-29-plan-bullet-reasons-design.md\`
- Plan: \`docs/superpowers/plans/2026-05-29-plan-bullet-reasons.md\`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5: Surface URL and CI**

---

## Self-review notes

- Spec coverage: prompt edit (Task 2) + eval test (Task 1) + lint/push/PR (Task 3). Item 6 deferred.
- Renumbering walks 1-14 → 1-15 (only digits of items 11-14 change).
- Test transcript uses Ghengis Khan (third seeded patient, separate from Samwise/Ocie used by items 3/4) to avoid fixture overlap.
- The heuristic in `/tmp/manual_verify_plan_reasons.py` is approximate ("for"/"to" anywhere in the line); the eval criteria is stricter. Baselines may show false positives in the heuristic — read the bullets to confirm.
