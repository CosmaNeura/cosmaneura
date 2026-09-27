from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

import app.jobs.score_engagement
import app.jobs.transcribe
from app.services.doctor_access import doctor_access_in_force

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.scribe_transcript_delivery import (
    LOGICAL_SCRIBE_PRODUCER,
    ScribeTranscriptDelivery,
)
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select


class TranscriptConflictError(Exception):
    """A delivery identifier or encounter is bound to different content."""


class EncounterNotFoundError(Exception):
    """The canonical encounter does not exist."""


class TranscriptPersistenceError(Exception):
    """The delivery could not be durably recorded."""


class DoctorAccessNotInForceError(Exception):
    """The encounter owner is not currently authorized for PHI processing."""


@dataclass(frozen=True)
class TranscriptImportResult:
    transcript: AIVisitTranscript
    disposition: Literal["created", "replayed"]


def _entry_data(entries: list[TranscriptEntry]) -> list[dict[str, object]]:
    return [entry.model_dump() for entry in entries]


def _matching_receipt(
    session: Session,
    *,
    logical_producer: str,
    idempotency_key: str,
    scribe_session_id: UUID,
    appointment_id: TypeIDType,
    payload_sha256: str,
) -> ScribeTranscriptDelivery | None:
    by_key = session.exec(
        select(ScribeTranscriptDelivery).where(
            ScribeTranscriptDelivery.logical_producer == logical_producer,
            ScribeTranscriptDelivery.idempotency_key == idempotency_key,
        )
    ).first()
    by_session = session.exec(
        select(ScribeTranscriptDelivery).where(
            ScribeTranscriptDelivery.logical_producer == logical_producer,
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
    session: Session,
    receipt: ScribeTranscriptDelivery,
    *,
    dispatch_pipeline: bool = True,
) -> TranscriptImportResult:
    transcript = session.get(AIVisitTranscript, receipt.transcript_id)
    if transcript is None:
        raise TranscriptPersistenceError("Transcript receipt is incomplete.")
    if dispatch_pipeline:
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
    dispatch_pipeline: bool = True,
    commit_import: bool = True,
) -> TranscriptImportResult:
    if not commit_import and dispatch_pipeline:
        raise ValueError("Deferred import cannot dispatch background work.")
    # Serializing deliveries for one encounter prevents two callers from
    # creating competing transcript rows before either receipt is visible.
    appointment = session.exec(
        select(Appointment).where(Appointment.id == encounter_id).with_for_update()
    ).first()
    if appointment is None:
        raise EncounterNotFoundError

    doctor = session.get(Doctor, appointment.doctor_id)
    if doctor is None or not doctor_access_in_force(doctor):
        raise DoctorAccessNotInForceError

    receipt = _matching_receipt(
        session,
        logical_producer=LOGICAL_SCRIBE_PRODUCER,
        idempotency_key=idempotency_key,
        scribe_session_id=scribe_session_id,
        appointment_id=appointment.id,
        payload_sha256=payload_sha256,
    )
    if receipt is not None:
        return _replayed_result(session, receipt, dispatch_pipeline=dispatch_pipeline)

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
        logical_producer=LOGICAL_SCRIBE_PRODUCER,
        caller_client_id=caller_client_id,
        idempotency_key=idempotency_key,
        scribe_session_id=scribe_session_id,
        appointment_id=appointment.id,
        transcript_id=transcript.id,
        payload_sha256=payload_sha256,
    )
    session.add(delivery)

    if not commit_import:
        # The explicit capture receiver must atomically add its source/gap
        # provenance before any background reader can observe this delivery.
        # It owns the outer commit and rollback; legacy callers retain their
        # existing committed import and dispatch behavior below.
        try:
            session.flush()
        except IntegrityError as exc:
            raise TranscriptPersistenceError from exc
        return TranscriptImportResult(
            transcript=transcript,
            disposition="created" if created else "replayed",
        )

    try:
        session.commit()
        session.refresh(transcript)
    except IntegrityError as exc:
        session.rollback()
        receipt = _matching_receipt(
            session,
            logical_producer=LOGICAL_SCRIBE_PRODUCER,
            idempotency_key=idempotency_key,
            scribe_session_id=scribe_session_id,
            appointment_id=appointment.id,
            payload_sha256=payload_sha256,
        )
        if receipt is None:
            raise TranscriptPersistenceError from exc
        return _replayed_result(session, receipt, dispatch_pipeline=dispatch_pipeline)

    if dispatch_pipeline:
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
