from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, cast

import pytest
from fastapi import FastAPI, HTTPException, status
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from typeid import TypeID

import app.routes.visit_prep as route
from app.routes.dependencies.visit_prep import (
    require_visit_prep_read,
    require_visit_prep_write,
)
from app.routes.internal import internal_api_app
from app.services.visit_prep_access import VisitPrepAccessDecision
from app.services.visit_prep_question_sets import (
    QuestionChoiceInput,
    QuestionConditionInput,
    QuestionDraftInput,
    QuestionSetMutationResult,
    QuestionSetResult,
    QuestionSetVersionInput,
    QuestionSetVersionResult,
    VisitPrepQuestionSetConflict,
    VisitPrepQuestionSetNotFound,
    VisitPrepQuestionSetValidation,
)

from activemodel.types import TypeIDType
from sqlalchemy.exc import SQLAlchemyError

OWNER_ID = cast(TypeIDType, TypeID("practice_member"))
PRACTICE_ID = cast(TypeIDType, TypeID("practice"))
DOCTOR_ID = cast(TypeIDType, TypeID("doctor"))
QUESTION_SET_ID = cast(TypeIDType, TypeID("visit_prep_question_set"))
VERSION_ID = cast(TypeIDType, TypeID("visit_prep_question_set_version"))
NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
IDEMPOTENCY_KEY = "123e4567-e89b-12d3-a456-426614174000"


def _decision(*, view_as_active: bool = False) -> VisitPrepAccessDecision:
    return VisitPrepAccessDecision(
        eligible=True,
        can_manage=not view_as_active,
        can_send_email=False,
        can_send_sms=False,
        doctor_id=DOCTOR_ID,
        practice_id=PRACTICE_ID,
        practice_member_id=OWNER_ID,
        denial_reason=None,
        view_as_active=view_as_active,
    )


def _version_result() -> QuestionSetVersionResult:
    return QuestionSetVersionResult(
        id=VERSION_ID,
        question_set_id=QUESTION_SET_ID,
        owner_practice_member_id=OWNER_ID,
        version_number=1,
        name="Five questions",
        supersedes_version_id=None,
        published_at=None,
        is_default=False,
        created_at=NOW,
        updated_at=NOW,
        questions=(),
    )


def _set_result() -> QuestionSetResult:
    return QuestionSetResult(
        id=QUESTION_SET_ID,
        owner_practice_member_id=OWNER_ID,
        seed_key=f"client.{IDEMPOTENCY_KEY}",
        created_at=NOW,
        updated_at=NOW,
        versions=(_version_result(),),
    )


def _mutation_result(
    disposition: Literal["created", "updated", "replayed"] = "created",
) -> QuestionSetMutationResult:
    return QuestionSetMutationResult(
        question_set=_set_result(),
        version=_version_result(),
        disposition=disposition,
    )


def _client(decision: VisitPrepAccessDecision) -> TestClient:
    app = FastAPI()
    app.include_router(route.visit_prep_api_app, prefix="/internal/v1")
    app.dependency_overrides[require_visit_prep_read] = lambda: decision
    app.dependency_overrides[require_visit_prep_write] = lambda: decision
    return TestClient(app)


def test_router_is_wired_only_under_the_internal_provider_api():
    paths = {
        item.path for item in internal_api_app.routes if isinstance(item, APIRoute)
    }

    assert {
        "/internal/v1/visit-prep/question-sets",
        "/internal/v1/visit-prep/question-set-versions/{version_id}",
        "/internal/v1/visit-prep/question-set-versions/{version_id}/successor",
        "/internal/v1/visit-prep/question-set-versions/{version_id}/publish",
        "/internal/v1/visit-prep/question-set-versions/{version_id}/default",
    } <= paths
    assert not any(path.startswith("/visit-prep") for path in paths)


def test_openapi_documents_replays_and_generic_error_contracts():
    app = FastAPI()
    app.include_router(route.visit_prep_api_app, prefix="/internal/v1")
    paths = app.openapi()["paths"]
    common_errors = {"403", "404", "409", "422", "503"}

    operations = {
        ("get", "/internal/v1/visit-prep/question-sets"),
        ("post", "/internal/v1/visit-prep/question-sets"),
        ("get", "/internal/v1/visit-prep/question-set-versions/{version_id}"),
        ("put", "/internal/v1/visit-prep/question-set-versions/{version_id}"),
        (
            "post",
            "/internal/v1/visit-prep/question-set-versions/{version_id}/successor",
        ),
        (
            "post",
            "/internal/v1/visit-prep/question-set-versions/{version_id}/publish",
        ),
        (
            "put",
            "/internal/v1/visit-prep/question-set-versions/{version_id}/default",
        ),
    }
    for method, path in operations:
        responses = paths[path][method]["responses"]
        assert common_errors <= responses.keys()
        for response_code in common_errors:
            schema = responses[response_code]["content"]["application/json"]["schema"]
            assert schema["$ref"].endswith("/VisitPrepQuestionSetErrorResponse")

    for path in {
        "/internal/v1/visit-prep/question-sets",
        "/internal/v1/visit-prep/question-set-versions/{version_id}/successor",
    }:
        responses = paths[path]["post"]["responses"]
        assert {"200", "201"} <= responses.keys()
        replay_schema = responses["200"]["content"]["application/json"]["schema"]
        assert replay_schema["$ref"].endswith("/VisitPrepQuestionSetMutationResponse")


