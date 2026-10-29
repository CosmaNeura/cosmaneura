"""Bounded catalog inputs under its existing provider transaction, never authority."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, cast

from app.services import authority, provider_record_authority
from app.services.doctor_access_row_ownership import lookup_owns_subject_rows

from activemodel.types import TypeIDType
from app.models.appointment import Appointment
from app.models.authority import (
    AuthorityPerson,
    AuthorityPersonBinding,
    AuthorityPersonOrigin,
)
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.practice import Practice
from app.models.provider_record_authority import (
    ProviderPracticeAuthority,
    ProviderRecordAuthority,
)
from app.models.scribe_capture_binding import (
    ScribeCaptureBinding,
    ScribeCaptureProviderLink,
)
from app.models.scribe_patient_identity import ScribePatientIdentity
from sqlalchemy import Integer, String, column, event, values
from sqlalchemy import select as sql_select
from sqlalchemy.exc import MultipleResultsFound
from sqlalchemy.orm import SessionTransaction
from sqlmodel import Session, col, select


@dataclass
class CatalogRow:
    appointment: Appointment | None
    patient: Patient | None
    binding: AuthorityPersonBinding | None
    record: ProviderRecordAuthority | None
    doctor: Doctor | None
    mapping: ProviderPracticeAuthority | None
    person: AuthorityPerson | None
    origin: AuthorityPersonOrigin | None
    capture: ScribeCaptureBinding | None
    identity: ScribePatientIdentity | None = None
    canonical_patient: Patient | None = None


@dataclass
class CatalogScope:
    session: Session
    transaction: SessionTransaction
    doctor_id: TypeIDType
    subject: str
    link: ScribeCaptureProviderLink
    practice: Practice
    rows: dict[int, CatalogRow]
    active: bool = True

    def invalidate(self, *_):
        self.active = False


_SCOPE: ContextVar[CatalogScope | None] = ContextVar(
    "scribe_catalog_rows", default=None
)


def _targets(rows):
    # TypeID UUID columns and their actual opaque text references remain distinct;
    # no SQL UUID-to-text cast is allowed to invent a TypeID binding reference.
    return values(
        column("ordinal", Integer),
        column("appointment_id", TypeIDType("appointment")),
        column("patient_id", TypeIDType("patient")),
        column("appointment_ref", String),
        column("patient_ref", String),
        name="catalog_targets",
    ).data(
        [
            (
                i,
                r.appointment_id,
                r.patient_id,
                str(r.appointment_id),
                str(r.patient_id),
            )
            for i, r in enumerate(rows)
        ]
    )


@contextmanager
def catalog_rows(session, rows, *, doctor_id, subject, link):
    """Gather exact inputs once; all record predicates still run for each row.

    Initial provider authority/locks already succeeded; a fresh final provider
    validation is still mandatory after the catalog. The shared provider access
    check below runs inside those same locks. No result survives a transaction
    transition, scope exit, pending mutation, different row or owner.
    """
    if not rows or not lookup_owns_subject_rows(session, doctor_id, subject):
        yield
        return
    transaction = session.get_nested_transaction() or session.get_transaction()
    if transaction is None or session.new or session.dirty or session.deleted:
        yield
        return
    if len(rows) > 1000:
        raise authority.AuthorityRequestDeniedError
    targets = _targets(rows)
    query = (
        sql_select(
            targets.c.ordinal,
            Appointment,
            Patient,
            AuthorityPersonBinding,
            ProviderRecordAuthority,
            Doctor,
            ProviderPracticeAuthority,
            AuthorityPerson,
            AuthorityPersonOrigin,
            ScribeCaptureBinding,
        )
        .select_from(targets)
        .outerjoin(Appointment, col(Appointment.id) == targets.c.appointment_id)
        .outerjoin(Patient, col(Patient.id) == targets.c.patient_id)
        .outerjoin(
            AuthorityPersonBinding,
            col(AuthorityPersonBinding.binding_ref) == targets.c.appointment_ref,
        )
        .outerjoin(
            ProviderRecordAuthority,
            col(ProviderRecordAuthority.binding_id) == col(AuthorityPersonBinding.id),
        )
        .outerjoin(Doctor, col(Doctor.id) == col(ProviderRecordAuthority.doctor_id))
        .outerjoin(
            ProviderPracticeAuthority,
            col(ProviderPracticeAuthority.practice_id) == link.practice_id,
        )
        .outerjoin(
            AuthorityPerson,
            col(AuthorityPerson.id) == col(AuthorityPersonBinding.person_id),
        )
        .outerjoin(
            AuthorityPersonOrigin,
            (col(AuthorityPersonOrigin.person_id) == col(AuthorityPerson.id))
            & (col(AuthorityPersonOrigin.origin) == "provider_entry")
            & (col(AuthorityPersonOrigin.source_ref) == targets.c.patient_ref),
        )
        .outerjoin(
            ScribeCaptureBinding,
            (col(ScribeCaptureBinding.appointment_id) == targets.c.appointment_id)
            & (col(ScribeCaptureBinding.actor_id) == link.actor_id),
        )
        .order_by(targets.c.ordinal)
        .limit(len(rows) * 2 + 1)
    )
    gathered = cast(Any, session).exec(query).all()
    loaded = {}
    for ordinal, *data in gathered:
        if ordinal in loaded:
            raise MultipleResultsFound("Duplicate catalog input rows")
        loaded[ordinal] = CatalogRow(*data)
    if len(loaded) != len(rows):
        raise authority.AuthorityRequestDeniedError
    owner = session.get(Doctor, doctor_id)
    if owner is None or owner.clerk_id != subject:
        raise authority.AuthorityRequestDeniedError
    # Every candidate must still belong to this exact current provider. Final
    # grant/time/roster checks run again before the response can be published.
    practice = provider_record_authority._current_practice(session, owner)
    identities = session.exec(
        select(targets.c.ordinal, ScribePatientIdentity)
        .select_from(targets)
        .join(
            ScribePatientIdentity,
            col(ScribePatientIdentity.appointment_id) == targets.c.appointment_id,
        )
        .order_by(targets.c.ordinal)
        .with_for_update(of=ScribePatientIdentity)
    ).all()
    seen = set()
    for ordinal, identity in identities:
        if ordinal in seen:
            raise MultipleResultsFound("Duplicate catalog patient identity")
        seen.add(ordinal)
        loaded[ordinal].identity = identity
    canonical_ids = {
        value.identity.canonical_patient_id
        for ordinal, value in loaded.items()
        if value.identity is not None
        and value.identity.canonical_patient_id != rows[ordinal].patient_id
    }
    canonical = {}
    if canonical_ids:
        canonical = {
            p.id: p
            for p in session.exec(
                select(Patient)
                .where(
                    col(Patient.id).in_(canonical_ids), Patient.doctor_id == doctor_id
                )
                .order_by(col(Patient.id))
                .with_for_update(read=True, key_share=True)
            ).all()
        }
    for ordinal, value in loaded.items():
        target = (
            value.identity.canonical_patient_id
            if value.identity
            else rows[ordinal].patient_id
        )
        value.canonical_patient = (
            value.patient
            if value.patient is not None and value.patient.id == target
            else canonical.get(target)
        )
    scope = CatalogScope(
        session,
        transaction,
        doctor_id,
        subject,
        link,
        practice,
        {id(rows[i]): value for i, value in loaded.items()},
    )
    token = _SCOPE.set(scope)
    names = ("after_transaction_create", "after_transaction_end", "before_flush")
    for name in names:
        event.listen(session, name, scope.invalidate)
    try:
        yield
    finally:
        scope.invalidate()
        for name in names:
            event.remove(session, name, scope.invalidate)
        _SCOPE.reset(token)


def catalog_row(session, row, *, doctor_id, subject, link):
    scope = _SCOPE.get()
    if (
        scope is None
        or not scope.active
        or scope.session is not session
        or scope.link is not link
        or scope.doctor_id != doctor_id
        or scope.subject != subject
        or not scope.transaction.is_active
        or (session.get_nested_transaction() or session.get_transaction())
        is not scope.transaction
        or session.new
        or session.dirty
        or session.deleted
        or not lookup_owns_subject_rows(session, doctor_id, subject)
    ):
        return None
    result = scope.rows.get(id(row))
    if result is None:
        return None
    return result


def validate_record_row(session, result, *, doctor_id, subject, link):
    scope = _SCOPE.get()
    assert scope is not None
    if result.record is None:
        _validate_unmapped_record(
            appointment=result.appointment,
            patient=result.patient,
            binding=result.binding,
            person=result.person,
            mapping=result.mapping,
            origin=result.origin,
            doctor_id=doctor_id,
            practice=scope.practice,
            link=link,
        )
        return result
    if (
        result.doctor is None
        or result.doctor.id != doctor_id
        or result.doctor.clerk_id != subject
    ):
        raise provider_record_authority.ProviderRecordScopeDenied
    provider_record_authority._validate_record_lineage(
        session=session,
        scope=result.record,
        doctor=result.doctor,
        practice=scope.practice,
        binding=result.binding,
        actor_id=link.actor_id,
        current=provider_record_authority._RecordLineageRows(
            (result.mapping, result.appointment, result.patient, result.person),
            (result.origin,) if result.origin is not None else (),
        ),
    )
    return result


def _validate_unmapped_record(
    *, appointment, patient, binding, person, mapping, origin, doctor_id, practice, link
):
    """Validate a retained Scribe binding that predates Prep record mapping."""
    if (
        appointment is None
        or patient is None
        or binding is None
        or person is None
        or mapping is None
        or origin is None
        or practice.id != link.practice_id
        or mapping.practice_id != practice.id
        or mapping.tenant_id != link.tenant_id
        or appointment.doctor_id != doctor_id
        or appointment.patient_id != patient.id
        or patient.doctor_id != doctor_id
        or binding.person_id != person.id
        or binding.actor_id != link.actor_id
        or binding.tenant_id != link.tenant_id
        or binding.binding_kind != "provider_record"
        or binding.binding_ref != str(appointment.id)
        or binding.status != "active"
        or binding.verified_at is None
        or binding.revoked_at is not None
        or person.status != "active"
    ):
        raise authority.AuthorityRequestDeniedError


def validate_unmapped_record(
    session, *, appointment, patient, binding, doctor_id, subject, link
):
    """Fresh fallback for adapters outside the bounded catalog read."""
    doctor = session.get(Doctor, doctor_id, populate_existing=True)
    if doctor is None or doctor.clerk_id != subject:
        raise authority.AuthorityRequestDeniedError
    practice = provider_record_authority._current_practice(session, doctor)
    person = (
        session.get(AuthorityPerson, binding.person_id, populate_existing=True)
        if binding is not None
        else None
    )
    mapping = session.exec(
        select(ProviderPracticeAuthority)
        .where(ProviderPracticeAuthority.practice_id == practice.id)
        .execution_options(populate_existing=True)
    ).one_or_none()
    origin = (
        session.exec(
            select(AuthorityPersonOrigin).where(
                AuthorityPersonOrigin.person_id == person.id,
                AuthorityPersonOrigin.origin == "provider_entry",
                AuthorityPersonOrigin.source_ref == str(patient.id),
            )
        ).one_or_none()
        if person is not None and patient is not None
        else None
    )
    _validate_unmapped_record(
        appointment=appointment,
        patient=patient,
        binding=binding,
        person=person,
        mapping=mapping,
        origin=origin,
        doctor_id=doctor_id,
        practice=practice,
        link=link,
    )


def identity_view(value, row, link):
    identity, patient = value.identity, value.canonical_patient
    if identity is not None and (
        identity.doctor_id != row.doctor_id
        or identity.actor_id != link.actor_id
        or identity.tenant_id != link.tenant_id
        or identity.provider_link_id != link.id
        or identity.source_patient_id != row.patient_id
    ):
        raise authority.AuthorityRequestDeniedError
    if patient is None or patient.doctor_id != row.doctor_id:
        raise authority.AuthorityRequestDeniedError
    return {
        "state": identity.state if identity else "confirmed",
        "version": identity.version if identity else 0,
        "canonical_patient_ref": str(patient.id),
        "name": patient.name,
        "date_of_birth": patient.date_of_birth.date().isoformat()
        if patient.date_of_birth
        else None,
    }
