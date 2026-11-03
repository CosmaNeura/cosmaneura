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
from app.models.synthetic_preview_appointment_fixture import (
    SYNTHETIC_PREVIEW_FIXTURE_VERSION,
    SyntheticPreviewAppointmentFixture,
)
from sqlalchemy import or_
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
    if doctor.deleted_at is not None or not doctor_access_in_force(
        doctor, at=now, purpose="read"
    ):
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


def _current_session_prep_practice(
    session: Session, doctor: Doctor
) -> Practice | Doctor:
    """Resolve a provider namespace without inventing practice or legal state.

    Existing practices retain their immutable provider namespace. An active
    account without a practice owns a separate private namespace keyed by its
    Doctor ID. Neither path changes Scribe's practice policy.
    """
    if not configuration.SESSION_PREP_PROVIDER_SETTINGS.allows(doctor.clerk_id):
        raise SessionPrepProviderScopeUnavailable
    decision = resolve_session_prep_provider_access(doctor, session=session)
    if not decision.allowed:
        raise SessionPrepProviderScopeUnavailable
    return decision.practice if decision.practice is not None else doctor


def _namespace_mapping_predicate(namespace: Practice | Doctor):
    if isinstance(namespace, Doctor):
        return (col(ProviderPracticeAuthority.doctor_id) == namespace.id) & col(
            ProviderPracticeAuthority.practice_id
        ).is_(None)
    return (col(ProviderPracticeAuthority.practice_id) == namespace.id) & col(
        ProviderPracticeAuthority.doctor_id
    ).is_(None)


def _scribe_mapping_owner_predicate(*, practice_id, doctor_id):
    """An existing Scribe practice or this exact account's retained namespace.

    Callers still require the real current practice and unchanged Scribe access.
    This predicate does not provision an account namespace or confer access.
    """
    return (
        (col(ProviderPracticeAuthority.practice_id) == practice_id)
        & col(ProviderPracticeAuthority.doctor_id).is_(None)
    ) | (
        (col(ProviderPracticeAuthority.doctor_id) == doctor_id)
        & col(ProviderPracticeAuthority.practice_id).is_(None)
    )


def _mapping_matches_namespace(
    mapping, namespace: Practice | Doctor, *, doctor: Doctor | None = None
) -> bool:
    if isinstance(namespace, Doctor):
        return (
            mapping.practice_id is None
            and getattr(mapping, "doctor_id", None) == namespace.id
        )
    if getattr(mapping, "doctor_id", None) is not None:
        # Scribe may reuse an account namespace created by Session Prep, but
        # its caller must already have passed the unchanged practice policy.
        return (
            doctor is not None
            and mapping.practice_id is None
            and mapping.doctor_id == doctor.id
        )
    return (
        mapping.practice_id == namespace.id
        and getattr(mapping, "doctor_id", None) is None
    )


def _mapping_allows_owned_session_prep(mapping, doctor: Doctor) -> bool:
    """Retain exact old record bindings without relying on practice eligibility.

    A practice namespace can contain records for multiple providers, so this
    check is never authority by itself. Its caller also verifies the immutable
    record's Doctor, patient, appointment, actor and person provenance. Private
    account namespaces additionally require the exact Doctor owner.
    """
    owner = getattr(mapping, "doctor_id", None)
    if owner is not None:
        return mapping.practice_id is None and owner == doctor.id
    return mapping.practice_id is not None


def _namespace_mapping_fields(namespace: Practice | Doctor) -> dict[str, Any]:
    if isinstance(namespace, Doctor):
        return {"doctor_id": namespace.id, "practice_id": None}
    return {"practice_id": namespace.id, "doctor_id": None}


