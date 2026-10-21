"""Retained exclusion of reviewed notes preceding an acknowledged capture edit.

The content-free frontier changes neither note content nor immutable review,
notebook, source mapping or admission receipts. Old notes remain readable, but
editing/re-reviewing/restoring their body cannot clear their source staleness.
"""

from __future__ import annotations

from uuid import UUID

from app.services.scribe_capture_admission import (
    AdmittedCaptureScope,
    CaptureCaller,
    _database,
    authorize_admitted_capture_locked,
)
from app.services.scribe_configured_capture_contract import ConfiguredSessionCreate
from app.services.scribe_creator_edit_contract import CreatorTranscriptEditReceiptV1

from app.models.ai_visit_note import AIVisitNote
from app.models.appointment import Appointment
from app.models.scribe_capture_admission import ScribeCaptureAdmission
from app.models.scribe_capture_binding import ScribeCaptureBinding
from app.models.scribe_capture_correction_frontier import (
    ScribeCaptureCorrectionFrontier,
)
from app.models.scribe_capture_reviewed_source import ScribeCaptureReviewedSource
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, col, select


class CaptureSourceCorrected(ValueError):
    """Fixed, content-free refusal of stale or inconsistent capture evidence."""


class CaptureSourceBusy(CaptureSourceCorrected):
    """An expected NOWAIT conflict leaves the surrounding transaction usable."""


def _frontier(session: Session, binding_id, session_id: UUID):
    return session.exec(
        select(ScribeCaptureCorrectionFrontier)
        .where(
            ScribeCaptureCorrectionFrontier.binding_id == binding_id,
            ScribeCaptureCorrectionFrontier.session_id == session_id,
        )
        .execution_options(populate_existing=True)
    ).one_or_none()


def _valid_frontier(frontier, admission) -> bool:
    return (
        admission is not None
        and frontier.admission_id == admission.id
        and frontier.binding_id == admission.binding_id
        and frontier.session_id == admission.session_id
        and admission.configured_capture is not None
        and type(frontier.minimum_working_version) is int
        and frontier.minimum_working_version >= 2
        and isinstance(frontier.working_transcript_id, UUID)
        and isinstance(frontier.edit_id, UUID)
    )


def note_has_saved_capture_correction(session: Session, note: AIVisitNote) -> bool:
    mapping = session.exec(
        select(ScribeCaptureReviewedSource).where(
            ScribeCaptureReviewedSource.transcript_id == note.ai_transcript_id
        )
    ).one_or_none()
    if mapping is None:
        return False
    frontier = _frontier(session, mapping.binding_id, mapping.session_id)
    if frontier is None:
        return False
    admission = session.get(ScribeCaptureAdmission, frontier.admission_id)
    binding = session.get(ScribeCaptureBinding, mapping.binding_id)
    return (
        not _valid_frontier(frontier, admission)
        or binding is None
        or note.appointment_id != binding.appointment_id
        or note.doctor_id != binding.doctor_id
        or mapping.working_transcript_id != frontier.working_transcript_id
        or mapping.working_version < frontier.minimum_working_version
    )


def lock_capture_source_admissions(session: Session, note_refs: list[str]) -> None:
    """Fence correction insertion too, by locking the existing admitted capture.

    A frontier row does not exist before the first save. Locking that row alone
    would leave a phantom-insert race. NOWAIT avoids reversing the capture
    writer's authority/appointment lock order while publishing a Prep result.
    """
    if not note_refs:
        return
    try:
        with session.begin_nested():
            session.exec(
                select(ScribeCaptureAdmission)
                .join(
                    ScribeCaptureReviewedSource,
                    (
                        col(ScribeCaptureReviewedSource.binding_id)
                        == col(ScribeCaptureAdmission.binding_id)
                    )
                    & (
                        col(ScribeCaptureReviewedSource.session_id)
                        == col(ScribeCaptureAdmission.session_id)
                    ),
                )
                .join(
                    AIVisitNote,
                    col(AIVisitNote.ai_transcript_id)
                    == col(ScribeCaptureReviewedSource.transcript_id),
                )
                .where(col(AIVisitNote.id).in_(note_refs))
                .order_by(col(ScribeCaptureAdmission.id))
                .with_for_update(of=ScribeCaptureAdmission, nowait=True)
                .execution_options(populate_existing=True)
            ).all()
    except SQLAlchemyError as error:
        if getattr(getattr(error, "orig", None), "sqlstate", None) == "55P03":
            raise CaptureSourceBusy("Capture source is busy") from None
        raise CaptureSourceCorrected("Capture source is busy or unavailable") from None


