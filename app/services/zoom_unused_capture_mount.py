"""Narrow staging Zoom fallback for a mounted visit with no Scribe source."""

from typing import Any, cast

from app.services import zoom_bot_note_policy
from app.services.doctor_access import doctor_access_in_force

from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.doctor import Doctor
from app.models.external_webhook_operation import ExternalWebhookOperation
from app.models.scribe_capture_admission import ScribeCaptureAdmission
from app.models.scribe_capture_binding import ScribeCaptureBinding
from app.models.scribe_capture_note_context import ScribeCaptureNoteContext
from app.models.scribe_capture_note_edit import (
    ScribeCaptureNoteEdit,
    ScribeCaptureNoteSaveFence,
)
from app.models.scribe_capture_reviewed_source import ScribeCaptureReviewedSource
from app.models.scribe_capture_source_version import ScribeCaptureSourceVersion
from app.models.scribe_transcript_delivery import ScribeTranscriptDelivery
from app.models.zoom_capture_binding import ZoomCaptureBinding
from sqlmodel import Session, select


def allows_grounded_zoom_source(
    session: Session, transcript: AIVisitTranscript, *, note_policy: str | None
) -> bool:
    """Selection is not an authority grant or a release-cohort expansion.

    Rechecked under note finalization's appointment lock. Normal Scribe binding
    and admission creation take that same lock before creating source claims;
    a concurrent admitted capture therefore retains publication precedence.
    A mere mount does not have a consent/session or publishable source.
    """
    if note_policy != zoom_bot_note_policy.POLICY:
        return False
    doctor = session.exec(
        select(Doctor)
        .where(Doctor.id == transcript.doctor_id)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if (
        doctor is None
        or not doctor_access_in_force(doctor)
        or not zoom_bot_note_policy.eligible(transcript, doctor)
    ):
        return False
    bindings = session.exec(
        select(ScribeCaptureBinding).where(
            ScribeCaptureBinding.appointment_id == transcript.appointment_id
        )
    ).all()
    if not bindings or any(
        row.doctor_id != transcript.doctor_id
        or row.patient_id != transcript.patient_id
        or row.revoked_at is not None
        for row in bindings
    ):
        return False
    binding_ids = [row.id for row in bindings]
    for model in (
        ScribeCaptureAdmission,
        ScribeCaptureSourceVersion,
        ScribeCaptureReviewedSource,
        ScribeCaptureNoteContext,
        ScribeCaptureNoteEdit,
        ScribeCaptureNoteSaveFence,
    ):
        if (
            session.exec(
                select(model.id)
                .where(cast(Any, model.binding_id).in_(binding_ids))
                .limit(1)
            ).first()
            is not None
        ):
            # Any admission phase, source version or retained human edit blocks
            # fallback, including uncertain attempts and revoked bindings.
            return False
    if (
        session.exec(
            select(ScribeTranscriptDelivery.id).where(
                ScribeTranscriptDelivery.appointment_id == transcript.appointment_id
            )
        ).first()
        is not None
        or session.exec(
            select(AIVisitNote.id).where(
                AIVisitNote.appointment_id == transcript.appointment_id,
                cast(Any, AIVisitNote.superseded_at).is_(None),
            )
        ).first()
        is not None
    ):
        return False
    current = session.exec(
        select(AIVisitTranscript)
        .where(
            AIVisitTranscript.appointment_id == transcript.appointment_id,
            cast(Any, AIVisitTranscript.deleted_at).is_(None),
        )
        .execution_options(populate_existing=True)
    ).all()
    # Do not merge other uploads or sources into the exception's Zoom recording.
    if len(current) != 1 or current[0].id != transcript.id:
        return False
    source = current[0]
    if source.duration is None or source.duration <= 0 or not source.transcript_data:
        return False
    operations = session.exec(
        select(ExternalWebhookOperation).where(
            ExternalWebhookOperation.provider == "recall",
            ExternalWebhookOperation.operation_type == "ingest_transcript",
            ExternalWebhookOperation.state == "succeeded",
            ExternalWebhookOperation.result_transcript_id == source.id,
        )
    ).all()
    if len(operations) != 1:
        return False
    from app.services.recall_webhook_operations import _content_digest
    from app.services.zoom_capture_bindings import _owned

    if operations[0].content_sha256 != _content_digest(
        doctor_id=str(source.doctor_id),
        appointment_id=str(source.appointment_id),
        patient_id=str(source.patient_id),
        duration=source.duration,
        entries=source.transcript_data,
    ):
        return False
    captures = session.exec(
        select(ZoomCaptureBinding).where(
            ZoomCaptureBinding.doctor_id == source.doctor_id,
            ZoomCaptureBinding.patient_id == source.patient_id,
            ZoomCaptureBinding.appointment_id == source.appointment_id,
        )
    ).all()
    return (
        len(captures) == 1
        and captures[0].recording_digest is not None
        and captures[0].terminal_status not in {"failed", "permission_denied"}
        and _owned(session, captures[0]) is not None
    )
