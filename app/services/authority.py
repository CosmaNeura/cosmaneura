from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import app.configuration.session_prep as session_prep_configuration
import app.services.session_prep_scope as session_prep_scope
from app.configuration.authority import (
    AUTHORITY_KERNEL_SETTINGS,
    AuthorityKernelSettings,
)
from app.services.provider_database_deadline import (
    install_provider_database_limits,
    provider_database_deadline,
)
from app.services.provider_record_authority import (
    ProviderRecordScopeDenied,
    ensure_provider_record_scope,
    validate_provider_record_scope,
)

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.authority import (
    AuthorityActor,
    AuthorityActorExternalIdentity,
    AuthorityAuditEvent,
    AuthorityPerson,
    AuthorityPersonBinding,
    AuthorityTenant,
    AuthorityTenantMembership,
    InteractionLineage,
    MountContextSession,
    MountContextToken,
)
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

PUBLIC_FREE_CAPABILITIES = (
    "education:answer",
    "intake:invite",
    "referrals:search",
    "values:answer",
)
PUBLIC_PROSPECT_CAPABILITIES = (
    "booking:route",
    "channel:verify",
    "consent:capture",
    "contact:collect",
    "intake:complete",
)
PUBLIC_PATIENT_CAPABILITIES = (
    "consent:check",
    "consent:manage",
    "disclosure:file",
    "offers:confirm",
    "outreach:receive",
    "person:lookup",
)
PROVIDER_CAPABILITIES = (
    "education:answer",
    "ethics:consult",
    "plan-workbench:run",
    "referrals:search",
    "visit-prep:run",
)


class AuthorityKernelError(Exception):
    pass


class AuthorityKernelDisabledError(AuthorityKernelError):
    pass


class AuthorityRequestDeniedError(AuthorityKernelError):
    pass


class ContextTokenInvalidError(AuthorityKernelError):
    pass


class ContextTokenExpiredError(AuthorityKernelError):
    pass


class ClientTurnConflictError(AuthorityKernelError):
    pass


class InteractionInvalidError(AuthorityKernelError):
    pass


@dataclass(frozen=True)
class ContextTokenIssueResult:
    context_token: str
    issued_at: datetime
    refresh_after: datetime
    expires_at: datetime


@dataclass(frozen=True)
class ResolvedMountContext:
    surface: str
    auth_tier: str
    capabilities: tuple[str, ...]
    tenant_id: str | None
    actor_id: str | None
    session_id: TypeIDType
    person_id: TypeIDType | None
    session_prep_context_ref: str | None = field(default=None, repr=False)

    def public_body(self) -> dict[str, object]:
        return {
            "surface": self.surface,
            "auth_tier": self.auth_tier,
            "capabilities": list(self.capabilities),
            "tenant_id": self.tenant_id,
            "actor_id": self.actor_id,
        }


@dataclass(frozen=True)
class MountResolutionResult:
    context: ResolvedMountContext
    interaction_ref: str
    reused: bool


@dataclass(frozen=True)
class AuthorizedInteraction:
    context: ResolvedMountContext
    mount_session: MountContextSession
    lineage: InteractionLineage


def _ensure_enabled(settings: AuthorityKernelSettings) -> None:
    if not settings.enabled or not settings.is_complete():
        raise AuthorityKernelDisabledError


def _token_hash(token: str) -> str:
    if not 16 <= len(token) <= 8192:
        raise ContextTokenInvalidError
    return hashlib.sha256(token.encode()).hexdigest()


