"""Real canonical/record guards for display-only retained synthetic answers."""

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from app.services import authority_conversation as conversation
from app.services import authority_conversation_read as history
from app.services.session_prep_note_corrections import correct_source_note
from app.services.session_prep_note_sources import note_content_sha256

from activemodel.session_manager import get_session
from app.models.appointment import Appointment
from app.models.authority import AuthorityIdempotencyRecord
from app.models.authority_conversation import CanonicalConversationFinalPhase as Final
from app.models.authority_conversation import CanonicalConversationTurn as Turn
from app.models.patient import Patient
from sqlalchemy import update
from sqlmodel import select

from tests.services.authority_conversation_read_test import memory
from tests.services.provider_question_context_test import _chat, _client, _read
from tests.services.provider_question_context_test import scope as _scope_fixture
from tests.services.reviewed_excerpt_citation_display_test import (
    accepted,
    browser,
    final_body,
    read,
    write,
)

scope = _scope_fixture


def retained(client, token, turn=None, **kwargs):
    return client.post(
        "/service/v1/agent/conversation-turns/read-retained",
        headers={"X-Cosmaneura-Mount-Context": token},
        json={
            "schema_version": "1.2.0",
            "mode": "turn" if turn else "recent",
            "client_turn_id": str(turn[0]) if turn else None,
        },
        **kwargs,
    )


def test_accepted_snapshot_hydration_preserves_original_answer_dates_and_map(scope):
    client, token, turn, body, citation, _ = accepted(scope)
    before = read(client, token, turn).json()["history"]["messages"][1]
    result = retained(client, token, turn)
    assert result.status_code == 200, result.text
    assert result.headers["cache-control"] == "no-store"
    assert result.json()["schema_version"] == "1.2.0"
    user, answer = result.json()["history"]["messages"]
    assert user["reviewed_citations"] is None and user["display_state"] is None
    assert answer["content"] == body["response"]["content"] == before["content"]
    assert answer["recorded_at"] == before["recorded_at"]
    assert answer["reviewed_citations"] == [citation]
    assert answer["display_state"] == "historical"
    assert result.json()["turns"][0]["state"] == "completed"
    assert all("url" not in item for item in answer["reviewed_citations"])


def test_retained_display_survives_correction_but_never_becomes_inference_memory(scope):
    client, token, turn, body, citation, _ = accepted(scope)
    before = retained(client, token, turn).json()["history"]["messages"][1]
    with get_session() as session:
        replacement = correct_source_note(
            session=cast(Any, session),
            doctor=scope[0],
            note_id=scope[2].id,
            expected_content_sha256=note_content_sha256(scope[2].note),
            fields={**scope[2].note, "Labs": "Fictional corrected canceled lab order."},
        )
        assert replacement.id != scope[2].id
        session.commit()
    result = retained(client, token, turn)
    assert result.status_code == 200, result.text
    answer = result.json()["history"]["messages"][1]
    assert answer == {**before, "display_state": "superseded"}
    assert answer["reviewed_citations"] == [citation]
    assert answer["content"] == body["response"]["content"]
    for version in ("1.0.0", "1.1.0"):
        current = read(client, token, turn, version).json()
        assert current["turns"][0]["state"] == "stale"
        assert [message["role"] for message in current["history"]["messages"]] == [
            "user"
        ]
    assert memory(client, token, _chat(token)).json()["result"]["messages"] == []


