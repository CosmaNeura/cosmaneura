"""Create Core visits atomically, then use the existing Scribe consent/capture path."""

from contextlib import nullcontext
from datetime import UTC, date, datetime, time, timedelta
from uuid import uuid4

from typeid import TypeID

from app.services import authority, provider_record_authority
from app.services.companion_appointment_setup import _current_provider
from app.services.companion_encounter_contract import digest
from app.services.companion_encounters import _locked_provider
from app.services.companion_records import (
    RecordUnavailable,
    check_deadline,
    transaction_budget,
)
from app.services.doctor_access_row_ownership import lookup_doctor_access_row_scope
from app.services.scribe_encounter_contract import (
    AddBirthDateRequest,
    CatalogRequest,
    CreateRequest,
    ExistingPatient,
    LookupRequest,
    NewPatient,
    PatientsRequest,
)
from app.services.scribe_encounter_status import saved_encounter_statuses

from activemodel.session_manager import global_session
from app.models.appointment import Appointment
from app.models.authority import (
    AuthorityAuditEvent,
    AuthorityPersonBinding,
    AuthorityTenantMembership,
)
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.provider_record_authority import ProviderPracticeAuthority
from app.models.scribe_capture_binding import ScribeCaptureBinding
from app.models.scribe_encounter_creation import ScribeEncounterCreation
from sqlalchemy import event
from sqlmodel import col, select


class EncounterConflict(RecordUnavailable):
    """The retained creation key was already used for different visit details."""


def _visit(session, row, *, doctor_id, link, subject, saved_statuses=None):
    if row.doctor_id != doctor_id or row.provider_link_id != link.id:
        raise authority.AuthorityRequestDeniedError
    appointment = session.get(Appointment, row.appointment_id)
    patient = session.get(Patient, row.patient_id)
    if (
        appointment is None
        or patient is None
        or appointment.doctor_id != doctor_id
        or patient.doctor_id != doctor_id
        or appointment.patient_id != patient.id
    ):
        raise authority.AuthorityRequestDeniedError
    binding = session.exec(
        select(AuthorityPersonBinding).where(
            AuthorityPersonBinding.binding_ref == str(appointment.id),
        )
    ).one_or_none()
    if (
        binding is None
        or binding.actor_id != link.actor_id
        or binding.tenant_id != link.tenant_id
        or binding.status != "active"
        or binding.revoked_at is not None
        or binding.verified_at is None
    ):
        raise authority.AuthorityRequestDeniedError
    if (
        provider_record_authority.validate_provider_record_scope(
            session=session,
            binding=binding,
            actor_id=link.actor_id,
            clerk_subject=subject,
        )
        is None
    ):
        raise authority.AuthorityRequestDeniedError
    capture = session.exec(
        select(ScribeCaptureBinding).where(
            ScribeCaptureBinding.appointment_id == appointment.id,
            ScribeCaptureBinding.actor_id == link.actor_id,
        )
    ).one_or_none()
    if capture is not None and (
        capture.revoked_at is not None
        or capture.workspace_id != row.workspace_id
        or capture.provider_link_id != link.id
    ):
        raise authority.AuthorityRequestDeniedError
    if saved_statuses is None:
        saved_statuses = saved_encounter_statuses(session, doctor_id, [appointment.id])
    status = saved_statuses.get(str(appointment.id))
    return {
        "workspace_id": str(row.workspace_id),
        "appointment_ref": str(appointment.id),
        "patient_ref": str(patient.id),
        "patient_name": patient.name,
        "date_of_birth": patient.date_of_birth.date().isoformat()
        if patient.date_of_birth
        else None,
        "title": appointment.appointment_name or "Visit",
        "appointment_at": appointment.appointment_date.replace(tzinfo=UTC).isoformat(),
        **({"status": status} if status else {}),
    }


