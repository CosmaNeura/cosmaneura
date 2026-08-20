import hashlib
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

import app.services.authority as authority_service
from app.configuration.authority import AuthorityKernelSettings
from app.services.authority import (
    AuthorityRequestDeniedError,
    ClientTurnConflictError,
    ContextTokenExpiredError,
    ContextTokenInvalidError,
    bootstrap_mount_context,
    exchange_context_token,
    provider_record_actor_ref,
    resolve_mount_context,
    rotate_context_token,
)

from app.models.authority import (
    AuthorityActor,
    AuthorityActorExternalIdentity,
    AuthorityAuditEvent,
    AuthorityIdempotencyRecord,
    AuthorityPerson,
    AuthorityPersonBinding,
    AuthorityPersonOrigin,
    AuthorityTenant,
    AuthorityTenantMembership,
    InteractionLineage,
    MountContextSession,
    MountContextToken,
)
from app.models.authority_conversation import (
    CanonicalConversationFinalPhase,
    CanonicalConversationTurn,
    CanonicalConversationUserPhase,
)
from app.models.authority_usage import AgentModelUsageEvent
from sqlmodel import Session, select

TEST_CLERK_ISSUER = "https://test.clerk.invalid"
ENABLED_SETTINGS = AuthorityKernelSettings(
    enabled=True,
    token_ttl_seconds=900,
    token_refresh_after_seconds=300,
    clerk_issuer=TEST_CLERK_ISSUER,
)

_V7_PERSISTED_MODELS = (
    AuthorityTenant,
    AuthorityActor,
    AuthorityPerson,
    AuthorityActorExternalIdentity,
    AuthorityTenantMembership,
    AuthorityPersonOrigin,
    AuthorityPersonBinding,
    MountContextSession,
    MountContextToken,
    InteractionLineage,
    CanonicalConversationTurn,
    CanonicalConversationUserPhase,
    CanonicalConversationFinalPhase,
    AgentModelUsageEvent,
    AuthorityIdempotencyRecord,
    AuthorityAuditEvent,
)


def _v7_row_counts(session: Session) -> dict[str, int]:
    return {
        model.__name__: len(session.exec(select(model)).all())
        for model in _V7_PERSISTED_MODELS
    }


def _provider_authority(
    session: Session,
    *,
    clerk_subject: str = "user_test_provider",
    record_ref: str = "record_provider_001",
) -> tuple[
    AuthorityActor,
    AuthorityTenant,
    AuthorityTenantMembership,
    AuthorityPersonBinding,
]:
    now = datetime.now(UTC)
    actor = AuthorityActor(actor_kind="human", status="active")
    tenant = AuthorityTenant(status="active")
    person = AuthorityPerson(lifecycle_state="patient", status="active")
    session.add_all([actor, tenant, person])
    session.flush()
    identity = AuthorityActorExternalIdentity(
        actor_id=actor.id,
        issuer=TEST_CLERK_ISSUER,
        subject=clerk_subject,
        verified_at=now,
    )
    membership = AuthorityTenantMembership(
        actor_id=actor.id,
        tenant_id=tenant.id,
        membership_kind="provider",
        status="active",
    )
    origin = AuthorityPersonOrigin(person_id=person.id, origin="provider_entry")
    binding = AuthorityPersonBinding(
        person_id=person.id,
        tenant_id=tenant.id,
        binding_ref=record_ref,
        binding_kind="provider_record",
        status="active",
        verified_at=now,
    )
    session.add_all([identity, membership, origin, binding])
    session.commit()
    return actor, tenant, membership, binding


def _public_authority(
    session: Session,
    *,
    clerk_subject: str = "user_test_patient",
    record_ref: str = "record_patient_001",
) -> None:
    now = datetime.now(UTC)
    actor = AuthorityActor(actor_kind="human", status="active")
    person = AuthorityPerson(lifecycle_state="patient", status="active")
    session.add_all([actor, person])
    session.flush()
    session.add_all(
        [
            AuthorityActorExternalIdentity(
                actor_id=actor.id,
                issuer=TEST_CLERK_ISSUER,
                subject=clerk_subject,
                verified_at=now,
            ),
            AuthorityPersonOrigin(person_id=person.id, origin="self_service"),
            AuthorityPersonBinding(
                person_id=person.id,
                actor_id=actor.id,
                binding_ref=record_ref,
                binding_kind="identified_session",
                status="active",
                verified_at=now,
            ),
        ]
    )
    session.commit()


