from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Callable, Literal, cast
from uuid import UUID

import pytest

import app.services.authority as authority
import app.services.session_prep as session_prep
from app.services.session_prep import (
    ImmutableArtifactStore,
    SessionPrepIdempotencyConflict,
    SessionPrepInvariantError,
    SessionPrepInvocation,
    SessionPrepScopeDenied,
    SyntheticContext,
    SyntheticSessionPrepService,
    _eligible_finalized_note,
    _synthetic_note,
    authorize_session_prep_read,
)

from sqlmodel import Session

CLIENT_TURN_ID = UUID("44444444-4444-4444-8444-444444444444")
SOURCE_TURN_REF = "conversation_turn_synthetic_0001"
OBSERVED_AT = datetime(2026, 9, 2, 16, 0, tzinfo=UTC)
TARGET_AT = datetime(2026, 9, 3, 14, 0, tzinfo=UTC)


def _service_for(
    context: SyntheticContext,
    *,
    clock: Callable[[], datetime] | None = None,
) -> SyntheticSessionPrepService:
    return SyntheticSessionPrepService((context,), clock=clock or (lambda: OBSERVED_AT))


def _invoke(
    service: SyntheticSessionPrepService,
    context: SyntheticContext,
    *,
    detail: Literal["brief", "supporting_context"] = "brief",
    idempotency_key: str = "idem_session_prep_test_0001",
    source_turn_ref: str = SOURCE_TURN_REF,
    turn_closed: bool = False,
) -> SessionPrepInvocation:
    return service.invoke(
        workload_client_id="11111111-1111-4111-8111-111111111111",
        interaction_ref="interaction_synthetic_0001",
        client_turn_id=CLIENT_TURN_ID,
        idempotency_key=idempotency_key,
        source_turn_ref=source_turn_ref,
        context_snapshot_ref=context.context_snapshot_ref,
        detail=detail,
        allowed_context_snapshot_ref=context.context_snapshot_ref,
        turn_closed=turn_closed,
    )


def _note(index: int, **overrides: Any):
    values: dict[str, Any] = {
        "source_ref": f"note_synthetic_eligibility_{index:02d}",
        "source_version": 1,
        "source_at": TARGET_AT - timedelta(days=index + 1),
        "recap": f"Synthetic finalized recap {index}.",
        "talking_points": (f"Review synthetic topic {index}.",),
        "suggested_outline": (f"Discuss synthetic item {index}.",),
    }
    values.update(overrides)
    return _synthetic_note(**values)


def _context(
    ref: str,
    *notes,
    state: Literal["ready", "refreshing", "pending", "source_revoked"] = "ready",
    temporarily_unavailable: bool = False,
) -> SyntheticContext:
    return SyntheticContext(ref, TARGET_AT, state, notes, temporarily_unavailable)


def test_finalized_note_selection_is_bounded_and_excludes_ineligible_sources() -> None:
    eligible = tuple(_note(index) for index in range(7))
    old = _note(20, source_at=TARGET_AT - timedelta(days=549))
    draft = _note(21, lifecycle="draft", clinician_reviewed=False)
    raw = _note(22, lifecycle="raw_transcript", clinician_reviewed=False)
    tampered = replace(_note(23), content_sha256="sha256_" + ("0" * 64))
    context = _context(
        "context_synthetic_eligibility", *eligible, old, draft, raw, tampered
    )

    result = _invoke(_service_for(context), context).response.result

    assert result.state == "ready"
    assert result.completeness == "partial"
    assert result.source_count == 5
    assert len([item for item in result.provenance if item.included]) == 5
    assert any(item.reason_code == "selection_limit" for item in result.provenance)
    assert any(item.reason_code == "source_not_enabled" for item in result.provenance)
    serialized = result.model_dump_json()
    for source in (old, draft, raw, tampered):
        assert source.source_ref not in serialized
        assert source.recap not in serialized

    boundary = _note(30, source_at=TARGET_AT - timedelta(days=548))
    too_old = _note(31, source_at=boundary.source_at - timedelta(microseconds=1))
    at_target = _note(32, source_at=TARGET_AT)
    after_target = _note(33, source_at=TARGET_AT + timedelta(microseconds=1))
    assert _eligible_finalized_note(boundary, target_encounter_at=TARGET_AT)
    assert not _eligible_finalized_note(too_old, target_encounter_at=TARGET_AT)
    assert not _eligible_finalized_note(at_target, target_encounter_at=TARGET_AT)
    assert not _eligible_finalized_note(after_target, target_encounter_at=TARGET_AT)


