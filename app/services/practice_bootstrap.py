"""Fail-closed administrative bootstrap for a new provider practice.

This workflow creates only a closed practice shell and one explicitly named
owner membership.  It does not record a BAA, grant entitlement, create an
organization, import roster authorization, call Stripe, or authorize PHI.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, cast

from app.services.organization_access import (
    OrganizationAccessError,
    RosterApprovalInput,
    email_matches_exact_domain,
    lock_provider_practice_domain,
    lock_roster_enrollment_email,
    normalize_email,
)

from activemodel.session_manager import get_session
from app.models.doctor import Doctor
from app.models.organization_access import (
    OrganizationRosterApproval,
    ProviderOrganization,
)
from app.models.practice import Practice, PracticeMember, PracticeRole
from app.models.practice_access_audit import (
    PracticeAccessAction,
    PracticeAccessAudit,
)
from sqlalchemy import func
from sqlmodel import Session, col, select

JOIN_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
JOIN_CODE_LENGTH = 8
JOIN_CODE_ATTEMPTS = 20


class ProviderPracticeBootstrapError(ValueError):
    """A bootstrap invariant failed without changing persistent state."""


@dataclass(frozen=True)
class ProviderPracticeBootstrapPlan:
    legal_name: str
    email_domain: str
    owner_doctor_id: Any
    roster_sha256: str
    roster_member_count: int
    bootstrap_ref: str
    operator_ref: str
    reason: str
    plan_sha256: str
    already_applied: bool = False
    practice_id: Any | None = None
    member_id: Any | None = None
    audit_id: Any | None = None


@dataclass(frozen=True)
class ProviderPracticeBootstrapResult:
    practice: Practice
    owner_member: PracticeMember
    audit: PracticeAccessAudit
    plan_sha256: str
    roster_sha256: str
    already_applied: bool


def _required(value: str, *, field: str, limit: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise ProviderPracticeBootstrapError(f"{field} is required")
    if len(normalized) > limit:
        raise ProviderPracticeBootstrapError(f"{field} exceeds {limit} characters")
    return normalized


def _sha256(value: str, *, field: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise ProviderPracticeBootstrapError(
            f"{field} must be a 64-character lowercase hex digest"
        )
    return normalized


def _domain(value: str) -> str:
    normalized = value.strip().lower().lstrip("@")
    if (
        not normalized
        or len(normalized) > 253
        or "@" in normalized
        or "." not in normalized
    ):
        raise ProviderPracticeBootstrapError("email_domain is invalid")
    return normalized


def _plan_sha256(
    *,
    legal_name: str,
    email_domain: str,
    owner_doctor_id: Any,
    roster_sha256: str,
    roster_member_count: int,
    bootstrap_ref: str,
    operator_ref: str,
    reason: str,
) -> str:
    payload = {
        "action": PracticeAccessAction.provider_practice_bootstrapped.value,
        "bootstrap_ref": bootstrap_ref,
        "email_domain": email_domain,
        "fixed_practice_settings": {
            "is_solo": False,
            "negotiated_baa_required": True,
            "origin_channel": "direct",
            "pastoral_pack": False,
            "public_onboarding_required": False,
        },
        "legal_name": legal_name,
        "operator_ref": operator_ref,
        "owner_doctor_id": str(owner_doctor_id),
        "reason": reason,
        "roster_member_count": roster_member_count,
        "roster_sha256": roster_sha256,
        "schema_version": 1,
    }
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _existing_bootstrap_audit(
    session: Session,
    bootstrap_ref: str,
    *,
    lock: bool = False,
) -> PracticeAccessAudit | None:
    statement = select(PracticeAccessAudit).where(
        PracticeAccessAudit.action
        == PracticeAccessAction.provider_practice_bootstrapped.value,
        PracticeAccessAudit.document_reference == bootstrap_ref,
    )
    if lock:
        statement = statement.with_for_update()
    return cast(PracticeAccessAudit | None, session.exec(statement).one_or_none())


def _lock_bootstrap_reference(session: Session, bootstrap_ref: str) -> None:
    """Serialize first apply and retries for one operator bootstrap reference."""

    lock_key = int.from_bytes(
        hashlib.sha256(
            f"provider-practice-bootstrap:{bootstrap_ref}".encode("utf-8")
        ).digest()[:8],
        byteorder="big",
        signed=True,
    )
    session.exec(select(func.pg_advisory_xact_lock(lock_key))).one()


def _applied_result(
    session: Session,
    *,
    audit: PracticeAccessAudit,
    owner_doctor_id: Any,
    legal_name: str,
    email_domain: str,
    plan_sha256: str,
    roster_sha256: str,
) -> ProviderPracticeBootstrapResult:
    if audit.evidence_sha256 != plan_sha256:
        raise ProviderPracticeBootstrapError(
            "bootstrap reference was already used for a different plan"
        )
    practice = session.exec(
        select(Practice).where(Practice.id == audit.practice_id)
    ).one_or_none()
    member = session.exec(
        select(PracticeMember).where(
            PracticeMember.practice_id == audit.practice_id,
            PracticeMember.doctor_id == owner_doctor_id,
        )
    ).one_or_none()
    if (
        practice is None
        or member is None
        or practice.legal_name != legal_name
        or practice.email_domain != email_domain
        or practice.is_solo is not False
        or practice.origin_channel != "direct"
        or practice.pastoral_pack is not False
        or practice.public_onboarding_required is not False
        or not practice.negotiated_baa_required
        or member.role != PracticeRole.owner.value
        or member.access_revoked_at is not None
    ):
        raise ProviderPracticeBootstrapError(
            "prior bootstrap evidence conflicts with current practice state"
        )
    return ProviderPracticeBootstrapResult(
        practice=practice,
        owner_member=member,
        audit=audit,
        plan_sha256=plan_sha256,
        roster_sha256=roster_sha256,
        already_applied=True,
    )


def _normalize_roster(
    entries: list[RosterApprovalInput],
    *,
    email_domain: str,
) -> tuple[str, ...]:
    if not entries:
        raise ProviderPracticeBootstrapError("roster must contain at least one member")
    emails: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        if entry.expires_at is not None:
            raise ProviderPracticeBootstrapError(
                "scheduled roster expiry is not supported"
            )
        try:
            email = normalize_email(entry.email)
        except OrganizationAccessError as exc:
            raise ProviderPracticeBootstrapError(
                "roster contains an invalid email"
            ) from exc
        if email in seen:
            raise ProviderPracticeBootstrapError("roster contains a duplicate email")
        if not email_matches_exact_domain(email, email_domain):
            raise ProviderPracticeBootstrapError(
                "one or more roster accounts do not match the practice domain"
            )
        seen.add(email)
        emails.append(email)
    return tuple(sorted(emails))


def _validate_unapplied_state(
    session: Session,
    *,
    owner_doctor_id: Any,
    owner_email: str,
    roster_emails: tuple[str, ...],
    email_domain: str,
) -> Doctor:
    owner = session.exec(
        select(Doctor).where(Doctor.id == owner_doctor_id)
    ).one_or_none()
    if owner is None or owner.deleted_at is not None:
        raise ProviderPracticeBootstrapError("owner account is not active")
    try:
        stored_email = normalize_email(owner.email or "")
    except OrganizationAccessError as exc:
        raise ProviderPracticeBootstrapError("owner account email is invalid") from exc
    if stored_email != owner_email or owner_email not in roster_emails:
        raise ProviderPracticeBootstrapError(
            "owner confirmation does not match an exact roster account"
        )
    if (
        session.exec(
            select(PracticeMember).where(PracticeMember.doctor_id == owner.id)
        ).first()
        is not None
    ):
        raise ProviderPracticeBootstrapError(
            "owner account already has a practice membership"
        )
    if (
        session.exec(
            select(Practice).where(
                func.lower(col(Practice.email_domain)) == email_domain
            )
        ).first()
        is not None
    ):
        raise ProviderPracticeBootstrapError(
            "a practice already exists for this email domain"
        )
    if (
        session.exec(
            select(ProviderOrganization).where(
                ProviderOrganization.email_domain == email_domain
            )
        ).first()
        is not None
    ):
        raise ProviderPracticeBootstrapError(
            "a provider organization already exists for this email domain"
        )

    # Every roster identity must already be a live application account and
    # unattached.  Error text deliberately omits the address that failed.
    for roster_email in roster_emails:
        rostered_doctor = session.exec(
            select(Doctor).where(func.lower(col(Doctor.email)) == roster_email)
        ).one_or_none()
        if rostered_doctor is None or rostered_doctor.deleted_at is not None:
            raise ProviderPracticeBootstrapError(
                "one or more roster accounts are missing or inactive"
            )
        if (
            session.exec(
                select(PracticeMember).where(
                    PracticeMember.doctor_id == rostered_doctor.id
                )
            ).first()
            is not None
        ):
            raise ProviderPracticeBootstrapError(
                "one or more roster accounts already have a practice membership"
            )
        reserved = session.exec(
            select(OrganizationRosterApproval).where(
                OrganizationRosterApproval.approved_email == roster_email,
                OrganizationRosterApproval.revoked_at == None,  # noqa: E711
            )
        ).first()
        if reserved is not None:
            raise ProviderPracticeBootstrapError(
                "one or more roster accounts already have managed access"
            )
    return owner


def plan_provider_practice_bootstrap(
    *,
    entries: list[RosterApprovalInput],
    roster_sha256: str,
    owner_doctor_id: Any,
    owner_email_confirmation: str,
    verified_primary_email: str | None,
    legal_name: str,
    email_domain: str,
    bootstrap_ref: str,
    operator_ref: str,
    reason: str,
) -> ProviderPracticeBootstrapPlan:
    """Validate and hash an exact bootstrap plan without writing anything."""

    name = _required(legal_name, field="legal_name", limit=160)
    domain = _domain(email_domain)
    reference = _required(bootstrap_ref, field="bootstrap_ref", limit=255)
    operator = _required(operator_ref, field="operator_ref", limit=255)
    explanation = _required(reason, field="reason", limit=512)
    digest = _sha256(roster_sha256, field="roster_sha256")
    roster_emails = _normalize_roster(entries, email_domain=domain)
    try:
        owner_email = normalize_email(owner_email_confirmation)
    except OrganizationAccessError as exc:
        raise ProviderPracticeBootstrapError(
            "owner email confirmation is invalid"
        ) from exc
    try:
        verified_email = normalize_email(verified_primary_email or "")
    except OrganizationAccessError as exc:
        raise ProviderPracticeBootstrapError(
            "owner Clerk primary email is not verified"
        ) from exc
    if verified_email != owner_email:
        raise ProviderPracticeBootstrapError(
            "owner Clerk primary email does not match the confirmed account"
        )

    plan_digest = _plan_sha256(
        legal_name=name,
        email_domain=domain,
        owner_doctor_id=owner_doctor_id,
        roster_sha256=digest,
        roster_member_count=len(roster_emails),
        bootstrap_ref=reference,
        operator_ref=operator,
        reason=explanation,
    )
    with get_session() as session:
        existing_audit = _existing_bootstrap_audit(session, reference)
        if existing_audit is not None:
            result = _applied_result(
                session,
                audit=existing_audit,
                owner_doctor_id=owner_doctor_id,
                legal_name=name,
                email_domain=domain,
                plan_sha256=plan_digest,
                roster_sha256=digest,
            )
            return ProviderPracticeBootstrapPlan(
                legal_name=name,
                email_domain=domain,
                owner_doctor_id=owner_doctor_id,
                roster_sha256=digest,
                roster_member_count=len(roster_emails),
                bootstrap_ref=reference,
                operator_ref=operator,
                reason=explanation,
                plan_sha256=plan_digest,
                already_applied=True,
                practice_id=result.practice.id,
                member_id=result.owner_member.id,
                audit_id=result.audit.id,
            )
        _validate_unapplied_state(
            session,
            owner_doctor_id=owner_doctor_id,
            owner_email=owner_email,
            roster_emails=roster_emails,
            email_domain=domain,
        )
    return ProviderPracticeBootstrapPlan(
        legal_name=name,
        email_domain=domain,
        owner_doctor_id=owner_doctor_id,
        roster_sha256=digest,
        roster_member_count=len(roster_emails),
        bootstrap_ref=reference,
        operator_ref=operator,
        reason=explanation,
        plan_sha256=plan_digest,
    )


def _new_join_code(session: Session) -> str:
    for _ in range(JOIN_CODE_ATTEMPTS):
        candidate = "".join(
            secrets.choice(JOIN_CODE_ALPHABET) for _ in range(JOIN_CODE_LENGTH)
        )
        if (
            session.exec(
                select(Practice).where(Practice.join_code == candidate)
            ).first()
            is None
        ):
            return candidate
    raise ProviderPracticeBootstrapError("could not allocate a unique join code")


def bootstrap_provider_practice(
    *,
    entries: list[RosterApprovalInput],
    roster_sha256: str,
    owner_doctor_id: Any,
    owner_email_confirmation: str,
    verified_primary_email: str | None,
    legal_name: str,
    email_domain: str,
    bootstrap_ref: str,
    operator_ref: str,
    reason: str,
    confirm_roster_sha256: str,
    confirm_plan_sha256: str,
    now: datetime | None = None,
) -> ProviderPracticeBootstrapResult:
    """Apply one exact, previously reviewed bootstrap plan atomically."""

    plan = plan_provider_practice_bootstrap(
        entries=entries,
        roster_sha256=roster_sha256,
        owner_doctor_id=owner_doctor_id,
        owner_email_confirmation=owner_email_confirmation,
        verified_primary_email=verified_primary_email,
        legal_name=legal_name,
        email_domain=email_domain,
        bootstrap_ref=bootstrap_ref,
        operator_ref=operator_ref,
        reason=reason,
    )
    if (
        _sha256(confirm_roster_sha256, field="confirm_roster_sha256")
        != plan.roster_sha256
    ):
        raise ProviderPracticeBootstrapError(
            "confirmed roster digest does not match the current manifest"
        )
    if _sha256(confirm_plan_sha256, field="confirm_plan_sha256") != plan.plan_sha256:
        raise ProviderPracticeBootstrapError(
            "confirmed plan digest does not match the current plan"
        )

    with get_session() as session:
        # A missing row cannot be protected by SELECT ... FOR UPDATE. Lock the
        # logical idempotency key first so concurrent first applies serialize,
        # then re-read the evidence inside the same transaction.
        _lock_bootstrap_reference(session, plan.bootstrap_ref)
        existing_audit = _existing_bootstrap_audit(
            session,
            plan.bootstrap_ref,
            lock=True,
        )
        if existing_audit is not None:
            return _applied_result(
                session,
                audit=existing_audit,
                owner_doctor_id=plan.owner_doctor_id,
                legal_name=plan.legal_name,
                email_domain=plan.email_domain,
                plan_sha256=plan.plan_sha256,
                roster_sha256=plan.roster_sha256,
            )

        roster_emails = _normalize_roster(entries, email_domain=plan.email_domain)
        owner_email = normalize_email(owner_email_confirmation)
        # Match self-service create's lock order: identity, domain, then Doctor.
        lock_roster_enrollment_email(session, owner_email)
        lock_provider_practice_domain(session, plan.email_domain)
        owner = session.exec(
            select(Doctor).where(Doctor.id == plan.owner_doctor_id).with_for_update()
        ).one_or_none()
        if owner is None:
            raise ProviderPracticeBootstrapError("owner account is not active")
        _validate_unapplied_state(
            session,
            owner_doctor_id=owner.id,
            owner_email=owner_email,
            roster_emails=roster_emails,
            email_domain=plan.email_domain,
        )

        current = now or datetime.now(timezone.utc)
        practice = Practice(
            legal_name=plan.legal_name,
            npi=None,
            city=None,
            region=None,
            is_solo=False,
            join_code=_new_join_code(session),
            email_domain=plan.email_domain,
            origin_channel="direct",
            pastoral_pack=False,
            public_onboarding_required=False,
            negotiated_baa_required=True,
        )
        session.add(practice)
        session.flush()
        owner_member = PracticeMember(
            practice_id=practice.id,
            doctor_id=owner.id,
            role=PracticeRole.owner.value,
        )
        session.add(owner_member)
        session.flush()
        audit = PracticeAccessAudit(
            practice_id=practice.id,
            action=PracticeAccessAction.provider_practice_bootstrapped.value,
            operator_ref=plan.operator_ref,
            reason=plan.reason,
            effective_at=current,
            document_reference=plan.bootstrap_ref,
            evidence_sha256=plan.plan_sha256,
        )
        session.add(audit)
        session.commit()
        session.refresh(practice)
        session.refresh(owner_member)
        session.refresh(audit)
        return ProviderPracticeBootstrapResult(
            practice=practice,
            owner_member=owner_member,
            audit=audit,
            plan_sha256=plan.plan_sha256,
            roster_sha256=plan.roster_sha256,
            already_applied=False,
        )