def _create(session, request, doctor_id, link, issuer, subject):
    request_digest = digest(request.model_dump(mode="json", exclude={"request_nonce"}))
    # _current_provider holds the Doctor lock before this lookup. Concurrent
    # submissions serialize, including two requests with the same creation key.
    row = session.exec(
        select(ScribeEncounterCreation).where(
            ScribeEncounterCreation.doctor_id == doctor_id,
            ScribeEncounterCreation.creation_key == request.creation_key,
        )
    ).one_or_none()
    if row is not None:
        if row.request_sha256 != request_digest:
            raise EncounterConflict
        return row, False
    mapping = session.exec(
        select(ProviderPracticeAuthority).where(
            ProviderPracticeAuthority.practice_id == link.practice_id,
        )
    ).one_or_none()
    memberships = list(
        session.exec(
            select(AuthorityTenantMembership).where(
                AuthorityTenantMembership.actor_id == link.actor_id,
                AuthorityTenantMembership.status == "active",
            )
        )
    )
    # The reused source-binding service can reconcile historical practice moves.
    # New encounter creation must never perform that administrative operation.
    if (
        mapping is None
        or mapping.tenant_id != link.tenant_id
        or len(memberships) != 1
        or memberships[0].tenant_id != link.tenant_id
        or memberships[0].membership_kind != "provider"
    ):
        raise authority.AuthorityRequestDeniedError
    now = datetime.now(UTC)
    if (
        not now - timedelta(days=62)
        <= request.appointment_at
        <= now + timedelta(days=366)
    ):
        raise ValueError("visit_date_out_of_range")
    if isinstance(request.patient, ExistingPatient):
        patient = session.exec(
            select(Patient)
            .where(
                Patient.id == TypeID.from_string(request.patient.patient_ref),
                Patient.doctor_id == doctor_id,
            )
            .with_for_update()
        ).one_or_none()
        if patient is None:
            raise authority.AuthorityRequestDeniedError
    elif isinstance(request.patient, NewPatient):
        birth_date = request.patient.date_of_birth
        if birth_date is not None and not date(1900, 1, 1) <= birth_date <= now.date():
            raise ValueError("birth_date_out_of_range")
        patient = Patient(
            doctor_id=doctor_id,
            name=request.patient.name,
            date_of_birth=(
                datetime.combine(birth_date, time.min)
                if birth_date is not None
                else None
            ),
        )
        session.add(patient)
        session.flush()
    else:
        raise ValueError("patient_selection_required")
    appointment = Appointment(
        doctor_id=doctor_id,
        patient_id=patient.id,
        appointment_date=request.appointment_at.astimezone(UTC).replace(tzinfo=None),
        appointment_name=request.title,
        created_by_system="scribe_provider_entry",
        external_id=None,
    )
    session.add(appointment)
    session.flush()
    # Reuse Core's normal provider-entry canonical person/record service. It
    # accepts exact source IDs and current provider policy, never name matching.
    provider_record_authority.ensure_provider_record_scope(
        session=session,
        clerk_issuer=issuer,
        clerk_subject=subject,
        record_ref=str(appointment.id),
    )
    row = ScribeEncounterCreation(
        doctor_id=doctor_id,
        provider_link_id=link.id,
        patient_id=patient.id,
        appointment_id=appointment.id,
        creation_key=request.creation_key,
        workspace_id=uuid4(),
        request_sha256=request_digest,
    )
    session.add(row)
    session.add(
        AuthorityAuditEvent(
            event_source="platform_authoritative",
            event_type="scribe.encounter.created",
            outcome="accepted",
            occurred_at=now,
            recorded_at=now,
            tenant_id=link.tenant_id,
            actor_id=link.actor_id,
            resource_type="appointment",
            resource_ref=str(appointment.id),
            request_digest=request_digest,
        )
    )
    session.flush()
    return row, True


