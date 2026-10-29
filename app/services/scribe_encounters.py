"""Create Core visits atomically, then use the existing Scribe consent/capture path."""

from contextlib import nullcontext
from datetime import UTC, date, datetime, time, timedelta
from uuid import uuid4

from typeid import TypeID

from app.services import (
    authority,
    provider_record_authority,
    scribe_catalog_rows,
    scribe_visit_extras,
)
from app.services.companion_appointment_setup import _current_provider
from app.services.companion_encounter_contract import digest
from app.services.companion_encounters import _locked_provider
from app.services.companion_records import (
    RecordUnavailable,
    check_deadline,
    transaction_budget,
)
from app.services.doctor_access_row_ownership import (
    lookup_doctor_access_row_scope,
    lookup_owns_subject_rows,
)
from app.services.scribe_capture_scope import _scope_digest as capture_scope_digest
from app.services.scribe_encounter_contract import (
    AddBirthDateRequest,
    CatalogRequest,
    CreateRequest,
    ExistingPatient,
    LookupRequest,
    NewPatient,
    PatientMatchCandidatesRequest,
    PatientMatchSaveRequest,
    PatientsRequest,
    PrivateNoteReadRequest,
    PrivateNoteSaveRequest,
)
from app.services.scribe_encounter_status import saved_encounter_statuses

from activemodel.session_manager import get_session, global_session
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
from app.models.scribe_patient_identity import ScribePatientIdentity
from sqlalchemy import event
from sqlmodel import Session, col, select


class EncounterConflict(RecordUnavailable):
    """The retained creation key was already used for different visit details."""


def _visit(
    session,
    row,
    *,
    doctor_id,
    link,
    subject,
    saved_statuses=None,
    expected_capture_binding_ref=None,
):
    if row.doctor_id != doctor_id or row.provider_link_id != link.id:
        raise authority.AuthorityRequestDeniedError
    batch = scribe_catalog_rows.catalog_row(
        session, row, doctor_id=doctor_id, subject=subject, link=link
    )
    owned = lookup_owns_subject_rows(session, doctor_id, subject)
    if batch is not None:
        appointment, patient, binding = batch.appointment, batch.patient, batch.binding
    elif owned:
        appointment, patient, binding = _visit_input_rows(session, row)
    else:
        appointment = session.get(Appointment, row.appointment_id)
        patient = session.get(Patient, row.patient_id)
        binding = None
    if (
        appointment is None
        or patient is None
        or appointment.doctor_id != doctor_id
        or patient.doctor_id != doctor_id
        or appointment.patient_id != patient.id
    ):
        raise authority.AuthorityRequestDeniedError
    if not owned and batch is None:
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
    if batch is not None:
        scribe_catalog_rows.validate_record_row(
            session, batch, doctor_id=doctor_id, subject=subject, link=link
        )
    else:
        record_scope = provider_record_authority.validate_provider_capture_record_scope(
            session=session,
            binding=binding,
            actor_id=link.actor_id,
            clerk_subject=subject,
        )
        if record_scope is None:
            scribe_catalog_rows.validate_unmapped_record(
                session,
                appointment=appointment,
                patient=patient,
                binding=binding,
                doctor_id=doctor_id,
                subject=subject,
                link=link,
            )
    capture_query = select(ScribeCaptureBinding).where(
        ScribeCaptureBinding.appointment_id == appointment.id,
        ScribeCaptureBinding.actor_id == link.actor_id,
    )
    if expected_capture_binding_ref is not None:
        capture_query = capture_query.execution_options(populate_existing=True)
    capture = (
        batch.capture
        if batch is not None
        else session.exec(capture_query).one_or_none()
    )
    if capture is not None and (
        capture.revoked_at is not None
        or capture.workspace_id != row.workspace_id
        or capture.provider_link_id != link.id
    ):
        raise authority.AuthorityRequestDeniedError
    if expected_capture_binding_ref is not None:
        # Mount alone creates a binding. Admission requires that exact existing
        # binding and the complete current visit/link/identity scope, using the
        # rows already read under this lookup's original provider locks. These
        # checks grant no future permission; admission locks/revalidates again.
        scope = {
            "provider_link_id": link.id,
            "actor_id": link.actor_id,
            "tenant_id": link.tenant_id,
            "source_identity_id": link.source_identity_id,
            "person_binding_id": binding.id,
            "doctor_id": doctor_id,
            "patient_id": patient.id,
            "appointment_id": appointment.id,
            "practice_id": link.practice_id,
            "scribe_tenant_id": link.scribe_tenant_id,
        }
        if (
            capture is None
            or str(capture.id) != expected_capture_binding_ref
            or capture.scope_sha256 != capture_scope_digest(scope)
            or any(getattr(capture, key) != value for key, value in scope.items())
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
        "identity": scribe_catalog_rows.identity_view(batch, row, link)
        if batch is not None
        else scribe_visit_extras.identity_view(session, row, link, patient),
        **({"status": status} if status else {}),
    }


def _visit_input_rows(session, row):
    """Exact visit inputs after original lookup locks; all validators remain.

    Preserve the original identity-map behavior. These inputs confer no access:
    complete current record lineage, provenance, capture binding and final
    provider authorization still follow at their original boundaries.
    """
    current = session.exec(
        select(Appointment, Patient, AuthorityPersonBinding)
        .select_from(Appointment)
        .outerjoin(Patient, col(Patient.id) == row.patient_id)
        .outerjoin(
            AuthorityPersonBinding,
            col(AuthorityPersonBinding.binding_ref) == str(row.appointment_id),
        )
        .where(Appointment.id == row.appointment_id)
    ).one_or_none()
    return current if current is not None else (None, None, None)