def _exchange_provider(
    session: Session,
    *,
    clerk_subject: str = "user_test_provider",
    record_ref: str = "record_provider_001",
):
    return exchange_context_token(
        clerk_issuer=TEST_CLERK_ISSUER,
        clerk_subject=clerk_subject,
        requested_surface="provider_assistant",
        record_ref=record_ref,
        session=session,
        settings=ENABLED_SETTINGS,
    )


def test_provider_exchange_hashes_token_and_resolves_from_current_state(
    authority_session: Session,
) -> None:
    actor, tenant, membership, _ = _provider_authority(authority_session)

    issued = _exchange_provider(authority_session)
    stored = authority_session.exec(select(MountContextToken)).one()

    assert (
        stored.token_hash == hashlib.sha256(issued.context_token.encode()).hexdigest()
    )
    assert stored.token_hash != issued.context_token
    assert issued.refresh_after < issued.expires_at

    resolved = resolve_mount_context(
        workload_client_id="11111111-1111-4111-8111-111111111111",
        mount_context_token=issued.context_token,
        client_turn_id=uuid4(),
        session=authority_session,
        settings=ENABLED_SETTINGS,
    )
    assert resolved.context.public_body() == {
        "surface": "provider_assistant",
        "auth_tier": "provider",
        "capabilities": [
            "education:answer",
            "ethics:consult",
            "plan-workbench:run",
            "referrals:search",
            "visit-prep:run",
        ],
        "tenant_id": str(tenant.id),
        "actor_id": str(actor.id),
    }
    assert resolved.context.capabilities == tuple(
        sorted(
            {
                "education:answer",
                "referrals:search",
                "ethics:consult",
                "visit-prep:run",
                "plan-workbench:run",
            }
        )
    )

    membership.status = "suspended"
    authority_session.add(membership)
    authority_session.commit()
    with pytest.raises(AuthorityRequestDeniedError):
        resolve_mount_context(
            workload_client_id="11111111-1111-4111-8111-111111111111",
            mount_context_token=issued.context_token,
            client_turn_id=uuid4(),
            session=authority_session,
            settings=ENABLED_SETTINGS,
        )


def test_provider_bootstrap_is_read_only_and_does_not_open_lineage(
    authority_session: Session,
) -> None:
    actor, tenant, _membership, _binding = _provider_authority(authority_session)
    issued = _exchange_provider(authority_session)
    before = _v7_row_counts(authority_session)

    context = bootstrap_mount_context(
        mount_context_token=issued.context_token,
        session=authority_session,
        settings=ENABLED_SETTINGS,
    )

    assert context.public_body() == {
        "surface": "provider_assistant",
        "auth_tier": "provider",
        "capabilities": [
            "education:answer",
            "ethics:consult",
            "plan-workbench:run",
            "referrals:search",
            "visit-prep:run",
        ],
        "tenant_id": str(tenant.id),
        "actor_id": str(actor.id),
    }
    assert _v7_row_counts(authority_session) == before
    assert not authority_session.new
    assert not authority_session.dirty
    assert not authority_session.deleted


def test_bootstrap_contract_is_mount_generic_and_public_resolution_is_read_only(
    authority_session: Session,
) -> None:
    _public_authority(authority_session)
    issued = exchange_context_token(
        clerk_issuer=TEST_CLERK_ISSUER,
        clerk_subject="user_test_patient",
        requested_surface="public",
        record_ref="record_patient_001",
        session=authority_session,
        settings=ENABLED_SETTINGS,
    )
    before = _v7_row_counts(authority_session)

    context = bootstrap_mount_context(
        mount_context_token=issued.context_token,
        session=authority_session,
        settings=ENABLED_SETTINGS,
    )

    assert context.surface == "public"
    assert context.auth_tier == "identified"
    assert _v7_row_counts(authority_session) == before
    assert not authority_session.new
    assert not authority_session.dirty
    assert not authority_session.deleted


