"""Reviewed administrative provider correspondence; no patient or provider I/O.

The CLI supplies a freshly verified Clerk primary email. Existing practice
membership and product access remain prerequisites, never granted by this path.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, NoReturn
from uuid import UUID

from typeid import TypeID
from typeid.errors import TypeIDException

from app.configuration.authority import AuthorityKernelSettings
from app.services.doctor_access import doctor_access_in_force
from app.services.organization_access import normalize_email

from app.models.authority import (
    AuthorityActor,
    AuthorityActorExternalIdentity,
    AuthorityAuditEvent,
    AuthorityTenant,
    AuthorityTenantMembership,
)
from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember
from app.models.provider_record_authority import ProviderPracticeAuthority
from app.models.scribe_capture_binding import ScribeCaptureProviderLink
from sqlalchemy import or_, text
from sqlmodel import Session, col, select


class CompanionProvisioningError(ValueError):
    """Closed operator error without identity, email, credential or clinical data."""


@dataclass(frozen=True)
class CompanionProvisioningResult:
    plan_sha256: str
    state: str
    authority_records_to_create: tuple[str, ...]
    provider_link_ref: str | None = None


def _fail(code: str) -> NoReturn:
    raise CompanionProvisioningError(code)


def _typeid(value, prefix):
    try:
        parsed = TypeID.from_string(str(value))
        if parsed.prefix != prefix or str(parsed) != str(value):
            raise ValueError
        return parsed
    except (TypeIDException, ValueError, TypeError):
        _fail("invalid_companion_reference")


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def provision_companion_provider(
    session: Session,
    *,
    doctor_id: Any,
    practice_id: Any,
    tenant_id: Any,
    scribe_tenant_id: UUID,
    verified_primary_email: str | None,
    verified_clerk_subject: str,
    email_confirmation: str,
    review_ref: str,
    operator_ref: str,
    settings: AuthorityKernelSettings,
    create_missing_authority: bool = False,
    apply: bool = False,
    confirm_plan_sha256: str | None = None,
) -> CompanionProvisioningResult:
    """Plan or atomically apply one explicit mapping; dry-run writes nothing.

    Run within the caller's normal Core session context. The caller owns rollback
    on failure; this function commits only a successful explicit apply.
    """
    if not settings.enabled or not settings.has_valid_clerk_issuer():
        _fail("configured_authority_required")
    if type(apply) is not bool or type(create_missing_authority) is not bool:
        _fail("explicit_provisioning_mode_required")
    doctor_id = _typeid(doctor_id, "doctor")
    practice_id = _typeid(practice_id, "practice")
    tenant_id = _typeid(tenant_id, "authority_tenant")
    if not isinstance(scribe_tenant_id, UUID) or scribe_tenant_id.int == 0:
        _fail("reviewed_scribe_practice_required")
    if any(
        not isinstance(value, str)
        or not re.fullmatch(r"[A-Za-z0-9._~-]{16,128}", value)
        for value in (review_ref, operator_ref)
    ):
        _fail("opaque_review_and_operator_references_required")
    try:
        email = normalize_email(email_confirmation)
        verified = normalize_email(verified_primary_email or "")
    except ValueError:
        _fail("verified_account_confirmation_required")
    if email != verified:
        _fail("verified_account_confirmation_required")

    if session.get_bind().dialect.name != "postgresql":
        _fail("postgresql_provisioning_required")
    session.execute(text("SET LOCAL statement_timeout = '15000ms'"))
    session.execute(text("SET LOCAL lock_timeout = '5000ms'"))
    # Match Core record exchange and roster/offboarding: Practice before Doctor.
    practice = session.exec(
        select(Practice)
        .where(Practice.id == practice_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    ).one_or_none()
    if practice is None:
        _fail("existing_practice_membership_required")
    doctor = session.exec(
        select(Doctor)
        .where(Doctor.id == doctor_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    ).one_or_none()
    if (
        doctor is None
        or doctor.deleted_at is not None
        or doctor.last_active_at is None
        or doctor.clerk_id != verified_clerk_subject
        or not doctor.email
    ):
        _fail("current_signed_in_provider_required")
    try:
        if normalize_email(doctor.email) != verified:
            _fail("current_signed_in_provider_required")
    except ValueError:
        _fail("current_signed_in_provider_required")
    member = session.exec(
        select(PracticeMember)
        .where(PracticeMember.doctor_id == doctor.id)
        .execution_options(populate_existing=True)
        .with_for_update()
    ).one_or_none()
    if member is None or member.practice_id != practice_id:
        _fail("existing_practice_membership_required")
    if not doctor_access_in_force(doctor):
        _fail("existing_processing_access_required")

    # Reuse Core's immutable practice mapping so its verified record exchange
    # cannot classify the companion's provider membership as a foreign import.
    mappings = list(
        session.exec(
            select(ProviderPracticeAuthority)
            .where(
                or_(
                    ProviderPracticeAuthority.practice_id == practice_id,
                    ProviderPracticeAuthority.tenant_id == tenant_id,
                )
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        ).all()
    )
    if len(mappings) > 1 or any(
        (row.practice_id, row.tenant_id) != (practice_id, tenant_id) for row in mappings
    ):
        _fail("existing_core_practice_correspondence_required")
    mapping = mappings[0] if mappings else None

    # Serialize competing first links even when no identity or link row exists.
    # The database unique identity/link indexes remain independent constraints.
    if session.get_bind().dialect.name != "postgresql":
        _fail("postgresql_provisioning_required")
    for scope in sorted(
        ["tenant:" + str(tenant_id), "scribe:" + str(scribe_tenant_id)]
    ):
        lock = int.from_bytes(
            hashlib.sha256(("companion:" + scope).encode()).digest()[:8],
            "big",
            signed=True,
        )
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})

    related = list(
        session.exec(
            select(ScribeCaptureProviderLink)
            .where(
                or_(
                    col(ScribeCaptureProviderLink.doctor_id) == doctor_id,
                    col(ScribeCaptureProviderLink.tenant_id) == tenant_id,
                    col(ScribeCaptureProviderLink.practice_id) == practice_id,
                    col(ScribeCaptureProviderLink.scribe_tenant_id) == scribe_tenant_id,
                )
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        ).all()
    )
    if any(
        (row.tenant_id, row.practice_id, row.scribe_tenant_id)
        != (tenant_id, practice_id, scribe_tenant_id)
        for row in related
    ):
        _fail("practice_correspondence_review_required")
    own = [row for row in related if row.doctor_id == doctor_id]
    if len(own) > 1 or any(row.revoked_at is not None for row in own):
        _fail("revoked_or_ambiguous_provider_link")

    identity = session.exec(
        select(AuthorityActorExternalIdentity)
        .where(
            AuthorityActorExternalIdentity.issuer == settings.clerk_issuer,
            AuthorityActorExternalIdentity.subject == doctor.clerk_id,
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    ).one_or_none()
    actor = None
    memberships = []
    if identity is not None:
        actor = session.exec(
            select(AuthorityActor)
            .where(AuthorityActor.id == identity.actor_id)
            .execution_options(populate_existing=True)
            .with_for_update()
        ).one_or_none()
        if (
            identity.revoked_at is not None
            or identity.verified_at is None
            or actor is None
            or actor.status != "active"
            or actor.actor_kind != "human"
        ):
            _fail("current_verified_authority_required")
        memberships = list(
            session.exec(
                select(AuthorityTenantMembership)
                .where(
                    AuthorityTenantMembership.actor_id == actor.id,
                )
                .execution_options(populate_existing=True)
                .with_for_update()
            ).all()
        )
        if any(
            row.tenant_id != tenant_id
            or row.status != "active"
            or row.membership_kind != "provider"
            for row in memberships
        ):
            _fail("authority_membership_review_required")
    tenant = session.exec(
        select(AuthorityTenant)
        .where(AuthorityTenant.id == tenant_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    ).one_or_none()
    if tenant is not None and tenant.status != "active":
        _fail("active_authority_tenant_required")
    # An older authority tenant may predate companion links. Its current
    # provider memberships must also resolve to this exact existing practice.
    # A guessed tenant identifier must never enroll a different practice.
    if tenant is not None:
        tenant_members = session.exec(
            select(AuthorityTenantMembership)
            .where(
                AuthorityTenantMembership.tenant_id == tenant_id,
                AuthorityTenantMembership.status == "active",
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        ).all()
        for tenant_member in tenant_members:
            if actor is not None and tenant_member.actor_id == actor.id:
                continue
            providers = session.exec(
                select(col(PracticeMember.practice_id))
                .join(
                    Doctor,
                    col(Doctor.id) == PracticeMember.doctor_id,
                )
                .join(
                    AuthorityActorExternalIdentity,
                    col(AuthorityActorExternalIdentity.subject) == Doctor.clerk_id,
                )
                .where(
                    AuthorityActorExternalIdentity.actor_id == tenant_member.actor_id,
                    AuthorityActorExternalIdentity.issuer == settings.clerk_issuer,
                    col(AuthorityActorExternalIdentity.revoked_at).is_(None),
                )
            ).all()
            if len(providers) != 1 or providers[0] != practice_id:
                _fail("existing_tenant_practice_correspondence_required")
    missing = tuple(
        name
        for name, absent in [
            ("tenant", tenant is None),
            ("actor_identity", identity is None),
            ("provider_membership", not memberships),
            ("core_practice_authority", mapping is None),
        ]
        if absent
    )
    if missing and not create_missing_authority:
        _fail("explicit_authority_creation_review_required")
    payload = {
        "version": 2,
        "action": "companion_provider_provision",
        "doctor_ref": str(doctor_id),
        "practice_ref": str(practice_id),
        "practice_member_ref": str(member.id),
        "tenant_ref": str(tenant_id),
        "scribe_tenant_id": str(scribe_tenant_id),
        "issuer": settings.clerk_issuer,
        "subject_sha256": _digest(doctor.clerk_id),
        "verified_email_sha256": _digest(verified),
        "review_ref": review_ref,
        "operator_ref": operator_ref,
        "create_missing_authority": create_missing_authority,
    }
    digest = _digest(payload)
    bound_review = "companion:v2:" + digest
    legacy_reviews = {
        "companion:v1:"
        + _digest({**payload, "version": 1, "create_missing_authority": value})
        for value in (False, True)
    }
    if own:
        row = own[0]
        legacy = row.review_ref in legacy_reviews
        if (
            (set(missing) - {"core_practice_authority"})
            or identity is None
            or actor is None
            or row.actor_id != actor.id
            or row.source_identity_id != identity.id
            or (row.review_ref != bound_review and not legacy)
            or (mapping is None and not legacy)
        ):
            _fail("existing_provider_link_review_differs")
        if mapping is not None:
            return CompanionProvisioningResult(
                digest, "already_provisioned", (), str(row.id)
            )
    if not apply:
        return CompanionProvisioningResult(digest, "ready_for_review", missing)
    if not isinstance(confirm_plan_sha256, str) or not hmac.compare_digest(
        confirm_plan_sha256, digest
    ):
        _fail("current_reviewed_plan_confirmation_required")

    now = datetime.now(UTC)
    if tenant is None:
        tenant = AuthorityTenant.model_validate({"id": tenant_id, "status": "active"})
        session.add(tenant)
    if identity is None:
        actor = AuthorityActor(actor_kind="human", status="active")
        session.add(actor)
        session.flush()
        identity = AuthorityActorExternalIdentity(
            actor_id=actor.id,
            issuer=settings.clerk_issuer,
            subject=doctor.clerk_id,
            verified_at=now,
        )
        session.add(identity)
    if actor is None:
        _fail("current_verified_authority_required")
    if not memberships:
        session.add(
            AuthorityTenantMembership(
                actor_id=actor.id,
                tenant_id=tenant_id,
                membership_kind="provider",
                status="active",
            )
        )
    session.flush()
    if mapping is None:
        mapping = ProviderPracticeAuthority(
            practice_id=practice_id, tenant_id=tenant_id
        )
        session.add(mapping)
        session.flush()
    if own:
        # Upgrade only the missing Core mapping after the new plan is confirmed.
        # Preserve the original reviewed link and its audit provenance.
        link = own[0]
    else:
        link = ScribeCaptureProviderLink(
            actor_id=actor.id,
            tenant_id=tenant_id,
            source_identity_id=identity.id,
            doctor_id=doctor_id,
            practice_id=practice_id,
            scribe_tenant_id=scribe_tenant_id,
            reviewed_at=now,
            review_ref=bound_review,
        )
        session.add(link)
        session.flush()
    session.add(
        AuthorityAuditEvent(
            event_type="companion.provider.practice_authority_bound"
            if own
            else "companion.provider.provisioned",
            outcome="accepted",
            occurred_at=now,
            tenant_id=tenant_id,
            actor_id=actor.id,
            resource_type="provider_practice_authority"
            if own
            else "scribe_provider_link",
            resource_ref=str(mapping.id if own else link.id),
            request_digest=digest,
            platform_refs=[
                {"kind": "approval", "ref": review_ref},
                {"kind": "operator", "ref": operator_ref},
            ],
        )
    )
    link_ref = str(link.id)
    session.commit()
    return CompanionProvisioningResult(digest, "provisioned", (), link_ref)
