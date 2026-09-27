"""Execution-time authorization checks for asynchronous PHI work."""

from datetime import datetime, timezone

from app.jobs import (
    extract,
    extract_labs,
    extract_medications,
    score_engagement,
    summarize,
    transcribe,
)

from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.doctor_note_file import DoctorNoteFile
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.engagement_role import EngagementRole
from app.models.lab_panel import LabPanel, LabPanelSource, LabPanelStatus
from app.models.patient_medication import PatientMedication
from app.models.visit_audio_file import VisitAudioFile

from tests.factories import AppointmentFactory, DoctorFactory, PatientFactory


def _visit():
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    appointment = AppointmentFactory.build(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_date=datetime.now(timezone.utc),
    ).save()
    return doctor, patient, appointment


def _deny(monkeypatch, module) -> None:
    monkeypatch.setattr(module, "doctor_access_in_force", lambda _doctor: False)


def _unexpected(*_args, **_kwargs):
    raise AssertionError("PHI-processing side effect must not run after access denial")


def _transcript(doctor, patient, appointment) -> AIVisitTranscript:
    return AIVisitTranscript(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_id=appointment.id,
        duration=30,
        transcript_data=[
            TranscriptEntry(
                speaker="Patient",
                content="private transcript sentinel",
                startTime=0,
                endTime=1,
            )
        ],
    ).save()


def test_transcribe_denial_skips_provider_and_pipeline(monkeypatch):
    doctor, patient, appointment = _visit()
    audio = VisitAudioFile(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_id=appointment.id,
        azure_file_name="private-audio-sentinel.wav",
    ).save()
    _deny(monkeypatch, transcribe)
    monkeypatch.setattr(transcribe, "transcribe_audio", _unexpected)

    transcribe.perform(audio.id)

    assert AIVisitTranscript.get(appointment_id=appointment.id) is None


def test_summarize_denial_skips_llm_and_note_write(monkeypatch):
    doctor, patient, appointment = _visit()
    transcript = _transcript(doctor, patient, appointment)
    schema = DoctorNoteSchema(
        doctor_id=doctor.id,
        note_type="Access gate test",
        fields={"CC": "Chief complaint"},
    ).save()
    _deny(monkeypatch, summarize)
    monkeypatch.setattr(summarize, "generate_clinical_note", _unexpected)

    summarize.perform(transcript.id, schema.id)

    assert AIVisitNote.get(ai_transcript_id=transcript.id) is None


def test_example_note_extraction_denial_skips_blob_download(monkeypatch):
    doctor = DoctorFactory.build().save()
    note = DoctorNoteFile(
        doctor_id=doctor.id,
        note_type="Access gate test",
        azure_file_name="private-note-sentinel.pdf",
    ).save()
    _deny(monkeypatch, extract)
    monkeypatch.setattr(extract, "download_note", _unexpected)

    extract.perform(note.id)

    assert DoctorNoteSchema.get(doctor_id=doctor.id) is None


def test_lab_extraction_denial_leaves_upload_pending(monkeypatch):
    import app.services.reference_labs_gate as lab_gate

    monkeypatch.setattr(lab_gate, "REFERENCE_LABS_ENABLED", True)
    doctor, patient, _appointment = _visit()
    panel = LabPanel(
        doctor_id=doctor.id,
        patient_id=patient.id,
        source=LabPanelSource.upload,
        status=LabPanelStatus.pending,
        azure_file_name="private-labs-sentinel.pdf",
    ).save()
    _deny(monkeypatch, extract_labs)
    monkeypatch.setattr(extract_labs, "download_pages", _unexpected)

    extract_labs.perform(panel.id)

    panel.refresh()
    assert panel.status == LabPanelStatus.pending


def test_medication_extraction_denial_skips_processing(monkeypatch):
    doctor, patient, appointment = _visit()
    transcript = _transcript(doctor, patient, appointment)
    _deny(monkeypatch, extract_medications)
    monkeypatch.setattr(extract_medications, "extract_from_text", _unexpected)

    extract_medications.perform(transcript.id)

    assert PatientMedication.get(appointment_id=appointment.id) is None


def test_engagement_scoring_denial_skips_processing(monkeypatch):
    doctor, patient, appointment = _visit()
    transcript = _transcript(doctor, patient, appointment)
    _deny(monkeypatch, score_engagement)
    monkeypatch.setattr(score_engagement, "score_patient_text", _unexpected)

    score_engagement.perform(transcript.id)

    assert EngagementRole.get(ai_transcript_id=transcript.id) is None
