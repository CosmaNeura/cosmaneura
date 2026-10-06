"""Real capture finalization with synthetic sources; no model/network calls."""

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
from typeid import TypeID

from app.services import scribe_capture_notes as notes
from app.services import scribe_capture_plan_grounding as plans
from app.services.scribe_capture_notebook import ScratchNotes
from app.services.scribe_workflow import CaptureTranscript

from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.doctor import Doctor
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.note_type_customization import NoteTypeCustomization
from sqlmodel import Session

from tests.authority_kernel.scribe_capture_notes_test import (
    admission_case as _admission_case,
)
from tests.authority_kernel.scribe_capture_notes_test import (
    capture_engine as _capture_engine,
)
from tests.authority_kernel.scribe_capture_notes_test import (
    rows,
    setup,
)
from tests.authority_kernel.scribe_configured_capture_test import note_setup

admission_case = _admission_case
capture_engine = _capture_engine

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures/scribe_iphone55_synthetic_soap.json").read_text()
)
FIELDS = FIXTURE["generated_fields"]
PROSPECTIVE = (
    "P - PLAN",
    "Follow-ups",
    "Return precautions - given verbally, repeated back accurately, sent in writing",
)


class Model:
    def __init__(self, refs=None):
        self.refs = (
            refs if refs is not None else {heading: [] for heading in PROSPECTIVE}
        )
        self.calls = []

    async def complete(self, prompt, *, json_output):
        self.calls.append(prompt)
        assert json_output
        return json.dumps({"fields": FIELDS, "plan_source_refs": self.refs})


async def physical_case(f, monkeypatch, contents=None, *, role="unknown", refs=None):
    args = await setup(f, monkeypatch)
    with Session(f.engine) as session:
        schema = session.get(DoctorNoteSchema, f.schema_ref)
        # Exact deployed shape, not a two-field stand-in. Guidance is deliberately
        # adversarial here: template instructions cannot establish a plan.
        assert schema is not None
        schema.fields = {
            heading: "Extract supported source content" for heading in FIELDS
        }
        schema.fields["P - PLAN"] = "Always advise monitoring and seeking care."
        session.add(schema)
        session.commit()
    source = f.client.result.model_dump(mode="json")
    source["segments"] = [
        {
            "content": content,
            "start_ms": index * 1000,
            "end_ms": (index + 1) * 1000,
            "speaker_id": "synthetic-speaker",
            "speaker_role": role,
            "confidence": None,
        }
        for index, content in enumerate(contents or FIXTURE["transcript"])
    ]
    f.client.result = CaptureTranscript.model_validate(source)
    model = Model(refs)
    args["model"] = model
    return args, model


@pytest.mark.asyncio
async def test_actual_iphone55_shape_unspoken_plan_cannot_be_saved(
    admission_case, monkeypatch
):
    f = admission_case
    args, model = await physical_case(f, monkeypatch)
    # This exact output passed the old shape-only validator and was persisted.
    assert (
        "Seek medical attention"
        in notes._validated_note(json.dumps(FIELDS), FIELDS)["P - PLAN"]
    )
    result = await notes.capture_note(**args)
    assert result.fields is not None
    assert tuple(result.fields) == tuple(FIELDS) and len(result.fields) == 27
    assert plans.prospective_fields(FIELDS) == PROSPECTIVE
    assert result.fields is not None
    assert all(result.fields[heading] == plans.NOT_MENTIONED for heading in PROSPECTIVE)
    assert result.fields is not None
    assert result.fields["S - SUBJECTIVE"] == FIELDS["S - SUBJECTIVE"]
    assert result.fields is not None
    assert result.fields["Patient"] == "Avery Morgan"
    assert len(model.calls) == 1
    assert "complaint, symptom, question, current medication/history" in model.calls[0]
    assert "no plan was discussed, use an empty array" in model.calls[0]
    assert len(rows(f, AIVisitNote)) == len(rows(f, AIVisitTranscript)) == 1
    assert (await notes.capture_note(**{**args, "prepare": False})) == result
    assert (await notes.capture_note(**args)) == result
    assert len(model.calls) == 1  # Existing drafts are never re-grounded/regenerated.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "utterance,role,label",
    [
        (
            "Please monitor the cough and call our office tomorrow.",
            "clinician",
            "Clinician",
        ),
        (
            "Do not start ibuprofen. No medication change is planned.",
            "clinician",
            "Clinician",
        ),
        (
            "If the cough lasts another week, call the clinic; otherwise no follow-up is needed.",
            "clinician",
            "Clinician",
        ),
        (
            "We discussed antibiotics but decided not to prescribe them.",
            "unknown",
            "Unidentified speaker",
        ),
        (
            "Do not take the new medicine. Continue the previously agreed plan only.",
            "unknown",
            "Unidentified speaker",
        ),
        (
            "No tome ibuprofeno. No cambiamos sus medicamentos.",
            "clinician",
            "Clinician",
        ),
    ],
)
async def test_explicit_negated_conditional_and_translated_plans_keep_complete_source(
    admission_case, monkeypatch, utterance, role, label
):
    refs = {heading: [1] if heading == "P - PLAN" else [] for heading in PROSPECTIVE}
    args, model = await physical_case(
        admission_case, monkeypatch, [utterance], role=role, refs=refs
    )
    result = await notes.capture_note(**args)
    assert result.fields is not None
    assert result.fields["P - PLAN"] == f"Transcript — {label}:\n{utterance}"
    assert result.fields is not None
    assert "Seek medical attention" not in result.fields["P - PLAN"]
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_configured_spanish_uses_only_admitted_reviewed_english(
    admission_case,
):
    f = admission_case
    args = await note_setup(f)
    with Session(f.engine) as session:
        schema = session.get(DoctorNoteSchema, args["schema_ref"])
        assert schema is not None
        schema.fields = {heading: "Only supported source content" for heading in FIELDS}
        session.add(schema)
        session.commit()
    model = Model()
    result = await notes.capture_note(**{**args, "model": model})
    assert result.reviewed_source is not None
    assert result.reviewed_source.kind == "reviewed_english"
    assert "No tengo fiebre" not in model.calls[0]
    assert "I do not have a fever." in model.calls[0]
    assert result.fields is not None
    assert result.fields["P - PLAN"] == plans.NOT_MENTIONED
    assert len(model.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        "I take metformin daily.",
        "The cough started three days ago.",
        "Should I take an antibiotic?",
    ],
)
async def test_history_complaints_and_questions_do_not_authorize_generated_advice(
    admission_case, monkeypatch, content
):
    args, _ = await physical_case(
        admission_case, monkeypatch, [content], role="patient"
    )
    result = await notes.capture_note(**args)
    assert result.fields is not None
    assert result.fields["P - PLAN"] == plans.NOT_MENTIONED


