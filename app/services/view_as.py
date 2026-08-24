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
from typing import Literal, cast

from typeid import TypeID
from typeid.errors import TypeIDException

from app.services.doctor_access import resolve_doctor_access
from app.services.pilot_access_grandfather import revoked_pilot_access_grant
from app.services.superadmin_capability import (
    SuperadminCapabilityEnvironment,
    SuperadminCapabilityError,
    SuperadminCapabilityKind,
    get_active_superadmin_capability,
)

from activemodel.types import TypeIDType
from app.models.doctor import Doctor
from app.models.view_as import ViewAsAuditEvent, ViewAsSession
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

VIEW_AS_TTL = timedelta(minutes=15)
VIEW_AS_SESSION_PREFIX = "view_as_session"

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


@dataclass(frozen=True, slots=True)
class ResolvedViewAs:
    session: ViewAsSession
    actor: Doctor
    target: Doctor
    diagnostic_code: ViewAsDiagnosticCode | None


@dataclass(frozen=True, slots=True)
class ViewAsResolution:
    active: ResolvedViewAs | None
    clear_browser_session: bool
    rejected_unsafe_fallback: bool


def _now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ViewAsError("view-as timestamp must include a timezone")
    return current


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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
    if not target.email or not target.clerk_id:
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
    except SuperadminCapabilityError as exc:
        raise ViewAsError("view-as capability state is invalid") from exc
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
        select(Doctor).where(Doctor.id == actor.id).with_for_update()
    ).one_or_none()
    target = session.exec(
        select(Doctor).where(Doctor.id == target_doctor_id).with_for_update()
    ).one_or_none()
    if locked_actor is None or locked_actor.deleted_at is not None:
        raise ViewAsError("view-as actor identity is unavailable")
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

    prior = _active_actor_session(
        session,
        actor_doctor_id=locked_actor.id,
        environment=environment,
        lock=True,
    )
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
    return ViewAsBeginResult(session=record, browser_nonce=browser_nonce)


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

    actor_binding_matches = all(
        (
            actor.deleted_at is None,
            actor.clerk_id == verified.subject,
            hmac.compare_digest(record.actor_issuer_sha256, _sha256(verified.issuer)),
            hmac.compare_digest(record.actor_subject_sha256, _sha256(verified.subject)),
            hmac.compare_digest(
                record.clerk_session_sha256,
                _sha256(verified.clerk_session_id),
            ),
            hmac.compare_digest(
                record.browser_nonce_sha256,
                _sha256(browser_nonce),
            ),
        )
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

    try:
        capability = get_active_superadmin_capability(
            session,
            issuer=verified.issuer,
            subject=verified.subject,
            doctor_id=actor.id,
            environment=environment,
            capability_kind=SuperadminCapabilityKind.view_as,
            at=current_time,
        )
    except SuperadminCapabilityError:
        capability = None
    if capability is None or capability.id != record.capability_grant_id:
        _end_locked_session(
            session,
            record,
            reason="capability_revoked",
            at=current_time,
            event_type="stale_cleared",
        )
        session.commit()
        return ViewAsResolution(None, True, unsafe_request)

    target = cast(Doctor | None, session.get(Doctor, record.target_doctor_id))
    if target is None:
        # A foreign-key protected target should not disappear. Treat any such
        # state as diagnostic rather than falling back to the actor.
        raise ViewAsError("view-as target state is invalid")
    return ViewAsResolution(
        active=ResolvedViewAs(
            session=record,
            actor=actor,
            target=target,
            diagnostic_code=target_diagnostic(session, target),
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
