"""Derive new visit references from existing reviewed provider correspondence.

The appointment insertion lifecycle and explicit reviewed setup call this service,
never discovery.
It creates no actors, identities, tenants or memberships. Reference inserts use
the source transaction, so rolling back the appointment removes its reference.
"""

import hashlib
from datetime import UTC, datetime
from typing import Any, cast

from app import log
from app.configuration.authority import AUTHORITY_KERNEL_SETTINGS
from app.services.doctor_access import doctor_access_in_force

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
from app.models.practice import PracticeMember
from app.models.scribe_capture_binding import ScribeCaptureProviderLink
from sqlalchemy import insert
from sqlalchemy.orm import Session
from sqlmodel import col, select


class DeferredBinding(Exception):
    """Closed operational reason without patient or account content."""


def _provider(s, appointment):
    doctor = s.get(Doctor, appointment.doctor_id)
    if doctor is None or doctor.deleted_at is not None:
        raise DeferredBinding("provider_unavailable")
    links = list(s.scalars(select(ScribeCaptureProviderLink).where(
        ScribeCaptureProviderLink.doctor_id == doctor.id,
        col(ScribeCaptureProviderLink.revoked_at).is_(None),
    )))
    if not links:
        return None
    if len(links) != 1:
        raise DeferredBinding("provider_mapping_ambiguous")
    link = links[0]
    actor = s.get(AuthorityActor, link.actor_id)
    identity = s.get(AuthorityActorExternalIdentity, link.source_identity_id)
    tenant = s.get(AuthorityTenant, link.tenant_id)
    membership = s.scalar(select(AuthorityTenantMembership).where(
        AuthorityTenantMembership.actor_id == link.actor_id,
        AuthorityTenantMembership.tenant_id == link.tenant_id,
    ))
    practice_member = s.scalar(select(PracticeMember).where(PracticeMember.doctor_id == doctor.id))
    if (actor is None or actor.actor_kind != "human" or actor.status != "active"
        or identity is None or identity.actor_id != actor.id or identity.revoked_at is not None
        or identity.verified_at is None or identity.issuer != AUTHORITY_KERNEL_SETTINGS.clerk_issuer
        or identity.subject != doctor.clerk_id or tenant is None or tenant.status != "active"
        or membership is None or membership.membership_kind != "provider" or membership.status != "active"
        or link.reviewed_at is None or len(link.review_ref) < 16
        or practice_member is None or practice_member.practice_id != link.practice_id
        or not doctor_access_in_force(doctor)):
        raise DeferredBinding("provider_mapping_unavailable")
    return link


def _existing_person(s: Session, patient: Patient, tenant_id):
    """Read-only eligibility shared by new-visit insertion and reviewed setup."""
    origins = list(s.scalars(select(col(AuthorityPersonOrigin.person_id)).where(
        AuthorityPersonOrigin.origin == "provider_entry",
        AuthorityPersonOrigin.source_ref == str(patient.id),
    ).distinct().limit(2)))
    people = {str(person_id): person_id for person_id in origins}
    if len(people) > 1:
        raise DeferredBinding("patient_correspondence_ambiguous")
    if people:
        person_id = next(iter(people.values()))
        person = s.get(AuthorityPerson, person_id)
        if person is None or person.status != "active" or person.lifecycle_state not in {"patient", "enrolled"}:
            raise DeferredBinding("patient_correspondence_unavailable")
        if s.scalar(select(col(AuthorityPersonBinding.id)).where(
            AuthorityPersonBinding.person_id == person_id,
            col(AuthorityPersonBinding.tenant_id).is_not(None),
            AuthorityPersonBinding.tenant_id != tenant_id,
        ).limit(1)) is not None:
            raise DeferredBinding("patient_correspondence_review_required")
        return person_id
    return None


def _bind(s: Session, appointment: Appointment, *, setup_plan_sha256: str | None = None):
    link = _provider(s, appointment)
    if link is None:
        return
    # NO KEY UPDATE is compatible with concurrent appointment FK key-share locks.
    patient = s.scalar(select(Patient).where(Patient.id == appointment.patient_id)
                       .with_for_update(key_share=True))
    if patient is None or patient.doctor_id != appointment.doctor_id:
        raise DeferredBinding("patient_scope_unavailable")
    reference = str(appointment.id)
    if s.scalar(select(col(AuthorityPersonBinding.id)).where(
        AuthorityPersonBinding.binding_ref == reference,
    )) is not None:
        raise ValueError("companion_appointment_reference_collision")
    person_id = _existing_person(s, patient, link.tenant_id)
    if person_id is None:
        # after_flush_postexec persisted source IDs but did not commit. Immediate
        # inserts avoid re-entering ORM flush or creating a second session.
        person_id = s.execute(insert(AuthorityPerson).values(
            lifecycle_state="patient", status="active",
        ).returning(cast(Any, AuthorityPerson.id))).scalar_one()
        s.execute(insert(AuthorityPersonOrigin).values(
            person_id=person_id, origin="provider_entry", source_ref=str(patient.id),
        ))
    now = datetime.now(UTC)
    s.execute(insert(AuthorityPersonBinding).values(
        person_id=person_id, tenant_id=link.tenant_id, actor_id=link.actor_id,
        binding_ref=reference, binding_kind="provider_record", status="active", verified_at=now,
    ))
    prefix = "new-core-visit-v1" if setup_plan_sha256 is None else "existing-core-visit-v1:" + setup_plan_sha256
    evidence = f"{prefix}:{link.id}:{patient.id}:{appointment.id}"
    s.execute(insert(AuthorityAuditEvent).values(
        event_source="platform_authoritative", event_type="authority.appointment_reference.created",
        outcome="accepted", occurred_at=now, recorded_at=now, tenant_id=link.tenant_id,
        actor_id=link.actor_id, person_id=person_id, resource_type="appointment", resource_ref=reference,
        request_digest=hashlib.sha256(evidence.encode()).hexdigest(),
        content_sha256=setup_plan_sha256,
    ))


def bind_inserted_appointments(s: Session, appointments: list[Appointment]):
    if not AUTHORITY_KERNEL_SETTINGS.enabled or not AUTHORITY_KERNEL_SETTINGS.is_complete():
        raise ValueError("companion_appointment_binding_authority_configuration_required")
    with s.no_autoflush:
        for appointment in sorted(appointments, key=lambda row: (str(row.patient_id), str(row.id))):
            try:
                _bind(s, appointment)
            except DeferredBinding as error:
                # Core remains usable when disconnected. No authority row is
                # created and no private source contents are logged on this path.
                log.warning("companion appointment reference deferred", reason=str(error))
