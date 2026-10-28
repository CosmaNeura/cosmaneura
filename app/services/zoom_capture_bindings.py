"""Durable, pseudonymous authority; provider metadata never creates a binding."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from app.services.external_webhook_inbox import digest_provider_reference

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.appointment import Appointment
from app.models.patient import Patient
from app.models.zoom_capture_binding import ZoomCaptureBinding
from app.models.zoom_meeting import ZoomMeeting
from sqlalchemy import text
from sqlmodel import Session, select


@dataclass(frozen=True)
class BoundCapture:
    meeting_id: str
    doctor_id: str
    appointment_id: str
    patient_id: str
    occurrence: str


def _digest(kind: str, reference: str) -> str:
    return digest_provider_reference(f"recall:capture:{kind}:{reference}")


def _owned(session: Session, binding: ZoomCaptureBinding) -> BoundCapture | None:
    meeting = session.get(ZoomMeeting, binding.zoom_meeting_id)
    appointment = session.get(Appointment, binding.appointment_id)
    patient = session.get(Patient, binding.patient_id)
    if (
        meeting is None
        or appointment is None
        or patient is None
        or meeting.doctor_id != binding.doctor_id
        or meeting.appointment_id != binding.appointment_id
        or meeting.capture_authorization_occurrence_digest != binding.occurrence_digest
        or meeting.capture_authorization_consumed_at
        != binding.authorization_consumed_at
        or appointment.doctor_id != binding.doctor_id
        or appointment.patient_id != binding.patient_id
        or patient.doctor_id != binding.doctor_id
    ):
        return None
    return BoundCapture(
        meeting_id=meeting.zoom_meeting_id,
        doctor_id=str(binding.doctor_id),
        appointment_id=str(binding.appointment_id),
        patient_id=str(binding.patient_id),
        occurrence=binding.occurrence_digest,
    )


def bind_bot(
    *,
    doctor_id: TypeIDType,
    meeting_id: str,
    appointment_id: TypeIDType,
    patient_id: TypeIDType,
    occurrence: str,
    bot_id: str,
    require: Callable[[], None],
) -> None:
    """Publish only the already-consumed visit, before any recording starts."""
    with get_session() as session:
        session.execute(text("SET LOCAL statement_timeout = '1000ms'"))
        session.execute(text("SET LOCAL lock_timeout = '1000ms'"))
        meeting = session.exec(
            select(ZoomMeeting)
            .where(
                ZoomMeeting.doctor_id == doctor_id,
                ZoomMeeting.zoom_meeting_id == meeting_id,
            )
            .with_for_update()
        ).one_or_none()
        require()
        if (
            meeting is None
            or meeting.appointment_id != appointment_id
            or meeting.capture_authorization_occurrence_digest != occurrence
            or meeting.capture_authorization_consumed_at is None
        ):
            raise ValueError("zoom_durable_binding_authority_unavailable")
        existing = session.exec(
            select(ZoomCaptureBinding).where(
                ZoomCaptureBinding.zoom_meeting_id == meeting.id
            )
        ).one_or_none()
        if existing is not None:
            raise ValueError("zoom_durable_binding_already_consumed")
        binding = ZoomCaptureBinding(
            zoom_meeting_id=meeting.id,
            doctor_id=doctor_id,
            appointment_id=appointment_id,
            patient_id=patient_id,
            occurrence_digest=occurrence,
            authorization_consumed_at=meeting.capture_authorization_consumed_at,
            bot_digest=_digest("bot", bot_id),
        )
        if _owned(session, binding) is None:
            raise ValueError("zoom_durable_binding_tenant_unavailable")
        session.add(binding)
        require()
        session.commit()


def bind_recording(
    *, bot_id: str, recording_id: str, require: Callable[[], None]
) -> None:
    """Confirm the one new recording without replacing any prior authority."""
    with get_session() as session:
        session.execute(text("SET LOCAL statement_timeout = '1000ms'"))
        session.execute(text("SET LOCAL lock_timeout = '1000ms'"))
        binding = session.exec(
            select(ZoomCaptureBinding)
            .where(ZoomCaptureBinding.bot_digest == _digest("bot", bot_id))
            .with_for_update()
        ).one_or_none()
        require()
        if (
            binding is None
            or _owned(session, binding) is None
            or binding.recording_digest is not None
            or binding.terminal_status is not None
        ):
            raise ValueError("zoom_durable_recording_binding_unavailable")
        binding.recording_digest = _digest("recording", recording_id)
        session.add(binding)
        require()
        session.commit()


def for_recording(
    recording_id: str, *, bot_id: str | None = None
) -> BoundCapture | None:
    with get_session() as session:
        binding = session.exec(
            select(ZoomCaptureBinding).where(
                ZoomCaptureBinding.recording_digest
                == _digest("recording", recording_id)
            )
        ).one_or_none()
        if binding is None or (
            bot_id is not None and binding.bot_digest != _digest("bot", bot_id)
        ):
            return None
        return _owned(session, binding)


def project_terminal(bot_id: str, status: str, updated_at: datetime) -> bool:
    """Project a validated signed status, retaining denial/failure precedence."""
    with get_session() as session:
        binding = session.exec(
            select(ZoomCaptureBinding)
            .where(ZoomCaptureBinding.bot_digest == _digest("bot", bot_id))
            .with_for_update()
        ).one_or_none()
        if binding is None or _owned(session, binding) is None:
            return False
        previous = binding.terminal_status
        if previous == "permission_denied" or (
            previous == "failed" and status == "ended"
        ):
            status = previous
        binding.terminal_status = status
        old = binding.terminal_updated_at
        if old is None or old < updated_at:
            binding.terminal_updated_at = updated_at.astimezone(UTC)
        session.add(binding)
        session.commit()
        return True


def terminal_for_meeting(meeting: ZoomMeeting) -> str | None:
    with get_session() as session:
        binding = session.exec(
            select(ZoomCaptureBinding).where(
                ZoomCaptureBinding.zoom_meeting_id == meeting.id
            )
        ).one_or_none()
        return binding.terminal_status if binding and _owned(session, binding) else None
