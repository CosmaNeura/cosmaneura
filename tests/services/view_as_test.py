import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

import app.services.authority as authority_service
import app.services.view_as as view_as_service
from app.configuration.authority import AuthorityKernelSettings
from app.services.authority import AuthorityRequestDeniedError
from app.services.view_as import (
    VIEW_AS_TTL,
    ViewAsActorIdentity,
    ViewAsError,
    begin_view_as,
    context_token_rotation_blocked_by_view_as,
    end_view_as,
    reconcile_view_as_without_browser_binding,
    resolve_view_as,
    target_diagnostic,
)

from activemodel.session_manager import get_session
from app.models.authority import (
    AuthorityActor,
    AuthorityActorExternalIdentity,
    AuthorityPerson,
    AuthorityPersonBinding,
    AuthorityPersonOrigin,
    AuthorityTenant,
    AuthorityTenantMembership,
    MountContextSession,
    MountContextToken,
)
from app.models.doctor import Doctor
from app.models.superadmin_capability import (
    SuperadminCapabilityEnvironment,
    SuperadminCapabilityGrant,
    SuperadminCapabilityKind,
)
from app.models.view_as import ViewAsAuditEvent, ViewAsSession

from tests.routes.utils import provision_practice_in_force

ISSUER = "https://staging.example.clerk.accounts.dev"
AUTHORITY_SETTINGS = AuthorityKernelSettings(
    enabled=True,
    token_ttl_seconds=900,
    token_refresh_after_seconds=300,
    clerk_issuer=ISSUER,
)
WORKLOAD_ID = "11111111-1111-4111-8111-111111111111"


@pytest.fixture(autouse=True)
def _matching_live_clerk_identity(monkeypatch):
    def current_email(clerk_id: str) -> str | None:
        doctor = Doctor.get(clerk_id=clerk_id)
        return doctor.email.strip().lower() if doctor and doctor.email else None

    monkeypatch.setattr(
        "app.services.view_as.verified_primary_email",
        current_email,
    )
    monkeypatch.setattr(
        view_as_service,
        "AUTHORITY_KERNEL_SETTINGS",
        AUTHORITY_SETTINGS,
    )


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


def _mount_token_for_actor(session, actor: Doctor, plaintext_token: str, *, at):
    authority_actor = AuthorityActor(actor_kind="human", status="active")
    person = AuthorityPerson(lifecycle_state="patient", status="active")
    session.add_all([authority_actor, person])
    session.flush()
    source_identity = AuthorityActorExternalIdentity(
        actor_id=authority_actor.id,
        issuer=ISSUER,
        subject=actor.clerk_id,
        verified_at=at,
    )
    binding = AuthorityPersonBinding(
        person_id=person.id,
        actor_id=authority_actor.id,
        binding_ref="record_view_as_guard",
        binding_kind="identified_session",
        status="active",
        verified_at=at,
    )
    session.add_all([source_identity, binding])
    session.flush()
    mount_session = MountContextSession(
        actor_id=authority_actor.id,
        person_binding_id=binding.id,
        source_identity_id=source_identity.id,
        session_kind="authenticated",
        surface_ceiling="public",
        auth_tier_ceiling="identified",
        status="active",
        absolute_expires_at=at + timedelta(hours=1),
    )
    session.add(mount_session)
    session.flush()
    session.add(
        MountContextToken(
            session_id=mount_session.id,
            token_hash=_sha256(plaintext_token),
            issued_at=at,
            refresh_after=at + timedelta(minutes=5),
            expires_at=at + timedelta(minutes=15),
        )
    )
    session.commit()


