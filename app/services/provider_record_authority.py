"""Bind selected providers to their actual practice and owned clinical record.

Only verified Clerk exchange calls may provision. Reads revalidate current
access and the immutable binding; client record references never confer access.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from typeid import TypeID
from typeid.errors import TypeIDException

import app.configuration.session_prep as configuration
from app.services.doctor_access import doctor_access_in_force

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
from sqlmodel import Session, col, select


class ProviderRecordScopeDenied(ValueError):
    """Value-free refusal; no discovery of another provider's record."""


def _current_practice_rows(session: Session, doctor: Doctor):
    """One fresh post-access read, including an absent practice or membership."""
    return session.exec(
        select(PracticeMember, Practice)
        .outerjoin(Practice, col(Practice.id) == col(PracticeMember.practice_id))
        .where(PracticeMember.doctor_id == doctor.id)
        .execution_options(populate_existing=True)
    ).one_or_none()


def _current_practice(session: Session, doctor: Doctor) -> Practice:
    now = datetime.now(UTC)
    if (
        doctor.deleted_at is not None
        or not configuration.SESSION_PREP_PROVIDER_SETTINGS.allows(doctor.clerk_id)
        or not doctor_access_in_force(doctor, at=now)
    ):
        raise ProviderRecordScopeDenied
    rows = _current_practice_rows(session, doctor)
    member, practice = rows if rows is not None else (None, None)
    if (
        member is None
        or member_access_state(member, at=now) not in SEAT_HOLDING_ACCESS_STATES
    ):
        raise ProviderRecordScopeDenied
    if practice is None:
        raise ProviderRecordScopeDenied
    return practice


def _record_lineage_rows(session, scope, binding):
    """Fresh exact-key lineage rows; never a provider access decision."""
    return session.exec(
        sql_select(ProviderPracticeAuthority, Appointment, Patient, AuthorityPerson)
        .select_from(ProviderPracticeAuthority)
        .join(Appointment, Appointment.id == scope.appointment_id)
        .join(Patient, Patient.id == scope.patient_id)
        .join(AuthorityPerson, AuthorityPerson.id == binding.person_id)
        .where(ProviderPracticeAuthority.id == scope.practice_authority_id)
        .execution_options(populate_existing=True)
    ).one_or_none()


def validate_provider_record_scope(
    *,
    session: Session,
    binding: AuthorityPersonBinding,
    actor_id: TypeIDType,
    clerk_subject: str,
) -> ProviderRecordAuthority | None:
    """A stored real-record binding can never fall back to synthetic authority."""
    scope = session.exec(
        select(ProviderRecordAuthority).where(
            ProviderRecordAuthority.binding_id == binding.id
        )
    ).one_or_none()
    if scope is None:
        return None
    doctor = session.get(Doctor, scope.doctor_id, populate_existing=True)
    if doctor is None or doctor.clerk_id != clerk_subject:
        raise ProviderRecordScopeDenied
    practice = _current_practice(session, doctor)
    # Keep this fresh record-lineage boundary after the complete current
    # practice/access decision. Nothing becomes cached permission, and the
    # separate exact provenance check still follows these ownership checks.
    current = _record_lineage_rows(session, scope, binding)
    if current is None:
        raise ProviderRecordScopeDenied
    mapping, appointment, patient, person = current
    if (
        mapping is None
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


def lock_provider_record_owner(
    *, session: Session, clerk_subject: str
) -> tuple[Doctor, Practice]:
    """Take the shared practice/doctor order before any request view-as lock."""
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
    practice = _current_practice(session, doctor)
    session.exec(
        select(Practice).where(Practice.id == practice.id).with_for_update()
    ).one()
    doctor = session.exec(
        select(Doctor)
        .where(Doctor.clerk_id == clerk_subject)
        .with_for_update(of=Doctor)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if doctor is None or _current_practice(session, doctor).id != practice.id:
        raise ProviderRecordScopeDenied
    return doctor, practice


def ensure_provider_record_scope(
    *, session: Session, clerk_issuer: str, clerk_subject: str, record_ref: str
) -> ProviderRecordAuthority:
    """Provision within the verified token exchange transaction; never repair tombstones."""
    try:
        appointment_id = TypeID.from_string(record_ref)
    except (TypeIDException, TypeError, ValueError):
        raise ProviderRecordScopeDenied from None
    if appointment_id.prefix != "appointment":
        raise ProviderRecordScopeDenied
    doctor, practice = lock_provider_record_owner(
        session=session, clerk_subject=clerk_subject
    )
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
        scope = validate_provider_record_scope(
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
