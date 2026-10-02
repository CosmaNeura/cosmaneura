"""Current-source Core preparation only; no transport, CAB login or inference."""

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from app.configuration import billing_header as configuration
from app.configuration.clerk import VerifiedProviderSession
from app.services.billing_header_contract import (
    BillingHeaderOperation,
    BillingHeaderSource,
    BillingSource,
    PrepareBillingHeaderOperation,
    PreparedSummary,
    digest,
    project_note,
)
from app.services.doctor_access import doctor_access_in_force

from activemodel.session_manager import get_session
from app.models.ai_visit_note import AIVisitNote
from app.models.appointment import Appointment
from app.models.billing_header_operation import (
    BillingHeaderPreparedOperation as Operation,
)
from app.models.doctor import Doctor
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.patient import Patient
from app.models.practice import (
    SEAT_HOLDING_ACCESS_STATES,
    PracticeMember,
    lock_practice_seats,
    member_access_state,
)
from sqlmodel import func, select


class BillingPreparationError(ValueError):
    def __init__(self, code="billing_source_unavailable", status=404):
        self.code, self.status = code, status
        super().__init__(code)


def _require(ok, code="billing_source_unavailable", status=404):
    if not ok:
        raise BillingPreparationError(code, status)


def _scope(session, actor, note_id):
    _require(isinstance(actor, VerifiedProviderSession))
    doctor = session.exec(
        select(Doctor).where(Doctor.clerk_id == actor.subject)
    ).one_or_none()
    _require(doctor is not None)
    member = session.exec(
        select(PracticeMember).where(PracticeMember.doctor_id == doctor.id)
    ).one_or_none()
    _require(member is not None)
    practice = lock_practice_seats(session, member.practice_id)
    _require(practice is not None)
    doctor = session.exec(
        select(Doctor)
        .where(Doctor.id == doctor.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    member = session.exec(
        select(PracticeMember)
        .where(PracticeMember.doctor_id == doctor.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    _require(
        doctor.clerk_id == actor.subject
        and doctor.deleted_at is None
        and member is not None
        and member.practice_id == practice.id
        and member.role in {"owner", "workforce"}
        and member_access_state(member, at=datetime.now(UTC))
        in SEAT_HOLDING_ACCESS_STATES
        and doctor_access_in_force(doctor)
    )
    # Select source columns, not an ORM entity: capability reads must never
    # refresh/mutate the note snapshot already being returned by note_detail.
    note_query = select(
        AIVisitNote.id,
        AIVisitNote.doctor_id,
        AIVisitNote.appointment_id,
        AIVisitNote.doctor_note_schema_id,
        AIVisitNote.superseded_at,
        AIVisitNote.note,
    )
    note = session.exec(note_query.where(AIVisitNote.id == note_id)).one_or_none()
    _require(note is not None and note.doctor_id == doctor.id)
    appointment = session.exec(
        select(Appointment)
        .where(Appointment.id == note.appointment_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    _require(appointment is not None and appointment.doctor_id == doctor.id)
    patient = session.exec(
        select(Patient)
        .where(Patient.id == appointment.patient_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    note = session.exec(
        note_query.where(AIVisitNote.id == note_id).with_for_update()
    ).one()
    schema = session.get(DoctorNoteSchema, note.doctor_note_schema_id)
    _require(
        patient is not None
        and patient.doctor_id == doctor.id
        and note.doctor_id == doctor.id
        and note.appointment_id == appointment.id
        and schema is not None
        and schema.doctor_id == doctor.id
    )
    _require(note.superseded_at is None, "billing_source_changed", 409)
    try:
        original, projected, content_sha, excluded = project_note(note.note)
    except ValueError:
        raise BillingPreparationError() from None
    source = BillingSource(
        noteId=str(note.id),
        appointmentId=str(appointment.id),
        noteSchemaId=str(note.doctor_note_schema_id),
        contentSha256=content_sha,
        textSha256=hashlib.sha256(projected.encode()).hexdigest(),
        excludedRecordingTime=excluded,
    )
    return doctor, practice, appointment, patient, note, source, original, projected


def _admission(scope, actor):
    policy = configuration.BILLING_SOURCE_POLICY
    if not policy.enabled or actor.issuer != policy.clerkIssuer:
        return None
    doctor, practice, appointment, patient, note, source, _, _ = scope
    for row in policy.admissions:
        if (
            row.practiceId == str(practice.id)
            and row.doctorId == str(doctor.id)
            and row.appointmentId == str(appointment.id)
            and row.patientId == str(patient.id)
            and row.noteId == str(note.id)
            and row.contentSha256 == source.contentSha256
            and datetime.now(UTC)
            < datetime.fromisoformat(row.expiresAt.replace("Z", "+00:00"))
        ):
            return row
    return None


def _receipt(row, source, *, replayed):
    from app.services.cab_bridge_client import configured

    return BillingHeaderOperation(
        transport={"state": "configured" if configured() else "not_configured"},
        operationId=str(row.operation_id),
        sourceRevision=row.source_revision,
        source=source,
        completedAt=row.completed_at,
        replayed=replayed,
    )


def read_source(actor, note_id, *, displayed_source=None):
    with get_session() as session:
        scope = _scope(session, actor, note_id)
        source = scope[5]
        if displayed_source is not None:
            _require(
                source.model_dump() == displayed_source, "billing_source_changed", 409
            )
        admitted = _admission(scope, actor) is not None
        latest = session.exec(
            select(Operation)
            .where(
                Operation.note_id == scope[4].id,
                Operation.doctor_id == scope[0].id,
                Operation.practice_id == scope[1].id,
                Operation.patient_id == scope[3].id,
            )
            .order_by(Operation.source_revision.desc())
            .limit(1)
        ).first()
        if latest is not None and (
            latest.source != source.model_dump()
            or latest.actor.get("issuer") != actor.issuer
            or latest.actor.get("subject") != actor.subject
        ):
            latest = None
        result = BillingHeaderSource(
            state="review_required" if admitted else "not_configured",
            source=source,
            latestOperation=PreparedSummary(
                operationId=str(latest.operation_id),
                sourceRevision=latest.source_revision,
                createdAt=latest.completed_at,
            )
            if latest
            else None,
        )
        return result


def prepare_operation(actor, note_id, command: PrepareBillingHeaderOperation):
    with get_session() as session:
        scope = _scope(session, actor, note_id)
        doctor, practice, appointment, patient, note, source, original, projected = (
            scope
        )
        _require(
            source.contentSha256 == command.expectedContentSha256
            and source.textSha256 == command.expectedTextSha256,
            "billing_source_changed",
            409,
        )
        request_sha = digest(
            {
                "noteId": str(note.id),
                "actorIssuer": actor.issuer,
                "actorSubject": actor.subject,
                "practiceId": str(practice.id),
                "command": command.model_dump(exclude_unset=True),
            }
        )
        existing = session.exec(
            select(Operation).where(Operation.operation_id == UUID(command.operationId))
        ).one_or_none()
        if existing is not None:
            _require(
                existing.doctor_id == doctor.id
                and existing.practice_id == practice.id
                and existing.patient_id == patient.id
                and existing.appointment_id == appointment.id
                and existing.request_sha256 == request_sha
                and existing.source == source.model_dump(),
                "billing_operation_conflict",
                409,
            )
            return _receipt(existing, source, replayed=True)
        admission = _admission(scope, actor)
        _require(admission is not None, "billing_source_not_configured", 403)
        last = session.exec(
            select(func.max(Operation.source_revision)).where(
                Operation.appointment_id == appointment.id,
                Operation.schema_ref == str(note.doctor_note_schema_id),
            )
        ).one()
        row = Operation(
            operation_id=UUID(command.operationId),
            doctor_id=doctor.id,
            practice_id=practice.id,
            appointment_id=appointment.id,
            patient_id=patient.id,
            note_id=note.id,
            schema_ref=str(note.doctor_note_schema_id),
            source_revision=(last or 0) + 1,
            request_sha256=request_sha,
            actor={
                "issuer": actor.issuer,
                "subject": actor.subject,
                "sessionSha256": actor.session_sha256,
                "role": member_role(session, doctor.id),
                "purpose": "billing-header.prepare",
                "confirmedComplete": True,
            },
            source=source.model_dump(),
            ordered_content=json.loads(original),
            projected_text=projected,
            visit=command.visit.model_dump(exclude_unset=True),
            admission=admission.model_dump(),
            completed_at=datetime.now(UTC),
        )
        session.add(row)
        session.flush()
        _require(
            _admission(scope, actor) == admission, "billing_source_not_configured", 403
        )
        result = _receipt(row, source, replayed=False)
        session.commit()
        return result


def member_role(session, doctor_id):
    return session.exec(
        select(PracticeMember.role).where(PracticeMember.doctor_id == doctor_id)
    ).one()


def read_operation(actor, operation_id):
    with get_session() as session:
        row = session.exec(
            select(Operation).where(Operation.operation_id == operation_id)
        ).one_or_none()
        _require(
            row is not None
            and row.actor.get("issuer") == actor.issuer
            and row.actor.get("subject") == actor.subject
        )
        scope = _scope(session, actor, row.note_id)
        _require(
            row.doctor_id == scope[0].id
            and row.practice_id == scope[1].id
            and row.patient_id == scope[3].id
        )
        _require(row.source == scope[5].model_dump(), "billing_source_changed", 409)
        return _receipt(row, scope[5], replayed=True)


def admitted_source_map(actor, notes):
    policy = configuration.BILLING_SOURCE_POLICY
    if not policy.enabled or actor.issuer != policy.clerkIssuer:
        return {}
    allowed = {a.noteId for a in policy.admissions}
    result = {}
    # Compare the exact displayed ordered content with the current locked source.
    snapshots = []
    for note in notes:
        if str(note.id) not in allowed:
            continue
        try:
            _, projected, content_sha, excluded = project_note(note.note)
            snapshots.append(
                (
                    note.id,
                    BillingSource(
                        noteId=str(note.id),
                        appointmentId=str(note.appointment_id),
                        noteSchemaId=str(note.doctor_note_schema_id),
                        contentSha256=content_sha,
                        textSha256=hashlib.sha256(projected.encode()).hexdigest(),
                        excludedRecordingTime=excluded,
                    ).model_dump(),
                )
            )
        except ValueError:
            continue
    for note_id, snapshot in snapshots:
        try:
            response = read_source(actor, note_id, displayed_source=snapshot)
            if response.state == "review_required":
                result[str(note_id)] = response
        except BillingPreparationError:
            continue
    return result
