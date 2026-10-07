"""Discover reviewed connections and existing visits using current human access.

The account result is correspondence evidence, never a permission grant. Every
read resolves the verified platform identity and existing practice policy again.
No identity, membership, patient, appointment or capture binding is created.
"""

from datetime import UTC, datetime, time
from typing import Any, cast

from app.configuration.authority import AuthorityKernelSettings
from app.configuration.companion_encounters import CompanionEncounterSettings
from app.services import authority
from app.services.companion_encounter_contract import (
    PAGE_SIZE,
    ConnectedRequest,
    ConnectionContent,
    ConnectionRequest,
    ConnectionSnapshot,
    DiscoveryRequest,
    DiscoveryResponse,
    EncounterContent,
    EncounterCursor,
    EncounterRequest,
    EncounterSnapshot,
    EncountersRequest,
    digest,
)
from app.services.companion_record_contract import RecordAppointment, RecordPatient
from app.services.companion_records import (
    RecordBudget,
    RecordUnavailable,
    check_deadline,
    transaction_budget,
)
from app.services.doctor_access import doctor_access_in_force
from app.services.doctor_access_row_ownership import lock_doctor_access_rows
from app.services.view_as import ViewAsActorIdentity, actor_bound_unended_view_as_exists

from activemodel.session_manager import global_session
from app.models.appointment import Appointment
from app.models.authority import AuthorityPerson, AuthorityPersonBinding
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.practice import Practice, PracticeMember
from app.models.scribe_capture_binding import ScribeCaptureProviderLink
from sqlalchemy import and_, func, or_
from sqlmodel import Session, col, select


def _provider(session, *, issuer, subject, session_id, tenant_ref, settings):
    authority._ensure_enabled(settings)
    if issuer != settings.clerk_issuer:
        raise authority.AuthorityRequestDeniedError
    # Follow current Core exchange/offboarding: Practice before Doctor, then
    # view-as and authority rows. Recheck a practice move after waiting.
    owner = session.exec(
        select(Doctor)
        .where(Doctor.clerk_id == subject)
        .execution_options(populate_existing=True)
    ).one_or_none()
    member = (
        session.exec(
            select(PracticeMember)
            .where(PracticeMember.doctor_id == owner.id)
            .execution_options(populate_existing=True)
        ).one_or_none()
        if owner is not None
        else None
    )
    if owner is None or member is None:
        raise authority.AuthorityRequestDeniedError
    practice_id, doctor_id = member.practice_id, owner.id
    with lock_doctor_access_rows(
        session,
        practice_id=practice_id,
        doctor_id=doctor_id,
        subject=subject,
    ) as locked:
        if locked is None:
            raise authority.AuthorityRequestDeniedError
        practice, owner, member, ownership = locked
        # Use the existing observational guard. Reconciliation may commit stale
        # support-session cleanup, which is outside a discovery/read transaction.
        if actor_bound_unended_view_as_exists(
            session,
            actor=owner,
            identity=ViewAsActorIdentity(
                issuer=issuer, subject=subject, clerk_session_id=session_id
            ),
        ):
            raise authority.AuthorityRequestDeniedError
        actor, identity = authority._actor_for_clerk_identity(session, issuer, subject)
        if identity.verified_at is None:
            raise authority.AuthorityRequestDeniedError
        link = session.exec(
            select(ScribeCaptureProviderLink).where(
                ScribeCaptureProviderLink.actor_id == actor.id,
                ScribeCaptureProviderLink.tenant_id == tenant_ref,
                col(ScribeCaptureProviderLink.revoked_at).is_(None),
            )
        ).one_or_none()
        if (
            link is None
            or link.source_identity_id != identity.id
            or link.reviewed_at is None
            or len(link.review_ref) < 16
        ):
            raise authority.AuthorityRequestDeniedError
        authority._active_tenant(session, link.tenant_id)
        if (
            authority._active_provider_membership(
                session,
                actor_id=actor.id,
                tenant_id=link.tenant_id,
            )
            is None
        ):
            raise authority.AuthorityRequestDeniedError
        doctor = session.get(Doctor, link.doctor_id)
        member = session.exec(
            select(PracticeMember).where(
                PracticeMember.doctor_id == link.doctor_id,
            )
        ).one_or_none()
        practice = session.exec(
            select(col(Practice.id), col(Practice.legal_name)).where(
                Practice.id == link.practice_id,
                func.char_length(col(Practice.legal_name)).between(1, 200),
            )
        ).one_or_none()
        if (
            doctor is None
            or doctor.deleted_at is not None
            or doctor.clerk_id != subject
            or member is None
            or member.practice_id != link.practice_id
            or practice is None
            or not doctor_access_in_force(
                doctor,
                _ownership=ownership,
                _practice_id=link.practice_id,
            )
        ):
            raise authority.AuthorityRequestDeniedError
        content = ConnectionContent(
            actor_ref=str(actor.id),
            tenant_ref=str(link.tenant_id),
            provider_link_ref=str(link.id),
            practice_ref=str(practice.id),
            practice_label=practice.legal_name,
            review_sha256=digest(
                {
                    "provider_link_ref": str(link.id),
                    "identity_ref": str(identity.id),
                    "doctor_ref": str(doctor.id),
                    "reviewed_at": link.reviewed_at.isoformat(),
                    "review_ref": link.review_ref,
                }
            ),
        )
        data = content.model_dump(mode="json")
        return ConnectionSnapshot(**data, connection_sha256=digest(data)), doctor.id


