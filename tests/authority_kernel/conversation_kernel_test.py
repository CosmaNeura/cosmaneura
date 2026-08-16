from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

import app.services.authority_conversation as conversation_service
from app.configuration.authority import AuthorityKernelSettings
from app.services.authority import (
    AuthorityRequestDeniedError,
    ClientTurnConflictError,
    exchange_context_token,
    resolve_mount_context,
)
from app.services.authority_conversation import (
    ConversationInvariantError,
    IdempotencyConflictError,
    TurnPhaseRequiredError,
    write_conversation_turn,
)

import sqlalchemy as sa
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
)
from app.models.authority_conversation import (
    CanonicalConversationFinalPhase,
    CanonicalConversationTurn,
    CanonicalConversationUserPhase,
)
from sqlmodel import Session, select

WORKLOAD_CLIENT_ID = "11111111-1111-4111-8111-111111111111"
ENABLED_SETTINGS = AuthorityKernelSettings(
    enabled=True,
    token_ttl_seconds=900,
    token_refresh_after_seconds=300,
)
ALL_SUPPRESSIONS = [
    "disclaimer",
    "intent_badge",
    "doctrinal_badge",
    "citations",
    "structured_cards",
    "feature_actions",
    "guided_paths",
]


def _provider_context(
    session: Session,
) -> tuple[str, UUID, str, AuthorityTenantMembership]:
    now = datetime.now(UTC)
    actor = AuthorityActor(actor_kind="human", status="active")
    tenant = AuthorityTenant(status="active")
    person = AuthorityPerson(lifecycle_state="patient", status="active")
    session.add_all([actor, tenant, person])
    session.flush()
    membership = AuthorityTenantMembership(
        actor_id=actor.id,
        tenant_id=tenant.id,
        membership_kind="provider",
        status="active",
    )
    session.add_all(
        [
            AuthorityActorExternalIdentity(
                actor_id=actor.id,
                issuer="clerk",
                subject="user_conversation_provider",
                verified_at=now,
            ),
            membership,
            AuthorityPersonOrigin(person_id=person.id, origin="provider_entry"),
            AuthorityPersonBinding(
                person_id=person.id,
                tenant_id=tenant.id,
                binding_ref="record_conversation_provider_001",
                binding_kind="provider_record",
                status="active",
                verified_at=now,
            ),
        ]
    )
    session.commit()
    issued = exchange_context_token(
        clerk_subject="user_conversation_provider",
        requested_surface="provider_assistant",
        record_ref="record_conversation_provider_001",
        session=session,
        settings=ENABLED_SETTINGS,
    )
    client_turn_id = uuid4()
    resolution = resolve_mount_context(
        workload_client_id=WORKLOAD_CLIENT_ID,
        mount_context_token=issued.context_token,
        client_turn_id=client_turn_id,
        session=session,
        settings=ENABLED_SETTINGS,
    )
    return (
        issued.context_token,
        client_turn_id,
        resolution.interaction_ref,
        membership,
    )


def _public_context(session: Session) -> tuple[str, UUID, str]:
    now = datetime.now(UTC)
    actor = AuthorityActor(actor_kind="human", status="active")
    person = AuthorityPerson(lifecycle_state="patient", status="active")
    session.add_all([actor, person])
    session.flush()
    session.add_all(
        [
            AuthorityActorExternalIdentity(
                actor_id=actor.id,
                issuer="clerk",
                subject="user_conversation_public",
                verified_at=now,
            ),
            AuthorityPersonOrigin(person_id=person.id, origin="self_service"),
            AuthorityPersonBinding(
                person_id=person.id,
                actor_id=actor.id,
                binding_ref="record_conversation_public_001",
                binding_kind="identified_session",
                status="active",
                verified_at=now,
            ),
        ]
    )
    session.commit()
    issued = exchange_context_token(
        clerk_subject="user_conversation_public",
        requested_surface="public",
        record_ref="record_conversation_public_001",
        session=session,
        settings=ENABLED_SETTINGS,
    )
    client_turn_id = uuid4()
    resolution = resolve_mount_context(
        workload_client_id=WORKLOAD_CLIENT_ID,
        mount_context_token=issued.context_token,
        client_turn_id=client_turn_id,
        session=session,
        settings=ENABLED_SETTINGS,
    )
    return issued.context_token, client_turn_id, resolution.interaction_ref


def _user_payload(content: str = "Please explain these results exactly.") -> dict:
    return {
        "phase": "user_accepted",
        "ingress_kind": "chat_text",
        "action_code": None,
        "message": {"content_type": "text/plain", "content": content},
    }


def _final_payload(content: str = "I’m sorry you are carrying this.") -> dict:
    return {
        "phase": "final_response",
        "outcome": "completed",
        "response": {"content_type": "text/plain", "content": content},
        "presentation": {
            "mode": "sensitive_disclosure",
            "suppress": ALL_SUPPRESSIONS,
            "human_contact": {
                "label": "Talk with a care-team member",
                "action_id": "care-team-contact",
                "fallback_text": "Please contact your care team when you are ready.",
            },
        },
    }