def require_current_capture_source(
    session: Session,
    scope: AdmittedCaptureScope,
    *,
    working_transcript_id: UUID | None,
    working_version: int | None,
) -> None:
    """Recheck the boundary under the caller's ordinary publication locks.

    This also protects an in-flight first draft: a correction can be recorded
    before any note or imported reviewed-source mapping exists. A newer source
    must still publish a new note and receive explicit fresh clinician review.
    """
    admission = session.get(ScribeCaptureAdmission, scope.operation_ref)
    if admission is None or admission.session_id != scope.session_id:
        raise CaptureSourceCorrected("Capture source is unavailable")
    frontier = _frontier(session, admission.binding_id, scope.session_id)
    if frontier is None:
        return
    if (
        not _valid_frontier(frontier, admission)
        or frontier.working_transcript_id != working_transcript_id
        or type(working_version) is not int
        or working_version < frontier.minimum_working_version
    ):
        raise CaptureSourceCorrected("Capture source was corrected")


def retain_acknowledged_capture_correction(
    *,
    caller: CaptureCaller,
    binding_ref: str,
    workspace_id: UUID,
    scope: AdmittedCaptureScope,
    receipt: CreatorTranscriptEditReceiptV1,
) -> None:
    """Commit exclusion before returning a validated, reauthorized saved ack.

    Admission IDs/recording keys and selected session receipts are retained on
    renewal/replay. The extra stable binding/session uniqueness prevents any
    future admission rotation from silently dropping a capture's frontier.
    Existing authority locks serialize max-version writes on the original
    owned appointment and exact selected admission. No external I/O runs here.
    """
    if not isinstance(scope.session_request, ConfiguredSessionCreate):
        return
    if receipt.session_id != scope.session_id:
        raise CaptureSourceCorrected("Capture source is unavailable")
    with _database() as session:
        if (
            authorize_admitted_capture_locked(
                session, caller, binding_ref, workspace_id
            )
            != scope
        ):
            raise CaptureSourceCorrected("Capture source is unavailable")
        admission = session.get(ScribeCaptureAdmission, scope.operation_ref)
        binding = session.get(ScribeCaptureBinding, binding_ref)
        appointment = session.get(Appointment, scope.session_request.appointment_id)
        if (
            admission is None
            or binding is None
            or appointment is None
            or admission.binding_id != binding.id
            or admission.session_id != scope.session_id
            or str(binding.id) != binding_ref
            or binding.workspace_id != workspace_id
            or binding.appointment_id != appointment.id
            or binding.doctor_id != appointment.doctor_id
            or binding.patient_id != appointment.patient_id
            or str(appointment.id) != scope.session_request.appointment_id
            or str(appointment.doctor_id) != scope.session_request.practitioner_id
            or str(appointment.patient_id) != scope.session_request.patient_id
        ):
            raise CaptureSourceCorrected("Capture source is unavailable")
        mappings = session.exec(
            select(ScribeCaptureReviewedSource).where(
                ScribeCaptureReviewedSource.binding_id == binding.id,
                ScribeCaptureReviewedSource.session_id == scope.session_id,
            )
        ).all()
        if any(
            mapping.working_transcript_id != receipt.working_transcript_id
            for mapping in mappings
        ):
            raise CaptureSourceCorrected("Capture source is unavailable")
        frontier = _frontier(session, binding.id, scope.session_id)
        if frontier is not None:
            if (
                not _valid_frontier(frontier, admission)
                or frontier.working_transcript_id != receipt.working_transcript_id
            ):
                raise CaptureSourceCorrected("Capture source is unavailable")
            if frontier.minimum_working_version >= receipt.working_transcript_version:
                if (
                    frontier.minimum_working_version
                    == receipt.working_transcript_version
                    and frontier.edit_id != receipt.edit_id
                ):
                    raise CaptureSourceCorrected("Capture source is unavailable")
                session.commit()
                return
            frontier.minimum_working_version = receipt.working_transcript_version
            frontier.edit_id = receipt.edit_id
        else:
            frontier = ScribeCaptureCorrectionFrontier(
                admission_id=admission.id,
                binding_id=binding.id,
                session_id=scope.session_id,
                working_transcript_id=receipt.working_transcript_id,
                minimum_working_version=receipt.working_transcript_version,
                edit_id=receipt.edit_id,
            )
        session.add(frontier)
        session.commit()
