"""Actual receiver/DB guards with synthetic accounts; no external credentials."""

import copy
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes.agent import get_agent_authority_settings
from app.routes.session_prep_review import session_prep_review_api
from app.services.provider_question_context import ProviderQuestionSourcesOnlyResult
from app.services.reviewed_excerpt_citations import enumeration_digest, render_quotes
from app.services.session_prep_note_sources import note_content_sha256

from activemodel.session_manager import get_session
from app.models.authority_conversation import CanonicalConversationFinalPhase as Final
from app.models.visit_audio_file import VisitAudioFile
from sqlalchemy import text, update
from sqlmodel import select

from tests.factories import DoctorFactory
from tests.services.provider_question_context_test import (
    AUTHORITY_SETTINGS,
    _chat,
    _client,
    _headers,
    _read,
)
from tests.services.provider_question_context_test import scope as _scope_fixture

scope = _scope_fixture


def final_body(response, *, section=0):
    result = ProviderQuestionSourcesOnlyResult.model_validate(response["result"])
    assert result.current_through is not None
    source = result.reviewed_sources[0]
    title, quote = list(source.sections.items())[section]
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
            "excerpts": [{"ordinal": 1, "source_index": 0, "section_index": section}],
        },
    }


def write(client, token, turn, body):
    return client.post(
        "/service/v1/agent/conversation-turns/write",
        headers=_headers(token, turn, "final"),
        json=body,
    )


def read(client, token, turn, version="1.1.0"):
    path = "/service/v1/agent/conversation-turns/read" + (
        "-with-citations" if version == "1.1.0" else ""
    )
    return client.post(
        path,
        headers={"X-Cosmaneura-Mount-Context": token},
        json={
            "schema_version": version,
            "mode": "turn",
            "client_turn_id": str(turn[0]),
        },
    )


def browser(doctor, *, view_as=False):
    app = FastAPI()
    app.include_router(session_prep_review_api)
    app.dependency_overrides[get_agent_authority_settings] = lambda: AUTHORITY_SETTINGS

    @app.middleware("http")
    async def synthetic_authenticated_state(request, call_next):
        request.state.doctor = doctor
        request.scope["session"] = (
            {"view_as_session_id": "synthetic-view"} if view_as else {}
        )
        return await call_next(request)

    return TestClient(app)


def accepted(scope):
    # This is an owned synthetic web-upload source, matching the real edit gate.
    VisitAudioFile(
        doctor_id=scope[0].id,
        patient_id=scope[1].patient_id,
        appointment_id=scope[2].appointment_id,
        azure_file_name="fictional-retained-citation.wav",
    ).save()
    token, client = scope[3], _client()
    turn = _chat(token)
    response = _read(client, token, turn, "1.2.0")
    assert response.status_code == 200, response.text
    assert response.headers.get_list("x-cosmaneura-reviewed-excerpts") == ["1.0.0"]
    body = final_body(response.json())
    saved = write(client, token, turn, body)
    assert saved.status_code == 201, saved.text
    hydrated = read(client, token, turn)
    assert hydrated.status_code == 200, hydrated.text
    citation = hydrated.json()["history"]["messages"][1]["reviewed_citations"][0]
    final_ref, ordinal = citation["citation_ref"].rsplit(".", 1)
    return (
        client,
        token,
        turn,
        body,
        citation,
        f"/session-prep/citations/{final_ref}/{ordinal}",
    )