def test_ready_result_has_process_local_immutable_lineage_and_replay_receipt() -> None:
    note = _note(1)
    context = _context("context_synthetic_ready_test", note)
    service = _service_for(context)

    first = _invoke(service, context)
    retry = _invoke(service, context, turn_closed=True)
    poll = _invoke(service, context, idempotency_key="idem_session_prep_test_0002")

    assert first.replayed is False
    assert retry.replayed is True
    assert retry.response == first.response
    assert poll.replayed is False
    assert poll.response.tool_call_ref != first.response.tool_call_ref
    assert poll.response.result.snapshot_ref == first.response.result.snapshot_ref
    assert poll.response.result.brief_ref == first.response.result.brief_ref
    assert first.response.result.snapshot_version == 1
    assert first.response.result.snapshot_supersedes_ref is None
    assert first.response.result.brief_version == 1
    assert first.response.result.brief_supersedes_ref is None

    with pytest.raises(SessionPrepScopeDenied):
        _invoke(
            service,
            context,
            idempotency_key="idem_session_prep_closed_0003",
            turn_closed=True,
        )
    with pytest.raises(SessionPrepScopeDenied):
        _invoke(_service_for(context), context, turn_closed=True)


def test_source_revocation_replaces_a_cached_ready_response_with_a_tombstone() -> None:
    context = _context("context_synthetic_revocation_test", _note(1))
    service = _service_for(context)

    ready = _invoke(service, context)
    assert ready.response.result.state == "ready"

    revoked = replace(context, state="source_revoked")
    service._contexts[context.context_snapshot_ref] = revoked
    tombstone = _invoke(service, revoked, turn_closed=True)
    replay = _invoke(service, revoked, turn_closed=True)
    poll = _invoke(
        service,
        revoked,
        idempotency_key="idem_session_prep_revoked_poll_0002",
        turn_closed=True,
    )

    assert tombstone.replayed is False
    assert tombstone.response.result.state == "unavailable"
    assert tombstone.response.result.reason_code == "context_not_available"
    assert tombstone.response.result.prepared_brief is None
    assert tombstone.response.result.provenance == []
    assert "Synthetic finalized recap" not in tombstone.response.model_dump_json()
    assert replay.replayed is True
    assert replay.response == tombstone.response
    assert poll.response.result.state == "unavailable"
    assert poll.response.result.reason_code == "context_not_available"
    assert poll.response.result.prepared_brief is None
    assert poll.response.result.provenance == []
    assert "Synthetic finalized recap" not in poll.response.model_dump_json()

    with pytest.raises(SessionPrepIdempotencyConflict):
        _invoke(
            service,
            revoked,
            source_turn_ref="conversation_turn_changed_after_revocation",
            turn_closed=True,
        )


def test_supporting_context_requires_an_unimplemented_follow_up_action() -> None:
    context = _context("context_synthetic_context_test", _note(1), _note(2))

    with pytest.raises(SessionPrepScopeDenied):
        _invoke(_service_for(context), context, detail="supporting_context")


@pytest.mark.parametrize(
    ("state", "expected_state", "reason"),
    [
        ("pending", "pending", "generation_pending"),
        ("source_revoked", "unavailable", "context_not_available"),
    ],
)
def test_content_free_states_never_return_prior_or_source_content(
    state: Literal["pending", "source_revoked"],
    expected_state: str,
    reason: str,
) -> None:
    context = _context(f"context_synthetic_{state}_test", _note(1), state=state)

    result = _invoke(_service_for(context), context).response.result

    assert result.state == expected_state
    assert result.reason_code == reason
    assert result.prepared_brief is None
    assert result.supporting_context is None
    assert result.provenance == []
    assert "Synthetic finalized recap" not in result.model_dump_json()


