"""Audited provider-organization roster access.

Organization roster authorization is deliberately separate from workforce
attestation.  A roster-authorized clinician has not executed or acknowledged
the BAA personally; access rests on an in-force negotiated BAA for the target
practice plus the organization's explicit roster approval.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, cast

from app.services.public_entitlements import lock_practice_for_active_seat_mutation

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
from sqlalchemy import func, or_
from sqlmodel import Session, col, select

ACCESS_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
ACCESS_CODE_LENGTH = 12
ACCESS_CODE_SCRYPT_N = 2**14
ACCESS_CODE_SCRYPT_R = 8
ACCESS_CODE_SCRYPT_P = 1
ACCESS_CODE_SALT_BYTES = 16
ACCESS_RECORD_RETENTION_YEARS = 7


class OrganizationAccessError(ValueError):
    """A fail-closed organization access validation error."""


@dataclass(frozen=True)
class OrganizationAccessOpportunity:
    organization: ProviderOrganization
    approval: OrganizationRosterApproval
    practice: Practice


@dataclass(frozen=True)
class RosterApprovalInput:
    email: str
    expires_at: datetime | None = None


@dataclass(frozen=True)
class RosterAssignmentInput:
    """One schema-v2 roster identity and its explicit practice destination."""

    email: str
    target_practice_id: Any
    expires_at: datetime | None = None


@dataclass(frozen=True)
class RosterImportResult:
    created: int
    unchanged: int
    approval_ids: tuple[str, ...]


def _require_pilot_organization_prerequisites(
    practice: Practice,
    *,
    at: datetime | None,
) -> None:
    if not practice.negotiated_baa_in_force(at=at):
        raise OrganizationAccessError("an in-force negotiated BAA is required")
    if not practice.billing_exemption_active(at=at):
        raise OrganizationAccessError("an active billing exemption is required")

    # An exemption grants platform entitlement; it does not cancel Stripe.
    # Refuse the pilot cutover while an existing checkout or billable
    # subscription could continue charging in parallel.
    from app.services import stripe_billing

    if (
        practice.stripe_checkout_request_json is not None
        or practice.stripe_checkout_session_id is not None
    ):
        raise OrganizationAccessError(
            "an existing Stripe checkout must be resolved before organization setup"
        )
    if stripe_billing.has_nonterminal_subscription(practice):
        raise OrganizationAccessError(
            "an existing Stripe subscription must be resolved before organization setup"
        )


def configure_provider_organization(
    practice: Practice,
    *,
    legal_name: str,
    slug: str,
    email_domain: str,
    operator_ref: str,
    reason: str,
    access_code: str | None = None,
    now: datetime | None = None,
) -> tuple[ProviderOrganization, str, PracticeAccessAudit]:
    """Create an organization above one existing practice and issue its code."""

    operator = _required(operator_ref, field="operator_ref", limit=255)
    explanation = _required(reason, field="reason", limit=512)
    name = _required(legal_name, field="legal_name", limit=160)
    normalized_slug = slug.strip().lower()
    normalized_domain = email_domain.strip().lower().lstrip("@")
    if (
        not normalized_slug
        or len(normalized_slug) > 64
        or any(
            ch not in "abcdefghijklmnopqrstuvwxyz0123456789-" for ch in normalized_slug
        )
    ):
        raise OrganizationAccessError(
            "slug must be 1 to 64 lowercase letters, numbers, or hyphens"
        )
    if (
        not normalized_domain
        or len(normalized_domain) > 253
        or practice.email_domain.lower() != normalized_domain
    ):
        raise OrganizationAccessError(
            "organization domain must exactly match the target practice domain"
        )
    if practice.organization_id is not None:
        raise OrganizationAccessError("practice is already linked to an organization")
    _require_pilot_organization_prerequisites(practice, at=now)

    current = now or datetime.now(timezone.utc)
    code = _issued_access_code(access_code)
    salt = secrets.token_bytes(ACCESS_CODE_SALT_BYTES)
    organization = ProviderOrganization(
        legal_name=name,
        slug=normalized_slug,
        email_domain=normalized_domain,
        status="active",
        access_code_salt=salt.hex(),
        access_code_digest=_access_code_digest(code, salt),
        access_code_version=1,
        access_code_created_at=current,
    )
    with get_session() as session:
        locked_practice = session.exec(
            select(Practice).where(Practice.id == practice.id).with_for_update()
        ).one()
        lock_provider_practice_domain(session, normalized_domain)
        domain_practice_ids = {
            str(practice_id)
            for practice_id in session.exec(
                select(col(Practice.id)).where(
                    func.lower(func.btrim(col(Practice.email_domain)))
                    == normalized_domain
                )
            ).all()
        }
        if domain_practice_ids != {str(locked_practice.id)}:
            raise OrganizationAccessError(
                "single-practice setup requires exactly one practice for the domain"
            )
        if (
            session.exec(
                select(ProviderOrganization).where(
                    or_(
                        col(ProviderOrganization.slug) == normalized_slug,
                        col(ProviderOrganization.email_domain) == normalized_domain,
                    )
                )
            ).first()
            is not None
        ):
            raise OrganizationAccessError(
                "organization slug or email domain is already configured"
            )
        if locked_practice.organization_id is not None:
            raise OrganizationAccessError(
                "practice became linked to an organization during setup"
            )
        _require_pilot_organization_prerequisites(locked_practice, at=current)
        session.add(organization)
        session.flush()
        locked_practice.organization_id = organization.id
        audit = PracticeAccessAudit(
            practice_id=locked_practice.id,
            action=PracticeAccessAction.provider_organization_configured.value,
            operator_ref=operator,
            reason=explanation,
            effective_at=current,
            document_reference=str(organization.id),
        )
        session.add(locked_practice)
        session.add(audit)
        session.commit()
        session.refresh(organization)
        session.refresh(audit)
    practice.refresh()
    return organization, code, audit


def _normalize_multi_practice_plan_inputs(
    assignments: list[RosterAssignmentInput],
    *,
    legal_name: str,
    slug: str,
    email_domain: str,
    operator_ref: str,
    reason: str,
    source_ref: str,
    source_sha256: str,
) -> tuple[
    str,
    str,
    str,
    str,
    str,
    str,
    tuple[tuple[str, str, datetime | None], ...],
    str,
]:
    """Normalize every mutation input represented by the canonical plan."""

    operator = _required(operator_ref, field="operator_ref", limit=255)
    explanation = _required(reason, field="reason", limit=512)
    name = _required(legal_name, field="legal_name", limit=160)
    source = _required(source_ref, field="source_ref", limit=255)
    normalized_slug = slug.strip().lower()
    normalized_domain = email_domain.strip().lower().lstrip("@")
    digest = source_sha256.strip().lower()
    if (
        not normalized_slug
        or len(normalized_slug) > 64
        or any(
            ch not in "abcdefghijklmnopqrstuvwxyz0123456789-" for ch in normalized_slug
        )
    ):
        raise OrganizationAccessError(
            "slug must be 1 to 64 lowercase letters, numbers, or hyphens"
        )
    if (
        not normalized_domain
        or len(normalized_domain) > 253
        or "@" in normalized_domain
        or "." not in normalized_domain
    ):
        raise OrganizationAccessError("a valid organization email domain is required")
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise OrganizationAccessError("source_sha256 must be a 64-character hex digest")
    if not assignments:
        raise OrganizationAccessError("roster must contain at least one assignment")

    normalized_assignments: list[tuple[str, str, datetime | None]] = []
    seen_emails: set[str] = set()
    for assignment in assignments:
        email = normalize_email(assignment.email)
        target_practice_id = str(assignment.target_practice_id)
        if email in seen_emails:
            raise OrganizationAccessError("roster contains a duplicate email")
        if not email_matches_exact_domain(email, normalized_domain):
            raise OrganizationAccessError(
                "one or more roster accounts do not match the organization domain"
            )
        if assignment.expires_at is not None:
            raise OrganizationAccessError(
                "scheduled roster expiry is not supported; use explicit offboarding"
            )
        seen_emails.add(email)
        normalized_assignments.append(
            (email, target_practice_id, assignment.expires_at)
        )

    return (
        operator,
        explanation,
        name,
        normalized_slug,
        normalized_domain,
        source,
        tuple(sorted(normalized_assignments)),
        digest,
    )


def _multi_practice_plan_sha256_from_normalized(
    *,
    operator_ref: str,
    reason: str,
    legal_name: str,
    organization_slug: str,
    email_domain: str,
    source_ref: str,
    assignments: tuple[tuple[str, str, datetime | None], ...],
    roster_sha256: str,
) -> str:
    payload = {
        "action": "configure_provider_organization_with_roster",
        "email_domain": email_domain,
        "fixed_configuration": {
            "access_code_version": 1,
            "organization_status": "active",
        },
        "legal_name": legal_name,
        "manifest_schema_version": 2,
        "members": [
            {"email": email, "target_practice_id": target_practice_id}
            for email, target_practice_id, _ in assignments
        ],
        "operator_ref": operator_ref,
        "organization_slug": organization_slug,
        "plan_schema_version": 1,
        "reason": reason,
        "roster_sha256": roster_sha256,
        "source_ref": source_ref,
    }
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def provider_organization_with_roster_plan_sha256(
    assignments: list[RosterAssignmentInput],
    *,
    organization_slug: str,
    email_domain: str,
    legal_name: str,
    operator_ref: str,
    reason: str,
    source_ref: str,
    roster_sha256: str,
) -> str:
    """Return the canonical digest for one normalized multi-practice mutation."""

    (
        operator,
        explanation,
        name,
        normalized_slug,
        normalized_domain,
        source,
        rows,
        digest,
    ) = _normalize_multi_practice_plan_inputs(
        assignments,
        legal_name=legal_name,
        slug=organization_slug,
        email_domain=email_domain,
        operator_ref=operator_ref,
        reason=reason,
        source_ref=source_ref,
        source_sha256=roster_sha256,
    )
    return _multi_practice_plan_sha256_from_normalized(
        operator_ref=operator,
        reason=explanation,
        legal_name=name,
        organization_slug=normalized_slug,
        email_domain=normalized_domain,
        source_ref=source,
        assignments=rows,
        roster_sha256=digest,
    )


def _normalize_multi_practice_configuration(
    practices: list[Practice],
    assignments: list[RosterAssignmentInput],
    *,
    legal_name: str,
    slug: str,
    email_domain: str,
    operator_ref: str,
    reason: str,
    source_ref: str,
    source_sha256: str,
) -> tuple[
    str,
    str,
    str,
    str,
    str,
    str,
    tuple[Any, ...],
    tuple[tuple[str, str, datetime | None], ...],
    str,
]:
    """Normalize the complete immutable input to a multi-practice cutover."""

    (
        operator,
        explanation,
        name,
        normalized_slug,
        normalized_domain,
        source,
        normalized_assignments,
        digest,
    ) = _normalize_multi_practice_plan_inputs(
        assignments,
        legal_name=legal_name,
        slug=slug,
        email_domain=email_domain,
        operator_ref=operator_ref,
        reason=reason,
        source_ref=source_ref,
        source_sha256=source_sha256,
    )
    if len(practices) < 2:
        raise OrganizationAccessError(
            "multi-practice configuration requires at least two practices"
        )
    practice_ids = tuple(sorted((practice.id for practice in practices), key=str))
    selected = {str(practice_id) for practice_id in practice_ids}
    if len(selected) != len(practice_ids):
        raise OrganizationAccessError("target practices must be unique")
    if any(
        target_practice_id not in selected
        for _, target_practice_id, _ in normalized_assignments
    ):
        raise OrganizationAccessError(
            "roster assignment targets a practice outside this configuration"
        )
    return (
        operator,
        explanation,
        name,
        normalized_slug,
        normalized_domain,
        source,
        practice_ids,
        normalized_assignments,
        digest,
    )


def _lock_and_validate_multi_practice_configuration(
    session: Session,
    *,
    practice_ids: tuple[Any, ...],
    assignments: tuple[tuple[str, str, datetime | None], ...],
    slug: str,
    email_domain: str,
    now: datetime,
) -> tuple[Practice, ...]:
    """Lock and revalidate every mutable authorization input before cutover."""

    locked_practices: list[Practice] = []
    for practice_id in practice_ids:
        practice = session.exec(
            select(Practice).where(Practice.id == practice_id).with_for_update()
        ).one_or_none()
        if practice is None:
            raise OrganizationAccessError("target practice no longer exists")
        locked_practices.append(practice)

    for email, _, _ in assignments:
        lock_roster_enrollment_email(session, email)
    # Self-service practice creation takes its email lock before this domain
    # lock. Taking the same order prevents a cutover/create deadlock; the
    # complete same-domain practice-set check below detects whichever won.
    lock_provider_practice_domain(session, email_domain)

    all_domain_practice_ids = {
        str(practice_id)
        for practice_id in session.exec(
            select(col(Practice.id)).where(
                func.lower(func.btrim(col(Practice.email_domain))) == email_domain
            )
        ).all()
    }
    if all_domain_practice_ids != {str(practice_id) for practice_id in practice_ids}:
        raise OrganizationAccessError(
            "all existing practices for the organization domain must be included"
        )

    active_domain_doctors = cast(
        list[Doctor],
        list(
            session.exec(
                select(Doctor)
                .where(
                    Doctor.deleted_at == None,  # noqa: E711
                    func.split_part(func.lower(func.btrim(col(Doctor.email))), "@", 2)
                    == email_domain,
                )
                .order_by(cast(Any, Doctor.id))
                .with_for_update()
            ).all()
        ),
    )
    assigned_emails = {email for email, _, _ in assignments}
    active_domain_emails: set[str] = set()
    for doctor in active_domain_doctors:
        try:
            active_domain_emails.add(normalize_email(doctor.email or ""))
        except OrganizationAccessError as exc:
            raise OrganizationAccessError(
                "an active organization-domain account has an invalid email"
            ) from exc
    if not active_domain_emails.issubset(assigned_emails):
        raise OrganizationAccessError(
            "every active organization-domain account requires an explicit roster "
            "assignment"
        )

    existing_organization = session.exec(
        select(ProviderOrganization)
        .where(
            or_(
                col(ProviderOrganization.slug) == slug,
                col(ProviderOrganization.email_domain) == email_domain,
            )
        )
        .with_for_update()
    ).first()
    if existing_organization is not None:
        raise OrganizationAccessError(
            "organization slug or email domain is already configured"
        )

    assignment_target_by_email = {
        email: target_practice_id for email, target_practice_id, _ in assignments
    }
    for practice in locked_practices:
        if practice.organization_id is not None:
            raise OrganizationAccessError(
                "target practice is already linked to an organization"
            )
        # The composite organization/domain foreign key requires exact stored
        # normalization; silently changing a tenant domain is out of scope.
        if practice.email_domain != email_domain:
            raise OrganizationAccessError(
                "organization domain must exactly match every target practice domain"
            )
        _require_pilot_organization_prerequisites(practice, at=now)

    locked_members: list[PracticeMember] = []
    for practice_id in practice_ids:
        locked_members.extend(
            cast(
                list[PracticeMember],
                list(
                    session.exec(
                        select(PracticeMember)
                        .where(
                            PracticeMember.practice_id == practice_id,
                            PracticeMember.access_revoked_at == None,  # noqa: E711
                        )
                        .order_by(cast(Any, PracticeMember.doctor_id))
                        .with_for_update()
                    ).all()
                ),
            )
        )

    members_by_doctor_id = {str(member.doctor_id): member for member in locked_members}
    for email, target_practice_id, _ in assignments:
        existing_approval = session.exec(
            select(OrganizationRosterApproval)
            .where(
                OrganizationRosterApproval.approved_email == email,
                OrganizationRosterApproval.revoked_at == None,  # noqa: E711
            )
            .with_for_update()
        ).one_or_none()
        if existing_approval is not None:
            raise OrganizationAccessError(
                "roster account already has an active organization assignment"
            )

        doctor = session.exec(
            select(Doctor)
            .where(func.lower(col(Doctor.email)) == email)
            .with_for_update()
        ).one_or_none()
        if doctor is None:
            continue
        if doctor.deleted_at is not None:
            raise OrganizationAccessError("roster account is already offboarded")
        member = members_by_doctor_id.get(str(doctor.id))
        if member is None:
            member = session.exec(
                select(PracticeMember)
                .where(PracticeMember.doctor_id == doctor.id)
                .with_for_update()
            ).one_or_none()
        if member is not None and str(member.practice_id) != target_practice_id:
            raise OrganizationAccessError(
                "roster account belongs to a different target practice"
            )
        if member is not None and member.access_revoked_at is not None:
            raise OrganizationAccessError("roster account has revoked practice access")

    owners_by_practice = {str(practice_id): 0 for practice_id in practice_ids}
    for member in locked_members:
        doctor = session.exec(
            select(Doctor).where(Doctor.id == member.doctor_id).with_for_update()
        ).one_or_none()
        if doctor is None or doctor.deleted_at is not None:
            raise OrganizationAccessError(
                "an active practice member has no active account"
            )
        email = normalize_email(doctor.email or "")
        target_practice_id = assignment_target_by_email.get(email)
        if target_practice_id != str(member.practice_id):
            raise OrganizationAccessError(
                "every active practice member must retain its current target practice"
            )
        if member.role == PracticeRole.owner.value:
            owners_by_practice[str(member.practice_id)] += 1

    if any(count == 0 for count in owners_by_practice.values()):
        raise OrganizationAccessError(
            "every target practice must retain at least one existing owner"
        )

    return tuple(locked_practices)


def validate_provider_organization_with_roster(
    practices: list[Practice],
    assignments: list[RosterAssignmentInput],
    *,
    legal_name: str,
    slug: str,
    email_domain: str,
    operator_ref: str,
    reason: str,
    source_ref: str,
    source_sha256: str,
    now: datetime | None = None,
) -> None:
    """Run the apply validations under locks without changing persistent state."""

    normalized = _normalize_multi_practice_configuration(
        practices,
        assignments,
        legal_name=legal_name,
        slug=slug,
        email_domain=email_domain,
        operator_ref=operator_ref,
        reason=reason,
        source_ref=source_ref,
        source_sha256=source_sha256,
    )
    _, _, _, normalized_slug, normalized_domain, _, practice_ids, rows, _ = normalized
    with get_session() as session:
        _lock_and_validate_multi_practice_configuration(
            session,
            practice_ids=practice_ids,
            assignments=rows,
            slug=normalized_slug,
            email_domain=normalized_domain,
            now=now or datetime.now(timezone.utc),
        )
        session.rollback()


def configure_provider_organization_with_roster(
    practices: list[Practice],
    assignments: list[RosterAssignmentInput],
    *,
    legal_name: str,
    slug: str,
    email_domain: str,
    operator_ref: str,
    reason: str,
    source_ref: str,
    source_sha256: str,
    plan_sha256: str,
    access_code: str | None = None,
    now: datetime | None = None,
) -> tuple[
    ProviderOrganization,
    str,
    RosterImportResult,
    tuple[PracticeAccessAudit, ...],
]:
    """Atomically create one organization, link all practices, and import v2 rows."""

    normalized = _normalize_multi_practice_configuration(
        practices,
        assignments,
        legal_name=legal_name,
        slug=slug,
        email_domain=email_domain,
        operator_ref=operator_ref,
        reason=reason,
        source_ref=source_ref,
        source_sha256=source_sha256,
    )
    (
        operator,
        explanation,
        name,
        normalized_slug,
        normalized_domain,
        source,
        practice_ids,
        rows,
        digest,
    ) = normalized
    confirmed_plan_digest = plan_sha256.strip().lower()
    if len(confirmed_plan_digest) != 64 or any(
        character not in "0123456789abcdef" for character in confirmed_plan_digest
    ):
        raise OrganizationAccessError("plan_sha256 must be a 64-character hex digest")
    expected_plan_digest = _multi_practice_plan_sha256_from_normalized(
        operator_ref=operator,
        reason=explanation,
        legal_name=name,
        organization_slug=normalized_slug,
        email_domain=normalized_domain,
        source_ref=source,
        assignments=rows,
        roster_sha256=digest,
    )
    if not hmac.compare_digest(confirmed_plan_digest, expected_plan_digest):
        raise OrganizationAccessError(
            "confirmed plan_sha256 does not match the normalized mutation plan"
        )
    current = now or datetime.now(timezone.utc)
    code = _issued_access_code(access_code)
    salt = secrets.token_bytes(ACCESS_CODE_SALT_BYTES)
    organization = ProviderOrganization(
        legal_name=name,
        slug=normalized_slug,
        email_domain=normalized_domain,
        status="active",
        access_code_salt=salt.hex(),
        access_code_digest=_access_code_digest(code, salt),
        access_code_version=1,
        access_code_created_at=current,
    )
    configuration_audits: list[PracticeAccessAudit] = []
    approval_ids: list[str] = []
    with get_session() as session:
        locked_practices = _lock_and_validate_multi_practice_configuration(
            session,
            practice_ids=practice_ids,
            assignments=rows,
            slug=normalized_slug,
            email_domain=normalized_domain,
            now=current,
        )
        practice_by_id = {str(practice.id): practice for practice in locked_practices}
        session.add(organization)
        session.flush()

        for practice in locked_practices:
            practice.organization_id = organization.id
            session.add(practice)
            audit = PracticeAccessAudit(
                practice_id=practice.id,
                action=PracticeAccessAction.provider_organization_configured.value,
                operator_ref=operator,
                reason=explanation,
                effective_at=current,
                document_reference=str(organization.id),
                evidence_sha256=expected_plan_digest,
            )
            configuration_audits.append(audit)
            session.add(audit)
        session.flush()

        practices_with_assignments: set[str] = set()
        for email, target_practice_id, expires_at in rows:
            target_practice = practice_by_id[target_practice_id]
            prior_approval = session.exec(
                select(OrganizationRosterApproval)
                .where(
                    OrganizationRosterApproval.organization_id == organization.id,
                    OrganizationRosterApproval.target_practice_id == target_practice.id,
                    OrganizationRosterApproval.approved_email == email,
                )
                .with_for_update()
            ).first()
            if prior_approval is not None:
                if prior_approval.revoked_at is not None:
                    raise OrganizationAccessError(
                        "a roster approval was previously revoked; an explicit "
                        "reauthorization workflow is required"
                    )
                raise OrganizationAccessError(
                    "roster account already has an active organization assignment"
                )
            approval = OrganizationRosterApproval(
                organization_id=organization.id,
                target_practice_id=target_practice.id,
                approved_email=email,
                approved_at=current,
                approved_by_ref=operator,
                source_ref=source,
                source_sha256=digest,
                expires_at=expires_at,
            )
            session.add(approval)
            session.flush()
            approval_ids.append(str(approval.id))
            practices_with_assignments.add(target_practice_id)

        for practice_id in sorted(practices_with_assignments):
            session.add(
                PracticeAccessAudit(
                    practice_id=practice_by_id[practice_id].id,
                    action=PracticeAccessAction.organization_roster_imported.value,
                    operator_ref=operator,
                    reason=explanation,
                    effective_at=current,
                    document_reference=source,
                    evidence_sha256=digest,
                )
            )
        session.commit()
        session.refresh(organization)
        for audit in configuration_audits:
            session.refresh(audit)

    for practice in practices:
        practice.refresh()
    return (
        organization,
        code,
        RosterImportResult(len(approval_ids), 0, tuple(approval_ids)),
        tuple(configuration_audits),
    )


def normalize_email(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized or len(normalized) > 320 or normalized.count("@") != 1:
        raise OrganizationAccessError("a valid email address is required")
    local, domain = normalized.rsplit("@", 1)
    if not local or not domain or "." not in domain:
        raise OrganizationAccessError("a valid email address is required")
    return normalized


def email_matches_exact_domain(
    email: str | None,
    required_domain: str | None,
) -> bool:
    """Match one normalized mailbox to the exact managed organization domain."""

    try:
        normalized_email = normalize_email(email or "")
    except OrganizationAccessError:
        return False
    normalized_domain = (required_domain or "").strip().lower().lstrip("@")
    if (
        not normalized_domain
        or len(normalized_domain) > 253
        or "@" in normalized_domain
        or "." not in normalized_domain
    ):
        return False
    return normalized_email.rsplit("@", 1)[1] == normalized_domain


def normalize_access_code(value: str) -> str:
    return value.strip().upper().replace(" ", "").replace("-", "")


def generate_access_code() -> str:
    return "".join(
        secrets.choice(ACCESS_CODE_ALPHABET) for _ in range(ACCESS_CODE_LENGTH)
    )


def _issued_access_code(value: str | None) -> str:
    code = generate_access_code() if value is None else normalize_access_code(value)
    if len(code) != ACCESS_CODE_LENGTH or any(
        character not in ACCESS_CODE_ALPHABET for character in code
    ):
        raise OrganizationAccessError("generated access code is invalid")
    return code


def _access_code_digest(code: str, salt: bytes) -> str:
    return hashlib.scrypt(
        normalize_access_code(code).encode("ascii"),
        salt=salt,
        n=ACCESS_CODE_SCRYPT_N,
        r=ACCESS_CODE_SCRYPT_R,
        p=ACCESS_CODE_SCRYPT_P,
        dklen=32,
    ).hex()


def organization_code_matches(
    organization: ProviderOrganization, supplied_code: str
) -> bool:
    if not organization.access_code_salt or not organization.access_code_digest:
        return False
    normalized = normalize_access_code(supplied_code)
    if len(normalized) != ACCESS_CODE_LENGTH:
        return False
    try:
        salt = bytes.fromhex(organization.access_code_salt)
        candidate = _access_code_digest(normalized, salt)
    except (UnicodeEncodeError, ValueError):
        return False
    return hmac.compare_digest(candidate, organization.access_code_digest)


def _required(value: str, *, field: str, limit: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise OrganizationAccessError(f"{field} is required")
    if len(normalized) > limit:
        raise OrganizationAccessError(f"{field} exceeds {limit} characters")
    return normalized


def _seven_calendar_years_after(value: datetime) -> datetime:
    try:
        return value.replace(year=value.year + ACCESS_RECORD_RETENTION_YEARS)
    except ValueError:
        # February 29 has no matching day in most seventh years.
        return value.replace(
            year=value.year + ACCESS_RECORD_RETENTION_YEARS,
            month=2,
            day=28,
        )


def approval_is_active(
    approval: OrganizationRosterApproval, *, at: datetime | None = None
) -> bool:
    current = at or datetime.now(timezone.utc)
    return approval.revoked_at is None and (
        approval.expires_at is None or approval.expires_at > current
    )


def lock_roster_enrollment_email(session: Session, email: str) -> None:
    """Serialize roster imports and legacy enrollment for one exact email.

    The database cannot express a cross-table uniqueness constraint between a
    Doctor email and an organization roster row.  A transaction-scoped
    advisory lock closes the race without retaining the email or its hash in a
    separate lock table.
    """

    normalized = normalize_email(email)
    lock_key = int.from_bytes(
        hashlib.sha256(normalized.encode("utf-8")).digest()[:8],
        byteorder="big",
        signed=True,
    )
    session.exec(select(func.pg_advisory_xact_lock(lock_key))).one()


def lock_provider_practice_domain(session: Session, domain: str) -> None:
    """Serialize administrative bootstrap and self-service practice creation.

    The bootstrap path is intentionally limited to domains with no existing
    practice.  A transaction-scoped advisory lock makes that zero-practice
    assertion safe against concurrent web enrollment without persisting the
    domain (or a derived identifier) in a separate coordination table.
    """

    normalized = domain.strip().lower().lstrip("@")
    if (
        not normalized
        or len(normalized) > 253
        or "@" in normalized
        or "." not in normalized
    ):
        raise OrganizationAccessError("a valid email domain is required")
    lock_key = int.from_bytes(
        hashlib.sha256(f"provider-practice:{normalized}".encode()).digest()[:8],
        byteorder="big",
        signed=True,
    )
    session.exec(select(func.pg_advisory_xact_lock(lock_key))).one()


def email_has_managed_roster_assignment(
    email: str,
    *,
    at: datetime | None = None,
    session: Session | None = None,
) -> bool:
    """Return whether an unrevoked roster row reserves this identity.

    Organization suspension, an entitlement lapse, or a temporarily invalid
    BAA must not let a managed identity escape into legacy self-enrollment.
    Those conditions make the assignment unclaimable, but it remains managed
    until an operator explicitly revokes it.
    """

    normalized = normalize_email(email)
    current = at or datetime.now(timezone.utc)
    statement = select(OrganizationRosterApproval).where(
        col(OrganizationRosterApproval.approved_email) == normalized,
        col(OrganizationRosterApproval.revoked_at).is_(None),
        or_(
            col(OrganizationRosterApproval.expires_at).is_(None),
            col(OrganizationRosterApproval.expires_at) > current,
        ),
    )
    if session is not None:
        return session.exec(statement).first() is not None
    with get_session() as lookup_session:
        return lookup_session.exec(statement).first() is not None


def doctor_has_managed_roster_assignment(
    doctor: Doctor,
    *,
    at: datetime | None = None,
    session: Session | None = None,
) -> bool:
    if doctor.deleted_at is not None or not doctor.email:
        return False
    return email_has_managed_roster_assignment(
        doctor.email,
        at=at,
        session=session,
    )


def member_authorization_basis(
    member: PracticeMember, *, at: datetime | None = None
) -> str | None:
    """Return the live authorization basis without manufacturing attestation."""

    if member.access_revoked_at is not None:
        return None
    practice = Practice.get(id=member.practice_id)
    if practice is None:
        return None
    # Once a practice is organization-managed, exact roster authorization is
    # mandatory. A pre-cutover attestation remains historical evidence but can
    # no longer bypass the explicit roster.
    if practice.organization_id is None:
        return "workforce_attestation" if member.attested_at is not None else None
    if (
        member.organization_roster_approval_id is None
        or member.organization_authorized_at is None
    ):
        return None

    approval = OrganizationRosterApproval.get(id=member.organization_roster_approval_id)
    if (
        approval is None
        or not approval_is_active(approval, at=at)
        or approval.claimed_doctor_id != member.doctor_id
        or approval.target_practice_id != member.practice_id
    ):
        return None
    doctor = Doctor.get(id=member.doctor_id)
    if doctor is None or doctor.deleted_at is not None or not doctor.email:
        return None
    try:
        current_email = normalize_email(doctor.email)
    except OrganizationAccessError:
        return None
    if current_email != approval.approved_email:
        return None
    current = at or datetime.now(timezone.utc)
    approval_history = cast(
        list[OrganizationRosterApproval],
        list(
            OrganizationRosterApproval.where(
                OrganizationRosterApproval.approved_email == current_email
            ).all()
        ),
    )
    active_approvals = [
        item for item in approval_history if approval_is_active(item, at=current)
    ]
    if (
        any(item.revoked_at is not None for item in approval_history)
        or len(active_approvals) != 1
        or active_approvals[0].id != approval.id
    ):
        return None
    organization = ProviderOrganization.get(id=approval.organization_id)
    if (
        organization is None
        or organization.status != "active"
        or practice.organization_id != organization.id
        or not email_matches_exact_domain(current_email, organization.email_domain)
        or not practice.negotiated_baa_in_force(at=current)
    ):
        return None
    return "organization_roster"


def member_access_in_force(
    member: PracticeMember, *, at: datetime | None = None
) -> bool:
    return member_authorization_basis(member, at=at) is not None


def find_access_opportunity(
    doctor: Doctor, *, at: datetime | None = None
) -> OrganizationAccessOpportunity | None:
    """Find the one active exact-email roster approval for this clinician."""

    if not doctor.email or doctor.deleted_at is not None:
        return None
    current = at or datetime.now(timezone.utc)
    email = normalize_email(doctor.email)
    approvals = cast(
        list[OrganizationRosterApproval],
        list(
            OrganizationRosterApproval.where(
                OrganizationRosterApproval.approved_email == email,
            ).all()
        ),
    )
    if any(item.revoked_at is not None for item in approvals):
        return None
    active = [item for item in approvals if approval_is_active(item, at=current)]
    if len(active) != 1:
        return None
    approval = active[0]
    organization = ProviderOrganization.get(id=approval.organization_id)
    practice = Practice.get(id=approval.target_practice_id)
    if (
        organization is None
        or organization.status != "active"
        or practice is None
        or practice.organization_id != organization.id
        or not email_matches_exact_domain(email, organization.email_domain)
        or not email_matches_exact_domain(email, practice.email_domain)
        or not practice.negotiated_baa_in_force(at=current)
        or not practice.entitled(at=current)
    ):
        return None
    return OrganizationAccessOpportunity(organization, approval, practice)


def rotate_access_code(
    organization: ProviderOrganization,
    *,
    operator_ref: str,
    reason: str,
    access_code: str | None = None,
    now: datetime | None = None,
) -> tuple[str, tuple[PracticeAccessAudit, ...]]:
    """Rotate one shared organization code and audit every linked practice."""

    operator = _required(operator_ref, field="operator_ref", limit=255)
    explanation = _required(reason, field="reason", limit=512)
    current = now or datetime.now(timezone.utc)
    with get_session() as session:
        practice_ids = tuple(
            session.exec(
                select(col(Practice.id))
                .where(Practice.organization_id == organization.id)
                .order_by(cast(Any, Practice.id))
            ).all()
        )
        if not practice_ids:
            raise OrganizationAccessError(
                "organization has no linked practices to audit"
            )
        locked_practices = tuple(
            session.exec(
                select(Practice).where(Practice.id == practice_id).with_for_update()
            ).one()
            for practice_id in practice_ids
        )
        locked_organization = session.exec(
            select(ProviderOrganization)
            .where(ProviderOrganization.id == organization.id)
            .with_for_update()
        ).one_or_none()
        if locked_organization is None:
            raise OrganizationAccessError("organization no longer exists")
        current_practice_ids = tuple(
            session.exec(
                select(col(Practice.id))
                .where(Practice.organization_id == locked_organization.id)
                .order_by(cast(Any, Practice.id))
            ).all()
        )
        if current_practice_ids != practice_ids:
            raise OrganizationAccessError(
                "organization practice set changed during rotation; retry"
            )
        if any(
            practice.organization_id != locked_organization.id
            for practice in locked_practices
        ):
            raise OrganizationAccessError(
                "organization practice set changed during rotation; retry"
            )
        code = _issued_access_code(access_code)
        salt = secrets.token_bytes(ACCESS_CODE_SALT_BYTES)
        locked_organization.access_code_salt = salt.hex()
        locked_organization.access_code_digest = _access_code_digest(code, salt)
        locked_organization.access_code_version += 1
        locked_organization.access_code_created_at = current
        audits = tuple(
            PracticeAccessAudit(
                practice_id=practice.id,
                action=PracticeAccessAction.organization_code_rotated.value,
                operator_ref=operator,
                reason=explanation,
                effective_at=current,
                document_reference=str(locked_organization.id),
            )
            for practice in locked_practices
        )
        session.add(locked_organization)
        for audit in audits:
            session.add(audit)
        session.commit()
        session.refresh(locked_organization)
        for audit in audits:
            session.refresh(audit)
    organization.refresh()
    return code, audits


def import_roster_approvals(
    practice: Practice,
    entries: list[RosterApprovalInput],
    *,
    operator_ref: str,
    reason: str,
    source_ref: str,
    source_sha256: str,
    now: datetime | None = None,
) -> RosterImportResult:
    """Atomically approve an exact-email roster; exact retries are no-ops.

    Approval rows retain the source that first authorized each address.  A
    single append-only audit row records every distinct source manifest, so a
    later cumulative roster can reaffirm existing addresses without erasing
    their original provenance. Imports are additive: omitting an existing
    address never revokes it; offboarding is a separate explicit operation.
    """

    operator = _required(operator_ref, field="operator_ref", limit=255)
    explanation = _required(reason, field="reason", limit=512)
    source = _required(source_ref, field="source_ref", limit=255)
    digest = source_sha256.strip().lower()
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise OrganizationAccessError("source_sha256 must be a 64-character hex digest")
    if practice.organization_id is None:
        raise OrganizationAccessError("practice is not linked to an organization")
    organization = ProviderOrganization.get(id=practice.organization_id)
    if organization is None or organization.status != "active":
        raise OrganizationAccessError("organization is not active")
    if not practice.negotiated_baa_in_force(at=now):
        raise OrganizationAccessError("an in-force negotiated BAA is required")
    if not practice.billing_exemption_active(at=now):
        raise OrganizationAccessError("an active billing exemption is required")

    normalized: list[tuple[str, datetime | None]] = []
    seen: set[str] = set()
    for entry in entries:
        email = normalize_email(entry.email)
        if email in seen:
            raise OrganizationAccessError(f"duplicate roster email: {email}")
        if not email_matches_exact_domain(email, organization.email_domain):
            raise OrganizationAccessError(
                f"roster email does not match @{organization.email_domain}: {email}"
            )
        if entry.expires_at is not None:
            raise OrganizationAccessError(
                "scheduled roster expiry is not supported; use explicit offboarding"
            )
        seen.add(email)
        normalized.append((email, entry.expires_at))

    current = now or datetime.now(timezone.utc)
    created = 0
    unchanged = 0
    approval_ids: list[str] = []
    with get_session() as session:
        locked_practice = session.exec(
            select(Practice).where(Practice.id == practice.id).with_for_update()
        ).one()
        if locked_practice.organization_id != organization.id:
            raise OrganizationAccessError("practice organization changed during import")
        locked_organization = session.exec(
            select(ProviderOrganization)
            .where(ProviderOrganization.id == organization.id)
            .with_for_update()
        ).one()
        if locked_organization.status != "active":
            raise OrganizationAccessError("organization is not active")
        if not locked_practice.negotiated_baa_in_force(at=current):
            raise OrganizationAccessError("an in-force negotiated BAA is required")
        if not locked_practice.billing_exemption_active(at=current):
            raise OrganizationAccessError("an active billing exemption is required")
        # Acquire all identity locks in a stable order before inspecting any
        # approval or account. This prevents concurrent imports from
        # deadlocking and serializes against legacy create/join enrollment.
        for email, _ in sorted(normalized):
            lock_roster_enrollment_email(session, email)
        for email, expires_at in normalized:
            prior_revoked = session.exec(
                select(OrganizationRosterApproval)
                .where(
                    OrganizationRosterApproval.organization_id
                    == locked_organization.id,
                    OrganizationRosterApproval.target_practice_id == locked_practice.id,
                    OrganizationRosterApproval.approved_email == email,
                    OrganizationRosterApproval.revoked_at != None,  # noqa: E711
                )
                .with_for_update()
            ).first()
            if prior_revoked is not None:
                raise OrganizationAccessError(
                    "a roster approval was previously revoked; an explicit "
                    "reauthorization workflow is required"
                )
            existing = session.exec(
                select(OrganizationRosterApproval)
                .where(
                    OrganizationRosterApproval.organization_id
                    == locked_organization.id,
                    OrganizationRosterApproval.approved_email == email,
                    OrganizationRosterApproval.revoked_at == None,  # noqa: E711
                )
                .with_for_update()
            ).one_or_none()
            rostered_doctor = session.exec(
                select(Doctor)
                .where(func.lower(col(Doctor.email)) == email)
                .with_for_update()
            ).one_or_none()
            if rostered_doctor is not None:
                if rostered_doctor.deleted_at is not None:
                    raise OrganizationAccessError(
                        f"roster account is already offboarded: {email}"
                    )
                rostered_member = session.exec(
                    select(PracticeMember)
                    .where(PracticeMember.doctor_id == rostered_doctor.id)
                    .with_for_update()
                ).one_or_none()
                if (
                    rostered_member is not None
                    and rostered_member.practice_id != locked_practice.id
                ):
                    raise OrganizationAccessError(
                        f"roster account belongs to another practice: {email}"
                    )
                if (
                    rostered_member is not None
                    and rostered_member.access_revoked_at is not None
                ):
                    raise OrganizationAccessError(
                        f"roster account has revoked practice access: {email}"
                    )
            if existing is not None:
                if (
                    existing.target_practice_id != practice.id
                    or existing.expires_at != expires_at
                ):
                    raise OrganizationAccessError(
                        f"active roster approval conflicts for {email}"
                    )
                unchanged += 1
                approval_ids.append(str(existing.id))
                continue
            approval = OrganizationRosterApproval(
                organization_id=organization.id,
                target_practice_id=practice.id,
                approved_email=email,
                approved_at=current,
                approved_by_ref=operator,
                source_ref=source,
                source_sha256=digest,
                expires_at=expires_at,
            )
            session.add(approval)
            session.flush()
            created += 1
            approval_ids.append(str(approval.id))

        prior_source_audit = session.exec(
            select(PracticeAccessAudit).where(
                PracticeAccessAudit.practice_id == practice.id,
                PracticeAccessAudit.action
                == PracticeAccessAction.organization_roster_imported.value,
                PracticeAccessAudit.document_reference == source,
                PracticeAccessAudit.evidence_sha256 == digest,
            )
        ).first()
        if prior_source_audit is None:
            session.add(
                PracticeAccessAudit(
                    practice_id=practice.id,
                    action=PracticeAccessAction.organization_roster_imported.value,
                    operator_ref=operator,
                    reason=explanation,
                    effective_at=current,
                    document_reference=source,
                    evidence_sha256=digest,
                )
            )
        session.commit()
    return RosterImportResult(created, unchanged, tuple(approval_ids))


def claim_roster_access(
    doctor: Doctor,
    *,
    supplied_code: str,
    now: datetime | None = None,
) -> tuple[Practice, PracticeMember]:
    """Claim one exact roster approval and create membership atomically."""

    opportunity = find_access_opportunity(doctor, at=now)
    if opportunity is None:
        raise OrganizationAccessError("organization access is not available")
    if not organization_code_matches(opportunity.organization, supplied_code):
        raise OrganizationAccessError("organization access is not available")
    current = now or datetime.now(timezone.utc)

    with get_session() as session:
        # All organization-access mutations take the practice lock first. This
        # serializes import, claim, rotation, and offboarding and avoids lock
        # cycles between those workflows.
        practice = lock_practice_for_active_seat_mutation(
            session, opportunity.practice.id
        )
        organization = session.exec(
            select(ProviderOrganization)
            .where(ProviderOrganization.id == opportunity.organization.id)
            .with_for_update()
        ).one()
        lock_roster_enrollment_email(
            session,
            opportunity.approval.approved_email,
        )
        approval = session.exec(
            select(OrganizationRosterApproval)
            .where(OrganizationRosterApproval.id == opportunity.approval.id)
            .with_for_update()
        ).one()
        locked_doctor = session.exec(
            select(Doctor).where(Doctor.id == doctor.id).with_for_update()
        ).one()
        approval_history = list(
            session.exec(
                select(OrganizationRosterApproval)
                .where(
                    OrganizationRosterApproval.approved_email == approval.approved_email
                )
                .with_for_update()
            ).all()
        )
        active_approvals = [
            item for item in approval_history if approval_is_active(item, at=current)
        ]
        if (
            locked_doctor.deleted_at is not None
            or normalize_email(locked_doctor.email or "") != approval.approved_email
            or any(item.revoked_at is not None for item in approval_history)
            or len(active_approvals) != 1
            or active_approvals[0].id != approval.id
            or not approval_is_active(approval, at=current)
            or approval.organization_id != organization.id
            or practice.organization_id != organization.id
            or organization.status != "active"
            or not organization_code_matches(organization, supplied_code)
            or not email_matches_exact_domain(
                locked_doctor.email, organization.email_domain
            )
            or not practice.negotiated_baa_in_force(at=current)
            or not practice.entitled(at=current)
        ):
            raise OrganizationAccessError("organization access is not available")

        member = session.exec(
            select(PracticeMember)
            .where(PracticeMember.doctor_id == locked_doctor.id)
            .with_for_update()
        ).one_or_none()
        grant_required = False
        if member is not None:
            if member.access_revoked_at is not None:
                raise OrganizationAccessError("organization access has been revoked")
            if member.practice_id != practice.id:
                raise OrganizationAccessError(
                    "account already belongs to another practice"
                )
            if member.organization_roster_approval_id is None:
                grant_required = True
            elif (
                member.organization_roster_approval_id == approval.id
                and member.organization_authorized_at is not None
            ):
                session.commit()
                session.refresh(practice)
                session.refresh(member)
            else:
                raise OrganizationAccessError(
                    "account has a conflicting organization authorization"
                )
        else:
            member = PracticeMember(
                practice_id=practice.id,
                doctor_id=locked_doctor.id,
                role=PracticeRole.workforce.value,
            )
            grant_required = True

        if grant_required:
            if (
                approval.claimed_doctor_id is not None
                and approval.claimed_doctor_id != locked_doctor.id
            ):
                raise OrganizationAccessError("organization access is not available")

            approval.claimed_doctor_id = locked_doctor.id
            approval.claimed_at = approval.claimed_at or current
            approval.claimed_code_version = organization.access_code_version
            member.organization_roster_approval_id = approval.id
            member.organization_authorized_at = current
            # New organization members deliberately keep attestation fields
            # null. A converted pre-cutover member keeps any historical values,
            # but those values are no longer an authorization basis here.
            locked_doctor.onboarding_completed_at = (
                locked_doctor.onboarding_completed_at or current
            )
            session.add(approval)
            session.add(member)
            session.add(locked_doctor)
            session.flush()
            session.add(
                PracticeAccessAudit(
                    practice_id=practice.id,
                    action=PracticeAccessAction.organization_access_granted.value,
                    operator_ref="self-service-organization-code",
                    reason=(
                        "Exact roster, verified domain, and organization code matched"
                    ),
                    effective_at=current,
                    expires_at=approval.expires_at,
                    document_reference=str(approval.id),
                )
            )
            session.commit()
            session.refresh(practice)
            session.refresh(member)

    if practice.stripe_subscription_id:
        # Persist a seat-count reconciliation obligation; this performs no
        # external Stripe request on the enrollment path.
        from app.services import stripe_billing

        stripe_billing.mark_quantity_sync_required(practice)
    return practice, member


@dataclass(frozen=True)
class RosterRevocationResult:
    approval: OrganizationRosterApproval
    member: PracticeMember | None
    doctor: Doctor | None
    retain_until: datetime
    audit: PracticeAccessAudit


def revoke_roster_access(
    practice: Practice,
    approved_email: str,
    *,
    operator_ref: str,
    reason: str,
    expected_doctor_id: Any | None = None,
    now: datetime | None = None,
) -> RosterRevocationResult:
    """Revoke claimed or unclaimed roster access and retain its evidence."""

    operator = _required(operator_ref, field="operator_ref", limit=255)
    explanation = _required(reason, field="reason", limit=512)
    email = normalize_email(approved_email)
    current = now or datetime.now(timezone.utc)
    retain_until = _seven_calendar_years_after(current)

    with get_session() as session:
        locked_practice = lock_practice_for_active_seat_mutation(session, practice.id)
        if locked_practice.organization_id is None:
            raise OrganizationAccessError(
                "offboarding requires an organization-managed practice"
            )
        locked_organization = session.exec(
            select(ProviderOrganization)
            .where(ProviderOrganization.id == locked_practice.organization_id)
            .with_for_update()
        ).one()

        lock_roster_enrollment_email(session, email)

        approval = session.exec(
            select(OrganizationRosterApproval)
            .where(
                OrganizationRosterApproval.organization_id == locked_organization.id,
                OrganizationRosterApproval.target_practice_id == locked_practice.id,
                OrganizationRosterApproval.approved_email == email,
                OrganizationRosterApproval.revoked_at == None,  # noqa: E711
            )
            .with_for_update()
        ).one_or_none()
        if approval is None:
            approval = session.exec(
                select(OrganizationRosterApproval)
                .where(
                    OrganizationRosterApproval.organization_id
                    == locked_organization.id,
                    OrganizationRosterApproval.target_practice_id == locked_practice.id,
                    OrganizationRosterApproval.approved_email == email,
                )
                .order_by(cast(Any, OrganizationRosterApproval.created_at).desc())
                .with_for_update()
            ).first()
        if approval is None:
            raise OrganizationAccessError(
                "no organization-roster approval matches this practice and email"
            )
        if (
            expected_doctor_id is not None
            and approval.claimed_doctor_id != expected_doctor_id
        ):
            raise OrganizationAccessError(
                "roster approval is not claimed by the expected doctor"
            )

        locked_doctor: Doctor | None = None
        member: PracticeMember | None = None
        if approval.claimed_doctor_id is not None:
            locked_doctor = session.exec(
                select(Doctor)
                .where(Doctor.id == approval.claimed_doctor_id)
                .with_for_update()
            ).one()
            member = session.exec(
                select(PracticeMember)
                .where(
                    PracticeMember.practice_id == locked_practice.id,
                    PracticeMember.doctor_id == locked_doctor.id,
                    PracticeMember.organization_roster_approval_id == approval.id,
                )
                .with_for_update()
            ).one_or_none()
            if member is None:
                raise OrganizationAccessError(
                    "claimed roster approval has no matching practice member"
                )

        if (
            approval.revoked_at is None
            and member is not None
            and member.role == PracticeRole.owner.value
        ):
            other_active_owner = session.exec(
                select(PracticeMember)
                .join(
                    OrganizationRosterApproval,
                    col(OrganizationRosterApproval.id)
                    == col(PracticeMember.organization_roster_approval_id),
                )
                .join(Doctor, col(Doctor.id) == col(PracticeMember.doctor_id))
                .where(
                    PracticeMember.practice_id == locked_practice.id,
                    PracticeMember.id != member.id,
                    PracticeMember.role == PracticeRole.owner.value,
                    PracticeMember.access_revoked_at == None,  # noqa: E711
                    PracticeMember.organization_authorized_at != None,  # noqa: E711
                    OrganizationRosterApproval.organization_id
                    == locked_organization.id,
                    OrganizationRosterApproval.target_practice_id == locked_practice.id,
                    OrganizationRosterApproval.claimed_doctor_id
                    == PracticeMember.doctor_id,
                    OrganizationRosterApproval.revoked_at == None,  # noqa: E711
                    func.lower(func.btrim(col(Doctor.email)))
                    == col(OrganizationRosterApproval.approved_email),
                    func.split_part(func.lower(func.btrim(col(Doctor.email))), "@", 2)
                    == locked_organization.email_domain,
                    or_(
                        col(OrganizationRosterApproval.expires_at).is_(None),
                        col(OrganizationRosterApproval.expires_at) > current,
                    ),
                    Doctor.deleted_at == None,  # noqa: E711
                )
                .with_for_update()
            ).first()
            if other_active_owner is None:
                raise OrganizationAccessError(
                    "cannot offboard the last active practice owner; "
                    "transfer ownership first"
                )

        if approval.revoked_at is not None:
            if approval.retain_until is None:
                raise OrganizationAccessError("revoked approval has no retention hold")
            if member is not None and (
                member.access_revoked_at is None
                or member.access_records_retain_until is None
            ):
                raise OrganizationAccessError(
                    "revoked approval has an active or unretained member"
                )
            existing_audit = cast(
                PracticeAccessAudit | None,
                session.exec(
                    select(PracticeAccessAudit)
                    .where(
                        PracticeAccessAudit.practice_id == locked_practice.id,
                        PracticeAccessAudit.action
                        == PracticeAccessAction.organization_access_revoked.value,
                        PracticeAccessAudit.document_reference == str(approval.id),
                    )
                    .order_by(cast(Any, PracticeAccessAudit.created_at).desc())
                ).first(),
            )
            if existing_audit is None:
                raise OrganizationAccessError(
                    "revoked member is missing audit evidence"
                )
            retain_until = approval.retain_until
            audit = existing_audit
            session.commit()
            session.refresh(approval)
            if member is not None:
                session.refresh(member)
            if locked_doctor is not None:
                session.refresh(locked_doctor)
            session.refresh(audit)
        else:
            approval.revoked_at = current
            approval.revoked_by_ref = operator
            approval.revocation_reason = explanation
            approval.retain_until = retain_until
            session.add(approval)
            if member is not None and locked_doctor is not None:
                member.access_revoked_at = current
                member.access_revoked_by_ref = operator
                member.access_revocation_reason = explanation
                member.access_records_retain_until = retain_until
                locked_doctor.deleted_at = locked_doctor.deleted_at or current
                session.add(member)
                session.add(locked_doctor)
            audit = PracticeAccessAudit(
                practice_id=locked_practice.id,
                action=PracticeAccessAction.organization_access_revoked.value,
                operator_ref=operator,
                reason=explanation,
                effective_at=current,
                expires_at=retain_until,
                document_reference=str(approval.id),
            )
            session.add(audit)
            session.commit()
            session.refresh(approval)
            if member is not None:
                session.refresh(member)
            if locked_doctor is not None:
                session.refresh(locked_doctor)
            session.refresh(audit)

        session.refresh(locked_practice)

    if locked_practice.stripe_subscription_id:
        from app.services import stripe_billing

        stripe_billing.mark_quantity_sync_required(locked_practice)
    return RosterRevocationResult(
        approval=approval,
        member=member,
        doctor=locked_doctor,
        retain_until=retain_until,
        audit=audit,
    )


def offboard_member(
    practice: Practice,
    doctor: Doctor,
    *,
    operator_ref: str,
    reason: str,
    now: datetime | None = None,
) -> tuple[PracticeMember, datetime, PracticeAccessAudit]:
    """Disable claimed roster access while preserving durable records."""

    if not doctor.email:
        raise OrganizationAccessError("doctor has no email address")
    result = revoke_roster_access(
        practice,
        doctor.email,
        operator_ref=operator_ref,
        reason=reason,
        expected_doctor_id=doctor.id,
        now=now,
    )
    if result.member is None:
        raise OrganizationAccessError("roster approval has not been claimed")
    return result.member, result.retain_until, result.audit
