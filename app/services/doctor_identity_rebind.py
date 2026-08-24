"""Audited recovery when a provider recreates their Clerk sign-in.

Clinical, practice, and legal records stay attached to the original ``Doctor``
row.  The operation only replaces its external Clerk subject after the old
subject is proven absent and the replacement's verified primary email matches.
Any empty Doctor row created by the replacement's first login is retained as a
soft-deleted tombstone rather than being destroyed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from clerk_backend_api.models import ClerkErrors

from app.configuration.authority import AUTHORITY_KERNEL_SETTINGS
from app.configuration.clerk import clerk
from app.environments import python_environment
from app.services.clerk_cohort import ClerkCohortIdentity
from app.services.pilot_access_lock import lock_pilot_access_lineage

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.authority import (
    AuthorityActor,
    AuthorityActorExternalIdentity,
    AuthorityAuditEvent,
    AuthorityTenant,
    AuthorityTenantMembership,
)
from app.models.doctor import CLERK_OBJECT_PREFIX, Doctor, DoctorRole, Terminology
from app.models.pilot_access_grandfather_grant import PilotAccessGrandfatherGrant
from app.models.practice import PracticeMember
from app.models.practice_access_audit import PracticeAccessAction, PracticeAccessAudit
from app.models.superadmin_capability import SuperadminCapabilityGrant
from app.models.view_as import ViewAsSession
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, SQLModel, col, select

IDENTITY_REBIND_RETENTION_YEARS = 7
IDENTITY_REBIND_EVENT_TYPE = "platform.doctor_identity.rebound"
IDENTITY_REBIND_RESOURCE_TYPE = "doctor_identity_rebind"
IDENTITY_REBIND_ROUTE = "cli:rebind-doctor-clerk-identity"
IDENTITY_REBIND_SCHEMA_VERSION = 2
_SUPPORTED_ENVIRONMENTS = frozenset({"development", "test", "staging", "production"})


class DoctorIdentityRebindError(ValueError):
    """A privacy-safe refusal that never contains an email or Clerk subject."""


@dataclass(frozen=True)
class DoctorIdentityRebindPlan:
    plan_sha256: str
    doctor_id: TypeIDType
    practice_id: TypeIDType | None
    environment: str
    clerk_issuer_sha256: str
    provisional_doctor_id: TypeIDType | None
    pilot_grant_rebound: bool
    authority_identity_count: int
    already_applied: bool


@dataclass(frozen=True)
class DoctorIdentityRebindResult:
    plan_sha256: str
    doctor_id: TypeIDType
    changed: bool


@dataclass(frozen=True)
class _AuthorityIdentityScope:
    identity: AuthorityActorExternalIdentity
    tenant_id: TypeIDType
    membership_id: TypeIDType


@dataclass(frozen=True)
class _IdentityProvenance:
    environment: str
    clerk_issuer: str
    clerk_issuer_sha256: str


@dataclass(frozen=True)
class _EvaluatedRebind:
    plan: DoctorIdentityRebindPlan
    doctor: Doctor
    member: PracticeMember | None
    provisional: Doctor | None
    pilot_grant: PilotAccessGrandfatherGrant | None
    authority_scopes: tuple[_AuthorityIdentityScope, ...]
    receipt: AuthorityAuditEvent | None
    practice_receipt: PracticeAccessAudit | None


def _required(value: str, *, field: str, limit: int) -> str:
    normalized = value.strip()
    if not normalized or normalized != value or len(normalized) > limit:
        raise DoctorIdentityRebindError(f"{field} is invalid")
    return normalized


def _normalize_clerk_id(value: str) -> str:
    normalized = _required(value, field="Clerk subject", limit=255)
    if not normalized.startswith(CLERK_OBJECT_PREFIX) or any(
        character.isspace() for character in normalized
    ):
        raise DoctorIdentityRebindError("Clerk subject is invalid")
    return normalized


def _normalize_email(value: str) -> str:
    normalized = value.strip().lower()
    if (
        normalized != value
        or not normalized
        or len(normalized) > 320
        or normalized.count("@") != 1
        or not normalized.rsplit("@", 1)[0]
        or "." not in normalized.rsplit("@", 1)[1]
    ):
        raise DoctorIdentityRebindError("account email confirmation is invalid")
    return normalized


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _identity_provenance() -> _IdentityProvenance:
    environment = python_environment().strip().lower()
    if environment not in _SUPPORTED_ENVIRONMENTS:
        raise DoctorIdentityRebindError("runtime environment is invalid")
    settings = AUTHORITY_KERNEL_SETTINGS
    if not settings.has_valid_clerk_issuer():
        raise DoctorIdentityRebindError("configured Clerk issuer is unavailable")
    return _IdentityProvenance(
        environment=environment,
        clerk_issuer=settings.clerk_issuer,
        clerk_issuer_sha256=_sha256(settings.clerk_issuer),
    )


def _seven_years_after(value: datetime) -> datetime:
    try:
        return value.replace(year=value.year + IDENTITY_REBIND_RETENTION_YEARS)
    except ValueError:
        return value.replace(
            year=value.year + IDENTITY_REBIND_RETENTION_YEARS,
            month=2,
            day=28,
        )


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


def resolve_live_rebind_identity(
    *,
    stale_clerk_id: str,
    replacement_clerk_id: str,
    confirmed_email: str,
) -> ClerkCohortIdentity:
    """Prove the predecessor is gone and the replacement owns the same email."""

    stale = _normalize_clerk_id(stale_clerk_id)
    replacement = _normalize_clerk_id(replacement_clerk_id)
    email = _normalize_email(confirmed_email)
    if stale == replacement:
        raise DoctorIdentityRebindError("replacement identity must be different")

    try:
        user = clerk.users.get(user_id=replacement)
    except Exception:
        raise DoctorIdentityRebindError(
            "the replacement Clerk identity could not be verified"
        ) from None
    if (
        user is None
        or getattr(user, "id", None) != replacement
        or getattr(user, "banned", None) is not False
        or getattr(user, "locked", None) is not False
    ):
        raise DoctorIdentityRebindError(
            "the replacement Clerk identity could not be verified"
        )
    primary_id = getattr(user, "primary_email_address_id", None)
    addresses = getattr(user, "email_addresses", None)
    primary = (
        [item for item in addresses if getattr(item, "id", None) == primary_id]
        if isinstance(primary_id, str) and addresses
        else []
    )
    if (
        len(primary) != 1
        or getattr(getattr(primary[0], "verification", None), "status", None)
        != "verified"
    ):
        raise DoctorIdentityRebindError(
            "the replacement Clerk identity could not be verified"
        )
    live_email = getattr(primary[0], "email_address", None)
    if not isinstance(live_email, str) or not hmac.compare_digest(
        live_email.strip().lower(), email
    ):
        raise DoctorIdentityRebindError(
            "the replacement Clerk identity could not be verified"
        )

    try:
        predecessor = clerk.users.get(user_id=stale)
    except Exception as exc:
        if _is_exact_clerk_resource_not_found(exc):
            predecessor = None
        else:
            raise DoctorIdentityRebindError(
                "the prior Clerk identity absence could not be proven"
            ) from None
    if predecessor is not None:
        raise DoctorIdentityRebindError("the prior Clerk identity still exists")
    return ClerkCohortIdentity(clerk_id=replacement, email=email)


def _lock_subject(session: Session, subject: str) -> None:
    lock_key = int.from_bytes(
        hashlib.sha256(f"doctor-identity:{subject}".encode()).digest()[:8],
        byteorder="big",
        signed=True,
    )
    session.exec(select(func.pg_advisory_xact_lock(lock_key))).one()


def _provisional_has_references(session: Session, doctor_id: TypeIDType) -> bool:
    """Fail closed if any persisted object points at the replacement shell."""

    doctor_table = SQLModel.metadata.tables[cast(str, Doctor.__tablename__)]
    for table in SQLModel.metadata.sorted_tables:
        columns = [
            foreign_key.parent
            for foreign_key in table.foreign_keys
            if foreign_key.column.table is doctor_table
            and foreign_key.column.name == "id"
        ]
        if not columns:
            continue
        count = session.exec(
            select(func.count())
            .select_from(table)
            .where(or_(*(column == doctor_id for column in columns)))
        ).one()
        if int(count) > 0:
            return True
    return False


def _tombstone_subject(doctor_id: TypeIDType, replacement_subject: str) -> str:
    digest = _sha256(f"{doctor_id}:{replacement_subject}")
    return f"user_rebound_{digest}"


def _receipt_ref(doctor_id: TypeIDType, replacement_subject: str) -> str:
    return f"doctor-identity-rebind:{doctor_id}:{_sha256(replacement_subject)[:24]}"


def _authority_scopes(
    session: Session,
    *,
    stale_subject: str,
    replacement_subject: str,
    clerk_issuer: str,
    lock: bool,
) -> tuple[_AuthorityIdentityScope, ...]:
    """Resolve only the configured Clerk issuer and prove its actor tenancy."""

    subject_statement = select(AuthorityActorExternalIdentity).where(
        col(AuthorityActorExternalIdentity.subject).in_(
            [stale_subject, replacement_subject]
        )
    )
    if lock:
        subject_statement = subject_statement.with_for_update()
    subject_rows = list(session.exec(subject_statement).all())
    if any(row.issuer != clerk_issuer for row in subject_rows):
        raise DoctorIdentityRebindError(
            "authority identity state has an unexpected Clerk issuer"
        )
    issuer_rows = [row for row in subject_rows if row.issuer == clerk_issuer]
    if any(row.subject == replacement_subject for row in issuer_rows):
        raise DoctorIdentityRebindError("replacement authority identity already exists")
    stale_active = [
        row
        for row in issuer_rows
        if row.subject == stale_subject and row.revoked_at is None
    ]
    if len(stale_active) > 1:
        raise DoctorIdentityRebindError("authority identity state is ambiguous")
    if not stale_active:
        return ()

    identity = stale_active[0]
    actor_statement = select(AuthorityActor).where(
        AuthorityActor.id == identity.actor_id
    )
    if lock:
        actor_statement = actor_statement.with_for_update()
    actor = session.exec(actor_statement).one_or_none()
    if actor is None or actor.actor_kind != "human" or actor.status != "active":
        raise DoctorIdentityRebindError("authority actor state is invalid")

    actor_identities_statement = select(AuthorityActorExternalIdentity).where(
        AuthorityActorExternalIdentity.actor_id == actor.id
    )
    if lock:
        actor_identities_statement = actor_identities_statement.with_for_update()
    actor_identities = list(session.exec(actor_identities_statement).all())
    if any(row.issuer != clerk_issuer for row in actor_identities):
        raise DoctorIdentityRebindError(
            "the authority actor has an identity from an unexpected issuer"
        )
    active_actor_identities = [
        row for row in actor_identities if row.revoked_at is None
    ]
    if (
        len(active_actor_identities) != 1
        or active_actor_identities[0].id != identity.id
    ):
        raise DoctorIdentityRebindError("authority identity state is ambiguous")

    membership_statement = select(AuthorityTenantMembership).where(
        AuthorityTenantMembership.actor_id == actor.id,
        AuthorityTenantMembership.membership_kind == "provider",
        AuthorityTenantMembership.status == "active",
    )
    if lock:
        membership_statement = membership_statement.with_for_update()
    memberships = list(session.exec(membership_statement).all())
    if len(memberships) != 1:
        raise DoctorIdentityRebindError("authority tenant scope is ambiguous")
    membership = memberships[0]
    tenant_statement = select(AuthorityTenant).where(
        AuthorityTenant.id == membership.tenant_id
    )
    if lock:
        tenant_statement = tenant_statement.with_for_update()
    tenant = session.exec(tenant_statement).one_or_none()
    if tenant is None or tenant.status != "active":
        raise DoctorIdentityRebindError("authority tenant scope is invalid")
    return (
        _AuthorityIdentityScope(
            identity=identity,
            tenant_id=tenant.id,
            membership_id=membership.id,
        ),
    )


def _require_expected_authority_subject_issuers(
    session: Session,
    *,
    subjects: tuple[str, str],
    clerk_issuer: str,
    lock: bool,
) -> None:
    statement = select(AuthorityActorExternalIdentity).where(
        col(AuthorityActorExternalIdentity.subject).in_(subjects)
    )
    if lock:
        statement = statement.with_for_update()
    rows = list(session.exec(statement).all())
    if any(row.issuer != clerk_issuer for row in rows):
        raise DoctorIdentityRebindError(
            "authority identity state has an unexpected Clerk issuer"
        )


def _plan_sha256(
    *,
    doctor: Doctor,
    member: PracticeMember | None,
    stale_subject: str,
    replacement: ClerkCohortIdentity,
    provisional: Doctor | None,
    pilot_grant: PilotAccessGrandfatherGrant | None,
    authority_scopes: tuple[_AuthorityIdentityScope, ...],
    provenance: _IdentityProvenance,
    operator_ref: str,
    reason: str,
) -> str:
    payload: dict[str, Any] = {
        "action": "doctor_clerk_identity_rebind",
        "authority_scopes": [
            {
                "actor_id": str(scope.identity.actor_id),
                "identity_id": str(scope.identity.id),
                "issuer_sha256": _sha256(scope.identity.issuer),
                "membership_id": str(scope.membership_id),
                "tenant_id": str(scope.tenant_id),
            }
            for scope in sorted(
                authority_scopes, key=lambda item: str(item.identity.id)
            )
        ],
        "clerk_issuer_sha256": provenance.clerk_issuer_sha256,
        "doctor_id": str(doctor.id),
        "email_sha256": _sha256(replacement.email),
        "environment": provenance.environment,
        "member_id": str(member.id) if member is not None else None,
        "operator_ref": operator_ref,
        "pilot_grant_id": str(pilot_grant.id) if pilot_grant else None,
        "practice_id": str(member.practice_id) if member is not None else None,
        "provisional_doctor_id": str(provisional.id) if provisional else None,
        "provisional_email_sha256": (
            _sha256(provisional.email.strip().lower())
            if provisional is not None and provisional.email
            else None
        ),
        "reason": reason,
        "replacement_subject_sha256": _sha256(replacement.clerk_id),
        "schema_version": IDENTITY_REBIND_SCHEMA_VERSION,
        "stale_subject_sha256": _sha256(stale_subject),
    }
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return _sha256(canonical)


def _is_sha256(value: str | None) -> bool:
    return bool(
        value
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _validate_identity_receipt(
    receipt: AuthorityAuditEvent,
    *,
    provenance: _IdentityProvenance,
    resource_ref: str,
) -> str:
    if (
        receipt.event_source != "platform_authoritative"
        or receipt.event_type != IDENTITY_REBIND_EVENT_TYPE
        or receipt.outcome != "accepted"
        or receipt.resource_type != IDENTITY_REBIND_RESOURCE_TYPE
        or receipt.resource_ref != resource_ref
        or receipt.route != IDENTITY_REBIND_ROUTE
        or receipt.intent != provenance.environment
        or receipt.content_sha256 != provenance.clerk_issuer_sha256
        or not _is_sha256(receipt.request_digest)
    ):
        raise DoctorIdentityRebindError("identity recovery audit state is invalid")
    return cast(str, receipt.request_digest)


def _validate_practice_receipt(
    receipt: PracticeAccessAudit,
    *,
    member: PracticeMember,
    identity_receipt: AuthorityAuditEvent,
    plan_sha256: str,
    operator_ref: str,
    reason: str,
    resource_ref: str,
) -> None:
    if (
        receipt.practice_id != member.practice_id
        or receipt.action != PracticeAccessAction.doctor_identity_rebound.value
        or receipt.operator_ref != operator_ref
        or receipt.reason != reason
        or receipt.effective_at != identity_receipt.occurred_at
        or receipt.expires_at is not None
        or receipt.document_reference != resource_ref
        or receipt.evidence_sha256 != plan_sha256
    ):
        raise DoctorIdentityRebindError("identity recovery audit state is invalid")


def _validate_applied_pilot_lineage(
    grants: list[PilotAccessGrandfatherGrant],
    *,
    doctor: Doctor,
    member: PracticeMember | None,
    stale_subject: str,
    replacement_subject: str,
    email: str,
    plan_sha256: str,
    operator_ref: str,
    reason: str,
    effective_at: datetime,
) -> bool:
    predecessors = [
        grant
        for grant in grants
        if grant.clerk_id == stale_subject
        and grant.revocation_plan_sha256 == plan_sha256
    ]
    successors = [
        grant
        for grant in grants
        if grant.clerk_id == replacement_subject
        and grant.grant_plan_sha256 == plan_sha256
    ]
    replacement_history = [
        grant for grant in grants if grant.clerk_id == replacement_subject
    ]
    if not predecessors and not successors:
        if replacement_history:
            raise DoctorIdentityRebindError("pilot authorization has conflicting state")
        return False
    if len(predecessors) != 1 or len(successors) != 1 or len(replacement_history) != 1:
        raise DoctorIdentityRebindError("pilot authorization has conflicting state")

    predecessor = predecessors[0]
    successor = successors[0]
    expected_practice_id = member.practice_id if member is not None else None
    if (
        predecessor.id == successor.id
        or predecessor.revoked_at != effective_at
        or predecessor.revoked_by_ref != operator_ref
        or predecessor.revocation_reason != reason
        or predecessor.retain_until is None
        or predecessor.retain_until < _seven_years_after(effective_at)
        or successor.revoked_at is not None
        or successor.granted_at != effective_at
        or successor.granted_by_ref != operator_ref
        or successor.grant_reason != reason
        or predecessor.source_ref != successor.source_ref
        or not hmac.compare_digest(predecessor.source_sha256, successor.source_sha256)
        or not hmac.compare_digest(predecessor.email_snapshot, email)
        or not hmac.compare_digest(successor.email_snapshot, email)
        or not hmac.compare_digest(predecessor.email_sha256, _sha256(email))
        or not hmac.compare_digest(successor.email_sha256, _sha256(email))
        or predecessor.doctor_id_snapshot not in (None, doctor.id)
        or successor.doctor_id_snapshot != doctor.id
        or predecessor.practice_id_snapshot not in (None, expected_practice_id)
        or successor.practice_id_snapshot != expected_practice_id
    ):
        raise DoctorIdentityRebindError("pilot authorization has conflicting state")
    return True


def _require_inactive_privileged_state(
    session: Session,
    *,
    doctor: Doctor,
    provisional: Doctor | None,
    stale_subject: str,
    replacement_subject: str,
    lock: bool,
) -> None:
    """Refuse a rebind that would strand live privileged authorization state."""

    doctor_ids = [doctor.id]
    if provisional is not None:
        doctor_ids.append(provisional.id)

    capability_statement = select(SuperadminCapabilityGrant).where(
        col(SuperadminCapabilityGrant.revoked_at).is_(None),
        or_(
            col(SuperadminCapabilityGrant.doctor_id).in_(doctor_ids),
            col(SuperadminCapabilityGrant.clerk_subject).in_(
                [stale_subject, replacement_subject]
            ),
        ),
    )
    if lock:
        capability_statement = capability_statement.with_for_update()
    if session.exec(capability_statement).first() is not None:
        raise DoctorIdentityRebindError(
            "active superadmin capabilities must be revoked before identity recovery"
        )

    view_as_statement = select(ViewAsSession).where(
        col(ViewAsSession.ended_at).is_(None),
        or_(
            col(ViewAsSession.actor_doctor_id).in_(doctor_ids),
            col(ViewAsSession.target_doctor_id).in_(doctor_ids),
        ),
    )
    if lock:
        view_as_statement = view_as_statement.with_for_update()
    if session.exec(view_as_statement).first() is not None:
        raise DoctorIdentityRebindError(
            "active view-as sessions must be ended before identity recovery"
        )


def _evaluate(
    session: Session,
    *,
    doctor_id: TypeIDType,
    stale_clerk_id: str,
    replacement: ClerkCohortIdentity,
    operator_ref: str,
    reason: str,
    lock: bool,
) -> _EvaluatedRebind:
    stale = _normalize_clerk_id(stale_clerk_id)
    new_subject = _normalize_clerk_id(replacement.clerk_id)
    email = _normalize_email(replacement.email)
    operator = _required(operator_ref, field="operator_ref", limit=255)
    explanation = _required(reason, field="reason", limit=512)
    provenance = _identity_provenance()
    if stale == new_subject:
        raise DoctorIdentityRebindError("replacement identity must be different")
    if lock:
        for subject in sorted((stale, new_subject)):
            _lock_subject(session, subject)

    statement = select(Doctor).where(Doctor.id == doctor_id)
    if lock:
        statement = statement.with_for_update()
    doctor = cast(Doctor | None, session.exec(statement).one_or_none())
    if doctor is None or doctor.deleted_at is not None or not doctor.email:
        raise DoctorIdentityRebindError("the retained provider account is unavailable")
    if not hmac.compare_digest(doctor.email.strip().lower(), email):
        raise DoctorIdentityRebindError(
            "the verified replacement email does not match the retained account"
        )

    member_statement = select(PracticeMember).where(
        PracticeMember.doctor_id == doctor.id
    )
    if lock:
        member_statement = member_statement.with_for_update()
    member = cast(PracticeMember | None, session.exec(member_statement).one_or_none())
    if member is not None and member.access_revoked_at is not None:
        raise DoctorIdentityRebindError(
            "the retained provider does not have active practice access"
        )

    _require_expected_authority_subject_issuers(
        session,
        subjects=(stale, new_subject),
        clerk_issuer=provenance.clerk_issuer,
        lock=lock,
    )

    receipt_ref = _receipt_ref(doctor.id, new_subject)
    identity_receipts = cast(
        list[AuthorityAuditEvent],
        list(
            session.exec(
                select(AuthorityAuditEvent).where(
                    AuthorityAuditEvent.event_type == IDENTITY_REBIND_EVENT_TYPE,
                    AuthorityAuditEvent.resource_type == IDENTITY_REBIND_RESOURCE_TYPE,
                    AuthorityAuditEvent.resource_ref == receipt_ref,
                )
            ).all()
        ),
    )
    practice_receipts = cast(
        list[PracticeAccessAudit],
        list(
            session.exec(
                select(PracticeAccessAudit).where(
                    PracticeAccessAudit.action
                    == PracticeAccessAction.doctor_identity_rebound.value,
                    PracticeAccessAudit.document_reference == receipt_ref,
                )
            ).all()
        ),
    )
    if len(identity_receipts) > 1 or len(practice_receipts) > 1:
        raise DoctorIdentityRebindError("identity recovery has conflicting audit state")
    receipt = identity_receipts[0] if identity_receipts else None
    practice_receipt = practice_receipts[0] if practice_receipts else None

    grant_statement = select(PilotAccessGrandfatherGrant).where(
        col(PilotAccessGrandfatherGrant.clerk_id).in_([stale, new_subject]),
    )
    if lock:
        grant_statement = grant_statement.with_for_update()
    grants = cast(
        list[PilotAccessGrandfatherGrant], list(session.exec(grant_statement).all())
    )

    if doctor.clerk_id == new_subject:
        if receipt is None:
            raise DoctorIdentityRebindError(
                "identity recovery is missing its audit evidence"
            )
        receipt_plan_sha256 = _validate_identity_receipt(
            receipt,
            provenance=provenance,
            resource_ref=receipt_ref,
        )
        if member is None:
            if practice_receipt is not None:
                raise DoctorIdentityRebindError(
                    "identity recovery has conflicting audit state"
                )
        else:
            if practice_receipt is None:
                raise DoctorIdentityRebindError(
                    "identity recovery is missing its practice audit evidence"
                )
            _validate_practice_receipt(
                practice_receipt,
                member=member,
                identity_receipt=receipt,
                plan_sha256=receipt_plan_sha256,
                operator_ref=operator,
                reason=explanation,
                resource_ref=receipt_ref,
            )
        pilot_grant_rebound = _validate_applied_pilot_lineage(
            grants,
            doctor=doctor,
            member=member,
            stale_subject=stale,
            replacement_subject=new_subject,
            email=email,
            plan_sha256=receipt_plan_sha256,
            operator_ref=operator,
            reason=explanation,
            effective_at=receipt.occurred_at,
        )
        if member is None and not pilot_grant_rebound:
            raise DoctorIdentityRebindError(
                "identity recovery is missing continuity authorization evidence"
            )
        return _EvaluatedRebind(
            plan=DoctorIdentityRebindPlan(
                plan_sha256=receipt_plan_sha256,
                doctor_id=doctor.id,
                practice_id=member.practice_id if member is not None else None,
                environment=provenance.environment,
                clerk_issuer_sha256=provenance.clerk_issuer_sha256,
                provisional_doctor_id=None,
                pilot_grant_rebound=pilot_grant_rebound,
                authority_identity_count=0,
                already_applied=True,
            ),
            doctor=doctor,
            member=member,
            provisional=None,
            pilot_grant=None,
            authority_scopes=(),
            receipt=receipt,
            practice_receipt=practice_receipt,
        )
    if doctor.clerk_id != stale:
        raise DoctorIdentityRebindError(
            "the retained provider is not bound to the confirmed prior identity"
        )
    if receipt is not None or practice_receipt is not None:
        raise DoctorIdentityRebindError("identity recovery has conflicting audit state")

    provisional_statement = select(Doctor).where(Doctor.clerk_id == new_subject)
    if lock:
        provisional_statement = provisional_statement.with_for_update()
    provisional = cast(Doctor | None, session.exec(provisional_statement).one_or_none())
    if provisional is not None:
        provisional_email_matches = bool(
            provisional.email
            and hmac.compare_digest(provisional.email.strip().lower(), email)
        )
        if (
            provisional.id == doctor.id
            or provisional.deleted_at is not None
            or (provisional.email and not provisional_email_matches)
            or provisional.role != DoctorRole.normal
            or provisional.api_key is not None
            or provisional.onboarding_completed_at is not None
            or provisional.biography not in (None, "")
            or provisional.has_natmed_subscription
            or provisional.terminology != Terminology.doctor_patient
            or _provisional_has_references(session, provisional.id)
        ):
            raise DoctorIdentityRebindError(
                "the replacement sign-in already owns application state"
            )

    _require_inactive_privileged_state(
        session,
        doctor=doctor,
        provisional=provisional,
        stale_subject=stale,
        replacement_subject=new_subject,
        lock=lock,
    )

    email_owners = session.exec(
        select(Doctor).where(func.lower(func.btrim(Doctor.email)) == email)
    ).all()
    allowed_email_owner_ids = {doctor.id}
    if provisional is not None:
        allowed_email_owner_ids.add(provisional.id)
    if doctor.id not in {owner.id for owner in email_owners} or any(
        owner.id not in allowed_email_owner_ids for owner in email_owners
    ):
        raise DoctorIdentityRebindError("the retained email ownership is ambiguous")

    new_grants = [grant for grant in grants if grant.clerk_id == new_subject]
    old_active_grants = [
        grant
        for grant in grants
        if grant.clerk_id == stale and grant.revoked_at is None
    ]
    if new_grants or len(old_active_grants) > 1:
        raise DoctorIdentityRebindError("pilot authorization has conflicting state")
    pilot_grant = old_active_grants[0] if old_active_grants else None
    if pilot_grant is not None and (
        pilot_grant.doctor_id_snapshot not in (None, doctor.id)
        or not hmac.compare_digest(pilot_grant.email_snapshot, email)
        or not hmac.compare_digest(pilot_grant.email_sha256, _sha256(email))
    ):
        raise DoctorIdentityRebindError(
            "pilot authorization does not match the account"
        )
    if member is None:
        if pilot_grant is None:
            raise DoctorIdentityRebindError(
                "the retained provider has neither active practice access nor "
                "continuity authorization"
            )
        if pilot_grant.practice_id_snapshot is not None:
            raise DoctorIdentityRebindError(
                "pilot authorization has conflicting practice state"
            )
    elif pilot_grant is not None and pilot_grant.practice_id_snapshot not in (
        None,
        member.practice_id,
    ):
        raise DoctorIdentityRebindError(
            "pilot authorization has conflicting practice state"
        )

    authority_scopes = _authority_scopes(
        session,
        stale_subject=stale,
        replacement_subject=new_subject,
        clerk_issuer=provenance.clerk_issuer,
        lock=lock,
    )

    plan_sha256 = _plan_sha256(
        doctor=doctor,
        member=member,
        stale_subject=stale,
        replacement=replacement,
        provisional=provisional,
        pilot_grant=pilot_grant,
        authority_scopes=authority_scopes,
        provenance=provenance,
        operator_ref=operator,
        reason=explanation,
    )
    return _EvaluatedRebind(
        plan=DoctorIdentityRebindPlan(
            plan_sha256=plan_sha256,
            doctor_id=doctor.id,
            practice_id=member.practice_id if member is not None else None,
            environment=provenance.environment,
            clerk_issuer_sha256=provenance.clerk_issuer_sha256,
            provisional_doctor_id=provisional.id if provisional else None,
            pilot_grant_rebound=pilot_grant is not None,
            authority_identity_count=len(authority_scopes),
            already_applied=False,
        ),
        doctor=doctor,
        member=member,
        provisional=provisional,
        pilot_grant=pilot_grant,
        authority_scopes=authority_scopes,
        receipt=None,
        practice_receipt=None,
    )


def plan_doctor_identity_rebind(
    *,
    doctor_id: TypeIDType,
    stale_clerk_id: str,
    replacement: ClerkCohortIdentity,
    operator_ref: str,
    reason: str,
    session: Session | None = None,
) -> DoctorIdentityRebindPlan:
    if session is None:
        with get_session() as managed:
            return plan_doctor_identity_rebind(
                doctor_id=doctor_id,
                stale_clerk_id=stale_clerk_id,
                replacement=replacement,
                operator_ref=operator_ref,
                reason=reason,
                session=cast(Session, managed),
            )
    try:
        return _evaluate(
            session,
            doctor_id=doctor_id,
            stale_clerk_id=stale_clerk_id,
            replacement=replacement,
            operator_ref=operator_ref,
            reason=reason,
            lock=False,
        ).plan
    except DoctorIdentityRebindError:
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
        raise DoctorIdentityRebindError(
            "identity recovery database validation failed"
        ) from None


def rebind_doctor_identity(
    *,
    doctor_id: TypeIDType,
    stale_clerk_id: str,
    replacement: ClerkCohortIdentity,
    operator_ref: str,
    reason: str,
    confirmed_plan_sha256: str,
    session: Session | None = None,
    now: datetime | None = None,
) -> DoctorIdentityRebindResult:
    if session is None:
        with get_session() as managed:
            return rebind_doctor_identity(
                doctor_id=doctor_id,
                stale_clerk_id=stale_clerk_id,
                replacement=replacement,
                operator_ref=operator_ref,
                reason=reason,
                confirmed_plan_sha256=confirmed_plan_sha256,
                session=cast(Session, managed),
                now=now,
            )
    confirmed = confirmed_plan_sha256.strip().lower()
    if len(confirmed) != 64 or any(ch not in "0123456789abcdef" for ch in confirmed):
        raise DoctorIdentityRebindError(
            "confirmed_plan_sha256 must be a 64-character lowercase hex digest"
        )

    try:
        lock_pilot_access_lineage(session)
        evaluated = _evaluate(
            session,
            doctor_id=doctor_id,
            stale_clerk_id=stale_clerk_id,
            replacement=replacement,
            operator_ref=operator_ref,
            reason=reason,
            lock=True,
        )
        if not hmac.compare_digest(evaluated.plan.plan_sha256, confirmed):
            raise DoctorIdentityRebindError(
                "confirmed plan digest does not match the current recovery plan"
            )
        if evaluated.plan.already_applied:
            session.commit()
            return DoctorIdentityRebindResult(
                plan_sha256=evaluated.plan.plan_sha256,
                doctor_id=evaluated.doctor.id,
                changed=False,
            )

        current = now or datetime.now(UTC)
        if current.tzinfo is None or current.utcoffset() is None:
            raise DoctorIdentityRebindError(
                "operation timestamp must include a timezone"
            )

        if evaluated.provisional is not None:
            evaluated.provisional.clerk_id = _tombstone_subject(
                evaluated.provisional.id,
                replacement.clerk_id,
            )
            # Doctor.email is historically annotated as ``str`` even though
            # its column is nullable. Clearing it releases the unique email
            # for the retained identity without deleting the tombstone.
            cast(Any, evaluated.provisional).email = None
            evaluated.provisional.deleted_at = current
            session.add(evaluated.provisional)
            session.flush()

        evaluated.doctor.clerk_id = replacement.clerk_id
        session.add(evaluated.doctor)
        session.flush()

        if evaluated.pilot_grant is not None:
            old_grant = evaluated.pilot_grant
            old_grant.revoked_at = current
            old_grant.revoked_by_ref = operator_ref
            old_grant.revocation_reason = reason
            old_grant.revocation_plan_sha256 = evaluated.plan.plan_sha256
            old_grant.retain_until = _seven_years_after(current)
            session.add(old_grant)
            session.flush()
            session.add(
                PilotAccessGrandfatherGrant(
                    clerk_id=replacement.clerk_id,
                    doctor_id_snapshot=evaluated.doctor.id,
                    practice_id_snapshot=evaluated.plan.practice_id,
                    email_snapshot=replacement.email,
                    email_sha256=_sha256(replacement.email),
                    source_ref=old_grant.source_ref,
                    source_sha256=old_grant.source_sha256,
                    grant_plan_sha256=evaluated.plan.plan_sha256,
                    granted_by_ref=operator_ref,
                    grant_reason=reason,
                    granted_at=current,
                )
            )

        receipt_scope = (
            evaluated.authority_scopes[0] if evaluated.authority_scopes else None
        )
        session.add(
            AuthorityAuditEvent(
                event_source="platform_authoritative",
                event_type=IDENTITY_REBIND_EVENT_TYPE,
                outcome="accepted",
                occurred_at=current,
                recorded_at=current,
                tenant_id=(
                    receipt_scope.tenant_id if receipt_scope is not None else None
                ),
                actor_id=(
                    receipt_scope.identity.actor_id
                    if receipt_scope is not None
                    else None
                ),
                resource_type=IDENTITY_REBIND_RESOURCE_TYPE,
                resource_ref=_receipt_ref(evaluated.doctor.id, replacement.clerk_id),
                request_digest=evaluated.plan.plan_sha256,
                route=IDENTITY_REBIND_ROUTE,
                intent=evaluated.plan.environment,
                content_sha256=evaluated.plan.clerk_issuer_sha256,
                platform_refs=[],
            )
        )

        for scope in evaluated.authority_scopes:
            identity = scope.identity
            identity.revoked_at = current
            session.add(identity)
            session.add(
                AuthorityActorExternalIdentity(
                    actor_id=identity.actor_id,
                    issuer=identity.issuer,
                    subject=replacement.clerk_id,
                    verified_at=current,
                )
            )
            session.add(
                AuthorityAuditEvent(
                    event_source="platform_authoritative",
                    event_type="authority.provider_identity.rebound",
                    outcome="accepted",
                    occurred_at=current,
                    recorded_at=current,
                    tenant_id=scope.tenant_id,
                    actor_id=identity.actor_id,
                    resource_type=IDENTITY_REBIND_RESOURCE_TYPE,
                    resource_ref=f"identity-rebind:{evaluated.plan.plan_sha256}",
                    request_digest=evaluated.plan.plan_sha256,
                    platform_refs=[],
                )
            )

        if evaluated.member is not None:
            session.add(
                PracticeAccessAudit(
                    practice_id=evaluated.member.practice_id,
                    action=PracticeAccessAction.doctor_identity_rebound.value,
                    operator_ref=operator_ref,
                    reason=reason,
                    effective_at=current,
                    document_reference=_receipt_ref(
                        evaluated.doctor.id, replacement.clerk_id
                    ),
                    evidence_sha256=evaluated.plan.plan_sha256,
                )
            )
        session.commit()
    except DoctorIdentityRebindError:
        session.rollback()
        raise
    except (IntegrityError, SQLAlchemyError):
        session.rollback()
        raise DoctorIdentityRebindError(
            "identity recovery database operation failed; verify state before retrying"
        ) from None

    return DoctorIdentityRebindResult(
        plan_sha256=evaluated.plan.plan_sha256,
        doctor_id=evaluated.doctor.id,
        changed=True,
    )