def _write(
    session: Session,
    *,
    context_token: str,
    client_turn_id: UUID,
    interaction_ref: str,
    idempotency_key: str,
    payload: dict,
):
    return write_conversation_turn(
        workload_client_id=WORKLOAD_CLIENT_ID,
        mount_context_token=context_token,
        interaction_ref=interaction_ref,
        client_turn_id=client_turn_id,
        idempotency_key=idempotency_key,
        payload=payload,
        session=session,
        settings=ENABLED_SETTINGS,
    )


def test_two_phases_persist_exact_text_presentation_and_atomic_audit(
    authority_session: Session,
) -> None:
    token, client_turn_id, interaction_ref, _ = _provider_context(authority_session)

    accepted = _write(
        authority_session,
        context_token=token,
        client_turn_id=client_turn_id,
        interaction_ref=interaction_ref,
        idempotency_key="idem-user-accepted-0001",
        payload=_user_payload(),
    )
    completed = _write(
        authority_session,
        context_token=token,
        client_turn_id=client_turn_id,
        interaction_ref=interaction_ref,
        idempotency_key="idem-final-response-0001",
        payload=_final_payload(),
    )

    turn = authority_session.exec(select(CanonicalConversationTurn)).one()
    user_phase = authority_session.exec(select(CanonicalConversationUserPhase)).one()
    final_phase = authority_session.exec(select(CanonicalConversationFinalPhase)).one()
    conversation_audit = [
        event
        for event in authority_session.exec(select(AuthorityAuditEvent)).all()
        if event.event_type.startswith("canonical_conversation.")
    ]
    idempotency = [
        record
        for record in authority_session.exec(select(AuthorityIdempotencyRecord)).all()
        if record.operation.startswith("conversation_turn.")
    ]

    assert accepted.turn_ref == completed.turn_ref == str(turn.id)
    assert accepted.state == "accepted"
    assert completed.state == "completed"
    assert turn.client_turn_id == client_turn_id
    assert turn.workload_client_id == WORKLOAD_CLIENT_ID
    assert user_phase.content == "Please explain these results exactly."
    assert final_phase.content == "I’m sorry you are carrying this."
    assert final_phase.presentation_mode == "sensitive_disclosure"
    assert all(
        (
            final_phase.suppress_disclaimer,
            final_phase.suppress_intent_badge,
            final_phase.suppress_doctrinal_badge,
            final_phase.suppress_citations,
            final_phase.suppress_structured_cards,
            final_phase.suppress_feature_actions,
            final_phase.suppress_guided_paths,
        )
    )
    assert final_phase.human_contact_action_id == "care-team-contact"
    assert [event.event_type for event in conversation_audit] == [
        "canonical_conversation.user_accepted",
        "canonical_conversation.final_response",
    ]
    assert {event.resource_ref for event in conversation_audit} == {str(turn.id)}
    assert len(idempotency) == 2
    for record in idempotency:
        recorded_at = accepted.recorded_at
        if record.operation == conversation_service.FINAL_RESPONSE_OPERATION:
            recorded_at = completed.recorded_at
        retain_until = record.retain_until
        if retain_until.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=None)
        assert retain_until == recorded_at + timedelta(hours=24)


def test_exact_retries_reuse_receipts_and_changed_phase_conflicts(
    authority_session: Session,
) -> None:
    token, client_turn_id, interaction_ref, _ = _provider_context(authority_session)
    user_key = "idem-user-accepted-0002"
    first = _write(
        authority_session,
        context_token=token,
        client_turn_id=client_turn_id,
        interaction_ref=interaction_ref,
        idempotency_key=user_key,
        payload=_user_payload(),
    )

    replay = _write(
        authority_session,
        context_token=token,
        client_turn_id=client_turn_id,
        interaction_ref=interaction_ref,
        idempotency_key=user_key,
        payload=_user_payload(),
    )
    assert replay.replayed is True
    assert replay.turn_ref == first.turn_ref
    assert replay.recorded_at == first.recorded_at

    with pytest.raises(IdempotencyConflictError):
        _write(
            authority_session,
            context_token=token,
            client_turn_id=client_turn_id,
            interaction_ref=interaction_ref,
            idempotency_key=user_key,
            payload=_user_payload("Changed content under the same key."),
        )
    with pytest.raises(ClientTurnConflictError):
        _write(
            authority_session,
            context_token=token,
            client_turn_id=client_turn_id,
            interaction_ref=interaction_ref,
            idempotency_key="idem-user-accepted-changed-0002",
            payload=_user_payload("Changed content under a new key."),
        )
    with pytest.raises(IdempotencyConflictError):
        _write(
            authority_session,
            context_token=token,
            client_turn_id=client_turn_id,
            interaction_ref=interaction_ref,
            idempotency_key=user_key,
            payload=_final_payload(),
        )

    assert (
        len(authority_session.exec(select(CanonicalConversationUserPhase)).all()) == 1
    )
    assert not authority_session.exec(select(CanonicalConversationFinalPhase)).all()