def test_no_eligible_finalized_note_is_non_retryable_unavailable() -> None:
    context = _context(
        "context_synthetic_empty_test",
        _note(1, lifecycle="draft", clinician_reviewed=False),
    )

    result = _invoke(_service_for(context), context).response.result

    assert result.state == "unavailable"
    assert result.reason_code == "no_eligible_sources"
    assert result.retry_after_seconds is None

    oversized = _note(2, recap="x" * 18_001)
    bounded_context = replace(context, notes=(oversized,))
    bounded = _invoke(_service_for(bounded_context), bounded_context).response.result
    assert _eligible_finalized_note(oversized, target_encounter_at=TARGET_AT)
    assert bounded.state == "unavailable"
    assert bounded.reason_code == "context_not_available"
    mixed_context = replace(context, notes=(_note(3), oversized))
    mixed = _invoke(_service_for(mixed_context), mixed_context).response.result
    assert mixed.completeness == "partial" and mixed.source_count == 1
    assert any(item.reason_code == "selection_limit" for item in mixed.provenance)


@pytest.mark.parametrize(
    ("field", "unsafe_text"),
    [
        ("recap", "The diagnosis is pneumonia."),
        ("material_changes", "Start taking lisinopril now."),
        ("talking_points", "Stop taking metformin."),
        ("open_threads", "Increase your dose to 20 mg."),
        ("cautions", "Decrease the medication dose."),
        ("suggested_outline", "Avoid urgent care tonight."),
        ("suggested_outline", "Delay emergency care until tomorrow."),
    ],
)
def test_unsafe_text_is_rejected_before_persistence(
    field: str,
    unsafe_text: str,
) -> None:
    value: object = unsafe_text if field == "recap" else (unsafe_text,)
    context = _context("context_synthetic_unsafe_test", _note(1, **{field: value}))
    service = _service_for(context)
    result = _invoke(service, context).response.result
    assert result.state == "unavailable" and result.prepared_brief is None
    assert unsafe_text not in result.model_dump_json()
    assert service._artifacts == {}


def test_safety_is_rechecked_before_egress(monkeypatch) -> None:
    context = _context("context_synthetic_egress_test", _note(1))
    decisions = iter((True, True, False))
    monkeypatch.setattr(session_prep, "_passes_safety", lambda _brief: next(decisions))
    service = _service_for(context)
    assert _invoke(service, context).response.result.state == "ready"
    blocked = _invoke(
        service, context, idempotency_key="idem_session_prep_egress_0002"
    ).response.result
    assert blocked.state == "unavailable" and blocked.prepared_brief is None


def test_refreshing_returns_a_bounded_stale_predecessor_then_suppresses_it() -> None:
    observed = [OBSERVED_AT]
    context = _context(
        "context_synthetic_refresh_test",
        _note(1),
        state="refreshing",
        temporarily_unavailable=True,
    )
    service = _service_for(context, clock=lambda: observed[0])

    refreshing = _invoke(service, context).response.result
    observed[0] += timedelta(minutes=16)
    expired = _invoke(
        service,
        context,
        idempotency_key="idem_session_prep_refresh_0002",
    ).response.result

    assert refreshing.state == "refreshing"
    assert refreshing.freshness == "stale"
    assert refreshing.retry_after_seconds == 5
    assert refreshing.prepared_brief is not None
    assert expired.state == "unavailable"
    assert expired.prepared_brief is None


