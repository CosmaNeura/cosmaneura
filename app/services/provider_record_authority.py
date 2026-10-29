"""Bind currently authorized providers to their practice and owned record.

Only verified Clerk exchange calls may provision. Reads revalidate current
access and the immutable binding; client record references never confer access.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from typeid import TypeID
from typeid.errors import TypeIDException

import app.configuration.session_prep as configuration
from app.services.doctor_access import doctor_access_in_force
from app.services.doctor_access_row_ownership import (
    capture_owns_subject_rows,
    provider_record_owner_rows,
)
from app.services.session_prep_provider_access import (
    resolve_session_prep_provider_access,
)

from activemodel.types import TypeIDType
from app.models.appointment import Appointment
from app.models.authority import (
    AuthorityActor,
    AuthorityActorExternalIdentity,
    AuthorityAuditEvent,
    AuthorityPerson,
    AuthorityPersonBinding,
    AuthorityPersonOrigin,
    AuthorityTenant,
    AuthorityTenantMembership,
)
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.practice import (
    SEAT_HOLDING_ACCESS_STATES,
    Practice,
    PracticeMember,
    member_access_state,
)
from app.models.provider_record_authority import (
    ProviderPracticeAuthority,
    ProviderRecordAuthority,
)
from sqlalchemy import select as sql_select
from sqlalchemy.exc import MultipleResultsFound
from sqlmodel import Session, col, select


class ProviderRecordScopeDenied(ValueError):
    """Value-free refusal; no discovery of another provider's record."""


class SessionPrepProviderScopeUnavailable(ProviderRecordScopeDenied):
    """Current provider access is insufficient for Session Prep only."""


def _current_practice(session: Session, doctor: Doctor) -> Practice:
    """Resolve the existing provider-product policy used by Scribe capture."""
    now = datetime.now(UTC)
    if doctor.deleted_at is not None or not doctor_access_in_force(doctor, at=now):
        raise ProviderRecordScopeDenied
    rows = session.exec(
        select(PracticeMember, Practice)
        .outerjoin(Practice, col(Practice.id) == col(PracticeMember.practice_id))
        .where(PracticeMember.doctor_id == doctor.id)
        .execution_options(populate_existing=True)
    ).one_or_none()
    member, practice = rows if rows is not None else (None, None)
    if (
        member is None
        or member_access_state(member, at=now) not in SEAT_HOLDING_ACCESS_STATES
        or practice is None
    ):
        raise ProviderRecordScopeDenied
    return practice


def _current_session_prep_practice(session: Session, doctor: Doctor) -> Practice:
    """Apply the stronger Session Prep policy without changing provider access."""
    if not configuration.SESSION_PREP_PROVIDER_SETTINGS.allows(doctor.clerk_id):
        raise SessionPrepProviderScopeUnavailable
    decision = resolve_session_prep_provider_access(doctor, session=session)
    if not decision.allowed or decision.practice is None:
        raise SessionPrepProviderScopeUnavailable
    return decision.practice


@dataclass(frozen=True)
class _RecordLineageRows:
    rows: tuple[Any, Any, Any, Any]
    origins: tuple[AuthorityPersonOrigin | None, ...]

    def __iter__(self):
        return iter(self.rows)


def _record_lineage_rows(session, scope, binding):
    """Fresh exact-key lineage and bounded provenance; no access decision."""
    statement = (
        sql_select(ProviderPracticeAuthority, Appointment, Patient, AuthorityPerson)
        .select_from(ProviderPracticeAuthority)
        .join(Appointment, Appointment.id == scope.appointment_id)
        .join(Patient, Patient.id == scope.patient_id)
        .join(AuthorityPerson, AuthorityPerson.id == binding.person_id)
        .where(ProviderPracticeAuthority.id == scope.practice_authority_id)
        .execution_options(populate_existing=True)
    )
    # Adapters retain the original four-row read and later exact origin query.
    if not isinstance(session, Session):
        return session.exec(statement).one_or_none()
    # Both reads previously followed the same complete current access check,
    # with only local lineage checks between them. Read append-only provenance
    # in that fresh snapshot; do not turn it into retained permission. Two rows
    # suffice to preserve the later duplicate refusal without unbounded reads.
    current = (
        cast(Any, session)
        .exec(
            statement.add_columns(AuthorityPersonOrigin)
            .outerjoin(
                AuthorityPersonOrigin,
                (col(AuthorityPersonOrigin.person_id) == col(AuthorityPerson.id))
                & (col(AuthorityPersonOrigin.origin) == "provider_entry")
                & (col(AuthorityPersonOrigin.source_ref) == str(scope.patient_id)),
            )
            .limit(2)
        )
        .all()
    )
    if not current:
        return None
    return _RecordLineageRows(
        (current[0][0], current[0][1], current[0][2], current[0][3]),
        tuple(row[4] for row in current),
    )