def test_final_requires_durable_user_phase_and_current_authority(
    authority_session: Session,
) -> None:
    token, client_turn_id, interaction_ref, membership = _provider_context(
        authority_session
    )

    with pytest.raises(TurnPhaseRequiredError):
        _write(
            authority_session,
            context_token=token,
            client_turn_id=client_turn_id,
            interaction_ref=interaction_ref,
            idempotency_key="idem-final-before-user-0003",
            payload=_final_payload(),
        )
    assert not authority_session.exec(select(CanonicalConversationTurn)).all()

    _write(
        authority_session,
        context_token=token,
        client_turn_id=client_turn_id,
        interaction_ref=interaction_ref,
        idempotency_key="idem-user-accepted-0003",
        payload=_user_payload(),
    )
    membership.status = "suspended"
    authority_session.add(membership)
    authority_session.commit()

    with pytest.raises(AuthorityRequestDeniedError):
        _write(
            authority_session,
            context_token=token,
            client_turn_id=client_turn_id,
            interaction_ref=interaction_ref,
            idempotency_key="idem-final-response-0003",
            payload=_final_payload(),
        )
    assert not authority_session.exec(select(CanonicalConversationFinalPhase)).all()


def test_service_boundary_rejects_non_contract_fields_and_unsafe_sensitive_shape(
    authority_session: Session,
) -> None:
    token, client_turn_id, interaction_ref, _ = _provider_context(authority_session)
    user_payload = _user_payload()
    user_payload["browser_history"] = []

    with pytest.raises(ConversationInvariantError):
        _write(
            authority_session,
            context_token=token,
            client_turn_id=client_turn_id,
            interaction_ref=interaction_ref,
            idempotency_key="idem-user-invalid-shape-0004",
            payload=user_payload,
        )

    unsafe_final = _final_payload()
    unsafe_final["presentation"]["suppress"] = ["disclaimer"]
    with pytest.raises(ConversationInvariantError):
        _write(
            authority_session,
            context_token=token,
            client_turn_id=client_turn_id,
            interaction_ref=interaction_ref,
            idempotency_key="idem-final-invalid-shape-0004",
            payload=unsafe_final,
        )

    assert not authority_session.exec(select(CanonicalConversationTurn)).all()


def test_provider_ui_action_requires_current_provider_context(
    authority_session: Session,
) -> None:
    token, client_turn_id, interaction_ref = _public_context(authority_session)
    payload = {
        "phase": "user_accepted",
        "ingress_kind": "provider_ui_action",
        "action_code": "labs.review-requested",
        "message": {
            "content_type": "text/plain",
            "content": "Review the selected lab results.",
        },
    }

    with pytest.raises(AuthorityRequestDeniedError):
        _write(
            authority_session,
            context_token=token,
            client_turn_id=client_turn_id,
            interaction_ref=interaction_ref,
            idempotency_key="idem-provider-action-denied-0005",
            payload=payload,
        )

    assert not authority_session.exec(select(CanonicalConversationTurn)).all()


def test_audit_insert_failure_rolls_back_the_canonical_phase(
    authority_session: Session,
) -> None:
    token, client_turn_id, interaction_ref, _ = _provider_context(authority_session)

    def reject_conversation_audit(
        _: object,
        __: object,
        target: AuthorityAuditEvent,
    ) -> None:
        if target.event_type.startswith("canonical_conversation."):
            raise RuntimeError("synthetic audit write failure")

    sa.event.listen(AuthorityAuditEvent, "before_insert", reject_conversation_audit)
    try:
        with pytest.raises(RuntimeError, match="synthetic audit write failure"):
            _write(
                authority_session,
                context_token=token,
                client_turn_id=client_turn_id,
                interaction_ref=interaction_ref,
                idempotency_key="idem-user-audit-failure-0005",
                payload=_user_payload(),
            )
    finally:
        sa.event.remove(
            AuthorityAuditEvent,
            "before_insert",
            reject_conversation_audit,
        )
        authority_session.rollback()

    assert not authority_session.exec(select(CanonicalConversationTurn)).all()
    assert not [
        event
        for event in authority_session.exec(select(AuthorityAuditEvent)).all()
        if event.event_type.startswith("canonical_conversation.")
    ]


def test_canonical_conversation_records_are_append_only_in_application_code(
    authority_session: Session,
) -> None:
    token, client_turn_id, interaction_ref, _ = _provider_context(authority_session)
    _write(
        authority_session,
        context_token=token,
        client_turn_id=client_turn_id,
        interaction_ref=interaction_ref,
        idempotency_key="idem-user-accepted-0004",
        payload=_user_payload(),
    )
    user_phase = authority_session.exec(select(CanonicalConversationUserPhase)).one()
    user_phase.content = "Attempted replacement"
    authority_session.add(user_phase)

    with pytest.raises(RuntimeError, match="append-only"):
        authority_session.commit()
    authority_session.rollback()