def _encounters(
    session,
    request,
    connection,
    doctor_id,
    *,
    issuer,
    subject,
    settings,
    require_source_match=True,
):
    if isinstance(request, ConnectionRequest):
        return (), None
    if (
        connection.actor_ref != request.expected_actor_ref
        or connection.connection_sha256 != request.connection_sha256
    ):
        raise authority.AuthorityRequestDeniedError
    # UUID-backed TypeIDs cannot be joined to opaque binding_ref text by a SQL
    # cast. Scan at most 100 identifiers, then resolve each existing authority
    # binding before reading its patient label. No clinical previews are loaded.
    scan_limit = 100
    query = select(
        col(Appointment.id),
        col(Appointment.patient_id),
        col(Appointment.appointment_date),
    ).where(Appointment.doctor_id == doctor_id)
    if isinstance(request, EncounterRequest):
        query = query.where(Appointment.id == request.appointment_ref)
    else:
        # The existing Core column stores wall-clock timestamps without a zone.
        # Filtering uses those source calendar dates, never a browser UTC guess.
        query = query.where(
            Appointment.appointment_date
            >= datetime.combine(request.window.start_date, time.min),
            Appointment.appointment_date
            < datetime.combine(request.window.end_date, time.min),
        )
        if request.cursor is not None:
            if request.cursor.appointment_date.utcoffset() is not None:
                raise RecordUnavailable
            query = query.where(
                or_(
                    Appointment.appointment_date > request.cursor.appointment_date,
                    and_(
                        Appointment.appointment_date == request.cursor.appointment_date,
                        col(Appointment.id) > request.cursor.appointment_ref,
                    ),
                )
            )
    rows = session.exec(
        query.order_by(
            col(Appointment.appointment_date),
            col(Appointment.id),
        )
        .limit(scan_limit + 1)
        .with_for_update(
            read=True,
            of=Appointment,
        )
    ).all()
    if isinstance(request, EncounterRequest) and len(rows) != 1:
        raise authority.AuthorityRequestDeniedError
    result = []
    scanned = 0
    for row in rows[:scan_limit]:
        scanned += 1
        eligible = session.exec(
            select(col(AuthorityPersonBinding.id))
            .join(
                AuthorityPerson,
                col(AuthorityPerson.id) == col(AuthorityPersonBinding.person_id),
            )
            .where(
                AuthorityPersonBinding.binding_ref == str(row.id),
                AuthorityPersonBinding.binding_kind == "provider_record",
                AuthorityPersonBinding.tenant_id == connection.tenant_ref,
                AuthorityPersonBinding.status == "active",
                col(AuthorityPersonBinding.verified_at).is_not(None),
                col(AuthorityPersonBinding.revoked_at).is_(None),
                or_(
                    col(AuthorityPersonBinding.actor_id).is_(None),
                    AuthorityPersonBinding.actor_id == connection.actor_ref,
                ),
                AuthorityPerson.status == "active",
            )
            .with_for_update(
                read=True, of=cast(Any, (AuthorityPersonBinding, AuthorityPerson))
            )
        ).one_or_none()
        if eligible is None:
            if isinstance(request, EncounterRequest):
                raise authority.AuthorityRequestDeniedError
            continue
        # Reuse the authority kernel's public provider-record resolver. The
        # narrower query above additionally binds the reviewed clinical doctor.
        actor_ref = authority.provider_record_actor_ref(
            clerk_issuer=issuer,
            clerk_subject=subject,
            record_ref=str(row.id),
            session=session,
            settings=settings,
        )
        if actor_ref != connection.actor_ref:
            raise authority.AuthorityRequestDeniedError
        patient = session.exec(
            select(Patient.name)
            .where(
                Patient.id == row.patient_id,
                Patient.doctor_id == doctor_id,
                func.char_length(col(Patient.name)).between(1, 200),
            )
            .with_for_update(read=True, of=Patient)
        ).one_or_none()
        if patient is None:
            raise authority.AuthorityRequestDeniedError
        content = EncounterContent(
            connection_sha256=connection.connection_sha256,
            patient=RecordPatient(
                patient_ref=str(row.patient_id), display_name=patient
            ),
            appointment=RecordAppointment(
                appointment_ref=str(row.id),
                patient_ref=str(row.patient_id),
                appointment_date=row.appointment_date,
                timezone_known=row.appointment_date.utcoffset() is not None,
            ),
        )
        data = content.model_dump(mode="json")
        result.append(EncounterSnapshot(**data, source_sha256=digest(data)))
        if len(result) == PAGE_SIZE:
            break
    if (
        isinstance(request, EncounterRequest)
        and require_source_match
        and result[0].source_sha256 != request.source_sha256
    ):
        raise RecordUnavailable
    cursor = None
    if isinstance(request, EncountersRequest) and len(rows) > scanned:
        last = rows[scanned - 1]
        cursor = EncounterCursor(
            window_sha256=digest(request.window.model_dump(mode="json")),
            connection_sha256=connection.connection_sha256,
            appointment_date=last.appointment_date,
            appointment_ref=str(last.id),
        )
    return tuple(result), cursor


