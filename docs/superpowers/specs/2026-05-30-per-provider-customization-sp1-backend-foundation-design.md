# SP1 — Backend Foundation: per-note-type customization model + config-driven prompt assembly

**Date:** 2026-05-30
**Depends on:** nothing
**Ships with:** zero behavior change (defaults reproduce current output)
**See also:** `2026-05-30-per-provider-customization-overview.md`

## Goal

Introduce a per-note-type customization record and refactor
`app/jobs/summarize.py::build_structured_prompt` so the currently-global numbered
instruction block (1–16) becomes **config-driven**: fixed house rules + behavior
modules parameterized by the customization + an appended free-text block. With the
default customization (or no record at all), the assembled prompt is behaviorally
identical to today's. This structurally guarantees that one provider's change to a
note-type cannot affect any other provider or note-type.

## Non-goals

- No API endpoints (SP2). No UI (SP3). No new eval tests beyond the regression
  guard (SP4 owns the eval harness).
- No change to which behaviors exist — only *where they are controlled from*.

## Data model

New model `NoteTypeCustomization` in `app/models/note_type_customization.py`,
following the existing model conventions (see `app/models/doctor_note_schema.py`):

```python
class NoteTypeCustomization(
    BaseModel,
    TimestampsMixin,
    SoftDeletionMixin,
    TypeIDMixin("note_type_customization"),
    table=True,
):
    """Per-note-type overrides for the configurable note-generation behaviors.

    A missing row (or a row with all defaults) reproduces the platform's current
    note-generation behavior. Edits affect only this one DoctorNoteSchema.
    """

    doctor_note_schema_id: TypeIDType = DoctorNoteSchema.foreign_key(unique=True)
    doctor_note_schema: DoctorNoteSchema = Relationship()

    subjective_source: SubjectiveSource = Field(default=SubjectiveSource.patient_only)
    pronoun_style: PronounStyle = Field(default=PronounStyle.infer_from_transcript)
    plan_ordering: PlanOrdering = Field(default=PlanOrdering.patient_then_doctor)
    flag_new_medications: bool = Field(default=True)
    require_plan_reason: bool = Field(default=True)
    ros_distinct_from_subjective: bool = Field(default=True)

    custom_instructions: str | None = Field(default=None)
    "Free-text provider instructions appended verbatim to the prompt; null = none."
```

Enums (str-backed, stored as their value):

```python
class SubjectiveSource(str, Enum):
    patient_only = "patient_only"
    patient_and_doctor = "patient_and_doctor"

class PronounStyle(str, Enum):
    infer_from_transcript = "infer_from_transcript"
    neutral = "neutral"

class PlanOrdering(str, Enum):
    patient_then_doctor = "patient_then_doctor"
    as_discussed = "as_discussed"
```

**1:1 with `DoctorNoteSchema`** via a `unique=True` FK. (Confirm the codebase's
`foreign_key()` helper accepts/forwards `unique`; if not, declare the FK column and
add a `UniqueConstraint("doctor_note_schema_id")` in `__table_args__`, mirroring the
unique constraint already on `DoctorNoteSchema`.)

Alembic migration: create the table, the enum types (if the DB uses native enums;
otherwise they are stored as `VARCHAR` — match whatever `DoctorRole` does in
`app/models/doctor.py`), the unique index on `doctor_note_schema_id`, timestamps,
and `deleted_at`. No backfill: absence of a row == defaults.

### Defaults helper

Add a module-level default instance / factory so the prompt builder and SP2/SP3 can
share one source of truth:

```python
def default_customization() -> NoteTypeCustomization:
    """An unsaved record carrying the platform defaults (== current behavior)."""
    return NoteTypeCustomization(doctor_note_schema_id=...)  # transient; not saved
```

The prompt builder treats `None` and a default record identically.

## Prompt assembly refactor

Today `build_structured_prompt` hardcodes the 1–16 block in one f-string
(`summarize.py:197–255`). Refactor:

