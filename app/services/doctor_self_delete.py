"""Authenticated self-delete for the caller's own Cosmaneura identity (Apple 5.1.1(v)).

Reuses ``doctor_account_closure`` helpers for soft-disable / retention / video OAuth;
adds Clerk user removal and the ownerless-practice latch. Clinical purge policy
lives in ``apply_clinical_record_retention_policy``.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal, cast

from clerk_backend_api.models import ClerkError, ClerkErrors, ClerkErrorsData

from app import log
from app.configuration.clerk import clerk
from app.services import doctor_account_closure as _account_closure
from app.services.external_webhook_inbox import provider_log_ref
from app.services.public_entitlements import (
    ClinicianSeatPolicyError,
    active_clinician_seat_count,
    record_active_clinician_seat_quantity,
)
from app.services.stripe_billing import has_nonterminal_subscription
from app.services.superadmin_capability import (
    SuperadminCapabilityError,
    get_active_runtime_superadmin_capability_kinds,
)

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.authority import AuthorityAuditEvent
from app.models.doctor import Doctor
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
SELF_DELETE_RETENTION_YEARS = _account_closure.ACCOUNT_CLOSURE_RETENTION_YEARS
SELF_DELETE_RESOURCE_TYPE = "doctor_self_delete"
SELF_DELETE_OPERATOR_PREFIX = "self:doctor:"
SELF_DELETE_REASON = "Authenticated self-delete requested by the account holder"


class DoctorSelfDeleteError(ValueError):
    """Privacy-safe refusal for the self-delete workflow."""

    status_code: int = 400

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code


DeletionStatus = Literal["deleted", "already_deleted"]


@dataclass(frozen=True)
class DoctorSelfDeleteResult:
    status: DeletionStatus
    doctor_id: TypeIDType | None
    practice_id: TypeIDType | None
    practice_left_ownerless: bool
    clerk_identity_removed: bool
    stripe_follow_up_required: bool
    changed: bool


def apply_clinical_record_retention_policy(
    *,
    doctor_id: TypeIDType,
    practice_id: TypeIDType | None,
    session: Session,
) -> None:
    """Retention hook: holds/deletes nothing clinical until counsel decides."""

    del doctor_id, practice_id, session
    return


def _sha256(value: str) -> str:
    return _account_closure._sha256(value)


def _aware(value: datetime | None) -> datetime:
    try:
        return _account_closure._aware(value)
    except ValueError as error:
        raise DoctorSelfDeleteError(str(error)) from error


def _seven_years_after(value: datetime) -> datetime:
    return _account_closure._seven_years_after(value)


def _require_confirm(confirm: str) -> None:
    if confirm != CONFIRM_PHRASE:
        raise DoctorSelfDeleteError(
            "confirm must be the exact phrase DELETE",
            status_code=422,
        )


def _audit_step(
    *,
    event: str,
    doctor_id: TypeIDType | None,
    practice_id: TypeIDType | None,
    result: str,
    **extra: Any,
) -> None:
    payload: dict[str, Any] = {
        "event_code": event,
        "practice_id": str(practice_id) if practice_id is not None else None,
        "result": result,
    }
    if doctor_id is not None:
        payload["doctor_id_hash"] = _sha256(str(doctor_id))
        payload["doctor_ref"] = provider_log_ref(f"doctor:{doctor_id}")
    payload.update(extra)
    log.info("doctor_self_delete_step", **payload)


def _authority_scope(session: Session, *, clerk_subject: str, lock: bool):
    """Reuse closure authority lookup; soft-fail so self-delete can continue."""
    try:
        return _account_closure._authority_scope(
            session, clerk_subject=clerk_subject, lock=lock
        )
    except _account_closure.DoctorAccountClosureError:
        return None


def _clerk_unavailable() -> DoctorSelfDeleteError:
    return DoctorSelfDeleteError("clerk identity could not be deleted", status_code=503)


def _delete_clerk_identity(clerk_id: str) -> bool:
    """Delete the Clerk user through the existing server key. True if removed now."""

    if not isinstance(clerk_id, str) or not clerk_id.startswith("user_"):
        raise DoctorSelfDeleteError("clerk identity is invalid", status_code=400)
    try:
        clerk.users.delete(user_id=clerk_id)
        return True
    except ClerkErrors as error:
        if type(error.status_code) is not int or error.status_code != 404:
            raise _clerk_unavailable() from None
        data = error.data
        if not isinstance(data, ClerkErrorsData):
            raise _clerk_unavailable() from None
        errors = data.errors
        if (
            not isinstance(errors, list)
            or len(errors) != 1
            or not isinstance(errors[0], ClerkError)
            or type(errors[0].code) is not str
            or errors[0].code != "resource_not_found"
        ):
            raise _clerk_unavailable() from None
        return False
    except DoctorSelfDeleteError:
        raise
    except Exception as error:
        raise _clerk_unavailable() from error


def _scrub_personal_profile(doctor: Doctor) -> None:
    # email column is nullable; cast keeps Doctor.email typed as str elsewhere.
    doctor.email = cast(Any, None)
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
    # Same video-OAuth cleanup path as master's operator account closure.
    for integration in _account_closure._video_integrations(
        session, doctor=doctor, lock=True
    ):
        session.delete(integration)


def _checkout_can_still_complete(practice: Practice, now: datetime) -> bool:
    """Stripe Checkout lives at most 24h; +1h covers an unrecorded session."""

    if practice.stripe_checkout_session_id is not None:
        return (practice.stripe_checkout_session_expires_at or now) >= now
    created = practice.stripe_checkout_request_created_at or now
    pending = practice.stripe_checkout_request_json is not None
    return pending and created + timedelta(hours=25) > now


def _flag_stripe_seat_follow_up(
    session: Session,
    *,
    practice: Practice,
    doctor_id: TypeIDType,
) -> bool:
    """Flag seat-sync follow-up without canceling Stripe or refusing delete."""

    if (
        not has_nonterminal_subscription(practice)
        or not practice.stripe_subscription_id
    ):
        return False
    try:
        record_active_clinician_seat_quantity(session, practice)
        target = practice.stripe_quantity_sync_target
    except ClinicianSeatPolicyError:
        try:
            target = active_clinician_seat_count(session, practice.id)
        except ClinicianSeatPolicyError:
            target = 0
        practice.stripe_quantity_sync_required = True
        practice.stripe_quantity_sync_target = target
        practice.stripe_quantity_sync_version += 1
        practice.stripe_quantity_sync_error = None
        session.add(practice)
    _audit_step(
        event="doctor_self_delete.stripe_seat_sync_required",
        doctor_id=doctor_id,
        practice_id=practice.id,
        result="queued",
        stripe_quantity_sync_required=True,
        stripe_quantity_sync_target=target,
        stripe_subscription_present=True,
    )
    return True


def delete_clerk_only_identity(
    *,
    clerk_id: str,
    confirm: str,
    clerk_deleter=None,
) -> DoctorSelfDeleteResult:
    """Remove a Clerk subject that never received a Doctor row."""

    _require_confirm(confirm)
    if clerk_deleter is None:
        clerk_deleter = _delete_clerk_identity
    removed = clerk_deleter(clerk_id)
    _audit_step(
        event="doctor_self_delete.clerk_only",
        doctor_id=None,
        practice_id=None,
        result="deleted" if removed else "already_absent",
        clerk_id_prefix=clerk_id[:12] if isinstance(clerk_id, str) else None,
    )
    return DoctorSelfDeleteResult(
        status="deleted",
        doctor_id=None,
        practice_id=None,
        practice_left_ownerless=False,
        clerk_identity_removed=True,
        stripe_follow_up_required=False,
        changed=True,
    )


def delete_own_account(
    *,
    doctor_id: TypeIDType,
    confirm: str,
    authenticated_doctor_id: TypeIDType | None = None,
    session: Session | None = None,
    now: datetime | None = None,
    clerk_deleter=None,
) -> DoctorSelfDeleteResult:
    """Delete the authenticated caller's own account. Never accepts a target id."""

    _require_confirm(confirm)
    if authenticated_doctor_id is not None:
        assert_self_only(
            authenticated_doctor_id=authenticated_doctor_id,
            requested_doctor_id=doctor_id,
        )
    if clerk_deleter is None:
        clerk_deleter = _delete_clerk_identity
    if session is None:
        with get_session() as managed:
            return delete_own_account(
                doctor_id=doctor_id,
                confirm=confirm,
                authenticated_doctor_id=authenticated_doctor_id,
                session=cast(Session, managed),
                now=now,
                clerk_deleter=clerk_deleter,
            )

    current = _aware(now)
    retain_until = _seven_years_after(current)
    operator_ref = f"{SELF_DELETE_OPERATOR_PREFIX}{doctor_id}"
    practice_left_ownerless = False
    stripe_follow_up_required = False
    practice_id: TypeIDType | None = None
    clerk_id_for_delete: str | None = None

    try:
        # Join, invitation, roster and closure paths lock Practice before Doctor.
        locked = session.exec(
            select(PracticeMember.practice_id).where(
                PracticeMember.doctor_id == doctor_id
            )
        ).one_or_none()
        if locked is not None:
            lock_practice_seats(session, locked)
        doctor = cast(
            Doctor | None,
            session.exec(
                select(Doctor).where(Doctor.id == doctor_id).with_for_update()
            ).one_or_none(),
        )
        if doctor is None:
            raise DoctorSelfDeleteError(
                "provider account does not exist",
                status_code=400,
            )

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
                stripe_follow_up_required=False,
                changed=False,
            )

        member_snapshot = session.exec(
            select(PracticeMember).where(PracticeMember.doctor_id == doctor.id)
        ).one_or_none()
        if member_snapshot is not None:
            practice_id = member_snapshot.practice_id
            practice_left_ownerless = (
                session.exec(
                    select(Practice).where(
                        Practice.id == member_snapshot.practice_id,
                        col(Practice.owner_reassignment_needed_at).is_not(None),
                    )
                ).first()
                is not None
            )

        # Mid-flight retry after local commit / Clerk failure: finish identity.
        if (
            doctor.account_deletion_status == ACCOUNT_DELETION_PENDING
            and doctor.deleted_at is not None
        ):
            clerk_id_for_delete = doctor.clerk_id
            session.commit()
            _audit_step(
                event="doctor_self_delete.resume_pending",
                doctor_id=doctor.id,
                practice_id=practice_id,
                result="ok",
            )
        else:
            try:
                protected = get_active_runtime_superadmin_capability_kinds(
                    session,
                    subject=doctor.clerk_id,
                    doctor_id=doctor.id,
                    lock_for_update=True,
                )
            except SuperadminCapabilityError as error:
                raise DoctorSelfDeleteError(
                    "superadmin capability state could not be verified",
                    status_code=409,
                ) from error
            if protected:
                raise DoctorSelfDeleteError(
                    "active superadmin capabilities must be revoked before self-delete",
                    status_code=409,
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

            practice: Practice | None = None
            member: PracticeMember | None = None
            held_active_seat = False
            if member_snapshot is not None:
                practice = lock_practice_seats(session, member_snapshot.practice_id)
                if practice is None:
                    raise DoctorSelfDeleteError(
                        "provider practice does not exist",
                        status_code=400,
                    )
                practice_id = practice.id
                member = cast(
                    PracticeMember,
                    session.exec(
                        select(PracticeMember)
                        .where(
                            PracticeMember.doctor_id == doctor.id,
                            PracticeMember.practice_id == practice.id,
                        )
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    ).one(),
                )
                held_active_seat = (
                    member.access_revoked_at is None
                    and member.seat_state == ClinicianSeatState.active.value
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

            revoke_digest = _sha256(
                f"self-delete:{doctor.id}:{practice_id}:{current.isoformat()}"
            )

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
                            col(PilotAccessGrandfatherGrant.clerk_id)
                            == doctor.clerk_id,
                        ),
                    )
                ).all():
                    grant.revoked_at = current
                    grant.revoked_by_ref = operator_ref
                    grant.revocation_reason = SELF_DELETE_REASON
                    grant.revocation_plan_sha256 = revoke_digest
                    grant.retain_until = retain_until
                    session.add(grant)

                # H1: never refuse or cancel Stripe; flag operator seat follow-up.
                if held_active_seat or practice_left_ownerless:
                    stripe_follow_up_required = _flag_stripe_seat_follow_up(
                        session,
                        practice=practice,
                        doctor_id=doctor.id,
                    )
                # Never refuse: a Checkout that can still complete goes to an operator.
                if practice_left_ownerless and _checkout_can_still_complete(
                    practice, current
                ):
                    stripe_follow_up_required = True
                    session.add(
                        PracticeAccessAudit(
                            practice_id=practice.id,
                            action=PracticeAccessAction.stripe_checkout_follow_up_required.value,
                            operator_ref=operator_ref,
                            reason="Sole owner self-deleted while Checkout could complete",
                            effective_at=current,
                            document_reference="stripe-checkout:"
                            f"{practice.stripe_checkout_session_id or 'request'}",
                        )
                    )
                    log.error(
                        "sole owner self-deleted with an open Stripe Checkout",
                        practice_ref=provider_log_ref(f"practice:{practice.id}"),
                    )

            authority = _authority_scope(
                session, clerk_subject=doctor.clerk_id, lock=True
            )
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
                        request_digest=revoke_digest,
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
                            f"ownerless={int(practice_left_ownerless)}:"
                            f"stripe_follow_up={int(stripe_follow_up_required)}"
                        ),
                        evidence_sha256=_sha256(
                            f"self-delete:{doctor.id}:{practice.id}:"
                            f"{int(practice_left_ownerless)}:"
                            f"{int(stripe_follow_up_required)}"
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
            "self-delete database operation failed; verify state before retrying",
            status_code=503,
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
            "write failed; retry to mark deleted",
            status_code=503,
        ) from None

    return DoctorSelfDeleteResult(
        status="deleted",
        doctor_id=doctor_id,
        practice_id=practice_id,
        practice_left_ownerless=practice_left_ownerless,
        clerk_identity_removed=True,
        stripe_follow_up_required=stripe_follow_up_required,
        changed=True,
    )


def assert_self_only(
    *, authenticated_doctor_id: TypeIDType, requested_doctor_id
) -> None:
    if requested_doctor_id is not None and not hmac.compare_digest(
        str(authenticated_doctor_id), str(requested_doctor_id)
    ):
        raise DoctorSelfDeleteError(
            "cannot delete another account",
            status_code=400,
        )
