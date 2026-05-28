# Design — Notetaking improvements: pronouns + Subjective source

**Issue:** #33 (items 1 and 2 of 6)
**Date:** 2026-05-28
**Author:** Oscar (with Claude)

## Background

Issue #33 collects six doctor-requested tweaks to the clinical-note output (Pearson 4/4/25, Jensen 5/5/25). The boss confirmed priority order #33 → #29 → #34 and asked we tackle #33 items one at a time, starting with the two **highest-visible-impact** items:

1. **Pronouns** — notes default to "they/them" even when the transcript reveals the patient's pronouns. Source: Pearson.
2. **Subjective from patient only** — the Subjective section sometimes includes the doctor's interpretation rather than only the patient's own statements. Source: Pearson.

Both are output-quality regressions visible to the doctors using the product daily. They are also the cheapest item-1 wins on the list (no schema work, no UI work).

## Constraints

- **No patient pronouns field exists.** `app/models/patient.py` has only `name` and `date_of_birth`. No `pronouns`, `gender`, `sex`, or equivalent anywhere in the app. Adding such a field is out of scope for this PR (it would require migration + UI surface + a sourcing decision — intake? Tebra sync? doctor entry?). We infer pronouns from the transcript instead.
- **Prompt template is global.** `app/jobs/summarize.py::build_structured_prompt` is a single `dedent(f""")` block. Per-doctor data already plumbed in (`doctor.biography`, `note_schema.fields`, `example_note.field_examples`) but the **template text** is shared. Any tweak lands for every provider at once. The boss confirmed this is acceptable for these two tweaks since both are universally-correct clinical-writing practices, not stylistic preferences.
- **Live-LLM evals are opt-in.** `tests/job/summarize_test.py` runs against Azure OpenAI when `RUN_LIVE_LLM_TESTS=true` and creds are set. CI default is skip; we don't want to gate PRs on Azure cost or flakiness, but we want regression coverage when someone explicitly runs evals.

## Approach

Single-PR prompt-instruction change in `build_structured_prompt`, plus two new live-LLM eval tests using the existing `eval_n_of_m` LLM-as-judge helper.

### 1. Prompt changes

Modify the numbered instructions block in `build_structured_prompt` (currently items 1–11 at `app/jobs/summarize.py:233-248`). Insert two new items between the existing 7 ("don't add speculative info") and 8 ("ensure all action items included"), then renumber.

New item 8 — pronouns:

> Use the patient's pronouns as they appear in the transcript — whether the patient self-identifies, the doctor refers to the patient, or context makes them unambiguous (for example, repeated "she/her" usage by either speaker). Default to "they/them" ONLY when no pronoun usage is evident anywhere in the transcript.

New item 9 — Subjective source:

> When populating the Subjective section, source ONLY from lines spoken by the patient (lines beginning with "Patient:"). The doctor's interpretations, follow-up questions, and clinical observations belong in the Assessment or Objective sections, never in Subjective. Statements like "the patient reports X" are acceptable as long as X itself came from a Patient: line.

Existing items 8–11 shift to 10–13. No other prompt text changes.

### 2. Eval tests

Add to `tests/job/summarize_test.py`, following the pattern of the existing `test_note_includes_patient_action_items`:

**`test_note_uses_patient_pronouns_from_transcript`**
- Build (or reuse a seeded) transcript where the patient self-identifies with non-"they/them" pronouns — e.g., a `Patient:` line saying "she said I should come in" or a `Doctor:` line consistently saying "how is she feeling about the medication".
- Call `generate_clinical_note` and assert via `eval_n_of_m` that the generated note uses the matching pronoun and does NOT use "they/them".

**`test_subjective_excludes_doctor_interpretation`**
- Build (or reuse) a transcript where the doctor offers a clear interpretation in a `Doctor:` line that does NOT appear in any `Patient:` line — e.g., doctor says "sounds like classic costochondritis" while the patient only describes "sharp pain when I breathe in".
- Call `generate_clinical_note` and assert via `eval_n_of_m` that the Subjective section does NOT contain the doctor's interpretation (e.g., does not mention "costochondritis"), and that the Assessment or Objective DOES.

Both tests gated by the existing `pytestmark = pytest.mark.skipif(not (_RUN_LIVE_LLM_TESTS and _HAS_AZURE_OPENAI_CREDS))` at the top of the file, so they auto-skip in normal CI.

At implementation time, decide whether to:
- Reuse one of the existing seed patients (Samwise / Ocie / Ghengis) if their transcripts already exercise the needed pronouns/Doctor-interpretation patterns; or
- Add a small purpose-built transcript fixture into `tests/seeds.py` for these two cases.

Prefer reuse — the seeds are already large and adding more is a separate concern.

### 3. Out of scope

- The remaining four items in issue #33 (plan ordering, current vs prescribed meds, plan reasons, ROS-vs-Subjective). Tracked there; will get one PR each.
- Adding a `Patient.pronouns` field. The transcript-inference approach should cover Pearson's complaint; if it doesn't hold up in field feedback, we revisit with a separate design for the data model.
- Per-provider prompt customization. The architectural conversation is real (DoctorNoteSchema and DoctorNoteExample already imply per-doctor variation is expected) but unrelated to this PR's deliverable. Track separately if a third provider asks for a conflicting tweak.

## Risks and rollback

- **Risk: model still defaults to they/them on ambiguous transcripts.** Pearson reported this on multiple notes; a single eval test won't catch every variant. Field feedback after merge is the real signal. Mitigation: word the prompt to be permissive ("repeated usage by either speaker") and ship.
- **Risk: model leaks doctor interpretation into Subjective via paraphrase.** The eval can detect literal-keyword leaks but not paraphrased ones. Same mitigation: field feedback.
- **Rollback:** revert is one commit; no DB migration, no FE change, no config change.

## Affected files

- `app/jobs/summarize.py` — `build_structured_prompt` only.
- `tests/job/summarize_test.py` — two new tests.
- Maybe `tests/seeds.py` — only if reuse of existing seed transcripts isn't sufficient.
