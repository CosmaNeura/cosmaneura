# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

"""Unit tests for summarize.perform's merge behavior across N transcripts
on the same appointment. LLM calls are stubbed; the focus is on which
transcripts get concatenated, segment markers, and replace-vs-skip
semantics for the resulting AIVisitNote."""

from datetime import datetime, timedelta, timezone

import pytest
from flexmock import flexmock

from app.jobs import summarize

from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry

from tests.factories import (
    AppointmentFactory,
    DoctorFactory,
    DoctorNoteSchemaFactory,
    PatientFactory,
)


def _make_transcript(
    doctor_id,
    appointment_id,
    patient_id,
    *,
    entries: list[tuple[str, str]],
    duration: int,
    created_at: datetime | None = None,
) -> AIVisitTranscript:
    data = [
        TranscriptEntry(speaker=spk, content=txt, startTime=0, endTime=0)
        for spk, txt in entries
    ]
    transcript = AIVisitTranscript(
        doctor_id=doctor_id,
        appointment_id=appointment_id,
        patient_id=patient_id,
        duration=duration,
        transcript_data=data,
    ).save()
    if created_at is not None:
        # TimestampsMixin sets created_at automatically; override for ordering tests.
        transcript.created_at = created_at
        transcript.save()
    return transcript


@pytest.fixture
def appointment_with_schema():
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    appointment = AppointmentFactory.build(
        doctor_id=doctor.id,
        patient_id=patient.id,
    ).save()
    schema = DoctorNoteSchemaFactory.build(
        doctor_id=doctor.id,
        fields={"CC": "Chief complaint", "Plan": "Treatment plan"},
    ).save()
    return doctor, patient, appointment, schema


@pytest.fixture
def stub_llm():
    """Replace the LLM-touching helpers with deterministic stubs and
    return the recorder so tests can introspect what was passed in."""
    captured: dict = {"generate": [], "action_items": []}

    def fake_generate(transcript, example_note, note_schema):
        captured["generate"].append(
            {
                "transcript_id": str(transcript.id) if transcript.id else None,
                "transcript_data": [e.model_dump() for e in transcript.transcript_data],
                "duration": transcript.duration,
            }
        )
        return {"CC": "stub complaint", "Plan": "stub plan"}

    def fake_action_items(transcript):
        captured["action_items"].append(
            [e.model_dump() for e in transcript.transcript_data]
        )
        return "- stub action item"

    flexmock(summarize).should_receive("generate_clinical_note").replace_with(
        fake_generate
    )
    flexmock(summarize).should_receive("extract_action_items").replace_with(
        fake_action_items
    )
    # augment_clinical_note still runs (no LLM, just appends duration text).
    return captured


def test_summarize_with_single_transcript_creates_note(
    appointment_with_schema,
    stub_llm,
):
    doctor, patient, appointment, schema = appointment_with_schema
    transcript = _make_transcript(
        doctor.id,
        appointment.id,
        patient.id,
        entries=[("doctor", "hello"), ("patient", "hi")],
        duration=120,
    )

    summarize.perform(transcript.id, schema.id)

    count = AIVisitNote.where(
        (AIVisitNote.appointment_id == appointment.id)
        & (AIVisitNote.doctor_note_schema_id == schema.id)
    ).count()
    note = AIVisitNote.where(
        (AIVisitNote.appointment_id == appointment.id)
        & (AIVisitNote.doctor_note_schema_id == schema.id)
    ).one()
    assert count == 1
    assert note.ai_transcript_id == transcript.id
    # Single segment → no synthetic segment marker injected
    sent = stub_llm["generate"][0]["transcript_data"]
    assert not any(e["speaker"] == "system" for e in sent)