def _provider_token_for_actor(session, actor: Doctor):
    now = datetime.now(timezone.utc)
    authority_actor = AuthorityActor(actor_kind="human", status="active")
    tenant = AuthorityTenant(status="active")
    person = AuthorityPerson(lifecycle_state="patient", status="active")
    session.add_all([authority_actor, tenant, person])
    session.flush()
    binding_ref = f"record_view_as_{secrets.token_hex(6)}"
    session.add_all(
        [
            AuthorityActorExternalIdentity(
                actor_id=authority_actor.id,
                issuer=ISSUER,
                subject=actor.clerk_id,
                verified_at=now,
            ),
            AuthorityTenantMembership(
                actor_id=authority_actor.id,
                tenant_id=tenant.id,
                membership_kind="provider",
                status="active",
            ),
            AuthorityPersonOrigin(person_id=person.id, origin="provider_entry"),
            AuthorityPersonBinding(
                person_id=person.id,
                tenant_id=tenant.id,
                binding_ref=binding_ref,
                binding_kind="provider_record",
                status="active",
                verified_at=now,
            ),
        ]
    )
    session.commit()
    issued = authority_service.exchange_context_token(
        clerk_issuer=ISSUER,
        clerk_subject=actor.clerk_id,
        requested_surface="provider_assistant",
        record_ref=binding_ref,
        session=session,
        settings=AUTHORITY_SETTINGS,
    )
    client_turn_id = uuid4()
    resolved = authority_service.resolve_mount_context(
        workload_client_id=WORKLOAD_ID,
        mount_context_token=issued.context_token,
        client_turn_id=client_turn_id,
        session=session,
        settings=AUTHORITY_SETTINGS,
    )
    return issued.context_token, client_turn_id, resolved.interaction_ref


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
        assert resolved.active.target is not None
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


def test_begin_revalidates_and_locks_capability_after_actor_lock(monkeypatch):
    now = datetime.now(timezone.utc)
    actor = _doctor("capability-race-actor")
    target = _doctor("capability-race-target")
    grant = _view_as_grant(actor, at=now - timedelta(minutes=1))
    original_get_capability = view_as_service.get_active_superadmin_capability
    lock_modes: list[bool] = []

    def revoke_before_locked_revalidation(
        session,
        *,
        lock_for_update=False,
        **kwargs,
    ):
        lock_modes.append(lock_for_update)
        if lock_for_update:
            current_grant = session.get(SuperadminCapabilityGrant, grant.id)
            assert current_grant is not None
            current_grant.revoked_at = now
            current_grant.revoked_by_ref = "ticket:view-as-test"
            current_grant.revocation_reason_code = "concurrent_test_revocation"
            current_grant.revocation_plan_sha256 = "c" * 64
            current_grant.retain_until = now + timedelta(days=365 * 8)
            session.add(current_grant)
            session.flush()
        return original_get_capability(
            session,
            lock_for_update=lock_for_update,
            **kwargs,
        )

    monkeypatch.setattr(
        view_as_service,
        "get_active_superadmin_capability",
        revoke_before_locked_revalidation,
    )
    with get_session() as session:
        with pytest.raises(ViewAsError, match="capability is required"):
            begin_view_as(
                session,
                actor=actor,
                target_doctor_id=target.id,
                identity=_identity(actor),
                environment=SuperadminCapabilityEnvironment.staging,
                now=now,
            )

    assert lock_modes == [False, True]
    assert ViewAsSession.count() == 0
    [audit] = ViewAsAuditEvent.all()
    assert audit.reason_code == "capability_required"
    assert audit.target_doctor_id is None


def test_selected_target_is_revalidated_against_live_clerk_on_every_resolve(
    monkeypatch,
):
    now = datetime(2026, 8, 24, 16, 0, tzinfo=timezone.utc)
    actor = _doctor("actor")
    target = _doctor("live-target")
    provision_practice_in_force(target)
    _view_as_grant(actor, at=now - timedelta(minutes=1))
    live_lookups: list[str] = []

    def matching_email(clerk_id: str) -> str | None:
        live_lookups.append(clerk_id)
        assert target.email
        return target.email.strip().lower()

    monkeypatch.setattr(
        "app.services.view_as.verified_primary_email",
        matching_email,
    )
    with get_session() as session:
        started = begin_view_as(
            session,
            actor=actor,
            target_doctor_id=target.id,
            identity=_identity(actor),
            environment=SuperadminCapabilityEnvironment.staging,
            now=now,
        )
        assert started.diagnostic_code is None
        assert live_lookups == [target.clerk_id]

        monkeypatch.setattr(
            "app.services.view_as.verified_primary_email",
            lambda _clerk_id: None,
        )
        resolved = resolve_view_as(
            session,
            view_as_session_id=str(started.session.id),
            browser_nonce=started.browser_nonce,
            actor=actor,
            identity=_identity(actor),
            environment=SuperadminCapabilityEnvironment.staging,
            unsafe_request=False,
            now=now + timedelta(minutes=1),
        )

    assert resolved.active is not None
    assert resolved.active.diagnostic_code == "target_identity_unready"