def test_provider_record_grant_actor_is_derived_from_current_authority(
    authority_session: Session,
) -> None:
    actor, _tenant, membership, binding = _provider_authority(authority_session)

    assert provider_record_actor_ref(
        clerk_issuer=TEST_CLERK_ISSUER,
        clerk_subject="user_test_provider",
        record_ref=binding.binding_ref,
        session=authority_session,
        settings=ENABLED_SETTINGS,
    ) == str(actor.id)

    membership.status = "revoked"
    authority_session.add(membership)
    authority_session.commit()
    with pytest.raises(AuthorityRequestDeniedError):
        provider_record_actor_ref(
            clerk_issuer=TEST_CLERK_ISSUER,
            clerk_subject="user_test_provider",
            record_ref=binding.binding_ref,
            session=authority_session,
            settings=ENABLED_SETTINGS,
        )


def test_same_subject_from_another_clerk_issuer_cannot_cross_bind(
    authority_session: Session,
) -> None:
    actor, _tenant, _membership, binding = _provider_authority(authority_session)
    other_actor = AuthorityActor(actor_kind="human", status="active")
    authority_session.add(other_actor)
    authority_session.flush()
    authority_session.add(
        AuthorityActorExternalIdentity(
            actor_id=other_actor.id,
            issuer="https://other.clerk.invalid",
            subject="user_test_provider",
            verified_at=datetime.now(UTC),
        )
    )
    authority_session.commit()

    with pytest.raises(AuthorityRequestDeniedError):
        exchange_context_token(
            clerk_issuer="https://other.clerk.invalid",
            clerk_subject="user_test_provider",
            requested_surface="provider_assistant",
            record_ref=binding.binding_ref,
            session=authority_session,
            settings=ENABLED_SETTINGS,
        )
    issued = exchange_context_token(
        clerk_issuer=TEST_CLERK_ISSUER,
        clerk_subject="user_test_provider",
        requested_surface="provider_assistant",
        record_ref=binding.binding_ref,
        session=authority_session,
        settings=ENABLED_SETTINGS,
    )
    assert issued.context_token
    assert actor.id != other_actor.id


def test_service_reuses_the_request_managed_database_session(
    authority_session: Session,
    monkeypatch,
) -> None:
    _provider_authority(authority_session)

    @contextmanager
    def managed_session():
        yield authority_session

    monkeypatch.setattr(authority_service, "get_session", managed_session)
    issued = exchange_context_token(
        clerk_issuer=TEST_CLERK_ISSUER,
        clerk_subject="user_test_provider",
        requested_surface="provider_assistant",
        record_ref="record_provider_001",
        settings=ENABLED_SETTINGS,
    )

    resolved = resolve_mount_context(
        workload_client_id="11111111-1111-4111-8111-111111111111",
        mount_context_token=issued.context_token,
        client_turn_id=uuid4(),
        settings=ENABLED_SETTINGS,
    )

    assert resolved.context.surface == "provider_assistant"


def test_surface_and_record_hints_cannot_create_provider_authority(
    authority_session: Session,
) -> None:
    _, _, membership, binding = _provider_authority(authority_session)
    membership.status = "revoked"
    authority_session.add(membership)
    authority_session.commit()

    with pytest.raises(AuthorityRequestDeniedError):
        exchange_context_token(
            clerk_issuer=TEST_CLERK_ISSUER,
            clerk_subject="user_test_provider",
            requested_surface="provider_assistant",
            record_ref=binding.binding_ref,
            session=authority_session,
            settings=ENABLED_SETTINGS,
        )


def test_authenticated_actor_without_verified_person_binding_is_denied(
    authority_session: Session,
) -> None:
    actor = AuthorityActor(actor_kind="human", status="active")
    authority_session.add(actor)
    authority_session.flush()
    authority_session.add(
        AuthorityActorExternalIdentity(
            actor_id=actor.id,
            issuer=TEST_CLERK_ISSUER,
            subject="user_test_unbound",
            verified_at=datetime.now(UTC),
        )
    )
    authority_session.commit()

    with pytest.raises(AuthorityRequestDeniedError):
        exchange_context_token(
            clerk_issuer=TEST_CLERK_ISSUER,
            clerk_subject="user_test_unbound",
            requested_surface="public",
            record_ref=None,
            session=authority_session,
            settings=ENABLED_SETTINGS,
        )


