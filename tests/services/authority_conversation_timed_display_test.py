"""Synthetic real-route receipt deadlines; no model, vendor or live identity calls."""

import copy
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from pydantic import ValidationError

from app.services import authority_conversation as conversation
from app.services import authority_conversation_read as history
from app.services.authority_conversation_timed_display import (
    TimedConversationReadResponse,
)

from activemodel.session_manager import get_session
from app.models.authority import AuthorityIdempotencyRecord
from app.models.authority_conversation import CanonicalConversationTurn as Turn
from app.models.patient import Patient
from sqlalchemy import update
from sqlmodel import select

from tests.factories import DoctorFactory
from tests.services.authority_conversation_display_test import retained
from tests.services.authority_conversation_read_test import final, memory
from tests.services.provider_question_context_test import _chat, _client, _read
from tests.services.provider_question_context_test import scope as _scope_fixture
from tests.services.reviewed_excerpt_citation_display_test import (
    accepted,
    correct_uploaded_source,
    final_body,
    read,
    write,
)

scope = _scope_fixture
PATH = "/service/v1/agent/conversation-turns/read-display-timed"


def timed(client, token, turn):
    return client.post(
        PATH,
        headers={"X-Cosmaneura-Mount-Context": token},
        json={
            "schema_version": "1.3.0",
            "mode": "turn",
            "client_turn_id": str(turn[0]),
        },
    )


def set_deadline(turn, operation, *, expired=False):
    # Only this test's real persisted synthetic receipt is changed. The actual
    # final/context hashes, provider records and normal migrations are retained.
    deadline = datetime.now(UTC) + timedelta(minutes=-1 if expired else 5)
    with get_session() as session:
        anchor = session.get(Turn, turn[2])
        assert anchor is not None
        receipt = session.exec(
            select(AuthorityIdempotencyRecord).where(
                AuthorityIdempotencyRecord.interaction_id == anchor.interaction_id,
                AuthorityIdempotencyRecord.operation == operation,
            )
        ).one()
        receipt.retain_until = deadline
        session.commit()
    return deadline


def receipt_snapshot():
    with get_session() as session:
        return sorted(
            (
                str(receipt.id),
                history._utc(receipt.retain_until).isoformat(),
                receipt.request_digest,
                receipt.response_ref,
            )
            for receipt in session.exec(select(AuthorityIdempotencyRecord)).all()
        )


def parsed(response):
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    result = response.json()
    assert result["schema_version"] == "1.3.0"
    TimedConversationReadResponse.model_validate(result)
    user, answer = result["history"]["messages"]
    assert user["role"] == "user" and user["expires_at"] is None
    assert answer["role"] == "assistant"
    observed = datetime.fromisoformat(result["observed_at"])
    expiry = datetime.fromisoformat(answer["expires_at"])
    assert observed.utcoffset() == expiry.utcoffset() == timedelta(0)
    assert datetime.fromisoformat(answer["recorded_at"]) <= observed < expiry
    return result, answer, expiry


@pytest.mark.parametrize(
    "operation",
    [
        conversation.USER_ACCEPTED_OPERATION,
        conversation.FINAL_RESPONSE_OPERATION,
        history.prep.OPERATION,
    ],
    ids=["user", "final", "provider"],
)
def test_timed_answer_uses_earliest_actual_phase_or_provider_receipt(scope, operation):
    client, token, turn, body, citation, _ = accepted(scope)
    deadline = set_deadline(turn, operation)
    before = receipt_snapshot()
    _, answer, expiry = parsed(timed(client, token, turn))
    assert expiry == deadline
    assert expiry < datetime.fromisoformat(answer["recorded_at"]) + timedelta(hours=24)
    assert answer["content"] == body["response"]["content"]
    assert answer["reviewed_citations"] == [citation]
    assert receipt_snapshot() == before


