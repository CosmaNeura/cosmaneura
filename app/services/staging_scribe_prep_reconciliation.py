"""Explicit, additive reconciliation of a reviewed staging Scribe scope.

This operator is never called by token exchange or a public route. It preserves
all existing identity and capture rows. The caller supplies a reviewed plan
digest and owns a SERIALIZABLE transaction, including rollback on refusal.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.configuration.authority import AUTHORITY_KERNEL_SETTINGS
from app.configuration.session_prep import SESSION_PREP_PROVIDER_SETTINGS
from app.environments import is_staging
from app.services.doctor_access import resolve_doctor_access
from app.services.organization_access import (
    doctor_has_managed_roster_assignment,
    lock_roster_enrollment_email,
)
from app.services.pilot_access_lock import lock_pilot_access_lineage
from app.services.provider_record_authority import validate_provider_record_scope
from app.services.public_entitlements import (
    record_active_clinician_seat_quantity,
    require_active_clinician_seat_capacity,
)
from app.services.scribe_capture_scope import _scope_digest

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
from app.models.practice import Practice, PracticeMember
from app.models.practice_access_audit import PracticeAccessAudit
from app.models.provider_record_authority import (
    ProviderPracticeAuthority,
    ProviderRecordAuthority,
)
from app.models.scribe_capture_binding import (
    ScribeCaptureBinding,
    ScribeCaptureProviderLink,
)
from sqlmodel import Session, col, select


class ReconciliationRefused(ValueError):
    """Value-free refusal. The caller must roll back the whole transaction."""


def _require(condition: object) -> None:
    if not condition:
        raise ReconciliationRefused("staging_scribe_prep_reconciliation_refused")


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _one(session: Session, statement: Any, lock: bool) -> Any:
    if lock:
        statement = statement.with_for_update()
    return session.exec(
        statement.execution_options(populate_existing=True)
    ).one_or_none()


def _get(session: Session, model: Any, identifier: Any, lock: bool) -> Any:
    value = _one(session, select(model).where(model.id == identifier), lock)
    _require(value is not None)
    return value


def reconcile(
    session: Session,
    *,
    additional_doctor_id: TypeIDType,
    capture_binding_id: TypeIDType,
    operator_ref: str,
    preserve_solo_enrollment: bool = False,
    confirm_plan_sha256: str | None = None,
) -> dict[str, Any]:
    """Plan, or append the exact reviewed mappings and one pilot membership.

    No membership is moved, revived, attested or promoted. Existing account
    access must independently resolve to an active grandfathered pilot grant.
    The practice's public enrollment, BAA and billing settings are preserved.
    """
    _require(is_staging())
    _require(16 <= len(operator_ref) <= 160)
    _require(all(c.isalnum() or c in "._-" for c in operator_ref))
    lock = confirm_plan_sha256 is not None
    if lock:
        _require(session.connection().get_isolation_level() == "SERIALIZABLE")
        lock_pilot_access_lineage(session)

    candidate = _get(session, ScribeCaptureBinding, capture_binding_id, False)
    practice = _get(session, Practice, candidate.practice_id, lock)
    _require(practice.organization_id is None)
    _require(not practice.stripe_subscription_id)
    # Explicit owner-directed pilot membership may preserve a legacy solo
    # enrollment setting. It never opens the public join-code route.
    _require(not practice.is_solo or preserve_solo_enrollment)
    if lock:
        additional_before_lock = _get(session, Doctor, additional_doctor_id, False)
        _require(additional_before_lock.email)
        lock_roster_enrollment_email(session, additional_before_lock.email)
    doctors = {
        str(identifier): _get(session, Doctor, identifier, lock)
        for identifier in sorted({candidate.doctor_id, additional_doctor_id}, key=str)
    }
    _require(len(doctors) == 2)
    doctor = doctors[str(candidate.doctor_id)]
    additional = doctors[str(additional_doctor_id)]
    _require(not doctor_has_managed_roster_assignment(additional, session=session))
    for row in doctors.values():
        decision = resolve_doctor_access(row)
        _require(decision.allowed and decision.authorization_basis == "pilot_grant")
        _require(SESSION_PREP_PROVIDER_SETTINGS.allows(row.clerk_id))

    members = {
        key: _one(
            session,
            select(PracticeMember).where(PracticeMember.doctor_id == row.id),
            lock,
        )
        for key, row in doctors.items()
    }
    _require(members[str(doctor.id)] is not None)
    for member in members.values():
        if member is not None:
            _require(
                member.practice_id == practice.id
                and member.access_revoked_at is None
                and member.seat_state == "active"
            )

    binding = _get(session, ScribeCaptureBinding, capture_binding_id, lock)
    link = _get(session, ScribeCaptureProviderLink, binding.provider_link_id, lock)
    actor = _get(session, AuthorityActor, binding.actor_id, lock)
    identity = _get(
        session, AuthorityActorExternalIdentity, binding.source_identity_id, lock
    )
    tenant = _get(session, AuthorityTenant, binding.tenant_id, lock)
    person_binding = _get(
        session, AuthorityPersonBinding, binding.person_binding_id, lock
    )
    person = _get(session, AuthorityPerson, person_binding.person_id, lock)
    appointment = _get(session, Appointment, binding.appointment_id, lock)
    patient = _get(session, Patient, binding.patient_id, lock)
    _require(
        binding.revoked_at is None
        and link.revoked_at is None
        and link.reviewed_at is not None
        and len(link.review_ref) >= 16
        and actor.status == "active"
        and actor.actor_kind == "human"
        and identity.actor_id == actor.id
        and identity.subject == doctor.clerk_id
        and identity.issuer == AUTHORITY_KERNEL_SETTINGS.clerk_issuer
        and identity.revoked_at is None
        and tenant.status == "active"
        and person.status == "active"
        and person.lifecycle_state == "patient"
        and person_binding.actor_id == actor.id
        and person_binding.tenant_id == tenant.id
        and person_binding.binding_kind == "provider_record"
        and person_binding.binding_ref == str(appointment.id)
        and person_binding.status == "active"
        and person_binding.verified_at is not None
        and person_binding.revoked_at is None
        and appointment.doctor_id == doctor.id
        and appointment.patient_id == patient.id
        and patient.doctor_id == doctor.id
    )
    expected = {
        "provider_link_id": link.id,
        "actor_id": actor.id,
        "tenant_id": tenant.id,
        "source_identity_id": identity.id,
        "person_binding_id": person_binding.id,
        "doctor_id": doctor.id,
        "patient_id": patient.id,
        "appointment_id": appointment.id,
        "practice_id": practice.id,
        "scribe_tenant_id": link.scribe_tenant_id,
    }
    _require(all(getattr(binding, k) == v for k, v in expected.items()))
    _require(binding.scope_sha256 == _scope_digest(expected))
    _require(
        all(
            getattr(link, k) == expected[k]
            for k in (
                "actor_id",
                "tenant_id",
                "source_identity_id",
                "doctor_id",
                "practice_id",
            )
        )
    )
    # This operator deliberately supports only one pre-existing provider. A
    # mixed tenant/practice or shared imported person needs separate review.
    memberships = session.exec(
        select(AuthorityTenantMembership).where(
            (AuthorityTenantMembership.tenant_id == tenant.id)
            | (AuthorityTenantMembership.actor_id == actor.id)
        )
    ).all()
    _require(len(memberships) == 1)
    membership = memberships[0]
    _require(
        membership.actor_id == actor.id
        and membership.tenant_id == tenant.id
        and membership.status == "active"
        and membership.membership_kind == "provider"
    )
    links = session.exec(
        select(ScribeCaptureProviderLink).where(
            (
                (ScribeCaptureProviderLink.practice_id == practice.id)
                | (ScribeCaptureProviderLink.tenant_id == tenant.id)
            ),
            col(ScribeCaptureProviderLink.revoked_at).is_(None),
        )
    ).all()
    _require(len(links) == 1 and links[0].id == link.id)
    person_bindings = session.exec(
        select(AuthorityPersonBinding).where(
            AuthorityPersonBinding.person_id == person.id
        )
    ).all()
    _require(len(person_bindings) == 1 and person_bindings[0].id == person_binding.id)
    origins = session.exec(
        select(AuthorityPersonOrigin).where(
            AuthorityPersonOrigin.person_id == person.id
        )
    ).all()
    # Never reinterpret an origin that names another actual clinical patient.
    for origin in origins:
        _require(origin.origin == "provider_entry")
        _require(
            origin.source_ref == str(patient.id)
            or re.fullmatch(
                r"scribe-staging-[0-9]{8}-[a-z0-9]{8}-provider", origin.source_ref or ""
            )
        )

    mapping = _one(
        session,
        select(ProviderPracticeAuthority).where(
            (ProviderPracticeAuthority.practice_id == practice.id)
            | (ProviderPracticeAuthority.tenant_id == tenant.id)
        ),
        lock,
    )
    record = _one(
        session,
        select(ProviderRecordAuthority).where(
            (ProviderRecordAuthority.binding_id == person_binding.id)
            | (ProviderRecordAuthority.appointment_id == appointment.id)
        ),
        lock,
    )
    audit = _one(
        session,
        select(PracticeAccessAudit).where(
            PracticeAccessAudit.action == "staging_scribe_prep_reconciled",
            PracticeAccessAudit.document_reference == operator_ref,
        ),
        lock,
    )
    scope = {k: str(v) for k, v in expected.items()}
    request_digest = _digest({"additional_doctor": str(additional.id), "scope": scope})
    if audit is not None:
        _require(audit.practice_id == practice.id and audit.reason == request_digest)
        _require(mapping is not None and record is not None)
        _require(mapping.practice_id == practice.id and mapping.tenant_id == tenant.id)
        _require(
            record.binding_id == person_binding.id
            and record.practice_authority_id == mapping.id
            and record.doctor_id == doctor.id
            and record.patient_id == patient.id
            and record.appointment_id == appointment.id
        )
        _require(members[str(additional.id)] is not None)
        _require(
            any(
                o.origin == "provider_entry" and o.source_ref == str(patient.id)
                for o in origins
            )
        )
        if lock:
            _require(confirm_plan_sha256 == audit.evidence_sha256)
        _require(
            validate_provider_record_scope(
                session=session,
                binding=person_binding,
                actor_id=actor.id,
                clerk_subject=doctor.clerk_id,
            )
            is not None
        )
        return {"status": "already_applied", "plan_sha256": audit.evidence_sha256}
    _require(mapping is None and record is None)
    if members[str(additional.id)] is None:
        require_active_clinician_seat_capacity(session, practice)
    plan = {
        "version": 1,
        "operator_ref": operator_ref,
        "scope": scope,
        "additional_doctor": str(additional.id),
        "additional_member_exists": members[str(additional.id)] is not None,
        "preserve_solo_enrollment": preserve_solo_enrollment,
        "practice_solo_enrollment": practice.is_solo,
        "account_fingerprints": sorted(
            _digest(
                {
                    "id": str(d.id),
                    "clerk": d.clerk_id,
                    "email": d.email,
                }
            )
            for d in doctors.values()
        ),
        "capture_scope_sha256": binding.scope_sha256,
        "review_ref_sha256": _digest(link.review_ref),
        "origins_sha256": _digest(
            sorted((str(o.id), o.origin, o.source_ref) for o in origins)
        ),
    }
    plan_sha256 = _digest(plan)
    if not lock:
        return {"status": "planned", "plan_sha256": plan_sha256, "plan": plan}
    _require(confirm_plan_sha256 == plan_sha256)
    now = datetime.now(UTC)
    if members[str(additional.id)] is None:
        session.add(
            PracticeMember(
                doctor_id=additional.id,
                practice_id=practice.id,
                role="workforce",
                seat_state="active",
                attested_at=None,
            )
        )
    mapping = ProviderPracticeAuthority(practice_id=practice.id, tenant_id=tenant.id)
    session.add(mapping)
    session.flush()
    if not any(
        o.origin == "provider_entry" and o.source_ref == str(patient.id)
        for o in origins
    ):
        session.add(
            AuthorityPersonOrigin(
                person_id=person.id,
                origin="provider_entry",
                source_ref=str(patient.id),
            )
        )
    session.add(
        ProviderRecordAuthority(
            binding_id=person_binding.id,
            practice_authority_id=mapping.id,
            doctor_id=doctor.id,
            appointment_id=appointment.id,
            patient_id=patient.id,
            context_snapshot_ref=f"context_prep_{uuid4().hex}",
        )
    )
    session.add(
        PracticeAccessAudit(
            practice_id=practice.id,
            action="staging_scribe_prep_reconciled",
            operator_ref=operator_ref,
            reason=request_digest,
            effective_at=now,
            document_reference=operator_ref,
            evidence_sha256=plan_sha256,
        )
    )
    session.add(
        AuthorityAuditEvent(
            event_type="authority.scribe_prep.reconciled",
            outcome="accepted",
            occurred_at=now,
            tenant_id=tenant.id,
            actor_id=actor.id,
            person_id=person.id,
            resource_type="provider_record",
            resource_ref=str(person_binding.id),
            request_digest=plan_sha256,
        )
    )
    session.flush()
    record_active_clinician_seat_quantity(session, practice)
    _require(
        validate_provider_record_scope(
            session=session,
            binding=person_binding,
            actor_id=actor.id,
            clerk_subject=doctor.clerk_id,
        )
        is not None
    )
    return {"status": "applied", "plan_sha256": plan_sha256}