@pytest.mark.asyncio
async def test_wrongly_selected_complaint_cannot_become_invented_advice(
    admission_case, monkeypatch
):
    text = "I have a cough."
    refs = {heading: [1] if heading == "P - PLAN" else [] for heading in PROSPECTIVE}
    args, _ = await physical_case(
        admission_case, monkeypatch, [text], role="patient", refs=refs
    )
    result = await notes.capture_note(**args)
    # Classification can still be wrong; source attribution makes that visible.
    # A model cannot transform this reference into an unspoken recommendation.
    assert result.fields is not None
    assert result.fields["P - PLAN"] == "Transcript — Patient:\nI have a cough."


@pytest.mark.asyncio
async def test_saved_scratch_decision_is_not_labeled_as_recorded_speech(
    admission_case, monkeypatch
):
    f = admission_case
    refs = {heading: [2] if heading == "P - PLAN" else [] for heading in PROSPECTIVE}
    args, model = await physical_case(
        f, monkeypatch, ["The cough began three days ago."], refs=refs
    )
    content = "Plan discussed:\nDo not start antibiotics.\nCall if symptoms change."
    scratch = ScratchNotes(
        workspace_id=f.binding.workspace_id,
        entry_id=uuid4(),
        revision=1,
        content=content,
        content_sha256=hashlib.sha256(content.encode()).hexdigest(),
    )
    result = await notes.capture_note(**args, scratch_notes=scratch)
    assert result.fields is not None
    assert result.fields["P - PLAN"] == "Clinician's saved scratch notes:\n" + content
    assert result.notebook_source == scratch.reference()
    assert model.calls[0].count(json.dumps(content)[1:-1]) == 1
    assert "[source 2] Separate source:" in model.calls[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid", [[0], [999], [True], [1.0], ["1"], [1, 1], None, {"index": 1}]
)
async def test_invalid_reference_retains_source_but_never_publishes_unchecked_plan(
    admission_case, monkeypatch, invalid
):
    f = admission_case
    refs = {
        heading: invalid if heading == "P - PLAN" else [] for heading in PROSPECTIVE
    }
    args, _ = await physical_case(f, monkeypatch, refs=refs)
    with pytest.raises(notes.CaptureNoteUnconfirmed) as error:
        await notes.capture_note(**args)
    assert not str(error.value)
    assert not rows(f, AIVisitNote) and len(rows(f, AIVisitTranscript)) == 1