def test_mount_session_has_an_absolute_expiry(
    authority_session: Session,
) -> None:
    _provider_authority(authority_session)
    issued = _exchange_provider(authority_session)
    mount_session = authority_session.exec(select(MountContextSession)).one()
    mount_session.absolute_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    authority_session.add(mount_session)
    authority_session.commit()

    with pytest.raises(ContextTokenExpiredError):
        resolve_mount_context(
            workload_client_id="11111111-1111-4111-8111-111111111111",
            mount_context_token=issued.context_token,
            client_turn_id=uuid4(),
            session=authority_session,
            settings=ENABLED_SETTINGS,
        )


def test_source_identity_revocation_invalidates_existing_tokens(
    authority_session: Session,
) -> None:
    _provider_authority(authority_session)
    issued = _exchange_provider(authority_session)
    identity = authority_session.exec(select(AuthorityActorExternalIdentity)).one()
    identity.revoked_at = datetime.now(UTC)
    authority_session.add(identity)
    authority_session.commit()

    with pytest.raises(AuthorityRequestDeniedError):
        resolve_mount_context(
            workload_client_id="11111111-1111-4111-8111-111111111111",
            mount_context_token=issued.context_token,
            client_turn_id=uuid4(),
            session=authority_session,
            settings=ENABLED_SETTINGS,
        )
    with pytest.raises(AuthorityRequestDeniedError):
        rotate_context_token(
            current_token=issued.context_token,
            requested_surface="provider_assistant",
            record_ref="record_provider_001",
            session=authority_session,
            settings=ENABLED_SETTINGS,
        )


def test_rotation_revokes_old_token_and_cannot_change_record_binding(
    authority_session: Session,
) -> None:
    _provider_authority(authority_session)
    issued = _exchange_provider(authority_session)

    rotated = rotate_context_token(
        current_token=issued.context_token,
        requested_surface="provider_assistant",
        record_ref="record_provider_001",
        session=authority_session,
        settings=ENABLED_SETTINGS,
    )

    assert rotated.context_token != issued.context_token
    with pytest.raises(ContextTokenInvalidError):
        resolve_mount_context(
            workload_client_id="11111111-1111-4111-8111-111111111111",
            mount_context_token=issued.context_token,
            client_turn_id=uuid4(),
            session=authority_session,
            settings=ENABLED_SETTINGS,
        )
    with pytest.raises(AuthorityRequestDeniedError):
        rotate_context_token(
            current_token=rotated.context_token,
            requested_surface="provider_assistant",
            record_ref="record_different_002",
            session=authority_session,
            settings=ENABLED_SETTINGS,
        )


def test_existing_session_and_rotation_cannot_inherit_a_later_tier_elevation(
    authority_session: Session,
) -> None:
    now = datetime.now(UTC)
    actor = AuthorityActor(actor_kind="human", status="active")
    person = AuthorityPerson(lifecycle_state="prospect", status="active")
    authority_session.add_all([actor, person])
    authority_session.flush()
    authority_session.add_all(
        [
            AuthorityActorExternalIdentity(
                actor_id=actor.id,
                issuer=TEST_CLERK_ISSUER,
                subject="user_test_patient",
                verified_at=now,
            ),
            AuthorityPersonOrigin(person_id=person.id, origin="self_service"),
            AuthorityPersonBinding(
                person_id=person.id,
                actor_id=actor.id,
                binding_ref="record_patient_001",
                binding_kind="identified_session",
                status="active",
                verified_at=now,
            ),
        ]
    )
    authority_session.commit()
    issued = exchange_context_token(
        clerk_issuer=TEST_CLERK_ISSUER,
        clerk_subject="user_test_patient",
        requested_surface="public",
        record_ref="record_patient_001",
        session=authority_session,
        settings=ENABLED_SETTINGS,
    )

    person.lifecycle_state = "enrolled"
    authority_session.add(person)
    authority_session.commit()
    current = resolve_mount_context(
        workload_client_id="11111111-1111-4111-8111-111111111111",
        mount_context_token=issued.context_token,
        client_turn_id=uuid4(),
        session=authority_session,
        settings=ENABLED_SETTINGS,
    )
    rotated = rotate_context_token(
        current_token=issued.context_token,
        requested_surface="public",
        record_ref="record_patient_001",
        session=authority_session,
        settings=ENABLED_SETTINGS,
    )
    after_rotation = resolve_mount_context(
        workload_client_id="11111111-1111-4111-8111-111111111111",
        mount_context_token=rotated.context_token,
        client_turn_id=uuid4(),
        session=authority_session,
        settings=ENABLED_SETTINGS,
    )
    fresh_exchange = exchange_context_token(
        clerk_issuer=TEST_CLERK_ISSUER,
        clerk_subject="user_test_patient",
        requested_surface="public",
        record_ref="record_patient_001",
        session=authority_session,
        settings=ENABLED_SETTINGS,
    )
    fresh_context = resolve_mount_context(
        workload_client_id="11111111-1111-4111-8111-111111111111",
        mount_context_token=fresh_exchange.context_token,
        client_turn_id=uuid4(),
        session=authority_session,
        settings=ENABLED_SETTINGS,
    )

    assert current.context.auth_tier == "prospect"
    assert after_rotation.context.auth_tier == "prospect"
    assert fresh_context.context.auth_tier == "enrolled"


