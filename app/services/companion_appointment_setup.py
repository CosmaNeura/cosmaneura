"""Connect an exact reviewed batch of existing source appointments atomically."""

from datetime import UTC, datetime, time

from app.services import authority
from app.services import companion_appointment_bindings as bindings
from app.services.companion_appointment_setup_contract import (
    AppointmentSetupRequest,
    AppointmentSetupResponse,
)
from app.services.companion_encounter_contract import PAGE_SIZE, EncounterCursor, digest
from app.services.companion_encounters import _provider
from app.services.companion_records import (
    RecordUnavailable,
    check_deadline,
    transaction_budget,
)

from activemodel.session_manager import global_session
from app.models.appointment import Appointment
from app.models.authority import (
    AuthorityActor,
    AuthorityActorExternalIdentity,
    AuthorityPerson,
    AuthorityPersonBinding,
    AuthorityPersonOrigin,
    AuthorityTenant,
    AuthorityTenantMembership,
)
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.practice import Practice, PracticeMember
from app.models.scribe_capture_binding import ScribeCaptureProviderLink
from sqlalchemy import and_, event, or_
from sqlmodel import col, select


class AppointmentSetupChanged(RecordUnavailable):
    pass


def _lock(session, model, identifier):
    row = session.exec(
        select(model)
        .where(model.id == identifier)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if row is None:
        raise authority.AuthorityRequestDeniedError
    return row


def _current_provider(session, args):
    connection, doctor_id = _provider(session, **args)
    # The provider resolver has locked Practice before Doctor, matching capture.
    # The Doctor lock also serializes ordinary appointment/provider-link inserts
    # through their FK locks and the existing staging provisioning command.
    _lock(session, Doctor, doctor_id)
    _lock(session, AuthorityActor, connection.actor_ref)
    _lock(session, AuthorityTenant, connection.tenant_ref)
    links = list(
        session.exec(
            select(ScribeCaptureProviderLink)
            .where(
                ScribeCaptureProviderLink.doctor_id == doctor_id,
                col(ScribeCaptureProviderLink.revoked_at).is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    if len(links) != 1 or str(links[0].id) != connection.provider_link_ref:
        raise authority.AuthorityRequestDeniedError
    link = links[0]
    _lock(session, AuthorityActorExternalIdentity, link.source_identity_id)
    _lock(session, Practice, link.practice_id)
    session.exec(
        select(AuthorityTenantMembership)
        .where(
            AuthorityTenantMembership.actor_id == link.actor_id,
            AuthorityTenantMembership.tenant_id == link.tenant_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    session.exec(
        select(PracticeMember)
        .where(PracticeMember.doctor_id == doctor_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    session.expire_all()
    current, current_doctor = _provider(session, **args)
    if current != connection or current_doctor != doctor_id:
        raise AppointmentSetupChanged
    return connection, doctor_id, link


def _batch(session, request, doctor_id, connection):
    query = select(Appointment).where(
        Appointment.doctor_id == doctor_id,
        Appointment.appointment_date
        >= datetime.combine(request.window.start_date, time.min),
        Appointment.appointment_date
        < datetime.combine(request.window.end_date, time.min),
    )
    if request.cursor is not None:
        query = query.where(
            or_(
                Appointment.appointment_date > request.cursor.appointment_date,
                and_(
                    Appointment.appointment_date == request.cursor.appointment_date,
                    col(Appointment.id) > request.cursor.appointment_ref,
                ),
            )
        )
    rows = list(
        session.exec(
            query.order_by(col(Appointment.appointment_date), col(Appointment.id))
            .limit(PAGE_SIZE + 1)
            .with_for_update(read=True)
        ).all()
    )
    page = rows[:PAGE_SIZE]
    next_cursor = None
    if len(rows) > PAGE_SIZE:
        last = page[-1]
        next_cursor = EncounterCursor(
            window_sha256=digest(request.window.model_dump(mode="json")),
            connection_sha256=connection.connection_sha256,
            appointment_date=last.appointment_date,
            appointment_ref=str(last.id),
        )
    patients = (
        {
            str(p.id): p
            for p in session.exec(
                select(Patient)
                .where(
                    col(Patient.id).in_([row.patient_id for row in page]),
                )
                .order_by(col(Patient.id))
                .with_for_update(key_share=True)
            ).all()
        }
        if page
        else {}
    )
    return page, patients, next_cursor


def _inspect(session, appointment, patient, link):
    base = dict(
        appointment_ref=str(appointment.id),
        patient_ref=str(appointment.patient_id),
        appointment_date=appointment.appointment_date.isoformat(),
    )
    if patient is None or patient.doctor_id != appointment.doctor_id:
        return base | {"state": "review", "reason": "patient_scope_unavailable"}
    existing = session.exec(
        select(AuthorityPersonBinding)
        .where(
            AuthorityPersonBinding.binding_ref == str(appointment.id),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    people = set(
        session.exec(
            select(col(AuthorityPersonOrigin.person_id))
            .where(
                AuthorityPersonOrigin.origin == "provider_entry",
                AuthorityPersonOrigin.source_ref == str(patient.id),
            )
            .distinct()
            .limit(2)
        ).all()
    )
    if existing is not None:
        people.add(existing.person_id)
    # Prevent concurrent status changes and new FK-linked cross-tenant bindings
    # while the reviewed canonical person is being reused.
    for person_id in sorted(people, key=str):
        _lock(session, AuthorityPerson, person_id)
    try:
        person_id = bindings._existing_person(session, patient, link.tenant_id)
    except bindings.DeferredBinding as error:
        return base | {"state": "review", "reason": str(error)}
    if existing is not None:
        active = (
            person_id is not None
            and existing.person_id == person_id
            and existing.tenant_id == link.tenant_id
            and existing.actor_id in (None, link.actor_id)
            and existing.binding_kind == "provider_record"
            and existing.status == "active"
            and existing.verified_at is not None
            and existing.revoked_at is None
        )
        return base | {
            "state": "connected" if active else "review",
            "binding_ref": str(existing.id),
            "person_ref": str(existing.person_id),
        }
    return base | {
        "state": "eligible",
        "person_ref": str(person_id) if person_id is not None else None,
    }


def setup_appointments(
    *,
    issuer,
    subject,
    session_id,
    request: AppointmentSetupRequest,
    settings_provider,
    authority_settings,
    deadline,
):
    def available():
        check_deadline(deadline)
        if not settings_provider().enabled:
            raise RecordUnavailable

    available()
    args = dict(
        issuer=issuer,
        subject=subject,
        session_id=session_id,
        tenant_ref=request.expected_tenant_ref,
        settings=authority_settings,
    )
    with global_session() as session:
        try:
            with transaction_budget(session, deadline):
                connection, doctor_id, link = _current_provider(session, args)
                if (
                    connection.actor_ref != request.expected_actor_ref
                    or connection.connection_sha256 != request.connection_sha256
                ):
                    raise authority.AuthorityRequestDeniedError
                page, patients, next_cursor = _batch(
                    session, request, doctor_id, connection
                )
                entries = [
                    _inspect(session, row, patients.get(str(row.patient_id)), link)
                    for row in page
                ]
                plan = digest(
                    dict(
                        version=1,
                        connection_sha256=connection.connection_sha256,
                        window=request.window.model_dump(mode="json"),
                        cursor=request.cursor.model_dump(mode="json")
                        if request.cursor
                        else None,
                        entries=entries,
                        next_cursor=next_cursor.model_dump(mode="json")
                        if next_cursor
                        else None,
                    )
                )
                counts = {
                    state: sum(e["state"] == state for e in entries)
                    for state in ("eligible", "connected", "review")
                }
                if request.action == "connect":
                    if request.plan_sha256 != plan:
                        raise AppointmentSetupChanged
                    eligible = [
                        row
                        for row, entry in zip(page, entries, strict=True)
                        if entry["state"] == "eligible"
                    ]
                    for row in sorted(
                        eligible, key=lambda a: (str(a.patient_id), str(a.id))
                    ):
                        available()
                        bindings._bind(session, row, setup_plan_sha256=plan)
                    if any(
                        _inspect(session, row, patients.get(str(row.patient_id)), link)[
                            "state"
                        ]
                        != "connected"
                        for row in eligible
                    ):
                        raise AppointmentSetupChanged
                    session.expire_all()
                    current, _ = _provider(session, **args)
                    if current != connection:
                        raise AppointmentSetupChanged
                    # Guard the physical commit as well as each SQL statement.
                    db = session.connection()

                    def committing(_connection):
                        available()

                    event.listen(db, "commit", committing)
                    try:
                        available()
                        session.commit()
                    finally:
                        event.remove(db, "commit", committing)
                available()
                return AppointmentSetupResponse(
                    action=request.action,
                    request_nonce=request.request_nonce,
                    checked_at=datetime.now(UTC),
                    connection=connection,
                    window=request.window,
                    plan_sha256=plan,
                    scanned_count=len(page),
                    eligible_count=counts["eligible"],
                    already_connected_count=counts["connected"],
                    review_required_count=counts["review"],
                    connected_now_count=counts["eligible"]
                    if request.action == "connect"
                    else 0,
                    next_cursor=next_cursor,
                )
        finally:
            session.rollback()