def _create(session, request, doctor_id, link, issuer, subject):
    body = request.model_dump(mode="json", exclude={"request_nonce"})
    # Keep pre-redesign retry digests byte-for-byte compatible. Opting into
    # provisional entry is additive and remains bound into its own digest.
    if isinstance(request.patient, NewPatient) and not request.patient.provisional:
        body["patient"].pop("provisional", None)
    request_digest = digest(body)
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
                col(Patient.id).not_in(
                    scribe_visit_extras.unselectable_source_patients()
                ),
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
    if isinstance(request.patient, NewPatient) and request.patient.provisional:
        session.add(
            ScribePatientIdentity(
                appointment_id=appointment.id,
                doctor_id=doctor_id,
                actor_id=link.actor_id,
                tenant_id=link.tenant_id,
                provider_link_id=link.id,
                source_patient_id=patient.id,
                canonical_patient_id=patient.id,
                provisional_entry=True,
                state="provisional",
                version=0,
            )
        )
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
    expected_capture_binding_ref: str | None = None,
    existing_session: Session | None = None,
):
    """Authorize each request; optionally omit unpublished lookup display status.

    The native facade uses this internal option for intermediate reauthorization
    only. Public lookup callers and catalog/create responses retain enrichment.
    """
    if expected_capture_binding_ref is not None and not isinstance(
        request, LookupRequest
    ):
        raise ValueError("capture_binding_requires_lookup")

    def available():
        check_deadline(deadline)
        if not settings_provider().enabled:
            raise RecordUnavailable

    available()
    scoped_read = isinstance(request, (CatalogRequest, LookupRequest))
    args = dict(
        issuer=issuer,
        subject=subject,
        session_id=session_id,
        tenant_ref=request.expected_tenant_ref,
        settings=authority_settings,
    )
    # Browser routes already own an ActiveModel request scope. Reuse only that
    # exact inherited Session; native/service calls keep their fresh scope.
    with get_session() if existing_session is not None else global_session() as session:
        if existing_session is not None and session is not existing_session:
            raise authority.AuthorityRequestDeniedError
        provider_transaction = None
        provider_locks_current = False

        def transaction_changed(*_args):
            nonlocal provider_locks_current
            provider_locks_current = False

        try:
            with (
                transaction_budget(session, deadline),
                lookup_doctor_access_row_scope(session)
                if scoped_read
                else nullcontext(),
            ):
                connection, doctor_id, link = _current_provider(session, args)
                if scoped_read:
                    # The initial routine individually locks the full provider
                    # authority/link/member rows. Only this synchronous lookup
                    # or catalog may use their fresh final-row validator, in the
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
                    query = select(Patient).where(
                        Patient.doctor_id == doctor_id,
                        col(Patient.id).not_in(
                            scribe_visit_extras.unselectable_source_patients()
                        ),
                    )
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
                    with scribe_catalog_rows.catalog_rows(
                        session, rows, doctor_id=doctor_id, subject=subject, link=link
                    ):
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
                        expected_capture_binding_ref=expected_capture_binding_ref,
                    )
                elif isinstance(
                    request,
                    (
                        PrivateNoteReadRequest,
                        PrivateNoteSaveRequest,
                        PatientMatchCandidatesRequest,
                        PatientMatchSaveRequest,
                    ),
                ):
                    row = session.exec(
                        select(ScribeEncounterCreation).where(
                            ScribeEncounterCreation.appointment_id
                            == TypeID.from_string(request.appointment_ref),
                            ScribeEncounterCreation.doctor_id == doctor_id,
                            ScribeEncounterCreation.provider_link_id == link.id,
                        )
                    ).one_or_none()
                    if row is None:
                        raise authority.AuthorityRequestDeniedError
                    _visit(
                        session, row, doctor_id=doctor_id, link=link, subject=subject
                    )
                    if isinstance(request, PrivateNoteReadRequest):
                        result["private_note"] = scribe_visit_extras.private_note_view(
                            scribe_visit_extras.private_note(session, row, link)
                        )
                    elif isinstance(request, PrivateNoteSaveRequest):
                        result["private_note"], changed = (
                            scribe_visit_extras.save_private_note(
                                session, request, row, link, EncounterConflict
                            )
                        )
                        result["change_key"] = str(request.change_key)
                    elif isinstance(request, PatientMatchCandidatesRequest):
                        result.update(
                            scribe_visit_extras.match_candidates(session, request, row)
                        )
                        result["identity"] = scribe_visit_extras.identity_view(
                            session, row, link
                        )
                    else:
                        changed = scribe_visit_extras.save_match(
                            session, request, row, link, EncounterConflict
                        )
                        result["encounter"] = _visit(
                            session,
                            row,
                            doctor_id=doctor_id,
                            link=link,
                            subject=subject,
                        )
                        result["identity"] = result["encounter"]["identity"]
                        result["change_key"] = str(request.change_key)
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
                    scoped_read
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
            if scoped_read and provider_transaction is not None:
                for name in ("after_transaction_create", "after_transaction_end"):
                    event.remove(session, name, transaction_changed)
            session.rollback()
