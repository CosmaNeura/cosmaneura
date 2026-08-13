from __future__ import annotations

import app.jobs.score_engagement
import app.jobs.transcribe

from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.appointment import Appointment


class TranscriptConflictError(Exception):
    """The appointment already has a different transcript."""


def perform(
    *,
    appointment: Appointment,
    duration: int,
    transcript_data: list[TranscriptEntry],
) -> AIVisitTranscript:
    existing = AIVisitTranscript.get(appointment_id=appointment.id)
    if existing is not None:
        existing_entries = [entry.model_dump() for entry in existing.transcript_data]
        incoming_entries = [entry.model_dump() for entry in transcript_data]
        if existing.duration != duration or existing_entries != incoming_entries:
            raise TranscriptConflictError(
                "This appointment already has a different transcript."
            )
        return existing

    transcript = AIVisitTranscript(
        doctor_id=appointment.doctor_id,
        appointment_id=appointment.id,
        patient_id=appointment.patient_id,
        duration=duration,
        transcript_data=transcript_data,
    ).save()

    app.jobs.transcribe.queue_schema_transcription(transcript)
    app.jobs.score_engagement.queue(transcript_id=transcript.id)
    return transcript
