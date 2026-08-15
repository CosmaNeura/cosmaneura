from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

import app.jobs.score_engagement
import app.jobs.transcribe

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.appointment import Appointment
from app.models.scribe_transcript_delivery import ScribeTranscriptDelivery
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select


class TranscriptConflictError(Exception):
    """A delivery identifier or encounter is bound to different content."""


class EncounterNotFoundError(Exception):
    """The canonical encounter does not exist."""


class TranscriptPersistenceError(Exception):
    """The delivery could not be durably recorded."""


@dataclass(frozen=True)
class TranscriptImportResult:
    transcript: AIVisitTranscript
    disposition: Literal["created", "replayed"]


def _entry_data(entries: list[TranscriptEntry]) -> list[dict[str, object]]:
    return [entry.model_dump() for entry in entries]


def _matching_receipt(
    session: Session,
    *,
    caller_client_id: str,
    idempotency_key: str,
    scribe_session_id: UUID,
    appointment_id: TypeIDType,
    payload_sha256: str,
) -> ScribeTranscriptDelivery | None:
    by_key = session.exec(
        select(ScribeTranscriptDelivery).where(
            ScribeTranscriptDelivery.caller_client_id == caller_client_id,
            ScribeTranscriptDelivery.idempotency_key == idempotency_key,
        )
    ).first()
    by_session = session.exec(
        select(ScribeTranscriptDelivery).where(
            ScribeTranscriptDelivery.caller_client_id == caller_client_id,
            ScribeTranscriptDelivery.scribe_session_id == scribe_session_id,
        )
    ).first()

    if by_key is not None and by_session is not None and by_key.id != by_session.id:
        raise TranscriptConflictError("Delivery identifiers conflict.")

    receipt = by_key or by_session
    if receipt is None:
        return None

    if (
        receipt.scribe_session_id != scribe_session_id
        or receipt.appointment_id != appointment_id
        or receipt.payload_sha256 != payload_sha256
    ):
        raise TranscriptConflictError("Delivery identifiers conflict.")
    return receipt


def _replayed_result(
    session: Session, receipt: ScribeTranscriptDelivery
) -> TranscriptImportResult:
    transcript = session.get(AIVisitTranscript, receipt.transcript_id)
    if transcript is None:
        raise TranscriptPersistenceError("Transcript receipt is incomplete.")
    _dispatch_pipeline_if_needed(session, receipt, transcript)
    return TranscriptImportResult(transcript=transcript, disposition="replayed")


def _dispatch_pipeline_if_needed(
    session: Session,
    receipt: ScribeTranscriptDelivery,
    transcript: AIVisitTranscript,
) -> None:
    if receipt.pipeline_dispatched_at is not None:
        return

    # Downstream tasks are idempotent. Mark the receipt only after both sends
    # return so a transient broker failure is repaired by the next delivery retry.
    app.jobs.transcribe.queue_schema_transcription(transcript)
    app.jobs.score_engagement.queue(transcript_id=transcript.id)
    receipt.pipeline_dispatched_at = datetime.now(UTC)
    session.add(receipt)
    session.commit()


def _perform(
    *,
    session: Session,
    encounter_id: TypeIDType,
    caller_client_id: str,
    idempotency_key: str,
    scribe_session_id: UUID,
    payload_sha256: str,
    duration: int,
    transcript_data: list[TranscriptEntry],
) -> TranscriptImportResult:
    # Serializing deliveries for one encounter prevents two callers from
    # creating competing transcript rows before either receipt is visible.
    appointment = session.exec(
        select(Appointment).where(Appointment.id == encounter_id).with_for_update()
    ).first()
    if appointment is None:
        raise EncounterNotFoundError

    receipt = _matching_receipt(
        session,
        caller_client_id=caller_client_id,
        idempotency_key=idempotency_key,
        scribe_session_id=scribe_session_id,
        appointment_id=appointment.id,
        payload_sha256=payload_sha256,
    )
    if receipt is not None:
        return _replayed_result(session, receipt)

    existing = session.exec(
        select(AIVisitTranscript).where(
            AIVisitTranscript.appointment_id == appointment.id
        )
    ).first()
    created = existing is None
    if existing is not None:
        if existing.duration != duration or _entry_data(
            existing.transcript_data
        ) != _entry_data(transcript_data):
            raise TranscriptConflictError(
                "This encounter already has a different transcript."
            )
        transcript = existing
    else:
        transcript = AIVisitTranscript(
            doctor_id=appointment.doctor_id,
            appointment_id=appointment.id,
            patient_id=appointment.patient_id,
            duration=duration,
            transcript_data=transcript_data,
        )
        session.add(transcript)
        session.flush()

    delivery = ScribeTranscriptDelivery(
        caller_client_id=caller_client_id,
        idempotency_key=idempotency_key,
        scribe_session_id=scribe_session_id,
        appointment_id=appointment.id,
        transcript_id=transcript.id,
        payload_sha256=payload_sha256,
    )
    session.add(delivery)

    try:
        session.commit()
        session.refresh(transcript)
    except IntegrityError as exc:
        session.rollback()
        receipt = _matching_receipt(
            session,
            caller_client_id=caller_client_id,
            idempotency_key=idempotency_key,
            scribe_session_id=scribe_session_id,
            appointment_id=appointment.id,
            payload_sha256=payload_sha256,
        )
        if receipt is None:
            raise TranscriptPersistenceError from exc
        return _replayed_result(session, receipt)

    _dispatch_pipeline_if_needed(session, delivery, transcript)

    return TranscriptImportResult(
        transcript=transcript,
        disposition="created" if created else "replayed",
    )


def perform(
    *,
    encounter_id: TypeIDType,
    caller_client_id: str,
    idempotency_key: str,
    scribe_session_id: UUID,
    payload_sha256: str,
    duration: int,
    transcript_data: list[TranscriptEntry],
) -> TranscriptImportResult:
    with get_session() as session:
        return _perform(
            session=session,
            encounter_id=encounter_id,
            caller_client_id=caller_client_id,
            idempotency_key=idempotency_key,
            scribe_session_id=scribe_session_id,
            payload_sha256=payload_sha256,
            duration=duration,
            transcript_data=transcript_data,
        )
