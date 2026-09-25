"""Date-only upload values retain the visit day and existing patient identity."""

from datetime import datetime

import app.jobs.transcribe
from app.commands.uploads.upload_visit_audio_file import perform

from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.visit_audio_file import VisitAudioFile


def test_calendar_upload_preserves_day_and_reuses_existing_patient(monkeypatch):
    queued = []
    monkeypatch.setattr(app.jobs.transcribe, "queue", queued.append)
    doctor = Doctor(clerk_id="user_synthetic_calendar_test").save()
    patient = Patient(
        doctor_id=doctor.id,
        name="Synthetic calendar patient",
        date_of_birth=datetime(2000, 1, 1),
    ).save()
    # These are the actual form's timezone-free ISO datetime wire values.
    perform(
        doctor=doctor,
        patient_name=patient.name,
        appointment_date=datetime.fromisoformat("2026-09-24T00:00:00"),
        date_of_birth=datetime.fromisoformat("2000-01-01T00:00:00"),
        file_name="synthetic_calendar_recording.wav",
    )
    file = VisitAudioFile.get(
        doctor_id=doctor.id, azure_file_name="synthetic_calendar_recording.wav"
    )
    assert file is not None
    appointment = Appointment.get(id=file.appointment_id)
    assert appointment is not None
    assert appointment.appointment_date == datetime(2026, 9, 24)
    assert appointment.patient_id == patient.id
    assert Patient.where(Patient.doctor_id == doctor.id).count() == 1
    assert queued == [file.id]