def test_exact_client_turn_retry_reuses_lineage_and_mismatch_conflicts(
    authority_session: Session,
) -> None:
    _provider_authority(authority_session)
    issued = _exchange_provider(authority_session)
    workload = "11111111-1111-4111-8111-111111111111"
    client_turn_id = uuid4()

    first = resolve_mount_context(
        workload_client_id=workload,
        mount_context_token=issued.context_token,
        client_turn_id=client_turn_id,
        session=authority_session,
        settings=ENABLED_SETTINGS,
    )
    replay = resolve_mount_context(
        workload_client_id=workload,
        mount_context_token=issued.context_token,
        client_turn_id=client_turn_id,
        session=authority_session,
        settings=ENABLED_SETTINGS,
    )

    assert first.reused is False
    assert replay.reused is True
    assert replay.interaction_ref == first.interaction_ref
    assert len(authority_session.exec(select(InteractionLineage)).all()) == 1

    rotated = rotate_context_token(
        current_token=issued.context_token,
        requested_surface="provider_assistant",
        record_ref="record_provider_001",
        session=authority_session,
        settings=ENABLED_SETTINGS,
    )
    replay_after_rotation = resolve_mount_context(
        workload_client_id=workload,
        mount_context_token=rotated.context_token,
        client_turn_id=client_turn_id,
        session=authority_session,
        settings=ENABLED_SETTINGS,
    )

    assert replay_after_rotation.reused is True
    assert replay_after_rotation.interaction_ref == first.interaction_ref
    assert len(authority_session.exec(select(InteractionLineage)).all()) == 1

    with pytest.raises(ClientTurnConflictError):
        resolve_mount_context(
            workload_client_id="22222222-2222-4222-8222-222222222222",
            mount_context_token=rotated.context_token,
            client_turn_id=client_turn_id,
            session=authority_session,
            settings=ENABLED_SETTINGS,
        )

    second_session_token = _exchange_provider(authority_session)
    with pytest.raises(ClientTurnConflictError):
        resolve_mount_context(
            workload_client_id=workload,
            mount_context_token=second_session_token.context_token,
            client_turn_id=client_turn_id,
            session=authority_session,
            settings=ENABLED_SETTINGS,
        )

    audit_events = authority_session.exec(select(AuthorityAuditEvent)).all()
    assert [event.event_type for event in audit_events].count(
        "interaction_lineage.opened"
    ) == 1


def test_authoritative_audit_events_are_append_only_in_application_code(
    authority_session: Session,
) -> None:
    _provider_authority(authority_session)
    _exchange_provider(authority_session)
    event = authority_session.exec(select(AuthorityAuditEvent)).first()
    assert event is not None

    event.outcome = "changed"
    authority_session.add(event)
    with pytest.raises(RuntimeError, match="append-only"):
        authority_session.commit()
    authority_session.rollback()