@pytest.mark.parametrize(
    ("failure", "status"),
    [
        (history.SessionPrepSourceBusy, 503),
        (conversation.ConversationInvariantError, 404),
    ],
)
def test_retained_source_opening_returns_closed_busy_or_integrity_error(
    scope, monkeypatch, failure, status
):
    from app.services import authority_conversation_display as display

    _, _, _, _, citation, _ = accepted(scope)

    def denied(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(display, "_retained_source_fence", denied)
    response = browser(scope[0]).get(
        "/session-prep/citations/" + citation["citation_ref"].replace(".", "/")
    )
    assert response.status_code == status, response.text
    assert "sections" not in response.json()


@pytest.mark.parametrize(
    "operation",
    [
        conversation.USER_ACCEPTED_OPERATION,
        conversation.FINAL_RESPONSE_OPERATION,
        history.prep.OPERATION,
    ],
)
@pytest.mark.parametrize("mutation", ["missing", "expired", "digest", "response_ref"])
def test_missing_expired_or_corrupt_receipts_cannot_restore_an_answer(
    scope, operation, mutation
):
    client, token, turn, _, _, _ = accepted(scope)
    with get_session() as session:
        anchor = session.get(Turn, turn[2])
        assert anchor is not None
        receipt = session.exec(
            select(AuthorityIdempotencyRecord).where(
                AuthorityIdempotencyRecord.operation == operation,
                AuthorityIdempotencyRecord.interaction_id == anchor.interaction_id,
            )
        ).one()
        if mutation == "missing":
            session.delete(receipt)
        elif mutation == "expired":
            receipt.retain_until = datetime.now(UTC) - timedelta(seconds=1)
        elif mutation == "digest":
            receipt.request_digest = "f" * 64
        else:
            receipt.response_ref = "authority_idem_wrong_reference"
        session.commit()
    result = retained(client, token, turn)
    assert result.status_code in {200, 404, 409}, result.text
    if result.status_code == 200:
        assert all(
            message["role"] != "assistant"
            for message in result.json()["history"]["messages"]
        )


def test_current_real_patient_ownership_is_required_for_retained_display(scope):
    from tests.factories import DoctorFactory

    client, token, turn, _, _, _ = accepted(scope)
    other = DoctorFactory.build().save()
    with get_session() as session:
        session.execute(
            update(Patient)
            .where(
                cast(Any, Patient.id) == scope[1].patient_id,
            )
            .values(doctor_id=other.id)
        )
        session.commit()
    result = retained(client, token, turn)
    assert result.status_code == 403, result.text
    assert "history" not in result.json()


def test_unselected_source_cannot_change_patient_while_retaining_a_selected_quote(
    scope,
):
    from app.services.session_prep_note_sources import finalize_note_for_session_prep

    from tests.factories import PatientFactory
    from tests.services.session_prep_note_sources_test import _note

    token, client = scope[3], _client()
    original_patient = Patient.get(id=scope[1].patient_id)
    extra = _note(scope[0], original_patient, days_ago=2)
    with get_session() as session:
        finalize_note_for_session_prep(
            session=cast(Any, session),
            doctor_id=scope[0].id,
            note_id=extra.id,
            expected_content_sha256=note_content_sha256(extra.note),
            observed_at=datetime.now(UTC) - timedelta(hours=1),
        )
        session.commit()
    turn = _chat(token)
    context = _read(client, token, turn, "1.2.0")
    assert context.status_code == 200, context.text
    sources = context.json()["result"]["reviewed_sources"]
    assert len(sources) == 2 and sources[1]["source_ref"] == str(extra.id)
    body = final_body(context.json())
    assert write(client, token, turn, body).status_code == 201
    assert (
        retained(client, token, turn).json()["history"]["messages"][1]["content"]
        == body["response"]["content"]
    )
    other_patient = PatientFactory.build(doctor_id=scope[0].id).save()
    with get_session() as session:
        session.execute(
            update(Appointment)
            .where(
                cast(Any, Appointment.id) == extra.appointment_id,
            )
            .values(patient_id=other_patient.id)
        )
        session.commit()
    result = retained(client, token, turn)
    assert result.status_code in {200, 409}, result.text
    if result.status_code == 200:
        assert [
            message["role"] for message in result.json()["history"]["messages"]
        ] == ["user"]


def test_original_history_witness_is_integrity_only_and_preserves_empty_members(scope):
    token, client = scope[3], _client()
    turn = _chat(token)
    context = _read(client, token, turn, "1.2.0")
    assert context.status_code == 200, context.text
    original_memory = memory(client, token, turn)
    assert original_memory.status_code == 200, original_memory.text
    assert original_memory.json()["result"]["messages"] == []
    body = final_body(context.json())
    body["history_context_ref"] = original_memory.json()["tool_call_ref"]
    assert write(client, token, turn, body).status_code == 201
    result = retained(client, token, turn)
    assert result.status_code == 200, result.text
    assert (
        result.json()["history"]["messages"][1]["content"]
        == body["response"]["content"]
    )
    with get_session() as session:
        receipt = session.exec(
            select(AuthorityIdempotencyRecord).where(
                AuthorityIdempotencyRecord.operation == history.OPERATION,
            )
        ).one()
        receipt.request_digest = "f" * 64
        session.commit()
    result = retained(client, token, turn)
    assert result.status_code in {200, 409}, result.text
    if result.status_code == 200:
        assert [
            message["role"] for message in result.json()["history"]["messages"]
        ] == ["user"]


def test_exact_nonempty_history_and_inherited_source_survive_only_for_display(scope):
    client, token, first, _, _, _ = accepted(scope)
    second = _chat(token)
    context = _read(client, token, second, "1.2.0")
    original_memory = memory(client, token, second)
    assert context.status_code == original_memory.status_code == 200
    assert len(original_memory.json()["result"]["messages"]) == 2
    body = final_body(context.json())
    body["history_context_ref"] = original_memory.json()["tool_call_ref"]
    assert write(client, token, second, body).status_code == 201
    before = retained(client, token).json()["history"]["messages"]
    assert len(before) == 4
    with get_session() as session:
        correct_source_note(
            session=cast(Any, session),
            doctor=scope[0],
            note_id=scope[2].id,
            expected_content_sha256=note_content_sha256(scope[2].note),
            fields={**scope[2].note, "Labs": "Fictional order corrected; no result."},
        )
        session.commit()
    restored = retained(client, token)
    assert restored.status_code == 200, restored.text
    after = restored.json()["history"]["messages"]
    assert after == [
        {**message, "display_state": "superseded"}
        if message["role"] == "assistant"
        else message
        for message in before
    ]
    for turn in (first, second):
        assert read(client, token, turn).json()["turns"][0]["state"] == "stale"
    assert memory(client, token, _chat(token)).json()["result"]["messages"] == []


def test_read_retained_rejects_legacy_versions_extra_fields_and_query_parameters(scope):
    client, token, turn, _, _, _ = accepted(scope)
    path = "/service/v1/agent/conversation-turns/read-retained"
    body = {"schema_version": "1.2.0", "mode": "turn", "client_turn_id": str(turn[0])}
    for changed in (
        {**body, "schema_version": "1.1.0"},
        {**body, "url": "https://example.test"},
    ):
        response = client.post(
            path, headers={"X-Cosmaneura-Mount-Context": token}, json=changed
        )
        assert response.status_code == 422, response.text
    response = client.post(
        path + "?source=current",
        headers={"X-Cosmaneura-Mount-Context": token},
        json=body,
    )
    assert response.status_code == 422, response.text
    assert Final.count() == 1
