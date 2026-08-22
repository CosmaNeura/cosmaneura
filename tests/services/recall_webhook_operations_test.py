import pytest

import app.jobs.score_engagement
import app.jobs.summarize
import app.jobs.transcribe
from app.services import recall_webhook_operations as operations

from activemodel.session_manager import global_session
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.external_webhook_operation import ExternalWebhookOperation

from tests.factories import AppointmentFactory, DoctorFactory, DoctorNoteSchemaFactory


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


def test_pipeline_replays_partial_schema_fanout(monkeypatch) -> None:
    # Keep all ActiveModel helpers on one session. In production each helper
    # owns a connection; the test suite's outer savepoint intentionally shares
    # one connection and would otherwise let the schema-query generator roll
    # back a later helper's update while unwinding the simulated exception.
    with global_session():
        result, doctor, _appointment, _entries = _persist("partial-fanout-private")
        schemas = [
            DoctorNoteSchemaFactory.build(
                doctor_id=doctor.id,
                note_type=f"Partial fanout {index}",
            ).save()
            for index in range(2)
        ]
        publish_attempts: list[object] = []

        def partially_publish(*, transcript_id, schema_id):
            del transcript_id
            publish_attempts.append(schema_id)
            if len(publish_attempts) == 2:
                raise RuntimeError("broker acknowledgement lost")

        monkeypatch.setattr(app.jobs.summarize, "queue", partially_publish)
        monkeypatch.setattr(app.jobs.score_engagement, "queue", lambda **_kwargs: None)
        operations.dispatch_pipeline(result.operation_id)

        interrupted = ExternalWebhookOperation.get(id=result.operation_id)
        assert interrupted is not None
        assert interrupted.schema_pipeline_dispatched_at is None
        assert interrupted.pipeline_dispatched_at is None
        assert interrupted.last_error_code == "pipeline_publish_failed"

        monkeypatch.setattr(
            app.jobs.summarize,
            "queue",
            lambda *, transcript_id, schema_id: publish_attempts.append(schema_id),
        )
        operations.dispatch_pipeline(result.operation_id)

        repaired = ExternalWebhookOperation.get(id=result.operation_id)
        assert repaired is not None
        assert len(publish_attempts) == 4
        assert set(publish_attempts) == {schema.id for schema in schemas}
        assert repaired.schema_pipeline_dispatched_at is not None
        assert repaired.pipeline_dispatched_at is not None
        assert repaired.last_error_code is None