1. **Signature** gains the customization (defaulted so callers/tests are easy):

   ```python
   def build_structured_prompt(
       transcript: AIVisitTranscript,
       example_note: DoctorNoteExample | None,
       note_schema: DoctorNoteSchema,
       customization: NoteTypeCustomization | None = None,
   ) -> str:
   ```

   `generate_clinical_note(...)` gains the same `customization` parameter and
   forwards it.

2. **Instruction assembly** moves into a helper that returns the ordered list of
   numbered instructions, composed from:
   - **Fixed house rules** — the current text of instructions #1, #2, #3 (grouping
     body without the patient-then-doctor sub-bullet), #4, #5, #6, #7, #13, #14,
     #15, #16, verbatim.
   - **Behavior modules** — one function per configurable preset that returns the
     instruction string for the *active* value. The default value returns the
     **exact current wording** of the corresponding global instruction (#8, #9,
     #10, #11, #12, and the #3 patient-then-doctor sub-bullet). Non-default values
     return alternative wording (e.g. `subjective_source=patient_and_doctor`
     instructs that the Subjective may incorporate doctor-elicited history;
     `require_plan_reason=False` omits the "every Plan bullet states its why"
     instruction; `plan_ordering=as_discussed` omits the patient-then-doctor sort).
   - **Free-text** — when `custom_instructions` is set, append it as a final
     "Additional provider instructions:" section after the numbered block.

   The numbered list is assembled by joining the active instructions in a fixed
   canonical order and numbering them sequentially at render time, so dropping a
   behavior (e.g. `require_plan_reason=False`) does not leave a numbering gap.

3. `summarize.py::perform` loads the customization for the schema before generating:

   ```python
   customization = NoteTypeCustomization.get(doctor_note_schema_id=schema.id)
   note = generate_clinical_note(
       transcript=merged,
       example_note=example,
       note_schema=schema,
       customization=customization,  # None -> defaults
   )
   ```

   (Match the codebase's actual fetch idiom — e.g. a `.where(...).first()` if there
   is no `.get(**kwargs)` on the base model.)

## Behavior-module wording map

Each module's default branch must reproduce today's instruction text exactly
(source: `summarize.py:233–254`). Captured here so the implementer doesn't have to
re-derive it:

- `pronoun_style.infer_from_transcript` → current #8 text. `.neutral` → "Refer to
  the patient as 'the patient'; do not infer pronouns."
- `subjective_source.patient_only` → current #9 text. `.patient_and_doctor` →
  Subjective may include doctor-elicited history framed as the patient's report;
  drop the "ONLY from Patient: lines" restriction.
- `plan_ordering.patient_then_doctor` → current #3 patient-then-doctor sub-bullet.
  `.as_discussed` → omit that sub-bullet (order Plan as discussed).
- `flag_new_medications=True` → current #10 text. `False` → omit #10.
- `require_plan_reason=True` → current #11 text. `False` → omit #11.
- `ros_distinct_from_subjective=True` → current #12 text. `False` → omit #12.

## Testing

- **Unit (no LLM):** `build_structured_prompt(..., customization=None)` and
  `build_structured_prompt(..., customization=default_customization())` produce a
  string containing every fixed house rule and the default wording of every
  behavior module — i.e. the prompt is behaviorally identical to the pre-refactor
  output. A golden-string assertion (snapshot of the assembled instruction block for
  the default case) guards against accidental drift.
- **Unit (no LLM):** each non-default preset value flips exactly its module's text
  and nothing else (e.g. `require_plan_reason=False` removes the plan-reason
  instruction; all other instructions unchanged).
- **Live evals:** the existing `tests/job/summarize_test.py` evals continue to pass
  unchanged (they exercise the default path). SP4 extends them per-config.

## Risks / notes

- The instruction renumbering means any code or test that asserts on specific
  instruction numbers must be updated; prefer asserting on instruction *text*.
- Keep the canonical ordering of behavior modules stable to minimize prompt churn
  and keep the default-case golden snapshot meaningful.
- `extra_action_items`, bio injection, schema/example injection are unchanged.