def read_discovery(
    *,
    issuer: str,
    subject: str,
    session_id: str,
    request: DiscoveryRequest,
    settings: CompanionEncounterSettings,
    authority_settings: AuthorityKernelSettings,
    deadline: float | RecordBudget,
) -> DiscoveryResponse:
    if not settings.enabled:
        raise RecordUnavailable
    check_deadline(deadline)
    with global_session() as managed:
        session = cast(Session, managed)
        try:
            with session.no_autoflush, transaction_budget(session, deadline):
                arguments = dict(
                    issuer=issuer,
                    subject=subject,
                    session_id=session_id,
                    tenant_ref=request.expected_tenant_ref,
                    settings=authority_settings,
                )
                connection, doctor_id = _provider(session, **arguments)
                if isinstance(request, ConnectedRequest) and (
                    request.expected_actor_ref != connection.actor_ref
                    or request.connection_sha256 != connection.connection_sha256
                ):
                    raise authority.AuthorityRequestDeniedError
                records, cursor = _encounters(
                    session,
                    request,
                    connection,
                    doctor_id,
                    issuer=issuer,
                    subject=subject,
                    settings=authority_settings,
                )
                if session.new or session.dirty or session.deleted:
                    raise RecordUnavailable
                session.expire_all()
                if _provider(session, **arguments) != (connection, doctor_id):
                    raise authority.AuthorityRequestDeniedError
                if session.new or session.dirty or session.deleted:
                    raise RecordUnavailable
                check_deadline(deadline)
                return DiscoveryResponse(
                    action=request.action,
                    request_nonce=request.request_nonce,
                    checked_at=datetime.now(UTC),
                    connection=connection,
                    encounters=records,
                    next_cursor=cursor,
                )
        finally:
            session.rollback()
