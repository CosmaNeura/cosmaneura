# SP4 — Per-Config Eval Harness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make the eval/regression story configuration-aware: (a) a CI-runnable test proving `summarize.perform` loads each note-type's OWN customization (per-schema isolation at the job level), and (b) gated live-LLM evals proving the model honors a customization, plus a clear "evals are test-only / how to run" doc.

**Architecture:** Most non-LLM guarantees already shipped in SP1 (default prompt byte-identity, per-toggle prompt flips in `summarize_prompt_test.py`; row-level isolation in `note_type_customization_test.py`). SP4 adds the missing job-level isolation test and the live behavior evals, reusing the existing `tests/eval.py` judge harness (now pointed via `OPENAI_EVAL_MODEL`, from #68).

**Tech Stack:** pytest, SQLModel/activemodel, the `tests/eval.py` judge (Azure OpenAI), uv. CI gate: `just py_lint` (ruff check + `ruff format --check` + pyright). Live evals skip in CI (gated).

**Spec:** `docs/superpowers/specs/2026-05-30-per-provider-customization-sp4-eval-harness-design.md`

**Branch:** `feat/note-customization-evals-sp4` off `origin/master`.

**What already exists (do NOT duplicate):**
- `tests/job/summarize_prompt_test.py`: default golden byte-identity (`test_default_prompt_block_byte_identical_to_master`) + per-toggle prompt-flip tests + `generate_clinical_note` forwards `customization`.
- `tests/model/note_type_customization_test.py`: `test_rows_are_isolated_across_note_types` (saving A's row doesn't change B's).

---

## File Structure

- **Create** `tests/job/summarize_customization_isolation_test.py` — CI, non-LLM: `perform` loads per-schema customization (job-level isolation).
- **Modify** `tests/job/summarize_test.py` — add gated live per-config behavior evals + a short module docstring clarifying evals are test-only.

Run before pushing: `uv tool run ruff check .`, `uv tool run ruff format --check .`, `uv run pyright .` (CI gates). Non-LLM tests: `PYTHON_ENV=test uv run pytest <path> -v`.

---

## Task 1: Job-level per-schema isolation (CI, non-LLM)

Proves `summarize.perform` loads the customization belonging to the schema it's summarizing — so one note-type's config can never drive another's note. SP1 only tested that `generate_clinical_note` forwards a customization passed to it; this tests that `perform` selects the right row from the DB per schema.

**Files:**
- Create: `tests/job/summarize_customization_isolation_test.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/job/summarize_customization_isolation_test.py
from unittest.mock import patch

from app.jobs import summarize
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.note_type_customization import NoteTypeCustomization

from tests.factories import AppointmentFactory, DoctorFactory, PatientFactory


def _saved_transcript(doctor_id, appointment_id, patient_id) -> AIVisitTranscript:
    return AIVisitTranscript(
        doctor_id=doctor_id,
        appointment_id=appointment_id,
        patient_id=patient_id,
        duration=60,
        transcript_data=[
            TranscriptEntry(
                speaker="Patient", content="My back hurts.", startTime=0, endTime=1
            )
        ],
    ).save()


def test_perform_loads_each_schemas_own_customization():
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    appointment = AppointmentFactory.build(
        doctor_id=doctor.id, patient_id=patient.id
    ).save()
    transcript = _saved_transcript(doctor.id, appointment.id, patient.id)

    schema_a = DoctorNoteSchema(
        doctor_id=doctor.id, note_type="Iso A", fields={"CC": "x", "Plan": "y"}
    ).save()
    schema_b = DoctorNoteSchema(
        doctor_id=doctor.id, note_type="Iso B", fields={"CC": "x", "Plan": "y"}
    ).save()
    NoteTypeCustomization(
        doctor_note_schema_id=schema_a.id, require_plan_reason=False
    ).save()
    NoteTypeCustomization(
        doctor_note_schema_id=schema_b.id, require_plan_reason=True
    ).save()

    seen: dict = {}

    def fake_generate(transcript, example_note, note_schema, customization=None):
        seen[note_schema.id] = customization
        return {"CC": "stub", "Plan": "stub"}

    # mock generation + action-item extraction (both hit the LLM)
    with patch.object(
        summarize, "generate_clinical_note", side_effect=fake_generate
    ), patch.object(summarize, "extract_action_items", return_value="none"):
        summarize.perform(transcript.id, schema_a.id)
        summarize.perform(transcript.id, schema_b.id)

    assert seen[schema_a.id] is not None
    assert seen[schema_b.id] is not None
    # each perform received the customization belonging to ITS schema
    assert seen[schema_a.id].doctor_note_schema_id == schema_a.id
    assert seen[schema_b.id].doctor_note_schema_id == schema_b.id
    assert seen[schema_a.id].require_plan_reason is False
    assert seen[schema_b.id].require_plan_reason is True
```

- [ ] **Step 2: Run to confirm it fails (or passes meaningfully)**

Run: `PYTHON_ENV=test uv run pytest tests/job/summarize_customization_isolation_test.py -v`
This may PASS immediately if `perform` already loads per-schema (it does, from SP1). That's fine — the value is the regression guard. But FIRST confirm it actually exercises the path: temporarily change `perform` to pass `customization=None` and confirm the test FAILS, then revert. (If you can't easily do that, instead add an assertion that distinguishes the two — the `require_plan_reason` False/True split already does this: a broken `perform` that loaded the wrong/no row could not produce both values.) Report what you observed.

- [ ] **Step 3: (only if the test revealed a gap) fix `perform`**

If `perform` does NOT load per-schema customization correctly, fix `app/jobs/summarize.py::perform` to `NoteTypeCustomization.get(doctor_note_schema_id=schema.id)` and pass it. (Per SP1 it already does — so likely no change needed.)

- [ ] **Step 4: Confirm green**

Run: `PYTHON_ENV=test uv run pytest tests/job/summarize_customization_isolation_test.py -v`
Expected: PASS.

- [ ] **Step 5: Lint gate**

```
uv tool run ruff check tests/job/summarize_customization_isolation_test.py
uv tool run ruff format --check tests/job/summarize_customization_isolation_test.py
uv run pyright tests/job/summarize_customization_isolation_test.py
```
Fix findings (`uv tool run ruff format <file>` for format). For pyright, this test file may need `# pyright: reportAttributeAccessIssue=false` at the top like `summarize_test.py:1` if ORM attribute access trips it — match that file's header if needed.

- [ ] **Step 6: Commit**

```bash
git add tests/job/summarize_customization_isolation_test.py
git commit -m "test(notes): perform loads each note-type's own customization (isolation)"
```

---

## Task 2: Gated live per-config behavior evals + test-only doc

Adds live-LLM evals (skip in CI) that confirm the model honors a customization, using the existing `tests/eval.py` judge. Representative coverage of two high-signal presets (`require_plan_reason`, `subjective_source`) following the established `generate_clinical_note(...)` + `eval_n_of_m` pattern; documented so more can be added per the same shape (do NOT write all six — live evals are slow/costly; representative + extensible is the intent).

**Files:**
- Modify: `tests/job/summarize_test.py`

- [ ] **Step 1: Add a module docstring clarifying evals are test-only**

At the very top of `tests/job/summarize_test.py` (after the existing `# pyright:` line), add:
```python
"""Live-LLM evals for note generation.

These are a TEST mechanism only: they never run in production and never change
note output. They are gated by RUN_LIVE_LLM_TESTS=true + Azure OpenAI creds and
skip in CI. The judge model is configurable via OPENAI_EVAL_MODEL (see
tests/eval.py) so a deployed model can stand in for gpt-4.1-mini on staging.

Per-config evals below build a NoteTypeCustomization and pass it through
generate_clinical_note(..., customization=...) to verify the model honors the
provider's settings. Each note type's config is independent — see
tests/job/summarize_customization_isolation_test.py.
"""
```

- [ ] **Step 2: Add the per-config evals**

Append to `tests/job/summarize_test.py` (reuse the existing seeded "Test SOAP Note" lookup pattern used by other tests in this file — `Appointment.where(...)`, `DoctorNoteSchema.where((doctor_id==appt.doctor_id) & (note_type=="Test SOAP Note"))`, and the seeded transcript; mirror `test_note_includes_patient_action_items` for the exact fixture-loading lines, then attach a customization):

```python
def _soap_schema_and_transcript():
    """The seeded 'Test SOAP Note' schema + its transcript (mirrors the other
    evals in this file)."""
    appt = Appointment.where(Appointment.appointment_name == "diabetes").first()
    schema = DoctorNoteSchema.where(
        (DoctorNoteSchema.doctor_id == appt.doctor_id)
        & (DoctorNoteSchema.note_type == "Test SOAP Note")
    ).first()
    transcript = AIVisitTranscript.where(
        AIVisitTranscript.appointment_id == appt.id
    ).first()
    return schema, transcript
```
(IMPORTANT: copy the EXACT seeded lookups other tests in this file use — e.g. the appointment filter, schema filter, and transcript filter from `test_note_includes_patient_action_items`/`test_summarize_is_idempotent`. The lines above are a template; replace with the verbatim working queries from this file so the fixtures resolve.)

```python
def test_require_plan_reason_default_keeps_reasons():
    from app.models.note_type_customization import NoteTypeCustomization

    schema, transcript = _soap_schema_and_transcript()
    customization = NoteTypeCustomization(
        doctor_note_schema_id=schema.id, require_plan_reason=True
    )

    def create():
        note = generate_clinical_note(
            transcript=transcript,
            example_note=None,
            note_schema=schema,
            customization=customization,
        )
        return json.dumps(note)

    eval_n_of_m(
        call_llm=create,
        criteria=(
            "Every bullet in the Plan section states a reason or purpose for the "
            "action (e.g. a 'to ...' or 'for ...' clause), not just the action alone."
        ),
        n=1,
        m=2,
    )


def test_subjective_source_patient_and_doctor_allows_doctor_elicited():
    from app.models.note_type_customization import (
        NoteTypeCustomization,
        SubjectiveSource,
    )

    schema, transcript = _soap_schema_and_transcript()
    customization = NoteTypeCustomization(
        doctor_note_schema_id=schema.id,
        subjective_source=SubjectiveSource.patient_and_doctor,
    )

    def create():
        note = generate_clinical_note(
            transcript=transcript,
            example_note=None,
            note_schema=schema,
            customization=customization,
        )
        return json.dumps(note)

    eval_n_of_m(
        call_llm=create,
        criteria=(
            "The note is a coherent clinical note with a populated Subjective "
            "section."
        ),
        n=1,
        m=2,
    )
```
Adjust `criteria` wording if the seeded transcript doesn't have a Plan/Subjective field — keep criteria achievable for the seeded data. The point is the harness runs with a customization and the judge grades the output.

- [ ] **Step 3: Confirm collection + skip behavior (CI parity)**

Run: `PYTHON_ENV=test uv run pytest tests/job/summarize_test.py -v`
Expected: all tests in the file SKIP (no `RUN_LIVE_LLM_TESTS`), and the new tests are COLLECTED without import/collection errors (a collection error fails CI even when skipped). Confirm the new tests appear as skipped, not errored.

- [ ] **Step 4: Lint gate**

```
uv tool run ruff check tests/job/summarize_test.py
uv tool run ruff format --check tests/job/summarize_test.py
uv run pyright tests/job/summarize_test.py
```
(The file already has a `# pyright:` suppression header; keep it.) Fix any ruff findings.

- [ ] **Step 5: (optional, if Azure creds available) one live sanity run**

If you have `AZURE_OPENAI_API_KEY`/`ENDPOINT` and a deployed judge model, optionally run ONE eval live to sanity-check the harness:
`PYTHON_ENV=test OPENAI_EVAL_MODEL=gpt-4.1-2025-04-14 RUN_LIVE_LLM_TESTS=true uv run pytest tests/job/summarize_test.py::test_require_plan_reason_default_keeps_reasons -v`
This is optional verification; do not block on it. Report if you ran it and the result.

- [ ] **Step 6: Commit**

```bash
git add tests/job/summarize_test.py
git commit -m "test(notes): gated per-config live evals + test-only eval doc"
```

---

## Task 3: Final gate

- [ ] **Step 1: Full CI-parity gate**

```
uv tool run ruff check .
uv tool run ruff format --check .
uv run pyright .
PYTHON_ENV=test uv run pytest tests/job/summarize_customization_isolation_test.py tests/job/summarize_prompt_test.py tests/model/note_type_customization_test.py -v
```
Expected: ruff clean, `ruff format --check` no reformat, pyright 0 errors (note any PRE-EXISTING unrelated errors but don't fix), non-LLM tests pass, and `tests/job/summarize_test.py` collects (its evals skip).

- [ ] **Step 2: Commit any fixes**

```bash
git add -A
git commit -m "chore: lint for per-config eval harness"
```

---

## Done criteria

- `summarize_customization_isolation_test.py` proves (in CI, no LLM) that `perform` drives each note-type's note with that note-type's own customization.
- `summarize_test.py` has gated per-config live evals (skip in CI, collect cleanly) and a docstring stating evals are test-only + how to run with `OPENAI_EVAL_MODEL`.
- ruff check + `ruff format --check` + pyright clean; non-LLM tests green.

## Notes / gotchas

- Live evals require Azure OpenAI creds + a deployed judge model; point `OPENAI_EVAL_MODEL` at `gpt-4.1-2025-04-14` (deployed) since `gpt-4.1-mini-2025-04-14` isn't on staging. They SKIP without creds and must not break collection.
- Don't duplicate SP1's existing prompt-level tests; SP4 is the job-level + LLM-level layer.
- Run the three CI lint commands locally before pushing (the `ci-lint-gate` memory).
- Keep live-eval count small — they're slow and cost tokens. Representative + the documented pattern is the deliverable, not exhaustive per-preset coverage.