def _validate_provider_record_scope(
    *,
    session: Session,
    binding: AuthorityPersonBinding,
    actor_id: TypeIDType,
    clerk_subject: str,
    session_prep: bool,
) -> ProviderRecordAuthority | None:
    """Validate one mapped record under generic or Session Prep policy."""
    if capture_owns_subject_rows(session, clerk_subject):
        current = _record_owner_rows(session, binding)
        if current is None:
            return None
        scope, doctor = current
    else:
        scope = session.exec(
            select(ProviderRecordAuthority).where(
                ProviderRecordAuthority.binding_id == binding.id
            )
        ).one_or_none()
        doctor = (
            session.get(Doctor, scope.doctor_id, populate_existing=True)
            if scope is not None
            else None
        )
    if scope is None:
        _validate_unmapped_provider_record_scope(
            session=session,
            binding=binding,
            actor_id=actor_id,
            clerk_subject=clerk_subject,
            session_prep=session_prep,
        )
        return None
    if doctor is None or doctor.clerk_id != clerk_subject:
        raise ProviderRecordScopeDenied
    practice = (
        _current_session_prep_practice(session, doctor)
        if session_prep
        else _current_practice(session, doctor)
    )
    # Keep this fresh record-lineage boundary after the complete current
    # practice/access decision. Nothing becomes cached permission, and the
    # exact provenance decision still follows these ownership checks.
    current = _record_lineage_rows(session, scope, binding)
    if current is None:
        raise ProviderRecordScopeDenied
    return _validate_record_lineage(
        session=session,
        scope=scope,
        doctor=doctor,
        practice=practice,
        binding=binding,
        actor_id=actor_id,
        current=current,
    )


def validate_provider_capture_record_scope(
    *,
    session: Session,
    binding: AuthorityPersonBinding,
    actor_id: TypeIDType,
    clerk_subject: str,
) -> ProviderRecordAuthority | None:
    """Validate generic provider-record access without granting Session Prep."""
    return _validate_provider_record_scope(
        session=session,
        binding=binding,
        actor_id=actor_id,
        clerk_subject=clerk_subject,
        session_prep=False,
    )


def validate_provider_record_scope(
    *,
    session: Session,
    binding: AuthorityPersonBinding,
    actor_id: TypeIDType,
    clerk_subject: str,
) -> ProviderRecordAuthority | None:
    """Validate mapped record lineage plus every current Session Prep gate."""
    return _validate_provider_record_scope(
        session=session,
        binding=binding,
        actor_id=actor_id,
        clerk_subject=clerk_subject,
        session_prep=True,
    )


def _validate_record_lineage(
    *, session, scope, doctor, practice, binding, actor_id, current
):
    """Exact-row checks shared by ordinary reads and bounded catalog inputs."""
    mapping, appointment, patient, person = current
    if (
        mapping is None
        or mapping.id != scope.practice_authority_id
        or mapping.practice_id != practice.id
        or mapping.tenant_id != binding.tenant_id
        or binding.actor_id != actor_id
        or binding.binding_kind != "provider_record"
        or binding.binding_ref != str(scope.appointment_id)
        or binding.status != "active"
        or binding.verified_at is None
        or binding.revoked_at is not None
        or person is None
        or person.status != "active"
        or appointment is None
        or appointment.doctor_id != doctor.id
        or appointment.patient_id != scope.patient_id
        or patient is None
        or patient.doctor_id != doctor.id
    ):
        raise ProviderRecordScopeDenied
    if isinstance(current, _RecordLineageRows):
        if len(current.origins) > 1:
            raise MultipleResultsFound
        origin = next(iter(current.origins), None)
    else:
        origin = session.exec(
            select(AuthorityPersonOrigin).where(
                AuthorityPersonOrigin.person_id == person.id,
                AuthorityPersonOrigin.origin == "provider_entry",
                AuthorityPersonOrigin.source_ref == str(patient.id),
            )
        ).one_or_none()
    if origin is None:
        raise ProviderRecordScopeDenied
    return scope


