# Review of Systems must add doctor insight — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land item 6 of issue #33 (the last item) — when a schema has a Review of Systems field, that section must contain the doctor's system-by-system findings, NOT a paraphrase of Subjective.

**Architecture:** Single-PR prompt-text change inserting a new numbered instruction (item 12) into `app/jobs/summarize.py::build_structured_prompt`. Renumbers existing items 12-15 to 13-16. One new live-LLM eval test in `tests/job/summarize_test.py` using an in-memory `DoctorNoteSchema` (not saved to DB) with a Review-of-Systems field plus an in-memory transcript with a chief-complaint narrative and a multi-system ROS survey.

**Tech Stack:** Python 3, pytest, Azure OpenAI Chat Completions.

**Spec:** `docs/superpowers/specs/2026-05-29-ros-distinct-from-subjective-design.md`

**Branch:** `feat/note-ros-distinct-from-subjective` (already created from PR #63's HEAD).

---

## Local prerequisite

Same as previous PRs. Azure creds via sequential `op read`; judge model still 404s on staging so use manual verify.

```bash
VAULT="a747na7sgxngfa7gczsqqwekfq"
export AZURE_OPENAI_API_KEY=$(op read "op://$VAULT/azure-openai-staging-runtime/password")
export AZURE_OPENAI_ENDPOINT=$(op read "op://$VAULT/azure-openai-staging-runtime/website")
echo "key=${#AZURE_OPENAI_API_KEY} ep=${#AZURE_OPENAI_ENDPOINT}"
```

---

## Task 1: Write the ROS eval test

**Files:**
- Modify: `tests/job/summarize_test.py` — append new test at end of file.

- [ ] **Step 1: Append the test**

Append (after the closing `eval_n_of_m(...)` of `test_plan_bullets_include_reason`):

```python


# Issue #33 item: when the schema includes a Review of Systems field,
# ROS must contain the doctor's system-by-system findings from
# targeted questions, NOT a paraphrase of Subjective. Constructs an
# in-memory DoctorNoteSchema (not saved) with a Review of Systems
# field, seeds a transcript where the patient describes a cough +
# fever and the doctor performs a multi-system ROS survey, and
# asserts the two sections do not duplicate.
def test_review_of_systems_distinct_from_subjective():
    patient = Patient.where(Patient.name == "Samwise Gamgee").one()
    assert patient is not None

    appt = Appointment.where(Appointment.patient_id == patient.id).one()
    assert appt is not None

    # In-memory schema with a Review of Systems field. Not saved --
    # generate_clinical_note only reads .fields and (via doctor_id)
    # the doctor's biography.
    schema = DoctorNoteSchema(
        doctor_id=appt.doctor_id,
        note_type="Inline schema with ROS",
        fields={
            "CC": "Summarize the main reason for the patient's visit in one sentence.",
            "Subjective": "Document the patient's own description of their symptoms, concerns, and any relevant personal or family medical history.",
            "Review of Systems": "System-by-system findings from the doctor's targeted questions during the visit (positives and pertinent negatives). Should NOT repeat the patient's initial narrative from Subjective.",
            "Assessment": "List the primary diagnosis and any secondary diagnoses, along with the rationale for each.",
            "Plan": "Outline the proposed management plan, including treatments, medications, follow-up, and referrals.",
        },
    )

    transcript = AIVisitTranscript(
        doctor_id=appt.doctor_id,
        appointment_id=appt.id,
        patient_id=patient.id,
        duration=180,
        transcript_data=[
            TranscriptEntry(
                startTime=0,
                endTime=4000,
                speaker="Doctor",
                content="What brings you in today?",
            ),
            TranscriptEntry(
                startTime=4000,
                endTime=18000,
                speaker="Patient",
                content="I've had a dry cough for about three days now, and a low-grade fever -- I'd say around 100 degrees on and off. I just haven't been feeling like myself.",
            ),
            TranscriptEntry(
                startTime=18000,
                endTime=24000,
                speaker="Doctor",
                content="Any chest pain or shortness of breath?",
            ),
            TranscriptEntry(
                startTime=24000,
                endTime=32000,
                speaker="Patient",
                content="No chest pain. A little short of breath when I climb stairs, but nothing at rest.",
            ),
            TranscriptEntry(
                startTime=32000,
                endTime=38000,
                speaker="Doctor",
                content="Sore throat, runny nose, headache?",
            ),
            TranscriptEntry(
                startTime=38000,
                endTime=46000,
                speaker="Patient",
                content="My throat's a little scratchy, yes. No runny nose. No headache.",
            ),
            TranscriptEntry(
                startTime=46000,
                endTime=52000,
                speaker="Doctor",
                content="Any nausea, vomiting, or diarrhea?",
            ),
            TranscriptEntry(
                startTime=52000,
                endTime=58000,
                speaker="Patient",
                content="No, none of that. Appetite's a bit off but I'm keeping food down.",
            ),
            TranscriptEntry(
                startTime=58000,
                endTime=64000,
                speaker="Doctor",
                content="Any joint pain, rash, or new fatigue beyond what you mentioned?",
            ),
            TranscriptEntry(
                startTime=64000,
                endTime=72000,
                speaker="Patient",
                content="No joint pain, no rash. I'm tired but I figure that's the fever.",
            ),
        ],
    )

    def create_clinical_note() -> str:
        return json.dumps(
            generate_clinical_note(
                transcript=transcript,
                example_note=None,
                note_schema=schema,
            )
        )

    criteria = (
        "This clinical note's 'Review of Systems' section must contain "
        "the doctor's system-by-system findings from the targeted "
        "questions asked during the visit -- pertinent positives and "
        "negatives organised by body system (e.g. 'HEENT: mild sore "
        "throat, no headache', 'Respiratory: mild dyspnea on exertion', "
        "'GI: denies nausea, vomiting, diarrhea', 'Cardiovascular: no "
        "chest pain', 'Musculoskeletal: no joint pain', 'Skin: no rash'). "
        "The Review of Systems MUST NOT be a paraphrase or restatement "
        "of the Subjective section. The Subjective section should "
        "describe the patient's initial narrative (3-day dry cough, "
        "low-grade fever, general malaise). If the Review of Systems "
        "reads as a narrative summary of the patient's chief complaint "
        "rather than organised system-by-system findings from the "
        "doctor's survey, the test fails."
    )

    eval_n_of_m(call_llm=create_clinical_note, criteria=criteria, n=1, m=2)
```

- [ ] **Step 2: Verify collection**

```bash
PYTHON_ENV=test uv run pytest tests/job/summarize_test.py::test_review_of_systems_distinct_from_subjective --collect-only -q
```

Expected: 1 test collected.

- [ ] **Step 3: Establish manual baseline**

Create `/tmp/manual_verify_ros.py`:

```python
"""Manual verification: call generate_clinical_note with an in-memory
schema containing a Review of Systems field, and print Subjective + ROS
side by side so we can eyeball whether ROS duplicates Subjective."""

import sys
sys.path.insert(0, ".")
import os
os.environ["PYTHON_ENV"] = "test"

from app.jobs.summarize import generate_clinical_note
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.appointment import Appointment
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.patient import Patient

patient = Patient.where(Patient.name == "Samwise Gamgee").one()
appt = Appointment.where(Appointment.patient_id == patient.id).one()

schema = DoctorNoteSchema(
    doctor_id=appt.doctor_id,
    note_type="Inline schema with ROS",
    fields={
        "CC": "Summarize the main reason for the patient's visit in one sentence.",
        "Subjective": "Document the patient's own description of their symptoms, concerns, and any relevant personal or family medical history.",
        "Review of Systems": "System-by-system findings from the doctor's targeted questions during the visit (positives and pertinent negatives). Should NOT repeat the patient's initial narrative from Subjective.",
        "Assessment": "List the primary diagnosis and any secondary diagnoses, along with the rationale for each.",
        "Plan": "Outline the proposed management plan, including treatments, medications, follow-up, and referrals.",
    },
)

transcript = AIVisitTranscript(
    doctor_id=appt.doctor_id,
    appointment_id=appt.id,
    patient_id=patient.id,
    duration=180,
    transcript_data=[
        TranscriptEntry(startTime=0, endTime=4000, speaker="Doctor", content="What brings you in today?"),
        TranscriptEntry(startTime=4000, endTime=18000, speaker="Patient", content="I've had a dry cough for about three days now, and a low-grade fever -- I'd say around 100 degrees on and off. I just haven't been feeling like myself."),
        TranscriptEntry(startTime=18000, endTime=24000, speaker="Doctor", content="Any chest pain or shortness of breath?"),
        TranscriptEntry(startTime=24000, endTime=32000, speaker="Patient", content="No chest pain. A little short of breath when I climb stairs, but nothing at rest."),
        TranscriptEntry(startTime=32000, endTime=38000, speaker="Doctor", content="Sore throat, runny nose, headache?"),
        TranscriptEntry(startTime=38000, endTime=46000, speaker="Patient", content="My throat's a little scratchy, yes. No runny nose. No headache."),
        TranscriptEntry(startTime=46000, endTime=52000, speaker="Doctor", content="Any nausea, vomiting, or diarrhea?"),
        TranscriptEntry(startTime=52000, endTime=58000, speaker="Patient", content="No, none of that. Appetite's a bit off but I'm keeping food down."),
        TranscriptEntry(startTime=58000, endTime=64000, speaker="Doctor", content="Any joint pain, rash, or new fatigue beyond what you mentioned?"),
        TranscriptEntry(startTime=64000, endTime=72000, speaker="Patient", content="No joint pain, no rash. I'm tired but I figure that's the fever."),
    ],
)

note = generate_clinical_note(transcript=transcript, example_note=None, note_schema=schema)
print("=" * 80)
print("SUBJECTIVE:")
print("=" * 80)
print(note.get("Subjective", "(missing)"))
print()
print("=" * 80)
print("REVIEW OF SYSTEMS:")
print("=" * 80)
print(note.get("Review of Systems", "(missing)"))
```

Run:

```bash
PYTHON_ENV=test uv run python /tmp/manual_verify_ros.py 2>&1 | tail -40
```

Expected baseline (master + items 1-5): the Review of Systems section likely reads as a narrative summary of the cough/fever rather than system-by-system findings. Document.

- [ ] **Step 4: Commit the test**

```bash
git add tests/job/summarize_test.py
git commit -m "test(summarize): eval — Review of Systems must not duplicate Subjective"
```

---

## Task 2: Add the ROS instruction to the prompt

**Files:**
- Modify: `app/jobs/summarize.py` — `build_structured_prompt` numbered block.

- [ ] **Step 1: Locate items 11 and 12**

After PR #63 merges, items 11 (Plan reasons) and 12 (action items) are adjacent in the numbered block. The block runs 1-15.

- [ ] **Step 2: Insert new item 12 between current items 11 and 12**

Insert (8 spaces leading indent):

```
        12. Review of Systems (ROS) section: when the schema includes a "Review of Systems" field (or similarly-named system-by-system survey field like "ROS"), populate it with the doctor's system-by-system findings from targeted questions asked during the visit -- both positives and pertinent negatives. The ROS section MUST NOT be a paraphrase or summary of the Subjective section. The patient's initial chief complaint and narrative go in Subjective; the doctor's follow-up "any chest pain? any nausea? any headaches?" survey responses go in ROS, organised by system (HEENT, Respiratory, Cardiovascular, GI, GU, Musculoskeletal, Neuro, Skin, Psych, etc.). If the doctor did not ask explicit ROS questions, write "Not performed" or "Deferred"; do not pad ROS with restated Subjective content.
```

- [ ] **Step 3: Renumber the trailing items**

Items that were 12, 13, 14, 15 become 13, 14, 15, 16. Only the digit changes.

```
12. Review of Systems (ROS) section: ... [NEW]
13. Ensure all action items ...           [was 12]
14. Imitate the style ...                 [was 13]
15. Respond only in JSON ...              [was 14]
16. Take a deep breath ...                [was 15]
```

- [ ] **Step 4: Re-run manual verification**

```bash
PYTHON_ENV=test uv run python /tmp/manual_verify_ros.py 2>&1 | tail -40
```

Expected: Subjective contains the patient's initial narrative (3-day cough, fever). Review of Systems contains organised system-by-system findings (HEENT: scratchy throat, no headache; Respiratory: mild dyspnea on exertion; Cardiovascular: no chest pain; GI: denies nausea/vomiting/diarrhea, mild appetite loss; Musculoskeletal/Skin: no joint pain, no rash). The two sections do NOT duplicate.

If the model still merges them, strengthen the instruction wording (e.g. "ROS must be a structured, system-labeled list, NOT prose") and re-verify.

- [ ] **Step 5: Re-run regression scripts for items 1-5**

```bash
PYTHON_ENV=test uv run python /tmp/manual_verify_pronouns.py 2>&1 | tail -4
PYTHON_ENV=test uv run python /tmp/manual_verify_subjective.py 2>&1 | tail -4
PYTHON_ENV=test uv run python /tmp/manual_verify_plan_order.py 2>&1 | tail -10
PYTHON_ENV=test uv run python /tmp/manual_verify_medications.py 2>&1 | tail -5
PYTHON_ENV=test uv run python /tmp/manual_verify_plan_reasons.py 2>&1 | tail -10
```

Expected: every prior verifier reports its expected state. If any prior verify script is missing from /tmp/, recreate from the corresponding plan docs.

- [ ] **Step 6: Commit**

```bash
git add app/jobs/summarize.py
git commit -m "feat(summarize): prompt — Review of Systems must add doctor insight"
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
PYTHON_ENV=test uv run pytest tests/job/summarize_test.py::test_review_of_systems_distinct_from_subjective -v
```

Expected: SKIPPED.

- [ ] **Step 3: Push**

```bash
git push -u origin feat/note-ros-distinct-from-subjective
```

- [ ] **Step 4: Open the PR**

```bash
gh pr create --title "feat(notes): Review of Systems must add doctor insight, not repeat Subjective (issue #33)" --body "$(cat <<'EOF'
## Summary
- Item 6 of #33 -- the **last** item -- (Jensen feedback): when the schema includes a Review of Systems field, that section must contain the doctor's system-by-system findings from targeted questions during the visit. It must NOT be a paraphrase of the Subjective section.
- One new live-LLM eval test (\`test_review_of_systems_distinct_from_subjective\`) using an in-memory schema with a 'Review of Systems' field and an in-memory transcript with a chief complaint plus a multi-system ROS survey. The seeded 'Test SOAP Note' schema has no ROS field, so the test constructs its own schema in memory. Gated by \`RUN_LIVE_LLM_TESTS=true\`.
- Prompt numbered instructions: new item 12; old items 12-15 shifted to 13-16. No DB migration, no FE change, no new seeds.

## How it was verified locally
- Manual verification script (\`/tmp/manual_verify_ros.py\`, not committed) baselined the duplicate behaviour and confirmed the post-fix sections are distinct.
- Items 1-5 regression scripts re-run after this change -- no regression.
- Live-LLM eval-judge unverified (staging Azure OpenAI judge-model 404).

## Stack order
- PR #60 (items 1+2): MERGED.
- PR #61 (item 3): MERGED.
- PR #62 (item 4): MERGED.
- PR #63 (item 5): CI green; awaiting merge.
- **This PR (item 6 -- closes #33)**: stacked on #63.

## Test plan
- [ ] CI: new test reports SKIPPED.
- [ ] Reviewer with judge-model access runs \`RUN_LIVE_LLM_TESTS=true uv run pytest tests/job/summarize_test.py::test_review_of_systems_distinct_from_subjective -v\` and confirms 1-of-2 pass.
- [ ] Smoke a recent appointment in a dev shell with a schema that has a ROS field; visually check ROS contains system-labelled findings instead of duplicating Subjective.

## Spec & plan
- Spec: \`docs/superpowers/specs/2026-05-29-ros-distinct-from-subjective-design.md\`
- Plan: \`docs/superpowers/plans/2026-05-29-ros-distinct-from-subjective.md\`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5: Surface URL and CI status**

Watch CI; report URL + conclusion. Once merged, issue #33 can close.

---

## Self-review notes

- Spec coverage: prompt edit (Task 2) + eval test (Task 1) + lint/push/PR (Task 3). This is the last #33 item.
- Renumbering walks 1-15 → 1-16 (only digits of items 12-15 change).
- Test uses an in-memory `DoctorNoteSchema` for the first time (previous tests all reused the seeded "Test SOAP Note"). The in-memory pattern is the same one `_merge_transcripts` uses for AIVisitTranscript — construct without `.save()`.
- `example_note=None` because the inline schema has no associated example. The prompt template already handles `example_note` being None.
- Manual-verify pattern matches items 1-5.
