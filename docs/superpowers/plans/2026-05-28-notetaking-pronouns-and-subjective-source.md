# Notetaking improvements: pronouns + Subjective source — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the first two doctor-feedback items from issue #33 — use the patient's actual pronouns (inferred from the transcript) and source the Subjective section only from `Patient:` lines — as a single prompt-text change, covered by two new live-LLM eval tests.

**Architecture:** Single-PR change to the global structured-prompt template at `app/jobs/summarize.py::build_structured_prompt`. Insert two new numbered instructions into the existing numbered block, renumber subsequent items, and add two `eval_n_of_m`-style tests in `tests/job/summarize_test.py` using in-memory transcript fixtures that reuse the existing seeded doctor + schema + example-note rows. No DB migrations, no FE changes, no new seeds.

**Tech Stack:** Python 3, pytest, OpenAI/Azure OpenAI Chat Completions (gpt-4.1-mini for the judge), existing ActiveModel/SQLModel ORM, existing `tests/eval.py` LLM-as-judge helper.

**Spec:** `docs/superpowers/specs/2026-05-28-notetaking-pronouns-and-subjective-source-design.md`

**Branch:** `feat/note-pronouns-and-subjective-source` (already created, spec already committed).

---

## File Structure

**Modify:**
- `app/jobs/summarize.py` — only the numbered-instructions block in `build_structured_prompt` (currently `:233-248`). Two new items inserted between items 7 and 8; subsequent items 8–11 shift to 10–13.
- `tests/job/summarize_test.py` — add two new test functions at end of file. Reuse the existing module-level `pytestmark` skipif (no change needed there).

**Do not modify:**
- `app/models/patient.py` — explicitly deferred (no pronouns field this PR).
- `tests/seeds.py` — tests use in-memory `AIVisitTranscript(...)` instances constructed against the seeded doctor; no new seeded patients/appointments needed.
- `tests/eval.py` — existing `eval_n_of_m` / `eval_llm` helpers are sufficient.

---

## Local prerequisite for running evals

The tests we add are gated by the existing module-level skipif:

```python
_RUN_LIVE_LLM_TESTS = os.getenv("RUN_LIVE_LLM_TESTS") == "true"
_HAS_AZURE_OPENAI_CREDS = bool(
    os.getenv("AZURE_OPENAI_API_KEY") and os.getenv("AZURE_OPENAI_ENDPOINT")
)
pytestmark = pytest.mark.skipif(
    not (_RUN_LIVE_LLM_TESTS and _HAS_AZURE_OPENAI_CREDS), reason=...
)
```

To exercise them locally during this work you need both env vars set AND `RUN_LIVE_LLM_TESTS=true`. CI default skips them — we are not changing that. Expected behavior in CI: both tests are reported as skipped, which is fine.

If you don't have Azure OpenAI creds in your dev shell, complete Tasks 1, 3, and 5 (which are file edits) and skip the local "verify it fails" / "verify it passes" steps; just verify the file diffs look right and rely on the reviewer with creds. Note the gap explicitly in the PR description.

---

## Task 1: Write the pronouns eval test

**Files:**
- Modify: `tests/job/summarize_test.py` — append a new test at end of file.

- [ ] **Step 1: Read the existing patterns**

Open `tests/job/summarize_test.py` and skim `test_note_includes_patient_action_items` (around `:80-118`). The new test follows the same shape: build a transcript, build a `create_clinical_note` closure, hand it to `eval_n_of_m`.

- [ ] **Step 2: Add the pronouns test**

Append to the end of `tests/job/summarize_test.py`:

```python
# Issue #33 item: notes should use the patient's actual pronouns
# instead of defaulting to "they/them". The patient's pronouns are
# inferred from the transcript itself (the doctor or patient uses them
# explicitly). This test seeds a transcript where "she/her" is used
# consistently and asserts the generated note matches.
def test_note_uses_patient_pronouns_from_transcript():
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

    # In-memory transcript — no .save() needed because
    # generate_clinical_note only reads transcript_data + doctor_id.
    # The doctor and patient both use "she/her" repeatedly; name is
    # never used in pronoun-bearing sentences so the LLM has only the
    # transcript's explicit pronoun signal to go on.
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
                content="How is she feeling about the new medication?",
            ),
            TranscriptEntry(
                startTime=4000,
                endTime=12000,
                speaker="Patient",
                content="I've been doing better since I started it. I feel less anxious overall.",
            ),
            TranscriptEntry(
                startTime=12000,
                endTime=16000,
                speaker="Doctor",
                content="And the headaches she was getting?",
            ),
            TranscriptEntry(
                startTime=16000,
                endTime=22000,
                speaker="Patient",
                content="They're mostly gone. I get one maybe once a week now instead of every day.",
            ),
            TranscriptEntry(
                startTime=22000,
                endTime=26000,
                speaker="Doctor",
                content="Has she had any side effects from it?",
            ),
            TranscriptEntry(
                startTime=26000,
                endTime=32000,
                speaker="Patient",
                content="A little dry mouth at first but that went away after the first week.",
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
        "When this clinical note refers to the patient using a pronoun, "
        "it should use 'she' or 'her' (matching the pronouns used in the "
        "transcript by both the doctor and the patient's context). The "
        "note should NOT refer to the patient as 'they' or 'them'."
    )

    eval_n_of_m(call_llm=create_clinical_note, criteria=criteria, n=1, m=2)
```

