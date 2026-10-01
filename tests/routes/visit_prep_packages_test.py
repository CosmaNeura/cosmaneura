"""Real provider HTTP reads; no patient bearer, hidden source or support leak."""

from uuid import uuid4

import pytest
from fastapi import Request

from app.routes.dependencies.view_as import apply_view_as
from app.services.visit_prep_contributions import correct_contribution
from app.services.visit_prep_interview_contract import ResponseInput

from tests.routes.visit_prep_question_sets_test import _provider_client
from tests.services.visit_prep_contribution_projection_test import (
    _prepared,
    _projection,
)


def test_http_package_preserves_patient_words_provenance_and_excludes_hidden_topic(
    monkeypatch,
):
    fixture = _prepared(monkeypatch, "package-http-hidden", adherence="some")
    correct_contribution(
        access_token=fixture.access_token,
        first_name="José",
        role="patient",
        expected_basis=_projection(fixture).basis,
        idempotency_key=str(uuid4()),
        response=ResponseInput("adherence", "answered", "yes"),
    )
    with _provider_client(fixture.doctor, monkeypatch) as client:
        response = client.get(
            f"/internal/v1/visit-prep/preps/{fixture.prep_id}/package"
        )
    assert response.status_code == 200
    body = response.json()
    assert (
        body["contract"] == "visit_prep.patient_sources.v1"
        and body["synthesis_status"] == "not_generated"
    )
    assert (
        body["package_id"] == str(fixture.prep_id) and len(body["source_version"]) == 64
    )
    assert body["preparation"]["patient_id"] is None
    adherence = next(
        topic for topic in body["topics"] if topic["question_key"] == "adherence"
    )
    assert (
        adherence["answer"] == "yes"
        and adherence["initial_answer"] == "some"
        and adherence["changed"]
    )
    assert adherence["original_response_id"] and adherence["current_revision_id"]
    assert (
        "adherence_barrier" not in response.text
        and "An invented barrier." not in response.text
    )
    assert (
        fixture.access_token not in response.text and "ciphertext" not in response.text
    )
    assert (
        response.headers["cache-control"] == "no-store"
        and response.headers["referrer-policy"] == "no-referrer"
    )


@pytest.mark.parametrize("suffix", ["", "/package"])
def test_support_context_discloses_neither_list_nor_answers(monkeypatch, suffix):
    fixture = _prepared(monkeypatch, f"package-http-support-{bool(suffix)}")
    with _provider_client(fixture.doctor, monkeypatch) as client:

        def support(request: Request):
            request.state.view_as = object()

        client.app.dependency_overrides[apply_view_as] = support
        response = client.get(
            f"/internal/v1/visit-prep/preps/{fixture.prep_id}/package"
            if suffix
            else "/internal/v1/visit-prep/preps"
        )
    assert response.status_code == 404 and response.json() == {"detail": "Not found"}
    assert "invented" not in response.text


@pytest.mark.parametrize(
    "query",
    [
        "?access_token=vp1_invalid",
        "?limit=20&limit=20",
        "?before_id=not-an-id",
        "?limit=51",
    ],
)
def test_list_rejects_unknown_duplicate_or_invalid_queries_without_echo(
    monkeypatch, query
):
    fixture = _prepared(monkeypatch, "package-http-invalid")
    with _provider_client(fixture.doctor, monkeypatch) as client:
        response = client.get(f"/internal/v1/visit-prep/preps{query}")
    assert response.status_code in {404, 422}
    assert response.json()["detail"] in {"Not found", "Visit Prep request is invalid."}
    assert "vp1_invalid" not in response.text and "not-an-id" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_full_provider_router_list_is_owner_scoped_without_patient_content(monkeypatch):
    fixture = _prepared(monkeypatch, "package-http-list")
    with _provider_client(fixture.doctor, monkeypatch) as client:
        response = client.get("/internal/v1/visit-prep/preps")
    assert response.status_code == 200
    assert len(response.json()["preparations"]) == 1
    assert (
        "My invented health story." not in response.text
        and "topics" not in response.text
    )


def test_package_paths_are_internal_and_have_named_openapi_operations():
    from app.routes.internal import internal_api_app
    from app.utils.openapi import generate_openapi_schema

    paths = generate_openapi_schema(internal_api_app)["paths"]
    assert (
        paths["/internal/v1/visit-prep/preps"]["get"]["operationId"]
        == "visit_prep_list"
    )
    assert (
        paths["/internal/v1/visit-prep/preps/{prep_id}/package"]["get"]["operationId"]
        == "visit_prep_package"
    )
    assert all(
        not path.startswith("/external/")
        for path in paths
        if "package" in path and "visit-prep" in path
    )