def mapped_followup(client, token):
    turn = _chat(token)
    context = _read(client, token, turn, "1.2.0")
    assert context.status_code == 200, context.text
    previous = memory(client, token, turn)
    assert previous.status_code == 200, previous.text
    body = final_body(context.json())
    body["history_context_ref"] = previous.json()["tool_call_ref"]
    saved = write(client, token, turn, body)
    assert saved.status_code == 201, saved.text
    return turn


@pytest.mark.parametrize(
    "earliest", ["history", "member_user", "member_final", "inherited_provider"]
)
def test_exact_retained_history_members_and_inherited_source_bound_expiry(
    scope, earliest
):
    client, token, previous, _, _, _ = accepted(scope)
    current = mapped_followup(client, token)
    subject, operation = {
        "history": (current, history.OPERATION),
        "member_user": (previous, conversation.USER_ACCEPTED_OPERATION),
        "member_final": (previous, conversation.FINAL_RESPONSE_OPERATION),
        "inherited_provider": (previous, history.prep.OPERATION),
    }[earliest]
    deadline = set_deadline(subject, operation)
    before = receipt_snapshot()
    _, _, expiry = parsed(timed(client, token, current))
    assert expiry == deadline
    assert receipt_snapshot() == before


def test_transitive_history_member_deadline_is_not_reset_by_a_later_answer(scope):
    client, token, _, _, _, _ = accepted(scope)
    middle = _chat(token)
    context = memory(client, token, middle)
    assert context.status_code == 200, context.text
    assert (
        final(client, token, middle, mem=context.json()["tool_call_ref"]).status_code
        == 201
    )
    current = mapped_followup(client, token)
    deadline = set_deadline(middle, history.OPERATION)
    _, _, expiry = parsed(timed(client, token, current))
    assert expiry == deadline


@pytest.mark.parametrize("missing", [False, True], ids=["expired", "missing"])
def test_missing_or_expired_inherited_provider_receipt_cannot_extend_display(
    scope, missing
):
    client, token, previous, _, _, _ = accepted(scope)
    current = mapped_followup(client, token)
    if missing:
        with get_session() as session:
            anchor = session.get(Turn, previous[2])
            assert anchor is not None
            receipt = session.exec(
                select(AuthorityIdempotencyRecord).where(
                    AuthorityIdempotencyRecord.interaction_id == anchor.interaction_id,
                    AuthorityIdempotencyRecord.operation == history.prep.OPERATION,
                )
            ).one()
            session.delete(receipt)
            session.commit()
    else:
        set_deadline(previous, history.prep.OPERATION, expired=True)
    before = receipt_snapshot()
    response = timed(client, token, current)
    assert response.status_code == 200, response.text
    assert [message["role"] for message in response.json()["history"]["messages"]] == [
        "user"
    ]
    assert response.json()["turns"][0]["state"] == "stale"
    assert receipt_snapshot() == before


def test_corrected_retained_answer_preserves_original_expiry_and_current_read_refusal(
    scope,
):
    client, token, turn, body, citation, _ = accepted(scope)
    deadline = set_deadline(turn, history.prep.OPERATION)
    _, old_answer, _ = parsed(timed(client, token, turn))
    correct_uploaded_source(
        scope, {**scope[2].note, "Labs": "Fictional corrected canceled lab; no result."}
    )
    _, answer, expiry = parsed(timed(client, token, turn))
    assert expiry == deadline
    assert answer == {**old_answer, "display_state": "superseded"}
    assert answer["content"] == body["response"]["content"]
    assert answer["reviewed_citations"] == [citation]
    for version in ("1.0.0", "1.1.0"):
        projection = read(client, token, turn, version).json()
        assert projection["turns"][0]["state"] == "stale"
        assert [message["role"] for message in projection["history"]["messages"]] == [
            "user"
        ]


def test_unchanged_null_map_legacy_answer_remains_queryable_without_fabricated_links(
    scope,
):
    token, client = scope[3], _client()
    turn = _chat(token)
    assert final(client, token, turn).status_code == 201
    deadline = set_deadline(turn, conversation.FINAL_RESPONSE_OPERATION)
    _, answer, expiry = parsed(timed(client, token, turn))
    assert answer["content"] == "Fictional accepted answer."
    assert (
        answer["reviewed_citations"] == [] and answer["display_state"] == "historical"
    )
    assert expiry == deadline