def test_selected_target_live_email_drift_is_ambiguous(monkeypatch):
    now = datetime(2026, 8, 24, 16, 0, tzinfo=timezone.utc)
    actor = _doctor("actor")
    target = _doctor("email-drift")
    provision_practice_in_force(target)
    _view_as_grant(actor, at=now - timedelta(minutes=1))
    monkeypatch.setattr(
        "app.services.view_as.verified_primary_email",
        lambda _clerk_id: "different-primary@example.com",
    )

    with get_session() as session:
        started = begin_view_as(
            session,
            actor=actor,
            target_doctor_id=target.id,
            identity=_identity(actor),
            environment=SuperadminCapabilityEnvironment.staging,
            now=now,
        )

    assert started.diagnostic_code == "target_identity_ambiguous"


def test_selected_target_identity_change_after_live_check_fails_closed(
    monkeypatch,
):
    now = datetime(2026, 8, 24, 16, 0, tzinfo=timezone.utc)
    actor = _doctor("actor")
    target = _doctor("concurrent-drift")
    provision_practice_in_force(target)
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
        assert started.diagnostic_code is None
        original_locked_target = view_as_service._locked_target

        def identity_changed_after_live_check(database_session, target_doctor_id):
            locked_target = original_locked_target(
                database_session,
                target_doctor_id,
            )
            assert locked_target is not None
            locked_target.email = "changed-after-live-check@example.com"
            return locked_target

        monkeypatch.setattr(
            "app.services.view_as._locked_target",
            identity_changed_after_live_check,
        )
        resolved = resolve_view_as(
            session,
            view_as_session_id=str(started.session.id),
            browser_nonce=started.browser_nonce,
            actor=actor,
            identity=_identity(actor),
            environment=SuperadminCapabilityEnvironment.staging,
            unsafe_request=False,
            now=now + timedelta(minutes=1),
        )

    assert resolved.active is not None
    assert resolved.active.diagnostic_code == "target_identity_ambiguous"


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


def test_missing_browser_binding_repeatedly_blocks_unsafe_until_safe_recovery():
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

        for offset in (1, 2):
            blocked = reconcile_view_as_without_browser_binding(
                session,
                actor=actor,
                identity=_identity(actor),
                environment=SuperadminCapabilityEnvironment.staging,
                unsafe_request=True,
                now=now + timedelta(minutes=offset),
            )
            assert blocked.rejected_unsafe_fallback is True
            session.refresh(started.session)
            assert started.session.ended_at is None

        recovered = reconcile_view_as_without_browser_binding(
            session,
            actor=actor,
            identity=_identity(actor),
            environment=SuperadminCapabilityEnvironment.staging,
            unsafe_request=False,
            now=now + timedelta(minutes=3),
        )
        assert recovered.rejected_unsafe_fallback is False
        assert recovered.clear_browser_session is True
        session.refresh(started.session)
        assert started.session.end_reason == "session_binding_changed"


def test_context_token_rotation_uses_server_side_source_identity_guard():
    now = datetime(2026, 8, 24, 16, 0, tzinfo=timezone.utc)
    actor = _doctor("actor")
    target = _doctor("target")
    _view_as_grant(actor, at=now - timedelta(minutes=1))
    plaintext_token = "mctx_server_side_view_as_guard_token"

    with get_session() as session:
        begin_view_as(
            session,
            actor=actor,
            target_doctor_id=target.id,
            identity=_identity(actor),
            environment=SuperadminCapabilityEnvironment.staging,
            now=now,
        )
        _mount_token_for_actor(session, actor, plaintext_token, at=now)

        assert context_token_rotation_blocked_by_view_as(
            session,
            current_token=plaintext_token,
            environment=SuperadminCapabilityEnvironment.staging,
            now=now + timedelta(minutes=1),
        )


