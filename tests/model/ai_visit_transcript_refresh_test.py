"""Transcript reads preserve entry types without creating or losing edits."""

from datetime import datetime

import pytest

from activemodel.session_manager import get_session
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from sqlalchemy import inspect

from tests.factories import AppointmentFactory, DoctorFactory, PatientFactory


@pytest.fixture
def transcript():
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    appointment = AppointmentFactory.build(
        doctor_id=doctor.id, patient_id=patient.id, appointment_date=datetime.now()
    ).save()
    return AIVisitTranscript(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_id=appointment.id,
        duration=1,
        transcript_data=[
            TranscriptEntry(
                speaker="Doctor",
                content="Synthetic prior visit.",
                startTime=0,
                endTime=1000,
            )
        ],
    ).save()


@pytest.mark.parametrize("operation", ["load", "refresh", "expire", "save"])
def test_entries_remain_typed_after_database_lifecycle(transcript, operation):
    expected = [entry.model_dump() for entry in transcript.transcript_data]
    with get_session() as session:
        session.add(transcript)
        if operation == "load":
            identifier = transcript.id
            session.expunge(transcript)
            transcript = session.get(AIVisitTranscript, identifier)
        elif operation == "refresh":
            session.refresh(transcript)
        elif operation == "expire":
            session.expire(transcript)
        else:
            transcript.save()
        assert transcript is not None
        assert [entry.model_dump() for entry in transcript.transcript_data] == expected
        assert not inspect(transcript).attrs.transcript_data.history.has_changes()


def test_partial_refresh_preserves_unsaved_transcript_edit(transcript):
    with get_session() as session:
        session.add(transcript)
        transcript.transcript_data = [
            TranscriptEntry(
                speaker="Doctor",
                content="Synthetic pending correction.",
                startTime=0,
                endTime=1000,
            )
        ]
        with session.no_autoflush:
            session.refresh(transcript, attribute_names=["duration"])
        assert transcript.transcript_data[0].content == "Synthetic pending correction."
        assert inspect(transcript).attrs.transcript_data.history.has_changes()
        session.rollback()
