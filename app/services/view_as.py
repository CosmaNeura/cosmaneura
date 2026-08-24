"""Fail-closed support view-as lifecycle.

View-as is a short-lived, read-only diagnostic capability.  It never changes
the authenticated Clerk principal and never treats an email address or the
legacy ``DoctorRole.admin`` flag as authority.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from typeid import TypeID
from typeid.errors import TypeIDException

from app.configuration.authority import AUTHORITY_KERNEL_SETTINGS
from app.services.clerk_identity import verified_primary_email
from app.services.doctor_access import resolve_doctor_access
from app.services.pilot_access_grandfather import revoked_pilot_access_grant
from app.services.superadmin_capability import (
    SuperadminCapabilityEnvironment,
    SuperadminCapabilityError,
    SuperadminCapabilityKind,
    configured_superadmin_capability_environment,
    get_active_superadmin_capability,
)

from activemodel.types import TypeIDType
from app.models.authority import (
    AuthorityActor,
    AuthorityActorExternalIdentity,
    MountContextSession,
    MountContextToken,
)
from app.models.doctor import Doctor
from app.models.view_as import ViewAsAuditEvent, ViewAsSession
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

VIEW_AS_TTL = timedelta(minutes=15)
VIEW_AS_SESSION_PREFIX = "view_as_session"
VIEW_AS_REQUEST_BINDING_HEADER = "X-Cosmaneura-View-As-Binding"
INACTIVE_VIEW_AS_REQUEST_BINDING = "inactive"
VIEW_AS_REQUEST_BINDING_PREFIX = "vab1."
VIEW_AS_REQUEST_BINDING_DOMAIN = "cosmaneura:view-as:binding:v1"

ViewAsDiagnosticCode = Literal[
    "target_disabled",
    "target_identity_unready",
    "target_identity_ambiguous",
    "target_access_revoked",
    "target_access_pending",
]

ViewAsEndReason = Literal[
    "operator_exit",
    "rotated",
    "expired",
    "capability_revoked",
    "session_binding_changed",
]


class ViewAsError(ValueError):
    """Privacy-safe refusal containing no identity values."""


@dataclass(frozen=True, slots=True)
class ViewAsActorIdentity:
    issuer: str
    subject: str
    clerk_session_id: str


@dataclass(frozen=True, slots=True)
class ViewAsBeginResult:
    session: ViewAsSession
    browser_nonce: str
    diagnostic_code: ViewAsDiagnosticCode | None


@dataclass(frozen=True, slots=True)
class ResolvedViewAs:
    session: ViewAsSession
    actor: Doctor
    target: Doctor | None
    diagnostic_code: ViewAsDiagnosticCode | None


@dataclass(frozen=True, slots=True)
class ViewAsResolution:
    active: ResolvedViewAs | None
    clear_browser_session: bool
    rejected_unsafe_fallback: bool


@dataclass(frozen=True, slots=True)
class _LiveTargetIdentityEvidence:
    target_identity_sha256: str
    diagnostic_code: ViewAsDiagnosticCode | None


def _now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ViewAsError("view-as timestamp must include a timezone")
    return current


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def view_as_request_binding(record: ViewAsSession) -> str:
    """Derive a non-authoritative, opaque browser request binding."""

    material = "\0".join(
        (
            VIEW_AS_REQUEST_BINDING_DOMAIN,
            str(record.id),
            record.browser_nonce_sha256,
            str(record.target_doctor_id),
        )
    )
    return f"{VIEW_AS_REQUEST_BINDING_PREFIX}{_sha256(material)}"


def _valid_identity(identity: ViewAsActorIdentity) -> ViewAsActorIdentity:
    values = (identity.issuer, identity.subject, identity.clerk_session_id)
    if any(not value or value != value.strip() or len(value) > 512 for value in values):
        raise ViewAsError("verified Clerk session identity is required")
    return identity


def _event_digest(
    *,
    environment: SuperadminCapabilityEnvironment,
    event_type: str,
    outcome: str,
    reason_code: str,
    occurred_at: datetime,
    actor_doctor_id: TypeIDType,
    target_doctor_id: TypeIDType | None,
    session_id: TypeIDType | None,
    capability_grant_id: TypeIDType | None,
) -> str:
    payload = {
        "actor_doctor_id": str(actor_doctor_id),
        "capability_grant_id": (
            str(capability_grant_id) if capability_grant_id is not None else None
        ),
        "environment": environment.value,
        "event_type": event_type,
        "occurred_at": occurred_at.isoformat(),
        "outcome": outcome,
        "reason_code": reason_code,
        "schema_version": 1,
        "session_id": str(session_id) if session_id is not None else None,
        "target_doctor_id": (
            str(target_doctor_id) if target_doctor_id is not None else None
        ),
    }
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return _sha256(canonical)


def _audit(
    session: Session,
    *,
    environment: SuperadminCapabilityEnvironment,
    event_type: Literal["began", "ended", "expired", "stale_cleared", "rejected"],
    outcome: Literal["accepted", "denied"],
    reason_code: str,
    occurred_at: datetime,
    actor_doctor_id: TypeIDType,
    target_doctor_id: TypeIDType | None = None,
    view_as_session_id: TypeIDType | None = None,
    capability_grant_id: TypeIDType | None = None,
) -> ViewAsAuditEvent:
    event = ViewAsAuditEvent(
        environment=environment.value,
        event_type=event_type,
        outcome=outcome,
        reason_code=reason_code,
        occurred_at=occurred_at,
        event_digest=_event_digest(
            environment=environment,
            event_type=event_type,
            outcome=outcome,
            reason_code=reason_code,
            occurred_at=occurred_at,
            actor_doctor_id=actor_doctor_id,
            target_doctor_id=target_doctor_id,
            session_id=view_as_session_id,
            capability_grant_id=capability_grant_id,
        ),
        session_id=view_as_session_id,
        capability_grant_id=capability_grant_id,
        actor_doctor_id=actor_doctor_id,
        target_doctor_id=target_doctor_id,
    )
    session.add(event)
    return event


def target_diagnostic(
    session: Session,
    target: Doctor,
) -> ViewAsDiagnosticCode | None:
    """Return a sanitized target state; ``None`` means safe read-only access."""

    if target.deleted_at is not None:
        return "target_disabled"
    if (
        not target.email
        or not target.email.strip()
        or not target.clerk_id
        or not target.clerk_id.startswith("user_")
    ):
        return "target_identity_unready"

    normalized_email = target.email.strip().lower()
    owners = list(
        session.exec(
            select(Doctor).where(
                func.lower(func.btrim(Doctor.email)) == normalized_email
            )
        ).all()
    )
    if len(owners) != 1 or owners[0].id != target.id:
        return "target_identity_ambiguous"

    # Authority reconciliation is additive, so the absence of an authority
    # identity is not by itself a diagnostic failure. If an identity has been
    # provisioned, however, never present an interactive target through an
    # ambiguous, revoked, orphaned, or inactive binding.
    authority_identities = list(
        session.exec(
            select(AuthorityActorExternalIdentity).where(
                AuthorityActorExternalIdentity.subject == target.clerk_id
            )
        ).all()
    )
    authority_settings = AUTHORITY_KERNEL_SETTINGS
    if authority_identities and not authority_settings.has_valid_clerk_issuer():
        return "target_identity_unready"
    if any(
        identity.issuer != authority_settings.clerk_issuer
        for identity in authority_identities
    ):
        return "target_identity_ambiguous"
    if len(authority_identities) > 1:
        return "target_identity_ambiguous"
    if authority_identities:
        authority_identity = authority_identities[0]
        if authority_identity.revoked_at is not None:
            return "target_access_revoked"
        authority_actor = session.get(AuthorityActor, authority_identity.actor_id)
        if authority_actor is None:
            return "target_identity_unready"
        if authority_actor.status != "active":
            return "target_access_revoked"
        if authority_actor.actor_kind != "human":
            return "target_identity_ambiguous"
        active_actor_identities = list(
            session.exec(
                select(AuthorityActorExternalIdentity).where(
                    AuthorityActorExternalIdentity.actor_id == authority_actor.id,
                    cast(Any, AuthorityActorExternalIdentity.revoked_at).is_(None),
                )
            ).all()
        )
        if (
            len(active_actor_identities) != 1
            or active_actor_identities[0].id != authority_identity.id
        ):
            return "target_identity_ambiguous"
    if revoked_pilot_access_grant(target.clerk_id) is not None:
        return "target_access_revoked"

    decision = resolve_doctor_access(target)
    if decision.allowed:
        return None
    if decision.denial_reason in {
        "doctor_disabled",
        "pilot_grant_revoked",
        "practice_membership_revoked",
    }:
        return "target_access_revoked"
    return "target_access_pending"


def selected_target_diagnostic(
    session: Session,
    target: Doctor,
) -> ViewAsDiagnosticCode | None:
    """Revalidate one selected target against Clerk without exposing identity.

    Target enumeration deliberately remains local-only. The selected target is
    checked at begin and every active resolution so Clerk deletion, lock, ban,
    verification loss, provider failure, or primary-email drift cannot leave an
    interactive support session open.
    """

    local_diagnostic = target_diagnostic(session, target)
    if local_diagnostic is not None:
        return local_diagnostic
    return _live_target_identity_evidence(target).diagnostic_code


def _target_identity_sha256(target: Doctor) -> str:
    payload = {
        "clerk_subject": target.clerk_id,
        "doctor_id": str(target.id),
        "email": target.email.strip().lower() if target.email else None,
    }
    return _sha256(json.dumps(payload, separators=(",", ":"), sort_keys=True))


def _live_target_identity_evidence(
    target: Doctor,
) -> _LiveTargetIdentityEvidence:
    target_identity_sha256 = _target_identity_sha256(target)
    if (
        not target.email
        or not target.email.strip()
        or not target.clerk_id
        or not target.clerk_id.startswith("user_")
    ):
        return _LiveTargetIdentityEvidence(
            target_identity_sha256=target_identity_sha256,
            diagnostic_code="target_identity_unready",
        )
    live_email = verified_primary_email(target.clerk_id)
    if live_email is None:
        return _LiveTargetIdentityEvidence(
            target_identity_sha256=target_identity_sha256,
            diagnostic_code="target_identity_unready",
        )
    if not hmac.compare_digest(live_email, target.email.strip().lower()):
        return _LiveTargetIdentityEvidence(
            target_identity_sha256=target_identity_sha256,
            diagnostic_code="target_identity_ambiguous",
        )
    return _LiveTargetIdentityEvidence(
        target_identity_sha256=target_identity_sha256,
        diagnostic_code=None,
    )


def _locked_target(
    session: Session,
    target_doctor_id: TypeIDType,
) -> Doctor | None:
    return session.exec(
        select(Doctor)
        .where(Doctor.id == target_doctor_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()


def _active_actor_session(
    session: Session,
    *,
    actor_doctor_id: TypeIDType,
    environment: SuperadminCapabilityEnvironment,
    lock: bool,
) -> ViewAsSession | None:
    statement = select(ViewAsSession).where(
        ViewAsSession.actor_doctor_id == actor_doctor_id,
        ViewAsSession.environment == environment.value,
        ViewAsSession.ended_at == None,  # noqa: E711
    )
    if lock:
        statement = statement.with_for_update()
    rows = list(session.exec(statement).all())
    if len(rows) > 1:
        raise ViewAsError("view-as session state is ambiguous")
    return rows[0] if rows else None


def _end_locked_session(
    session: Session,
    current: ViewAsSession,
    *,
    reason: ViewAsEndReason,
    at: datetime,
    event_type: Literal["ended", "expired", "stale_cleared"],
) -> None:
    if current.ended_at is not None:
        return
    current.ended_at = at
    current.end_reason = reason
    session.add(current)
    _audit(
        session,
        environment=SuperadminCapabilityEnvironment(current.environment),
        event_type=event_type,
        outcome="accepted",
        reason_code=reason,
        occurred_at=at,
        actor_doctor_id=current.actor_doctor_id,
        target_doctor_id=current.target_doctor_id,
        view_as_session_id=current.id,
        capability_grant_id=current.capability_grant_id,
    )


def _actor_binding_matches(
    record: ViewAsSession,
    *,
    actor: Doctor,
    identity: ViewAsActorIdentity,
) -> bool:
    return all(
        (
            actor.deleted_at is None,
            actor.clerk_id == identity.subject,
            hmac.compare_digest(record.actor_issuer_sha256, _sha256(identity.issuer)),
            hmac.compare_digest(
                record.actor_subject_sha256,
                _sha256(identity.subject),
            ),
            hmac.compare_digest(
                record.clerk_session_sha256,
                _sha256(identity.clerk_session_id),
            ),
        )
    )


def _capability_matches(
    session: Session,
    *,
    record: ViewAsSession,
    actor: Doctor,
    identity: ViewAsActorIdentity,
    environment: SuperadminCapabilityEnvironment,
    at: datetime,
) -> bool:
    try:
        capability = get_active_superadmin_capability(
            session,
            issuer=identity.issuer,
            subject=identity.subject,
            doctor_id=actor.id,
            environment=environment,
            capability_kind=SuperadminCapabilityKind.view_as,
            at=at,
        )
    except SuperadminCapabilityError:
        return False
    return capability is not None and capability.id == record.capability_grant_id


def reconcile_view_as_without_browser_binding(
    session: Session,
    *,
    actor: Doctor,
    identity: ViewAsActorIdentity,
    environment: SuperadminCapabilityEnvironment,
    unsafe_request: bool,
    now: datetime | None = None,
) -> ViewAsResolution:
    """Reconcile an actor-bound DB session when its browser binding is absent.

    Every non-control request locks the actor row before checking the active
    session. This serializes the explicit inactive marker with
    ``begin_view_as`` (which locks the same row), so neither a read nor a
    mutation can race into the wrong account context. A valid active session
    remains active and keeps unsafe requests denied; a subsequent safe
    navigation clears the irrecoverably lost browser binding.
    """

    current_time = _now(now)
    verified = _valid_identity(identity)
    locked_actor = session.exec(
        select(Doctor).where(Doctor.id == actor.id).with_for_update()
    ).one_or_none()
    if locked_actor is None:
        return ViewAsResolution(None, False, unsafe_request)
    actor = locked_actor

    record = _active_actor_session(
        session,
        actor_doctor_id=actor.id,
        environment=environment,
        lock=True,
    )
    if record is None:
        return ViewAsResolution(None, False, False)

    if not _actor_binding_matches(record, actor=actor, identity=verified):
        _end_locked_session(
            session,
            record,
            reason="session_binding_changed",
            at=current_time,
            event_type="stale_cleared",
        )
        session.commit()
        return ViewAsResolution(None, True, unsafe_request)
    if record.absolute_expires_at <= current_time:
        _end_locked_session(
            session,
            record,
            reason="expired",
            at=current_time,
            event_type="expired",
        )
        session.commit()
        return ViewAsResolution(None, True, unsafe_request)
    if not _capability_matches(
        session,
        record=record,
        actor=actor,
        identity=verified,
        environment=environment,
        at=current_time,
    ):
        _end_locked_session(
            session,
            record,
            reason="capability_revoked",
            at=current_time,
            event_type="stale_cleared",
        )
        session.commit()
        return ViewAsResolution(None, True, unsafe_request)

    if unsafe_request:
        # Keep the server session active. Repeating the request without the
        # signed browser binding must not turn a one-time denial into a bypass.
        return ViewAsResolution(None, False, True)

    _end_locked_session(
        session,
        record,
        reason="session_binding_changed",
        at=current_time,
        event_type="stale_cleared",
    )
    session.commit()
    return ViewAsResolution(None, True, False)


def actor_bound_unended_view_as_exists(
    session: Session,
    *,
    actor: Doctor,
    identity: ViewAsActorIdentity,
) -> bool:
    """Find an exact actor/session binding when runtime env config is unavailable.

    This deliberately searches across environments only for the exact immutable
    actor, issuer, and subject. A changed Clerk session id cannot be trusted to
    clear old support state while environment configuration is unavailable;
    normal reconciliation will end it once configuration recovers. Locking the
    actor row serializes the lookup with ``begin_view_as`` without treating
    unrelated ordinary users as view-as actors.
    """

    verified = _valid_identity(identity)
    locked_actor = session.exec(
        select(Doctor).where(Doctor.id == actor.id).with_for_update()
    ).one_or_none()
    if (
        locked_actor is None
        or locked_actor.deleted_at is not None
        or locked_actor.clerk_id != verified.subject
    ):
        raise ViewAsError("view-as actor identity is unavailable")
    statement = (
        select(ViewAsSession)
        .where(
            ViewAsSession.actor_doctor_id == locked_actor.id,
            ViewAsSession.ended_at == None,  # noqa: E711
            ViewAsSession.actor_issuer_sha256 == _sha256(verified.issuer),
            ViewAsSession.actor_subject_sha256 == _sha256(verified.subject),
        )
        .with_for_update()
    )
    return session.exec(statement).first() is not None


def context_token_exchange_blocked_by_view_as(
    session: Session,
    *,
    issuer: str,
    subject: str,
    clerk_session_id: str,
    environment: SuperadminCapabilityEnvironment,
    now: datetime | None = None,
) -> bool:
    """Return whether a verified Clerk actor has an active view-as session."""

    actors = list(session.exec(select(Doctor).where(Doctor.clerk_id == subject)).all())
    if len(actors) != 1:
        return (
            session.exec(
                select(ViewAsSession).where(
                    ViewAsSession.environment == environment.value,
                    ViewAsSession.ended_at == None,  # noqa: E711
                    ViewAsSession.actor_issuer_sha256 == _sha256(issuer),
                    ViewAsSession.actor_subject_sha256 == _sha256(subject),
                    ViewAsSession.clerk_session_sha256 == _sha256(clerk_session_id),
                )
            ).first()
            is not None
        )
    resolution = reconcile_view_as_without_browser_binding(
        session,
        actor=actors[0],
        identity=ViewAsActorIdentity(
            issuer=issuer,
            subject=subject,
            clerk_session_id=clerk_session_id,
        ),
        environment=environment,
        unsafe_request=True,
        now=now,
    )
    return resolution.rejected_unsafe_fallback


def context_token_rotation_blocked_by_view_as(
    session: Session,
    *,
    current_token: str,
    environment: SuperadminCapabilityEnvironment,
    now: datetime | None = None,
) -> bool:
    """Deny rotation when its immutable source actor is in view-as.

    A mount token intentionally contains no Clerk-session id. Rotation is
    therefore checked more conservatively against any current, environment-
    scoped view-as session for the exact issuer+subject source identity.
    """

    if not 16 <= len(current_token) <= 8192:
        return False
    token = session.exec(
        select(MountContextToken).where(
            MountContextToken.token_hash == _sha256(current_token)
        )
    ).one_or_none()
    if token is None:
        return False
    mount_session = session.get(MountContextSession, token.session_id)
    if mount_session is None:
        return False
    return mount_context_session_blocked_by_view_as(
        session,
        mount_session=mount_session,
        environment=environment,
        now=now,
    )


def mount_context_session_blocked_by_view_as(
    session: Session,
    *,
    mount_session: MountContextSession,
    environment: SuperadminCapabilityEnvironment | None = None,
    now: datetime | None = None,
) -> bool:
    """Serialize every actor-bound mount operation against support view-as.

    Mount tokens do not carry a Clerk-session id, so this check binds the
    authority session to its immutable Clerk issuer+subject and the exact local
    Doctor. The actor row is locked before the view-as row and is retained for
    the caller's transaction, matching ``begin_view_as`` lock order. This makes
    tokens issued before activation unusable for bootstrap, resolution,
    rotation, or writes while support is viewing the account.

    If runtime environment configuration is unavailable, exact identity-bound
    rows are checked across environments. Ordinary tokens with no view-as state
    remain usable.
    """

    if (
        mount_session.session_kind != "authenticated"
        or mount_session.actor_id is None
        or mount_session.source_identity_id is None
    ):
        return False
    source_identity = session.get(
        AuthorityActorExternalIdentity,
        mount_session.source_identity_id,
    )
    if (
        source_identity is None
        or source_identity.actor_id != mount_session.actor_id
        or source_identity.revoked_at is not None
    ):
        return False

    if environment is None:
        try:
            environment = configured_superadmin_capability_environment()
        except SuperadminCapabilityError:
            environment = None

    environment_filter: tuple[Any, ...] = ()
    if environment is not None:
        environment_filter = (ViewAsSession.environment == environment.value,)

    actors = list(
        session.exec(
            select(Doctor).where(Doctor.clerk_id == source_identity.subject)
        ).all()
    )
    if len(actors) != 1:
        # No new session can begin without one exact Doctor. Still fail closed
        # if an exact immutable session binding survived identity corruption.
        records = list(
            session.exec(
                select(ViewAsSession)
                .where(
                    ViewAsSession.ended_at == None,  # noqa: E711
                    ViewAsSession.actor_issuer_sha256
                    == _sha256(source_identity.issuer),
                    ViewAsSession.actor_subject_sha256
                    == _sha256(source_identity.subject),
                    *environment_filter,
                )
                .with_for_update()
            ).all()
        )
        return bool(records)

    actor = session.exec(
        select(Doctor).where(Doctor.id == actors[0].id).with_for_update()
    ).one_or_none()
    if actor is None:
        return False

    records = list(
        session.exec(
            select(ViewAsSession)
            .where(
                ViewAsSession.actor_doctor_id == actor.id,
                ViewAsSession.ended_at == None,  # noqa: E711
                *environment_filter,
            )
            .with_for_update()
        ).all()
    )
    if not records:
        return False

    current_time = _now(now)
    changed = False
    for record in records:
        binding_matches = all(
            (
                actor.deleted_at is None,
                actor.clerk_id == source_identity.subject,
                hmac.compare_digest(
                    record.actor_issuer_sha256,
                    _sha256(source_identity.issuer),
                ),
                hmac.compare_digest(
                    record.actor_subject_sha256,
                    _sha256(source_identity.subject),
                ),
            )
        )
        if not binding_matches:
            # The mount identity is not authoritative enough to mutate a
            # potentially valid support session. Denial is sufficient.
            continue
        if record.absolute_expires_at <= current_time:
            _end_locked_session(
                session,
                record,
                reason="expired",
                at=current_time,
                event_type="expired",
            )
            changed = True
            continue
        try:
            record_environment = SuperadminCapabilityEnvironment(record.environment)
            capability = get_active_superadmin_capability(
                session,
                issuer=source_identity.issuer,
                subject=source_identity.subject,
                doctor_id=actor.id,
                environment=record_environment,
                capability_kind=SuperadminCapabilityKind.view_as,
                at=current_time,
            )
        except (SuperadminCapabilityError, ValueError):
            capability = None
        if capability is None or capability.id != record.capability_grant_id:
            _end_locked_session(
                session,
                record,
                reason="capability_revoked",
                at=current_time,
                event_type="stale_cleared",
            )
            changed = True

    if changed:
        # The current operation is still denied. A later retry may proceed once
        # every stale support session has been reconciled.
        session.commit()
    return True


def audit_view_as_begin_rejection(
    session: Session,
    *,
    actor: Doctor,
    identity: ViewAsActorIdentity,
    environment: SuperadminCapabilityEnvironment,
    requested_reason_code: Literal["target_unavailable"],
    now: datetime | None = None,
) -> bool:
    """Audit a malformed begin only when capability remains valid."""

    current = _now(now)
    try:
        verified = _valid_identity(identity)
        capability = get_active_superadmin_capability(
            session,
            issuer=verified.issuer,
            subject=verified.subject,
            doctor_id=actor.id,
            environment=environment,
            capability_kind=SuperadminCapabilityKind.view_as,
            at=current,
        )
    except (SuperadminCapabilityError, ViewAsError):
        capability = None
    has_capability = bool(
        capability is not None
        and actor.deleted_at is None
        and actor.clerk_id == identity.subject
    )
    if not has_capability:
        return False
    _audit(
        session,
        environment=environment,
        event_type="rejected",
        outcome="denied",
        reason_code=requested_reason_code,
        occurred_at=current,
        actor_doctor_id=actor.id,
        capability_grant_id=capability.id if capability is not None else None,
    )
    session.commit()
    return has_capability


def begin_view_as(
    session: Session,
    *,
    actor: Doctor,
    target_doctor_id: TypeIDType,
    identity: ViewAsActorIdentity,
    environment: SuperadminCapabilityEnvironment,
    now: datetime | None = None,
) -> ViewAsBeginResult:
    """Create and rotate one actor-bound view-as session atomically."""

    current = _now(now)
    verified = _valid_identity(identity)
    if actor.deleted_at is not None or actor.clerk_id != verified.subject:
        raise ViewAsError("view-as actor identity is unavailable")

    try:
        capability = get_active_superadmin_capability(
            session,
            issuer=verified.issuer,
            subject=verified.subject,
            doctor_id=actor.id,
            environment=environment,
            capability_kind=SuperadminCapabilityKind.view_as,
            at=current,
        )
    except SuperadminCapabilityError:
        capability = None
    if capability is None:
        _audit(
            session,
            environment=environment,
            event_type="rejected",
            outcome="denied",
            reason_code="capability_required",
            occurred_at=current,
            actor_doctor_id=actor.id,
            target_doctor_id=None,
        )
        session.commit()
        raise ViewAsError("view-as capability is required")

    locked_actor = session.exec(
        select(Doctor)
        .where(Doctor.id == actor.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if (
        locked_actor is None
        or locked_actor.deleted_at is not None
        or locked_actor.clerk_id != verified.subject
    ):
        raise ViewAsError("view-as actor identity is unavailable")

    # Revocation locks the same grant rows. Re-resolve under a row lock only
    # after the actor lock so a revocation that linearizes first is observed,
    # while a begin that linearizes first retains the grant through commit.
    try:
        locked_capability = get_active_superadmin_capability(
            session,
            issuer=verified.issuer,
            subject=verified.subject,
            doctor_id=locked_actor.id,
            environment=environment,
            capability_kind=SuperadminCapabilityKind.view_as,
            at=current,
            lock_for_update=True,
        )
    except SuperadminCapabilityError:
        locked_capability = None
    if locked_capability is None or locked_capability.id != capability.id:
        _audit(
            session,
            environment=environment,
            event_type="rejected",
            outcome="denied",
            reason_code="capability_required",
            occurred_at=current,
            actor_doctor_id=locked_actor.id,
            target_doctor_id=None,
        )
        session.commit()
        raise ViewAsError("view-as capability is required")
    capability = locked_capability

    # Every path uses actor -> active session -> target lock order. This avoids
    # deadlocks while serializing begin with request resolution and token use.
    prior = _active_actor_session(
        session,
        actor_doctor_id=locked_actor.id,
        environment=environment,
        lock=True,
    )
    target = _locked_target(session, target_doctor_id)
    if target is None or target.id == locked_actor.id:
        _audit(
            session,
            environment=environment,
            event_type="rejected",
            outcome="denied",
            reason_code="target_unavailable",
            occurred_at=current,
            actor_doctor_id=locked_actor.id,
            target_doctor_id=None,
            capability_grant_id=capability.id,
        )
        session.commit()
        raise ViewAsError("view-as target is unavailable")

    if prior is not None:
        _end_locked_session(
            session,
            prior,
            reason="rotated",
            at=current,
            event_type="ended",
        )

    browser_nonce = secrets.token_urlsafe(32)
    record = ViewAsSession(
        environment=environment.value,
        capability_grant_id=capability.id,
        actor_doctor_id=locked_actor.id,
        target_doctor_id=target.id,
        actor_issuer_sha256=_sha256(verified.issuer),
        actor_subject_sha256=_sha256(verified.subject),
        clerk_session_sha256=_sha256(verified.clerk_session_id),
        browser_nonce_sha256=_sha256(browser_nonce),
        started_at=current,
        absolute_expires_at=current + VIEW_AS_TTL,
    )
    session.add(record)
    session.flush()
    _audit(
        session,
        environment=environment,
        event_type="began",
        outcome="accepted",
        reason_code="support_review",
        occurred_at=current,
        actor_doctor_id=locked_actor.id,
        target_doctor_id=target.id,
        view_as_session_id=record.id,
        capability_grant_id=capability.id,
    )
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ViewAsError("a concurrent view-as change prevented activation") from exc
    session.refresh(record)
    return ViewAsBeginResult(
        session=record,
        browser_nonce=browser_nonce,
        diagnostic_code=selected_target_diagnostic(session, target),
    )


def _parse_session_id(value: str) -> TypeID:
    try:
        parsed = TypeID.from_string(value)
    except TypeIDException as exc:
        raise ViewAsError("view-as session is invalid") from exc
    if parsed.prefix != VIEW_AS_SESSION_PREFIX:
        raise ViewAsError("view-as session is invalid")
    return parsed


def resolve_view_as(
    session: Session,
    *,
    view_as_session_id: str,
    browser_nonce: str,
    actor: Doctor,
    identity: ViewAsActorIdentity,
    environment: SuperadminCapabilityEnvironment,
    unsafe_request: bool,
    now: datetime | None = None,
) -> ViewAsResolution:
    """Resolve one cookie-bound view-as session and clear stale state safely."""

    current_time = _now(now)
    try:
        verified = _valid_identity(identity)
        parsed_id = _parse_session_id(view_as_session_id)
    except ViewAsError:
        return ViewAsResolution(None, True, unsafe_request)

    # Clerk is an external dependency. Revalidate the candidate target before
    # taking any row lock so a slow provider call cannot serialize parallel
    # page reads or block begin/end operations. All local session, actor, and
    # target facts are checked again under locks below.
    live_evidence: _LiveTargetIdentityEvidence | None = None
    candidate = session.exec(
        select(ViewAsSession).where(ViewAsSession.id == parsed_id.uuid)
    ).one_or_none()
    if (
        candidate is not None
        and candidate.environment == environment.value
        and candidate.actor_doctor_id == actor.id
        and candidate.ended_at is None
        and candidate.absolute_expires_at > current_time
        and _actor_binding_matches(candidate, actor=actor, identity=verified)
        and hmac.compare_digest(
            candidate.browser_nonce_sha256,
            _sha256(browser_nonce),
        )
    ):
        candidate_target = cast(
            Doctor | None,
            session.get(Doctor, candidate.target_doctor_id),
        )
        if candidate_target is not None:
            live_evidence = _live_target_identity_evidence(candidate_target)

    locked_actor = session.exec(
        select(Doctor).where(Doctor.id == actor.id).with_for_update()
    ).one_or_none()
    if locked_actor is None:
        return ViewAsResolution(None, True, unsafe_request)
    actor = locked_actor

    statement = (
        select(ViewAsSession)
        .where(ViewAsSession.id == parsed_id.uuid)
        .with_for_update()
    )
    record = session.exec(statement).one_or_none()
    if record is None or record.environment != environment.value:
        return ViewAsResolution(None, True, unsafe_request)
    if record.actor_doctor_id != actor.id:
        return ViewAsResolution(None, True, unsafe_request)
    if record.ended_at is not None:
        return ViewAsResolution(None, True, unsafe_request)

    actor_binding_matches = _actor_binding_matches(
        record,
        actor=actor,
        identity=verified,
    ) and hmac.compare_digest(
        record.browser_nonce_sha256,
        _sha256(browser_nonce),
    )
    if not actor_binding_matches:
        _end_locked_session(
            session,
            record,
            reason="session_binding_changed",
            at=current_time,
            event_type="stale_cleared",
        )
        session.commit()
        return ViewAsResolution(None, True, unsafe_request)
    if record.absolute_expires_at <= current_time:
        _end_locked_session(
            session,
            record,
            reason="expired",
            at=current_time,
            event_type="expired",
        )
        session.commit()
        return ViewAsResolution(None, True, unsafe_request)

    if not _capability_matches(
        session,
        record=record,
        actor=actor,
        identity=verified,
        environment=environment,
        at=current_time,
    ):
        _end_locked_session(
            session,
            record,
            reason="capability_revoked",
            at=current_time,
            event_type="stale_cleared",
        )
        session.commit()
        return ViewAsResolution(None, True, unsafe_request)

    target = _locked_target(session, record.target_doctor_id)
    if target is None:
        # A foreign-key protected target should not disappear. Retain the
        # session so status/exit remains available, but never fall through to
        # an interactive actor or onboarding state.
        return ViewAsResolution(
            active=ResolvedViewAs(
                session=record,
                actor=actor,
                target=None,
                diagnostic_code="target_identity_unready",
            ),
            clear_browser_session=False,
            rejected_unsafe_fallback=False,
        )
    local_diagnostic = target_diagnostic(session, target)
    if live_evidence is None:
        live_diagnostic: ViewAsDiagnosticCode | None = "target_identity_unready"
    elif not hmac.compare_digest(
        live_evidence.target_identity_sha256,
        _target_identity_sha256(target),
    ):
        live_diagnostic = "target_identity_ambiguous"
    else:
        live_diagnostic = live_evidence.diagnostic_code
    return ViewAsResolution(
        active=ResolvedViewAs(
            session=record,
            actor=actor,
            target=target,
            diagnostic_code=local_diagnostic or live_diagnostic,
        ),
        clear_browser_session=False,
        rejected_unsafe_fallback=False,
    )


def end_view_as(
    session: Session,
    *,
    view_as_session_id: str,
    actor: Doctor,
    identity: ViewAsActorIdentity,
    environment: SuperadminCapabilityEnvironment,
    now: datetime | None = None,
) -> bool:
    """End the actor's exact active session; always safe to retry."""

    current_time = _now(now)
    verified = _valid_identity(identity)
    try:
        parsed_id = _parse_session_id(view_as_session_id)
    except ViewAsError:
        return False
    record = session.exec(
        select(ViewAsSession)
        .where(ViewAsSession.id == parsed_id.uuid)
        .with_for_update()
    ).one_or_none()
    if (
        record is None
        or record.environment != environment.value
        or record.actor_doctor_id != actor.id
        or actor.clerk_id != verified.subject
        or not hmac.compare_digest(
            record.actor_issuer_sha256,
            _sha256(verified.issuer),
        )
        or not hmac.compare_digest(
            record.actor_subject_sha256,
            _sha256(verified.subject),
        )
    ):
        return False
    if record.ended_at is None:
        _end_locked_session(
            session,
            record,
            reason="operator_exit",
            at=current_time,
            event_type="ended",
        )
        session.commit()
        return True
    return False
