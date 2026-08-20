"""Explicit staging-only bindings from platform records into the authority kernel."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from app.configuration.authority import (
    AUTHORITY_KERNEL_SETTINGS,
    AuthorityKernelSettings,
)
from app.environments import is_staging

from activemodel.session_manager import get_session
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
from app.models.synthetic_preview_appointment_fixture import (
    SYNTHETIC_PREVIEW_FIXTURE_VERSION,
    SyntheticPreviewAppointmentFixture,
)
from sqlmodel import Session, select


class AuthorityProvisioningError(ValueError):
    """Safe operator-facing refusal; never includes record contents."""


@dataclass(frozen=True)
class ProviderAuthorityProvisioningResult:
    """Session-independent identifiers safe to return from the operator command."""

    actor_id: TypeIDType
    tenant_id: TypeIDType
    person_id: TypeIDType
    binding_id: TypeIDType
    binding_ref: str
    binding_kind: str
    binding_actor_id: TypeIDType | None
    created: bool


def _single_provider_membership(
    session: Session,
    actor_id: TypeIDType,
) -> AuthorityTenantMembership | None:
    memberships = list(
        session.exec(
            select(AuthorityTenantMembership).where(
                AuthorityTenantMembership.actor_id == actor_id,
                AuthorityTenantMembership.membership_kind == "provider",
                AuthorityTenantMembership.status == "active",
            )
        ).all()
    )
    if len(memberships) > 1:
        raise AuthorityProvisioningError(
            "provider authority has more than one active tenant membership"
        )
    return memberships[0] if memberships else None


def provision_staging_provider_authority(
    *,
    doctor_id: TypeIDType,
    doctor_email: str,
    appointment_id: TypeIDType,
    operator_ref: str,
    tenant_id: TypeIDType | None = None,
    session: Session | None = None,
    settings: AuthorityKernelSettings = AUTHORITY_KERNEL_SETTINGS,
) -> ProviderAuthorityProvisioningResult:
    """Bind one signed-in provider and fixed synthetic appointment idempotently."""

    if not is_staging():
        raise AuthorityProvisioningError(
            "provider authority provisioning is restricted to staging"
        )
    if not settings.has_valid_clerk_issuer():
        raise AuthorityProvisioningError(
            "the configured Clerk identity issuer is unavailable"
        )
    if not 3 <= len(operator_ref) <= 160 or not all(
        character.isalnum() or character in "._~-" for character in operator_ref
    ):
        raise AuthorityProvisioningError(
            "operator_ref must be an opaque audit reference"
        )
    if session is None:
        with get_session() as managed_session:
            return provision_staging_provider_authority(
                doctor_id=doctor_id,
                doctor_email=doctor_email,
                appointment_id=appointment_id,
                operator_ref=operator_ref,
                tenant_id=tenant_id,
                session=cast(Session, managed_session),
                settings=settings,
            )

    doctor = session.exec(
        select(Doctor).where(Doctor.id == doctor_id).with_for_update()
    ).one_or_none()
    if (
        doctor is None
        or doctor.deleted_at is not None
        or doctor.last_active_at is None
        or doctor.email != doctor_email
    ):
        raise AuthorityProvisioningError(
            "doctor id and email did not match one signed-in staging doctor"
        )
    fixture = session.exec(
        select(SyntheticPreviewAppointmentFixture).where(
            SyntheticPreviewAppointmentFixture.doctor_id == doctor.id,
            SyntheticPreviewAppointmentFixture.appointment_id == appointment_id,
            cast(Any, SyntheticPreviewAppointmentFixture.synthetic_only).is_(True),
            SyntheticPreviewAppointmentFixture.fixture_version
            == SYNTHETIC_PREVIEW_FIXTURE_VERSION,
        )
    ).one_or_none()
    appointment = session.get(Appointment, appointment_id)
    if (
        fixture is None
        or appointment is None
        or appointment.doctor_id != doctor.id
        or appointment.patient_id != fixture.patient_id
    ):
        raise AuthorityProvisioningError(
            "appointment is not the doctor's governed synthetic staging fixture"
        )
    patient = session.get(Patient, fixture.patient_id)
    if patient is None or patient.doctor_id != doctor.id:
        raise AuthorityProvisioningError("synthetic patient scope is invalid")

    created = False
    identity = session.exec(
        select(AuthorityActorExternalIdentity).where(
            AuthorityActorExternalIdentity.issuer == settings.clerk_issuer,
            AuthorityActorExternalIdentity.subject == doctor.clerk_id,
        )
    ).one_or_none()
    now = datetime.now(UTC)
    if identity is None:
        actor = AuthorityActor(actor_kind="human", status="active")
        session.add(actor)
        session.flush()
        identity = AuthorityActorExternalIdentity(
            actor_id=actor.id,
            issuer=settings.clerk_issuer,
            subject=doctor.clerk_id,
            verified_at=now,
        )
        session.add(identity)
        created = True
    else:
        actor = session.get(AuthorityActor, identity.actor_id)
        if (
            actor is None
            or actor.actor_kind != "human"
            or actor.status != "active"
            or identity.revoked_at is not None
        ):
            raise AuthorityProvisioningError("existing provider authority is inactive")

    membership = _single_provider_membership(session, actor.id)
    if membership is not None:
        tenant = session.get(AuthorityTenant, membership.tenant_id)
        if tenant_id is not None and membership.tenant_id != tenant_id:
            raise AuthorityProvisioningError(
                "existing provider membership does not match requested tenant"
            )
    elif tenant_id is not None:
        tenant = session.get(AuthorityTenant, tenant_id)
        if tenant is None or tenant.status != "active":
            raise AuthorityProvisioningError("requested authority tenant is inactive")
        membership = AuthorityTenantMembership(
            tenant_id=tenant.id,
            actor_id=actor.id,
            membership_kind="provider",
            status="active",
        )
        session.add(membership)
        created = True
    else:
        tenant = AuthorityTenant(status="active")
        session.add(tenant)
        session.flush()
        membership = AuthorityTenantMembership(
            tenant_id=tenant.id,
            actor_id=actor.id,
            membership_kind="provider",
            status="active",
        )
        session.add(membership)
        created = True
    if tenant is None or tenant.status != "active":
        raise AuthorityProvisioningError("provider authority tenant is inactive")

    binding_ref = str(appointment.id)
    binding = session.exec(
        select(AuthorityPersonBinding).where(
            AuthorityPersonBinding.binding_ref == binding_ref
        )
    ).one_or_none()
    if binding is None:
        person = AuthorityPerson(lifecycle_state="patient", status="active")
        session.add(person)
        session.flush()
        session.add(
            AuthorityPersonOrigin(
                person_id=person.id,
                origin="provider_entry",
                source_ref=str(patient.id),
            )
        )
        binding = AuthorityPersonBinding(
            person_id=person.id,
            tenant_id=tenant.id,
            binding_ref=binding_ref,
            binding_kind="provider_record",
            status="active",
            verified_at=now,
        )
        session.add(binding)
        created = True
    else:
        person = session.get(AuthorityPerson, binding.person_id)
        if (
            person is None
            or person.status != "active"
            or binding.tenant_id != tenant.id
            or binding.actor_id is not None
            or binding.binding_kind != "provider_record"
            or binding.status != "active"
            or binding.verified_at is None
            or binding.revoked_at is not None
        ):
            raise AuthorityProvisioningError(
                "existing appointment authority binding failed validation"
            )
        origin = session.exec(
            select(AuthorityPersonOrigin).where(
                AuthorityPersonOrigin.person_id == person.id,
                AuthorityPersonOrigin.origin == "provider_entry",
                AuthorityPersonOrigin.source_ref == str(patient.id),
            )
        ).one_or_none()
        if origin is None:
            raise AuthorityProvisioningError(
                "existing appointment authority provenance is incomplete"
            )

    if created:
        digest = hashlib.sha256(
            f"{doctor.id}:{appointment.id}:{operator_ref}".encode()
        ).hexdigest()
        session.add(
            AuthorityAuditEvent(
                event_type="authority.provider_fixture.provisioned",
                outcome="accepted",
                occurred_at=now,
                recorded_at=now,
                tenant_id=tenant.id,
                actor_id=actor.id,
                person_id=person.id,
                resource_type="appointment",
                resource_ref=binding_ref,
                request_digest=digest,
            )
        )
    session.commit()
    return ProviderAuthorityProvisioningResult(
        actor_id=actor.id,
        tenant_id=tenant.id,
        person_id=person.id,
        binding_id=binding.id,
        binding_ref=binding.binding_ref,
        binding_kind=binding.binding_kind,
        binding_actor_id=binding.actor_id,
        created=created,
    )
