"""Audited, retention-preserving closure of one provider account.

The operation disables access without deleting the provider's Clerk identity,
clinical records, legal records, or authorization history.  It is deliberately
operator-only and requires a deterministic dry-run digest before apply.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from app.configuration.authority import AUTHORITY_KERNEL_SETTINGS

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
from app.models.doctor import Doctor, DoctorRole
from app.models.doctor_video_integration import DoctorVideoIntegration
from app.models.organization_access import OrganizationRosterApproval
from app.models.pilot_access_grandfather_grant import PilotAccessGrandfatherGrant
from app.models.practice import Practice, PracticeMember, PracticeRole
from app.models.practice_access_audit import PracticeAccessAction, PracticeAccessAudit
from sqlalchemy import or_
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, col, select

ACCOUNT_CLOSURE_RETENTION_YEARS = 7
ACCOUNT_CLOSURE_RESOURCE_TYPE = "doctor_account_closure"


class DoctorAccountClosureError(ValueError):
    """A privacy-safe refusal for the privileged closure workflow."""


@dataclass(frozen=True)
class DoctorAccountClosurePlan:
    plan_sha256: str
    doctor_id: TypeIDType
    practice_id: TypeIDType
    member_id: TypeIDType
    member_role: str
    pilot_grant_count: int
    organization_approval_count: int
    authority_identity_count: int
    active_mount_session_count: int
    video_integration_count: int
    stripe_follow_up_required: bool
    already_applied: bool


@dataclass(frozen=True)
class DoctorAccountClosureResult:
    plan_sha256: str
    doctor_id: TypeIDType
    practice_id: TypeIDType
    retain_until: datetime
    stripe_follow_up_required: bool
    changed: bool


@dataclass(frozen=True)
class _AuthorityClosureScope:
    identity: AuthorityActorExternalIdentity
    actor: AuthorityActor
    membership: AuthorityTenantMembership
    tenant: AuthorityTenant
    sessions: tuple[MountContextSession, ...]


@dataclass(frozen=True)
class _EvaluatedClosure:
    plan: DoctorAccountClosurePlan
    doctor: Doctor
    practice: Practice
    member: PracticeMember
    pilot_grants: tuple[PilotAccessGrandfatherGrant, ...]
    roster_approval: OrganizationRosterApproval | None
    authority_scope: _AuthorityClosureScope | None
    video_integrations: tuple[DoctorVideoIntegration, ...]
    receipt: PracticeAccessAudit | None


def _required(value: str, *, field: str, limit: int) -> str:
    normalized = value.strip()
    if not normalized or normalized != value or len(normalized) > limit:
        raise DoctorAccountClosureError(f"{field} is invalid")
    return normalized


def _aware(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise DoctorAccountClosureError("operation timestamp must include a timezone")
    return current


def _seven_years_after(value: datetime) -> datetime:
    try:
        return value.replace(year=value.year + ACCOUNT_CLOSURE_RETENTION_YEARS)
    except ValueError:
        return value.replace(
            year=value.year + ACCOUNT_CLOSURE_RETENTION_YEARS,
            month=2,
            day=28,
        )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_sha256(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return _sha256(canonical)


@dataclass(frozen=True)
class _ReceiptCounts:
    pilot_grants: int
    roster_approvals: int
    authority_identities: int
    video_integrations: int


def _receipt_prefix(doctor_id: TypeIDType) -> str:
    return f"doctor-account-closure:{doctor_id}:"


def _receipt_reference(
    doctor_id: TypeIDType,
    *,
    pilot_grants: int,
    roster_approvals: int,
    authority_identities: int,
    video_integrations: int,
) -> str:
    return (
        f"{_receipt_prefix(doctor_id)}"
        f"p={pilot_grants}:r={roster_approvals}:"
        f"a={authority_identities}:v={video_integrations}"
    )


def _receipt_counts(
    receipt: PracticeAccessAudit, doctor_id: TypeIDType
) -> _ReceiptCounts:
    reference = receipt.document_reference or ""
    prefix = _receipt_prefix(doctor_id)
    if not reference.startswith(prefix):
        raise DoctorAccountClosureError("account closure audit state is invalid")
    try:
        fields = {
            key: int(value)
            for key, value in (
                item.split("=", 1) for item in reference.removeprefix(prefix).split(":")
            )
        }
        counts = _ReceiptCounts(
            pilot_grants=fields["p"],
            roster_approvals=fields["r"],
            authority_identities=fields["a"],
            video_integrations=fields["v"],
        )
    except (KeyError, TypeError, ValueError):
        raise DoctorAccountClosureError(
            "account closure audit state is invalid"
        ) from None
    if (
        set(fields) != {"p", "r", "a", "v"}
        or counts.pilot_grants not in (0, 1)
        or counts.roster_approvals not in (0, 1)
        or counts.authority_identities not in (0, 1)
        or counts.video_integrations < 0
    ):
        raise DoctorAccountClosureError("account closure audit state is invalid")
    return counts


def _authority_scope(
    session: Session,
    *,
    clerk_subject: str,
    lock: bool,
) -> _AuthorityClosureScope | None:
    subject_statement = select(AuthorityActorExternalIdentity).where(
        AuthorityActorExternalIdentity.subject == clerk_subject
    )
    if lock:
        subject_statement = subject_statement.with_for_update()
    subject_rows = list(session.exec(subject_statement).all())
    settings = AUTHORITY_KERNEL_SETTINGS
    if not settings.has_valid_clerk_issuer():
        if subject_rows:
            raise DoctorAccountClosureError(
                "authority identity state exists but its Clerk issuer is unavailable"
            )
        return None

    configured_rows = [
        row for row in subject_rows if row.issuer == settings.clerk_issuer
    ]
    active = [row for row in configured_rows if row.revoked_at is None]
    if len(active) > 1:
        raise DoctorAccountClosureError("authority identity state is ambiguous")
    if not active:
        return None

    identity = active[0]
    actor_statement = select(AuthorityActor).where(
        AuthorityActor.id == identity.actor_id
    )
    if lock:
        actor_statement = actor_statement.with_for_update()
    actor = session.exec(actor_statement).one_or_none()
    if actor is None or actor.actor_kind != "human" or actor.status != "active":
        raise DoctorAccountClosureError("authority actor state is invalid")

    identity_statement = select(AuthorityActorExternalIdentity).where(
        AuthorityActorExternalIdentity.actor_id == actor.id
    )
    if lock:
        identity_statement = identity_statement.with_for_update()
    actor_identities = list(session.exec(identity_statement).all())
    if any(row.issuer != settings.clerk_issuer for row in actor_identities):
        raise DoctorAccountClosureError(
            "the authority actor has an identity from an unexpected issuer"
        )
    active_identities = [row for row in actor_identities if row.revoked_at is None]
    if len(active_identities) != 1 or active_identities[0].id != identity.id:
        raise DoctorAccountClosureError("authority identity state is ambiguous")

    membership_statement = select(AuthorityTenantMembership).where(
        AuthorityTenantMembership.actor_id == actor.id,
        AuthorityTenantMembership.status == "active",
    )
    if lock:
        membership_statement = membership_statement.with_for_update()
    memberships = list(session.exec(membership_statement).all())
    if len(memberships) != 1 or memberships[0].membership_kind != "provider":
        raise DoctorAccountClosureError("authority tenant scope is ambiguous")
    membership = memberships[0]

    tenant_statement = select(AuthorityTenant).where(
        AuthorityTenant.id == membership.tenant_id
    )
    if lock:
        tenant_statement = tenant_statement.with_for_update()
    tenant = session.exec(tenant_statement).one_or_none()
    if tenant is None or tenant.status != "active":
        raise DoctorAccountClosureError("authority tenant scope is invalid")

    session_statement = select(MountContextSession).where(
        MountContextSession.actor_id == actor.id,
        MountContextSession.status == "active",
        col(MountContextSession.revoked_at).is_(None),
    )
    if lock:
        session_statement = session_statement.with_for_update()
    sessions = tuple(session.exec(session_statement).all())
    return _AuthorityClosureScope(
        identity=identity,
        actor=actor,
        membership=membership,
        tenant=tenant,
        sessions=sessions,
    )


def _active_pilot_grants(
    session: Session,
    *,
    doctor: Doctor,
    practice: Practice,
    lock: bool,
) -> tuple[PilotAccessGrandfatherGrant, ...]:
    statement = select(PilotAccessGrandfatherGrant).where(
        or_(
            col(PilotAccessGrandfatherGrant.doctor_id_snapshot) == doctor.id,
            col(PilotAccessGrandfatherGrant.clerk_id) == doctor.clerk_id,
        ),
        col(PilotAccessGrandfatherGrant.revoked_at).is_(None),
    )
    if lock:
        statement = statement.with_for_update()
    grants = tuple(session.exec(statement).all())
    if len(grants) > 1:
        raise DoctorAccountClosureError("pilot grant state is ambiguous")
    for grant in grants:
        if (
            grant.clerk_id != doctor.clerk_id
            or grant.doctor_id_snapshot not in (None, doctor.id)
            or grant.practice_id_snapshot not in (None, practice.id)
        ):
            raise DoctorAccountClosureError("pilot grant state is inconsistent")
        if doctor.email is None or not hmac.compare_digest(
            grant.email_sha256,
            _sha256(doctor.email.strip().lower()),
        ):
            raise DoctorAccountClosureError("pilot grant identity is inconsistent")
    return grants


def _roster_approval(
    session: Session,
    *,
    member: PracticeMember,
    lock: bool,
) -> OrganizationRosterApproval | None:
    if member.organization_roster_approval_id is None:
        return None
    statement = select(OrganizationRosterApproval).where(
        OrganizationRosterApproval.id == member.organization_roster_approval_id
    )
    if lock:
        statement = statement.with_for_update()
    approval = session.exec(statement).one_or_none()
    if (
        approval is None
        or approval.claimed_doctor_id != member.doctor_id
        or approval.target_practice_id != member.practice_id
    ):
        raise DoctorAccountClosureError("organization roster state is inconsistent")
    return approval


def _video_integrations(
    session: Session,
    *,
    doctor: Doctor,
    lock: bool,
) -> tuple[DoctorVideoIntegration, ...]:
    statement = select(DoctorVideoIntegration).where(
        DoctorVideoIntegration.doctor_id == doctor.id
    )
    if lock:
        statement = statement.with_for_update()
    return tuple(session.exec(statement).all())


def _plan_sha256(
    *,
    doctor: Doctor,
    practice: Practice,
    member: PracticeMember,
    pilot_grants: tuple[PilotAccessGrandfatherGrant, ...],
    roster_approval: OrganizationRosterApproval | None,
    authority_scope: _AuthorityClosureScope | None,
    video_integrations: tuple[DoctorVideoIntegration, ...],
    operator_ref: str,
    reason: str,
) -> str:
    authority_payload: dict[str, Any] | None = None
    if authority_scope is not None:
        authority_payload = {
            "actor_id": str(authority_scope.actor.id),
            "identity_id": str(authority_scope.identity.id),
            "issuer_sha256": _sha256(authority_scope.identity.issuer),
            "membership_id": str(authority_scope.membership.id),
            "mount_session_ids": sorted(
                str(item.id) for item in authority_scope.sessions
            ),
            "tenant_id": str(authority_scope.tenant.id),
        }
    return _canonical_sha256(
        {
            "action": "doctor_account_closure",
            "authority_scope": authority_payload,
            "clerk_subject_sha256": _sha256(doctor.clerk_id),
            "doctor_id": str(doctor.id),
            "email_sha256": _sha256((doctor.email or "").strip().lower()),
            "member_id": str(member.id),
            "member_role": member.role,
            "operator_ref": operator_ref,
            "organization_roster_approval_id": (
                str(roster_approval.id) if roster_approval is not None else None
            ),
            "pilot_grant_ids": sorted(str(item.id) for item in pilot_grants),
            "practice_id": str(practice.id),
            "reason": reason,
            "schema_version": 1,
            "stripe_subscription_ref_sha256": (
                _sha256(practice.stripe_subscription_id)
                if practice.stripe_subscription_id
                else None
            ),
            "video_integration_ids": sorted(
                str(item.id) for item in video_integrations
            ),
        }
    )


def _validate_applied_state(
    session: Session,
    *,
    doctor: Doctor,
    member: PracticeMember,
    roster_approval: OrganizationRosterApproval | None,
    video_integrations: tuple[DoctorVideoIntegration, ...],
    receipt: PracticeAccessAudit,
    operator_ref: str,
    reason: str,
) -> datetime:
    counts = _receipt_counts(receipt, doctor.id)
    if (
        receipt.operator_ref != operator_ref
        or receipt.reason != reason
        or receipt.effective_at is None
        or receipt.expires_at is None
        or receipt.evidence_sha256 is None
        or len(receipt.evidence_sha256) != 64
    ):
        raise DoctorAccountClosureError("account closure audit state is inconsistent")
    if receipt.expires_at < _seven_years_after(receipt.effective_at):
        raise DoctorAccountClosureError("account closure retention is insufficient")
    if (
        doctor.deleted_at != receipt.effective_at
        or member.access_revoked_at != receipt.effective_at
        or member.access_revoked_by_ref != operator_ref
        or member.access_revocation_reason != reason
        or member.access_records_retain_until != receipt.expires_at
    ):
        raise DoctorAccountClosureError("account closure state is incomplete")
    if roster_approval is not None and (
        roster_approval.revoked_at != receipt.effective_at
        or roster_approval.revoked_by_ref != operator_ref
        or roster_approval.revocation_reason != reason
        or roster_approval.retain_until != receipt.expires_at
    ):
        raise DoctorAccountClosureError("organization roster closure is incomplete")
    if int(roster_approval is not None) != counts.roster_approvals:
        raise DoctorAccountClosureError("organization roster closure is incomplete")
    if video_integrations:
        raise DoctorAccountClosureError("video integration closure is incomplete")

    related_grants = list(
        session.exec(
            select(PilotAccessGrandfatherGrant).where(
                or_(
                    col(PilotAccessGrandfatherGrant.doctor_id_snapshot) == doctor.id,
                    col(PilotAccessGrandfatherGrant.clerk_id) == doctor.clerk_id,
                )
            )
        ).all()
    )
    if any(grant.revoked_at is None for grant in related_grants):
        raise DoctorAccountClosureError("account closure left an active pilot grant")
    closure_grants = [
        grant
        for grant in related_grants
        if grant.revocation_plan_sha256 == receipt.evidence_sha256
    ]
    if len(closure_grants) != counts.pilot_grants:
        raise DoctorAccountClosureError("pilot grant closure evidence is incomplete")
    if any(
        grant.revoked_at != receipt.effective_at
        or grant.revoked_by_ref != operator_ref
        or grant.revocation_reason != reason
        or grant.retain_until != receipt.expires_at
        for grant in closure_grants
    ):
        raise DoctorAccountClosureError("pilot grant closure is incomplete")

    authority_rows = list(
        session.exec(
            select(AuthorityActorExternalIdentity).where(
                AuthorityActorExternalIdentity.subject == doctor.clerk_id
            )
        ).all()
    )
    settings = AUTHORITY_KERNEL_SETTINGS
    if authority_rows and not settings.has_valid_clerk_issuer():
        raise DoctorAccountClosureError(
            "authority identity state exists but its Clerk issuer is unavailable"
        )
    configured_rows = [
        row for row in authority_rows if row.issuer == settings.clerk_issuer
    ]
    if any(row.revoked_at is None for row in configured_rows):
        raise DoctorAccountClosureError("account closure left an active identity")
    authority_events = list(
        session.exec(
            select(AuthorityAuditEvent).where(
                AuthorityAuditEvent.event_type == "authority.provider_account.closed",
                AuthorityAuditEvent.resource_type == ACCOUNT_CLOSURE_RESOURCE_TYPE,
                AuthorityAuditEvent.request_digest == receipt.evidence_sha256,
            )
        ).all()
    )
    if len(authority_events) > 1:
        raise DoctorAccountClosureError("authority closure audit state is ambiguous")
    if len(authority_events) != counts.authority_identities:
        raise DoctorAccountClosureError(
            "authority closure audit evidence is incomplete"
        )
    if authority_events:
        event = authority_events[0]
        if event.actor_id is None or event.tenant_id is None:
            raise DoctorAccountClosureError("authority closure audit state is invalid")
        closure_identities = [
            row
            for row in configured_rows
            if row.actor_id == event.actor_id and row.revoked_at == receipt.effective_at
        ]
        if len(closure_identities) != 1:
            raise DoctorAccountClosureError("authority identity closure is incomplete")
        actor = session.get(AuthorityActor, event.actor_id)
        if actor is None or actor.status != "revoked":
            raise DoctorAccountClosureError("authority account closure is incomplete")
        if (
            session.exec(
                select(AuthorityTenantMembership).where(
                    AuthorityTenantMembership.actor_id == actor.id,
                    AuthorityTenantMembership.status == "active",
                )
            ).first()
            is not None
        ):
            raise DoctorAccountClosureError(
                "authority membership closure is incomplete"
            )
        if (
            session.exec(
                select(MountContextSession).where(
                    MountContextSession.actor_id == actor.id,
                    MountContextSession.status == "active",
                    col(MountContextSession.revoked_at).is_(None),
                )
            ).first()
            is not None
        ):
            raise DoctorAccountClosureError("authority session closure is incomplete")
    return receipt.expires_at


def _evaluate(
    session: Session,
    *,
    doctor_id: TypeIDType,
    operator_ref: str,
    reason: str,
    lock: bool,
) -> _EvaluatedClosure:
    operator = _required(operator_ref, field="operator_ref", limit=255)
    explanation = _required(reason, field="reason", limit=512)

    doctor_snapshot = cast(
        Doctor | None,
        session.exec(select(Doctor).where(Doctor.id == doctor_id)).one_or_none(),
    )
    if doctor_snapshot is None:
        raise DoctorAccountClosureError("provider account does not exist")

    member_snapshot = cast(
        PracticeMember | None,
        session.exec(
            select(PracticeMember).where(PracticeMember.doctor_id == doctor_snapshot.id)
        ).one_or_none(),
    )
    if member_snapshot is None:
        raise DoctorAccountClosureError(
            "provider account has no practice membership; use its specific access revocation workflow"
        )

    # Existing join and organization-access mutations lock the Practice first.
    # Preserve that order here so a concurrent enrollment cannot deadlock the
    # closure or slip a new member past the owner check.
    practice_statement = select(Practice).where(
        Practice.id == member_snapshot.practice_id
    )
    if lock:
        practice_statement = practice_statement.with_for_update()
    practice = cast(Practice | None, session.exec(practice_statement).one_or_none())
    if practice is None:
        raise DoctorAccountClosureError("provider practice does not exist")

    if lock:
        doctor = cast(
            Doctor,
            session.exec(
                select(Doctor)
                .where(Doctor.id == doctor_snapshot.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).one(),
        )
        member = cast(
            PracticeMember | None,
            session.exec(
                select(PracticeMember)
                .where(PracticeMember.doctor_id == doctor.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).one_or_none(),
        )
        if member is None or member.practice_id != practice.id:
            raise DoctorAccountClosureError(
                "provider practice membership changed during closure; retry"
            )
    else:
        doctor = doctor_snapshot
        member = member_snapshot

    receipt_statement = select(PracticeAccessAudit).where(
        PracticeAccessAudit.practice_id == practice.id,
        PracticeAccessAudit.action == PracticeAccessAction.doctor_account_closed.value,
    )
    if lock:
        receipt_statement = receipt_statement.with_for_update()
    receipts = [
        row
        for row in session.exec(receipt_statement).all()
        if (row.document_reference or "").startswith(_receipt_prefix(doctor.id))
    ]
    if len(receipts) > 1:
        raise DoctorAccountClosureError("account closure audit state is ambiguous")
    receipt = receipts[0] if receipts else None
    roster_approval = _roster_approval(session, member=member, lock=lock)
    video_integrations = _video_integrations(session, doctor=doctor, lock=lock)

    if receipt is not None:
        counts = _receipt_counts(receipt, doctor.id)
        retain_until = _validate_applied_state(
            session,
            doctor=doctor,
            member=member,
            roster_approval=roster_approval,
            video_integrations=video_integrations,
            receipt=receipt,
            operator_ref=operator,
            reason=explanation,
        )
        del retain_until
        return _EvaluatedClosure(
            plan=DoctorAccountClosurePlan(
                plan_sha256=cast(str, receipt.evidence_sha256),
                doctor_id=doctor.id,
                practice_id=practice.id,
                member_id=member.id,
                member_role=member.role,
                pilot_grant_count=counts.pilot_grants,
                organization_approval_count=counts.roster_approvals,
                authority_identity_count=counts.authority_identities,
                active_mount_session_count=0,
                video_integration_count=counts.video_integrations,
                stripe_follow_up_required=bool(practice.stripe_subscription_id),
                already_applied=True,
            ),
            doctor=doctor,
            practice=practice,
            member=member,
            pilot_grants=(),
            roster_approval=roster_approval,
            authority_scope=None,
            video_integrations=(),
            receipt=receipt,
        )

    if doctor.deleted_at is not None or member.access_revoked_at is not None:
        raise DoctorAccountClosureError(
            "provider has a prior or incomplete access revocation"
        )
    if doctor.role == DoctorRole.admin:
        raise DoctorAccountClosureError(
            "platform administrators must be demoted before account closure"
        )
    if roster_approval is not None and roster_approval.revoked_at is not None:
        raise DoctorAccountClosureError(
            "provider has a prior or incomplete organization revocation"
        )

    if member.role == PracticeRole.owner.value:
        other_active_member = session.exec(
            select(PracticeMember).where(
                PracticeMember.practice_id == practice.id,
                PracticeMember.id != member.id,
                col(PracticeMember.access_revoked_at).is_(None),
            )
        ).first()
        if other_active_member is not None:
            raise DoctorAccountClosureError(
                "cannot close a practice owner while another active member remains; transfer ownership first"
            )
        if not practice.is_solo:
            raise DoctorAccountClosureError(
                "a non-solo owner requires an explicit practice wind-down before account closure"
            )
    elif member.role != PracticeRole.workforce.value:
        raise DoctorAccountClosureError("provider membership role is invalid")

    pilot_grants = _active_pilot_grants(
        session,
        doctor=doctor,
        practice=practice,
        lock=lock,
    )
    authority_scope = _authority_scope(
        session,
        clerk_subject=doctor.clerk_id,
        lock=lock,
    )
    digest = _plan_sha256(
        doctor=doctor,
        practice=practice,
        member=member,
        pilot_grants=pilot_grants,
        roster_approval=roster_approval,
        authority_scope=authority_scope,
        video_integrations=video_integrations,
        operator_ref=operator,
        reason=explanation,
    )
    return _EvaluatedClosure(
        plan=DoctorAccountClosurePlan(
            plan_sha256=digest,
            doctor_id=doctor.id,
            practice_id=practice.id,
            member_id=member.id,
            member_role=member.role,
            pilot_grant_count=len(pilot_grants),
            organization_approval_count=int(roster_approval is not None),
            authority_identity_count=int(authority_scope is not None),
            active_mount_session_count=(
                len(authority_scope.sessions) if authority_scope is not None else 0
            ),
            video_integration_count=len(video_integrations),
            stripe_follow_up_required=bool(practice.stripe_subscription_id),
            already_applied=False,
        ),
        doctor=doctor,
        practice=practice,
        member=member,
        pilot_grants=pilot_grants,
        roster_approval=roster_approval,
        authority_scope=authority_scope,
        video_integrations=video_integrations,
        receipt=None,
    )


def plan_doctor_account_closure(
    *,
    doctor_id: TypeIDType,
    operator_ref: str,
    reason: str,
    session: Session | None = None,
) -> DoctorAccountClosurePlan:
    """Validate live state and return a no-write deterministic plan."""

    if session is None:
        with get_session() as managed:
            return plan_doctor_account_closure(
                doctor_id=doctor_id,
                operator_ref=operator_ref,
                reason=reason,
                session=cast(Session, managed),
            )
    try:
        return _evaluate(
            session,
            doctor_id=doctor_id,
            operator_ref=operator_ref,
            reason=reason,
            lock=False,
        ).plan
    except DoctorAccountClosureError:
        session.rollback()
        raise
    except SQLAlchemyError:
        session.rollback()
        raise DoctorAccountClosureError(
            "account closure database validation failed"
        ) from None


def close_doctor_account(
    *,
    doctor_id: TypeIDType,
    operator_ref: str,
    reason: str,
    confirmed_plan_sha256: str,
    session: Session | None = None,
    now: datetime | None = None,
) -> DoctorAccountClosureResult:
    """Apply one reviewed closure without deleting Clerk or retained records."""

    if session is None:
        with get_session() as managed:
            return close_doctor_account(
                doctor_id=doctor_id,
                operator_ref=operator_ref,
                reason=reason,
                confirmed_plan_sha256=confirmed_plan_sha256,
                session=cast(Session, managed),
                now=now,
            )
    confirmed = confirmed_plan_sha256.strip().lower()
    if len(confirmed) != 64 or any(ch not in "0123456789abcdef" for ch in confirmed):
        raise DoctorAccountClosureError(
            "confirmed_plan_sha256 must be a 64-character lowercase hex digest"
        )

    try:
        evaluated = _evaluate(
            session,
            doctor_id=doctor_id,
            operator_ref=operator_ref,
            reason=reason,
            lock=True,
        )
        if not hmac.compare_digest(evaluated.plan.plan_sha256, confirmed):
            raise DoctorAccountClosureError(
                "confirmed plan digest does not match the current closure plan"
            )
        if evaluated.plan.already_applied:
            assert evaluated.receipt is not None
            assert evaluated.receipt.expires_at is not None
            session.commit()
            result = DoctorAccountClosureResult(
                plan_sha256=evaluated.plan.plan_sha256,
                doctor_id=evaluated.doctor.id,
                practice_id=evaluated.practice.id,
                retain_until=evaluated.receipt.expires_at,
                stripe_follow_up_required=evaluated.plan.stripe_follow_up_required,
                changed=False,
            )
        else:
            current = _aware(now)
            retain_until = _seven_years_after(current)
            if any(current < grant.granted_at for grant in evaluated.pilot_grants):
                raise DoctorAccountClosureError(
                    "closure timestamp cannot precede an active pilot grant"
                )
            if (
                evaluated.roster_approval is not None
                and current < evaluated.roster_approval.approved_at
            ):
                raise DoctorAccountClosureError(
                    "closure timestamp cannot precede roster approval"
                )

            evaluated.doctor.deleted_at = current
            evaluated.member.access_revoked_at = current
            evaluated.member.access_revoked_by_ref = operator_ref
            evaluated.member.access_revocation_reason = reason
            evaluated.member.access_records_retain_until = retain_until
            session.add(evaluated.doctor)
            session.add(evaluated.member)

            if evaluated.roster_approval is not None:
                approval = evaluated.roster_approval
                approval.revoked_at = current
                approval.revoked_by_ref = operator_ref
                approval.revocation_reason = reason
                approval.retain_until = retain_until
                session.add(approval)

            for grant in evaluated.pilot_grants:
                grant.revoked_at = current
                grant.revoked_by_ref = operator_ref
                grant.revocation_reason = reason
                grant.revocation_plan_sha256 = evaluated.plan.plan_sha256
                grant.retain_until = retain_until
                session.add(grant)

            # OAuth credentials are operational secrets, not retained clinical
            # or legal records. Removing them locally makes the provider grant
            # unusable by this platform without deleting the external account.
            for integration in evaluated.video_integrations:
                session.delete(integration)

            if evaluated.authority_scope is not None:
                scope = evaluated.authority_scope
                scope.identity.revoked_at = current
                scope.actor.status = "revoked"
                scope.membership.status = "revoked"
                session.add(scope.identity)
                session.add(scope.actor)
                session.add(scope.membership)
                for mount_session in scope.sessions:
                    mount_session.status = "revoked"
                    mount_session.revoked_at = current
                    session.add(mount_session)
                session.add(
                    AuthorityAuditEvent(
                        event_source="platform_authoritative",
                        event_type="authority.provider_account.closed",
                        outcome="accepted",
                        occurred_at=current,
                        recorded_at=current,
                        tenant_id=scope.tenant.id,
                        actor_id=scope.actor.id,
                        resource_type=ACCOUNT_CLOSURE_RESOURCE_TYPE,
                        resource_ref=f"account-closure:{evaluated.plan.plan_sha256}",
                        request_digest=evaluated.plan.plan_sha256,
                        platform_refs=[],
                    )
                )

            receipt = PracticeAccessAudit(
                practice_id=evaluated.practice.id,
                action=PracticeAccessAction.doctor_account_closed.value,
                operator_ref=operator_ref,
                reason=reason,
                effective_at=current,
                expires_at=retain_until,
                document_reference=_receipt_reference(
                    evaluated.doctor.id,
                    pilot_grants=len(evaluated.pilot_grants),
                    roster_approvals=int(evaluated.roster_approval is not None),
                    authority_identities=int(evaluated.authority_scope is not None),
                    video_integrations=len(evaluated.video_integrations),
                ),
                evidence_sha256=evaluated.plan.plan_sha256,
            )
            session.add(receipt)
            session.commit()
            result = DoctorAccountClosureResult(
                plan_sha256=evaluated.plan.plan_sha256,
                doctor_id=evaluated.doctor.id,
                practice_id=evaluated.practice.id,
                retain_until=retain_until,
                stripe_follow_up_required=evaluated.plan.stripe_follow_up_required,
                changed=True,
            )
    except DoctorAccountClosureError:
        session.rollback()
        raise
    except SQLAlchemyError:
        session.rollback()
        raise DoctorAccountClosureError(
            "account closure database operation failed; verify state before retrying"
        ) from None

    if result.stripe_follow_up_required:
        from app.services.stripe_billing import mark_quantity_sync_required

        try:
            mark_quantity_sync_required(evaluated.practice)
        except Exception:
            raise DoctorAccountClosureError(
                "account closure is applied but Stripe seat reconciliation was not queued; verify account and billing state before retrying"
            ) from None

    return result
