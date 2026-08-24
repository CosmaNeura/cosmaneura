# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

"""Unit tests for summarize.perform's merge behavior across N transcripts
on the same appointment. LLM calls are stubbed; the focus is on which
transcripts get concatenated, segment markers, and replace-vs-skip
semantics for the resulting AIVisitNote."""

from datetime import datetime, timedelta, timezone

import pytest
from flexmock import flexmock

from app.jobs import summarize
from app.services.ai_visit_note_versions import (
    VisitNoteRestoreReason,
    plan_visit_note_restore,
    restore_visit_note_version,
)

from app.models.ai_visit_note import (
    HISTORICAL_DUPLICATE_SUPERSESSION,
    AIVisitNote,
)
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry

from tests.factories import (
    AppointmentFactory,
    DoctorFactory,
    DoctorNoteSchemaFactory,
    PatientFactory,
)


@pytest.fixture(autouse=True)
def _allow_doctor_access(monkeypatch):
    monkeypatch.setattr(summarize, "doctor_access_in_force", lambda _doctor: True)


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

    def fake_generate(transcript, example_note, note_schema, customization=None):
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
    has a note, the note is regenerated and the prior version is retained —
    we end up with exactly one current note linked to the latest transcript."""
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

    versions = list(
        AIVisitNote.where(
            (AIVisitNote.appointment_id == appointment.id)
            & (AIVisitNote.doctor_note_schema_id == schema.id)
        ).all()
    )
    current = [note for note in versions if note.superseded_at is None]
    historical = [note for note in versions if note.superseded_at is not None]
    note = current[0]
    assert len(versions) == 2
    assert len(current) == 1
    assert len(historical) == 1
    assert historical[0].id == note_after_first.id
    assert historical[0].note == note_after_first.note
    assert historical[0].created_at == note_after_first.created_at
    assert historical[0].updated_at == note_after_first.updated_at
    assert historical[0].supersession_reason == "regenerated_from_newer_transcript"
    assert note.ai_transcript_id == t2.id
    assert note.id != note_after_first.id

    current_count = AIVisitNote.where(
        (AIVisitNote.appointment_id == appointment.id)
        & (AIVisitNote.doctor_note_schema_id == schema.id)
        & (AIVisitNote.superseded_at == None)  # noqa: E711
    ).count()
    assert current_count == 1


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


def test_restore_frontier_blocks_delayed_retry_but_allows_later_transcript(
    appointment_with_schema,
):
    doctor, patient, appointment, schema = appointment_with_schema
    base = datetime.now(timezone.utc) - timedelta(minutes=20)
    first = _make_transcript(
        doctor.id,
        appointment.id,
        patient.id,
        entries=[("doctor", "first")],
        duration=30,
        created_at=base,
    )
    latest_at_restore = _make_transcript(
        doctor.id,
        appointment.id,
        patient.id,
        entries=[("doctor", "second")],
        duration=30,
        created_at=base + timedelta(minutes=1),
    )
    historical = AIVisitNote(
        doctor_id=doctor.id,
        appointment_id=appointment.id,
        ai_transcript_id=first.id,
        doctor_note_schema_id=schema.id,
        note={"CC": "operator-selected", "Plan": "retain this"},
        superseded_at=base + timedelta(minutes=2),
        supersession_reason=HISTORICAL_DUPLICATE_SUPERSESSION,
    ).save()
    displaced = AIVisitNote(
        doctor_id=doctor.id,
        appointment_id=appointment.id,
        ai_transcript_id=latest_at_restore.id,
        doctor_note_schema_id=schema.id,
        note={"CC": "generated current", "Plan": "generated"},
    ).save()
    plan = plan_visit_note_restore(
        target_note_id=historical.id,
        operator_ref="frontier-regression-reviewer",
        reason=VisitNoteRestoreReason.clinical_correction,
    )
    restore_visit_note_version(
        target_note_id=historical.id,
        operator_ref="frontier-regression-reviewer",
        reason=VisitNoteRestoreReason.clinical_correction,
        confirm_plan_sha256=plan.plan_sha256,
    )

    assert (
        summarize._finalize_generated_note(
            trigger=latest_at_restore,
            schema=schema,
            generated_through_transcript_id=latest_at_restore.id,
            note={"CC": "delayed retry", "Plan": "must not replace"},
            appointment_name="Delayed retry",
        )
        is False
    )
    after_retry = list(
        AIVisitNote.where(
            (AIVisitNote.appointment_id == appointment.id)
            & (AIVisitNote.doctor_note_schema_id == schema.id)
        ).all()
    )
    assert len(after_retry) == 2
    assert [note.id for note in after_retry if note.superseded_at is None] == [
        historical.id
    ]
    assert AIVisitNote.one(displaced.id).note == displaced.note

    genuinely_later = _make_transcript(
        doctor.id,
        appointment.id,
        patient.id,
        entries=[("doctor", "genuinely later")],
        duration=30,
        created_at=base + timedelta(minutes=3),
    )
    assert summarize._finalize_generated_note(
        trigger=genuinely_later,
        schema=schema,
        generated_through_transcript_id=genuinely_later.id,
        note={"CC": "later transcript", "Plan": "allowed"},
        appointment_name="Later transcript",
    )
    after_later = list(
        AIVisitNote.where(
            (AIVisitNote.appointment_id == appointment.id)
            & (AIVisitNote.doctor_note_schema_id == schema.id)
        ).all()
    )
    assert len(after_later) == 3
    current = [note for note in after_later if note.superseded_at is None]
    assert len(current) == 1
    assert current[0].ai_transcript_id == genuinely_later.id
    assert current[0].restore_frontier_transcript_id is None
