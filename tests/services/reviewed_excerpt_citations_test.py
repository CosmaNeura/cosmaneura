"""Real admission and immutable witness regressions with synthetic accounts."""

import copy
from datetime import UTC, datetime
from typing import cast

import pytest

from app.services.provider_question_context import (
    ProviderQuestionSourcesOnlyResult,
    ProviderQuestionSourcesResult,
)
from app.services.reviewed_excerpt_citations import (
    CitationBindings,
    enumeration_digest,
    render_quotes,
    retained_context_result,
)
from app.services.session_prep_note_corrections import correct_source_note
from app.services.session_prep_note_sources import note_content_sha256

from activemodel.session_manager import get_session
from app.models.authority_conversation import CanonicalConversationFinalPhase as Final
from app.models.authority_conversation import CanonicalConversationTurn as Turn
from app.models.visit_audio_file import VisitAudioFile
from sqlmodel import Session, select

from tests.services.provider_question_context_test import (
    _chat,
    _client,
    _headers,
    _read,
)
from tests.services.provider_question_context_test import scope as _scope_fixture

scope = _scope_fixture


def final_body(response):
    schema = (
        ProviderQuestionSourcesResult
        if response["result"]["schema_version"] == "1.1.0"
        else ProviderQuestionSourcesOnlyResult
    )
    result = schema.model_validate(response["result"])
    assert result.current_through is not None
    source = result.reviewed_sources[0]
    title, quote = next(iter(source.sections.items()))
    return {
        "phase": "final_response",
        "outcome": "completed",
        "provider_context_ref": response["tool_call_ref"],
        "response": {
            "content_type": "text/plain",
            "content": render_quotes(
                [(quote, source.source_at, title)],
                mode="selected",
                source_count=len(result.reviewed_sources),
                current_through=result.current_through,
            ),
        },
        "presentation": {"mode": "standard", "suppress": [], "human_contact": None},
        "reviewed_excerpts": {
            "schema_version": "1.0.0",
            "enumeration_sha256": enumeration_digest(result.reviewed_sources),
            "mode": "selected",
            "excerpts": [{"ordinal": 1, "source_index": 0, "section_index": 0}],
        },
    }


def write(client, token, turn, body):
    return client.post(
        "/service/v1/agent/conversation-turns/write",
        headers=_headers(token, turn, "final"),
        json=body,
    )


def accepted(scope):
    VisitAudioFile(
        doctor_id=scope[0].id,
        patient_id=scope[1].patient_id,
        appointment_id=scope[2].appointment_id,
        azure_file_name="fictional.wav",
    ).save()
    token, client = scope[3], _client()
    turn = _chat(token)
    response = _read(client, token, turn, "1.2.0")
    assert response.status_code == 200
    assert response.headers.get_list("x-cosmaneura-reviewed-excerpts") == ["1.0.0"]
    body = final_body(response.json())
    saved = write(client, token, turn, body)
    assert saved.status_code == 201, saved.text
    return client, token, turn, body, response.json()["result"]


def reconstruct(session, record):
    final = session.exec(select(Final)).one()
    turn = session.get(Turn, final.turn_id)
    return retained_context_result(
        session=session,
        scope=record,
        turn=turn,
        final=final,
        bindings=CitationBindings.model_validate(final.reviewed_excerpt_bindings),
        now=datetime.now(UTC),
    )


def test_atomic_full_witness_replay_and_unchanged_legacy_read(scope):
    client, token, turn, body, result = accepted(scope)
    assert write(client, token, turn, copy.deepcopy(body)).status_code == 201
    assert Final.count() == 1
    with get_session() as session:
        assert reconstruct(session, scope[4]).model_dump(mode="json") == result
        final = session.exec(select(Final)).one()
        witness = CitationBindings.model_validate(final.reviewed_excerpt_bindings)
        assert len(witness.context_witness.sources) == len(result["reviewed_sources"])
        assert "sections" not in str(witness.context_witness.model_dump())
    read = client.post(
        "/service/v1/agent/conversation-turns/read",
        headers={"X-Cosmaneura-Mount-Context": token},
        json={
            "schema_version": "1.0.0",
            "mode": "turn",
            "client_turn_id": str(turn[0]),
        },
    )
    assert read.status_code == 200 and read.json()["schema_version"] == "1.0.0"
    assert all(
        "reviewed_citations" not in item for item in read.json()["history"]["messages"]
    )