def _validate_unmapped_provider_record_scope(
    *,
    session: Session,
    binding: AuthorityPersonBinding,
    actor_id: TypeIDType,
    clerk_subject: str,
    session_prep: bool,
) -> None:
    """Validate a legacy appointment binding before record-scope reconciliation."""
    try:
        appointment_id = TypeID.from_string(binding.binding_ref)
    except (TypeIDException, TypeError, ValueError):
        return
    if appointment_id.prefix != "appointment":
        return
    doctor = session.exec(
        select(Doctor)
        .where(Doctor.clerk_id == clerk_subject)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if doctor is None:
        raise ProviderRecordScopeDenied
    practice = (
        _current_session_prep_practice(session, doctor)
        if session_prep
        else _current_practice(session, doctor)
    )
    appointment = session.get(
        Appointment, cast(TypeIDType, appointment_id), populate_existing=True
    )
    patient = (
        session.get(Patient, appointment.patient_id, populate_existing=True)
        if appointment is not None
        else None
    )
    mapping = session.exec(
        select(ProviderPracticeAuthority)
        .where(ProviderPracticeAuthority.practice_id == practice.id)
        .execution_options(populate_existing=True)
    ).one_or_none()
    person = session.get(AuthorityPerson, binding.person_id, populate_existing=True)
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
    if (
        appointment is None
        or patient is None
        or mapping is None
        or person is None
        or origin is None
        or appointment.doctor_id != doctor.id
        or appointment.patient_id != patient.id
        or patient.doctor_id != doctor.id
        or mapping.tenant_id != binding.tenant_id
        or binding.actor_id != actor_id
        or binding.binding_kind != "provider_record"
        or binding.binding_ref != str(appointment.id)
        or binding.status != "active"
        or binding.verified_at is None
        or binding.revoked_at is not None
        or person.status != "active"
    ):
        raise ProviderRecordScopeDenied


def _record_owner_rows(session: Session, binding: AuthorityPersonBinding):
    """Fresh exact-bound scope and Doctor; later full authorization remains."""
    return session.exec(
        select(ProviderRecordAuthority, Doctor)
        .select_from(ProviderRecordAuthority)
        .outerjoin(Doctor, col(Doctor.id) == col(ProviderRecordAuthority.doctor_id))
        .where(ProviderRecordAuthority.binding_id == binding.id)
        .execution_options(populate_existing=True)
    ).one_or_none()


def lock_provider_record_owner(
    *, session: Session, clerk_subject: str
) -> tuple[Doctor, Practice]:
    """Take generic provider locks before any request view-as lock."""
    return _lock_provider_record_owner(
        session=session,
        clerk_subject=clerk_subject,
        session_prep=False,
    )


def lock_session_prep_record_owner(
    *, session: Session, clerk_subject: str
) -> tuple[Doctor, Practice]:
    """Take the same locks while enforcing the stronger Session Prep policy."""
    return _lock_provider_record_owner(
        session=session,
        clerk_subject=clerk_subject,
        session_prep=True,
    )


def _lock_provider_record_owner(
    *, session: Session, clerk_subject: str, session_prep: bool
) -> tuple[Doctor, Practice]:
    """Share the Practice -> Doctor lock order across provider products."""
    # Organization roster/offboarding takes Practice before Doctor. Discover
    # the practice without a lock, acquire that same order, then recheck the
    # membership so a concurrent practice move cannot bind the old practice.
    doctor = session.exec(
        select(Doctor)
        .where(Doctor.clerk_id == clerk_subject)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if doctor is None:
        raise ProviderRecordScopeDenied
    current_practice = (
        _current_session_prep_practice if session_prep else _current_practice
    )
    practice = current_practice(session, doctor)
    with provider_record_owner_rows(
        session, practice_id=practice.id, subject=clerk_subject
    ) as doctor:
        if doctor is None or current_practice(session, doctor).id != practice.id:
            raise ProviderRecordScopeDenied
        return doctor, practice


def ensure_provider_record_scope(
    *, session: Session, clerk_issuer: str, clerk_subject: str, record_ref: str
) -> ProviderRecordAuthority:
    """Provision generic provider record authority for an existing product user."""
    return _ensure_provider_record_scope(
        session=session,
        clerk_issuer=clerk_issuer,
        clerk_subject=clerk_subject,
        record_ref=record_ref,
        session_prep=False,
    )


def ensure_session_prep_record_scope(
    *, session: Session, clerk_issuer: str, clerk_subject: str, record_ref: str
) -> ProviderRecordAuthority:
    """Provision only after the complete current Session Prep decision passes."""
    return _ensure_provider_record_scope(
        session=session,
        clerk_issuer=clerk_issuer,
        clerk_subject=clerk_subject,
        record_ref=record_ref,
        session_prep=True,
    )


def _ensure_provider_record_scope(
    *,
    session: Session,
    clerk_issuer: str,
    clerk_subject: str,
    record_ref: str,
    session_prep: bool,
) -> ProviderRecordAuthority:
    """Provision in one verified transaction; never repair tombstones."""
    try:
        appointment_id = TypeID.from_string(record_ref)
    except (TypeIDException, TypeError, ValueError):
        raise ProviderRecordScopeDenied from None
    if appointment_id.prefix != "appointment":
        raise ProviderRecordScopeDenied
    lock_owner = (
        lock_session_prep_record_owner if session_prep else lock_provider_record_owner
    )
    doctor, practice = lock_owner(session=session, clerk_subject=clerk_subject)
    appointment = session.get(
        Appointment, cast(TypeIDType, appointment_id), populate_existing=True
    )
    if appointment is None or appointment.doctor_id != doctor.id:
        raise ProviderRecordScopeDenied
    patient = session.exec(
        select(Patient)
        .where(Patient.id == appointment.patient_id)
        .with_for_update(of=Patient)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if patient is None or patient.doctor_id != doctor.id:
        raise ProviderRecordScopeDenied

    now = datetime.now(UTC)
    identity = session.exec(
        select(AuthorityActorExternalIdentity).where(
            AuthorityActorExternalIdentity.issuer == clerk_issuer,
            AuthorityActorExternalIdentity.subject == clerk_subject,
        )
    ).one_or_none()
    if identity is None:
        actor = AuthorityActor(actor_kind="human", status="active")
        session.add(actor)
        session.flush()
        identity = AuthorityActorExternalIdentity(
            actor_id=actor.id,
            issuer=clerk_issuer,
            subject=clerk_subject,
            verified_at=now,
        )
        session.add(identity)
    else:
        actor = session.get(AuthorityActor, identity.actor_id)
        if (
            identity.revoked_at is not None
            or actor is None
            or actor.status != "active"
            or actor.actor_kind != "human"
        ):
            raise ProviderRecordScopeDenied

    mapping = session.exec(
        select(ProviderPracticeAuthority).where(
            ProviderPracticeAuthority.practice_id == practice.id
        )
    ).one_or_none()
    if mapping is None:
        tenant = AuthorityTenant(status="active")
        session.add(tenant)
        session.flush()
        mapping = ProviderPracticeAuthority(
            practice_id=practice.id, tenant_id=tenant.id
        )
        session.add(mapping)
        session.flush()
    else:
        tenant = session.get(AuthorityTenant, mapping.tenant_id)
        if tenant is None or tenant.status != "active":
            raise ProviderRecordScopeDenied
    membership = session.exec(
        select(AuthorityTenantMembership).where(
            AuthorityTenantMembership.actor_id == actor.id,
            AuthorityTenantMembership.tenant_id == tenant.id,
        )
    ).one_or_none()
    if membership is not None and (
        membership.status != "active" or membership.membership_kind != "provider"
    ):
        raise ProviderRecordScopeDenied
    active_memberships = session.exec(
        select(AuthorityTenantMembership)
        .where(
            AuthorityTenantMembership.actor_id == actor.id,
            AuthorityTenantMembership.status == "active",
        )
        .with_for_update()
    ).all()
    # The platform's closure and identity-recovery contracts require one active
    # provider tenant. A verified current practice replaces stale provider
    # memberships; it never revives a revoked target or moves a staff identity.
    if any(row.membership_kind != "provider" for row in active_memberships):
        raise ProviderRecordScopeDenied
    # A fixture/import tenant is outside this practice-move mechanism. Deny
    # the exchange rather than revoking it or mixing it into a real cohort.
    for row in active_memberships:
        if (
            row.tenant_id != tenant.id
            and session.exec(
                select(ProviderPracticeAuthority).where(
                    ProviderPracticeAuthority.tenant_id == row.tenant_id
                )
            ).one_or_none()
            is None
        ):
            raise ProviderRecordScopeDenied
    for stale in active_memberships:
        if stale.tenant_id != tenant.id:
            stale.status = "revoked"
            session.add(stale)
            session.add(
                AuthorityAuditEvent(
                    event_type="authority.provider_membership.replaced",
                    outcome="accepted",
                    occurred_at=now,
                    tenant_id=stale.tenant_id,
                    actor_id=actor.id,
                    resource_type="tenant_membership",
                    resource_ref=str(stale.id),
                    request_digest=hashlib.sha256(
                        f"{doctor.id}:{practice.id}:{tenant.id}".encode()
                    ).hexdigest(),
                )
            )
    if membership is None:
        session.add(
            AuthorityTenantMembership(
                actor_id=actor.id,
                tenant_id=tenant.id,
                membership_kind="provider",
                status="active",
            )
        )

    binding = session.exec(
        select(AuthorityPersonBinding).where(
            AuthorityPersonBinding.binding_ref == record_ref
        )
    ).one_or_none()
    if binding is not None:
        validator = (
            validate_provider_record_scope
            if session_prep
            else validate_provider_capture_record_scope
        )
        scope = validator(
            session=session,
            binding=binding,
            actor_id=actor.id,
            clerk_subject=clerk_subject,
        )
        if scope is None:
            # Existing fixture/import bindings require explicit reconciliation.
            raise ProviderRecordScopeDenied
        return scope

    previous = session.exec(
        select(AuthorityPersonBinding)
        .join(
            ProviderRecordAuthority,
            cast(Any, ProviderRecordAuthority.binding_id) == AuthorityPersonBinding.id,
        )
        .where(
            ProviderRecordAuthority.patient_id == patient.id,
            ProviderRecordAuthority.practice_authority_id == mapping.id,
        )
    ).first()
    if previous is not None:
        person = session.get(AuthorityPerson, previous.person_id)
        if person is None or person.status != "active":
            raise ProviderRecordScopeDenied
    else:
        person = AuthorityPerson(lifecycle_state="patient", status="active")
        session.add(person)
        session.flush()
        session.add(
            AuthorityPersonOrigin(
                person_id=person.id, origin="provider_entry", source_ref=str(patient.id)
            )
        )
    binding = AuthorityPersonBinding(
        person_id=person.id,
        tenant_id=tenant.id,
        actor_id=actor.id,
        binding_ref=record_ref,
        binding_kind="provider_record",
        status="active",
        verified_at=now,
    )
    session.add(binding)
    session.flush()
    scope = ProviderRecordAuthority(
        binding_id=binding.id,
        practice_authority_id=mapping.id,
        doctor_id=doctor.id,
        appointment_id=appointment.id,
        patient_id=patient.id,
        context_snapshot_ref=f"context_prep_{uuid4().hex}",
    )
    session.add(scope)
    session.add(
        AuthorityAuditEvent(
            event_type="authority.provider_record.bound",
            outcome="accepted",
            occurred_at=now,
            tenant_id=tenant.id,
            actor_id=actor.id,
            person_id=person.id,
            resource_type="provider_record",
            resource_ref=str(binding.id),
            request_digest=hashlib.sha256(
                f"{doctor.id}:{appointment.id}".encode()
            ).hexdigest(),
        )
    )
    session.flush()
    return scope
