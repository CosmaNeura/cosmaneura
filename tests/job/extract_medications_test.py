import json
from datetime import datetime
from types import SimpleNamespace
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
    monkeypatch.setattr(job, "doctor_access_in_force", lambda _doctor: True)


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


def test_literal_held_sdk_result_persists_source_and_repeat_does_not_overwrite(
    monkeypatch,
):
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    transcript = _transcript(
        doctor, patient, [("Patient", "Metformin 500mg is temporarily on hold.")]
    )
    calls = []

    def complete(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(
                            {
                                "medications": [
                                    {
                                        "name": "metformin",
                                        "kind": "prescription",
                                        "dosage": "500mg",
                                        "status": "held",
                                    }
                                ]
                            }
                        )
                    )
                )
            ]
        )

    monkeypatch.setattr(job, "should_use_test_openai_fallback", lambda: False)
    monkeypatch.setattr(job.openai.chat.completions, "create", complete)
    monkeypatch.setattr(job, "normalize_drug_name", lambda _name: (None, None))
    perform(transcript.id)
    row = PatientMedication.one(patient_id=patient.id)
    assert row.status.value == "held" and not row.confirmed
    assert (
        row.appointment_id == transcript.appointment_id and row.doctor_id == doctor.id
    )
    assert row.source == MedicationSource.transcript and row.dosage == "500mg"
    prompt = calls[0]["messages"][0]["content"]
    assert "Metformin 500mg is temporarily on hold." in prompt
    assert 'Do not label a temporary hold "active" or "stopped"' in prompt
    assert '"status": "active" | "stopped" | "held"' in prompt
    row.confirmed = True
    row.save()
    perform(transcript.id)
    assert len(calls) == 1
    assert (
        PatientMedication.where(PatientMedication.patient_id == patient.id).count() == 1
    )
    assert PatientMedication.one(id=row.id).confirmed
    assert PatientMedication.one(id=row.id).status.value == "held"


@pytest.mark.parametrize("status_value", ["active", "stopped"])
def test_existing_explicit_extraction_statuses_are_preserved(monkeypatch, status_value):
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    transcript = _transcript(
        doctor, patient, [("Patient", "Synthetic medication source.")]
    )
    monkeypatch.setattr(
        job,
        "extract_from_text",
        lambda _text: [{"name": "metformin", "status": status_value}],
    )
    monkeypatch.setattr(job, "normalize_drug_name", lambda _name: (None, None))
    perform(transcript.id)
    assert PatientMedication.one(patient_id=patient.id).status.value == status_value


@pytest.mark.parametrize(
    "item",
    [
        {"name": "metformin"},
        {"name": "metformin", "status": "unknown"},
        {"name": "metformin", "status": None},
        {"name": "metformin", "status": []},
        {"name": "metformin", "status": {}},
    ],
    ids=["missing", "unknown", "null", "list", "object"],
)
def test_missing_or_unknown_status_does_not_invent_active(monkeypatch, item):
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    transcript = _transcript(
        doctor, patient, [("Patient", "Metformin mentioned without current status.")]
    )
    monkeypatch.setattr(job, "extract_from_text", lambda _text: [item])

    def no_lookup(_name):
        raise AssertionError("unsupported status must not trigger drug lookup")

    monkeypatch.setattr(job, "normalize_drug_name", no_lookup)
    perform(transcript.id)
    assert (
        PatientMedication.where(PatientMedication.patient_id == patient.id).count() == 0
    )
