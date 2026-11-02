"""Authenticated self-delete for the caller's own Cosmaneura identity.

Implements the Cosmas half of Apple Guideline 5.1.1(v) for Scribe. The caller
deletes only themselves: identity comes from the verified Clerk session, never
from a target user id. Clerk deletion uses the existing ``CLERK_PRIVATE_KEY``
integration (``app.configuration.clerk.clerk``); no new secrets are introduced.

Clinical and practice records are not purged here. See
``apply_clinical_record_retention_policy`` — the single retention hook where
counsel/John will decide later what must be held.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast

from clerk_backend_api.models import ClerkError, ClerkErrors, ClerkErrorsData

from app import log
from app.configuration.authority import AUTHORITY_KERNEL_SETTINGS
from app.configuration.clerk import clerk
from app.services.external_webhook_inbox import provider_log_ref
from app.services.superadmin_capability import (
    SuperadminCapabilityError,
    get_active_runtime_superadmin_capability_kinds,
)

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.authority import (
    AuthorityActor,
    AuthorityActorExternalIdentity,
    AuthorityAuditEvent,
    AuthorityTenant,
    AuthorityTenantMembership,
    MountContextSession,
)
from app.models.doctor import Doctor
from app.models.doctor_video_integration import DoctorVideoIntegration
from app.models.note_template_preference import NoteTemplatePreference
from app.models.organization_access import OrganizationRosterApproval
from app.models.pilot_access_grandfather_grant import PilotAccessGrandfatherGrant
from app.models.practice import (
    ClinicianSeatState,
    Practice,
    PracticeMember,
    PracticeRole,
    lock_practice_seats,
)
from app.models.practice_access_audit import PracticeAccessAction, PracticeAccessAudit
from sqlalchemy import or_
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, col, select

CONFIRM_PHRASE = "DELETE"
ACCOUNT_DELETION_PENDING = "deletion_pending"
ACCOUNT_DELETION_DELETED = "deleted"
SELF_DELETE_RETENTION_YEARS = 7
SELF_DELETE_RESOURCE_TYPE = "doctor_self_delete"
SELF_DELETE_OPERATOR_PREFIX = "self:doctor:"
SELF_DELETE_REASON = "Authenticated self-delete requested by the account holder"


class DoctorSelfDeleteError(ValueError):
    """Privacy-safe refusal for the self-delete workflow."""


DeletionStatus = Literal["deleted", "already_deleted"]


@dataclass(frozen=True)
class DoctorSelfDeleteResult:
    status: DeletionStatus
    doctor_id: TypeIDType
    practice_id: TypeIDType | None
    practice_left_ownerless: bool
    clerk_identity_removed: bool
    changed: bool


@dataclass(frozen=True)
class _AuthorityScope:
    identity: AuthorityActorExternalIdentity
    actor: AuthorityActor
    membership: AuthorityTenantMembership
    tenant: AuthorityTenant
    sessions: tuple[MountContextSession, ...]


def apply_clinical_record_retention_policy(
    *,
    doctor_id: TypeIDType,
    practice_id: TypeIDType | None,
    session: Session,
) -> None:
    """RETENTION HOOK — single strategy point for clinical-record retention.

    John is confirming retention policy with counsel. Until that decision lands,
    this function holds nothing and deletes nothing clinical.

    Kept (tied to the practice / historical record, not the deleted login):
    - patients, appointments, notes, transcripts, visit audio references
    - BAA acceptances and practice entity rows
    - other members' accounts and practice workforce data
    - append-only access / authority audit history

    Removed elsewhere in this module (personal only):
    - profile fields (email, biography, api_key, personal settings)
    - local video OAuth credentials, note-template preferences
    - the caller's practice membership access, authority mount sessions
    - the caller's Clerk identity (via the existing Clerk server key)
    """

    del doctor_id, practice_id, session
    return


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _aware(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise DoctorSelfDeleteError("operation timestamp must include a timezone")
    return current


def _seven_years_after(value: datetime) -> datetime:
    try:
        return value.replace(year=value.year + SELF_DELETE_RETENTION_YEARS)
    except ValueError:
        return value.replace(
            year=value.year + SELF_DELETE_RETENTION_YEARS,
            month=2,
            day=28,
        )


def _require_confirm(confirm: str) -> None:
    if confirm != CONFIRM_PHRASE:
        raise DoctorSelfDeleteError("confirm must be the exact phrase DELETE")


def _audit_step(
    *,
    event: str,
    doctor_id: TypeIDType,
    practice_id: TypeIDType | None,
    result: str,
) -> None:
    log.info(
        "doctor_self_delete_step",
        event_code=event,
        doctor_id_hash=_sha256(str(doctor_id)),
        doctor_ref=provider_log_ref(f"doctor:{doctor_id}"),
        practice_id=str(practice_id) if practice_id is not None else None,
        result=result,
    )


def _authority_scope(
    session: Session, *, clerk_subject: str, lock: bool
) -> _AuthorityScope | None:
    subject_statement = select(AuthorityActorExternalIdentity).where(
        AuthorityActorExternalIdentity.subject == clerk_subject
    )
    if lock:
        subject_statement = subject_statement.with_for_update()
    subject_rows = list(session.exec(subject_statement).all())
    settings = AUTHORITY_KERNEL_SETTINGS
    if not settings.has_valid_clerk_issuer():
        return None
    active = [
        row
        for row in subject_rows
        if row.issuer == settings.clerk_issuer and row.revoked_at is None
    ]
    if len(active) != 1:
        return None
    identity = active[0]
    actor_statement = select(AuthorityActor).where(
        AuthorityActor.id == identity.actor_id
    )
    if lock:
        actor_statement = actor_statement.with_for_update()
    actor = session.exec(actor_statement).one_or_none()
    if actor is None or actor.actor_kind != "human":
        return None
    membership_statement = select(AuthorityTenantMembership).where(
        AuthorityTenantMembership.actor_id == actor.id,
        AuthorityTenantMembership.status == "active",
    )
    if lock:
        membership_statement = membership_statement.with_for_update()
    memberships = list(session.exec(membership_statement).all())
    if len(memberships) != 1 or memberships[0].membership_kind != "provider":
        return None
    membership = memberships[0]
    tenant = session.exec(
        select(AuthorityTenant).where(AuthorityTenant.id == membership.tenant_id)
    ).one_or_none()
    if tenant is None:
        return None
    session_statement = select(MountContextSession).where(
        MountContextSession.actor_id == actor.id,
        MountContextSession.status == "active",
        col(MountContextSession.revoked_at).is_(None),
    )
    if lock:
        session_statement = session_statement.with_for_update()
    sessions = tuple(session.exec(session_statement).all())
    return _AuthorityScope(
        identity=identity,
        actor=actor,
        membership=membership,
        tenant=tenant,
        sessions=sessions,
    )


def _delete_clerk_identity(clerk_id: str) -> bool:
    """Delete the Clerk user through the existing server key. True if removed now."""

    if not isinstance(clerk_id, str) or not clerk_id.startswith("user_"):
        raise DoctorSelfDeleteError("clerk identity is invalid")
    try:
        clerk.users.delete(user_id=clerk_id)
        return True
    except ClerkErrors as error:
        if type(error.status_code) is not int or error.status_code != 404:
            raise DoctorSelfDeleteError("clerk identity could not be deleted") from None
        data = error.data
        if not isinstance(data, ClerkErrorsData):
            raise DoctorSelfDeleteError("clerk identity could not be deleted") from None
        errors = data.errors
        if (
            not isinstance(errors, list)
            or len(errors) != 1
            or not isinstance(errors[0], ClerkError)
            or type(errors[0].code) is not str
            or errors[0].code != "resource_not_found"
        ):
            raise DoctorSelfDeleteError("clerk identity could not be deleted") from None
        return False
    except DoctorSelfDeleteError:
        raise
    except Exception as error:
        raise DoctorSelfDeleteError("clerk identity could not be deleted") from error


def _scrub_personal_profile(doctor: Doctor) -> None:
    doctor.email = None
    doctor.biography = ""
    doctor.api_key = None
    doctor.has_natmed_subscription = False
    doctor.onboarding_completed_at = None


def _delete_personal_settings(session: Session, doctor: Doctor) -> None:
    for preference in session.exec(
        select(NoteTemplatePreference).where(
            NoteTemplatePreference.doctor_id == doctor.id
        )
    ).all():
        session.delete(preference)
    for integration in session.exec(
        select(DoctorVideoIntegration).where(
            DoctorVideoIntegration.doctor_id == doctor.id
        )
    ).all():
        session.delete(integration)


def delete_own_account(
    *,
    doctor_id: TypeIDType,
    confirm: str,
    session: Session | None = None,
    now: datetime | None = None,
    clerk_deleter=None,
) -> DoctorSelfDeleteResult:
    """Delete the authenticated caller's own account. Never accepts a target id."""

    _require_confirm(confirm)
    if clerk_deleter is None:
        clerk_deleter = _delete_clerk_identity
    if session is None:
        with get_session() as managed:
            return delete_own_account(
                doctor_id=doctor_id,
                confirm=confirm,
                session=cast(Session, managed),
                now=now,
                clerk_deleter=clerk_deleter,
            )

    current = _aware(now)
    retain_until = _seven_years_after(current)
    operator_ref = f"{SELF_DELETE_OPERATOR_PREFIX}{doctor_id}"
    practice_left_ownerless = False
    practice_id: TypeIDType | None = None
    clerk_identity_removed = False
    clerk_id_for_delete: str | None = None

    try:
        doctor = cast(
            Doctor | None,
            session.exec(
                select(Doctor).where(Doctor.id == doctor_id).with_for_update()
            ).one_or_none(),
        )
        if doctor is None:
            raise DoctorSelfDeleteError("provider account does not exist")

        if doctor.account_deletion_status == ACCOUNT_DELETION_DELETED:
            _audit_step(
                event="doctor_self_delete.already_deleted",
                doctor_id=doctor.id,
                practice_id=None,
                result="already_deleted",
            )
            member = session.exec(
                select(PracticeMember).where(PracticeMember.doctor_id == doctor.id)
            ).one_or_none()
            return DoctorSelfDeleteResult(
                status="already_deleted",
                doctor_id=doctor.id,
                practice_id=member.practice_id if member is not None else None,
                practice_left_ownerless=False,
                clerk_identity_removed=True,
                changed=False,
            )

        try:
            protected = get_active_runtime_superadmin_capability_kinds(
                session,
                subject=doctor.clerk_id,
                doctor_id=doctor.id,
                lock_for_update=True,
            )
        except SuperadminCapabilityError as error:
            raise DoctorSelfDeleteError(
                "superadmin capability state could not be verified"
            ) from error
        if protected:
            raise DoctorSelfDeleteError(
                "active superadmin capabilities must be revoked before self-delete"
            )

        doctor.account_deletion_status = ACCOUNT_DELETION_PENDING
        session.add(doctor)
        session.flush()
        _audit_step(
            event="doctor_self_delete.deletion_pending",
            doctor_id=doctor.id,
            practice_id=None,
            result="ok",
        )

        member_snapshot = session.exec(
            select(PracticeMember).where(PracticeMember.doctor_id == doctor.id)
        ).one_or_none()
        practice: Practice | None = None
        member: PracticeMember | None = None
        if member_snapshot is not None:
            practice = lock_practice_seats(session, member_snapshot.practice_id)
            if practice is None:
                raise DoctorSelfDeleteError("provider practice does not exist")
            practice_id = practice.id
            member = cast(
                PracticeMember,
                session.exec(
                    select(PracticeMember)
                    .where(PracticeMember.doctor_id == doctor.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                ).one(),
            )

        apply_clinical_record_retention_policy(
            doctor_id=doctor.id,
            practice_id=practice_id,
            session=session,
        )
        _audit_step(
            event="doctor_self_delete.retention_hook",
            doctor_id=doctor.id,
            practice_id=practice_id,
            result="held_nothing",
        )

        _delete_personal_settings(session, doctor)
        _scrub_personal_profile(doctor)
        doctor.deleted_at = current
        session.add(doctor)

        if member is not None and practice is not None:
            if member.access_revoked_at is None:
                member.access_revoked_at = current
                member.access_revoked_by_ref = operator_ref
                member.access_revocation_reason = SELF_DELETE_REASON
                member.access_records_retain_until = retain_until
                member.seat_state = ClinicianSeatState.inactive.value
                session.add(member)

            if member.role == PracticeRole.owner.value:
                other_active_owner = session.exec(
                    select(PracticeMember).where(
                        PracticeMember.practice_id == practice.id,
                        PracticeMember.id != member.id,
                        PracticeMember.role == PracticeRole.owner.value,
                        col(PracticeMember.access_revoked_at).is_(None),
                    )
                ).first()
                if other_active_owner is None:
                    practice.owner_reassignment_needed_at = current
                    session.add(practice)
                    practice_left_ownerless = True

            if member.organization_roster_approval_id is not None:
                approval = session.exec(
                    select(OrganizationRosterApproval).where(
                        OrganizationRosterApproval.id
                        == member.organization_roster_approval_id
                    )
                ).one_or_none()
                if approval is not None and approval.revoked_at is None:
                    approval.revoked_at = current
                    approval.revoked_by_ref = operator_ref
                    approval.revocation_reason = SELF_DELETE_REASON
                    approval.retain_until = retain_until
                    session.add(approval)

            for grant in session.exec(
                select(PilotAccessGrandfatherGrant).where(
                    col(PilotAccessGrandfatherGrant.revoked_at).is_(None),
                    or_(
                        col(PilotAccessGrandfatherGrant.doctor_id_snapshot)
                        == doctor.id,
                        col(PilotAccessGrandfatherGrant.clerk_id) == doctor.clerk_id,
                    ),
                )
            ).all():
                grant.revoked_at = current
                grant.revoked_by_ref = operator_ref
                grant.revocation_reason = SELF_DELETE_REASON
                grant.retain_until = retain_until
                session.add(grant)

        authority = _authority_scope(session, clerk_subject=doctor.clerk_id, lock=True)
        if authority is not None:
            authority.identity.revoked_at = current
            authority.actor.status = "revoked"
            authority.membership.status = "revoked"
            session.add(authority.identity)
            session.add(authority.actor)
            session.add(authority.membership)
            for mount_session in authority.sessions:
                mount_session.status = "revoked"
                mount_session.revoked_at = current
                session.add(mount_session)
            session.add(
                AuthorityAuditEvent(
                    event_source="platform_authoritative",
                    event_type="authority.provider_account.self_deleted",
                    outcome="accepted",
                    occurred_at=current,
                    recorded_at=current,
                    tenant_id=authority.tenant.id,
                    actor_id=authority.actor.id,
                    resource_type=SELF_DELETE_RESOURCE_TYPE,
                    resource_ref=f"self-delete:{_sha256(str(doctor.id))}",
                    request_digest=_sha256(
                        f"{doctor.id}:{practice_id}:{current.isoformat()}"
                    ),
                    platform_refs=[],
                )
            )

        if practice is not None:
            session.add(
                PracticeAccessAudit(
                    practice_id=practice.id,
                    action=PracticeAccessAction.doctor_self_deleted.value,
                    operator_ref=operator_ref,
                    reason=SELF_DELETE_REASON,
                    effective_at=current,
                    expires_at=retain_until,
                    document_reference=(
                        f"doctor-self-delete:{doctor.id}:"
                        f"ownerless={int(practice_left_ownerless)}"
                    ),
                    evidence_sha256=_sha256(
                        f"self-delete:{doctor.id}:{practice.id}:"
                        f"{int(practice_left_ownerless)}"
                    ),
                )
            )

        clerk_id_for_delete = doctor.clerk_id
        session.flush()
        _audit_step(
            event="doctor_self_delete.personal_data_removed",
            doctor_id=doctor.id,
            practice_id=practice_id,
            result="ok",
        )

        # Commit local state before the external Clerk call so a network failure
        # leaves deletion_pending for a safe retry that finishes identity removal.
        session.commit()
    except DoctorSelfDeleteError:
        session.rollback()
        raise
    except SQLAlchemyError:
        session.rollback()
        raise DoctorSelfDeleteError(
            "self-delete database operation failed; verify state before retrying"
        ) from None

    assert clerk_id_for_delete is not None
    try:
        clerk_identity_removed = clerk_deleter(clerk_id_for_delete)
        _audit_step(
            event="doctor_self_delete.clerk_identity",
            doctor_id=doctor_id,
            practice_id=practice_id,
            result="deleted" if clerk_identity_removed else "already_absent",
        )
    except DoctorSelfDeleteError:
        raise

    try:
        doctor = cast(
            Doctor,
            session.exec(
                select(Doctor).where(Doctor.id == doctor_id).with_for_update()
            ).one(),
        )
        doctor.account_deletion_status = ACCOUNT_DELETION_DELETED
        if doctor.deleted_at is None:
            doctor.deleted_at = current
        session.add(doctor)
        session.commit()
        _audit_step(
            event="doctor_self_delete.deleted",
            doctor_id=doctor_id,
            practice_id=practice_id,
            result="ok",
        )
    except SQLAlchemyError:
        session.rollback()
        raise DoctorSelfDeleteError(
            "self-delete marked pending and Clerk removed, but final status "
            "write failed; retry to mark deleted"
        ) from None

    return DoctorSelfDeleteResult(
        status="deleted",
        doctor_id=doctor_id,
        practice_id=practice_id,
        practice_left_ownerless=practice_left_ownerless,
        clerk_identity_removed=True,
        changed=True,
    )


def assert_self_only(
    *, authenticated_doctor_id: TypeIDType, requested_doctor_id
) -> None:
    """Refuse any attempt to target another account id (defense in depth)."""

    if requested_doctor_id is not None and not hmac.compare_digest(
        str(authenticated_doctor_id), str(requested_doctor_id)
    ):
        raise DoctorSelfDeleteError("cannot delete another account")