def correct_uploaded_source(scope, fields):
    response = browser(scope[0]).post(
        f"/notes/revisions/{scope[2].id}/source-correction",
        json={
            "expected_content_sha256": note_content_sha256(scope[2].note),
            "fields": fields,
        },
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["note_id"] != str(scope[2].id)
    return response.json()["note_id"]


def test_actual_atomic_acceptance_replay_and_opt_in_hydration_preserve_legacy(scope):
    client, token, turn, body, citation, url = accepted(scope)
    assert write(client, token, turn, copy.deepcopy(body)).status_code == 201
    assert Final.count() == 1
    reread = read(client, token, turn)
    assert reread.json()["history"]["messages"][1]["reviewed_citations"] == [citation]
    legacy = read(client, token, turn, "1.0.0")
    assert legacy.status_code == 200 and legacy.json()["schema_version"] == "1.0.0"
    assert all(
        "reviewed_citations" not in message
        for message in legacy.json()["history"]["messages"]
    )
    opened = browser(scope[0]).get(url)
    assert opened.status_code == 200, opened.text
    assert opened.headers["cache-control"] == "no-store"
    assert {part["title"]: part["text"] for part in opened.json()["sections"]} == scope[
        2
    ].note
    assert opened.json()["citation"] == citation


def test_exact_retained_review_survives_supersession_without_rebinding(scope):
    client, token, turn, body, citation, url = accepted(scope)
    before = browser(scope[0]).get(url).json()
    changed = {
        **scope[2].note,
        "Labs": "Fictional amended lab status; no completed result.",
    }
    correct_uploaded_source(scope, changed)
    after = browser(scope[0]).get(url)
    assert after.status_code == 200, after.text
    assert after.json() == before
    assert after.json()["citation"] == citation
    assert write(client, token, turn, body).status_code == 409
    history = read(client, token, turn)
    assert history.json()["turns"][0]["state"] == "stale"
    assert len(history.json()["history"]["messages"]) == 1


def test_other_account_and_read_only_impersonation_cannot_open_retained_quote(scope):
    _, _, _, _, _, url = accepted(scope)
    assert browser(DoctorFactory.build().save()).get(url).status_code == 404
    assert browser(scope[0], view_as=True).get(url).status_code == 403
    assert browser(scope[0]).get(url.rsplit("/", 1)[0] + "/2").status_code == 404


@pytest.mark.parametrize(
    "change",
    [
        "refused",
        "unavailable",
        "forged_identity",
        "wrong_section",
        "incomplete_quote",
        "duplicate_key",
    ],
)
def test_invalid_final_cannot_leave_an_accepted_identity_map(scope, change):
    token, client = scope[3], _client()
    turn = _chat(token)
    response = _read(client, token, turn, "1.2.0").json()
    body = final_body(response)
    if change in {"refused", "unavailable"}:
        body["outcome"] = change
    elif change == "forged_identity":
        body["reviewed_excerpts"]["excerpts"][0]["note_ref"] = "visit_note_forged"
    elif change == "wrong_section":
        body["reviewed_excerpts"]["excerpts"][0]["section_index"] = 63
    elif change == "incomplete_quote":
        body["response"]["content"] += " Take 99 mg."
    if change == "duplicate_key":
        import json

        raw = json.dumps(body).replace('"ordinal": 1', '"ordinal": 1, "ordinal": 2')
        denied = client.post(
            "/service/v1/agent/conversation-turns/write",
            headers={
                **_headers(token, turn, "final"),
                "Content-Type": "application/json",
            },
            content=raw,
        )
    else:
        denied = write(client, token, turn, body)
    assert denied.status_code == 422, denied.text
    assert Final.count() == 0


def test_orm_cannot_mutate_the_accepted_map(scope):
    accepted(scope)
    with get_session() as session:
        final = session.exec(select(Final)).one()
        final.reviewed_excerpt_bindings = None
        with pytest.raises(RuntimeError, match="append-only"):
            session.flush()
        session.rollback()


def test_forged_stored_binding_cannot_hydrate_or_open(scope):
    client, token, turn, _, _, url = accepted(scope)
    # Explicit test-owner corruption only; restore the normal append-only trigger
    # in finally. No ordinary caller receives this test authority.
    with get_session() as session:
        final = session.exec(select(Final)).one()
        value = copy.deepcopy(final.reviewed_excerpt_bindings)
        assert value is not None
        value["excerpts"][0]["content_sha256"] = "f" * 64
        session.execute(
            text(
                "ALTER TABLE canonical_conversation_final_phase DISABLE TRIGGER canonical_conversation_final_phase_append_only"
            )
        )
        try:
            session.execute(
                update(Final)
                .where(cast(Any, Final.id) == final.id)
                .values(reviewed_excerpt_bindings=value)
            )
        finally:
            session.execute(
                text(
                    "ALTER TABLE canonical_conversation_final_phase ENABLE TRIGGER canonical_conversation_final_phase_append_only"
                )
            )
        session.commit()
    assert read(client, token, turn).json()["turns"][0]["state"] == "stale"
    assert browser(scope[0]).get(url).status_code == 404


def test_same_day_same_quote_actual_source_actions_stay_distinct(scope):
    from datetime import timedelta

    from app.services.session_prep_note_sources import finalize_note_for_session_prep

    from app.models.appointment import Appointment
    from app.models.patient import Patient

    from tests.services.session_prep_note_sources_test import _note
    from tests.services.session_prep_runtime_test import NOW

    doctor, target, baseline, token, _ = scope
    patient = Patient.get(id=target.patient_id)
    assert patient is not None
    other = _note(
        doctor,
        patient,
        content={
            **baseline.note,
            "Labs": "Fictional later laboratory documentation gap.",
        },
    )
    original_visit = Appointment.get(id=baseline.appointment_id)
    other_visit = Appointment.get(id=other.appointment_id)
    assert original_visit and other_visit
    other_visit.appointment_date = original_visit.appointment_date.replace(
        hour=12, minute=10, second=0, microsecond=0
    )
    other_visit.save()
    with get_session() as session:
        finalize_note_for_session_prep(
            session=session,
            doctor_id=doctor.id,
            note_id=other.id,
            expected_content_sha256=note_content_sha256(other.note),
            observed_at=NOW - timedelta(hours=1),
        )
        session.commit()
    client, turn = _client(), _chat(token)
    response = _read(client, token, turn, "1.2.0").json()
    result = ProviderQuestionSourcesOnlyResult.model_validate(response["result"])
    assert len(result.reviewed_sources) == 2
    body = final_body(response)
    quotes = [
        (
            next(iter(source.sections.values())),
            source.source_at,
            next(iter(source.sections)),
        )
        for source in result.reviewed_sources
    ]
    assert result.current_through is not None
    body["response"]["content"] = render_quotes(
        quotes, mode="selected", source_count=2, current_through=result.current_through
    )
    body["reviewed_excerpts"]["excerpts"] = [
        {"ordinal": index + 1, "source_index": index, "section_index": 0}
        for index in range(2)
    ]
    assert write(client, token, turn, body).status_code == 201
    cited = read(client, token, turn).json()["history"]["messages"][1][
        "reviewed_citations"
    ]
    assert cited[0]["citation_ref"] != cited[1]["citation_ref"]
    assert cited[0]["source_at"][:10] == cited[1]["source_at"][:10]
    for reference, source in zip(cited, result.reviewed_sources, strict=True):
        final_ref, ordinal = reference["citation_ref"].rsplit(".", 1)
        opened = browser(doctor).get(f"/session-prep/citations/{final_ref}/{ordinal}")
        assert opened.status_code == 200, opened.text
        assert {
            section["title"]: section["text"] for section in opened.json()["sections"]
        } == source.sections