def test_context_token_rotation_reconciles_expired_view_as_before_retry():
    now = datetime(2026, 8, 24, 16, 0, tzinfo=timezone.utc)
    actor = _doctor("rotation-expired-actor")
    target = _doctor("rotation-expired-target")
    _view_as_grant(actor, at=now - VIEW_AS_TTL - timedelta(minutes=2))
    plaintext_token = "mctx_expired_view_as_guard_token"

    with get_session() as session:
        started = begin_view_as(
            session,
            actor=actor,
            target_doctor_id=target.id,
            identity=_identity(actor),
            environment=SuperadminCapabilityEnvironment.staging,
            now=now - VIEW_AS_TTL - timedelta(minutes=1),
        )
        _mount_token_for_actor(session, actor, plaintext_token, at=now)

        assert context_token_rotation_blocked_by_view_as(
            session,
            current_token=plaintext_token,
            environment=SuperadminCapabilityEnvironment.staging,
            now=now,
        )
        session.refresh(started.session)
        assert started.session.end_reason == "expired"

        assert not context_token_rotation_blocked_by_view_as(
            session,
            current_token=plaintext_token,
            environment=SuperadminCapabilityEnvironment.staging,
            now=now + timedelta(seconds=1),
        )


def test_context_token_rotation_reconciles_revoked_capability_before_retry():
    now = datetime(2026, 8, 24, 16, 0, tzinfo=timezone.utc)
    actor = _doctor("rotation-revoked-actor")
    target = _doctor("rotation-revoked-target")
    grant = _view_as_grant(actor, at=now - timedelta(minutes=2))
    plaintext_token = "mctx_revoked_view_as_guard_token"

    with get_session() as session:
        started = begin_view_as(
            session,
            actor=actor,
            target_doctor_id=target.id,
            identity=_identity(actor),
            environment=SuperadminCapabilityEnvironment.staging,
            now=now - timedelta(minutes=1),
        )
        _mount_token_for_actor(session, actor, plaintext_token, at=now)
        locked_grant = session.get(SuperadminCapabilityGrant, grant.id)
        assert locked_grant is not None
        locked_grant.revoked_at = now
        locked_grant.revoked_by_ref = "ticket:view-as-test"
        locked_grant.revocation_reason_code = "reviewed_test_revocation"
        locked_grant.revocation_plan_sha256 = "c" * 64
        locked_grant.retain_until = now + timedelta(days=365 * 8)
        session.add(locked_grant)
        session.commit()

        assert context_token_rotation_blocked_by_view_as(
            session,
            current_token=plaintext_token,
            environment=SuperadminCapabilityEnvironment.staging,
            now=now + timedelta(minutes=1),
        )
        session.refresh(started.session)
        assert started.session.end_reason == "capability_revoked"

        assert not context_token_rotation_blocked_by_view_as(
            session,
            current_token=plaintext_token,
            environment=SuperadminCapabilityEnvironment.staging,
            now=now + timedelta(minutes=2),
        )


