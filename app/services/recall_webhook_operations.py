"""Database fences for Recall mutations and canonical transcript fan-out."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from typeid import TypeID

import app.jobs.score_engagement
import app.jobs.transcribe
from app import log
from app.services.external_webhook_inbox import digest_provider_reference

from activemodel.session_manager import get_session
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.appointment import Appointment
from app.models.external_webhook_operation import ExternalWebhookOperation
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

PIPELINE_LEASE = timedelta(minutes=10)
PIPELINE_BATCH_SIZE = 25


class RecallOperationConflict(Exception):
    pass


@dataclass(frozen=True)
class TranscriptCreationReservation:
    operation_id: str
    disposition: Literal["call_provider", "succeeded", "uncertain"]


@dataclass(frozen=True)
class TranscriptIngestResult:
    transcript: AIVisitTranscript
    disposition: Literal["created", "replayed"]
    operation_id: str


@dataclass(frozen=True)
class PipelineClaim:
    operation_id: str
    fence: int
    transcript_id: TypeID
    schema_pending: bool
    engagement_pending: bool


def _operation_id(value: str) -> TypeID:
    parsed = TypeID.from_string(value)
    if parsed.prefix != "webhook_operation":
        raise ValueError("invalid operation reference")
    return parsed


def _resource_digest(provider_resource_id: str) -> str:
    if not provider_resource_id:
        raise RecallOperationConflict("provider resource is missing")
    return digest_provider_reference(provider_resource_id)


def reserve_transcript_creation(
    recording_id: str,
) -> TranscriptCreationReservation:
    """Reserve create_transcript before the external call.

    An existing in-flight receipt is treated as ambiguous and never replayed;
    only a signed transcript callback or an operator reconciliation may prove
    the provider-side creation succeeded.
    """

    digest = _resource_digest(recording_id)
    with get_session() as session:
        current = session.exec(
            select(ExternalWebhookOperation)
            .where(
                ExternalWebhookOperation.provider == "recall",
                ExternalWebhookOperation.operation_type == "create_transcript",
                ExternalWebhookOperation.provider_resource_digest == digest,
            )
            .with_for_update()
        ).one_or_none()
        if current is not None:
            disposition = "succeeded" if current.state == "succeeded" else "uncertain"
            if current.state == "in_flight":
                # A duplicate after the reservation committed cannot know
                # whether the first worker reached Recall. Convert the
                # orphanable in-flight state into an observable ambiguity;
                # the original worker or a signed transcript callback may
                # still reconcile it to succeeded.
                current.state = "uncertain"
                current.last_error_code = "provider_outcome_uncertain"
                session.add(current)
                session.commit()
            return TranscriptCreationReservation(str(current.id), disposition)

        operation = ExternalWebhookOperation(
            operation_type="create_transcript",
            provider_resource_digest=digest,
            state="in_flight",
        )
        session.add(operation)
        try:
            session.commit()
            session.refresh(operation)
            return TranscriptCreationReservation(str(operation.id), "call_provider")
        except IntegrityError:
            session.rollback()
            current = session.exec(
                select(ExternalWebhookOperation).where(
                    ExternalWebhookOperation.provider == "recall",
                    ExternalWebhookOperation.operation_type == "create_transcript",
                    ExternalWebhookOperation.provider_resource_digest == digest,
                )
            ).one()
            disposition = "succeeded" if current.state == "succeeded" else "uncertain"
            return TranscriptCreationReservation(str(current.id), disposition)


def _set_creation_state(operation_id: str, state: str, error_code: str | None) -> None:
    with get_session() as session:
        operation = session.exec(
            select(ExternalWebhookOperation)
            .where(
                ExternalWebhookOperation.id == _operation_id(operation_id),
                ExternalWebhookOperation.operation_type == "create_transcript",
            )
            .with_for_update()
        ).one_or_none()
        if operation is None:
            return
        operation.state = state
        operation.last_error_code = error_code
        session.add(operation)
        session.commit()


def mark_transcript_creation_succeeded(operation_id: str) -> None:
    _set_creation_state(operation_id, "succeeded", None)


def mark_transcript_creation_uncertain(operation_id: str) -> None:
    _set_creation_state(operation_id, "uncertain", "provider_outcome_uncertain")


def confirm_transcript_creation(recording_id: str) -> None:
    """A signed transcript callback resolves an ambiguous create outcome."""

    digest = _resource_digest(recording_id)
    with get_session() as session:
        operation = session.exec(
            select(ExternalWebhookOperation)
            .where(
                ExternalWebhookOperation.provider == "recall",
                ExternalWebhookOperation.operation_type == "create_transcript",
                ExternalWebhookOperation.provider_resource_digest == digest,
            )
            .with_for_update()
        ).one_or_none()
        if operation is None:
            operation = ExternalWebhookOperation(
                operation_type="create_transcript",
                provider_resource_digest=digest,
                state="succeeded",
            )
        else:
            operation.state = "succeeded"
            operation.last_error_code = None
        session.add(operation)
        try:
            session.commit()
        except IntegrityError:
            # A concurrent callback/reservation inserted the same receipt.
            session.rollback()
            operation = session.exec(
                select(ExternalWebhookOperation)
                .where(
                    ExternalWebhookOperation.provider == "recall",
                    ExternalWebhookOperation.operation_type == "create_transcript",
                    ExternalWebhookOperation.provider_resource_digest == digest,
                )
                .with_for_update()
            ).one()
            operation.state = "succeeded"
            operation.last_error_code = None
            session.add(operation)
            session.commit()


def _content_digest(
    *,
    doctor_id: str,
    appointment_id: str,
    patient_id: str,
    duration: int,
    entries: list[TranscriptEntry],
) -> str:
    canonical = json.dumps(
        {
            "doctor_id": doctor_id,
            "appointment_id": appointment_id,
            "patient_id": patient_id,
            "duration": duration,
            "entries": [entry.model_dump() for entry in entries],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _replayed_ingest(
    session, operation: ExternalWebhookOperation, content_sha256: str
) -> TranscriptIngestResult:
    if (
        operation.content_sha256 != content_sha256
        or operation.result_transcript_id is None
    ):
        raise RecallOperationConflict("provider transcript content conflict")
    transcript = session.get(AIVisitTranscript, operation.result_transcript_id)
    if transcript is None:
        raise RecallOperationConflict("canonical transcript receipt is incomplete")
    return TranscriptIngestResult(transcript, "replayed", str(operation.id))


def persist_transcript(
    *,
    provider_transcript_id: str,
    doctor_id: str,
    appointment_id: str,
    patient_id: str,
    duration: int,
    entries: list[TranscriptEntry],
) -> TranscriptIngestResult:
    """Atomically create the canonical row and its provider idempotency receipt."""

    digest = _resource_digest(provider_transcript_id)
    content_sha256 = _content_digest(
        doctor_id=doctor_id,
        appointment_id=appointment_id,
        patient_id=patient_id,
        duration=duration,
        entries=entries,
    )
    appointment_ref = TypeID.from_string(appointment_id)
    doctor_ref = TypeID.from_string(doctor_id)
    patient_ref = TypeID.from_string(patient_id)

    with get_session() as session:
        current = session.exec(
            select(ExternalWebhookOperation)
            .where(
                ExternalWebhookOperation.provider == "recall",
                ExternalWebhookOperation.operation_type == "ingest_transcript",
                ExternalWebhookOperation.provider_resource_digest == digest,
            )
            .with_for_update()
        ).one_or_none()
        if current is not None:
            return _replayed_ingest(session, current, content_sha256)

        appointment = session.exec(
            select(Appointment)
            .where(Appointment.id == appointment_ref)
            .with_for_update()
        ).one_or_none()
        if (
            appointment is None
            or appointment.doctor_id != doctor_ref
            or appointment.patient_id != patient_ref
        ):
            raise RecallOperationConflict("canonical encounter ownership conflict")

        transcript = AIVisitTranscript(
            doctor_id=appointment.doctor_id,
            appointment_id=appointment.id,
            patient_id=appointment.patient_id,
            duration=duration,
            transcript_data=entries,
        )
        session.add(transcript)
        session.flush()
        operation = ExternalWebhookOperation(
            operation_type="ingest_transcript",
            provider_resource_digest=digest,
            state="succeeded",
            content_sha256=content_sha256,
            result_transcript_id=transcript.id,
        )
        session.add(operation)
        try:
            session.commit()
            session.refresh(transcript)
            session.refresh(operation)
        except IntegrityError as exc:
            session.rollback()
            current = session.exec(
                select(ExternalWebhookOperation).where(
                    ExternalWebhookOperation.provider == "recall",
                    ExternalWebhookOperation.operation_type == "ingest_transcript",
                    ExternalWebhookOperation.provider_resource_digest == digest,
                )
            ).one_or_none()
            if current is None:
                raise RecallOperationConflict("canonical transcript race") from exc
            return _replayed_ingest(session, current, content_sha256)
        return TranscriptIngestResult(transcript, "created", str(operation.id))


def find_ingested_transcript(
    provider_transcript_id: str,
) -> TranscriptIngestResult | None:
    digest = _resource_digest(provider_transcript_id)
    with get_session() as session:
        operation = session.exec(
            select(ExternalWebhookOperation).where(
                ExternalWebhookOperation.provider == "recall",
                ExternalWebhookOperation.operation_type == "ingest_transcript",
                ExternalWebhookOperation.provider_resource_digest == digest,
                ExternalWebhookOperation.state == "succeeded",
            )
        ).one_or_none()
        if operation is None or operation.result_transcript_id is None:
            return None
        transcript = session.get(AIVisitTranscript, operation.result_transcript_id)
        if transcript is None:
            raise RecallOperationConflict("canonical transcript receipt is incomplete")
        return TranscriptIngestResult(transcript, "replayed", str(operation.id))


def claim_pipeline(operation_id: str) -> PipelineClaim | None:
    now = datetime.now(UTC)
    with get_session() as session:
        operation = session.exec(
            select(ExternalWebhookOperation)
            .where(ExternalWebhookOperation.id == _operation_id(operation_id))
            .with_for_update(skip_locked=True)
        ).one_or_none()
        if (
            operation is None
            or operation.operation_type != "ingest_transcript"
            or operation.state != "succeeded"
            or operation.result_transcript_id is None
            or operation.pipeline_dispatched_at is not None
            or (
                operation.pipeline_lease_expires_at is not None
                and operation.pipeline_lease_expires_at > now
            )
        ):
            return None
        operation.pipeline_attempt_count += 1
        operation.pipeline_lease_expires_at = now + PIPELINE_LEASE
        operation.last_error_code = None
        session.add(operation)
        session.commit()
        return PipelineClaim(
            operation_id=str(operation.id),
            fence=operation.pipeline_attempt_count,
            transcript_id=cast(TypeID, operation.result_transcript_id),
            schema_pending=operation.schema_pipeline_dispatched_at is None,
            engagement_pending=operation.engagement_pipeline_dispatched_at is None,
        )


def _mark_pipeline_step(claim: PipelineClaim, step: str) -> bool:
    now = datetime.now(UTC)
    with get_session() as session:
        operation = session.exec(
            select(ExternalWebhookOperation)
            .where(
                ExternalWebhookOperation.id == _operation_id(claim.operation_id),
                ExternalWebhookOperation.pipeline_attempt_count == claim.fence,
            )
            .with_for_update()
        ).one_or_none()
        if operation is None:
            return False
        if step == "schema":
            operation.schema_pipeline_dispatched_at = now
        elif step == "engagement":
            operation.engagement_pipeline_dispatched_at = now
        else:
            operation.pipeline_dispatched_at = now
            operation.pipeline_lease_expires_at = None
            operation.last_error_code = None
        session.add(operation)
        session.commit()
        return True


def _mark_pipeline_failure(claim: PipelineClaim) -> None:
    with get_session() as session:
        operation = session.exec(
            select(ExternalWebhookOperation)
            .where(
                ExternalWebhookOperation.id == _operation_id(claim.operation_id),
                ExternalWebhookOperation.pipeline_attempt_count == claim.fence,
            )
            .with_for_update()
        ).one_or_none()
        if operation is None:
            return
        operation.pipeline_lease_expires_at = None
        operation.last_error_code = "pipeline_publish_failed"
        session.add(operation)
        session.commit()


def dispatch_pipeline(operation_id: str) -> None:
    claim = claim_pipeline(operation_id)
    if claim is None:
        return
    transcript = AIVisitTranscript.get(id=claim.transcript_id)
    if transcript is None:
        _mark_pipeline_failure(claim)
        return
    try:
        if claim.schema_pending:
            app.jobs.transcribe.queue_schema_transcription(transcript)
            if not _mark_pipeline_step(claim, "schema"):
                return
        if claim.engagement_pending:
            app.jobs.score_engagement.queue(transcript_id=transcript.id)
            if not _mark_pipeline_step(claim, "engagement"):
                return
        _mark_pipeline_step(claim, "complete")
    except Exception as exc:
        log.warning(
            "recall transcript pipeline publication deferred",
            operation_id=operation_id,
            error_type=type(exc).__name__,
        )
        _mark_pipeline_failure(claim)


def publish_due_pipeline_operations() -> int:
    now = datetime.now(UTC)
    with get_session() as session:
        rows = session.exec(
            select(ExternalWebhookOperation)
            .where(
                ExternalWebhookOperation.operation_type == "ingest_transcript",
                ExternalWebhookOperation.state == "succeeded",
                ExternalWebhookOperation.pipeline_dispatched_at.is_(None),  # type: ignore[attr-defined]
                (
                    ExternalWebhookOperation.pipeline_lease_expires_at.is_(None)  # type: ignore[attr-defined]
                    | (
                        cast(Any, ExternalWebhookOperation.pipeline_lease_expires_at)
                        <= now
                    )
                ),
            )
            .order_by(cast(Any, ExternalWebhookOperation.created_at))
            .limit(PIPELINE_BATCH_SIZE)
        ).all()
        operation_ids = [str(operation.id) for operation in rows]

    from app.jobs.external_webhooks import dispatch_pipeline_celery

    published = 0
    for operation_id in operation_ids:
        try:
            dispatch_pipeline_celery.delay(operation_id)
            published += 1
        except Exception as exc:
            log.warning(
                "recall transcript pipeline wakeup deferred",
                operation_id=operation_id,
                error_type=type(exc).__name__,
            )
    return published
