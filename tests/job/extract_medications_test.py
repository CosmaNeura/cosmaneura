from datetime import datetime
from typing import cast

import pytest

import app.jobs.extract_medications as job
from app.jobs.extract_medications import perform

from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.patient_medication import (
    MedicationKind,
    MedicationSource,
    PatientMedication,
)

from tests.factories import AppointmentFactory, DoctorFactory, PatientFactory


@pytest.fixture(autouse=True)
def _allow_doctor_access(monkeypatch):
    monkeypatch.setattr(
        job,
        "doctor_capability_access_in_force",
        lambda _doctor, capability: capability == "soap_note",
    )


def _transcript(doctor, patient, lines: list[tuple[str, str]]) -> AIVisitTranscript:
    appointment = AppointmentFactory.build(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_date=datetime.now(),
    ).save()
    return AIVisitTranscript(
        doctor_id=doctor.id,
        appointment_id=appointment.id,
        patient_id=patient.id,
        duration=60,
        transcript_data=[
            TranscriptEntry(speaker=s, content=c, startTime=0, endTime=1)
            for s, c in lines
        ],
    ).save()


def test_extracts_unconfirmed_rows_from_transcript():
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    transcript = _transcript(
        doctor,
        patient,
        [
            ("Patient", "I take metformin and fish oil every morning."),
            ("Doctor", "Keep taking the vitamin d too."),
        ],
    )

    perform(transcript.id)

    rows = cast(
        list[PatientMedication],
        list(PatientMedication.where(PatientMedication.patient_id == patient.id).all()),
    )
    names = {r.normalized_name for r in rows}
    assert "metformin" in names
    assert "fish oil" in names
    assert "vitamin d" in names
    assert all(r.source == MedicationSource.transcript for r in rows)
    assert all(r.confirmed is False for r in rows)

    metformin = next(r for r in rows if r.normalized_name == "metformin")
    assert metformin.kind == MedicationKind.prescription
    fish_oil = next(r for r in rows if r.normalized_name == "fish oil")
    assert fish_oil.kind == MedicationKind.supplement


def test_extraction_is_idempotent_per_appointment():
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    transcript = _transcript(doctor, patient, [("Patient", "I take metformin.")])

    perform(transcript.id)
    first = PatientMedication.where(PatientMedication.patient_id == patient.id).count()

    perform(transcript.id)
    second = PatientMedication.where(PatientMedication.patient_id == patient.id).count()

    assert first == second == 1


def test_missing_transcript_is_noop():
    # should not raise
    perform("ai_visit_transcript_00000000000000000000000000")
