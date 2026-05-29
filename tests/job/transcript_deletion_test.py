# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false, reportArgumentType=false, reportReturnType=false

from datetime import datetime, timezone
from unittest.mock import patch

from app.jobs.anonymize import perform
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.ai_visit_transcript_non_phi import AIVisitTranscriptNonPHI
from app.models.appointment import Appointment
from app.models.patient import Patient


def test_perform_deidentifies_and_tombstones_transcript():
    patient = Patient.where(Patient.name == "Samwise Gamgee").one()
    appt = Appointment.where(Appointment.patient_id == patient.id).one()

    transcript = AIVisitTranscript(
        doctor_id=appt.doctor_id,
        appointment_id=appt.id,
        patient_id=patient.id,
        duration=60,
        transcript_data=[
            TranscriptEntry(
                speaker="Patient",
                content="My name is John Smith and I live in Chicago.",
                startTime=0,
                endTime=5000,
            ),
            TranscriptEntry(
                speaker="Doctor",
                content="Have you been to Dr. Mozal recently?",
                startTime=5000,
                endTime=8000,
            ),
        ],
    ).save()

    initial_non_phi_count = AIVisitTranscriptNonPHI.where().count()

    with patch("app.jobs.anonymize.deidentify", side_effect=lambda s: f"DEID({s})"):
        perform(transcript.id)

    # New NonPHI row exists with deidentified entries.
    assert AIVisitTranscriptNonPHI.where().count() == initial_non_phi_count + 1
    new_non_phi = (
        AIVisitTranscriptNonPHI.select()
        .order_by(AIVisitTranscriptNonPHI.id.desc())
        .limit(1)
        .one()
    )
    assert [e.content for e in new_non_phi.data] == [
        "DEID(My name is John Smith and I live in Chicago.)",
        "DEID(Have you been to Dr. Mozal recently?)",
    ]
    # speaker / timing preserved
    assert [e.speaker for e in new_non_phi.data] == ["Patient", "Doctor"]
    assert [e.startTime for e in new_non_phi.data] == [0, 5000]
    assert [e.endTime for e in new_non_phi.data] == [5000, 8000]

    # Original transcript: data cleared, deleted_at set.
    refreshed = AIVisitTranscript.get(id=transcript.id)
    assert refreshed is not None
    assert refreshed.transcript_data == []
    assert refreshed.deleted_at is not None


def test_perform_is_idempotent_when_already_deleted():
    patient = Patient.where(Patient.name == "Samwise Gamgee").one()
    appt = Appointment.where(Appointment.patient_id == patient.id).one()

    transcript = AIVisitTranscript(
        doctor_id=appt.doctor_id,
        appointment_id=appt.id,
        patient_id=patient.id,
        duration=60,
        transcript_data=[
            TranscriptEntry(
                speaker="Patient", content="hi", startTime=0, endTime=1000
            ),
        ],
        deleted_at=datetime.now(timezone.utc),
    ).save()

    initial_non_phi_count = AIVisitTranscriptNonPHI.where().count()

    with patch("app.jobs.anonymize.deidentify") as mock_deid:
        perform(transcript.id)
        mock_deid.assert_not_called()

    # No new NonPHI row.
    assert AIVisitTranscriptNonPHI.where().count() == initial_non_phi_count
