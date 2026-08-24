"""Sanitized routing state for provider-organization enrollment.

An email-domain match only keeps a managed account out of legacy practice
creation and personal BAA clickwrap.  It is never an authorization basis.
The only positive claim state requires the existing exact-roster workflow's
full set of checks; the access code is still verified only by the atomic claim
service.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, cast

from app.services.organization_access import (
    OrganizationAccessError,
    approval_is_active,
    email_matches_exact_domain,
    find_access_opportunity,
    member_authorization_basis,
    normalize_email,
)
from app.utils.email_domain import (
    MANAGED_PROVIDER_EMAIL_DOMAINS,
    email_matches_domain,
)

from activemodel.session_manager import get_session
from app.models.doctor import Doctor
from app.models.organization_access import (
    OrganizationRosterApproval,
    ProviderOrganization,
)
from app.models.practice import Practice, PracticeMember
from sqlmodel import col, select

ManagedOrganizationAccessState = Literal["ready", "claim", "pending", "support"]


@dataclass(frozen=True)
class ManagedOrganizationAccessDisposition:
    """Minimal, non-sensitive state suitable for an authenticated browser."""

    state: ManagedOrganizationAccessState
    email_domain: str
    organization_name: str | None = None


def _configured_managed_domains() -> set[str]:
    """Return domains whose server-side records reserve managed enrollment.

    Suspended organizations and incomplete negotiated-BAA practice shells are
    intentionally included.  Losing coverage must close access, not reopen the
    ordinary self-service path.
    """

    domains = set(MANAGED_PROVIDER_EMAIL_DOMAINS)
    with get_session() as session:
        domains.update(
            str(value).strip().lower()
            for value in session.exec(
                select(col(ProviderOrganization.email_domain))
            ).all()
            if value
        )
        domains.update(
            str(value).strip().lower()
            for value in session.exec(
                select(col(Practice.email_domain)).where(
                    Practice.negotiated_baa_required == True  # noqa: E712
                )
            ).all()
            if value
        )
    return domains


def managed_organization_discovery_domain(email: str | None) -> str | None:
    """Discover a managed domain without treating it as access authority."""

    try:
        normalized_email = normalize_email(email or "")
    except OrganizationAccessError:
        return None
    candidates = {
        domain
        for domain in _configured_managed_domains()
        if email_matches_domain(normalized_email, domain)
    }
    if not candidates:
        return None
    # Prefer the narrowest configured namespace if parent and subdomain records
    # ever coexist.  This only controls routing copy, never authorization.
    return max(candidates, key=lambda value: (len(value), value))


def uses_managed_organization_path(
    doctor: Doctor,
    *,
    verified_primary_email: str | None = None,
) -> bool:
    """Whether this identity must stay out of legacy enrollment surfaces.

    Local email is retained as a fail-closed signal, while a current verified
    Clerk email can newly route an account into the managed path. Neither
    domain is sufficient to grant organization access.
    """

    return (
        managed_organization_discovery_domain(doctor.email) is not None
        or managed_organization_discovery_domain(verified_primary_email) is not None
    )


def resolve_managed_organization_access(
    doctor: Doctor,
    *,
    verified_primary_email: str | None,
    at: datetime | None = None,
) -> ManagedOrganizationAccessDisposition | None:
    """Resolve a sanitized managed-enrollment state without mutating data.

    ``pending`` means no exact roster row has been provisioned yet.  Any
    conflicting or previously provisioned state fails closed to ``support``.
    ``claim`` is returned only for exactly one currently valid mapping whose
    organization, negotiated BAA, and entitlement are all in force.  ``ready``
    additionally requires the mapping to be claimed by this Doctor and bound
    to its live membership.
    """

    local_discovery_domain = managed_organization_discovery_domain(doctor.email)
    live_discovery_domain = managed_organization_discovery_domain(
        verified_primary_email
    )
    discovery_domain = live_discovery_domain or local_discovery_domain
    if discovery_domain is None:
        return None

    fallback = ManagedOrganizationAccessDisposition(
        state="support",
        email_domain=discovery_domain,
    )
    if doctor.deleted_at is not None:
        return fallback

    try:
        local_email = normalize_email(doctor.email or "")
        verified_email = normalize_email(verified_primary_email or "")
    except OrganizationAccessError:
        return fallback
    if verified_email != local_email:
        return fallback

    current = at or datetime.now(timezone.utc)
    approvals = cast(
        list[OrganizationRosterApproval],
        list(
            OrganizationRosterApproval.where(
                OrganizationRosterApproval.approved_email == local_email
            ).all()
        ),
    )
    if any(approval.revoked_at is not None for approval in approvals):
        return fallback
    active_approvals = [
        approval for approval in approvals if approval_is_active(approval, at=current)
    ]
    if not active_approvals:
        return ManagedOrganizationAccessDisposition(
            state="support" if approvals else "pending",
            email_domain=discovery_domain,
        )
    if len(active_approvals) != 1:
        return fallback

    opportunity = find_access_opportunity(doctor, at=current)
    if opportunity is None or opportunity.approval.id != active_approvals[0].id:
        return fallback

    approval = opportunity.approval
    organization = opportunity.organization
    practice = opportunity.practice
    if (
        organization.status != "active"
        or practice.organization_id != organization.id
        or approval.organization_id != organization.id
        or approval.target_practice_id != practice.id
        or not email_matches_exact_domain(local_email, organization.email_domain)
        or not email_matches_exact_domain(local_email, practice.email_domain)
        or not practice.negotiated_baa_in_force(at=current)
        or not practice.entitled(at=current)
    ):
        return fallback

    scoped = ManagedOrganizationAccessDisposition(
        state="support",
        email_domain=organization.email_domain,
        organization_name=organization.legal_name,
    )
    member = PracticeMember.get(doctor_id=doctor.id)
    if approval.claimed_doctor_id is None:
        if member is None or (
            member.practice_id == practice.id
            and member.access_revoked_at is None
            and member.organization_roster_approval_id is None
            and member.organization_authorized_at is None
        ):
            return ManagedOrganizationAccessDisposition(
                state="claim",
                email_domain=scoped.email_domain,
                organization_name=scoped.organization_name,
            )
        return scoped

    if (
        approval.claimed_doctor_id == doctor.id
        and member is not None
        and member.practice_id == practice.id
        and member.access_revoked_at is None
        and member.organization_roster_approval_id == approval.id
        and member.organization_authorized_at is not None
        and member_authorization_basis(member, at=current) == "organization_roster"
    ):
        return ManagedOrganizationAccessDisposition(
            state="ready",
            email_domain=scoped.email_domain,
            organization_name=scoped.organization_name,
        )
    return scoped