def _mapping_has_owned_session_prep_lineage(
    *, session: Session, mapping, namespace: Practice | Doctor, doctor: Doctor, actor
) -> bool:
    """New exchanges may reuse only a verified account/current/retained namespace."""
    if mapping.doctor_id is not None:
        return mapping.practice_id is None and mapping.doctor_id == doctor.id
    if isinstance(namespace, Practice) and mapping.practice_id == namespace.id:
        return True
    # A retained practice namespace survives later roster changes only when this
    # exact account already has a real owned, live record in it. A guessed active
    # provider membership alone cannot enroll a foreign practice or import scope.
    owned = session.exec(
        select(ProviderRecordAuthority, AuthorityPersonBinding)
        .join(
            AuthorityPersonBinding,
            col(AuthorityPersonBinding.id) == ProviderRecordAuthority.binding_id,
        )
        .where(
            ProviderRecordAuthority.practice_authority_id == mapping.id,
            ProviderRecordAuthority.doctor_id == doctor.id,
            AuthorityPersonBinding.actor_id == actor.id,
            AuthorityPersonBinding.tenant_id == mapping.tenant_id,
            AuthorityPersonBinding.status == "active",
            col(AuthorityPersonBinding.revoked_at).is_(None),
            col(AuthorityPersonBinding.verified_at).is_not(None),
        )
        .limit(1)
        .execution_options(populate_existing=True)
    ).first()
    if owned is None:
        return False
    _scope, binding = owned
    return (
        validate_provider_record_scope(
            session=session,
            binding=binding,
            actor_id=actor.id,
            clerk_subject=doctor.clerk_id,
        )
        is not None
    )


def _session_prep_namespace_mapping(
    *, session: Session, doctor: Doctor, namespace: Practice | Doctor, actor
):
    """Reuse verified provider tenancy; never replace a Scribe membership.

    The exact account and owned patient are already locked by the exchange.
    A single active provider membership can be reused only when its immutable
    mapping is a provider namespace belonging to this account's record scope.
    Fixture/import and ambiguous memberships are never auto-promoted.
    """
    active = session.exec(
        select(AuthorityTenantMembership)
        .where(
            AuthorityTenantMembership.actor_id == actor.id,
            AuthorityTenantMembership.status == "active",
        )
        .with_for_update()
        .limit(2)
        .execution_options(populate_existing=True)
    ).all()
    if len(active) > 1 or any(row.membership_kind != "provider" for row in active):
        raise ProviderRecordScopeDenied
    if active:
        member = active[0]
        mapping = session.exec(
            select(ProviderPracticeAuthority)
            .where(ProviderPracticeAuthority.tenant_id == member.tenant_id)
            .execution_options(populate_existing=True)
        ).one_or_none()
        tenant = session.get(AuthorityTenant, member.tenant_id, populate_existing=True)
        if (
            mapping is None
            or not _mapping_allows_owned_session_prep(mapping, doctor)
            or not _mapping_has_owned_session_prep_lineage(
                session=session,
                mapping=mapping,
                namespace=namespace,
                doctor=doctor,
                actor=actor,
            )
            or tenant is None
            or tenant.status != "active"
        ):
            raise ProviderRecordScopeDenied
        return mapping, tenant, member, active

    mapping = session.exec(
        select(ProviderPracticeAuthority)
        .where(_namespace_mapping_predicate(namespace))
        .execution_options(populate_existing=True)
    ).one_or_none()
    if mapping is None:
        tenant = AuthorityTenant(status="active")
        session.add(tenant)
        session.flush()
        mapping = ProviderPracticeAuthority(
            **_namespace_mapping_fields(namespace), tenant_id=tenant.id
        )
        session.add(mapping)
        session.flush()
        return mapping, tenant, None, active
    tenant = session.get(AuthorityTenant, mapping.tenant_id, populate_existing=True)
    member = session.exec(
        select(AuthorityTenantMembership)
        .where(
            AuthorityTenantMembership.actor_id == actor.id,
            AuthorityTenantMembership.tenant_id == mapping.tenant_id,
        )
        .execution_options(populate_existing=True)
    ).one_or_none()
    if tenant is None or tenant.status != "active" or member is not None:
        # An inactive membership is an explicit authority tombstone, not a
        # missing feature entitlement; the global policy must not revive it.
        raise ProviderRecordScopeDenied
    return mapping, tenant, None, active


