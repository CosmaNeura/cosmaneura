"""Reviewed English provenance for Core's existing canonical import and note engine."""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from uuid import UUID

import orjson
from pydantic import SerializerFunctionWrapHandler, model_serializer
from typeid import TypeID

from app.commands import scribe_transcript as ingest
from app.services.scribe_capture_admission import AdmittedCaptureScope
from app.services.scribe_capture_continuity import CaptureContinuity
from app.services.scribe_capture_working_contract import (
    ProtectedContract,
    Version,
    canonical_hash,
)
from app.services.scribe_configured_capture_contract import ConfiguredSessionCreate
from app.services.scribe_creator_review_contract import CreatorReviewView

from activemodel.types import TypeIDType
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.scribe_capture_admission import ScribeCaptureAdmission
from app.models.scribe_capture_reviewed_source import ScribeCaptureReviewedSource
from app.models.scribe_transcript_delivery import (
    LOGICAL_SCRIBE_PRODUCER,
    ScribeTranscriptDelivery,
)
from sqlmodel import Session, select


class ReviewedSourceConflict(Exception):
    pass


class ReviewedSourceReference(ProtectedContract):
    kind: Literal["reviewed_english"] = "reviewed_english"
    source_version: Version
    working_transcript_id: UUID
    working_version: Version
    capture_continuity: CaptureContinuity | None = None

    @model_serializer(mode="wrap")
    def compatible_continuity(self, handler: SerializerFunctionWrapHandler):
        value = handler(self)
        if self.capture_continuity is None:
            value.pop("capture_continuity", None)
        return value


@dataclass(frozen=True, repr=False)
class ReviewedSource:
    reference: ReviewedSourceReference
    duration: int
    entries: list[TranscriptEntry]
    digest: str
    payload_digest: str
    capture_continuity: CaptureContinuity | None = None


def payload_digest(scope, duration, entries):
    document = dict(
        schema_version="1.0",
        encounter_id=scope.session_request.appointment_id,
        scribe_session_id=str(scope.session_id),
        duration_seconds=duration,
        segments=[
            dict(
                speaker=e.speaker,
                content=e.content,
                start_ms=e.startTime,
                end_ms=e.endTime,
            )
            for e in entries
        ],
    )
    return hashlib.sha256(
        orjson.dumps(document, option=orjson.OPT_SORT_KEYS)
    ).hexdigest()


def project(view: CreatorReviewView, scope: AdmittedCaptureScope) -> ReviewedSource:
    view = CreatorReviewView.model_validate(view.model_dump(mode="json"))
    working, source = view.working, view.source
    if (
        view.session_id != scope.session_id
        or working is None
        or source is None
        or not working.note_ready
    ):
        raise ReviewedSourceConflict
    if (
        not isinstance(scope.session_request, ConfiguredSessionCreate)
        or source.language_mode != scope.session_request.language_mode
        or source.transcription_adapter_id != "azure_ai_speech"
        or source.transcription_model_profile_id != "azure_fast_transcription_en_es_v1"
        or source.normalization_profile_id != "azure_phrase_en_es_evidence_v1"
    ):
        raise ReviewedSourceConflict
    roles = {
        "clinician": "Doctor",
        "patient": "Patient",
        "other": "Other",
        "unknown": "Unknown",
    }
    entries = []
    for segment in working.segments:
        if segment.content is None:
            raise ReviewedSourceConflict
        entries.append(
            TranscriptEntry(
                speaker=roles[segment.speaker_role],
                content=segment.content,
                startTime=segment.start_ms,
                endTime=segment.end_ms,
            )
        )
    duration = (max(s.endTime for s in entries) + 999) // 1000
    return ReviewedSource(
        ReviewedSourceReference(
            source_version=source.source_transcript_version,
            working_transcript_id=working.working_transcript_id,
            working_version=working.working_transcript_version,
            capture_continuity=view.capture_continuity,
        ),
        duration,
        entries,
        canonical_hash(
            {
                "session_id": scope.session_id,
                "source": source,
                "working": working,
                **(
                    {"capture_continuity": view.capture_continuity}
                    if view.capture_continuity is not None
                    else {}
                ),
            }
        ),
        payload_digest(scope, duration, entries),
        view.capture_continuity,
    )


def _rows(session: Session, scope):
    admission = session.exec(
        select(ScribeCaptureAdmission).where(
            ScribeCaptureAdmission.id == scope.operation_ref
        )
    ).one()
    if admission.configured_capture is None or admission.session_id != scope.session_id:
        raise ReviewedSourceConflict
    rows = list(
        session.exec(
            select(ScribeCaptureReviewedSource)
            .where(ScribeCaptureReviewedSource.binding_id == admission.binding_id)
            .order_by(cast(Any, ScribeCaptureReviewedSource.working_version))
        ).all()
    )
    return admission.binding_id, rows