@pytest.mark.parametrize(
    "raw",
    [
        '{"fields":{},"plan_source_refs":{}}',
        '{"fields":{},"plan_source_refs":{"Plan":[]},"extra":1}',
        '{"fields":{},"plan_source_refs":{"Plan":[],"Plan":[1]}}',
        '{"Plan":"Unchecked advice"}',
    ],
)
def test_missing_duplicate_or_legacy_envelope_fails_closed(raw):
    grounding = plans.PlanGrounding(
        ("Plan",), (plans.PlanSource.speech("Unknown", "Synthetic source."),)
    )
    with pytest.raises(plans.PlanGroundingInvalid) as error:
        grounding.resolve(raw, max_bytes=262144)
    assert not str(error.value)


def test_complete_entries_preserve_chronology_and_conflicting_decisions():
    sources = (
        plans.PlanSource.speech("Doctor", "We could start a medication."),
        plans.PlanSource.speech(
            "Doctor", "Correction: do not start it. We decided against it."
        ),
    )
    grounding = plans.PlanGrounding(("Plan",), sources)
    _, resolved = grounding.resolve(
        json.dumps(
            {
                "fields": {"Plan": "Start medication."},
                "plan_source_refs": {"Plan": [2, 1]},
            }
        ),
        max_bytes=262144,
    )
    assert resolved["Plan"].index(sources[0].text) < resolved["Plan"].index(
        sources[1].text
    )
    assert "Correction: do not start it." in resolved["Plan"]


@pytest.mark.asyncio
async def test_small_model_reference_list_cannot_publish_oversized_source_text(
    admission_case, monkeypatch
):
    f = admission_case
    refs = {
        heading: list(range(1, 101)) if heading == "P - PLAN" else []
        for heading in PROSPECTIVE
    }
    text = "Do not start a treatment based on this synthetic example. " * 60
    args, _ = await physical_case(f, monkeypatch, [text] * 100, refs=refs)
    with pytest.raises(notes.CaptureNoteUnconfirmed):
        await notes.capture_note(**args)
    assert not rows(f, AIVisitNote)
    assert len(rows(f, AIVisitTranscript)) == 1


def test_default_builder_bytes_preserved_and_indexing_preserves_speaker_labels():
    doctor = Doctor(
        clerk_id="user_synthetic_prompt",
        biography="Synthetic primary care clinician.",
    )
    # Retain the legacy nullable terminology used to capture the prompt golden.
    setattr(doctor, "terminology", None)
    setattr(doctor, "id", TypeID("doctor"))
    transcript = AIVisitTranscript.model_validate(
        dict(
            id=TypeID("ai_visit_transcript"),
            doctor_id=doctor.id,
            patient_id=TypeID("patient"),
            appointment_id=TypeID("appointment"),
            duration=None,
            transcript_data=[
                TranscriptEntry(
                    speaker="Unknown", content=value, startTime=0, endTime=0
                )
                for value in FIXTURE["transcript"]
            ],
        )
    )
    schema = DoctorNoteSchema(
        doctor_id=doctor.id,
        note_type="Synthetic prompt",
        fields={key: "Supported source content only." for key in FIELDS},
    )
    customization = NoteTypeCustomization(
        require_plan_reason=False, custom_instructions="Use the supplied sources."
    )
    plain = notes.summarize.build_structured_prompt(
        transcript, None, schema, customization, action_items="", doctor=doctor
    )
    # Captured by executing the unchanged c48b940 builder against these exact
    # synthetic inputs; no live model, database or dynamic Git read in this test.
    assert (
        hashlib.sha256(plain.encode()).hexdigest()
        == "3867749b0eb78ec61889f10c43a09fc7a046a4283084283fa20596b0962c5557"
    )
    indexed = notes.summarize.build_structured_prompt(
        transcript,
        None,
        schema,
        customization,
        action_items="",
        doctor=doctor,
        source_indexes=True,
    )
    for index, text in enumerate(FIXTURE["transcript"], 1):
        assert f"Unknown: [source {index}] {text}" in indexed
        indexed = indexed.replace(f"[source {index}] ", "")
    assert indexed == plain


@pytest.mark.asyncio
async def test_long_capture_prompt_contains_each_complete_utterance_once(
    admission_case, monkeypatch
):
    contents = [
        (f"Synthetic grounding source {index:05d}: " + "Invented source words. " * 20)[
            :250
        ]
        for index in range(1800)
    ]
    args, model = await physical_case(admission_case, monkeypatch, contents)
    result = await notes.capture_note(**args)
    assert result.fields is not None
    assert result.fields["P - PLAN"] == plans.NOT_MENTIONED
    prompt = model.calls[0]
    assert all(prompt.count(text) == 1 for text in contents)
    assert "Unknown: [source 1800] " in prompt
    # 450KB of synthetic content plus source indexes and normal SOAP guidance;
    # serializing the complete source a second time would exceed this bound.
    assert len(prompt.encode()) < sum(len(text.encode()) for text in contents) + 70000
    assert len(model.calls) == 1
