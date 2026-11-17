"""Automatic correspondence after authorization, never inside a read path.

The caller supplies a verified principal and its normal Core session context.
Successful activation commits once; the caller owns rollback on any exception.
No email matching, account adoption, membership grant or billing change occurs.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from typeid import TypeID

from app.configuration.scribe_activation import ScribeActivationSettings
from app.services.companion_provider_provisioning import (
    CompanionProvisioningError,
    _digest,
    _provider_correspondence,
    _write_provider_correspondence,
)
from app.services.default_note_template import provision_default_note_template
from app.services.doctor_access import resolve_doctor_access
from app.services.organization_access import member_authorization_basis
from app.services.scribe_cancellation import digest
from app.services.scribe_cancellation_access import cancellation_denial

from app.models.authority import (
    AuthorityActor,
    AuthorityActorExternalIdentity,
    AuthorityAuditEvent,
    AuthorityTenantMembership,
)
from app.models.doctor import Doctor
from app.models.pilot_access_grandfather_grant import PilotAccessGrandfatherGrant
from app.models.practice import Practice, PracticeMember
from app.models.practice_scribe_tenant import PracticeScribeTenant
from app.models.provider_record_authority import ProviderPracticeAuthority
from app.models.scribe_cancellation import scribe_cancellation_operation
from app.models.scribe_capture_binding import ScribeCaptureProviderLink
from app.models.view_as import ViewAsSession
from sqlalchemy import text
from sqlmodel import Session, col, select

ActivationTrigger = Literal["onboarding", "entitlement", "membership", "native-client"]
_TRIGGERS = {"onboarding", "entitlement", "membership", "native-client"}


class ActivationDenied(ValueError):
    """Closed denial: no identity, email or clinical detail in the exception."""


class ActivationSetupRequired(ValueError):
    """Practice setup is incomplete; retrying activation must not create rows."""


@dataclass(frozen=True)
class ActivationResult:
    state: Literal["active", "already_active"]
    provider_link_ref: str


def _deny_tombstones(session: Session, doctor: Doctor, issuer: str) -> None:
    if doctor.deleted_at is not None:
        raise ActivationDenied("scribe_activation_denied")
    support_session = session.exec(
        select(ViewAsSession).where(
            ViewAsSession.actor_doctor_id == doctor.id,
            col(ViewAsSession.ended_at).is_(None),
        )
    ).first()
    if support_session is not None:
        raise ActivationDenied("scribe_activation_denied")
    revoked_grant = session.exec(
        select(col(PilotAccessGrandfatherGrant.id)).where(
            PilotAccessGrandfatherGrant.clerk_id == doctor.clerk_id,
            col(PilotAccessGrandfatherGrant.revoked_at).is_not(None),
        )
    ).first()
    links = session.exec(
        select(ScribeCaptureProviderLink)
        .outerjoin(
            AuthorityActorExternalIdentity,
            col(AuthorityActorExternalIdentity.id)
            == col(ScribeCaptureProviderLink.source_identity_id),
        )
        .where(
            (ScribeCaptureProviderLink.doctor_id == doctor.id)
            | (
                (AuthorityActorExternalIdentity.issuer == issuer)
                & (AuthorityActorExternalIdentity.subject == doctor.clerk_id)
            )
        )
        .execution_options(populate_existing=True)
    ).all()
    identities = session.exec(
        select(AuthorityActorExternalIdentity)
        .where(
            AuthorityActorExternalIdentity.issuer == issuer,
            AuthorityActorExternalIdentity.subject == doctor.clerk_id,
        )
        .execution_options(populate_existing=True)
    ).all()
    if (
        revoked_grant is not None
        or len(links) > 1
        or any(link.revoked_at is not None for link in links)
        or any(link.doctor_id != doctor.id for link in links)
        or len(identities) > 1
        or cancellation_denial(
            session.connection(),
            doctor_ref=str(doctor.id),
            purpose="process",
            now=datetime.now(UTC),
        )
        is not None
    ):
        raise ActivationDenied("scribe_activation_denied")
    for identity in identities:
        actor = session.get(AuthorityActor, identity.actor_id, populate_existing=True)
        memberships = session.exec(
            select(AuthorityTenantMembership)
            .where(AuthorityTenantMembership.actor_id == identity.actor_id)
            .execution_options(populate_existing=True)
        ).all()
        if (
            identity.revoked_at is not None
            or identity.verified_at is None
            or actor is None
            or actor.status != "active"
            or actor.actor_kind != "human"
            or any(row.status != "active" for row in memberships)
        ):
            raise ActivationDenied("scribe_activation_denied")


def activation_provider(
    session: Session,
    *,
    issuer: str,
    subject: str,
    settings: ScribeActivationSettings,
    lock: bool = False,
) -> tuple[Doctor, Practice]:
    """Read-only readiness. Activation repeats all checks under ordered locks."""
    if (
        not settings.authority.has_valid_clerk_issuer()
        or issuer != settings.authority.clerk_issuer
        or not isinstance(subject, str)
        or not subject
        or subject != subject.strip()
        or len(subject) > 255
    ):
        raise ActivationDenied("scribe_activation_denied")
    recorded = session.execute(
        select(scribe_cancellation_operation.c.id).where(
            scribe_cancellation_operation.c.caller_sha256 == digest([issuer, subject])
        )
    ).first()
    if recorded is not None:
        raise ActivationDenied("scribe_activation_denied")
    doctors = session.exec(select(Doctor).where(Doctor.clerk_id == subject)).all()
    if not doctors:
        revoked = session.exec(
            select(col(PilotAccessGrandfatherGrant.id)).where(
                PilotAccessGrandfatherGrant.clerk_id == subject,
                col(PilotAccessGrandfatherGrant.revoked_at).is_not(None),
            )
        ).first()
        identities = session.exec(
            select(AuthorityActorExternalIdentity).where(
                AuthorityActorExternalIdentity.issuer == issuer,
                AuthorityActorExternalIdentity.subject == subject,
            )
        ).all()
        historical_link = session.exec(
            select(ScribeCaptureProviderLink)
            .join(
                AuthorityActorExternalIdentity,
                col(AuthorityActorExternalIdentity.id)
                == col(ScribeCaptureProviderLink.source_identity_id),
            )
            .where(
                AuthorityActorExternalIdentity.issuer == issuer,
                AuthorityActorExternalIdentity.subject == subject,
            )
        ).first()
        if historical_link is not None:
            raise ActivationDenied("scribe_activation_denied")
        if (
            revoked is not None
            or len(identities) > 1
            or any(
                identity.revoked_at is not None or identity.verified_at is None
                for identity in identities
            )
        ):
            raise ActivationDenied("scribe_activation_denied")
        raise ActivationSetupRequired("practice")
    if len(doctors) != 1:
        raise ActivationDenied("scribe_activation_denied")
    doctor = doctors[0]
    _deny_tombstones(session, doctor, issuer)
    members = session.exec(
        select(PracticeMember).where(PracticeMember.doctor_id == doctor.id)
    ).all()
    if len(members) > 1:
        raise ActivationDenied("scribe_activation_denied")
    if not members:
        raise ActivationSetupRequired("practice")
    member = members[0]
    practice_query = select(Practice).where(Practice.id == member.practice_id)
    if lock:
        practice_query = practice_query.execution_options(
            populate_existing=True
        ).with_for_update()
    practice = session.exec(practice_query).one_or_none()
    if practice is None:
        raise ActivationSetupRequired("practice")
    if lock:
        # No successful mutation may rely on the pre-lock Doctor/member snapshots.
        doctor = session.exec(
            select(Doctor)
            .where(Doctor.id == doctor.id)
            .execution_options(populate_existing=True)
            .with_for_update()
        ).one()
        members = session.exec(
            select(PracticeMember)
            .where(PracticeMember.doctor_id == doctor.id)
            .execution_options(populate_existing=True)
            .with_for_update()
        ).all()
        current_doctors = session.exec(
            select(col(Doctor.id)).where(Doctor.clerk_id == subject)
        ).all()
        if len(current_doctors) != 1 or doctor.clerk_id != subject or len(members) != 1:
            raise ActivationDenied("scribe_activation_denied")
        member = members[0]
        if member.practice_id != practice.id:
            raise ActivationDenied("scribe_activation_denied")
        _deny_tombstones(session, doctor, issuer)
    if member.access_revoked_at is not None or member.seat_state != "active":
        raise ActivationDenied("scribe_activation_denied")
    decision = resolve_doctor_access(doctor)
    if (
        doctor.last_active_at is None
        or not doctor.email
        or doctor.onboarding_completed_at is None
        or member_authorization_basis(member, practice=practice)
        not in ("workforce_attestation", "organization_roster")
        or not practice.baa_in_force()
        or not decision.allowed
        or not decision.entitlement_in_force
    ):
        raise ActivationSetupRequired("practice")
    if not settings.authority.enabled or not settings.allows_practice(str(practice.id)):
        # Disabled capability/cohort is a denial, not a setup instruction.
        raise ActivationDenied("scribe_activation_denied")
    return doctor, practice


def activate_scribe(
    session: Session,
    *,
    issuer: str,
    subject: str,
    trigger: ActivationTrigger,
    settings: ScribeActivationSettings,
    request_key: UUID | None = None,
    before_commit: Callable[[], None] | None = None,
) -> ActivationResult:
    """Create only the missing exact correspondence; no operator plan is needed."""
    if trigger not in _TRIGGERS or session.get_bind().dialect.name != "postgresql":
        raise ActivationDenied("scribe_activation_denied")
    session.execute(text("SET LOCAL statement_timeout = '15000ms'"))
    session.execute(text("SET LOCAL lock_timeout = '5000ms'"))
    doctor, practice = activation_provider(
        session, issuer=issuer, subject=subject, settings=settings, lock=True
    )
    provision_default_note_template(session, doctor.id)
    stable = session.exec(
        select(PracticeScribeTenant)
        .where(PracticeScribeTenant.practice_id == practice.id)
        .execution_options(populate_existing=True)
        .with_for_update()
    ).one_or_none()
    practice_links = session.exec(
        select(ScribeCaptureProviderLink).where(
            ScribeCaptureProviderLink.practice_id == practice.id,
        )
    ).all()
    existing_uuids = {link.scribe_tenant_id for link in practice_links}
    if len(existing_uuids) > 1 or (
        stable and existing_uuids - {stable.scribe_tenant_id}
    ):
        raise ActivationDenied("scribe_activation_denied")
    scribe_uuid = (
        stable.scribe_tenant_id if stable else next(iter(existing_uuids), uuid4())
    )
    if not scribe_uuid.int:
        raise ActivationDenied("scribe_activation_denied")
    mappings = session.exec(
        select(ProviderPracticeAuthority).where(
            (ProviderPracticeAuthority.doctor_id == doctor.id)
            | (ProviderPracticeAuthority.practice_id == practice.id)
        )
    ).all()
    personal = [row for row in mappings if row.doctor_id == doctor.id]
    mapping = (
        personal[0]
        if len(personal) == 1
        else (mappings[0] if len(mappings) == 1 else None)
    )
    if len(personal) > 1 or (not personal and len(mappings) > 1):
        raise ActivationDenied("scribe_activation_denied")
    tenant_id: Any = mapping.tenant_id if mapping else TypeID(prefix="authority_tenant")
    try:
        correspondence = _provider_correspondence(
            session,
            doctor=doctor,
            practice_id=practice.id,
            tenant_id=tenant_id,
            scribe_tenant_id=scribe_uuid,
            settings=settings.authority,
            allow_new_practice_namespace=mapping is None,
        )
    except CompanionProvisioningError:
        raise ActivationDenied("scribe_activation_denied") from None
    if correspondence.own and (
        correspondence.missing
        or correspondence.identity is None
        or correspondence.actor is None
        or correspondence.own[0].actor_id != correspondence.actor.id
        or correspondence.own[0].source_identity_id != correspondence.identity.id
    ):
        raise ActivationDenied("scribe_activation_denied")
    refs = [
        {"kind": "trigger", "ref": trigger},
        {"kind": "doctor", "ref": str(doctor.id)},
        {"kind": "practice", "ref": str(practice.id)},
    ]
    if request_key is not None:
        refs.append({"kind": "request", "ref": _digest(str(request_key))})
    digest = _digest({"version": 1, "action": "scribe.activate", "refs": refs})
    if stable is None:
        stable = PracticeScribeTenant(
            practice_id=practice.id, scribe_tenant_id=scribe_uuid
        )
        session.add(stable)
    if correspondence.own:
        link = correspondence.own[0]
        if stable in session.new:
            session.add(
                AuthorityAuditEvent(
                    event_type="scribe.practice.tenant_bound",
                    outcome="accepted",
                    occurred_at=datetime.now(UTC),
                    tenant_id=tenant_id,
                    actor_id=link.actor_id,
                    resource_type="scribe_provider_link",
                    resource_ref=str(link.id),
                    request_digest=digest,
                    platform_refs=refs,
                )
            )
        result = ActivationResult("already_active", str(link.id))
    else:
        link = _write_provider_correspondence(
            session,
            correspondence=correspondence,
            doctor=doctor,
            practice_id=practice.id,
            tenant_id=tenant_id,
            scribe_tenant_id=scribe_uuid,
            settings=settings.authority,
            link_review_ref="scribe:auto:v1:" + digest,
            digest=digest,
            event_type="scribe.provider.activated",
            platform_refs=refs,
        )
        result = ActivationResult("active", str(link.id))
    if before_commit is not None:
        before_commit()
    session.commit()
    return result
