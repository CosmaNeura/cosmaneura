"""Source fidelity candidate for two existing staging Zoom test identities."""

from typing import Any, cast

from app.environments import is_staging

from activemodel.session_manager import get_session
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.doctor import Doctor
from app.models.external_webhook_operation import ExternalWebhookOperation
from app.models.zoom_meeting import ZoomMeeting
from sqlmodel import select

POLICY = "zoom_source_fidelity_v1"
_COHORT = {
    "doctor_01m5sf17b2f54bfj1ctsfhgwcb": (
        "user_3LBZzKKVdxBBPQz0QX3NcCi15dh",
        "scribe-lab-a+clerk_test@cosmaneura.com",
    ),
    "doctor_01m5sf1cwwfwv9mk5fnx07cwr9": (
        "user_3LBZzp4Ez8cZjPGivU8XLlk4h45",
        "scribe-lab-b+clerk_test@cosmaneura.com",
    ),
}


def eligible(transcript: AIVisitTranscript, doctor: Doctor | None = None) -> bool:
    """A queue hint cannot opt a different caller, identity or provider in."""
    if not is_staging() or str(transcript.doctor_id) not in _COHORT:
        return False
    doctor = doctor if doctor is not None else Doctor.get(id=transcript.doctor_id)
    if (
        doctor is None
        or doctor.id != transcript.doctor_id
        or doctor.deleted_at is not None
        or (doctor.clerk_id, doctor.email) != _COHORT[str(transcript.doctor_id)]
    ):
        return False
    with get_session() as session:
        meetings = session.exec(
            select(ZoomMeeting.id)
            .where(
                ZoomMeeting.doctor_id == transcript.doctor_id,
                ZoomMeeting.appointment_id == transcript.appointment_id,
                cast(Any, ZoomMeeting.capture_authorization_consumed_at).is_not(None),
            )
            .limit(2)
        ).all()
        ingestion = session.exec(
            select(ExternalWebhookOperation.id).where(
                ExternalWebhookOperation.provider == "recall",
                ExternalWebhookOperation.operation_type == "ingest_transcript",
                ExternalWebhookOperation.state == "succeeded",
                ExternalWebhookOperation.result_transcript_id == transcript.id,
            )
        ).first()
    return len(meetings) == 1 and ingestion is not None
