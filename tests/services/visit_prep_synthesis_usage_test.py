"""Real PostgreSQL usage facts; no external model or fabricated chart lineage."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from typeid import TypeID

import app.services.visit_prep_synthesis_usage as usage_service
from app.services.visit_prep_synthesis_builds import (
    VisitPrepBuildDiscarded,
    claim_visit_prep_synthesis,
)
from app.services.visit_prep_synthesis_generator import (
    MODEL_PROFILE,
    VisitPrepGenerationUsage,
)
from app.services.visit_prep_synthesis_usage import (
    VisitPrepUsageUnavailable,
    begin_synthesis_usage,
    record_synthesis_usage,
)
from app.services.visit_preps import VisitPrepNotFound

from activemodel.session_manager import get_session
from app.models.authority import (
    AuthorityActor,
    AuthorityPerson,
    AuthorityTenant,
    InteractionLineage,
)
from app.models.authority_usage import AgentModelUsageEvent
from sqlalchemy import delete, text, update
from sqlalchemy.exc import DBAPIError
from sqlmodel import select

from tests.services.visit_prep_contribution_commands_test import _correct
from tests.services.visit_prep_contribution_projection_test import _prepared
from tests.services.visit_prep_interviews_test import _respond
from tests.services.visit_prep_synthesis_builds_test import _expire, _request
from tests.services.visit_prep_synthesis_publication_test import _selected
from tests.services.visit_preps_test import _withdraw_seat


def _events(fixture):
    with get_session() as session:
        return session.exec(
            select(AgentModelUsageEvent)
            .where(AgentModelUsageEvent.preparation_ref == fixture.prep_id)
            .order_by(AgentModelUsageEvent.recorded_at)
        ).all()


def _started(monkeypatch, label="usage", *, role="patient", complete=True):
    fixture = _prepared(monkeypatch, label, role=role, complete=complete)
    selected = _selected(fixture)
    _request(fixture)
    claim = claim_visit_prep_synthesis()
    assert claim is not None and claim.visit_prep_id == fixture.prep_id
    return fixture, claim, selected


def _usage(**changes):
    now = datetime.now(UTC)
    return replace(
        VisitPrepGenerationUsage(
            provider="azure_openai",
            model_profile=MODEL_PROFILE,
            provider_request_id="chatcmpl-synthetic",
            started_at=now,
            completed_at=now + timedelta(milliseconds=8),
            latency_ms=8,
            status="succeeded",
            usage_availability="complete",
            input_tokens=51,
            output_tokens=23,
        ),
        **changes,
    )


@pytest.mark.parametrize("role,complete", [("patient", True), ("caregiver", False)])
def test_one_central_intent_and_result_without_chart_or_patient_content(
    monkeypatch, role, complete
):
    fixture, claim, selected = _started(monkeypatch, role=role, complete=complete)
    with get_session() as session:
        before = [
            len(session.exec(select(model)).all())
            for model in (
                AuthorityActor,
                AuthorityTenant,
                AuthorityPerson,
                InteractionLineage,
            )
        ]
    intent_id = begin_synthesis_usage(claim, selected)
    intent = _events(fixture)[0]
    assert intent.id == intent_id and intent.status == "started"
    assert intent.completed_at is None and intent.input_tokens is None
    assert (
        intent.interaction_id
        is intent.tenant_id
        is intent.actor_id
        is intent.person_id
        is None
    )
    assert intent.core_member_id == fixture.owner.id
    assert intent.core_doctor_id == fixture.doctor.id
    assert intent.core_practice_id == fixture.practice.id
    assert intent.source_version == selected.source_version
    assert intent.build_lease_until == claim.lease_until
    result_id = record_synthesis_usage(intent_id, _usage())
    intent, result = _events(fixture)
    assert result.id == result_id and result.call_intent_id == intent.id
    assert result.status == "succeeded" and result.input_tokens == 51
    assert result.output_tokens == 23 and result.usage_source == "provider_response"
    assert intent.status == "started" and intent.input_tokens is None
    for row in (intent, result):
        serialized = str(row.model_dump()) + repr(row)
        assert fixture.access_token not in serialized
        assert fixture.doctor.clerk_id not in serialized
        assert "My original invented reason." not in serialized
    with get_session() as session:
        assert before == [
            len(session.exec(select(model)).all())
            for model in (
                AuthorityActor,
                AuthorityTenant,
                AuthorityPerson,
                InteractionLineage,
            )
        ]


def test_intent_retry_never_authorizes_another_call_and_result_retry_is_exact(
    monkeypatch,
):
    fixture, claim, selected = _started(monkeypatch, "usage-replay")
    intent = begin_synthesis_usage(claim, selected)
    with pytest.raises(VisitPrepUsageUnavailable):
        begin_synthesis_usage(claim, selected)
    usage = _usage()
    first = record_synthesis_usage(intent, usage)
    assert record_synthesis_usage(intent, usage) == first
    with pytest.raises(VisitPrepUsageUnavailable):
        record_synthesis_usage(intent, replace(usage, input_tokens=52))
    assert len(_events(fixture)) == 2


@pytest.mark.parametrize("change", ["correction", "offboard", "lease"])
def test_already_attempted_usage_is_recorded_after_scope_or_lease_changes(
    monkeypatch, change
):
    fixture, claim, selected = _started(monkeypatch, "usage-already-attempted")
    intent = begin_synthesis_usage(claim, selected)
    if change == "correction":
        _correct(fixture)
    elif change == "offboard":
        _withdraw_seat(fixture.owner)
    else:
        _expire(fixture)
    result = record_synthesis_usage(
        intent,
        _usage(
            status="failed",
            usage_availability="unavailable",
            input_tokens=None,
            output_tokens=None,
            provider_request_id=None,
        ),
    )
    assert _events(fixture)[1].id == result
    assert _events(fixture)[1].usage_source == "not_available"
    assert _events(fixture)[1].status == "failed"


@pytest.mark.parametrize("change", ["correction", "offboard", "lease", "wording"])
def test_begin_denies_changed_scope_source_or_fence(monkeypatch, change):
    fixture, claim, selected = _started(monkeypatch, "usage-begin-denial")
    if change == "correction":
        _correct(fixture)
    elif change == "offboard":
        _withdraw_seat(fixture.owner)
    elif change == "lease":
        _expire(fixture)
    else:
        source = replace(selected.sources[0], answer="Other synthetic words.")
        selected = replace(selected, sources=(source, *selected.sources[1:]))
    with pytest.raises((VisitPrepBuildDiscarded, VisitPrepNotFound)):
        begin_synthesis_usage(claim, selected)
    assert not _events(fixture)


def test_audit_failure_rolls_back_call_intent(monkeypatch):
    fixture, claim, selected = _started(monkeypatch, "usage-audit-rollback")

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic audit failure")

    monkeypatch.setattr("app.services.visit_prep_synthesis_builds._audit", fail)
    with pytest.raises(RuntimeError, match="synthetic audit failure"):
        begin_synthesis_usage(claim, selected)
    assert not _events(fixture)


def test_final_database_lease_expiry_rolls_back_intent(monkeypatch):
    fixture, claim, selected = _started(monkeypatch, "usage-final-clock")
    calls = 0

    def clock(session):
        nonlocal calls
        calls += 1
        return (
            datetime.now(UTC)
            if calls <= 2
            else claim.lease_until + timedelta(seconds=1)
        )

    monkeypatch.setattr(
        "app.services.visit_prep_synthesis_builds._database_now",
        clock,
    )
    with pytest.raises(VisitPrepBuildDiscarded):
        begin_synthesis_usage(claim, selected)
    assert not _events(fixture)
    assert calls == 3


def test_empty_sources_create_no_model_usage_and_unknown_intent_is_denied(monkeypatch):
    from app.services import visit_prep_interviews as interviews

    from tests.services.visit_prep_interviews_test import _fixture

    fixture = _fixture(monkeypatch, "usage-no-sources")
    interviews.confirm_interview(
        access_token=fixture.access_token, first_name="José", role="patient"
    )
    for key in ("reason", "medications", "adherence", "story"):
        _respond(fixture, key)
    interviews.complete_interview(access_token=fixture.access_token)
    selected = _selected(fixture)
    assert not selected.sources
    _request(fixture)
    claim = claim_visit_prep_synthesis()
    with pytest.raises(VisitPrepUsageUnavailable):
        begin_synthesis_usage(claim, selected)
    with pytest.raises(VisitPrepUsageUnavailable):
        record_synthesis_usage(TypeID(prefix="model_usage"), _usage())
    assert not _events(fixture)


def test_result_commit_failure_leaves_unmatched_intent_and_no_success(monkeypatch):
    fixture, claim, selected = _started(monkeypatch, "usage-result-rollback")
    intent = begin_synthesis_usage(claim, selected)

    def fail(*args):
        raise RuntimeError("synthetic persistence failure")

    monkeypatch.setattr(usage_service, "_commit", fail)
    with pytest.raises(RuntimeError, match="synthetic persistence failure"):
        record_synthesis_usage(intent, _usage())
    assert len(_events(fixture)) == 1 and _events(fixture)[0].status == "started"


@pytest.mark.parametrize(
    "changes",
    [
        {"input_tokens": -1},
        {"output_tokens": True},
        {"input_tokens": 2**63},
        {"latency_ms": True},
        {"latency_ms": -1},
        {"provider_request_id": "Synthetic patient words with spaces"},
        {"provider_request_id": "bad\nrequest"},
        {"model_profile": "wrong-model"},
        {"usage_availability": "unavailable"},
        {"input_tokens": None},
        {"started_at": datetime(2026, 1, 1)},
        {"started_at": datetime(2026, 1, 1, tzinfo=UTC)},
        {"completed_at": datetime(2026, 1, 1, tzinfo=UTC)},
        {"status": "cancelled"},
    ],
)
def test_invalid_usage_is_rejected_without_result(monkeypatch, changes):
    fixture, claim, selected = _started(monkeypatch, "usage-invalid")
    intent = begin_synthesis_usage(claim, selected)
    with pytest.raises(VisitPrepUsageUnavailable):
        record_synthesis_usage(intent, _usage(**changes))
    assert len(_events(fixture)) == 1


def _clone(row, **changes):
    values = row.model_dump()
    values.pop("id")
    values["client_event_id"] = uuid4()
    return AgentModelUsageEvent(**{**values, **changes})


@pytest.mark.parametrize(
    "changes",
    [
        {"event_origin": "agent_turn"},
        {"event_origin": "another_origin"},
        {"batch_phase": None},
        {"build_epoch": None},
        {"build_attempt": None},
        {"core_member_id": None},
        {"source_version": "x" * 64},
        {"surface": "provider_assistant"},
        {"auth_tier": "anonymous"},
        {"build_epoch": 2},
        {"status": "succeeded"},
        {"input_tokens": 0},
        {"build_ref": TypeID(prefix="visit_prep_build")},
    ],
)
def test_database_rejects_incomplete_agent_or_invalid_batch_intent(
    monkeypatch, changes
):
    fixture, claim, selected = _started(monkeypatch, "usage-shape")
    begin_synthesis_usage(claim, selected)
    intent = _events(fixture)[0]
    with pytest.raises(DBAPIError), get_session() as session:
        session.add(_clone(intent, **changes))
        session.commit()
    assert len(_events(fixture)) == 1


def test_database_result_must_match_immutable_intent_and_usage_is_append_only(
    monkeypatch,
):
    fixture, claim, selected = _started(monkeypatch, "usage-immutable")
    intent = begin_synthesis_usage(claim, selected)
    record_synthesis_usage(intent, _usage())
    original, result = _events(fixture)
    with pytest.raises(DBAPIError), get_session() as session:
        session.add(_clone(result, build_epoch=2))
        session.commit()
    for statement in (
        update(AgentModelUsageEvent)
        .where(AgentModelUsageEvent.id == original.id)
        .values(input_tokens=0),
        delete(AgentModelUsageEvent).where(AgentModelUsageEvent.id == result.id),
        text("TRUNCATE public.agent_model_usage_event"),
    ):
        with pytest.raises(DBAPIError), get_session() as session:
            session.execute(statement)
            session.commit()
    assert len(_events(fixture)) == 2