def test_summarize_merges_multiple_transcripts_with_segment_markers(
    appointment_with_schema,
    stub_llm,
):
    doctor, patient, appointment, schema = appointment_with_schema
    base = datetime.now(timezone.utc) - timedelta(minutes=20)
    t1 = _make_transcript(
        doctor.id,
        appointment.id,
        patient.id,
        entries=[("doctor", "first half"), ("patient", "ok")],
        duration=60,
        created_at=base,
    )
    t2 = _make_transcript(
        doctor.id,
        appointment.id,
        patient.id,
        entries=[("doctor", "second half"), ("patient", "alright")],
        duration=45,
        created_at=base + timedelta(minutes=5),
    )

    summarize.perform(t2.id, schema.id)

    # generate_clinical_note must have been called with a transcript whose
    # transcript_data is the concatenation of both segments, ordered by
    # created_at, with a system-speaker segment marker before segment 2.
    sent = stub_llm["generate"][0]
    contents = [e["content"] for e in sent["transcript_data"]]
    assert contents.index("first half") < contents.index("second half")
    markers = [e for e in sent["transcript_data"] if e["speaker"] == "system"]
    assert len(markers) == 1
    assert "Segment 2" in markers[0]["content"]
    # Total duration is the sum
    assert sent["duration"] == 105

    # Exactly one note exists, linked to the latest transcript (the trigger).
    count = AIVisitNote.where(
        (AIVisitNote.appointment_id == appointment.id)
        & (AIVisitNote.doctor_note_schema_id == schema.id)
    ).count()
    note = AIVisitNote.where(
        (AIVisitNote.appointment_id == appointment.id)
        & (AIVisitNote.doctor_note_schema_id == schema.id)
    ).one()
    assert count == 1
    assert note.ai_transcript_id == t2.id
    # And t1 is unchanged (the stored row is not mutated)
    assert AIVisitTranscript.one(t1.id).transcript_data[0].content == "first half"


def test_summarize_replaces_existing_note_when_new_segment_lands(
    appointment_with_schema,
    stub_llm,
):
    """When a second transcript arrives for an appointment that already
    has a note, the note is regenerated and the prior one is removed —
    we end up with exactly one note linked to the latest transcript."""
    doctor, patient, appointment, schema = appointment_with_schema
    base = datetime.now(timezone.utc) - timedelta(minutes=20)
    t1 = _make_transcript(
        doctor.id,
        appointment.id,
        patient.id,
        entries=[("doctor", "first")],
        duration=30,
        created_at=base,
    )

    summarize.perform(t1.id, schema.id)
    note_after_first = AIVisitNote.where(
        (AIVisitNote.appointment_id == appointment.id)
        & (AIVisitNote.doctor_note_schema_id == schema.id)
    ).one()
    assert note_after_first.ai_transcript_id == t1.id

    t2 = _make_transcript(
        doctor.id,
        appointment.id,
        patient.id,
        entries=[("doctor", "second")],
        duration=40,
        created_at=base + timedelta(minutes=5),
    )
    summarize.perform(t2.id, schema.id)

    count = AIVisitNote.where(
        (AIVisitNote.appointment_id == appointment.id)
        & (AIVisitNote.doctor_note_schema_id == schema.id)
    ).count()
    note = AIVisitNote.where(
        (AIVisitNote.appointment_id == appointment.id)
        & (AIVisitNote.doctor_note_schema_id == schema.id)
    ).one()
    assert count == 1
    assert note.ai_transcript_id == t2.id
    assert note.id != note_after_first.id  # the old row was replaced


def test_summarize_skips_when_note_already_reflects_trigger(
    appointment_with_schema,
    stub_llm,
):
    """Re-running summarize against the same trigger transcript must not
    re-generate. This is the idempotency guard for celery retries and
    duplicate webhook deliveries — the note's ai_transcript_id matching
    the latest transcript is the 'up to date' signal."""
    doctor, patient, appointment, schema = appointment_with_schema
    t1 = _make_transcript(
        doctor.id,
        appointment.id,
        patient.id,
        entries=[("doctor", "only")],
        duration=30,
    )

    summarize.perform(t1.id, schema.id)
    first_calls = len(stub_llm["generate"])

    summarize.perform(t1.id, schema.id)
    assert len(stub_llm["generate"]) == first_calls, (
        "second call must short-circuit; LLM should not be invoked"
    )