def test_physician_list_lazily_seeds_then_lists_only_the_resolved_owner(monkeypatch):
    calls: list[tuple[str, TypeIDType]] = []

    def ensure_default_question_set(*, owner_practice_member_id):
        calls.append(("seed", owner_practice_member_id))
        return _mutation_result()

    def list_question_sets(*, owner_practice_member_id):
        calls.append(("list", owner_practice_member_id))
        return (_set_result(),)

    monkeypatch.setattr(
        route.question_set_service,
        "ensure_default_question_set",
        ensure_default_question_set,
    )
    monkeypatch.setattr(
        route.question_set_service,
        "list_question_sets",
        list_question_sets,
    )

    with _client(_decision()) as client:
        response = client.get("/internal/v1/visit-prep/question-sets")

    assert response.status_code == status.HTTP_200_OK
    assert calls == [("seed", OWNER_ID), ("list", OWNER_ID)]
    assert response.json()["question_sets"][0]["id"] == str(QUESTION_SET_ID)
    assert "owner_practice_member_id" not in response.text
    assert "seed_key" not in response.text


def test_view_as_list_is_read_only_and_never_materializes_the_seed(monkeypatch):
    def unexpected_seed(**_kwargs):
        raise AssertionError("view-as must not write a seeded set")

    monkeypatch.setattr(
        route.question_set_service,
        "ensure_default_question_set",
        unexpected_seed,
    )
    monkeypatch.setattr(
        route.question_set_service,
        "list_question_sets",
        lambda *, owner_practice_member_id: (),
    )

    with _client(_decision(view_as_active=True)) as client:
        response = client.get("/internal/v1/visit-prep/question-sets")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"question_sets": []}


@pytest.mark.parametrize(
    "bad_id",
    [str(TypeID("visit_prep_question_set")), "not-a-typeid"],
)
def test_exact_version_rejects_wrong_or_malformed_typeid_as_generic_404(
    monkeypatch,
    bad_id: str,
):
    def unexpected_read(**_kwargs):
        raise AssertionError("invalid IDs must not reach persistence")

    monkeypatch.setattr(
        route.question_set_service,
        "get_question_set_version",
        unexpected_read,
    )

    with _client(_decision()) as client:
        response = client.get(f"/internal/v1/visit-prep/question-set-versions/{bad_id}")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "Not found"}


def test_exact_version_uses_resolved_owner_and_serializes_explicitly(monkeypatch):
    captured: dict[str, object] = {}

    def get_question_set_version(**kwargs):
        captured.update(kwargs)
        return _version_result()

    monkeypatch.setattr(
        route.question_set_service,
        "get_question_set_version",
        get_question_set_version,
    )

    with _client(_decision()) as client:
        response = client.get(
            f"/internal/v1/visit-prep/question-set-versions/{VERSION_ID}"
        )

    assert response.status_code == status.HTTP_200_OK
    assert captured == {
        "owner_practice_member_id": OWNER_ID,
        "version_id": VERSION_ID,
    }
    assert response.json() == {
        "id": str(VERSION_ID),
        "question_set_id": str(QUESTION_SET_ID),
        "version_number": 1,
        "name": "Five questions",
        "supersedes_version_id": None,
        "published_at": None,
        "is_default": False,
        "created_at": "2026-08-25T12:00:00Z",
        "updated_at": "2026-08-25T12:00:00Z",
        "questions": [],
    }


