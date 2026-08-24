"""Safe current-version selection for retained generated clinical notes.

No note body, patient identifier, or direct visit/note identifier is written to
the audit table or emitted by this module. The operator command uses a reviewed
plan digest so a stale current version cannot be swapped accidentally.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any, cast

from app.utils.encryption import pseudonymous_digest

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.ai_visit_note import ADMIN_RESTORE_SUPERSESSION, AIVisitNote
from app.models.ai_visit_note_version_audit import AIVisitNoteVersionAudit
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.appointment import Appointment
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.patient import Patient
from sqlalchemy import func, update
from sqlmodel import Session, select

_OPAQUE_OPERATOR_REF = re.compile(r"^[A-Za-z0-9._:/~-]+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class VisitNoteRestoreReason(str, Enum):
    operator_selected_prior_version = "operator_selected_prior_version"
    clinical_correction = "clinical_correction"
    incident_recovery = "incident_recovery"


class VisitNoteVersionError(ValueError):
    """Safe, operator-facing refusal with no clinical identifiers or content."""


@dataclass(frozen=True)
class VisitNoteRestorePlan:
    plan_sha256: str
    current_note_ref: str
    target_note_ref: str
    version_count: int
    transition_sequence: int
    restore_frontier_transcript_ref: str | None


@dataclass(frozen=True)
class VisitNoteRestoreResult:
    plan_sha256: str
    audit_id: TypeIDType
    changed: bool


def _operator_ref(value: str) -> str:
    normalized = value.strip()
    if not 3 <= len(normalized) <= 255 or not _OPAQUE_OPERATOR_REF.fullmatch(
        normalized
    ):
        raise VisitNoteVersionError(
            "operator_ref must be a 3-255 character opaque audit reference"
        )
    return normalized


def _note_ref(value: object) -> str:
    return pseudonymous_digest(str(value), context="visit-note-version-audit")


def _plan(
    *,
    target: AIVisitNote,
    current: AIVisitNote,
    version_count: int,
    transition_sequence: int,
    restore_frontier: AIVisitTranscript | None,
    operator_ref: str,
    reason: VisitNoteRestoreReason,
) -> VisitNoteRestorePlan:
    current_ref = _note_ref(current.id)
    target_ref = _note_ref(target.id)
    frontier_ref = (
        _note_ref(restore_frontier.id) if restore_frontier is not None else None
    )
    payload = json.dumps(
        {
            "appointment_ref": _note_ref(target.appointment_id),
            "current_note_ref": current_ref,
            "operator_ref": operator_ref,
            "reason_code": reason.value,
            "restore_frontier_transcript_ref": frontier_ref,
            "schema_ref": _note_ref(target.doctor_note_schema_id),
            "target_note_ref": target_ref,
            "transition_sequence": transition_sequence,
            "version_count": version_count,
            "version": 2,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return VisitNoteRestorePlan(
        plan_sha256=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        current_note_ref=current_ref,
        target_note_ref=target_ref,
        version_count=version_count,
        transition_sequence=transition_sequence,
        restore_frontier_transcript_ref=frontier_ref,
    )


def _family_refs(target: AIVisitNote) -> tuple[str, str]:
    return _note_ref(target.appointment_id), _note_ref(target.doctor_note_schema_id)


def _next_transition_sequence(session: Session, target: AIVisitNote) -> int:
    """Return the next monotonic transition number for one logical note slot."""

    appointment_ref, schema_ref = _family_refs(target)
    latest = session.exec(
        select(func.coalesce(func.max(AIVisitNoteVersionAudit.transition_sequence), 0))
        .select_from(AIVisitNoteVersionAudit)
        .where(
            AIVisitNoteVersionAudit.appointment_ref_sha256 == appointment_ref,
            AIVisitNoteVersionAudit.schema_ref_sha256 == schema_ref,
        )
    ).one()
    return int(latest) + 1


def _latest_family_audit(
    session: Session, target: AIVisitNote
) -> AIVisitNoteVersionAudit | None:
    appointment_ref, schema_ref = _family_refs(target)
    return session.exec(
        select(AIVisitNoteVersionAudit)
        .where(
            AIVisitNoteVersionAudit.appointment_ref_sha256 == appointment_ref,
            AIVisitNoteVersionAudit.schema_ref_sha256 == schema_ref,
        )
        .order_by(
            cast(Any, AIVisitNoteVersionAudit.transition_sequence).desc(),
            cast(Any, AIVisitNoteVersionAudit.id).desc(),
        )
        .limit(1)
    ).one_or_none()


def _transcript_matches_appointment(
    transcript: AIVisitTranscript, appointment: Appointment
) -> bool:
    return (
        transcript.appointment_id == appointment.id
        and transcript.doctor_id == appointment.doctor_id
        and transcript.patient_id == appointment.patient_id
    )


def _validate_family_scope(
    session: Session,
    *,
    appointment: Appointment,
    versions: list[AIVisitNote],
) -> None:
    """Fail closed when foreign keys form a cross-tenant logical family.

    Individual foreign keys prove that referenced rows exist; they do not prove
    that a note, schema, transcript, appointment, doctor, and patient all belong
    to the same clinical scope. Restore validates that stronger invariant while
    holding the appointment serialization lock on apply.
    """

    if not versions:
        raise VisitNoteVersionError("note version family scope validation failed")
    schema_id = versions[0].doctor_note_schema_id
    schema = session.exec(
        select(DoctorNoteSchema).where(DoctorNoteSchema.id == schema_id)
    ).one_or_none()
    patient = session.exec(
        select(Patient).where(Patient.id == appointment.patient_id)
    ).one_or_none()
    if (
        schema is None
        or schema.doctor_id != appointment.doctor_id
        or patient is None
        or patient.doctor_id != appointment.doctor_id
    ):
        raise VisitNoteVersionError("note version family scope validation failed")

    transcript_ids = {
        transcript_id
        for note in versions
        for transcript_id in (
            note.ai_transcript_id,
            note.restore_frontier_transcript_id,
        )
        if transcript_id is not None
    }
    transcripts = {
        transcript.id: transcript
        for transcript in session.exec(
            select(AIVisitTranscript).where(
                cast(Any, AIVisitTranscript.id).in_(list(transcript_ids))
            )
        ).all()
    }
    if len(transcripts) != len(transcript_ids):
        raise VisitNoteVersionError("note version family scope validation failed")

    for note in versions:
        if (
            note.appointment_id != appointment.id
            or note.doctor_note_schema_id != schema.id
            or note.doctor_id != appointment.doctor_id
        ):
            raise VisitNoteVersionError("note version family scope validation failed")
        transcript = transcripts.get(note.ai_transcript_id)
        if transcript is None or not _transcript_matches_appointment(
            transcript, appointment
        ):
            raise VisitNoteVersionError("note version family scope validation failed")
        if note.restore_frontier_transcript_id is not None:
            frontier = transcripts.get(note.restore_frontier_transcript_id)
            if frontier is None or not _transcript_matches_appointment(
                frontier, appointment
            ):
                raise VisitNoteVersionError(
                    "note version family scope validation failed"
                )


def _latest_active_transcript(
    session: Session, appointment: Appointment
) -> AIVisitTranscript | None:
    transcript = session.exec(
        select(AIVisitTranscript)
        .where(
            AIVisitTranscript.appointment_id == appointment.id,
            cast(Any, AIVisitTranscript.deleted_at).is_(None),
        )
        .order_by(
            cast(Any, AIVisitTranscript.created_at).desc(),
            cast(Any, AIVisitTranscript.id).desc(),
        )
        .limit(1)
    ).one_or_none()
    if transcript is not None and not _transcript_matches_appointment(
        transcript, appointment
    ):
        raise VisitNoteVersionError("note version family scope validation failed")
    return transcript


def _versions_for_target(
    session: Session,
    target_note_id: TypeIDType,
    *,
    lock: bool,
    locked_appointment: Appointment | None = None,
    allow_target_current: bool = False,
) -> tuple[AIVisitNote, AIVisitNote, list[AIVisitNote], Appointment]:
    target = session.exec(
        select(AIVisitNote).where(AIVisitNote.id == target_note_id)
    ).one_or_none()
    if target is None:
        raise VisitNoteVersionError("target note version was not found")

    appointment = locked_appointment
    if appointment is None:
        appointment_query = select(Appointment).where(
            Appointment.id == target.appointment_id
        )
        if lock:
            # Summarization uses this same appointment row as its serialization
            # lock, so restore and generation cannot select current rows at once.
            appointment_query = appointment_query.with_for_update()
        appointment = session.exec(appointment_query).one_or_none()
    if appointment is None or appointment.id != target.appointment_id:
        raise VisitNoteVersionError("target note version scope is incomplete")

    versions_query = (
        select(AIVisitNote)
        .where(
            AIVisitNote.appointment_id == target.appointment_id,
            AIVisitNote.doctor_note_schema_id == target.doctor_note_schema_id,
        )
        .order_by(cast(Any, AIVisitNote.id).asc())
    )
    if lock:
        # AIVisitNote eagerly joins its schema relationship. Restrict the lock
        # target to the note table so PostgreSQL never tries to lock the
        # nullable side of that outer join. Refresh identity-map rows because a
        # concurrent restore may have committed while this transaction waited
        # for the appointment lock.
        versions_query = versions_query.with_for_update(
            of=AIVisitNote
        ).execution_options(populate_existing=True)
    versions = list(session.exec(versions_query).all())
    target = next((row for row in versions if row.id == target_note_id), None)
    if target is None or target.appointment_id != appointment.id:
        raise VisitNoteVersionError("target note version scope is incomplete")
    current = [row for row in versions if row.superseded_at is None]
    if len(current) != 1:
        raise VisitNoteVersionError(
            "note version group does not contain exactly one current row"
        )
    _validate_family_scope(session, appointment=appointment, versions=versions)
    if target.superseded_at is None and not allow_target_current:
        raise VisitNoteVersionError("target note version is already current")
    return target, current[0], versions, appointment


def plan_visit_note_restore(
    *,
    target_note_id: TypeIDType,
    operator_ref: str,
    reason: VisitNoteRestoreReason,
) -> VisitNoteRestorePlan:
    """Return a content-free plan for promoting one retained version."""

    operator = _operator_ref(operator_ref)
    with get_session() as session:
        target, current, versions, appointment = _versions_for_target(
            session, target_note_id, lock=False
        )
        return _plan(
            target=target,
            current=current,
            version_count=len(versions),
            transition_sequence=_next_transition_sequence(session, target),
            restore_frontier=_latest_active_transcript(session, appointment),
            operator_ref=operator,
            reason=reason,
        )


def restore_visit_note_version(
    *,
    target_note_id: TypeIDType,
    operator_ref: str,
    reason: VisitNoteRestoreReason,
    confirm_plan_sha256: str,
    now: datetime | None = None,
) -> VisitNoteRestoreResult:
    """Atomically promote a retained note and append pseudonymous evidence."""

    operator = _operator_ref(operator_ref)
    confirmed = confirm_plan_sha256.strip().lower()
    if not _SHA256.fullmatch(confirmed):
        raise VisitNoteVersionError(
            "confirm_plan_sha256 must be a 64-character lowercase hex digest"
        )

    with get_session() as session:
        # Resolve only the appointment scope before taking the same
        # serialization lock used by note generation. The audit replay check
        # deliberately happens after this lock: a concurrent invocation with
        # the same plan can finish while we wait, and must then be observed as
        # an idempotent replay instead of a misleading "already current"
        # refusal.
        target_scope = session.exec(
            select(AIVisitNote).where(AIVisitNote.id == target_note_id)
        ).one_or_none()
        if target_scope is None:
            raise VisitNoteVersionError("target note version was not found")
        appointment = session.exec(
            select(Appointment)
            .where(Appointment.id == target_scope.appointment_id)
            .with_for_update()
        ).one_or_none()
        if appointment is None:
            raise VisitNoteVersionError("target note version scope is incomplete")

        replay = session.exec(
            select(AIVisitNoteVersionAudit).where(
                AIVisitNoteVersionAudit.plan_sha256 == confirmed
            )
        ).one_or_none()
        if replay is not None:
            appointment_ref, schema_ref = _family_refs(target_scope)
            if (
                replay.operator_ref != operator
                or replay.reason_code != reason.value
                or replay.restored_note_ref_sha256 != _note_ref(target_note_id)
                or replay.appointment_ref_sha256 != appointment_ref
                or replay.schema_ref_sha256 != schema_ref
            ):
                raise VisitNoteVersionError(
                    "confirmed restore plan belongs to a different operation"
                )
            target, current, _versions, _appointment = _versions_for_target(
                session,
                target_note_id,
                lock=True,
                locked_appointment=appointment,
                allow_target_current=True,
            )
            latest_audit = _latest_family_audit(session, target)
            if latest_audit is None or latest_audit.id != replay.id:
                raise VisitNoteVersionError(
                    "confirmed restore plan is not the latest family transition"
                )
            if current.id != target.id:
                raise VisitNoteVersionError(
                    "confirmed restore plan was applied but is no longer current"
                )
            return VisitNoteRestoreResult(
                plan_sha256=confirmed,
                audit_id=replay.id,
                changed=False,
            )

        target, current, versions, appointment = _versions_for_target(
            session,
            target_note_id,
            lock=True,
            locked_appointment=appointment,
        )
        restore_frontier = _latest_active_transcript(session, appointment)
        plan = _plan(
            target=target,
            current=current,
            version_count=len(versions),
            transition_sequence=_next_transition_sequence(session, target),
            restore_frontier=restore_frontier,
            operator_ref=operator,
            reason=reason,
        )
        if not hmac.compare_digest(confirmed, plan.plan_sha256):
            raise VisitNoteVersionError(
                "confirmed restore plan no longer matches current note state"
            )

        effective_at = now or datetime.now(UTC)
        if effective_at.tzinfo is None:
            raise VisitNoteVersionError("restore time must include a timezone")

        # Release the partial unique slot first. Both statements and the audit
        # insert remain invisible until this transaction commits. Explicitly
        # retain updated_at so a version-state change never rewrites a clinical
        # content timestamp.
        session.execute(
            update(cast(Any, AIVisitNote))
            .where(cast(Any, AIVisitNote.id) == current.id)
            .values(
                superseded_at=effective_at,
                supersession_reason=ADMIN_RESTORE_SUPERSESSION,
                updated_at=current.updated_at,
            )
        )
        session.flush()
        session.execute(
            update(cast(Any, AIVisitNote))
            .where(cast(Any, AIVisitNote.id) == target.id)
            .values(
                superseded_at=None,
                supersession_reason=None,
                restore_frontier_transcript_id=(
                    restore_frontier.id if restore_frontier is not None else None
                ),
                updated_at=target.updated_at,
            )
        )

        audit = AIVisitNoteVersionAudit(
            action="current_note_restored",
            operator_ref=operator,
            reason_code=reason.value,
            transition_sequence=plan.transition_sequence,
            effective_at=effective_at,
            appointment_ref_sha256=_note_ref(target.appointment_id),
            schema_ref_sha256=_note_ref(target.doctor_note_schema_id),
            prior_current_note_ref_sha256=plan.current_note_ref,
            restored_note_ref_sha256=plan.target_note_ref,
            restore_frontier_transcript_ref_sha256=(
                plan.restore_frontier_transcript_ref
            ),
            plan_sha256=plan.plan_sha256,
        )
        session.add(audit)
        session.commit()
        session.refresh(audit)
        return VisitNoteRestoreResult(
            plan_sha256=plan.plan_sha256,
            audit_id=audit.id,
            changed=True,
        )
