"""Staging-only, audited replacement of one stale authority identity.

This operation is intentionally narrower than authority provisioning.  It can
only replace the single active Clerk identity on an already-provisioned
synthetic provider fixture, and it never changes the actor, tenant,
membership, person, provider-record binding, mount sessions, or Doctor row.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from clerk_backend_api.models import ClerkErrors

from app.configuration.authority import (
    AUTHORITY_KERNEL_SETTINGS,
    AuthorityKernelSettings,
)
from app.configuration.clerk import clerk
from app.environments import is_staging
from app.services.clerk_cohort import (
    ClerkCohortError,
    ClerkCohortIdentity,
    canonical_clerk_email_pair_sha256,
)
from app.services.staging_synthetic_appointment import (
    SYNTHETIC_APPOINTMENT_DATE,
    SYNTHETIC_APPOINTMENT_NAME,
    SYNTHETIC_APPOINTMENT_NOTES,
    SYNTHETIC_CREATED_BY_SYSTEM,
    SYNTHETIC_PATIENT_DATE_OF_BIRTH,
    SYNTHETIC_PATIENT_NAME,
)

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
from app.models.doctor import CLERK_OBJECT_PREFIX, Doctor
from app.models.patient import Patient
from app.models.synthetic_preview_appointment_fixture import (
    SYNTHETIC_PREVIEW_FIXTURE_VERSION,
    SyntheticPreviewAppointmentFixture,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

AUTHORITY_IDENTITY_REBIND_EVENT_TYPE = "authority.provider_identity.rebound"
AUTHORITY_IDENTITY_REBIND_RESOURCE_TYPE = "authority_identity_rebind"
AUTHORITY_IDENTITY_REBIND_SCHEMA_VERSION = 1

_OPAQUE_OPERATOR_REF = re.compile(r"^[A-Za-z0-9._~-]+$")


class AuthorityIdentityRebindError(ValueError):
    """Safe refusal that never includes a Clerk subject or email address."""


@dataclass(frozen=True)
class AuthorityIdentityRebindPlan:
    """Value-free plan summary suitable for an operator confirmation."""

    plan_sha256: str
    identity_count: int
    active_identity_count: int
    revoked_identity_count: int
    already_applied: bool


@dataclass(frozen=True)
class AuthorityIdentityRebindResult:
    """Value-free result for one atomic rebind attempt."""

    plan_sha256: str
    changed: bool


@dataclass(frozen=True)
class _ValidatedScope:
    doctor: Doctor
    actor: AuthorityActor
    tenant: AuthorityTenant
    membership: AuthorityTenantMembership
    person: AuthorityPerson
    origin: AuthorityPersonOrigin
    binding: AuthorityPersonBinding
    fixture: SyntheticPreviewAppointmentFixture
    patient: Patient


@dataclass(frozen=True)
class _EvaluatedRebind:
    plan: AuthorityIdentityRebindPlan
    scope: _ValidatedScope
    stale_identity: AuthorityActorExternalIdentity
    current_identity: AuthorityActorExternalIdentity | None
    receipt: AuthorityAuditEvent | None
    current_clerk_identity: ClerkCohortIdentity


def _confirmed_sha256(value: str, *, field: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise AuthorityIdentityRebindError(
            f"{field} must be a 64-character lowercase hex digest"
        )
    return normalized


def _operator_ref(value: str) -> str:
    normalized = value.strip()
    if (
        normalized != value
        or not 3 <= len(normalized) <= 160
        or not _OPAQUE_OPERATOR_REF.fullmatch(normalized)
    ):
        raise AuthorityIdentityRebindError(
            "operator_ref must be one opaque audit reference"
        )
    return normalized


def _reason(value: str) -> str:
    normalized = value.strip()
    if (
        normalized != value
        or not 8 <= len(normalized) <= 512
        or any(ord(character) < 32 for character in normalized)
    ):
        raise AuthorityIdentityRebindError(
            "reason must contain 8 to 512 printable characters"
        )
    return normalized


def _subject_sha256(subject: str) -> str:
    return hashlib.sha256(subject.encode("utf-8")).hexdigest()


def _normalized_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _same_timestamp(*values: datetime) -> bool:
    normalized = [_normalized_naive_utc(value) for value in values]
    return all(value == normalized[0] for value in normalized[1:])


def _plan_sha256(
    *,
    doctor_id: TypeIDType,
    appointment_id: TypeIDType,
    actor_id: TypeIDType,
    issuer: str,
    current_clerk_pair_sha256: str,
    current_subject_sha256: str,
    stale_subject_sha256: str,
    fixture_id: TypeIDType,
    tenant_id: TypeIDType,
    membership_id: TypeIDType,
    person_id: TypeIDType,
    person_origin_id: TypeIDType,
    binding_id: TypeIDType,
    patient_id: TypeIDType,
    predecessor_set_sha256: str,
    operator_ref: str,
    reason: str,
) -> str:
    canonical = json.dumps(
        {
            "action": "staging_authority_identity_rebind",
            "actor_id": str(actor_id),
            "appointment_id": str(appointment_id),
            "current_clerk_pair_sha256": current_clerk_pair_sha256,
            "current_subject_sha256": current_subject_sha256,
            "doctor_id": str(doctor_id),
            "fixture_id": str(fixture_id),
            "issuer_sha256": hashlib.sha256(issuer.encode("utf-8")).hexdigest(),
            "membership_id": str(membership_id),
            "operator_ref": operator_ref,
            "patient_id": str(patient_id),
            "person_id": str(person_id),
            "person_binding_id": str(binding_id),
            "person_origin_id": str(person_origin_id),
            "predecessor_set_sha256": predecessor_set_sha256,
            "reason": reason,
            "schema_version": AUTHORITY_IDENTITY_REBIND_SCHEMA_VERSION,
            "stale_subject_sha256": stale_subject_sha256,
            "tenant_id": str(tenant_id),
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _receipt_resource_ref(plan_sha256: str) -> str:
    return f"identity-rebind:{plan_sha256}"


def _predecessor_set_sha256(
    identities: list[AuthorityActorExternalIdentity],
    *,
    current_subject: str,
    stale_subject_sha256: str,
) -> str:
    predecessors = [
        identity for identity in identities if identity.subject != current_subject
    ]
    canonical = json.dumps(
        [
            {
                "identity_id": str(identity.id),
                "issuer_sha256": hashlib.sha256(
                    identity.issuer.encode("utf-8")
                ).hexdigest(),
                "role": (
                    "stale_active_target"
                    if hmac.compare_digest(
                        _subject_sha256(identity.subject), stale_subject_sha256
                    )
                    else "prior_revoked"
                ),
                "subject_sha256": _subject_sha256(identity.subject),
                "verified_at": identity.verified_at.isoformat(),
            }
            for identity in sorted(predecessors, key=lambda row: str(row.id))
        ],
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _exact_live_clerk_identity(
    current_subject: str,
    expected_current_clerk_pair_sha256: str,
) -> ClerkCohortIdentity:
    try:
        user = clerk.users.get(user_id=current_subject)
    except Exception:
        raise AuthorityIdentityRebindError(
            "the exact current Clerk identity could not be verified"
        ) from None
    if (
        user is None
        or getattr(user, "id", None) != current_subject
        or getattr(user, "banned", None) is not False
        or getattr(user, "locked", None) is not False
    ):
        raise AuthorityIdentityRebindError(
            "the exact current Clerk identity could not be verified"
        )
    primary_id = getattr(user, "primary_email_address_id", None)
    addresses = getattr(user, "email_addresses", None)
    if not isinstance(primary_id, str) or not primary_id or not addresses:
        raise AuthorityIdentityRebindError(
            "the exact current Clerk identity could not be verified"
        )
    primary = [
        address for address in addresses if getattr(address, "id", None) == primary_id
    ]
    if (
        len(primary) != 1
        or getattr(getattr(primary[0], "verification", None), "status", None)
        != "verified"
    ):
        raise AuthorityIdentityRebindError(
            "the exact current Clerk identity could not be verified"
        )
    email_value = getattr(primary[0], "email_address", None)
    if not isinstance(email_value, str):
        raise AuthorityIdentityRebindError(
            "the exact current Clerk identity could not be verified"
        )
    identity = ClerkCohortIdentity(
        clerk_id=current_subject,
        email=email_value.strip().lower(),
    )
    try:
        actual_pair_sha256 = canonical_clerk_email_pair_sha256((identity,))
    except ClerkCohortError:
        raise AuthorityIdentityRebindError(
            "the exact current Clerk identity could not be verified"
        ) from None
    if not hmac.compare_digest(
        actual_pair_sha256,
        expected_current_clerk_pair_sha256,
    ):
        raise AuthorityIdentityRebindError(
            "the exact current Clerk identity did not match the confirmed digest"
        )
    return identity


def _is_exact_clerk_resource_not_found(error: Exception) -> bool:
    if not isinstance(error, ClerkErrors):
        return False
    data = getattr(error, "data", None)
    errors = getattr(data, "errors", None)
    return (
        isinstance(errors, list)
        and len(errors) == 1
        and getattr(errors[0], "code", None) == "resource_not_found"
    )


def _require_absent_clerk_predecessor(subject: str) -> None:
    try:
        user = clerk.users.get(user_id=subject)
    except Exception as exc:
        if _is_exact_clerk_resource_not_found(exc):
            return
        raise AuthorityIdentityRebindError(
            "a predecessor Clerk identity absence could not be proven"
        ) from None
    if user is not None:
        raise AuthorityIdentityRebindError("a predecessor Clerk identity still exists")
    raise AuthorityIdentityRebindError(
        "a predecessor Clerk identity absence could not be proven"
    )


def _validate_synthetic_scope(
    session: Session,
    *,
    doctor: Doctor,
    appointment_id: TypeIDType,
    lock: bool,
) -> tuple[SyntheticPreviewAppointmentFixture, Appointment, Patient]:
    fixture_statement = select(SyntheticPreviewAppointmentFixture).where(
        SyntheticPreviewAppointmentFixture.doctor_id == doctor.id,
        SyntheticPreviewAppointmentFixture.appointment_id == appointment_id,
        cast(Any, SyntheticPreviewAppointmentFixture.synthetic_only).is_(True),
        SyntheticPreviewAppointmentFixture.fixture_version
        == SYNTHETIC_PREVIEW_FIXTURE_VERSION,
    )
    if lock:
        fixture_statement = fixture_statement.with_for_update()
    fixture = session.exec(fixture_statement).one_or_none()
    if fixture is None:
        raise AuthorityIdentityRebindError(
            "the requested appointment is not the governed synthetic fixture"
        )

    appointment_statement = select(Appointment).where(
        Appointment.id == fixture.appointment_id,
        Appointment.doctor_id == doctor.id,
        Appointment.patient_id == fixture.patient_id,
    )
    patient_statement = select(Patient).where(
        Patient.id == fixture.patient_id,
        Patient.doctor_id == doctor.id,
    )
    if lock:
        appointment_statement = appointment_statement.with_for_update()
        patient_statement = patient_statement.with_for_update()
    appointment = session.exec(appointment_statement).one_or_none()
    patient = session.exec(patient_statement).one_or_none()
    if (
        appointment is None
        or patient is None
        or patient.name != SYNTHETIC_PATIENT_NAME
        or patient.date_of_birth is None
        or _normalized_naive_utc(patient.date_of_birth)
        != SYNTHETIC_PATIENT_DATE_OF_BIRTH
        or _normalized_naive_utc(appointment.appointment_date)
        != SYNTHETIC_APPOINTMENT_DATE
        or appointment.appointment_name != SYNTHETIC_APPOINTMENT_NAME
        or appointment.appointment_notes_preview != SYNTHETIC_APPOINTMENT_NOTES
        or appointment.created_by_system != SYNTHETIC_CREATED_BY_SYSTEM
        or appointment.external_id is not None
    ):
        raise AuthorityIdentityRebindError(
            "the governed synthetic fixture failed its integrity check"
        )
    return fixture, appointment, patient


def _validated_scope(
    session: Session,
    *,
    doctor_id: TypeIDType,
    appointment_id: TypeIDType,
    actor_id: TypeIDType,
    current_clerk_identity: ClerkCohortIdentity,
    lock: bool,
) -> _ValidatedScope:
    doctor_statement = select(Doctor).where(Doctor.id == doctor_id)
    if lock:
        doctor_statement = doctor_statement.with_for_update()
    doctor = session.exec(doctor_statement).one_or_none()
    if (
        doctor is None
        or doctor.deleted_at is not None
        or doctor.last_active_at is None
        or not doctor.email
        or doctor.clerk_id != current_clerk_identity.clerk_id
        or not hmac.compare_digest(
            doctor.email.strip().lower(), current_clerk_identity.email
        )
    ):
        raise AuthorityIdentityRebindError(
            "the Doctor row did not match the active Clerk singleton"
        )

    fixture, appointment, patient = _validate_synthetic_scope(
        session,
        doctor=doctor,
        appointment_id=appointment_id,
        lock=lock,
    )

    binding_statement = select(AuthorityPersonBinding).where(
        AuthorityPersonBinding.binding_ref == str(appointment.id)
    )
    if lock:
        binding_statement = binding_statement.with_for_update()
    binding = session.exec(binding_statement).one_or_none()
    if (
        binding is None
        or binding.binding_kind != "provider_record"
        or binding.actor_id is not None
        or binding.status != "active"
        or binding.verified_at is None
        or binding.revoked_at is not None
        or binding.tenant_id is None
    ):
        raise AuthorityIdentityRebindError(
            "the synthetic provider-record binding is not active and exact"
        )

    actor_statement = select(AuthorityActor).where(AuthorityActor.id == actor_id)
    tenant_statement = select(AuthorityTenant).where(
        AuthorityTenant.id == binding.tenant_id
    )
    person_statement = select(AuthorityPerson).where(
        AuthorityPerson.id == binding.person_id
    )
    if lock:
        actor_statement = actor_statement.with_for_update()
        tenant_statement = tenant_statement.with_for_update()
        person_statement = person_statement.with_for_update()
    actor = session.exec(actor_statement).one_or_none()
    tenant = session.exec(tenant_statement).one_or_none()
    person = session.exec(person_statement).one_or_none()
    if actor is None or actor.actor_kind != "human" or actor.status != "active":
        raise AuthorityIdentityRebindError("the requested authority actor is inactive")
    if tenant is None or tenant.status != "active":
        raise AuthorityIdentityRebindError("the synthetic authority tenant is inactive")
    if (
        person is None
        or person.lifecycle_state != "patient"
        or person.status != "active"
    ):
        raise AuthorityIdentityRebindError(
            "the synthetic authority person is not an active patient"
        )

    membership_statement = select(AuthorityTenantMembership).where(
        AuthorityTenantMembership.actor_id == actor.id
    )
    if lock:
        membership_statement = membership_statement.with_for_update()
    memberships = list(session.exec(membership_statement).all())
    if (
        len(memberships) != 1
        or memberships[0].tenant_id != tenant.id
        or memberships[0].membership_kind != "provider"
        or memberships[0].status != "active"
    ):
        raise AuthorityIdentityRebindError(
            "the authority actor membership is not singular, active, and exact"
        )

    origin_statement = select(AuthorityPersonOrigin).where(
        AuthorityPersonOrigin.person_id == person.id,
        AuthorityPersonOrigin.origin == "provider_entry",
        AuthorityPersonOrigin.source_ref == str(patient.id),
    )
    if lock:
        origin_statement = origin_statement.with_for_update()
    origins = list(session.exec(origin_statement).all())
    if len(origins) != 1:
        raise AuthorityIdentityRebindError(
            "the synthetic authority provenance is incomplete"
        )
    return _ValidatedScope(
        doctor=doctor,
        actor=actor,
        tenant=tenant,
        membership=memberships[0],
        person=person,
        origin=origins[0],
        binding=binding,
        fixture=fixture,
        patient=patient,
    )


def _matching_receipts(
    session: Session,
    *,
    plan_sha256: str,
) -> list[AuthorityAuditEvent]:
    return list(
        session.exec(
            select(AuthorityAuditEvent).where(
                AuthorityAuditEvent.event_type == AUTHORITY_IDENTITY_REBIND_EVENT_TYPE,
                AuthorityAuditEvent.resource_type
                == AUTHORITY_IDENTITY_REBIND_RESOURCE_TYPE,
                AuthorityAuditEvent.resource_ref == _receipt_resource_ref(plan_sha256),
            )
        ).all()
    )


def _receipt_is_exact(
    receipt: AuthorityAuditEvent,
    *,
    scope: _ValidatedScope,
    stale_identity: AuthorityActorExternalIdentity,
    current_identity: AuthorityActorExternalIdentity,
    plan_sha256: str,
) -> bool:
    return (
        receipt.event_source == "platform_authoritative"
        and receipt.event_type == AUTHORITY_IDENTITY_REBIND_EVENT_TYPE
        and receipt.outcome == "accepted"
        and receipt.tenant_id == scope.tenant.id
        and receipt.actor_id == scope.actor.id
        and receipt.person_id == scope.person.id
        and receipt.workload_client_id is None
        and receipt.client_event_id is None
        and receipt.interaction_id is None
        and receipt.resource_type == AUTHORITY_IDENTITY_REBIND_RESOURCE_TYPE
        and receipt.resource_ref == _receipt_resource_ref(plan_sha256)
        and receipt.request_digest == plan_sha256
        and receipt.route is None
        and receipt.intent is None
        and receipt.content_sha256 is None
        and receipt.platform_refs == []
        and stale_identity.revoked_at is not None
        and _same_timestamp(
            stale_identity.revoked_at,
            current_identity.verified_at,
            receipt.occurred_at,
            receipt.recorded_at,
        )
    )


def _evaluate(
    session: Session,
    *,
    doctor_id: TypeIDType,
    appointment_id: TypeIDType,
    actor_id: TypeIDType,
    operator_ref: str,
    reason: str,
    expected_current_clerk_pair_sha256: str,
    expected_stale_subject_sha256: str,
    settings: AuthorityKernelSettings,
    lock: bool,
) -> _EvaluatedRebind:
    if not is_staging():
        raise AuthorityIdentityRebindError(
            "authority identity rebind is restricted to staging"
        )
    if not settings.has_valid_clerk_issuer():
        raise AuthorityIdentityRebindError(
            "the configured Clerk identity issuer is unavailable"
        )
    operator = _operator_ref(operator_ref)
    explanation = _reason(reason)
    current_pair_sha256 = _confirmed_sha256(
        expected_current_clerk_pair_sha256,
        field="expected_current_clerk_pair_sha256",
    )
    stale_subject_sha256 = _confirmed_sha256(
        expected_stale_subject_sha256,
        field="expected_stale_subject_sha256",
    )
    doctor_subject = session.exec(
        select(Doctor.clerk_id).where(Doctor.id == doctor_id)
    ).one_or_none()
    if not isinstance(doctor_subject, str) or not doctor_subject.startswith(
        CLERK_OBJECT_PREFIX
    ):
        raise AuthorityIdentityRebindError(
            "the Doctor row did not contain a valid Clerk subject"
        )
    live_identity = _exact_live_clerk_identity(
        doctor_subject,
        current_pair_sha256,
    )
    current_subject_sha256 = _subject_sha256(live_identity.clerk_id)
    if hmac.compare_digest(current_subject_sha256, stale_subject_sha256):
        raise AuthorityIdentityRebindError(
            "the stale identity confirmation matches the current Clerk subject"
        )

    scope = _validated_scope(
        session,
        doctor_id=doctor_id,
        appointment_id=appointment_id,
        actor_id=actor_id,
        current_clerk_identity=live_identity,
        lock=lock,
    )
    identities_statement = select(AuthorityActorExternalIdentity).where(
        AuthorityActorExternalIdentity.actor_id == scope.actor.id
    )
    if lock:
        identities_statement = identities_statement.with_for_update()
    identities = list(session.exec(identities_statement).all())
    if any(identity.issuer != settings.clerk_issuer for identity in identities):
        raise AuthorityIdentityRebindError(
            "the authority actor has an identity from an unexpected issuer"
        )

    stale = [
        identity
        for identity in identities
        if hmac.compare_digest(_subject_sha256(identity.subject), stale_subject_sha256)
    ]
    current = [
        identity
        for identity in identities
        if identity.subject == live_identity.clerk_id
    ]
    active = [identity for identity in identities if identity.revoked_at is None]
    revoked = [identity for identity in identities if identity.revoked_at is not None]
    if len(stale) != 1 or len(current) > 1:
        raise AuthorityIdentityRebindError(
            "the authority identity set did not match the reviewed rebind"
        )

    already_applied = (
        len(identities) == 3
        and len(active) == 1
        and len(revoked) == 2
        and len(current) == 1
        and current[0] is active[0]
        and stale[0].revoked_at is not None
    )
    pending = (
        len(identities) == 2
        and len(active) == 1
        and len(revoked) == 1
        and not current
        and stale[0] is active[0]
    )
    if not pending and not already_applied:
        raise AuthorityIdentityRebindError(
            "the authority identity set did not match the reviewed rebind"
        )

    predecessor_set_sha256 = _predecessor_set_sha256(
        identities,
        current_subject=live_identity.clerk_id,
        stale_subject_sha256=stale_subject_sha256,
    )
    plan_sha256 = _plan_sha256(
        doctor_id=doctor_id,
        appointment_id=appointment_id,
        actor_id=actor_id,
        issuer=settings.clerk_issuer,
        current_clerk_pair_sha256=current_pair_sha256,
        current_subject_sha256=current_subject_sha256,
        stale_subject_sha256=stale_subject_sha256,
        fixture_id=scope.fixture.id,
        tenant_id=scope.tenant.id,
        membership_id=scope.membership.id,
        person_id=scope.person.id,
        person_origin_id=scope.origin.id,
        binding_id=scope.binding.id,
        patient_id=scope.patient.id,
        predecessor_set_sha256=predecessor_set_sha256,
        operator_ref=operator,
        reason=explanation,
    )

    # The active predecessor is revocable only when its exact Clerk GET proves
    # that the user no longer exists. A generic 404, network failure, or
    # malformed provider error is not absence evidence. Historical revoked
    # identities remain untouched and are not consulted for authorization.
    _require_absent_clerk_predecessor(stale[0].subject)

    # The global issuer/subject uniqueness is part of the schema. Check it
    # explicitly so a cross-actor collision fails with a safe deterministic
    # error instead of relying on an IntegrityError after the mutation starts.
    global_current = session.exec(
        select(AuthorityActorExternalIdentity).where(
            AuthorityActorExternalIdentity.issuer == settings.clerk_issuer,
            AuthorityActorExternalIdentity.subject == live_identity.clerk_id,
        )
    ).one_or_none()
    if (pending and global_current is not None) or (
        already_applied
        and (
            global_current is None
            or global_current.id != cast(AuthorityActorExternalIdentity, current[0]).id
        )
    ):
        raise AuthorityIdentityRebindError(
            "the current Clerk subject is already bound outside the reviewed state"
        )

    receipts = _matching_receipts(session, plan_sha256=plan_sha256)
    receipt = receipts[0] if len(receipts) == 1 else None
    if pending and receipts:
        raise AuthorityIdentityRebindError(
            "a rebind receipt exists without the reviewed terminal state"
        )
    if already_applied and (
        len(receipts) != 1
        or receipt is None
        or not _receipt_is_exact(
            receipt,
            scope=scope,
            stale_identity=stale[0],
            current_identity=cast(AuthorityActorExternalIdentity, current[0]),
            plan_sha256=plan_sha256,
        )
    ):
        raise AuthorityIdentityRebindError(
            "the reviewed terminal state lacks one exact immutable receipt"
        )

    return _EvaluatedRebind(
        plan=AuthorityIdentityRebindPlan(
            plan_sha256=plan_sha256,
            identity_count=len(identities),
            active_identity_count=len(active),
            revoked_identity_count=len(revoked),
            already_applied=already_applied,
        ),
        scope=scope,
        stale_identity=stale[0],
        current_identity=current[0] if current else None,
        receipt=receipt,
        current_clerk_identity=live_identity,
    )


def plan_staging_provider_authority_identity_rebind(
    *,
    doctor_id: TypeIDType,
    appointment_id: TypeIDType,
    actor_id: TypeIDType,
    operator_ref: str,
    reason: str,
    expected_current_clerk_pair_sha256: str,
    expected_stale_subject_sha256: str,
    session: Session | None = None,
    settings: AuthorityKernelSettings = AUTHORITY_KERNEL_SETTINGS,
) -> AuthorityIdentityRebindPlan:
    """Validate and hash the complete rebind without changing durable state."""

    if session is None:
        with get_session() as managed_session:
            return plan_staging_provider_authority_identity_rebind(
                doctor_id=doctor_id,
                appointment_id=appointment_id,
                actor_id=actor_id,
                operator_ref=operator_ref,
                reason=reason,
                expected_current_clerk_pair_sha256=(expected_current_clerk_pair_sha256),
                expected_stale_subject_sha256=expected_stale_subject_sha256,
                session=cast(Session, managed_session),
                settings=settings,
            )
    try:
        return _evaluate(
            session,
            doctor_id=doctor_id,
            appointment_id=appointment_id,
            actor_id=actor_id,
            operator_ref=operator_ref,
            reason=reason,
            expected_current_clerk_pair_sha256=expected_current_clerk_pair_sha256,
            expected_stale_subject_sha256=expected_stale_subject_sha256,
            settings=settings,
            lock=False,
        ).plan
    except SQLAlchemyError:
        try:
            session.rollback()
        except Exception:
            pass
        raise AuthorityIdentityRebindError(
            "authority identity database validation failed"
        ) from None


def rebind_staging_provider_authority_identity(
    *,
    doctor_id: TypeIDType,
    appointment_id: TypeIDType,
    actor_id: TypeIDType,
    operator_ref: str,
    reason: str,
    expected_current_clerk_pair_sha256: str,
    expected_stale_subject_sha256: str,
    confirmed_plan_sha256: str,
    session: Session | None = None,
    settings: AuthorityKernelSettings = AUTHORITY_KERNEL_SETTINGS,
) -> AuthorityIdentityRebindResult:
    """Atomically replace only the reviewed stale active Clerk identity."""

    confirmed_plan = _confirmed_sha256(
        confirmed_plan_sha256,
        field="confirmed_plan_sha256",
    )
    if session is None:
        with get_session() as managed_session:
            return rebind_staging_provider_authority_identity(
                doctor_id=doctor_id,
                appointment_id=appointment_id,
                actor_id=actor_id,
                operator_ref=operator_ref,
                reason=reason,
                expected_current_clerk_pair_sha256=(expected_current_clerk_pair_sha256),
                expected_stale_subject_sha256=expected_stale_subject_sha256,
                confirmed_plan_sha256=confirmed_plan,
                session=cast(Session, managed_session),
                settings=settings,
            )

    try:
        evaluated = _evaluate(
            session,
            doctor_id=doctor_id,
            appointment_id=appointment_id,
            actor_id=actor_id,
            operator_ref=operator_ref,
            reason=reason,
            expected_current_clerk_pair_sha256=expected_current_clerk_pair_sha256,
            expected_stale_subject_sha256=expected_stale_subject_sha256,
            settings=settings,
            lock=True,
        )
        if not hmac.compare_digest(evaluated.plan.plan_sha256, confirmed_plan):
            raise AuthorityIdentityRebindError(
                "confirmed_plan_sha256 does not match the current reviewed plan"
            )
        if evaluated.plan.already_applied:
            session.commit()
            return AuthorityIdentityRebindResult(
                plan_sha256=evaluated.plan.plan_sha256,
                changed=False,
            )

        now = datetime.now(UTC)
        evaluated.stale_identity.revoked_at = now
        session.add(evaluated.stale_identity)
        session.add(
            AuthorityActorExternalIdentity(
                actor_id=evaluated.scope.actor.id,
                issuer=settings.clerk_issuer,
                subject=evaluated.current_clerk_identity.clerk_id,
                verified_at=now,
            )
        )
        session.add(
            AuthorityAuditEvent(
                event_source="platform_authoritative",
                event_type=AUTHORITY_IDENTITY_REBIND_EVENT_TYPE,
                outcome="accepted",
                occurred_at=now,
                recorded_at=now,
                tenant_id=evaluated.scope.tenant.id,
                actor_id=evaluated.scope.actor.id,
                person_id=evaluated.scope.person.id,
                resource_type=AUTHORITY_IDENTITY_REBIND_RESOURCE_TYPE,
                resource_ref=_receipt_resource_ref(evaluated.plan.plan_sha256),
                request_digest=evaluated.plan.plan_sha256,
                platform_refs=[],
            )
        )
        session.commit()
    except AuthorityIdentityRebindError:
        try:
            session.rollback()
        except Exception:
            pass
        raise
    except SQLAlchemyError:
        try:
            session.rollback()
        except Exception:
            pass
        raise AuthorityIdentityRebindError(
            "authority identity database operation failed"
        ) from None
    return AuthorityIdentityRebindResult(
        plan_sha256=evaluated.plan.plan_sha256,
        changed=True,
    )