def test_preissued_agent_token_cannot_read_resolve_rotate_or_write_during_view_as():
    now = datetime.now(timezone.utc)
    actor = _doctor("preissued-token-actor")
    target = _doctor("preissued-token-target")
    _view_as_grant(actor, at=now - timedelta(minutes=1))

    with get_session() as session:
        token, client_turn_id, interaction_ref = _provider_token_for_actor(
            session,
            actor,
        )
        # The token is valid and usable before support view-as begins.
        assert (
            authority_service.bootstrap_mount_context(
                mount_context_token=token,
                session=session,
                settings=AUTHORITY_SETTINGS,
            ).surface
            == "provider_assistant"
        )

        begin_view_as(
            session,
            actor=actor,
            target_doctor_id=target.id,
            identity=_identity(actor),
            environment=SuperadminCapabilityEnvironment.staging,
            now=now,
        )

        with pytest.raises(AuthorityRequestDeniedError):
            authority_service.bootstrap_mount_context(
                mount_context_token=token,
                session=session,
                settings=AUTHORITY_SETTINGS,
            )
        with pytest.raises(AuthorityRequestDeniedError):
            authority_service.resolve_mount_context(
                workload_client_id=WORKLOAD_ID,
                mount_context_token=token,
                client_turn_id=uuid4(),
                session=session,
                settings=AUTHORITY_SETTINGS,
            )
        with pytest.raises(AuthorityRequestDeniedError):
            authority_service.rotate_context_token(
                current_token=token,
                requested_surface="provider_assistant",
                record_ref=None,
                session=session,
                settings=AUTHORITY_SETTINGS,
            )
        with pytest.raises(AuthorityRequestDeniedError):
            authority_service.authorize_interaction(
                session=session,
                workload_client_id=WORKLOAD_ID,
                mount_context_token=token,
                interaction_ref=interaction_ref,
                client_turn_id=client_turn_id,
                settings=AUTHORITY_SETTINGS,
            )
        with pytest.raises(AuthorityRequestDeniedError):
            authority_service.authorize_observational_reporting(
                session=session,
                workload_client_id=WORKLOAD_ID,
                interaction_ref=interaction_ref,
                mount_context_token=None,
                settings=AUTHORITY_SETTINGS,
            )


def test_missing_target_remains_a_diagnostic_session_instead_of_actor_fallback(
    monkeypatch,
):
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
        monkeypatch.setattr(
            "app.services.view_as._locked_target",
            lambda _session, _target_doctor_id: None,
        )
        resolution = resolve_view_as(
            session,
            view_as_session_id=str(started.session.id),
            browser_nonce=started.browser_nonce,
            actor=actor,
            identity=_identity(actor),
            environment=SuperadminCapabilityEnvironment.staging,
            unsafe_request=False,
            now=now + timedelta(minutes=1),
        )

        assert resolution.active is not None
        assert resolution.active.target is None
        assert resolution.active.diagnostic_code == "target_identity_unready"
        assert resolution.clear_browser_session is False


def test_reconciled_but_inactive_authority_identity_is_diagnostic():
    target = _doctor("target")
    now = datetime.now(timezone.utc)
    with get_session() as session:
        authority_actor = AuthorityActor(actor_kind="human", status="suspended")
        session.add(authority_actor)
        session.flush()
        session.add(
            AuthorityActorExternalIdentity(
                actor_id=authority_actor.id,
                issuer=ISSUER,
                subject=target.clerk_id,
                verified_at=now,
            )
        )
        session.commit()

        assert target_diagnostic(session, target) == "target_access_revoked"


@pytest.mark.parametrize("include_configured_identity", [False, True])
def test_target_authority_identity_wrong_or_mixed_issuer_is_ambiguous(
    include_configured_identity,
):
    target = _doctor("issuer-mismatch-target")
    now = datetime.now(timezone.utc)
    with get_session() as session:
        wrong_actor = AuthorityActor(actor_kind="human", status="active")
        session.add(wrong_actor)
        session.flush()
        session.add(
            AuthorityActorExternalIdentity(
                actor_id=wrong_actor.id,
                issuer="https://unexpected-issuer.example",
                subject=target.clerk_id,
                verified_at=now,
            )
        )
        if include_configured_identity:
            configured_actor = AuthorityActor(actor_kind="human", status="active")
            session.add(configured_actor)
            session.flush()
            session.add(
                AuthorityActorExternalIdentity(
                    actor_id=configured_actor.id,
                    issuer=ISSUER,
                    subject=target.clerk_id,
                    verified_at=now,
                )
            )
        session.commit()

        assert target_diagnostic(session, target) == "target_identity_ambiguous"