def encounter_workflow(
    *,
    issuer,
    subject,
    session_id,
    request,
    settings_provider,
    authority_settings,
    deadline,
    include_lookup_status: bool = True,
):
    """Authorize each request; optionally omit unpublished lookup display status.

    The native facade uses this internal option for intermediate reauthorization
    only. Public lookup callers and catalog/create responses retain enrichment.
    """

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
        provider_transaction = None
        provider_locks_current = False

        def transaction_changed(*_args):
            nonlocal provider_locks_current
            provider_locks_current = False

        try:
            with (
                transaction_budget(session, deadline),
                lookup_doctor_access_row_scope(session)
                if isinstance(request, LookupRequest)
                else nullcontext(),
            ):
                connection, doctor_id, link = _current_provider(session, args)
                if isinstance(request, LookupRequest):
                    # The initial routine individually locks the full provider
                    # authority/link/member rows. Only this synchronous lookup
                    # may use their existing fresh final-row validator, in the
                    # same uninterrupted transaction. It re-reads permission;
                    # neither a decision nor a transaction spans remote I/O.
                    provider_transaction = (
                        session.get_nested_transaction() or session.get_transaction()
                    )
                    provider_locks_current = provider_transaction is not None
                    for name in ("after_transaction_create", "after_transaction_end"):
                        event.listen(session, name, transaction_changed)
                if connection.actor_ref != request.expected_actor_ref:
                    raise authority.AuthorityRequestDeniedError
                doctor = session.get(Doctor, doctor_id)
                if doctor is None:
                    raise authority.AuthorityRequestDeniedError
                result = {
                    "version": "1",
                    "action": request.action,
                    "request_nonce": str(request.request_nonce),
                    "account": {
                        "actor_ref": connection.actor_ref,
                        "tenant_ref": connection.tenant_ref,
                        "doctor_ref": str(doctor_id),
                        "email": doctor.email,
                    },
                }
                changed = False
                if isinstance(request, PatientsRequest):
                    query = select(Patient).where(Patient.doctor_id == doctor_id)
                    if request.query:
                        query = query.where(
                            col(Patient.name).icontains(request.query, autoescape=True)
                        )
                    patients = list(
                        session.exec(
                            query.order_by(col(Patient.name), col(Patient.id)).limit(
                                101
                            )
                        )
                    )
                    result.update(
                        patients=[
                            {
                                "patient_ref": str(p.id),
                                "name": p.name,
                                "date_of_birth": (
                                    p.date_of_birth.date().isoformat()
                                    if p.date_of_birth is not None
                                    else None
                                ),
                            }
                            for p in patients[:100]
                        ],
                        more_results=len(patients) > 100,
                    )
                elif isinstance(request, CatalogRequest):
                    rows = list(
                        session.exec(
                            select(ScribeEncounterCreation)
                            .where(
                                ScribeEncounterCreation.doctor_id == doctor_id,
                                ScribeEncounterCreation.provider_link_id == link.id,
                            )
                            .order_by(col(ScribeEncounterCreation.created_at).desc())
                            .limit(1001)
                        )
                    )
                    if len(rows) > 1000:
                        raise RecordUnavailable
                    statuses = saved_encounter_statuses(
                        session, doctor_id, [r.appointment_id for r in rows]
                    )
                    result["encounters"] = [
                        _visit(
                            session,
                            r,
                            doctor_id=doctor_id,
                            link=link,
                            subject=subject,
                            saved_statuses=statuses,
                        )
                        for r in rows
                    ]
                elif isinstance(request, LookupRequest):
                    row = session.exec(
                        select(ScribeEncounterCreation).where(
                            ScribeEncounterCreation.workspace_id
                            == request.workspace_id,
                            ScribeEncounterCreation.doctor_id == doctor_id,
                            ScribeEncounterCreation.provider_link_id == link.id,
                        )
                    ).one_or_none()
                    if row is None:
                        raise authority.AuthorityRequestDeniedError
                    result["encounter"] = _visit(
                        session,
                        row,
                        doctor_id=doctor_id,
                        link=link,
                        subject=subject,
                        saved_statuses=None if include_lookup_status else {},
                    )
                elif isinstance(request, AddBirthDateRequest):
                    row = session.exec(
                        select(ScribeEncounterCreation).where(
                            ScribeEncounterCreation.appointment_id
                            == TypeID.from_string(request.appointment_ref),
                            ScribeEncounterCreation.patient_id
                            == TypeID.from_string(request.patient_ref),
                            ScribeEncounterCreation.doctor_id == doctor_id,
                            ScribeEncounterCreation.provider_link_id == link.id,
                        )
                    ).one_or_none()
                    if row is None:
                        raise authority.AuthorityRequestDeniedError
                    _visit(
                        session, row, doctor_id=doctor_id, link=link, subject=subject
                    )
                    now = datetime.now(UTC)
                    if not date(1900, 1, 1) <= request.date_of_birth <= now.date():
                        raise ValueError("birth_date_out_of_range")
                    patient = session.exec(
                        select(Patient)
                        .where(Patient.id == row.patient_id)
                        .with_for_update()
                    ).one()
                    request_digest = digest(
                        request.model_dump(mode="json", exclude={"request_nonce"})
                    )
                    # A metadata-only audit receipt binds the retry key and exact
                    # request. Filling a missing DOB never overwrites a known DOB.
                    event_type = "scribe.patient.birth_date_added"
                    receipt_ref = "scribe_birth_date:" + str(request.change_key)
                    previous = session.exec(
                        select(AuthorityAuditEvent).where(
                            AuthorityAuditEvent.actor_id == link.actor_id,
                            AuthorityAuditEvent.tenant_id == link.tenant_id,
                            AuthorityAuditEvent.event_type == event_type,
                            AuthorityAuditEvent.resource_ref == receipt_ref,
                        )
                    ).one_or_none()
                    if (
                        previous is not None
                        and previous.request_digest != request_digest
                    ):
                        raise EncounterConflict
                    if (
                        patient.date_of_birth is not None
                        and patient.date_of_birth.date() != request.date_of_birth
                    ):
                        raise EncounterConflict
                    if previous is None:
                        patient.date_of_birth = datetime.combine(
                            request.date_of_birth, time.min
                        )
                        session.add(patient)
                        session.add(
                            AuthorityAuditEvent(
                                event_source="platform_authoritative",
                                event_type=event_type,
                                outcome="accepted",
                                occurred_at=now,
                                recorded_at=now,
                                tenant_id=link.tenant_id,
                                actor_id=link.actor_id,
                                resource_type="patient_demographic_update",
                                resource_ref=receipt_ref,
                                request_digest=request_digest,
                            )
                        )
                        session.flush()
                        changed = True
                    result["encounter"] = _visit(
                        session, row, doctor_id=doctor_id, link=link, subject=subject
                    )
                    result["change_key"] = str(request.change_key)
                elif isinstance(request, CreateRequest):
                    row, changed = _create(
                        session, request, doctor_id, link, issuer, subject
                    )
                    result["encounter"] = _visit(
                        session, row, doctor_id=doctor_id, link=link, subject=subject
                    )
                    result["creation_key"] = str(row.creation_key)
                else:
                    raise ValueError("unsupported_action")
                session.expire_all()
                if (
                    isinstance(request, LookupRequest)
                    and provider_locks_current
                    and provider_transaction is not None
                    and provider_transaction.is_active
                    and (session.get_nested_transaction() or session.get_transaction())
                    is provider_transaction
                ):
                    current, current_doctor = _locked_provider(
                        session, expected=connection, doctor_id=doctor_id, args=args
                    )
                else:
                    current, current_doctor, _ = _current_provider(session, args)
                if current != connection or current_doctor != doctor_id:
                    raise authority.AuthorityRequestDeniedError
                if changed:
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
                return result
        finally:
            if isinstance(request, LookupRequest) and provider_transaction is not None:
                for name in ("after_transaction_create", "after_transaction_end"):
                    event.remove(session, name, transaction_changed)
            session.rollback()