def test_scope_and_idempotency_conflicts_fail_closed() -> None:
    context = _context("context_synthetic_scope_test", _note(1))
    service = _service_for(context)
    _invoke(service, context)

    with pytest.raises(SessionPrepIdempotencyConflict):
        _invoke(service, context, source_turn_ref="conversation_turn_synthetic_changed")
    with pytest.raises(SessionPrepScopeDenied):
        service.invoke(
            workload_client_id="11111111-1111-4111-8111-111111111111",
            interaction_ref="interaction_synthetic_0001",
            client_turn_id=CLIENT_TURN_ID,
            idempotency_key="idem_session_prep_scope_0002",
            source_turn_ref=SOURCE_TURN_REF,
            context_snapshot_ref="context_synthetic_other_scope",
            detail="brief",
            allowed_context_snapshot_ref=context.context_snapshot_ref,
        )


def test_immutable_store_rejects_replacement_of_an_artifact_ref() -> None:
    note = _note(1)
    context = _context("context_synthetic_immutable_test", note)
    service = _service_for(context)
    _invoke(service, context)
    snapshot, brief = service._artifacts[context.context_snapshot_ref]
    store = ImmutableArtifactStore()
    store.record(snapshot, brief)

    with pytest.raises(SessionPrepInvariantError, match="replacement refused"):
        store.record(
            replace(snapshot, source_version_digest="sha256_" + ("b" * 64)), brief
        )


class _SyntheticRows:
    def __init__(self, value: object) -> None:
        self.value = value

    def one_or_none(self) -> object:
        return self.value


class _SyntheticTurnSession:
    def __init__(self, turn: object, user_phase: object, final_phase: object = None):
        self.turn = turn
        self.results = iter((user_phase, final_phase))

    def get(self, model: object, source_turn_ref: str) -> object:
        return self.turn if source_turn_ref == SOURCE_TURN_REF else None

    def exec(self, statement: object) -> _SyntheticRows:
        return _SyntheticRows(next(self.results))


def test_fresh_canonical_session_prep_action_is_required(monkeypatch) -> None:
    authorized = SimpleNamespace(
        context=SimpleNamespace(
            capabilities=("session-prep:read",), person_id="person_synthetic_001"
        ),
        mount_session=SimpleNamespace(
            tenant_id="tenant_synthetic_001", actor_id="actor_synthetic_001"
        ),
        lineage=SimpleNamespace(id="lineage_synthetic_001"),
    )
    monkeypatch.setattr(authority, "authorize_interaction", lambda **kwargs: authorized)

    def scope(*, action="session-prep.open", interaction="lineage_synthetic_001"):
        turn = SimpleNamespace(
            id=SOURCE_TURN_REF,
            interaction_id=interaction,
            workload_client_id="11111111-1111-4111-8111-111111111111",
            client_turn_id=CLIENT_TURN_ID,
            tenant_id="tenant_synthetic_001",
            actor_id="actor_synthetic_001",
            person_id="person_synthetic_001",
        )
        user_phase = SimpleNamespace(
            ingress_kind="provider_ui_action", action_code=action
        )
        return cast(Session, _SyntheticTurnSession(turn, user_phase))

    accepted = authorize_session_prep_read(
        workload_client_id="11111111-1111-4111-8111-111111111111",
        mount_context_token="mctx_synthetic_token_000000000000",
        interaction_ref="interaction_synthetic_0001",
        client_turn_id=CLIENT_TURN_ID,
        source_turn_ref=SOURCE_TURN_REF,
        session=scope(),
    )
    assert accepted.authorized is authorized
    assert accepted.turn_closed is False

    for invalid_session, source_ref in (
        (scope(action="labs.review-requested"), SOURCE_TURN_REF),
        (scope(interaction="lineage_synthetic_other"), SOURCE_TURN_REF),
        (scope(), "conversation_turn_synthetic_forged"),
    ):
        with pytest.raises(SessionPrepScopeDenied):
            authorize_session_prep_read(
                workload_client_id="11111111-1111-4111-8111-111111111111",
                mount_context_token="mctx_synthetic_token_000000000000",
                interaction_ref="interaction_synthetic_0001",
                client_turn_id=CLIENT_TURN_ID,
                source_turn_ref=source_ref,
                session=invalid_session,
            )