def test_timed_read_does_not_change_three_old_closed_projections_or_renew_receipts(
    scope,
):
    client, token, turn, _, _, _ = accepted(scope)
    old = [read(client, token, turn, version).json() for version in ("1.0.0", "1.1.0")]
    old.append(retained(client, token, turn).json())
    before = receipt_snapshot()
    parsed(timed(client, token, turn))
    after = [
        read(client, token, turn, version).json() for version in ("1.0.0", "1.1.0")
    ]
    after.append(retained(client, token, turn).json())
    assert after == old
    assert all("observed_at" not in value for value in after)
    assert all(
        "expires_at" not in message
        for value in after
        for message in value["history"]["messages"]
    )
    assert receipt_snapshot() == before


def test_timed_metadata_does_not_grant_access_after_patient_ownership_changes(scope):
    client, token, turn, _, _, _ = accepted(scope)
    other = DoctorFactory.build().save()
    with get_session() as session:
        session.execute(
            update(Patient)
            .where(cast(Any, Patient.id) == scope[1].patient_id)
            .values(doctor_id=other.id)
        )
        session.commit()
    response = timed(client, token, turn)
    assert response.status_code == 403, response.text
    assert "observed_at" not in response.json() and "history" not in response.json()


@pytest.mark.parametrize(
    ("path", "version"),
    [
        (PATH, "1.0.0"),
        (PATH, "1.1.0"),
        (PATH, "1.2.0"),
        ("/service/v1/agent/conversation-turns/read", "1.3.0"),
        ("/service/v1/agent/conversation-turns/read-with-citations", "1.3.0"),
        ("/service/v1/agent/conversation-turns/read-retained", "1.3.0"),
    ],
    ids=["timed-1.0", "timed-1.1", "timed-1.2", "old-1.0", "old-1.1", "old-1.2"],
)
def test_timed_opt_in_cannot_change_any_old_request_version(scope, path, version):
    response = _client().post(
        path,
        headers={"X-Cosmaneura-Mount-Context": scope[3]},
        json={"schema_version": version, "mode": "recent", "client_turn_id": None},
    )
    assert response.status_code == 422, response.text
    assert "history" not in response.json()


def test_client_cannot_supply_a_display_deadline_or_observation(scope):
    response = _client().post(
        PATH,
        headers={"X-Cosmaneura-Mount-Context": scope[3]},
        json={
            "schema_version": "1.3.0",
            "mode": "recent",
            "client_turn_id": None,
            "observed_at": "2026-11-01T12:00:00Z",
            "expires_at": "2026-11-02T12:00:00Z",
        },
    )
    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    "invalid",
    [
        "naive_observation",
        "nonutc_observation",
        "missing_expiry",
        "expired",
        "future_recorded",
    ],
)
def test_timed_contract_cannot_serialize_unbounded_or_invalid_time_metadata(
    scope, invalid
):
    client, token, turn, _, _, _ = accepted(scope)
    result, _, _ = parsed(timed(client, token, turn))
    damaged = copy.deepcopy(result)
    if invalid == "naive_observation":
        damaged["observed_at"] = "2026-11-01T12:00:00"
    elif invalid == "nonutc_observation":
        damaged["observed_at"] = "2026-11-01T12:00:00-05:00"
    elif invalid == "missing_expiry":
        damaged["history"]["messages"][1]["expires_at"] = None
    elif invalid == "expired":
        damaged["history"]["messages"][1]["expires_at"] = damaged["observed_at"]
    else:
        damaged["history"]["messages"][1]["recorded_at"] = (
            datetime.fromisoformat(damaged["observed_at"]) + timedelta(seconds=1)
        ).isoformat()
    with pytest.raises(ValidationError):
        TimedConversationReadResponse.model_validate(damaged)