def test_retained_witness_reconstructs_original_after_source_correction(scope):
    client, token, turn, body, result = accepted(scope)
    with get_session() as managed:
        correct_source_note(
            session=cast(Session, managed),
            doctor=scope[0],
            note_id=scope[2].id,
            expected_content_sha256=note_content_sha256(scope[2].note),
            fields={**scope[2].note, "Labs": "Fictional amended lab status."},
        )
        managed.commit()
    with get_session() as session:
        assert reconstruct(session, scope[4]).model_dump(mode="json") == result
    assert write(client, token, turn, body).status_code == 409


def test_caller_cannot_supply_internal_witness_or_alter_retained_map(scope):
    token, client, turn = scope[3], _client(), _chat(scope[3])
    response = _read(client, token, turn, "1.2.0")
    body = final_body(response.json())
    body["reviewed_excerpts"]["context_witness"] = {"permission": "invented"}
    assert write(client, token, turn, body).status_code == 422
    assert Final.count() == 0
    body["reviewed_excerpts"].pop("context_witness")
    assert write(client, token, turn, body).status_code == 201
    with get_session() as session:
        final = session.exec(select(Final)).one()
        final.reviewed_excerpt_bindings = None
        with pytest.raises(RuntimeError, match="append-only"):
            session.flush()
        session.rollback()


@pytest.mark.parametrize("association_change", [None, "retarget"])
@pytest.mark.parametrize("version", ["1.1.0", "1.2.0"])
@pytest.mark.committed_database
def test_real_matched_source_artifact_and_changed_identity_admission(
    scope, monkeypatch, association_change, version
):
    from tests.services import provider_question_context_test as existing

    captured, original_read = {}, existing._read

    def capture(client, token, turn, wire_version="1.0.0"):
        response = original_read(client, token, turn, wire_version)
        if response.status_code == 200:
            captured[str(turn[0])] = response.json()
        return response

    def selected_final(client, token, turn, reference=None, outcome="completed"):
        body = final_body(captured[str(turn[0])])
        body["provider_context_ref"], body["outcome"] = reference, outcome
        return write(client, token, turn, body)

    monkeypatch.setattr(existing, "_read", capture)
    monkeypatch.setattr(existing, "_final", selected_final)
    existing.test_explicit_scribe_patient_match_can_finalize_selected_prior_source(
        scope, association_change, version
    )


def test_original_ordered_history_members_are_retained_and_replayed(scope):
    from tests.services.authority_conversation_read_test import memory

    client, token, first, _, _ = accepted(scope)
    second = _chat(token)
    source = _read(client, token, second, "1.2.0")
    response = memory(client, token, second)
    assert response.status_code == 200
    body = final_body(source.json())
    body["history_context_ref"] = response.json()["tool_call_ref"]
    assert write(client, token, second, body).status_code == 201
    with get_session() as session:
        final = session.exec(select(Final).where(Final.turn_id == second[2])).one()
        bound = CitationBindings.model_validate(final.reviewed_excerpt_bindings)
        assert bound.history_witness is not None
        assert [member.turn_ref for member in bound.history_witness.members] == [
            first[2]
        ]
    assert write(client, token, second, body).status_code == 201


def test_unrelated_owned_reviewed_patient_is_not_in_the_witness(scope):
    from tests.factories import PatientFactory
    from tests.services.session_prep_note_sources_test import _finalize, _note

    patient = PatientFactory.build(doctor_id=scope[0].id).save()
    note = _note(scope[0], patient)
    with get_session() as session:
        _finalize(session, scope[0], note)
        session.commit()
    _, _, _, _, result = accepted(scope)
    assert str(note.id) not in {
        item["source_ref"] for item in result["reviewed_sources"]
    }
