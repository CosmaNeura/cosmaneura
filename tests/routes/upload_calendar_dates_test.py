"""Date-only upload values retain the visit day and existing patient identity."""

from datetime import UTC, datetime
from typing import cast

import app.jobs.transcribe
from app.commands.uploads.upload_visit_audio_file import perform
from app.services.session_prep_note_sources import (
    finalize_note_for_session_prep,
    load_reviewed_note_sources,
    note_content_sha256,
    prior_note_overview,
)

from activemodel.session_manager import get_session
from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.patient import Patient
from app.models.visit_audio_file import VisitAudioFile
from sqlmodel import Session


def test_calendar_upload_preserves_day_and_reuses_existing_patient(monkeypatch):
    queued = []
    monkeypatch.setattr(app.jobs.transcribe, "queue", queued.append)
    doctor = Doctor(clerk_id="user_synthetic_calendar_test", biography=None).save()
    patient = Patient(
        doctor_id=doctor.id,
        name="Synthetic calendar patient",
        date_of_birth=datetime(2000, 1, 1),
    ).save()
    # Encounters carry UTC instants; only the birthday is a calendar day.
    returned_id = perform(
        doctor=doctor,
        patient_name=patient.name,
        appointment_date=datetime.fromisoformat("2026-09-25T00:45:00Z"),
        date_of_birth=datetime.fromisoformat("2000-01-01T00:00:00"),
        file_name="synthetic_calendar_recording.wav",
    )
    file = VisitAudioFile.get(
        doctor_id=doctor.id, azure_file_name="synthetic_calendar_recording.wav"
    )
    assert file is not None
    appointment = Appointment.get(id=file.appointment_id)
    assert appointment is not None
    assert returned_id == str(appointment.id)
    assert appointment.appointment_date == datetime(2026, 9, 25, 0, 45)
    assert appointment.patient_id == patient.id
    assert Patient.where(Patient.doctor_id == doctor.id).count() == 1
    assert queued == [file.id]
    replay_id = perform(
        doctor=doctor,
        patient_name=patient.name,
        appointment_date=datetime.fromisoformat("2026-09-25T00:45:00Z"),
        date_of_birth=datetime(2000, 1, 1),
        file_name="synthetic_calendar_recording.wav",
    )
    assert replay_id == returned_id
    assert Appointment.where(Appointment.doctor_id == doctor.id).count() == 1


def test_registration_returns_its_authoritative_appointment_id(monkeypatch):
    from types import SimpleNamespace

    from starlette.requests import Request

    from app.routes.internal import VisitAudioUploadRequest, upload_visit_audio_file

    monkeypatch.setattr(
        "app.routes.internal.app.commands.uploads.sas.consume_upload",
        lambda *args, **kwargs: SimpleNamespace(
            blob_key="synthetic.wav", claim_token="synthetic"
        ),
    )
    monkeypatch.setattr(
        "app.routes.internal.app.commands.uploads.sas.complete_upload",
        lambda *args: True,
    )
    monkeypatch.setattr(app.jobs.transcribe, "queue", lambda _id: None)
    doctor = Doctor(clerk_id="user_synthetic_registration", biography=None).save()
    request = Request({"type": "http"})
    request.state.doctor = doctor
    request.state.auth_state = SimpleNamespace(
        payload={"iss": "https://synthetic.invalid"}
    )
    response = upload_visit_audio_file(
        request,
        VisitAudioUploadRequest(
            registration_token="A" * 43,
            appointment_date=datetime(2026, 9, 25),
            patient_name="Synthetic registration patient",
            date_of_birth=datetime(2000, 1, 1),
        ),
    )
    registered = VisitAudioFile.one(
        doctor_id=doctor.id, azure_file_name="synthetic.wav"
    )
    assert response.appointment_id == str(registered.appointment_id)


def test_registration_openapi_declares_appointment_response():
    from tests.integration.server import api_app

    schema = api_app.openapi()
    operation = next(
        path["post"]
        for path in schema["paths"].values()
        if path.get("post", {}).get("operationId") == "upload_visit_audio_file"
    )
    response_schema = operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
    assert response_schema["$ref"].endswith("/VisitAudioUploadResponse")
    assert schema["components"]["schemas"]["VisitAudioUploadResponse"]["required"] == [
        "appointment_id"
    ]


def test_two_same_day_uploads_keep_the_earlier_reviewed_visit_as_a_prep_source(
    monkeypatch,
):
    monkeypatch.setattr(app.jobs.transcribe, "queue", lambda _file_id: None)
    doctor = Doctor(clerk_id="user_synthetic_same_day_test", biography=None).save()
    appointments = []
    for hour in (13, 19):
        file_name = f"synthetic_same_day_{hour}.wav"
        perform(
            doctor=doctor,
            patient_name="Synthetic same-day patient",
            appointment_date=datetime(2026, 9, 24, hour, 30, tzinfo=UTC),
            date_of_birth=datetime(2000, 1, 1),
            file_name=file_name,
        )
        file = VisitAudioFile.one(doctor_id=doctor.id, azure_file_name=file_name)
        appointments.append(Appointment.one(id=file.appointment_id))
    prior, target = appointments
    assert prior.patient_id == target.patient_id
    transcript = AIVisitTranscript(
        doctor_id=doctor.id,
        patient_id=prior.patient_id,
        appointment_id=prior.id,
        duration=1,
        transcript_data=[],
    ).save()
    schema = DoctorNoteSchema(
        doctor_id=doctor.id,
        note_type="Synthetic same-day review",
        fields={"Plan": "Synthetic follow-up"},
    ).save()
    note = AIVisitNote(
        doctor_id=doctor.id,
        appointment_id=prior.id,
        ai_transcript_id=transcript.id,
        doctor_note_schema_id=schema.id,
        note={"Plan": "Review fictional sleep diary at the afternoon visit."},
    ).save()
    observed_at = datetime(2026, 9, 24, 20, tzinfo=UTC)
    with get_session() as managed:
        session = cast(Session, managed)
        finalize_note_for_session_prep(
            session=session,
            doctor_id=doctor.id,
            note_id=note.id,
            expected_content_sha256=note_content_sha256(note.note),
            observed_at=observed_at,
        )
        sources = load_reviewed_note_sources(
            session=session,
            doctor_id=doctor.id,
            target_appointment_id=target.id,
            observed_at=observed_at,
        ).sources
        overview = prior_note_overview(
            session=session,
            doctor_id=doctor.id,
            target_appointment_id=target.id,
            observed_at=observed_at,
        )
        assert [source.source_ref for source in sources] == [str(note.id)]
        assert overview.included_count == 1
        assert overview.notes[0].status == "included"