def select_transcript(session, scope, source: ReviewedSource, transcripts, delivery):
    _, rows = _rows(session, scope)
    if not rows:
        if transcripts or delivery is not None:
            raise ReviewedSourceConflict
        return None, set(), False
    by_id = {row.id: row for row in transcripts}
    known = {row.transcript_id for row in rows}
    if (
        delivery is None
        or not known.issubset(by_id)
        or (
            delivery.transcript_id != rows[0].transcript_id
            or delivery.payload_sha256 != rows[0].payload_sha256
            or str(delivery.appointment_id) != scope.session_request.appointment_id
            or rows[-1].working_version > source.reference.working_version
        )
    ):
        raise ReviewedSourceConflict
    selected = None
    for mapping in rows:
        transcript = by_id[mapping.transcript_id]
        if (
            mapping.session_id != scope.session_id
            or mapping.working_transcript_id != source.reference.working_transcript_id
            or mapping.source_version > source.reference.source_version
            or transcript.deleted_at is not None
            or str(transcript.doctor_id) != scope.session_request.practitioner_id
            or str(transcript.patient_id) != scope.session_request.patient_id
            or str(transcript.appointment_id) != scope.session_request.appointment_id
            or payload_digest(scope, transcript.duration, transcript.transcript_data)
            != mapping.payload_sha256
        ):
            raise ReviewedSourceConflict
        if mapping.working_version == source.reference.working_version:
            if (
                mapping.source_version != source.reference.source_version
                or mapping.source_sha256 != source.digest
                or mapping.payload_sha256 != source.payload_digest
            ):
                raise ReviewedSourceConflict
            selected = transcript
    return (
        selected,
        known,
        source.reference.working_version != rows[0].working_version,
    )


def import_source(
    session: Session, scope, source: ReviewedSource, caller_client_id: str
):
    binding_id, rows = _rows(session, scope)
    transcripts = list(
        session.exec(
            select(AIVisitTranscript).where(
                AIVisitTranscript.appointment_id == scope.session_request.appointment_id
            )
        ).all()
    )
    delivery = session.exec(
        select(ScribeTranscriptDelivery).where(
            ScribeTranscriptDelivery.logical_producer == LOGICAL_SCRIBE_PRODUCER,
            ScribeTranscriptDelivery.scribe_session_id == scope.session_id,
        )
    ).one_or_none()
    selected, _, _ = select_transcript(session, scope, source, transcripts, delivery)
    if selected is not None:
        return
    if not rows:
        ingest._perform(
            session=session,
            encounter_id=cast(
                TypeIDType, TypeID.from_string(scope.session_request.appointment_id)
            ),
            caller_client_id=caller_client_id,
            idempotency_key="core-source-" + scope.operation_ref,
            scribe_session_id=scope.session_id,
            payload_sha256=source.payload_digest,
            duration=source.duration,
            transcript_data=source.entries,
            dispatch_pipeline=False,
            commit_import=False,
        )
        delivery = session.exec(
            select(ScribeTranscriptDelivery).where(
                ScribeTranscriptDelivery.logical_producer == LOGICAL_SCRIBE_PRODUCER,
                ScribeTranscriptDelivery.scribe_session_id == scope.session_id,
            )
        ).one()
        transcript_id = delivery.transcript_id
    else:
        transcript = AIVisitTranscript(
            doctor_id=cast(
                TypeIDType, TypeID.from_string(scope.session_request.practitioner_id)
            ),
            patient_id=cast(
                TypeIDType, TypeID.from_string(scope.session_request.patient_id)
            ),
            appointment_id=cast(
                TypeIDType, TypeID.from_string(scope.session_request.appointment_id)
            ),
            duration=source.duration,
            transcript_data=source.entries,
        )
        times = [row.created_at for row in transcripts if row.created_at is not None]
        if len(times) != len(transcripts):
            raise ReviewedSourceConflict
        transcript.created_at = max(
            datetime.now(UTC), max(times) + timedelta(microseconds=1)
        )
        session.add(transcript)
        session.flush()
        transcript_id = transcript.id
    session.add(
        ScribeCaptureReviewedSource(
            binding_id=binding_id,
            session_id=scope.session_id,
            source_version=source.reference.source_version,
            working_transcript_id=source.reference.working_transcript_id,
            working_version=source.reference.working_version,
            source_sha256=source.digest,
            payload_sha256=source.payload_digest,
            transcript_id=transcript_id,
        )
    )
    session.flush()