def _require_existing_session_prep_membership(
    *, session: Session, doctor: Doctor, actor, binding
) -> None:
    """Existing records retain their original live authority, not just lineage."""
    members = session.exec(
        select(AuthorityTenantMembership)
        .where(
            AuthorityTenantMembership.actor_id == actor.id,
            AuthorityTenantMembership.status == "active",
        )
        .with_for_update()
        .limit(2)
        .execution_options(populate_existing=True)
    ).all()
    tenant = session.get(AuthorityTenant, binding.tenant_id, populate_existing=True)
    mapping = session.exec(
        select(ProviderPracticeAuthority)
        .where(ProviderPracticeAuthority.tenant_id == binding.tenant_id)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if (
        len(members) != 1
        or members[0].membership_kind != "provider"
        or members[0].tenant_id != binding.tenant_id
        or tenant is None
        or tenant.status != "active"
        or mapping is None
        or not _mapping_allows_owned_session_prep(mapping, doctor)
    ):
        raise ProviderRecordScopeDenied


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
        session_prep=session_prep,
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
    *,
    session,
    scope,
    doctor,
    practice,
    binding,
    actor_id,
    current,
    session_prep: bool = False,
):
    """Exact-row checks shared by ordinary reads and bounded catalog inputs."""
    mapping, appointment, patient, person = current
    if (
        mapping is None
        or mapping.id != scope.practice_authority_id
        or not (
            _mapping_allows_owned_session_prep(mapping, doctor)
            if session_prep
            else _mapping_matches_namespace(mapping, practice, doctor=doctor)
        )
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
    appointment = session.get(
        Appointment, cast(TypeIDType, appointment_id), populate_existing=True
    )
    patient = (
        session.get(Patient, appointment.patient_id, populate_existing=True)
        if appointment is not None
        else None
    )
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
    fixture = session.exec(
        select(SyntheticPreviewAppointmentFixture).where(
            SyntheticPreviewAppointmentFixture.appointment_id == appointment_id,
            SyntheticPreviewAppointmentFixture.doctor_id == doctor.id,
            SyntheticPreviewAppointmentFixture.fixture_version
            == SYNTHETIC_PREVIEW_FIXTURE_VERSION,
        )
    ).one_or_none()
    provisioned = session.exec(
        select(AuthorityAuditEvent).where(
            AuthorityAuditEvent.event_type == "authority.provider_fixture.provisioned",
            AuthorityAuditEvent.event_source == "platform_authoritative",
            AuthorityAuditEvent.outcome == "accepted",
            AuthorityAuditEvent.actor_id == actor_id,
            AuthorityAuditEvent.tenant_id == binding.tenant_id,
            AuthorityAuditEvent.person_id == binding.person_id,
            AuthorityAuditEvent.resource_type == "appointment",
            AuthorityAuditEvent.resource_ref == binding.binding_ref,
        )
    ).one_or_none()
    if fixture is not None and provisioned is not None:
        if (
            doctor.deleted_at is not None
            or doctor.last_active_at is None
            or appointment is None
            or patient is None
            or person is None
            or origin is None
            or fixture.synthetic_only is not True
            or fixture.patient_id != patient.id
            or appointment.doctor_id != doctor.id
            or appointment.patient_id != patient.id
            or patient.doctor_id != doctor.id
            or binding.actor_id is not None
            or binding.binding_kind != "provider_record"
            or binding.status != "active"
            or binding.verified_at is None
            or binding.revoked_at is not None
            or person.status != "active"
        ):
            raise ProviderRecordScopeDenied
        return
    practice = (
        _current_session_prep_practice(session, doctor)
        if session_prep
        else _current_practice(session, doctor)
    )
    mapping = session.exec(
        select(ProviderPracticeAuthority)
        .where(ProviderPracticeAuthority.tenant_id == binding.tenant_id)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if (
        appointment is None
        or patient is None
        or mapping is None
        or (
            not (
                _mapping_allows_owned_session_prep(mapping, doctor)
                if session_prep
                else _mapping_matches_namespace(mapping, practice, doctor=doctor)
            )
        )
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
) -> tuple[Doctor, Practice | Doctor]:
    """Lock the active account and any existing practice namespace."""
    return _lock_provider_record_owner(
        session=session,
        clerk_subject=clerk_subject,
        session_prep=True,
    )


def _lock_provider_record_owner(
    *, session: Session, clerk_subject: str, session_prep: bool
) -> tuple[Doctor, Practice | Doctor]:
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
    if session_prep and isinstance(practice, Doctor):
        # Accounts with no practice have no Practice row to lock. Recheck the
        # exact account after its row lock so activation/deletion or a newly
        # assigned namespace cannot race provision of a private tenant.
        locked = session.exec(
            select(Doctor)
            .where(Doctor.clerk_id == clerk_subject)
            .with_for_update(of=Doctor)
            .execution_options(populate_existing=True)
        ).one_or_none()
        if locked is None or current_practice(session, locked).id != practice.id:
            raise ProviderRecordScopeDenied
        return locked, locked
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


def _reconcile_ordinary_session_prep_scope(
    *,
    session,
    binding,
    doctor,
    practice,
    actor,
    appointment_id,
    clerk_issuer,
    clerk_subject,
) -> ProviderRecordAuthority:
    """Add only a missing scope during a verified, current owned exchange.

    Missing scope is not validation evidence. Recheck ordinary provenance under
    the existing Practice/Doctor/Patient fences and serialize the binding;
    fixture/import/partial authority must never become ordinary patient access.
    """
    binding = session.exec(
        select(AuthorityPersonBinding)
        .where(AuthorityPersonBinding.id == binding.id)
        .with_for_update(of=AuthorityPersonBinding)
        .execution_options(populate_existing=True)
    ).one()
    existing = session.exec(
        select(ProviderRecordAuthority)
        .where(ProviderRecordAuthority.binding_id == binding.id)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if existing is not None:
        validated = validate_provider_record_scope(
            session=session,
            binding=binding,
            actor_id=actor.id,
            clerk_subject=clerk_subject,
        )
        if validated is None:
            raise SessionPrepProviderScopeUnavailable
        return validated
    appointment = session.exec(
        select(Appointment)
        .where(Appointment.id == appointment_id)
        .with_for_update(of=Appointment)
        .execution_options(populate_existing=True)
    ).one_or_none()
    patient = (
        session.exec(
            select(Patient)
            .where(Patient.id == appointment.patient_id)
            .with_for_update(of=Patient)
            .execution_options(populate_existing=True)
        ).one_or_none()
        if appointment is not None
        else None
    )
    mapping = session.exec(
        select(ProviderPracticeAuthority)
        .where(ProviderPracticeAuthority.tenant_id == binding.tenant_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    ).one_or_none()
    tenant = (
        session.get(AuthorityTenant, binding.tenant_id, populate_existing=True)
        if binding.tenant_id is not None
        else None
    )
    person = session.get(AuthorityPerson, binding.person_id, populate_existing=True)
    identity = session.exec(
        select(AuthorityActorExternalIdentity)
        .where(
            AuthorityActorExternalIdentity.issuer == clerk_issuer,
            AuthorityActorExternalIdentity.subject == clerk_subject,
        )
        .execution_options(populate_existing=True)
    ).one_or_none()
    current_actor = session.get(AuthorityActor, actor.id, populate_existing=True)
    members = session.exec(
        select(AuthorityTenantMembership)
        .where(
            AuthorityTenantMembership.actor_id == actor.id,
            AuthorityTenantMembership.status == "active",
        )
        .with_for_update()
        .limit(2)
        .execution_options(populate_existing=True)
    ).all()
    origins = session.exec(
        select(AuthorityPersonOrigin)
        .where(AuthorityPersonOrigin.person_id == binding.person_id)
        .limit(2)
    ).all()
    fixture = (
        session.exec(
            select(col(SyntheticPreviewAppointmentFixture.id)).where(
                SyntheticPreviewAppointmentFixture.appointment_id == appointment.id
            )
        ).first()
        if appointment is not None
        else None
    )
    fixture_audit = session.exec(
        select(col(AuthorityAuditEvent.id)).where(
            AuthorityAuditEvent.event_type == "authority.provider_fixture.provisioned",
            or_(
                AuthorityAuditEvent.resource_ref == binding.binding_ref,
                AuthorityAuditEvent.person_id == binding.person_id,
            ),
        )
    ).first()
    if (
        _current_session_prep_practice(session, doctor).id != practice.id
        or doctor.clerk_id != clerk_subject
        or appointment is None
        or patient is None
        or appointment.doctor_id != doctor.id
        or patient.doctor_id != doctor.id
        or mapping is None
        or not _mapping_allows_owned_session_prep(mapping, doctor)
        or mapping.tenant_id != binding.tenant_id
        or tenant is None
        or tenant.status != "active"
        or current_actor is None
        or current_actor.actor_kind != "human"
        or current_actor.status != "active"
        or identity is None
        or identity.actor_id != actor.id
        or identity.verified_at is None
        or identity.revoked_at is not None
        or binding.actor_id != actor.id
        or binding.binding_kind != "provider_record"
        or binding.binding_ref != str(appointment.id)
        or binding.status != "active"
        or binding.verified_at is None
        or binding.revoked_at is not None
        or person is None
        or person.lifecycle_state != "patient"
        or person.status != "active"
        or len(members) != 1
        or members[0].tenant_id != binding.tenant_id
        or members[0].membership_kind != "provider"
        or len(origins) != 1
        or origins[0].origin != "provider_entry"
        or origins[0].source_ref != str(patient.id)
        or fixture is not None
        or fixture_audit is not None
    ):
        raise SessionPrepProviderScopeUnavailable
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
            occurred_at=datetime.now(UTC),
            tenant_id=binding.tenant_id,
            actor_id=actor.id,
            person_id=binding.person_id,
            resource_type="provider_record",
            resource_ref=str(binding.id),
            request_digest=hashlib.sha256(
                f"{doctor.id}:{appointment.id}".encode()
            ).hexdigest(),
        )
    )
    session.flush()
    validated = validate_provider_record_scope(
        session=session, binding=binding, actor_id=actor.id, clerk_subject=clerk_subject
    )
    if validated is None:
        raise SessionPrepProviderScopeUnavailable
    return validated


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

    if session_prep:
        existing_binding = session.exec(
            select(AuthorityPersonBinding)
            .where(AuthorityPersonBinding.binding_ref == record_ref)
            .execution_options(populate_existing=True)
        ).one_or_none()
        if existing_binding is not None:
            _require_existing_session_prep_membership(
                session=session, doctor=doctor, actor=actor, binding=existing_binding
            )
            existing_scope = session.exec(
                select(ProviderRecordAuthority)
                .where(ProviderRecordAuthority.binding_id == existing_binding.id)
                .execution_options(populate_existing=True)
            ).one_or_none()
            if existing_scope is not None:
                scope = validate_provider_record_scope(
                    session=session,
                    binding=existing_binding,
                    actor_id=actor.id,
                    clerk_subject=clerk_subject,
                )
                if scope is None:
                    raise SessionPrepProviderScopeUnavailable
                return scope
            return _reconcile_ordinary_session_prep_scope(
                session=session,
                binding=existing_binding,
                doctor=doctor,
                practice=practice,
                actor=actor,
                appointment_id=appointment.id,
                clerk_issuer=clerk_issuer,
                clerk_subject=clerk_subject,
            )
        mapping, tenant, membership, active_memberships = (
            _session_prep_namespace_mapping(
                session=session, doctor=doctor, namespace=practice, actor=actor
            )
        )
    else:
        # A real-practice Scribe caller may already own a private namespace
        # provisioned by Session Prep before onboarding. Reuse it unchanged;
        # otherwise preserve the established practice-backed Scribe path.
        mapping = session.exec(
            select(ProviderPracticeAuthority).where(
                col(ProviderPracticeAuthority.doctor_id) == doctor.id,
                col(ProviderPracticeAuthority.practice_id).is_(None),
            )
        ).one_or_none()
        if mapping is None:
            mapping = session.exec(
                select(ProviderPracticeAuthority).where(
                    _namespace_mapping_predicate(practice)
                )
            ).one_or_none()
        if mapping is None:
            tenant = AuthorityTenant(status="active")
            session.add(tenant)
            session.flush()
            mapping = ProviderPracticeAuthority(
                **_namespace_mapping_fields(practice), tenant_id=tenant.id
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
        if mapping.doctor_id is not None and (
            membership is None
            or len(active_memberships) != 1
            or active_memberships[0].id != membership.id
        ):
            # Scribe only preserves a fully provisioned personal namespace. It
            # cannot create/repair one or revoke a competing provider membership
            # to make the account-owned path usable.
            raise ProviderRecordScopeDenied
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
    if session_prep:
        legacy_binding = session.exec(
            select(AuthorityPersonBinding)
            .outerjoin(
                ProviderRecordAuthority,
                col(ProviderRecordAuthority.binding_id) == AuthorityPersonBinding.id,
            )
            .where(
                AuthorityPersonBinding.binding_ref == record_ref,
                col(ProviderRecordAuthority.id).is_(None),
            )
        ).one_or_none()
        if legacy_binding is not None:
            return _reconcile_ordinary_session_prep_scope(
                session=session,
                binding=legacy_binding,
                doctor=doctor,
                practice=practice,
                actor=actor,
                appointment_id=appointment.id,
                clerk_issuer=clerk_issuer,
                clerk_subject=clerk_subject,
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
            if session_prep:
                raise SessionPrepProviderScopeUnavailable
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