- [ ] **Step 3: Confirm the imports at the top of the file already cover what we need**

Open `tests/job/summarize_test.py` and verify these imports exist at the top (they should — they're already used by the existing tests):

```python
import json
import os
import re
import pytest
from app.jobs.summarize import generate_clinical_note, perform
from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.appointment import Appointment
from app.models.doctor_note_example import DoctorNoteExample
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.patient import Patient
from tests.eval import eval_llm, eval_n_of_m
```

The only addition we need is `TranscriptEntry`. Add it to the existing `from app.models.ai_visit_transcript import AIVisitTranscript` line:

```python
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
```

- [ ] **Step 4: Run the test against the current (unchanged) prompt to establish the baseline**

Set env vars in your shell (skip this step entirely if you don't have Azure creds — see the prerequisite section).

```bash
RUN_LIVE_LLM_TESTS=true \
  uv run pytest tests/job/summarize_test.py::test_note_uses_patient_pronouns_from_transcript -v
```

Expected: either FAIL with an AssertionError from `eval_n_of_m`, or PASS flakily. Document which one you see in your scratch notes. The point is the **current prompt does not enforce this**, so any pass is incidental.

If it passes 2/2 on the unchanged prompt, the test signal is too weak — strengthen the criteria by adding `"The pronouns 'they' or 'them' must not appear at all in the note when referring to the patient as an individual."`. Re-run and re-baseline.

- [ ] **Step 5: Commit the failing test**

```bash
git add tests/job/summarize_test.py
git commit -m "test(summarize): eval — patient pronouns should match transcript"
```

---

## Task 2: Add the pronouns instruction to the prompt

**Files:**
- Modify: `app/jobs/summarize.py` — `build_structured_prompt`, the numbered block at `:233-248`.

- [ ] **Step 1: Locate the numbered block**

In `app/jobs/summarize.py`, find the block beginning with `When creating the clinical note:` (around line 231) and the numbered list of 11 instructions from line 233 (`1. Keep in mind...`) through line 248 (`11. Take a deep breath...`).

- [ ] **Step 2: Insert a new item 8 between current items 7 and 8**

After current line `7. Ensure that all information in the clinical note is directly based on the content of the transcript. Do not add any speculative or assumed information.` and before current line `8. Ensure all action items are included in the relevant sections of the note. But do not repeat yourself -- if a section already mentions the action item, don't mention it again.`, insert:

```
        8. Use the patient's pronouns as they appear in the transcript -- whether the patient self-identifies, the doctor refers to the patient, or context makes them unambiguous (for example, repeated "she/her" or "he/him" usage by either speaker). Default to "they/them" ONLY when no pronoun usage is evident anywhere in the transcript.
```

Be careful with the leading whitespace — match the existing items (8 spaces of indent, since the whole `dedent(f""")` block is indented inside `return`). The `dedent()` call strips the common leading whitespace.

- [ ] **Step 3: Renumber the trailing items**

After the insert, the items that were 8, 9, 10, 11 should become 9, 10, 11, 12. The text of each is unchanged — only the leading digit changes.

Final state of the block (for sanity-checking — do not retype the unchanged items, just bump their numbers):

```
1. Keep in mind your BIO ...
2. Carefully review the transcript ...
3. Organize the information ...
4. Use clear, concise, and professional medical language.
5. Avoid including any irrelevant personal details or small talk ...
6. If certain information is not provided in the transcript, ...
7. Ensure that all information in the clinical note is directly based ...
8. Use the patient's pronouns as they appear in the transcript -- ... [NEW]
9. Ensure all action items are included ...                          [was 8]
10. Imitate the style and conventions ...                            [was 9]
11. Respond only in JSON format ...                                  [was 10]
12. Take a deep breath and think step by step.                       [was 11]
```

- [ ] **Step 4: Re-run the pronouns test**

```bash
RUN_LIVE_LLM_TESTS=true \
  uv run pytest tests/job/summarize_test.py::test_note_uses_patient_pronouns_from_transcript -v
```

Expected: PASS. If it still fails 0/2 or 1/2, strengthen the prompt wording (e.g. make the instruction more explicit, or move it earlier in the list) and re-run.

- [ ] **Step 5: Commit**

```bash
git add app/jobs/summarize.py
git commit -m "feat(summarize): prompt — infer patient pronouns from transcript"
```

---

## Task 3: Write the Subjective-source eval test

**Files:**
- Modify: `tests/job/summarize_test.py` — append a second new test.

- [ ] **Step 1: Append the test**

Add this function below the pronouns test in `tests/job/summarize_test.py`:

```python
# Issue #33 item: the Subjective section should be sourced only from
# what the patient said (lines beginning with "Patient:"), not from
# the doctor's interpretation. This test seeds a transcript where the
# doctor offers a specific diagnostic interpretation ("costochondritis")
# that the patient never says, and asserts that label does not appear
# in Subjective.
def test_subjective_excludes_doctor_interpretation():
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
        duration=120,
        transcript_data=[
            TranscriptEntry(
                startTime=0,
                endTime=3000,
                speaker="Doctor",
                content="What brings you in today?",
            ),
            TranscriptEntry(
                startTime=3000,
                endTime=10000,
                speaker="Patient",
                content="I've been having a sharp pain when I breathe in. It's right here on the left side of my chest, has been for about a week.",
            ),
            TranscriptEntry(
                startTime=10000,
                endTime=15000,
                speaker="Doctor",
                content="Any redness or swelling at the site? Does pressing on the spot make it worse?",
            ),
            TranscriptEntry(
                startTime=15000,
                endTime=21000,
                speaker="Patient",
                content="Yeah, pressing on it makes it a lot worse. No redness or swelling that I can see.",
            ),
            TranscriptEntry(
                startTime=21000,
                endTime=28000,
                speaker="Doctor",
                content="Sounds like classic costochondritis -- pain that worsens with palpation and with deep inspiration. That's an inflammation of the cartilage where the ribs meet the sternum.",
            ),
            TranscriptEntry(
                startTime=28000,
                endTime=32000,
                speaker="Patient",
                content="Okay. Anything I should be doing differently in the meantime?",
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
        "The 'Subjective' section of this clinical note should describe "
        "the patient's own reported symptoms (sharp pain when breathing in, "
        "pain on the left side of the chest, pain worse when pressing on "
        "the spot). The 'Subjective' section must NOT contain the word "
        "'costochondritis' or describe the condition by name -- that is "
        "the doctor's diagnostic interpretation and belongs in the "
        "'Assessment' section. It is acceptable, and expected, for "
        "'costochondritis' to appear elsewhere in the note (e.g. Assessment)."
    )

    eval_n_of_m(call_llm=create_clinical_note, criteria=criteria, n=1, m=2)
```

- [ ] **Step 2: Run the new test against the prompt as it stands after Task 2**

```bash
RUN_LIVE_LLM_TESTS=true \
  uv run pytest tests/job/summarize_test.py::test_subjective_excludes_doctor_interpretation -v
```

Expected: FAIL. The pronoun fix landed in Task 2 doesn't constrain Subjective sourcing, so the model probably still includes the doctor's interpretation in Subjective.

If it passes 2/2 on the current prompt — same playbook as Task 1 step 4: strengthen the criteria (e.g. "must not include any condition name or diagnosis"), re-run, re-baseline.

- [ ] **Step 3: Commit the failing test**

```bash
git add tests/job/summarize_test.py
git commit -m "test(summarize): eval — Subjective should not contain doctor interpretation"
```

---

## Task 4: Add the Subjective-source instruction to the prompt

**Files:**
- Modify: `app/jobs/summarize.py` — `build_structured_prompt` numbered block.

- [ ] **Step 1: Insert a new item 9 between the pronouns item (now 8) and the action-items item (now 9)**

After the pronouns item added in Task 2, and before the current item 9 (`Ensure all action items are included ...`), insert:

```
        9. When populating the Subjective section, source ONLY from lines spoken by the patient (lines beginning with "Patient:" in the transcript above). The doctor's interpretations, follow-up questions, and clinical observations belong in the Assessment or Objective sections, never in Subjective. Phrases like "the patient reports X" are acceptable as long as X itself came from a Patient: line.
```

Same indentation as the other items (8 spaces inside the `dedent(f""")` block).

- [ ] **Step 2: Renumber the trailing items again**

After Task 2 the trailing items were 9–12. After this insert they become 10–13. Final state:

```
1. Keep in mind your BIO ...
2. Carefully review the transcript ...
3. Organize the information ...
4. Use clear, concise, and professional medical language.
5. Avoid including any irrelevant personal details ...
6. If certain information is not provided in the transcript, ...
7. Ensure that all information in the clinical note is directly based ...
8. Use the patient's pronouns as they appear in the transcript ...
9. When populating the Subjective section, source ONLY from lines spoken by the patient ... [NEW]
10. Ensure all action items are included ...                                                [was 9, originally 8]
11. Imitate the style and conventions ...                                                   [was 10, originally 9]
12. Respond only in JSON format ...                                                         [was 11, originally 10]
13. Take a deep breath and think step by step.                                              [was 12, originally 11]
```

- [ ] **Step 3: Re-run the Subjective test**

```bash
RUN_LIVE_LLM_TESTS=true \
  uv run pytest tests/job/summarize_test.py::test_subjective_excludes_doctor_interpretation -v
```

Expected: PASS.

- [ ] **Step 4: Re-run the pronouns test too — make sure we didn't regress it**

```bash
RUN_LIVE_LLM_TESTS=true \
  uv run pytest tests/job/summarize_test.py::test_note_uses_patient_pronouns_from_transcript -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/jobs/summarize.py
git commit -m "feat(summarize): prompt — Subjective sourced only from Patient: lines"
```

---

## Task 5: Lint, push, and open the PR

**Files:**
- No source changes in this task.

- [ ] **Step 1: Run the Python linter (squawk + ruff are wired via `just`)**

```bash
just py_lint
```

Expected: clean. If ruff flags formatting in the new test or the prompt block, run the autofix recipe (`just py_lint_fix`), then re-stage and follow up with a `chore: format` commit.

- [ ] **Step 2: Verify the new tests still skip cleanly in a no-cred env**

```bash
unset RUN_LIVE_LLM_TESTS
uv run pytest tests/job/summarize_test.py -v
```

Expected: every test in the module is reported as SKIPPED with the existing skipif reason. The two new tests should be in that skipped list.

- [ ] **Step 3: Push the branch**

```bash
git push -u origin feat/note-pronouns-and-subjective-source
```

- [ ] **Step 4: Open the PR**

```bash
gh pr create --title "feat(notes): infer patient pronouns + restrict Subjective source (issue #33)" --body "$(cat <<'EOF'
## Summary
- First two items of #33: prompts now (a) use the patient's actual pronouns inferred from the transcript instead of defaulting to "they/them", and (b) instruct the model to source the Subjective section only from `Patient:` lines.
- Two new live-LLM eval tests in `tests/job/summarize_test.py` cover each behaviour using in-memory transcript fixtures. They are gated by the existing `RUN_LIVE_LLM_TESTS=true` skipif, so CI default is skip (matching prior eval tests in this file).
- No DB migration, no FE change, no new seeds. Prompt template remains global — both tweaks are universally-correct clinical-writing practices, so per-provider customisation is deferred (see spec).

## Test plan
- [ ] Reviewer with Azure creds runs `RUN_LIVE_LLM_TESTS=true uv run pytest tests/job/summarize_test.py::test_note_uses_patient_pronouns_from_transcript tests/job/summarize_test.py::test_subjective_excludes_doctor_interpretation -v` and confirms both pass at least 1-of-2.
- [ ] In CI: both new tests appear as SKIPPED (no Azure creds in CI shell).
- [ ] Smoke a real recent appointment through `perform()` in a dev shell and visually check pronouns + Subjective section.

## Spec
`docs/superpowers/specs/2026-05-28-notetaking-pronouns-and-subjective-source-design.md`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5: Note the PR URL in the handoff**

Append a one-line entry under "Open PRs" in `docs/2026-05-28-session-handoff.md` (or whatever the current handoff file is named) with the new PR number and a 1-line description. The boss expects PR links there.

---

## Self-review notes

- Spec coverage: both items 1 and 2 of #33 — covered by Tasks 2 and 4 (prompt edits) and Tasks 1 and 3 (eval tests).
- Spec deferred items (Patient.pronouns field, per-provider prompt customisation, items 3–6 of #33): explicitly out of scope per spec; no tasks here.
- Renumbering: the plan walks the numbering through three states (original 1–11, after pronouns 1–12, after Subjective 1–13) to keep the renumber from drifting.
- Risk: live evals are probabilistic. The plan calls out the "strengthen criteria if it passes flakily on the unmodified prompt" path in both test tasks.
