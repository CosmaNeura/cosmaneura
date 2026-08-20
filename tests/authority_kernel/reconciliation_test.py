from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.services.authority_reconciliation import (
    OPERATOR_FINAL_CONTENT,
    OPERATOR_FINAL_CONTENT_SHA256,
    OPERATOR_FINAL_REQUEST_DIGEST,
    IngestionReconciliationStateError,
    incomplete_ingestion_status,
    mark_stale_ingestion_failed,
    reconcile_stale_accepted_turn,
)

from app.models.authority import (
    AuthorityAuditEvent,
    AuthorityIdempotencyRecord,
    InteractionLineage,
    MountContextSession,
)
from app.models.authority_conversation import (
    CanonicalConversationFinalPhase,
    CanonicalConversationTurn,
    CanonicalConversationUserPhase,
)
from sqlmodel import Session, select

WORKLOAD_ID = "11111111-1111-4111-8111-111111111111"


def _lineage(
    session: Session,
    *,
    age_minutes: int,
    audit_status: str = "pending",
    usage_status: str = "pending",
) -> InteractionLineage:
    now = datetime.now(UTC)
    mount = MountContextSession(
        session_kind="anonymous",
        surface_ceiling="public",
        auth_tier_ceiling="anonymous",
        status="active",
        absolute_expires_at=now + timedelta(hours=1),
    )
    session.add(mount)
    session.flush()
    lineage = InteractionLineage(
        workload_client_id=WORKLOAD_ID,
        client_turn_id=uuid4(),
        session_id=mount.id,
        interaction_ref=f"ix_{uuid4().hex}",
        binding_fingerprint="a" * 64,
        audit_status=audit_status,
        usage_status=usage_status,
        created_at=now - timedelta(minutes=age_minutes),
    )
    session.add(lineage)
    session.commit()
    return lineage


def _accepted_turn(
    session: Session,
    *,
    age_minutes: int,
) -> CanonicalConversationTurn:
    lineage = _lineage(session, age_minutes=age_minutes)
    recorded_at = datetime.now(UTC) - timedelta(minutes=age_minutes)
    turn = CanonicalConversationTurn(
        interaction_id=lineage.id,
        workload_client_id=lineage.workload_client_id,
        client_turn_id=lineage.client_turn_id,
        created_at=recorded_at,
    )
    session.add(turn)
    session.flush()
    session.add(
        CanonicalConversationUserPhase(
            turn_id=turn.id,
            ingress_kind="chat_text",
            content_type="text/plain",
            content="Synthetic accepted test message.",
            request_digest="a" * 64,
            recorded_at=recorded_at,
        )
    )
    session.commit()
    return turn


def test_status_report_exposes_only_counts_states_and_opaque_refs(
    authority_session: Session,
) -> None:
    stale = _lineage(authority_session, age_minutes=60)
    failed = _lineage(
        authority_session,
        age_minutes=45,
        audit_status="failed",
        usage_status="complete",
    )
    _lineage(authority_session, age_minutes=1)

    report = incomplete_ingestion_status(
        older_than_minutes=15,
        limit=10,
        session=authority_session,
    )

    assert report.matching_interaction_count == 2
    assert report.pending_audit_count == 1
    assert report.failed_audit_count == 1
    assert report.pending_usage_count == 1
    assert report.failed_usage_count == 0
    assert {row.interaction_ref for row in report.interactions} == {
        stale.interaction_ref,
        failed.interaction_ref,
    }
    assert set(report.interactions[0].__dict__) == {
        "interaction_ref",
        "audit_status",
        "usage_status",
    }


def test_guarded_reconciliation_is_idempotent_and_authoritatively_audited(
    authority_session: Session,
) -> None:
    lineage = _lineage(authority_session, age_minutes=60)

    result = mark_stale_ingestion_failed(
        interaction_ref=lineage.interaction_ref,
        component="usage",
        older_than_minutes=15,
        session=authority_session,
    )
    replay = mark_stale_ingestion_failed(
        interaction_ref=lineage.interaction_ref,
        component="usage",
        older_than_minutes=15,
        session=authority_session,
    )

    assert result.changed is True
    assert replay.changed is False
    assert result.audit_status == "pending"
    assert result.usage_status == "failed"
    events = authority_session.exec(
        select(AuthorityAuditEvent).where(
            AuthorityAuditEvent.event_type == "interaction.usage_ingestion.failed"
        )
    ).all()
    assert len(events) == 1
    assert events[0].resource_ref == lineage.interaction_ref
    assert events[0].content_sha256 is None
    assert events[0].platform_refs == []


