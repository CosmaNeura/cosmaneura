"""Real follow-up API over owned synthetic evidence; no model or provider."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event
from uuid import uuid4

import pytest
from fastapi import FastAPI

import app.routes.visit_prep_interviews as route
import app.services.visit_prep_clarification_decisions as decisions
import app.services.visit_prep_clarification_view as views
from app.configuration.visit_prep import VisitPrepSettings
from app.services import visit_prep_interviews as interviews
from app.services.visit_prep_clarification_contract import (
    PatientClarificationReply,
    VisitPrepClarificationInvalid,
    append_patient_clarification,
)

from activemodel.session_manager import global_session
from app.models.practice import Practice
from app.models.visit_prep import VisitPrep
from sqlalchemy import text
from sqlmodel import select

from tests.integration.visit_prep_clarification_http_test import (
    PREFIX,
    _client,
    _headers,
    _private,
)
from tests.integration.visit_prep_clarification_turns_test import (
    WORDS,
    _counts,
    _save,
    _start,
    _submit,
)
from tests.integration.visit_prep_contribution_lock_test import _observe_waiting_lock
from tests.services.visit_prep_clarification_contract_test import _organize
from tests.services.visit_prep_contribution_commands_test import _correct
from tests.services.visit_prep_contribution_projection_test import _projection


def _read(client, fixture, role="patient", question="story"):
    return client.post(
        PREFIX + "/follow-up",
        headers=_headers(fixture),
        json=dict(first_name="José", role=role, question_key=question),
    )


def _payload(proposal, role="patient"):
    return dict(
        first_name="José",
        role=role,
        proposal_id=proposal.proposal_id,
        patient_words=WORDS,
    )


@pytest.mark.parametrize("role", ["patient", "caregiver"])
@pytest.mark.parametrize("question", ["story", "medications", "adherence"])
def test_real_prompt_save_exact_retry_and_read_recovery(
    monkeypatch, owned_rate_limit_redis, role, question
):
    fixture, _, proposal = _start(monkeypatch, role=role, question=question)
    before = _projection(fixture)
    client = _client(monkeypatch, owned_rate_limit_redis)
    first = _read(client, fixture, role, question)
    assert first.status_code == 200
    prompt = first.json()
    assert prompt["status"] == "prompt" and prompt["turns"] == []
    assert prompt["proposal_id"] == proposal.proposal_id
    key = str(uuid4())
    saved = client.post(
        PREFIX + "/turn", headers=_headers(fixture, key), json=_payload(proposal, role)
    )
    replay = client.post(
        PREFIX + "/turn", headers=_headers(fixture, key), json=_payload(proposal, role)
    )
    assert saved.status_code == replay.status_code == 200
    assert saved.json()["disposition"] == "accepted"
    assert replay.json()["disposition"] == "replayed"
    assert saved.json()["turn_id"] == replay.json()["turn_id"]
    assert WORDS not in saved.text
    resumed = _read(client, fixture, role, question)
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "answered"
    assert resumed.json()["turns"] == [
        dict(prompt=prompt["pending_prompt"], patient_words=WORDS)
    ]
    assert _counts(fixture) == (1, 1) and _projection(fixture) == before
    assert _read(client, fixture, role, question).json() == resumed.json()


@pytest.mark.parametrize(
    "change",
    ["correction", "supersede", "quarantine", "disabled", "wrong_name", "wrong_role"],
)
@pytest.mark.parametrize("suffix", ["/follow-up", "/turn"])
def test_source_recipient_and_release_denial_do_not_disclose_or_write(
    monkeypatch, owned_rate_limit_redis, change, suffix
):
    fixture, context, proposal = _start(monkeypatch)
    if change == "correction":
        _correct(fixture)
    elif change == "supersede":
        _save(fixture, context, reply=_organize(context))
    elif change == "quarantine":
        interviews.report_wrong_recipient(access_token=fixture.access_token)
    elif change == "disabled":
        monkeypatch.setattr(
            interviews.visit_prep_access, "VISIT_PREP_SETTINGS", VisitPrepSettings()
        )
    client = _client(monkeypatch, owned_rate_limit_redis)
    payload = (
        _payload(proposal)
        if suffix == "/turn"
        else dict(first_name="José", role="patient", question_key="story")
    )
    if change == "wrong_name":
        payload["first_name"] = "Wrong synthetic name"
    if change == "wrong_role":
        payload["role"] = "caregiver"
    response = client.post(
        PREFIX + suffix,
        headers=_headers(fixture, str(uuid4()) if suffix == "/turn" else None),
        json=payload,
    )
    if change in {"correction", "supersede"} and suffix == "/follow-up":
        assert (
            response.status_code == 200 and response.json()["status"] == "not_available"
        )
        assert (
            response.json()["turns"] == [] and response.json()["pending_prompt"] is None
        )
    else:
        assert response.status_code in {404, 409}
        _private(response, fixture)
        assert WORDS not in response.text
    assert _counts(fixture) == (0, 0)


@pytest.mark.parametrize("suffix", ["/follow-up", "/turn"])
@pytest.mark.parametrize("failure", ["get", "query", "extra", "large", "key"])
def test_new_paths_preserve_private_closed_http_boundary(
    monkeypatch, owned_rate_limit_redis, suffix, failure
):
    fixture, _, proposal = _start(monkeypatch)
    client = _client(monkeypatch, owned_rate_limit_redis)
    payload = (
        _payload(proposal)
        if suffix == "/turn"
        else dict(first_name="José", role="patient", question_key="story")
    )
    headers = _headers(fixture, str(uuid4()) if suffix == "/turn" else None)
    path = PREFIX + suffix
    if failure == "get":
        response = client.get(path, headers=headers)
    elif failure == "query":
        response = client.post(
            path + "?patient_words=Invented", headers=headers, json=payload
        )
    elif failure == "large":
        response = client.post(path, headers=headers, content=b"x" * 10241)
    elif failure == "extra":
        response = client.post(
            path, headers=headers, json=payload | {"context": "Invented"}
        )
    elif suffix == "/turn":
        headers["Idempotency-Key"] = "Invented"
        response = client.post(path, headers=headers, json=payload)
    else:
        response = client.post(
            path, headers=headers, json=payload | {"question_key": 42}
        )
    assert response.status_code in {404, 413, 422}
    _private(response, fixture)
    assert "Invented" not in response.text and _counts(fixture) == (0, 0)


def test_followup_read_cannot_disclose_without_audit_commit(monkeypatch):
    fixture, _, proposal = _start(monkeypatch)
    _submit(fixture, proposal)

    def lost(session):
        session.commit()
        raise VisitPrepClarificationInvalid()

    monkeypatch.setattr(views, "_commit", lost)
    with pytest.raises(VisitPrepClarificationInvalid):
        views.get_patient_followup_view(
            access_token=fixture.access_token,
            first_name="José",
            role="patient",
            question_key="story",
        )
    assert _counts(fixture) == (1, 1)


@pytest.mark.parametrize("change", ["correction", "baa"])
def test_followup_read_rechecks_source_and_access_after_actual_lock_wait(
    monkeypatch, change
):
    fixture, _, proposal = _start(monkeypatch)
    _submit(fixture, proposal)
    original = decisions._visit_prep_session
    started = Event()

    @contextmanager
    def named_session():
        with original() as session:
            session.execute(
                text("SET LOCAL application_name = 'vp_followup_read_wait'")
            )
            started.set()
            yield session

    with global_session() as session:
        session.exec(
            select(Practice).where(Practice.id == fixture.practice.id).with_for_update()
        ).one()
        session.exec(
            select(VisitPrep).where(VisitPrep.id == fixture.prep_id).with_for_update()
        ).one()
        with monkeypatch.context() as patch:
            patch.setattr(decisions, "_visit_prep_session", named_session)
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    views.get_patient_followup_view,
                    access_token=fixture.access_token,
                    first_name="José",
                    role="patient",
                    question_key="story",
                )
                try:
                    assert started.wait(5)
                    _observe_waiting_lock(session, "vp_followup_read_wait")
                    if change == "correction":
                        _correct(
                            fixture,
                            question="story",
                            answer="Synthetic waiting correction.",
                        )
                    else:
                        fixture.practice.negotiated_baa_on_file = False
                        fixture.practice.save()
                finally:
                    session.commit()
                if change == "baa":
                    with pytest.raises(VisitPrepClarificationInvalid):
                        future.result(15)
                else:
                    result = future.result(15)
                    assert result.status == "not_available" and not result.turns


def test_followup_does_not_decode_turn_before_current_recipient_authority(monkeypatch):
    fixture, _, proposal = _start(monkeypatch)
    _submit(fixture, proposal)

    def forbidden(*_args, **_kwargs):
        pytest.fail("No patient turn may be decoded before recipient authorization")

    monkeypatch.setattr(views, "read_clarification_turn", forbidden)
    with pytest.raises(VisitPrepClarificationInvalid):
        views.get_patient_followup_view(
            access_token=fixture.access_token,
            first_name="Wrong",
            role="patient",
            question_key="story",
        )


def test_closed_new_openapi_preserves_old_wording_read():
    app = FastAPI()
    app.include_router(route.visit_prep_interviews_api_app)
    schema = app.openapi()
    for suffix in ("/follow-up", "/turn"):
        operation = schema["paths"][PREFIX + suffix]
        assert set(operation) == {"post"}
        assert operation["post"]["security"] == [{"VisitPrepBearer": []}]
    request = schema["components"]["schemas"]["VisitPrepClarificationTurnRequest"]
    assert request["additionalProperties"] is False
    assert set(request["properties"]) == {
        "first_name",
        "role",
        "proposal_id",
        "patient_words",
    }
    assert schema["components"]["schemas"]["VisitPrepClarificationViewResponse"][
        "properties"
    ]["status"]["enum"] == ["available", "not_available"]


def test_followup_reads_use_the_existing_confirmation_budget(
    monkeypatch, owned_rate_limit_redis
):
    fixture, _, _ = _start(monkeypatch)
    client = _client(monkeypatch, owned_rate_limit_redis)
    for _ in range(8):
        assert _read(client, fixture).status_code == 200
    limited = _read(client, fixture)
    assert limited.status_code == 429
    _private(limited, fixture)


@pytest.mark.parametrize("role", ["patient", "caregiver"])
def test_two_turn_sources_and_existing_budget_expose_final_review_release_gap(
    monkeypatch, owned_rate_limit_redis, role
):
    """Document the real journey limit, not qualification of a usable AI session.

    Even without automatic post-save reads, two turns plus wording acceptance
    consume eight confirmation requests before the final review. Keep the
    existing security budget; resolving session continuation is separate work.
    """
    fixture, context, proposal = _start(monkeypatch, role=role)
    before = _projection(fixture)
    client = _client(monkeypatch, owned_rate_limit_redis)
    confirmation = dict(first_name="José", role=role)
    prompt_code = "detail"
    source = client.post(
        "/external/v1/visit-prep/contribution",
        headers=_headers(fixture),
        json=confirmation,
    )
    assert source.status_code == 200
    for index in range(2):
        view = _read(client, fixture, role)
        assert view.status_code == 200 and view.json()["status"] == "prompt"
        assert len(view.json()["turns"]) == index
        words = WORDS if index == 0 else "Invented second follow-up."
        operation = str(uuid4())
        response = client.post(
            PREFIX + "/turn",
            headers=_headers(fixture, operation),
            json=_payload(proposal, role) | {"patient_words": words},
        )
        assert (
            response.status_code == 200
            and response.json()["turn_sequence"] == index + 1
        )
        turn = _submit(fixture, proposal, operation=operation, words=words, role=role)
        context = append_patient_clarification(
            context,
            PatientClarificationReply(
                kind="clarify", prompt_code=prompt_code, statements=()
            ),
            patient_words=words,
        )
        proposal = _save(
            fixture,
            context,
            turn=turn,
            reply=_organize(context, source="clarification_2", words=words)
            if index == 1
            else PatientClarificationReply(
                kind="clarify", prompt_code="priority", statements=()
            ),
        )
        prompt_code = "priority"
    assert _projection(fixture) == before and _counts(fixture) == (2, 2)
    wording = client.post(
        PREFIX, headers=_headers(fixture), json=confirmation | {"question_key": "story"}
    )
    assert wording.status_code == 200 and len(wording.json()["turns"]) == 2
    choice = client.post(
        PREFIX + "/decision",
        headers=_headers(fixture, str(uuid4())),
        json=confirmation | dict(proposal_id=proposal.proposal_id, action="confirm"),
    )
    assert choice.status_code == 200
    refreshed = client.post(
        "/external/v1/visit-prep/contribution",
        headers=_headers(fixture),
        json=confirmation,
    )
    assert refreshed.status_code == 200
    final = client.post(
        "/external/v1/visit-prep/contribution/review",
        headers=_headers(fixture, str(uuid4())),
        json=confirmation | {"basis": refreshed.json()["basis"]},
    )
    assert final.status_code == 429
    assert not _projection(fixture).current_answers_reviewed
    _private(final, fixture)
