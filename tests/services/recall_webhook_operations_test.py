import pytest

import app.jobs.score_engagement
import app.jobs.transcribe
from app.services import recall_webhook_operations as operations

from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.external_webhook_operation import ExternalWebhookOperation

from tests.factories import AppointmentFactory, DoctorFactory


def test_create_transcript_mutation_is_never_blindly_repeated() -> None:
    first = operations.reserve_transcript_creation("recording-private")
    assert first.disposition == "call_provider"

    # Simulate a process dying after the operation reservation committed but
    # before it could record the provider outcome.
    retry = operations.reserve_transcript_creation("recording-private")
    assert retry.operation_id == first.operation_id
    assert retry.disposition == "uncertain"
    ambiguous = ExternalWebhookOperation.get(id=first.operation_id)
    assert ambiguous is not None
    assert ambiguous.state == "uncertain"
    assert ambiguous.last_error_code == "provider_outcome_uncertain"

    operations.confirm_transcript_creation("recording-private")
    confirmed = operations.reserve_transcript_creation("recording-private")
    assert confirmed.operation_id == first.operation_id
    assert confirmed.disposition == "succeeded"


def _persist(provider_transcript_id: str, *, content: str = "hello"):
    doctor = DoctorFactory.build().save()
    appointment = AppointmentFactory.build(doctor_id=doctor.id).save()
    entries = [
        TranscriptEntry(
            speaker="Clinician",
            content=content,
            startTime=0,
            endTime=1000,
        )
    ]
    result = operations.persist_transcript(
        provider_transcript_id=provider_transcript_id,
        doctor_id=str(doctor.id),
        appointment_id=str(appointment.id),
        patient_id=str(appointment.patient_id),
        duration=1,
        entries=entries,
    )
    return result, doctor, appointment, entries


def test_canonical_ingest_replays_one_transcript_and_rejects_changes() -> None:
    first, doctor, appointment, entries = _persist("transcript-private")
    replay = operations.persist_transcript(
        provider_transcript_id="transcript-private",
        doctor_id=str(doctor.id),
        appointment_id=str(appointment.id),
        patient_id=str(appointment.patient_id),
        duration=1,
        entries=entries,
    )

    assert first.disposition == "created"
    assert replay.disposition == "replayed"
    assert replay.transcript.id == first.transcript.id
    assert (
        AIVisitTranscript.where(
            AIVisitTranscript.appointment_id == appointment.id
        ).count()
        == 1
    )

    with pytest.raises(operations.RecallOperationConflict):
        operations.persist_transcript(
            provider_transcript_id="transcript-private",
            doctor_id=str(doctor.id),
            appointment_id=str(appointment.id),
            patient_id=str(appointment.patient_id),
            duration=1,
            entries=[entries[0].model_copy(update={"content": "changed"})],
        )


def test_pipeline_resume_does_not_repeat_completed_step(monkeypatch) -> None:
    result, _doctor, _appointment, _entries = _persist("pipeline-private")
    schema_calls: list[object] = []
    engagement_calls: list[object] = []

    monkeypatch.setattr(
        app.jobs.transcribe,
        "queue_schema_transcription",
        lambda transcript: schema_calls.append(transcript.id),
    )

    def fail_engagement(*, transcript_id):
        engagement_calls.append(transcript_id)
        raise RuntimeError("broker detail sentinel")

    monkeypatch.setattr(app.jobs.score_engagement, "queue", fail_engagement)
    operations.dispatch_pipeline(result.operation_id)

    receipt = ExternalWebhookOperation.get(id=result.operation_id)
    assert receipt is not None
    assert receipt.schema_pipeline_dispatched_at is not None
    assert receipt.engagement_pipeline_dispatched_at is None
    assert receipt.pipeline_dispatched_at is None
    assert receipt.last_error_code == "pipeline_publish_failed"

    monkeypatch.setattr(
        app.jobs.score_engagement,
        "queue",
        lambda *, transcript_id: engagement_calls.append(transcript_id),
    )
    operations.dispatch_pipeline(result.operation_id)

    repaired = ExternalWebhookOperation.get(id=result.operation_id)
    assert repaired is not None
    assert schema_calls == [result.transcript.id]
    assert engagement_calls == [result.transcript.id, result.transcript.id]
    assert repaired.engagement_pipeline_dispatched_at is not None
    assert repaired.pipeline_dispatched_at is not None
    assert repaired.last_error_code is None
