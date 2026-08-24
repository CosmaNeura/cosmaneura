import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from app.services.view_as import (
    VIEW_AS_TTL,
    ViewAsActorIdentity,
    begin_view_as,
    end_view_as,
    resolve_view_as,
)

from activemodel.session_manager import get_session
from app.models.doctor import Doctor
from app.models.superadmin_capability import (
    SuperadminCapabilityEnvironment,
    SuperadminCapabilityGrant,
    SuperadminCapabilityKind,
)
from app.models.view_as import ViewAsAuditEvent, ViewAsSession

ISSUER = "https://staging.example.clerk.accounts.dev"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _doctor(label: str) -> Doctor:
    nonce = secrets.token_hex(5)
    return Doctor(
        clerk_id=f"user_view_as_{label}_{nonce}",
        email=f"view-as-{label}-{nonce}@example.com",
        biography="",
    ).save()


def _view_as_grant(actor: Doctor, *, at: datetime) -> SuperadminCapabilityGrant:
    assert actor.email
    return SuperadminCapabilityGrant(
        environment=SuperadminCapabilityEnvironment.staging,
        capability_kind=SuperadminCapabilityKind.view_as,
        clerk_issuer=ISSUER,
        clerk_subject=actor.clerk_id,
        doctor_id=actor.id,
        issuer_sha256=_sha256(ISSUER),
        subject_sha256=_sha256(actor.clerk_id),
        verified_primary_email_sha256=_sha256(actor.email),
        source_ref="controlled:view-as-test",
        source_sha256="a" * 64,
        grant_plan_sha256="b" * 64,
        granted_by_ref="ticket:view-as-test",
        grant_reason_code="reviewed_test_grant",
        granted_at=at,
    ).save()


def _identity(actor: Doctor, *, sid: str = "session_view_as_test"):
    return ViewAsActorIdentity(
        issuer=ISSUER,
        subject=actor.clerk_id,
        clerk_session_id=sid,
    )


def test_begin_resolve_rotate_and_end_are_bound_and_audited():
    now = datetime(2026, 8, 24, 16, 0, tzinfo=timezone.utc)
    actor = _doctor("actor")
    first_target = _doctor("first")
    second_target = _doctor("second")
    _view_as_grant(actor, at=now - timedelta(minutes=1))

    with get_session() as session:
        first = begin_view_as(
            session,
            actor=actor,
            target_doctor_id=first_target.id,
            identity=_identity(actor),
            environment=SuperadminCapabilityEnvironment.staging,
            now=now,
        )
        assert first.session.absolute_expires_at == now + VIEW_AS_TTL
        assert first.browser_nonce not in first.session.browser_nonce_sha256

        resolved = resolve_view_as(
            session,
            view_as_session_id=str(first.session.id),
            browser_nonce=first.browser_nonce,
            actor=actor,
            identity=_identity(actor),
            environment=SuperadminCapabilityEnvironment.staging,
            unsafe_request=False,
            now=now + timedelta(minutes=1),
        )
        assert resolved.active is not None
        assert resolved.active.target.id == first_target.id
        assert resolved.clear_browser_session is False

        second = begin_view_as(
            session,
            actor=actor,
            target_doctor_id=second_target.id,
            identity=_identity(actor),
            environment=SuperadminCapabilityEnvironment.staging,
            now=now + timedelta(minutes=2),
        )
        session.refresh(first.session)
        assert first.session.end_reason == "rotated"
        assert ViewAsSession.count() == 2

        assert end_view_as(
            session,
            view_as_session_id=str(second.session.id),
            actor=actor,
            identity=_identity(actor),
            environment=SuperadminCapabilityEnvironment.staging,
            now=now + timedelta(minutes=3),
        )
        session.refresh(second.session)
        assert second.session.end_reason == "operator_exit"

    assert [event.event_type for event in ViewAsAuditEvent.all()] == [
        "began",
        "ended",
        "began",
        "ended",
    ]


def test_changed_clerk_session_ends_view_as_and_rejects_unsafe_fallback():
    now = datetime(2026, 8, 24, 16, 0, tzinfo=timezone.utc)
    actor = _doctor("actor")
    target = _doctor("target")
    _view_as_grant(actor, at=now - timedelta(minutes=1))

    with get_session() as session:
        started = begin_view_as(
            session,
            actor=actor,
            target_doctor_id=target.id,
            identity=_identity(actor),
            environment=SuperadminCapabilityEnvironment.staging,
            now=now,
        )
        resolution = resolve_view_as(
            session,
            view_as_session_id=str(started.session.id),
            browser_nonce=started.browser_nonce,
            actor=actor,
            identity=_identity(actor, sid="session_reauthenticated"),
            environment=SuperadminCapabilityEnvironment.staging,
            unsafe_request=True,
            now=now + timedelta(minutes=1),
        )
        assert resolution.active is None
        assert resolution.clear_browser_session is True
        assert resolution.rejected_unsafe_fallback is True
        session.refresh(started.session)
        assert started.session.end_reason == "session_binding_changed"


def test_expired_session_never_falls_through_to_an_unsafe_actor_write():
    now = datetime(2026, 8, 24, 16, 0, tzinfo=timezone.utc)
    actor = _doctor("actor")
    target = _doctor("target")
    _view_as_grant(actor, at=now - timedelta(minutes=1))

    with get_session() as session:
        started = begin_view_as(
            session,
            actor=actor,
            target_doctor_id=target.id,
            identity=_identity(actor),
            environment=SuperadminCapabilityEnvironment.staging,
            now=now,
        )
        resolution = resolve_view_as(
            session,
            view_as_session_id=str(started.session.id),
            browser_nonce=started.browser_nonce,
            actor=actor,
            identity=_identity(actor),
            environment=SuperadminCapabilityEnvironment.staging,
            unsafe_request=True,
            now=now + VIEW_AS_TTL,
        )
        assert resolution.active is None
        assert resolution.clear_browser_session is True
        assert resolution.rejected_unsafe_fallback is True
        session.refresh(started.session)
        assert started.session.end_reason == "expired"
