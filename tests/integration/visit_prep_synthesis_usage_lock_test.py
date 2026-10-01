"""Independent-connection accounting and populated single-ledger expand."""

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from app.services.visit_prep_synthesis_builds import fail_visit_prep_synthesis_claim
from app.services.visit_prep_synthesis_usage import (
    VisitPrepUsageUnavailable,
    begin_synthesis_usage,
    record_synthesis_usage,
)

from activemodel.session_manager import get_session
from app.models.authority_usage import AgentModelUsageEvent
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlmodel import select

from tests.authority_kernel.model_usage_test import (
    _persist_user_phase,
    _provider_lineage,
    _report,
    _usage_payload,
)
from tests.services.visit_prep_contribution_projection_test import _prepared
from tests.services.visit_prep_synthesis_builds_test import _request
from tests.services.visit_prep_synthesis_publication_test import _publish
from tests.services.visit_prep_synthesis_usage_test import _events, _started, _usage


def test_same_claim_intents_allow_one_call_and_exact_result_retries_deduplicate(
    monkeypatch,
):
    fixture, claim, selected = _started(monkeypatch, "usage-concurrent")
    barrier = Barrier(2)

    def begin():
        barrier.wait(timeout=5)
        try:
            return begin_synthesis_usage(claim, selected)
        except VisitPrepUsageUnavailable:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(begin) for _ in range(2)]
        intents = [future.result(timeout=10) for future in futures]
    assert sum(intent is not None for intent in intents) == 1
    intent = next(intent for intent in intents if intent is not None)
    usage, barrier = _usage(), Barrier(2)

    def record():
        barrier.wait(timeout=5)
        return record_synthesis_usage(intent, usage)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(record) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]
    assert results[0] == results[1]
    assert len(_events(fixture)) == 2
    assert fail_visit_prep_synthesis_claim(claim, code="scope_unavailable")


def test_committed_batch_usage_rejects_truncate_through_its_named_guard(monkeypatch):
    fixture, claim, selected = _started(monkeypatch, "usage-truncate")
    intent = begin_synthesis_usage(claim, selected)
    record_synthesis_usage(intent, _usage())
    # Independent committed transactions eliminate pending-FK trigger rejection
    # as a substitute for exercising the explicit ledger truncate guard.
    with (
        pytest.raises(DBAPIError, match="Batch usage is append-only"),
        get_session() as session,
    ):
        session.execute(text("TRUNCATE public.agent_model_usage_event"))
        session.commit()
    assert len(_events(fixture)) == 2
    assert fail_visit_prep_synthesis_claim(claim, code="scope_unavailable")


def test_populated_usage_expand_preserves_agent_facts_sources_and_old_default_writes(
    monkeypatch,
):
    if os.environ.get("VISIT_PREP_MIGRATION_TARGET") != "2f87498bb9fa":
        pytest.skip("requires explicit owned pre-usage migration-target rehearsal")
    from alembic import command
    from alembic.config import Config

    fixture = _prepared(monkeypatch, "usage-populated")
    _publish(fixture)
    assert _request(fixture).state == "ready"
    with get_session() as session:
        token, interaction, *_ = _provider_lineage(session)
        _persist_user_phase(session, token=token, interaction_ref=interaction)
        # The old service's ORM now knows new columns; emulate its actual old
        # insert by including only columns that existed before the expand.
        # Create a valid fact via the original SQL column list, not a fake row.
        from datetime import UTC, datetime
        from uuid import uuid4

        from app.models.authority import MountContextSession

        from tests.authority_kernel.model_usage_test import _lineage

        lineage = _lineage(session, interaction)
        mount = session.get(MountContextSession, lineage.session_id)
        session.execute(
            text("""
            INSERT INTO public.agent_model_usage_event
            (id, interaction_id, workload_client_id, client_event_id, tenant_id,
             actor_id, person_id, surface, auth_tier, call_kind, call_sequence,
             workstream, provider, purpose, status, usage_availability, usage_source,
             request_digest, recorded_at)
            SELECT :id, i.id, i.workload_client_id, :event, m.tenant_id, m.actor_id,
                   b.person_id, 'provider_assistant', 'provider', 'drafting', 1,
                   'provider.chat', 'azure_openai', 'response.drafting', 'succeeded',
                   'unavailable', 'not_available', :digest, :now
            FROM public.interaction_lineage i JOIN public.mount_context_session m ON m.id=i.session_id
            JOIN public.authority_person_binding b ON b.id=m.person_binding_id WHERE i.id=:interaction
        """),
            {
                "id": uuid4(),
                "event": uuid4(),
                "digest": "a" * 64,
                "now": datetime.now(UTC),
                "interaction": lineage.id.uuid,
            },
        )
        assert mount is not None
        session.commit()
        old_usage = (
            session.execute(
                text("SELECT to_jsonb(t)::text FROM public.agent_model_usage_event t")
            )
            .scalars()
            .all()
        )

    tables = (
        "visit_prep",
        "visit_prep_schedule_generation",
        "visit_prep_credential_generation",
        "visit_prep_question_response",
        "visit_prep_contribution_event",
        "visit_prep_synthesis_artifact",
        "visit_prep_synthesis_build",
    )

    def sources():
        with get_session() as session:
            return {
                name: session.execute(
                    text(f"SELECT to_jsonb(t)::text FROM public.{name} t ORDER BY id")
                )
                .scalars()
                .all()
                for name in tables
            }

    before = sources()
    command.upgrade(
        Config(str(Path(__file__).resolve().parents[2] / "alembic.ini")), "54bedaad381a"
    )
    assert sources() == before
    new_columns = "event_origin,core_practice_id,core_doctor_id,core_member_id,preparation_ref,build_ref,build_epoch,build_attempt,build_lease_until,source_version,batch_phase,call_intent_id".split(
        ","
    )
    with get_session() as session:
        old_projection = " - ".join("'" + name + "'" for name in new_columns)
        after = (
            session.execute(
                text(
                    f"SELECT (to_jsonb(t) - {old_projection})::text FROM public.agent_model_usage_event t"
                )
            )
            .scalars()
            .all()
        )
        assert after == old_usage
        existing = session.exec(select(AgentModelUsageEvent)).one()
        assert existing.event_origin == "agent_turn"
        assert existing.batch_phase is None and existing.core_member_id is None
        # Old agent reporting still works and exactly replays after expansion.
        payload = _usage_payload(sequence=2)
        first = _report(
            session, interaction_ref=interaction, payload=payload, token=token
        )
        assert (
            _report(
                session, interaction_ref=interaction, payload=payload, token=token
            ).record_ref
            == first.record_ref
        )
    assert sources() == before
    new_fixture, claim, selected = _started(monkeypatch, "usage-after-expand")
    intent = begin_synthesis_usage(claim, selected)
    record_synthesis_usage(intent, _usage())
    assert len(_events(new_fixture)) == 2
    assert fail_visit_prep_synthesis_claim(claim, code="scope_unavailable")