def test_reconciliation_rejects_recent_or_complete_components(
    authority_session: Session,
) -> None:
    recent = _lineage(authority_session, age_minutes=1)
    with pytest.raises(IngestionReconciliationStateError):
        mark_stale_ingestion_failed(
            interaction_ref=recent.interaction_ref,
            component="usage",
            older_than_minutes=15,
            session=authority_session,
        )

    complete = _lineage(
        authority_session,
        age_minutes=60,
        audit_status="complete",
        usage_status="complete",
    )
    with pytest.raises(IngestionReconciliationStateError):
        mark_stale_ingestion_failed(
            interaction_ref=complete.interaction_ref,
            component="usage",
            older_than_minutes=15,
            session=authority_session,
        )


def test_stale_accepted_turn_reconciliation_is_dry_run_fixed_and_idempotent(
    authority_session: Session,
) -> None:
    turn = _accepted_turn(authority_session, age_minutes=60)

    dry_run = reconcile_stale_accepted_turn(
        turn_id=turn.id,
        older_than_minutes=15,
        apply=False,
        session=authority_session,
    )
    assert dry_run.state == "eligible"
    assert dry_run.dry_run is True
    assert dry_run.changed is False
    assert authority_session.exec(select(CanonicalConversationFinalPhase)).first() is None

    applied = reconcile_stale_accepted_turn(
        turn_id=turn.id,
        older_than_minutes=15,
        apply=True,
        session=authority_session,
    )
    replay = reconcile_stale_accepted_turn(
        turn_id=turn.id,
        older_than_minutes=15,
        apply=True,
        session=authority_session,
    )

    assert applied.state == replay.state == "reconciled"
    assert applied.changed is True
    assert replay.changed is False
    assert set(applied.__dict__) == {
        "turn_ref",
        "interaction_ref",
        "state",
        "dry_run",
        "changed",
    }
    final = authority_session.exec(select(CanonicalConversationFinalPhase)).one()
    assert final.outcome == "unavailable"
    assert final.content == OPERATOR_FINAL_CONTENT
    assert final.presentation_mode == "standard"
    assert final.request_digest == OPERATOR_FINAL_REQUEST_DIGEST
    assert len(authority_session.exec(select(AuthorityIdempotencyRecord)).all()) == 1
    events = authority_session.exec(
        select(AuthorityAuditEvent).where(
            AuthorityAuditEvent.event_type
            == "canonical_conversation.final_response.operator_reconciled"
        )
    ).all()
    assert len(events) == 1
    assert events[0].content_sha256 == OPERATOR_FINAL_CONTENT_SHA256
    assert events[0].resource_ref == str(turn.id)


def test_stale_accepted_turn_reconciliation_refuses_recent_or_existing_final(
    authority_session: Session,
) -> None:
    recent = _accepted_turn(authority_session, age_minutes=1)
    with pytest.raises(IngestionReconciliationStateError):
        reconcile_stale_accepted_turn(
            turn_id=recent.id,
            older_than_minutes=15,
            apply=True,
            session=authority_session,
        )

    failed = _accepted_turn(authority_session, age_minutes=60)
    failed_lineage = authority_session.get(InteractionLineage, failed.interaction_id)
    assert failed_lineage is not None
    failed_lineage.audit_status = "failed"
    authority_session.add(failed_lineage)
    authority_session.commit()
    with pytest.raises(IngestionReconciliationStateError):
        reconcile_stale_accepted_turn(
            turn_id=failed.id,
            older_than_minutes=15,
            apply=True,
            session=authority_session,
        )

    stale = _accepted_turn(authority_session, age_minutes=60)
    authority_session.add(
        CanonicalConversationFinalPhase(
            turn_id=stale.id,
            outcome="unavailable",
            content_type="text/plain",
            content="A different final must never be overwritten.",
            presentation_mode="standard",
            suppress_disclaimer=False,
            suppress_intent_badge=False,
            suppress_doctrinal_badge=False,
            suppress_citations=False,
            suppress_structured_cards=False,
            suppress_feature_actions=False,
            suppress_guided_paths=False,
            request_digest="b" * 64,
            recorded_at=datetime.now(UTC),
        )
    )
    authority_session.commit()
    with pytest.raises(IngestionReconciliationStateError):
        reconcile_stale_accepted_turn(
            turn_id=stale.id,
            older_than_minutes=15,
            apply=True,
            session=authority_session,
        )