def test_create_requires_canonical_idempotency_and_maps_the_full_definition(
    monkeypatch,
):
    captured: dict[str, object] = {}

    def create_question_set(**kwargs):
        captured.update(kwargs)
        return _mutation_result()

    monkeypatch.setattr(
        route.question_set_service,
        "create_question_set",
        create_question_set,
    )
    payload = {
        "name": "Medication preparation",
        "questions": [
            {
                "key": "adherence",
                "prompt": "Are you taking them as prescribed?",
                "kind": "choice",
                "choices": [
                    {"value": "yes", "label": "Yes"},
                    {"value": "no", "label": "No", "hint": "Tell us why"},
                ],
            },
            {
                "key": "barrier",
                "prompt": "What got in the way?",
                "helper_text": "Optional context",
                "kind": "story",
                "show_if": {
                    "question_key": "adherence",
                    "values": ["no"],
                },
            },
        ],
    }

    with _client(_decision()) as client:
        response = client.post(
            "/internal/v1/visit-prep/question-sets",
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            json=payload,
        )

    assert response.status_code == status.HTTP_201_CREATED
    assert captured == {
        "owner_practice_member_id": OWNER_ID,
        "idempotency_key": IDEMPOTENCY_KEY,
        "definition": QuestionSetVersionInput(
            name="Medication preparation",
            questions=(
                QuestionDraftInput(
                    key="adherence",
                    prompt="Are you taking them as prescribed?",
                    kind="choice",
                    choices=(
                        QuestionChoiceInput(value="yes", label="Yes"),
                        QuestionChoiceInput(
                            value="no",
                            label="No",
                            hint="Tell us why",
                        ),
                    ),
                ),
                QuestionDraftInput(
                    key="barrier",
                    prompt="What got in the way?",
                    helper_text="Optional context",
                    kind="story",
                    show_if=QuestionConditionInput(
                        question_key="adherence",
                        values=("no",),
                    ),
                ),
            ),
        ),
    }


def test_create_replay_returns_200(monkeypatch):
    monkeypatch.setattr(
        route.question_set_service,
        "create_question_set",
        lambda **_kwargs: _mutation_result("replayed"),
    )

    with _client(_decision()) as client:
        response = client.post(
            "/internal/v1/visit-prep/question-sets",
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            json={"name": "Draft", "questions": []},
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["disposition"] == "replayed"


def test_create_rejects_noncanonical_uuid_without_leaking_domain_detail(monkeypatch):
    def reject(**_kwargs):
        raise VisitPrepQuestionSetValidation(
            "authored prompt and idempotency details must stay private"
        )

    monkeypatch.setattr(route.question_set_service, "create_question_set", reject)

    with _client(_decision()) as client:
        response = client.post(
            "/internal/v1/visit-prep/question-sets",
            headers={"Idempotency-Key": IDEMPOTENCY_KEY.upper()},
            json={"name": "private authored name", "questions": []},
        )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert response.json() == {"detail": "Question-set definition is invalid."}
    assert "private authored" not in response.text


def test_body_validation_never_echoes_authored_question_content(monkeypatch):
    def unexpected_create(**_kwargs):
        raise AssertionError("invalid request must not reach the service")

    monkeypatch.setattr(
        route.question_set_service,
        "create_question_set",
        unexpected_create,
    )

    with _client(_decision()) as client:
        response = client.post(
            "/internal/v1/visit-prep/question-sets",
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            json={
                "name": "private authored name",
                "questions": [
                    {
                        "key": "reason",
                        "kind": "story",
                        "helper_text": "private authored helper text",
                    }
                ],
            },
        )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert response.json() == {"detail": "Question-set definition is invalid."}
    assert "private authored" not in response.text


def test_header_validation_uses_the_same_redacted_error(monkeypatch):
    def unexpected_create(**_kwargs):
        raise AssertionError("invalid request must not reach the service")

    monkeypatch.setattr(
        route.question_set_service,
        "create_question_set",
        unexpected_create,
    )

    with _client(_decision()) as client:
        response = client.post(
            "/internal/v1/visit-prep/question-sets",
            json={"name": "Draft", "questions": []},
        )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert response.json() == {"detail": "Question-set definition is invalid."}


def test_dependency_validation_uses_the_same_redacted_error(monkeypatch):
    app = FastAPI()
    app.include_router(route.visit_prep_api_app, prefix="/internal/v1")

    def validated_read_dependency(probe: Literal["expected"]):
        return _decision()

    app.dependency_overrides[require_visit_prep_read] = validated_read_dependency

    def unexpected_list(**_kwargs):
        raise AssertionError("invalid dependency input must not reach the service")

    monkeypatch.setattr(
        route.question_set_service,
        "list_question_sets",
        unexpected_list,
    )

    with TestClient(app) as client:
        response = client.get(
            "/internal/v1/visit-prep/question-sets",
            params={"probe": "private-dependency-value"},
        )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert response.json() == {"detail": "Question-set definition is invalid."}
    assert "private-dependency-value" not in response.text


def test_replace_passes_expected_timestamp_and_complete_definition(monkeypatch):
    captured: dict[str, object] = {}

    def replace_question_set_draft(**kwargs):
        captured.update(kwargs)
        return _mutation_result("updated")

    monkeypatch.setattr(
        route.question_set_service,
        "replace_question_set_draft",
        replace_question_set_draft,
    )

    with _client(_decision()) as client:
        response = client.put(
            f"/internal/v1/visit-prep/question-set-versions/{VERSION_ID}",
            json={
                "expected_updated_at": "2026-08-25T12:00:00Z",
                "name": "Replacement",
                "questions": [],
            },
        )

    assert response.status_code == status.HTTP_200_OK
    assert captured == {
        "owner_practice_member_id": OWNER_ID,
        "version_id": VERSION_ID,
        "expected_updated_at": NOW,
        "definition": QuestionSetVersionInput(name="Replacement", questions=()),
    }


def test_successor_publish_and_default_delegate_with_owner_scope(monkeypatch):
    captured: list[tuple[str, dict[str, object]]] = []

    def successor(**kwargs):
        captured.append(("successor", kwargs))
        return _mutation_result()

    def publish(**kwargs):
        captured.append(("publish", kwargs))
        return _mutation_result("updated")

    def select_default(**kwargs):
        captured.append(("default", kwargs))
        return _mutation_result("updated")

    monkeypatch.setattr(route.question_set_service, "create_successor_draft", successor)
    monkeypatch.setattr(
        route.question_set_service,
        "publish_question_set_version",
        publish,
    )
    monkeypatch.setattr(
        route.question_set_service,
        "set_default_question_set_version",
        select_default,
    )

    with _client(_decision()) as client:
        successor_response = client.post(
            f"/internal/v1/visit-prep/question-set-versions/{VERSION_ID}/successor",
            json={"name": "Next version"},
        )
        publish_response = client.post(
            f"/internal/v1/visit-prep/question-set-versions/{VERSION_ID}/publish",
            json={"make_default": True},
        )
        default_response = client.put(
            f"/internal/v1/visit-prep/question-set-versions/{VERSION_ID}/default"
        )

    assert successor_response.status_code == status.HTTP_201_CREATED
    assert publish_response.status_code == status.HTTP_200_OK
    assert default_response.status_code == status.HTTP_200_OK
    assert captured == [
        (
            "successor",
            {
                "owner_practice_member_id": OWNER_ID,
                "source_version_id": VERSION_ID,
                "name": "Next version",
            },
        ),
        (
            "publish",
            {
                "owner_practice_member_id": OWNER_ID,
                "version_id": VERSION_ID,
                "make_default": True,
            },
        ),
        (
            "default",
            {
                "owner_practice_member_id": OWNER_ID,
                "version_id": VERSION_ID,
            },
        ),
    ]


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_detail"),
    [
        (
            VisitPrepQuestionSetNotFound("private owner mismatch"),
            status.HTTP_404_NOT_FOUND,
            "Not found",
        ),
        (
            VisitPrepQuestionSetConflict("private database state"),
            status.HTTP_409_CONFLICT,
            "Question-set operation conflicts with its current state.",
        ),
        (
            SQLAlchemyError("secret authored text and constraint name"),
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Visit Prep question sets are temporarily unavailable.",
        ),
    ],
)
def test_domain_and_persistence_errors_are_safe(
    monkeypatch,
    error: Exception,
    expected_status: int,
    expected_detail: str,
):
    def fail(**_kwargs):
        raise error

    monkeypatch.setattr(route.question_set_service, "list_question_sets", fail)

    with _client(_decision(view_as_active=True)) as client:
        response = client.get("/internal/v1/visit-prep/question-sets")

    assert response.status_code == expected_status
    assert response.json() == {"detail": expected_detail}
    assert "private" not in response.text
    assert "secret" not in response.text
    assert "constraint" not in response.text


