import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

import app.services.authority as authority
import app.services.authority_audit as audit_service
import app.services.authority_conversation as conversation_service
import app.services.authority_reconciliation as reconciliation_service
import app.services.authority_usage as usage_service
from app.configuration.authority import AuthorityKernelSettings

from app.models.authority import (
    AuthorityActor,
    AuthorityActorExternalIdentity,
    AuthorityIdempotencyRecord,
    AuthorityPerson,
    AuthorityPersonBinding,
    AuthorityPersonOrigin,
    AuthorityTenant,
    AuthorityTenantMembership,
    InteractionLineage,
)
from app.models.authority_usage import AgentModelUsageEvent
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

TEST_CLERK_ISSUER = "https://test.clerk.invalid"
ENABLED_SETTINGS = AuthorityKernelSettings(
    enabled=True,
    token_ttl_seconds=900,
    token_refresh_after_seconds=300,
    clerk_issuer=TEST_CLERK_ISSUER,
)
WORKLOAD_ID = "11111111-1111-4111-8111-111111111111"


def _provider_lineage(
    session: Session,
) -> tuple[str, str, AuthorityActor, AuthorityTenant, AuthorityPerson]:
    now = datetime.now(UTC)
    suffix = uuid4().hex
    clerk_subject = f"user_usage_provider_{suffix}"
    binding_ref = f"appointment_usage_{suffix}"
    actor = AuthorityActor(actor_kind="human", status="active")
    tenant = AuthorityTenant(status="active")
    person = AuthorityPerson(lifecycle_state="patient", status="active")
    session.add_all([actor, tenant, person])
    session.flush()
    session.add_all(
        [
            AuthorityActorExternalIdentity(
                actor_id=actor.id,
                issuer=TEST_CLERK_ISSUER,
                subject=clerk_subject,
                verified_at=now,
            ),
            AuthorityTenantMembership(
                actor_id=actor.id,
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
    issued = authority.exchange_context_token(
        clerk_issuer=TEST_CLERK_ISSUER,
        clerk_subject=clerk_subject,
        requested_surface="provider_assistant",
        record_ref=binding_ref,
        session=session,
        settings=ENABLED_SETTINGS,
    )
    resolved = authority.resolve_mount_context(
        workload_client_id=WORKLOAD_ID,
        mount_context_token=issued.context_token,
        client_turn_id=uuid4(),
        session=session,
        settings=ENABLED_SETTINGS,
    )
    return issued.context_token, resolved.interaction_ref, actor, tenant, person


def _usage_payload(*, sequence: int = 1) -> dict[str, object]:
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return {
        "client_event_id": str(uuid4()),
        "call_kind": "reasoning",
        "call_sequence": sequence,
        "workstream": "provider.chat",
        "provider": "azure_openai",
        "provider_request_id": "req_synthetic_001",
        "model": "synthetic-model",
        "deployment": "synthetic-deployment",
        "purpose": "response.drafting",
        "started_at": now,
        "completed_at": now,
        "latency_ms": 12,
        "status": "succeeded",
        "usage_availability": "complete",
        "usage_source": "provider_response",
        "token_usage": {
            "input_tokens": 10,
            "output_tokens": 20,
            "cached_input_tokens": 0,
            "reasoning_tokens": None,
        },
    }


def _lineage(session: Session, interaction_ref: str) -> InteractionLineage:
    return session.exec(
        select(InteractionLineage).where(
            InteractionLineage.interaction_ref == interaction_ref
        )
    ).one()


def _persist_user_phase(
    session: Session,
    *,
    token: str,
    interaction_ref: str,
) -> str:
    lineage = _lineage(session, interaction_ref)
    result = conversation_service.write_conversation_turn(
        workload_client_id=WORKLOAD_ID,
        mount_context_token=token,
        interaction_ref=interaction_ref,
        client_turn_id=lineage.client_turn_id,
        idempotency_key="opaque-user-phase-key-0001",
        payload={
            "phase": "user_accepted",
            "ingress_kind": "chat_text",
            "action_code": None,
            "message": {
                "content_type": "text/plain",
                "content": "Synthetic user request",
            },
        },
        session=session,
        settings=ENABLED_SETTINGS,
    )
    return result.turn_ref


def _persist_final_phase(
    session: Session,
    *,
    token: str,
    interaction_ref: str,
    content: str = "Synthetic final response",
    outcome: str = "completed",
) -> tuple[str, str]:
    lineage = _lineage(session, interaction_ref)
    result = conversation_service.write_conversation_turn(
        workload_client_id=WORKLOAD_ID,
        mount_context_token=token,
        interaction_ref=interaction_ref,
        client_turn_id=lineage.client_turn_id,
        idempotency_key="opaque-final-phase-key-001",
        payload={
            "phase": "final_response",
            "outcome": outcome,
            "response": {
                "content_type": "text/plain",
                "content": content,
            },
            "presentation": {
                "mode": "standard",
                "suppress": [],
                "human_contact": None,
            },
        },
        session=session,
        settings=ENABLED_SETTINGS,
    )
    return result.turn_ref, hashlib.sha256(content.encode()).hexdigest()


def _report(
    session: Session,
    *,
    interaction_ref: str,
    payload: dict[str, object],
    token: str | None,
    key: str = "opaque-usage-key-0001",
) -> usage_service.ModelUsageWriteResult:
    return usage_service.report_model_usage_event(
        workload_client_id=WORKLOAD_ID,
        interaction_ref=interaction_ref,
        idempotency_key=key,
        payload=payload,
        mount_context_token=token,
        session=session,
        settings=ENABLED_SETTINGS,
    )


def _terminal_payload(
    person: AuthorityPerson,
    usage_refs: list[str],
    *,
    turn_ref: str | None,
    content_sha256: str,
    event_type: str = "sovereign.turn.completed",
    observation_status: str = "completed",
) -> dict[str, object]:
    references = [{"kind": "person", "ref": str(person.id)}]
    if turn_ref is not None:
        references.append({"kind": "conversation_turn", "ref": turn_ref})
    references.extend({"kind": "model_usage", "ref": ref} for ref in usage_refs)
    return {
        "client_event_id": str(uuid4()),
        "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "event_type": event_type,
        "observation_status": observation_status,
        "route": "concierge",
        "intent": "care.navigation",
        "content_sha256": content_sha256,
        "platform_refs": references,
    }


def test_usage_is_authoritatively_enriched_and_exactly_replayed(
    authority_session: Session,
) -> None:
    token, interaction_ref, actor, tenant, person = _provider_lineage(
        authority_session
    )
    _persist_user_phase(
        authority_session,
        token=token,
        interaction_ref=interaction_ref,
    )
    payload = _usage_payload()
    first = _report(
        authority_session,
        interaction_ref=interaction_ref,
        payload=payload,
        token=token,
    )
    replay = _report(
        authority_session,
        interaction_ref=interaction_ref,
        payload=payload,
        token=None,
    )

    assert first.replayed is False
    assert replay.replayed is True
    assert replay.record_ref == first.record_ref
    event = authority_session.exec(select(AgentModelUsageEvent)).one()
    assert event.actor_id == actor.id
    assert event.tenant_id == tenant.id
    assert event.person_id == person.id
    assert event.surface == "provider_assistant"
    assert event.auth_tier == "provider"
    assert len(event.request_digest) == 64
    lineage = authority_session.exec(select(InteractionLineage)).one()
    assert lineage.audit_status == "pending"
    assert lineage.usage_status == "pending"


def test_usage_changed_replays_and_duplicate_sequences_conflict(
    authority_session: Session,
) -> None:
    token, interaction_ref, _, _, _ = _provider_lineage(authority_session)
    _persist_user_phase(
        authority_session,
        token=token,
        interaction_ref=interaction_ref,
    )
    payload = _usage_payload()
    _report(
        authority_session,
        interaction_ref=interaction_ref,
        payload=payload,
        token=token,
    )

    with pytest.raises(usage_service.ModelUsageIdempotencyConflictError):
        _report(
            authority_session,
            interaction_ref=interaction_ref,
            payload={**payload, "latency_ms": 13},
            token=token,
        )
    with pytest.raises(usage_service.ModelUsageIdempotencyConflictError):
        _report(
            authority_session,
            interaction_ref=interaction_ref,
            payload=payload,
            token=token,
            key="opaque-usage-key-0002",
        )
    with pytest.raises(usage_service.ModelUsageIdempotencyConflictError):
        _report(
            authority_session,
            interaction_ref=interaction_ref,
            payload=_usage_payload(sequence=1),
            token=token,
            key="opaque-usage-key-0003",
        )


def test_usage_requires_user_accepted_and_rejects_new_calls_after_final(
    authority_session: Session,
) -> None:
    token, interaction_ref, _, _, _ = _provider_lineage(authority_session)
    with pytest.raises(usage_service.ModelUsagePhaseRequiredError):
        _report(
            authority_session,
            interaction_ref=interaction_ref,
            payload=_usage_payload(),
            token=token,
        )
    _persist_user_phase(
        authority_session,
        token=token,
        interaction_ref=interaction_ref,
    )
    _persist_final_phase(
        authority_session,
        token=token,
        interaction_ref=interaction_ref,
    )
    with pytest.raises(usage_service.ModelUsageLineageClosedError):
        _report(
            authority_session,
            interaction_ref=interaction_ref,
            payload=_usage_payload(),
            token=token,
        )


def test_nonterminal_audit_stays_pending_then_terminal_closes_both_ledgers(
    authority_session: Session,
) -> None:
    token, interaction_ref, _, _, person = _provider_lineage(authority_session)
    _persist_user_phase(
        authority_session,
        token=token,
        interaction_ref=interaction_ref,
    )
    usage_payload = _usage_payload()
    usage = _report(
        authority_session,
        interaction_ref=interaction_ref,
        payload=usage_payload,
        token=token,
    )
    turn_ref, content_sha256 = _persist_final_phase(
        authority_session,
        token=token,
        interaction_ref=interaction_ref,
    )
    intermediate = _terminal_payload(
        person,
        [],
        turn_ref=turn_ref,
        content_sha256=content_sha256,
    )
    intermediate["event_type"] = "ehr.labs.read.completed"
    intermediate["platform_refs"] = [{"kind": "person", "ref": str(person.id)}]
    audit_service.write_observational_audit_event(
        workload_client_id=WORKLOAD_ID,
        interaction_ref=interaction_ref,
        idempotency_key="opaque-audit-key-usage-01",
        payload=intermediate,
        mount_context_token=token,
        session=authority_session,
        settings=ENABLED_SETTINGS,
    )
    lineage = authority_session.exec(select(InteractionLineage)).one()
    assert lineage.audit_status == "pending"
    assert lineage.usage_status == "pending"

    terminal_payload = _terminal_payload(
        person,
        [usage.record_ref],
        turn_ref=turn_ref,
        content_sha256=content_sha256,
    )
    terminal_result = audit_service.write_observational_audit_event(
        workload_client_id=WORKLOAD_ID,
        interaction_ref=interaction_ref,
        idempotency_key="opaque-audit-key-usage-02",
        payload=terminal_payload,
        mount_context_token=None,
        session=authority_session,
        settings=ENABLED_SETTINGS,
    )
    authority_session.refresh(lineage)
    assert lineage.audit_status == "complete"
    assert lineage.usage_status == "complete"
    assert lineage.completed_at is not None

    terminal_replay = audit_service.write_observational_audit_event(
        workload_client_id=WORKLOAD_ID,
        interaction_ref=interaction_ref,
        idempotency_key="opaque-audit-key-usage-02",
        payload=terminal_payload,
        mount_context_token=None,
        session=authority_session,
        settings=ENABLED_SETTINGS,
    )
    assert terminal_replay.replayed is True
    assert terminal_replay.record_ref == terminal_result.record_ref

    usage_replay = _report(
        authority_session,
        interaction_ref=interaction_ref,
        payload=usage_payload,
        token=None,
    )
    assert usage_replay.replayed is True
    assert usage_replay.record_ref == usage.record_ref

    with pytest.raises(audit_service.AuditLineageClosedError):
        audit_service.write_observational_audit_event(
            workload_client_id=WORKLOAD_ID,
            interaction_ref=interaction_ref,
            idempotency_key="opaque-late-audit-key-001",
            payload={
                "client_event_id": str(uuid4()),
                "observed_at": datetime.now(UTC).isoformat().replace(
                    "+00:00", "Z"
                ),
                "event_type": "orchestration.completed",
                "observation_status": "completed",
                "route": "concierge",
                "intent": None,
                "content_sha256": None,
                "platform_refs": [
                    {"kind": "person", "ref": str(person.id)}
                ],
            },
            mount_context_token=None,
            session=authority_session,
            settings=ENABLED_SETTINGS,
        )

    with pytest.raises(usage_service.ModelUsageLineageClosedError):
        _report(
            authority_session,
            interaction_ref=interaction_ref,
            payload=_usage_payload(sequence=2),
            token=token,
            key="opaque-usage-key-0004",
        )


def test_terminal_allows_zero_model_calls_but_rejects_missing_or_gapped_refs(
    authority_session: Session,
) -> None:
    token, interaction_ref, _, _, person = _provider_lineage(authority_session)
    _persist_user_phase(
        authority_session,
        token=token,
        interaction_ref=interaction_ref,
    )
    turn_ref, content_sha256 = _persist_final_phase(
        authority_session,
        token=token,
        interaction_ref=interaction_ref,
    )
    audit_service.write_observational_audit_event(
        workload_client_id=WORKLOAD_ID,
        interaction_ref=interaction_ref,
        idempotency_key="opaque-audit-zero-model-01",
        payload=_terminal_payload(
            person,
            [],
            turn_ref=turn_ref,
            content_sha256=content_sha256,
        ),
        mount_context_token=token,
        session=authority_session,
        settings=ENABLED_SETTINGS,
    )
    lineage = authority_session.exec(select(InteractionLineage)).one()
    assert lineage.audit_status == lineage.usage_status == "complete"

    second_session = authority_session
    token2, interaction_ref2, _, _, person2 = _provider_lineage(second_session)
    _persist_user_phase(
        second_session,
        token=token2,
        interaction_ref=interaction_ref2,
    )
    usage = _report(
        second_session,
        interaction_ref=interaction_ref2,
        payload=_usage_payload(sequence=2),
        token=token2,
        key="opaque-usage-gap-key-01",
    )
    turn_ref2, content_sha2562 = _persist_final_phase(
        second_session,
        token=token2,
        interaction_ref=interaction_ref2,
    )
    with pytest.raises(audit_service.AuditReferenceInvalidError):
        audit_service.write_observational_audit_event(
            workload_client_id=WORKLOAD_ID,
            interaction_ref=interaction_ref2,
            idempotency_key="opaque-audit-gap-model-01",
            payload=_terminal_payload(
                person2,
                [usage.record_ref],
                turn_ref=turn_ref2,
                content_sha256=content_sha2562,
            ),
            mount_context_token=token2,
            session=second_session,
            settings=ENABLED_SETTINGS,
        )
    lineage2 = second_session.exec(
        select(InteractionLineage).where(
            InteractionLineage.interaction_ref == interaction_ref2
        )
    ).one()
    assert lineage2.audit_status == lineage2.usage_status == "pending"


def test_terminal_rejects_missing_and_cross_interaction_extra_usage_refs(
    authority_session: Session,
) -> None:
    token, interaction_ref, _, _, person = _provider_lineage(authority_session)
    _persist_user_phase(
        authority_session,
        token=token,
        interaction_ref=interaction_ref,
    )
    usage = _report(
        authority_session,
        interaction_ref=interaction_ref,
        payload=_usage_payload(),
        token=token,
    )
    turn_ref, content_sha256 = _persist_final_phase(
        authority_session,
        token=token,
        interaction_ref=interaction_ref,
    )
    with pytest.raises(audit_service.AuditReferenceInvalidError):
        audit_service.write_observational_audit_event(
            workload_client_id=WORKLOAD_ID,
            interaction_ref=interaction_ref,
            idempotency_key="opaque-audit-missing-ref-01",
            payload=_terminal_payload(
                person,
                [],
                turn_ref=turn_ref,
                content_sha256=content_sha256,
            ),
            mount_context_token=token,
            session=authority_session,
            settings=ENABLED_SETTINGS,
        )

    token2, interaction_ref2, _, _, person2 = _provider_lineage(authority_session)
    _persist_user_phase(
        authority_session,
        token=token2,
        interaction_ref=interaction_ref2,
    )
    turn_ref2, content_sha2562 = _persist_final_phase(
        authority_session,
        token=token2,
        interaction_ref=interaction_ref2,
    )
    with pytest.raises(audit_service.AuditReferenceInvalidError):
        audit_service.write_observational_audit_event(
            workload_client_id=WORKLOAD_ID,
            interaction_ref=interaction_ref2,
            idempotency_key="opaque-audit-extra-ref-001",
            payload=_terminal_payload(
                person2,
                [usage.record_ref],
                turn_ref=turn_ref2,
                content_sha256=content_sha2562,
            ),
            mount_context_token=token2,
            session=authority_session,
            settings=ENABLED_SETTINGS,
        )
    lineages = authority_session.exec(select(InteractionLineage)).all()
    assert all(lineage.audit_status == "pending" for lineage in lineages)
    assert all(lineage.usage_status == "pending" for lineage in lineages)


def test_terminal_requires_durable_final_hash_status_and_exact_turn_ref(
    authority_session: Session,
) -> None:
    token, interaction_ref, _, _, person = _provider_lineage(authority_session)
    turn_ref = _persist_user_phase(
        authority_session,
        token=token,
        interaction_ref=interaction_ref,
    )
    with pytest.raises(audit_service.AuditReferenceInvalidError):
        audit_service.write_observational_audit_event(
            workload_client_id=WORKLOAD_ID,
            interaction_ref=interaction_ref,
            idempotency_key="opaque-audit-no-final-001",
            payload=_terminal_payload(
                person,
                [],
                turn_ref=turn_ref,
                content_sha256="a" * 64,
            ),
            mount_context_token=token,
            session=authority_session,
            settings=ENABLED_SETTINGS,
        )

    turn_ref, content_sha256 = _persist_final_phase(
        authority_session,
        token=token,
        interaction_ref=interaction_ref,
    )
    with pytest.raises(audit_service.AuditPayloadInvalidError):
        audit_service.write_observational_audit_event(
            workload_client_id=WORKLOAD_ID,
            interaction_ref=interaction_ref,
            idempotency_key="opaque-audit-wrong-hash-01",
            payload=_terminal_payload(
                person,
                [],
                turn_ref=turn_ref,
                content_sha256="f" * 64,
            ),
            mount_context_token=token,
            session=authority_session,
            settings=ENABLED_SETTINGS,
        )
    with pytest.raises(audit_service.AuditPayloadInvalidError):
        audit_service.write_observational_audit_event(
            workload_client_id=WORKLOAD_ID,
            interaction_ref=interaction_ref,
            idempotency_key="opaque-audit-wrong-status1",
            payload=_terminal_payload(
                person,
                [],
                turn_ref=turn_ref,
                content_sha256=content_sha256,
                event_type="sovereign.turn.refused",
                observation_status="refused",
            ),
            mount_context_token=token,
            session=authority_session,
            settings=ENABLED_SETTINGS,
        )

    token2, interaction_ref2, _, _, _ = _provider_lineage(authority_session)
    _persist_user_phase(
        authority_session,
        token=token2,
        interaction_ref=interaction_ref2,
    )
    other_turn_ref, _ = _persist_final_phase(
        authority_session,
        token=token2,
        interaction_ref=interaction_ref2,
    )
    with pytest.raises(audit_service.AuditReferenceInvalidError):
        audit_service.write_observational_audit_event(
            workload_client_id=WORKLOAD_ID,
            interaction_ref=interaction_ref,
            idempotency_key="opaque-audit-wrong-turn-001",
            payload=_terminal_payload(
                person,
                [],
                turn_ref=other_turn_ref,
                content_sha256=content_sha256,
            ),
            mount_context_token=token,
            session=authority_session,
            settings=ENABLED_SETTINGS,
        )


def test_reconciled_failed_lineage_rejects_late_audit_usage_and_terminal(
    authority_session: Session,
) -> None:
    token, interaction_ref, _, _, person = _provider_lineage(authority_session)
    _persist_user_phase(
        authority_session,
        token=token,
        interaction_ref=interaction_ref,
    )
    lineage = _lineage(authority_session, interaction_ref)
    lineage.created_at = datetime.now(UTC) - timedelta(hours=1)
    authority_session.add(lineage)
    authority_session.commit()
    reconciliation_service.mark_stale_ingestion_failed(
        interaction_ref=interaction_ref,
        component="audit",
        older_than_minutes=15,
        session=authority_session,
    )

    with pytest.raises(usage_service.ModelUsageLineageClosedError):
        _report(
            authority_session,
            interaction_ref=interaction_ref,
            payload=_usage_payload(),
            token=token,
        )
    with pytest.raises(audit_service.AuditLineageClosedError):
        audit_service.write_observational_audit_event(
            workload_client_id=WORKLOAD_ID,
            interaction_ref=interaction_ref,
            idempotency_key="opaque-failed-audit-key-01",
            payload={
                "client_event_id": str(uuid4()),
                "observed_at": datetime.now(UTC).isoformat().replace(
                    "+00:00", "Z"
                ),
                "event_type": "orchestration.completed",
                "observation_status": "completed",
                "route": None,
                "intent": None,
                "content_sha256": None,
                "platform_refs": [
                    {"kind": "person", "ref": str(person.id)}
                ],
            },
            mount_context_token=token,
            session=authority_session,
            settings=ENABLED_SETTINGS,
        )

    turn_ref, content_sha256 = _persist_final_phase(
        authority_session,
        token=token,
        interaction_ref=interaction_ref,
    )
    with pytest.raises(audit_service.AuditLineageClosedError):
        audit_service.write_observational_audit_event(
            workload_client_id=WORKLOAD_ID,
            interaction_ref=interaction_ref,
            idempotency_key="opaque-failed-terminal-001",
            payload=_terminal_payload(
                person,
                [],
                turn_ref=turn_ref,
                content_sha256=content_sha256,
            ),
            mount_context_token=token,
            session=authority_session,
            settings=ENABLED_SETTINGS,
        )

def test_usage_rollback_and_append_only_fence(authority_session: Session, monkeypatch) -> None:
    token, interaction_ref, _, _, _ = _provider_lineage(authority_session)
    _persist_user_phase(
        authority_session,
        token=token,
        interaction_ref=interaction_ref,
    )
    original_commit = authority_session.commit

    def fail_commit() -> None:
        raise OperationalError("COMMIT", {}, RuntimeError("synthetic"))

    monkeypatch.setattr(authority_session, "commit", fail_commit)
    with pytest.raises(OperationalError):
        _report(
            authority_session,
            interaction_ref=interaction_ref,
            payload=_usage_payload(),
            token=token,
        )
    authority_session.rollback()
    monkeypatch.setattr(authority_session, "commit", original_commit)
    assert not authority_session.exec(select(AgentModelUsageEvent)).all()
    assert not authority_session.exec(
        select(AuthorityIdempotencyRecord).where(
            AuthorityIdempotencyRecord.operation
            == usage_service.MODEL_USAGE_OPERATION
        )
    ).all()

    result = _report(
        authority_session,
        interaction_ref=interaction_ref,
        payload=_usage_payload(),
        token=token,
    )
    event = authority_session.get(AgentModelUsageEvent, result.record_ref)
    assert event is not None
    event.provider = "changed"
    authority_session.add(event)
    with pytest.raises(RuntimeError, match="append-only"):
        authority_session.commit()
    authority_session.rollback()