def _new_opaque_value(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def _actor_for_clerk_identity(
    session: Session,
    issuer: str,
    subject: str,
    *,
    lock_for_update: bool = False,
) -> tuple[AuthorityActor, AuthorityActorExternalIdentity]:
    identity_statement = select(AuthorityActorExternalIdentity).where(
        AuthorityActorExternalIdentity.issuer == issuer,
        AuthorityActorExternalIdentity.subject == subject,
        cast(Any, AuthorityActorExternalIdentity.revoked_at).is_(None),
    )
    if lock_for_update:
        identity_statement = identity_statement.with_for_update()
    identity = session.exec(identity_statement).first()
    if identity is None:
        raise AuthorityRequestDeniedError

    actor_statement = select(AuthorityActor).where(
        AuthorityActor.id == identity.actor_id
    )
    if lock_for_update:
        actor_statement = actor_statement.with_for_update()
    actor = session.exec(actor_statement).one_or_none()
    if actor is None or actor.status != "active" or actor.actor_kind != "human":
        raise AuthorityRequestDeniedError
    return actor, identity


def _active_tenant(session: Session, tenant_id: TypeIDType) -> AuthorityTenant:
    tenant = session.get(AuthorityTenant, tenant_id)
    if tenant is None or tenant.status != "active":
        raise AuthorityRequestDeniedError
    return tenant


def _active_provider_membership(
    session: Session,
    *,
    actor_id: TypeIDType,
    tenant_id: TypeIDType,
) -> AuthorityTenantMembership | None:
    return session.exec(
        select(AuthorityTenantMembership).where(
            AuthorityTenantMembership.actor_id == actor_id,
            AuthorityTenantMembership.tenant_id == tenant_id,
            AuthorityTenantMembership.membership_kind == "provider",
            AuthorityTenantMembership.status == "active",
        )
    ).first()


def _person_and_binding_for_ref(
    session: Session,
    record_ref: str,
) -> tuple[AuthorityPerson, AuthorityPersonBinding]:
    binding = session.exec(
        select(AuthorityPersonBinding).where(
            AuthorityPersonBinding.binding_ref == record_ref,
            AuthorityPersonBinding.status == "active",
            cast(Any, AuthorityPersonBinding.revoked_at).is_(None),
        )
    ).first()
    if binding is None or binding.verified_at is None:
        raise AuthorityRequestDeniedError

    person = session.get(AuthorityPerson, binding.person_id)
    if person is None or person.status != "active":
        raise AuthorityRequestDeniedError
    if binding.tenant_id is not None:
        _active_tenant(session, binding.tenant_id)
    return person, binding


def _single_actor_person_binding(
    session: Session,
    actor_id: TypeIDType,
) -> AuthorityPersonBinding | None:
    bindings = list(
        session.exec(
            select(AuthorityPersonBinding).where(
                AuthorityPersonBinding.actor_id == actor_id,
                AuthorityPersonBinding.status == "active",
                cast(Any, AuthorityPersonBinding.revoked_at).is_(None),
            )
        ).all()
    )
    verified = [
        binding
        for binding in bindings
        if binding.verified_at is not None and binding.binding_kind != "provider_record"
    ]
    return verified[0] if len(verified) == 1 else None


def _exchange_binding(
    session: Session,
    *,
    actor: AuthorityActor,
    requested_surface: str | None,
    record_ref: str | None,
) -> tuple[TypeIDType | None, TypeIDType | None]:
    if record_ref is None:
        if requested_surface == "provider_assistant":
            raise AuthorityRequestDeniedError
        binding = _single_actor_person_binding(session, actor.id)
        if binding is None:
            raise AuthorityRequestDeniedError
        return binding.tenant_id, binding.id

    _, binding = _person_and_binding_for_ref(session, record_ref)
    actor_owns_binding = binding.actor_id == actor.id
    provider_membership = None
    if binding.tenant_id is not None:
        provider_membership = _active_provider_membership(
            session,
            actor_id=actor.id,
            tenant_id=binding.tenant_id,
        )

    if requested_surface == "provider_assistant":
        if provider_membership is None or binding.binding_kind != "provider_record":
            raise AuthorityRequestDeniedError
    elif requested_surface == "public":
        if not actor_owns_binding or binding.binding_kind == "provider_record":
            raise AuthorityRequestDeniedError
    elif binding.binding_kind == "provider_record":
        if provider_membership is None:
            raise AuthorityRequestDeniedError
    elif not actor_owns_binding:
        raise AuthorityRequestDeniedError

    return binding.tenant_id, binding.id


def _public_auth_tier(person: AuthorityPerson) -> str:
    if person.lifecycle_state == "patient":
        return "identified"
    return person.lifecycle_state


def _exchange_authority_ceiling(
    session: Session,
    *,
    actor: AuthorityActor,
    tenant_id: TypeIDType | None,
    person_binding_id: TypeIDType | None,
) -> tuple[str, str]:
    if tenant_id is not None:
        _active_tenant(session, tenant_id)
    if person_binding_id is None:
        raise AuthorityRequestDeniedError

    binding = session.get(AuthorityPersonBinding, person_binding_id)
    if (
        binding is None
        or binding.status != "active"
        or binding.revoked_at is not None
        or binding.verified_at is None
        or binding.tenant_id != tenant_id
    ):
        raise AuthorityRequestDeniedError
    person = session.get(AuthorityPerson, binding.person_id)
    if person is None or person.status != "active":
        raise AuthorityRequestDeniedError

    if binding.binding_kind == "provider_record":
        if tenant_id is None:
            raise AuthorityRequestDeniedError
        if (
            _active_provider_membership(
                session,
                actor_id=actor.id,
                tenant_id=tenant_id,
            )
            is None
        ):
            raise AuthorityRequestDeniedError
        return "provider_assistant", "provider"

    if binding.actor_id != actor.id:
        raise AuthorityRequestDeniedError
    return "public", _public_auth_tier(person)


def _audit_event(
    *,
    event_type: str,
    resource_type: str,
    resource_ref: str,
    actor_id: TypeIDType | None = None,
    tenant_id: TypeIDType | None = None,
    person_id: TypeIDType | None = None,
    interaction_id: TypeIDType | None = None,
    request_digest: str | None = None,
) -> AuthorityAuditEvent:
    return AuthorityAuditEvent(
        event_type=event_type,
        outcome="accepted",
        occurred_at=datetime.now(UTC),
        actor_id=actor_id,
        tenant_id=tenant_id,
        person_id=person_id,
        interaction_id=interaction_id,
        resource_type=resource_type,
        resource_ref=resource_ref,
        request_digest=request_digest,
    )


def _issue_token(
    session: Session,
    *,
    mount_session: MountContextSession,
    settings: AuthorityKernelSettings,
) -> ContextTokenIssueResult:
    plaintext = _new_opaque_value("mctx")
    issued_at = datetime.now(UTC)
    refresh_after = issued_at + timedelta(seconds=settings.token_refresh_after_seconds)
    expires_at = issued_at + timedelta(seconds=settings.token_ttl_seconds)
    absolute_expires_at = mount_session.absolute_expires_at
    if absolute_expires_at.tzinfo is None:
        absolute_expires_at = absolute_expires_at.replace(tzinfo=UTC)
    expires_at = min(expires_at, absolute_expires_at)
    if refresh_after >= expires_at:
        raise ContextTokenExpiredError
    token = MountContextToken(
        session_id=mount_session.id,
        token_hash=_token_hash(plaintext),
        issued_at=issued_at,
        refresh_after=refresh_after,
        expires_at=expires_at,
    )
    session.add(token)
    session.flush()
    session.add(
        _audit_event(
            event_type="mount_context_token.issued",
            resource_type="mount_context_token",
            resource_ref=str(token.id),
            actor_id=mount_session.actor_id,
            tenant_id=mount_session.tenant_id,
        )
    )
    return ContextTokenIssueResult(
        context_token=plaintext,
        issued_at=issued_at,
        refresh_after=token.refresh_after,
        expires_at=token.expires_at,
    )


def exchange_context_token(
    *,
    clerk_issuer: str,
    clerk_subject: str,
    requested_surface: str | None,
    record_ref: str | None,
    session: Session | None = None,
    settings: AuthorityKernelSettings = AUTHORITY_KERNEL_SETTINGS,
) -> ContextTokenIssueResult:
    _ensure_enabled(settings)
    if session is None:
        with get_session() as managed_session:
            return exchange_context_token(
                clerk_issuer=clerk_issuer,
                clerk_subject=clerk_subject,
                requested_surface=requested_surface,
                record_ref=record_ref,
                session=cast(Session, managed_session),
                settings=settings,
            )
    db = session
    if clerk_issuer != settings.clerk_issuer:
        raise AuthorityRequestDeniedError
    provider_scope = None
    if (
        requested_surface == "provider_assistant"
        and record_ref is not None
        and session_prep_configuration.SESSION_PREP_PROVIDER_SETTINGS.allows(
            clerk_subject
        )
    ):
        try:
            provider_scope = ensure_provider_record_scope(
                session=db,
                clerk_issuer=clerk_issuer,
                clerk_subject=clerk_subject,
                record_ref=record_ref,
            )
        except ProviderRecordScopeDenied:
            raise AuthorityRequestDeniedError from None
    actor, source_identity = _actor_for_clerk_identity(
        db,
        clerk_issuer,
        clerk_subject,
        lock_for_update=True,
    )
    tenant_id, person_binding_id = _exchange_binding(
        db,
        actor=actor,
        requested_surface=requested_surface,
        record_ref=record_ref,
    )
    if person_binding_id is not None:
        binding = db.get(AuthorityPersonBinding, person_binding_id)
        if binding is None:
            raise AuthorityRequestDeniedError
        try:
            provider_scope = validate_provider_record_scope(
                session=db,
                binding=binding,
                actor_id=actor.id,
                clerk_subject=source_identity.subject,
            )
        except ProviderRecordScopeDenied:
            raise AuthorityRequestDeniedError from None
    if provider_scope is not None:
        from app.services.session_prep_runtime import schedule_scope_build

        schedule_scope_build(
            db, provider_scope, observed_at=datetime.now(UTC), settings=settings
        )
    surface_ceiling, auth_tier_ceiling = _exchange_authority_ceiling(
        db,
        actor=actor,
        tenant_id=tenant_id,
        person_binding_id=person_binding_id,
    )
    mount_session = MountContextSession(
        actor_id=actor.id,
        tenant_id=tenant_id,
        person_binding_id=person_binding_id,
        source_identity_id=source_identity.id,
        session_kind="authenticated",
        surface_ceiling=surface_ceiling,
        auth_tier_ceiling=auth_tier_ceiling,
        status="active",
        absolute_expires_at=datetime.now(UTC)
        + timedelta(seconds=settings.session_absolute_ttl_seconds),
    )
    db.add(mount_session)
    db.flush()
    result = _issue_token(db, mount_session=mount_session, settings=settings)
    db.commit()
    return result


def provider_record_actor_ref(
    *,
    clerk_issuer: str,
    clerk_subject: str,
    record_ref: str,
    session: Session | None = None,
    settings: AuthorityKernelSettings = AUTHORITY_KERNEL_SETTINGS,
) -> str:
    """Resolve the authority actor used by platform-mode synthetic EHR grants.

    This is a migration bridge, not a client identity assertion. The platform
    derives the actor from the verified Clerk subject and rechecks the exact
    provider membership and provider-record binding before returning its opaque
    authority identifier.
    """

    _ensure_enabled(settings)
    if session is None:
        with get_session() as managed_session:
            return provider_record_actor_ref(
                clerk_issuer=clerk_issuer,
                clerk_subject=clerk_subject,
                record_ref=record_ref,
                session=cast(Session, managed_session),
                settings=settings,
            )
    if clerk_issuer != settings.clerk_issuer:
        raise AuthorityRequestDeniedError
    actor, _identity = _actor_for_clerk_identity(
        session,
        clerk_issuer,
        clerk_subject,
    )
    tenant_id, person_binding_id = _exchange_binding(
        session,
        actor=actor,
        requested_surface="provider_assistant",
        record_ref=record_ref,
    )
    surface, tier = _exchange_authority_ceiling(
        session,
        actor=actor,
        tenant_id=tenant_id,
        person_binding_id=person_binding_id,
    )
    if surface != "provider_assistant" or tier != "provider":
        raise AuthorityRequestDeniedError
    return str(actor.id)


def _active_token(
    session: Session,
    plaintext: str,
    *,
    lock_for_update: bool = False,
    _before_view_as: Callable[[MountContextSession], None] | None = None,
) -> tuple[MountContextToken, MountContextSession]:
    # Resolve without a token lock first. Actor-bound view-as state must always
    # be locked before the token row so ``begin_view_as`` and every Agent API
    # path share one lock order.
    statement = select(MountContextToken).where(
        MountContextToken.token_hash == _token_hash(plaintext)
    )
    token = session.exec(statement).first()
    if token is None or token.revoked_at is not None:
        raise ContextTokenInvalidError

    now = datetime.now(UTC)
    if token.expires_at.tzinfo is None:
        now = now.replace(tzinfo=None)
    if token.expires_at <= now:
        raise ContextTokenExpiredError

    mount_session = session.get(MountContextSession, token.session_id)
    if (
        mount_session is None
        or mount_session.status != "active"
        or mount_session.revoked_at is not None
    ):
        raise ContextTokenInvalidError
    absolute_expires_at = mount_session.absolute_expires_at
    absolute_now = datetime.now(UTC)
    if absolute_expires_at.tzinfo is None:
        absolute_now = absolute_now.replace(tzinfo=None)
    if absolute_expires_at <= absolute_now:
        raise ContextTokenExpiredError

    # Capture adds the shared Practice -> Doctor order after bearer/session
    # validation and before the mandatory Doctor -> view-as -> token locks.
    if _before_view_as is not None:
        _before_view_as(mount_session)

    # Local import avoids coupling the authority model layer to the Platform's
    # support UI at module import time. The helper locks Doctor -> ViewAsSession
    # and retains the actor lock through this request transaction, preventing a
    # pre-issued token from racing a new support session.
    from app.services.view_as import mount_context_session_blocked_by_view_as

    if mount_context_session_blocked_by_view_as(
        session,
        mount_session=mount_session,
    ):
        raise AuthorityRequestDeniedError

    if lock_for_update:
        token = session.exec(
            select(MountContextToken)
            .where(MountContextToken.token_hash == _token_hash(plaintext))
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        if token is None or token.revoked_at is not None:
            raise ContextTokenInvalidError
        locked_now = datetime.now(UTC)
        if token.expires_at.tzinfo is None:
            locked_now = locked_now.replace(tzinfo=None)
        if token.expires_at <= locked_now:
            raise ContextTokenExpiredError
        mount_session = session.exec(
            select(MountContextSession)
            .where(MountContextSession.id == token.session_id)
            .execution_options(populate_existing=True)
        ).first()
        if (
            mount_session is None
            or mount_session.status != "active"
            or mount_session.revoked_at is not None
        ):
            raise ContextTokenInvalidError
        locked_absolute_now = datetime.now(UTC)
        if mount_session.absolute_expires_at.tzinfo is None:
            locked_absolute_now = locked_absolute_now.replace(tzinfo=None)
        if mount_session.absolute_expires_at <= locked_absolute_now:
            raise ContextTokenExpiredError
    return token, mount_session


def _context_for_session(
    session: Session,
    mount_session: MountContextSession,
    *,
    clerk_issuer: str,
) -> ResolvedMountContext:
    if mount_session.session_kind == "anonymous":
        if (
            mount_session.actor_id is not None
            or mount_session.tenant_id is not None
            or mount_session.person_binding_id is not None
            or mount_session.source_identity_id is not None
            or mount_session.surface_ceiling != "public"
            or mount_session.auth_tier_ceiling != "anonymous"
        ):
            raise AuthorityRequestDeniedError
    elif mount_session.session_kind == "authenticated":
        if (
            mount_session.actor_id is None
            or mount_session.person_binding_id is None
            or mount_session.source_identity_id is None
        ):
            raise AuthorityRequestDeniedError
    else:
        raise AuthorityRequestDeniedError

    actor = None
    if mount_session.actor_id is not None:
        actor = session.get(AuthorityActor, mount_session.actor_id)
        if actor is None or actor.status != "active" or actor.actor_kind != "human":
            raise AuthorityRequestDeniedError

    source_identity = None
    if mount_session.source_identity_id is not None:
        source_identity = session.get(
            AuthorityActorExternalIdentity,
            mount_session.source_identity_id,
        )
        if (
            source_identity is None
            or source_identity.revoked_at is not None
            or source_identity.actor_id != mount_session.actor_id
            or source_identity.issuer != clerk_issuer
        ):
            raise AuthorityRequestDeniedError

    tenant = None
    if mount_session.tenant_id is not None:
        tenant = _active_tenant(session, mount_session.tenant_id)

    person = None
    binding = None
    if mount_session.person_binding_id is not None:
        binding = session.get(AuthorityPersonBinding, mount_session.person_binding_id)
        if (
            binding is None
            or binding.status != "active"
            or binding.revoked_at is not None
            or binding.verified_at is None
        ):
            raise AuthorityRequestDeniedError
        if binding.tenant_id != mount_session.tenant_id:
            raise AuthorityRequestDeniedError
        person = session.get(AuthorityPerson, binding.person_id)
        if person is None or person.status != "active":
            raise AuthorityRequestDeniedError

    session_prep_context_ref = None
    if binding is not None and binding.binding_kind == "provider_record":
        if actor is None or tenant is None:
            raise AuthorityRequestDeniedError
        membership = _active_provider_membership(
            session,
            actor_id=actor.id,
            tenant_id=tenant.id,
        )
        if membership is None:
            raise AuthorityRequestDeniedError
        if source_identity is None:
            raise AuthorityRequestDeniedError
        try:
            record_scope = validate_provider_record_scope(
                session=session,
                binding=binding,
                actor_id=actor.id,
                clerk_subject=source_identity.subject,
            )
        except ProviderRecordScopeDenied:
            raise AuthorityRequestDeniedError from None
        if record_scope is not None:
            session_prep_context_ref = record_scope.context_snapshot_ref
        surface = "provider_assistant"
        derived_auth_tier = "provider"
    else:
        if actor is not None and binding is None:
            raise AuthorityRequestDeniedError
        if binding is not None and binding.actor_id != mount_session.actor_id:
            raise AuthorityRequestDeniedError
        surface = "public"
        if actor is None:
            derived_auth_tier = "anonymous"
        elif person is not None:
            derived_auth_tier = _public_auth_tier(person)
        else:
            raise AuthorityRequestDeniedError

    if surface != mount_session.surface_ceiling:
        raise AuthorityRequestDeniedError
    if surface == "provider_assistant":
        if mount_session.auth_tier_ceiling != "provider":
            raise AuthorityRequestDeniedError
        auth_tier = derived_auth_tier
        capabilities = PROVIDER_CAPABILITIES
        if session_prep_context_ref is not None or (
            binding is not None
            and source_identity is not None
            and session_prep_scope.synthetic_session_prep_context_ref(
                clerk_subject=source_identity.subject,
                record_ref=binding.binding_ref,
                session=session,
            )
            is not None
        ):
            capabilities = (*capabilities, "session-prep:read")
    else:
        public_tier_rank = {
            "anonymous": 0,
            "prospect": 1,
            "identified": 2,
            "enrolled": 3,
        }
        try:
            capped_rank = min(
                public_tier_rank[derived_auth_tier],
                public_tier_rank[mount_session.auth_tier_ceiling],
            )
        except KeyError:
            raise AuthorityRequestDeniedError from None
        auth_tier = next(
            tier for tier, rank in public_tier_rank.items() if rank == capped_rank
        )
        if person is None or auth_tier == "anonymous":
            capabilities = PUBLIC_FREE_CAPABILITIES
        elif auth_tier == "prospect":
            capabilities = PUBLIC_PROSPECT_CAPABILITIES
        else:
            capabilities = PUBLIC_PATIENT_CAPABILITIES

    return ResolvedMountContext(
        surface=surface,
        auth_tier=auth_tier,
        capabilities=tuple(sorted(capabilities)),
        tenant_id=str(tenant.id) if tenant is not None else None,
        actor_id=str(actor.id) if actor is not None else None,
        session_id=mount_session.id,
        person_id=person.id if person is not None else None,
        session_prep_context_ref=session_prep_context_ref,
    )


def _binding_fingerprint(context: ResolvedMountContext) -> str:
    canonical = json.dumps(
        {
            **context.public_body(),
            "person_id": str(context.person_id) if context.person_id else None,
            "session_id": str(context.session_id),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _require_current_token_window(
    token: MountContextToken, mount_session: MountContextSession
) -> None:
    """Refuse the same bearer when an authority lock outlasts its validity."""
    if token.revoked_at is not None or mount_session.revoked_at is not None:
        raise ContextTokenInvalidError
    if mount_session.status != "active" or token.session_id != mount_session.id:
        raise ContextTokenInvalidError
    observed_at = datetime.now(UTC)
    for expires_at in (token.expires_at, mount_session.absolute_expires_at):
        now = (
            observed_at.replace(tzinfo=None)
            if expires_at.tzinfo is None
            else observed_at
        )
        if expires_at <= now:
            raise ContextTokenExpiredError


@provider_database_deadline
def authorize_interaction(
    *,
    session: Session,
    workload_client_id: str,
    mount_context_token: str,
    interaction_ref: str,
    client_turn_id: UUID,
    settings: AuthorityKernelSettings = AUTHORITY_KERNEL_SETTINGS,
    _before_view_as: Callable[[MountContextSession], None] | None = None,
    _before_context: Callable[[MountContextSession], None] | None = None,
) -> AuthorizedInteraction:
    """Re-resolve and lock one exact provider interaction for a durable action."""

    _ensure_enabled(settings)
    token, mount_session = _active_token(
        session,
        mount_context_token,
        lock_for_update=True,
        _before_view_as=_before_view_as,
    )
    lineage_statement = (
        select(InteractionLineage)
        .where(InteractionLineage.interaction_ref == interaction_ref)
        .with_for_update()
    )
    lineage = None
    if _before_context is not None:
        # Capture previously held lineage before taking the remaining context
        # locks. Preserve that order even though context is now derived once.
        lineage = session.exec(lineage_statement).first()
        # Capture takes its remaining authority locks before the single complete
        # context decision. The callback supplies no earlier authorization.
        _before_context(mount_session)
        # Those locks can wait. Recheck both deadlines after the wait, keeping
        # the same bearer/token and session revocation refusals.
        _require_current_token_window(token, mount_session)
    context = _context_for_session(
        session,
        mount_session,
        clerk_issuer=settings.clerk_issuer,
    )
    if _before_context is None:
        lineage = session.exec(lineage_statement).first()
    # Both context and lineage locks may wait after _active_token's check.
    # The token row remains locked; refresh the same family without changing
    # lock order so revocation during that wait cannot use cached ORM state.
    current_mount = session.exec(
        select(MountContextSession)
        .where(MountContextSession.id == mount_session.id)
        .execution_options(populate_existing=True)
    ).first()
    if current_mount is None:
        raise ContextTokenInvalidError
    _require_current_token_window(token, current_mount)
    mount_session = current_mount
    if lineage is None:
        raise InteractionInvalidError
    if (
        lineage.workload_client_id != workload_client_id
        or lineage.client_turn_id != client_turn_id
        or lineage.session_id != mount_session.id
        or lineage.binding_fingerprint != _binding_fingerprint(context)
    ):
        raise ClientTurnConflictError
    if (
        context.surface != "provider_assistant"
        or context.auth_tier != "provider"
        or context.person_id is None
        or mount_session.tenant_id is None
        or mount_session.actor_id is None
    ):
        raise AuthorityRequestDeniedError
    return AuthorizedInteraction(
        context=context,
        mount_session=mount_session,
        lineage=lineage,
    )


@provider_database_deadline
def authorize_observational_reporting(
    *,
    session: Session,
    workload_client_id: str,
    interaction_ref: str,
    mount_context_token: str | None,
    settings: AuthorityKernelSettings = AUTHORITY_KERNEL_SETTINGS,
) -> AuthorizedInteraction:
    """Authorize provider-first reporting against one locked lineage.

    The mount token is optional for this observational-only endpoint. If it is
    supplied, its hash must identify the same, non-revoked mount session. Token
    expiry is deliberately ignored here because the accepted contract permits a
    late audit acknowledgement after expiry; this function confers no action or
    read authority.
    """

    _ensure_enabled(settings)
    # First discover the immutable mount binding without row locks. View-as
    # then takes Doctor -> ViewAsSession locks before this reporting write takes
    # its lineage/session locks, keeping the global ordering consistent.
    lineage = session.exec(
        select(InteractionLineage).where(
            InteractionLineage.interaction_ref == interaction_ref
        )
    ).first()
    if lineage is None or lineage.workload_client_id != workload_client_id:
        raise InteractionInvalidError

    mount_session = session.exec(
        select(MountContextSession).where(MountContextSession.id == lineage.session_id)
    ).first()
    if (
        mount_session is None
        or mount_session.status != "active"
        or mount_session.revoked_at is not None
    ):
        raise InteractionInvalidError

    from app.services.view_as import mount_context_session_blocked_by_view_as

    if mount_context_session_blocked_by_view_as(
        session,
        mount_session=mount_session,
    ):
        raise AuthorityRequestDeniedError

    # Match the interaction-write order: actor/view-as (above), optional token,
    # lineage, then mount session. Acquiring the token after the lineage could
    # deadlock with ``authorize_interaction`` on concurrent writes.
    token = None
    if mount_context_token is not None:
        token = session.exec(
            select(MountContextToken)
            .where(MountContextToken.token_hash == _token_hash(mount_context_token))
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        if (
            token is None
            or token.revoked_at is not None
            or token.session_id != mount_session.id
        ):
            raise ContextTokenInvalidError

    lineage = session.exec(
        select(InteractionLineage)
        .where(InteractionLineage.interaction_ref == interaction_ref)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    if lineage is None or lineage.workload_client_id != workload_client_id:
        raise InteractionInvalidError

    mount_session = session.exec(
        select(MountContextSession)
        .where(MountContextSession.id == lineage.session_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    if (
        mount_session is None
        or mount_session.status != "active"
        or mount_session.revoked_at is not None
    ):
        raise InteractionInvalidError

    if token is not None:
        if token.revoked_at is not None or token.session_id != mount_session.id:
            raise ContextTokenInvalidError

    context = _context_for_session(
        session,
        mount_session,
        clerk_issuer=settings.clerk_issuer,
    )
    if (
        context.surface != "provider_assistant"
        or context.auth_tier != "provider"
        or context.person_id is None
        or mount_session.tenant_id is None
        or mount_session.actor_id is None
    ):
        # V7 staging activation is deliberately provider-only. Public reporting
        # remains fail-closed until anonymous issuance and reconciliation land.
        raise AuthorityRequestDeniedError
    if lineage.binding_fingerprint != _binding_fingerprint(context):
        raise ClientTurnConflictError
    return AuthorizedInteraction(
        context=context,
        mount_session=mount_session,
        lineage=lineage,
    )


def rotate_context_token(
    *,
    current_token: str,
    requested_surface: str | None,
    record_ref: str | None,
    session: Session | None = None,
    settings: AuthorityKernelSettings = AUTHORITY_KERNEL_SETTINGS,
) -> ContextTokenIssueResult:
    _ensure_enabled(settings)
    if session is None:
        with get_session() as managed_session:
            return rotate_context_token(
                current_token=current_token,
                requested_surface=requested_surface,
                record_ref=record_ref,
                session=cast(Session, managed_session),
                settings=settings,
            )
    db = session
    token, mount_session = _active_token(db, current_token, lock_for_update=True)
    context = _context_for_session(
        db,
        mount_session,
        clerk_issuer=settings.clerk_issuer,
    )
    if requested_surface is not None and requested_surface != context.surface:
        raise AuthorityRequestDeniedError

    binding_ref = None
    if mount_session.person_binding_id is not None:
        binding = db.get(AuthorityPersonBinding, mount_session.person_binding_id)
        binding_ref = binding.binding_ref if binding is not None else None
    if record_ref is not None and record_ref != binding_ref:
        raise AuthorityRequestDeniedError

    token.revoked_at = datetime.now(UTC)
    db.add(token)
    result = _issue_token(db, mount_session=mount_session, settings=settings)
    db.add(
        _audit_event(
            event_type="mount_context_token.rotated",
            resource_type="mount_context_token",
            resource_ref=str(token.id),
            actor_id=mount_session.actor_id,
            tenant_id=mount_session.tenant_id,
        )
    )
    db.commit()
    return result


def _existing_lineage(
    session: Session,
    client_turn_id: UUID,
) -> InteractionLineage | None:
    return session.exec(
        select(InteractionLineage).where(
            InteractionLineage.client_turn_id == client_turn_id
        )
    ).first()


def _validate_lineage_retry(
    lineage: InteractionLineage,
    *,
    workload_client_id: str,
    session_id: TypeIDType,
    binding_fingerprint: str,
) -> None:
    if (
        lineage.workload_client_id != workload_client_id
        or lineage.session_id != session_id
        or lineage.binding_fingerprint != binding_fingerprint
    ):
        raise ClientTurnConflictError


@provider_database_deadline
def resolve_mount_context(
    *,
    workload_client_id: str,
    mount_context_token: str,
    client_turn_id: UUID,
    session: Session | None = None,
    settings: AuthorityKernelSettings = AUTHORITY_KERNEL_SETTINGS,
) -> MountResolutionResult:
    _ensure_enabled(settings)
    if session is None:
        with get_session() as managed_session:
            return resolve_mount_context(
                workload_client_id=workload_client_id,
                mount_context_token=mount_context_token,
                client_turn_id=client_turn_id,
                session=cast(Session, managed_session),
                settings=settings,
            )
    db = session
    _, mount_session = _active_token(db, mount_context_token)
    context = _context_for_session(
        db,
        mount_session,
        clerk_issuer=settings.clerk_issuer,
    )
    fingerprint = _binding_fingerprint(context)

    existing = _existing_lineage(db, client_turn_id)
    if existing is not None:
        _validate_lineage_retry(
            existing,
            workload_client_id=workload_client_id,
            session_id=mount_session.id,
            binding_fingerprint=fingerprint,
        )
        return MountResolutionResult(
            context=context,
            interaction_ref=existing.interaction_ref,
            reused=True,
        )

    lineage = InteractionLineage(
        workload_client_id=workload_client_id,
        client_turn_id=client_turn_id,
        session_id=mount_session.id,
        interaction_ref=_new_opaque_value("ix"),
        binding_fingerprint=fingerprint,
    )
    db.add(lineage)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        install_provider_database_limits(
            session=db, workload_client_id=workload_client_id, settings=settings,
        )
        raced = _existing_lineage(db, client_turn_id)
        if raced is None:
            raise
        _validate_lineage_retry(
            raced,
            workload_client_id=workload_client_id,
            session_id=mount_session.id,
            binding_fingerprint=fingerprint,
        )
        return MountResolutionResult(
            context=context,
            interaction_ref=raced.interaction_ref,
            reused=True,
        )

    db.add(
        _audit_event(
            event_type="interaction_lineage.opened",
            resource_type="interaction_lineage",
            resource_ref=lineage.interaction_ref,
            actor_id=mount_session.actor_id,
            tenant_id=mount_session.tenant_id,
            person_id=context.person_id,
            interaction_id=lineage.id,
        )
    )
    db.commit()
    return MountResolutionResult(
        context=context,
        interaction_ref=lineage.interaction_ref,
        reused=False,
    )


@provider_database_deadline
def bootstrap_mount_context(
    *,
    mount_context_token: str,
    workload_client_id: str | None = None,
    session: Session | None = None,
    settings: AuthorityKernelSettings = AUTHORITY_KERNEL_SETTINGS,
) -> ResolvedMountContext:
    """Resolve a governed mount without opening an interaction lineage.

    This read-only operation exists for pre-turn UI bootstrap. It intentionally
    does not accept a client-turn identifier, create an idempotency record, or
    grant action authority. A later user turn must use ``resolve_mount_context``
    before any durable action or model work begins.
    """

    _ensure_enabled(settings)
    if session is None:
        with get_session() as managed_session:
            return bootstrap_mount_context(
                mount_context_token=mount_context_token,
                workload_client_id=workload_client_id,
                session=cast(Session, managed_session),
                settings=settings,
            )

    _, mount_session = _active_token(session, mount_context_token)
    context = _context_for_session(
        session,
        mount_session,
        clerk_issuer=settings.clerk_issuer,
    )
    return context


def authorize_mount_read(
    *,
    session: Session,
    mount_context_token: str,
    settings: AuthorityKernelSettings = AUTHORITY_KERNEL_SETTINGS,
) -> tuple[ResolvedMountContext, MountContextSession]:
    """Reauthenticate a current read without inventing an interaction lineage."""
    _ensure_enabled(settings)
    _, mount = _active_token(session, mount_context_token, lock_for_update=True)
    return _context_for_session(
        session, mount, clerk_issuer=settings.clerk_issuer
    ), mount