def test_mutations_use_the_write_dependency(monkeypatch):
    app = FastAPI()
    app.include_router(route.visit_prep_api_app, prefix="/internal/v1")
    app.dependency_overrides[require_visit_prep_read] = lambda: _decision(
        view_as_active=True
    )

    def deny_write():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="View-as is read-only. Exit view-as before making changes.",
        )

    app.dependency_overrides[require_visit_prep_write] = deny_write

    def unexpected_create(**_kwargs):
        raise AssertionError("read-only view-as must not reach a mutation")

    monkeypatch.setattr(
        route.question_set_service,
        "create_question_set",
        unexpected_create,
    )

    with TestClient(app) as client:
        response = client.post(
            "/internal/v1/visit-prep/question-sets",
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            json={"name": "Draft", "questions": []},
        )

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_request_models_forbid_unexpected_ownership_fields(monkeypatch):
    def unexpected_create(**_kwargs):
        raise AssertionError("invalid request must not reach the service")

    monkeypatch.setattr(
        route.question_set_service,
        "create_question_set",
        unexpected_create,
    )

    with _client(_decision()) as client:
        response = client.post(
            "/internal/v1/visit-prep/question-sets",
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            json={
                "name": "Draft",
                "questions": [],
                "owner_practice_member_id": str(TypeID("practice_member")),
            },
        )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
