# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false, reportArgumentType=false, reportReturnType=false

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.jobs.transcript_cleanup import CLEANUP_AGE_DAYS, perform
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.appointment import Appointment
from app.models.patient import Patient


def _make_transcript(appt, created_offset_days: int) -> AIVisitTranscript:
    """Build, save, and back-date a transcript. ActiveModel's
    TimestampsMixin sets created_at on first save, so we save first
    then overwrite created_at and save again."""
    t = AIVisitTranscript(
        doctor_id=appt.doctor_id,
        appointment_id=appt.id,
        patient_id=appt.patient_id,
        duration=60,
        transcript_data=[
            TranscriptEntry(speaker="Patient", content="hi", startTime=0, endTime=1000)
        ],
    ).save()
    t.created_at = datetime.now(timezone.utc) - timedelta(days=created_offset_days)
    t.save()
    return t


def test_sweeper_queues_only_transcripts_older_than_cutoff():
    patient = Patient.where(Patient.name == "Samwise Gamgee").one()
    appt = Appointment.where(Appointment.patient_id == patient.id).one()

    old = _make_transcript(appt, created_offset_days=CLEANUP_AGE_DAYS + 1)
    young = _make_transcript(appt, created_offset_days=1)

    with patch("app.jobs.transcript_cleanup.queue_deletion") as q:
        perform()

    queued_ids = {call.args[0] for call in q.call_args_list}
    assert old.id in queued_ids
    assert young.id not in queued_ids


def test_sweeper_skips_already_deleted_transcripts():
    patient = Patient.where(Patient.name == "Samwise Gamgee").one()
    appt = Appointment.where(Appointment.patient_id == patient.id).one()

    already_deleted = _make_transcript(appt, created_offset_days=CLEANUP_AGE_DAYS + 1)
    already_deleted.deleted_at = datetime.now(timezone.utc)
    already_deleted.save()

    with patch("app.jobs.transcript_cleanup.queue_deletion") as q:
        perform()

    queued_ids = {call.args[0] for call in q.call_args_list}
    assert already_deleted.id not in queued_ids
