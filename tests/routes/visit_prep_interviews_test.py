"""Public-route contract tests for the patient Visit Prep interview."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient

import app.routes.visit_prep_interviews as route
from app.routes.api import authenticate_api_request_middleware, external_api_app
from app.routes.middleware import allowed_hosts
from app.server import api_app
from app.services.visit_prep_interview_contract import (
    InterviewChoice,
    InterviewMutationResult,
    InterviewQuestion,
    InterviewState,
    VisitPrepInterviewConflict,
    VisitPrepInterviewUnavailable,
    VisitPrepInterviewValidation,
)

from sqlalchemy.exc import SQLAlchemyError

ACCESS_TOKEN = "vp1_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
AUTHORIZATION = f"Bearer {ACCESS_TOKEN}"
SCHEDULED_AT = datetime(2026, 9, 25, 14, 30, tzinfo=UTC)
IDEMPOTENCY_KEY = "123e4567-e89b-12d3-a456-426614174000"


@pytest.fixture(autouse=True)
def enable_public_interview_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        route.interview_service,
        "public_interview_api_enabled",
        lambda: True,
    )


def _state(
    *,
    status: str = "in_progress",
    submitted_questions: int = 2,
    current_question: InterviewQuestion | None = None,
) -> InterviewState:
    if current_question is None and status == "in_progress":
        current_question = InterviewQuestion(
            key="visit_goal",
            position=3,
            prompt="What would make this visit useful?",
            helper_text="Share the outcome you want.",
            kind="story",
            choices=(
                InterviewChoice(
                    value="example",
                    label="Example choice",
                    hint=None,
                ),
            ),
        )
    return InterviewState(
        status=status,  # type: ignore[arg-type]
        physician_display_name="Dr. Example",
        practice_display_name="Example Practice",
        scheduled_at=SCHEDULED_AT,
        timezone_name="America/New_York",
        submitted_questions=submitted_questions,
        maximum_questions=5,
        current_question=current_question,
    )


def _mutation(
    disposition: str = "accepted",
    *,
    state: InterviewState | None = None,
) -> InterviewMutationResult:
    return InterviewMutationResult(
        interview=state or _state(),
        disposition=disposition,  # type: ignore[arg-type]
    )


def _install_happy_seams(
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, object]]:
    rate_calls: list[dict[str, object]] = []

    def network_limit(*, network_signal: str | None) -> None:
        rate_calls.append({"kind": "network", "network_signal": network_signal})

    def credential_limits(
        *,
        network_signal: str | None,
        access_token: str | None,
        confirmation: bool = False,
    ) -> None:
        rate_calls.append(
            {
                "kind": "credential",
                "network_signal": network_signal,
                "access_token": access_token,
                "confirmation": confirmation,
            }
        )

    monkeypatch.setattr(
        route.rate_limits,
        "enforce_network_rate_limit",
        network_limit,
    )
    monkeypatch.setattr(
        route.rate_limits,
        "enforce_credential_rate_limits",
        credential_limits,
    )
    return rate_calls


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    _install_happy_seams(monkeypatch)
    app = FastAPI()
    app.include_router(route.visit_prep_interviews_api_app)
    app.dependency_overrides[route.aglobal_session] = lambda: None
    return TestClient(app, base_url="https://visit-prep.example.test")


def _assert_private(response: Any) -> None:
    assert "no-store" in response.headers["Cache-Control"]
    assert response.headers["Referrer-Policy"] == "no-referrer"


def _dependency_calls(dependant: Any) -> set[Callable[..., Any]]:
    calls: set[Callable[..., Any]] = set()
    for dependency in dependant.dependencies:
        if dependency.call is not None:
            calls.add(dependency.call)
        calls.update(_dependency_calls(dependency))
    return calls


def test_router_is_mounted_at_exact_public_paths_without_doctor_auth() -> None:
    expected = {
        "visit_prep_public_resume": ("/external/v1/visit-prep", "GET"),
        "visit_prep_public_confirm": ("/external/v1/visit-prep/confirm", "POST"),
        "visit_prep_public_submit_response": (
            "/external/v1/visit-prep/responses",
            "POST",
        ),
        "visit_prep_public_complete": ("/external/v1/visit-prep/complete", "POST"),
    }
    public_routes = {
        item.route.name: item
        for item in iter_route_contexts(api_app.routes)
        if isinstance(item.route, APIRoute) and item.route.name in expected
    }

    assert public_routes.keys() == expected.keys()
    for name, (path, method) in expected.items():
        public_route = public_routes[name]
        assert public_route.path == path
        assert public_route.methods == {method}
        assert authenticate_api_request_middleware not in _dependency_calls(
            public_route.dependant
        )

    doctor_api_names = {
        item.route.name
        for item in iter_route_contexts(external_api_app.routes)
        if isinstance(item.route, APIRoute)
    }
    assert expected.keys().isdisjoint(doctor_api_names)


def test_all_four_routes_delegate_a_canonical_header_bearer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def record(operation: str, result: object):
        def invoke(**kwargs: object) -> object:
            calls.append((operation, kwargs))
            return result

        return invoke

    monkeypatch.setattr(
        route.interview_service,
        "get_interview",
        record("resume", _state()),
    )
    monkeypatch.setattr(
        route.interview_service,
        "confirm_interview",
        record("confirm", _mutation("confirmed")),
    )
    monkeypatch.setattr(
        route.interview_service,
        "submit_response",
        record("response", _mutation("accepted")),
    )
    monkeypatch.setattr(
        route.interview_service,
        "complete_interview",
        record(
            "complete",
            _mutation(
                "completed",
                state=_state(
                    status="completed",
                    submitted_questions=5,
                    current_question=None,
                ),
            ),
        ),
    )
    rate_calls = _install_happy_seams(monkeypatch)
    app = FastAPI()
    app.include_router(route.visit_prep_interviews_api_app)
    app.dependency_overrides[route.aglobal_session] = lambda: None
    client = TestClient(app)
    headers = {"Authorization": AUTHORIZATION}

    responses = [
        client.get("/external/v1/visit-prep", headers=headers),
        client.post(
            "/external/v1/visit-prep/confirm",
            headers=headers,
            json={"first_name": "Ada", "role": "patient"},
        ),
        client.post(
            "/external/v1/visit-prep/responses",
            headers={**headers, "Idempotency-Key": IDEMPOTENCY_KEY},
            json={
                "question_key": "visit_goal",
                "response_kind": "answered",
                "answer": "A private patient answer",
            },
        ),
        client.post("/external/v1/visit-prep/complete", headers=headers),
    ]

    assert [response.status_code for response in responses] == [200, 200, 200, 200]
    assert [operation for operation, _ in calls] == [
        "resume",
        "confirm",
        "response",
        "complete",
    ]
    assert all(arguments["access_token"] == ACCESS_TOKEN for _, arguments in calls)
    assert calls[1][1] == {
        "access_token": ACCESS_TOKEN,
        "first_name": "Ada",
        "role": "patient",
    }
    submitted = calls[2][1]
    assert submitted["idempotency_key"] == IDEMPOTENCY_KEY
    assert submitted["response"].answer == "A private patient answer"  # type: ignore[union-attr]
    assert "A private patient answer" not in responses[2].text
    for response in responses:
        _assert_private(response)
    assert [call["kind"] for call in rate_calls] == [
        "network",
        "credential",
        "network",
        "credential",
        "network",
        "credential",
        "network",
        "credential",
    ]
    assert [
        call["confirmation"] for call in rate_calls if call["kind"] == "credential"
    ] == [False, True, False, False]


def test_openapi_declares_bearer_security_and_idempotency_header() -> None:
    schema = api_app.openapi()
    security_scheme = schema["components"]["securitySchemes"]["VisitPrepBearer"]
    assert security_scheme["type"] == "http"
    assert security_scheme["scheme"] == "bearer"

    paths = schema["paths"]
    for path, method in (
        ("/external/v1/visit-prep", "get"),
        ("/external/v1/visit-prep/confirm", "post"),
        ("/external/v1/visit-prep/responses", "post"),
        ("/external/v1/visit-prep/complete", "post"),
    ):
        assert paths[path][method]["security"] == [{"VisitPrepBearer": []}]

    response_parameters = paths["/external/v1/visit-prep/responses"]["post"][
        "parameters"
    ]
    idempotency = next(
        parameter
        for parameter in response_parameters
        if parameter["name"] == "Idempotency-Key"
    )
    assert idempotency["in"] == "header"
    assert idempotency["required"] is True


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "Basic credentials",
        f"bearer {ACCESS_TOKEN}",
        f"Bearer  {ACCESS_TOKEN}",
        "Bearer vp1_too-short",
        f"Bearer vp2_{'A' * 43}",
        f"Bearer vp1_{'/' * 43}",
    ],
)
def test_missing_or_malformed_authorization_is_generic_and_never_classified(
    monkeypatch: pytest.MonkeyPatch,
    authorization: str | None,
) -> None:
    rate_calls = _install_happy_seams(monkeypatch)
    app = FastAPI()
    app.include_router(route.visit_prep_interviews_api_app)
    app.dependency_overrides[route.aglobal_session] = lambda: None
    client = TestClient(app)
    headers = {"Authorization": authorization} if authorization is not None else {}

    response = client.get("/external/v1/visit-prep", headers=headers)

    assert response.status_code == 404
    assert response.json() == {"detail": "Visit Prep link is unavailable."}
    assert [call["kind"] for call in rate_calls] == ["network", "credential"]
    assert rate_calls[-1]["access_token"] is None
    _assert_private(response)


def test_duplicate_authorization_headers_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_happy_seams(monkeypatch)
    app = FastAPI()
    app.include_router(route.visit_prep_interviews_api_app)
    app.dependency_overrides[route.aglobal_session] = lambda: None
    client = TestClient(app)

    response = client.get(
        "/external/v1/visit-prep",
        headers=[
            ("Authorization", AUTHORIZATION),
            ("Authorization", AUTHORIZATION),
        ],
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Visit Prep link is unavailable."}
    _assert_private(response)


def test_query_token_is_rejected_before_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rate_calls = _install_happy_seams(monkeypatch)
    app = FastAPI()
    app.include_router(route.visit_prep_interviews_api_app)
    app.dependency_overrides[route.aglobal_session] = lambda: None
    client = TestClient(app)

    response = client.get(f"/external/v1/visit-prep?token={ACCESS_TOKEN}")

    assert response.status_code == 422
    assert response.json() == {"detail": "Visit Prep request is invalid."}
    assert ACCESS_TOKEN not in response.text
    assert [call["kind"] for call in rate_calls] == ["network"]
    _assert_private(response)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/external/v1/visit-prep/"),
        ("POST", "/external/v1/visit-prep/confirm/"),
        ("POST", "/external/v1/visit-prep/responses/"),
        ("POST", "/external/v1/visit-prep/complete/"),
    ],
)
def test_slash_variant_never_redirects_or_reflects_query_token(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
) -> None:
    rate_calls = _install_happy_seams(monkeypatch)
    app = FastAPI()
    app.include_router(route.visit_prep_interviews_api_app)
    app.dependency_overrides[route.aglobal_session] = lambda: None
    client = TestClient(app, follow_redirects=False)

    response = client.request(method, f"{path}?token=PRIVATE-TOKEN")

    assert response.status_code == 422
    assert response.json() == {"detail": "Visit Prep request is invalid."}
    assert "Location" not in response.headers
    assert "PRIVATE-TOKEN" not in response.text
    assert [call["kind"] for call in rate_calls] == ["network"]
    _assert_private(response)


@pytest.mark.parametrize(
    "request_kwargs",
    [
        {"content": b'{"first_name":"PRIVATE-NAME"'},
        {
            "json": {
                "first_name": "PRIVATE-NAME",
                "role": "not-a-role",
                "secret_extra": "PRIVATE-EXTRA",
            }
        },
    ],
)
def test_validation_errors_are_redacted(
    monkeypatch: pytest.MonkeyPatch,
    request_kwargs: dict[str, Any],
) -> None:
    client = _client(monkeypatch)

    response = client.post(
        "/external/v1/visit-prep/confirm",
        headers={"Authorization": AUTHORIZATION},
        **request_kwargs,
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "Visit Prep request is invalid."}
    assert "PRIVATE" not in response.text
    assert "input" not in response.text
    _assert_private(response)


@pytest.mark.parametrize("status_code", [401, 404, 503])
def test_dependency_http_exceptions_cannot_expose_details_or_headers(
    monkeypatch, status_code
):
    from fastapi import HTTPException

    _install_happy_seams(monkeypatch)
    app = FastAPI()
    app.include_router(route.visit_prep_interviews_api_app)
    app.dependency_overrides[route.aglobal_session] = lambda: None

    def fail():
        raise HTTPException(
            status_code=status_code,
            detail="PRIVATE-DEPENDENCY-DETAIL",
            headers={"X-Private": "PRIVATE-HEADER"},
        )

    app.dependency_overrides[route.require_public_visit_prep_bearer] = fail
    client = TestClient(app)
    response = client.get("/external/v1/visit-prep")
    assert response.status_code == (404 if status_code == 404 else 503)
    assert "PRIVATE" not in response.text
    assert "X-Private" not in response.headers
    _assert_private(response)


def test_body_larger_than_ten_kib_is_rejected_before_service_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatched = False
    rate_calls = _install_happy_seams(monkeypatch)

    def confirm(**_: object) -> InterviewMutationResult:
        nonlocal dispatched
        dispatched = True
        return _mutation("confirmed")

    monkeypatch.setattr(route.interview_service, "confirm_interview", confirm)
    app = FastAPI()
    app.include_router(route.visit_prep_interviews_api_app)
    app.dependency_overrides[route.aglobal_session] = lambda: None
    client = TestClient(app)
    oversized = b"{" + b'"first_name":"PRIVATE",' + b'"padding":"' + b"x" * 10_240

    response = client.post(
        "/external/v1/visit-prep/confirm",
        headers={"Authorization": AUTHORIZATION, "Content-Type": "application/json"},
        content=oversized,
    )

    assert response.status_code == 413
    assert response.json() == {"detail": "Visit Prep request is invalid."}
    assert "PRIVATE" not in response.text
    assert dispatched is False
    assert [call["kind"] for call in rate_calls] == ["network"]
    _assert_private(response)


@pytest.mark.parametrize("declared_length", ["-1", "not-a-number"])
def test_invalid_content_length_is_rejected_before_service_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    declared_length: str,
) -> None:
    dispatched = False
    rate_calls = _install_happy_seams(monkeypatch)

    def confirm(**_: object) -> InterviewMutationResult:
        nonlocal dispatched
        dispatched = True
        return _mutation("confirmed")

    monkeypatch.setattr(route.interview_service, "confirm_interview", confirm)
    app = FastAPI()
    app.include_router(route.visit_prep_interviews_api_app)
    app.dependency_overrides[route.aglobal_session] = lambda: None
    client = TestClient(app)

    response = client.post(
        "/external/v1/visit-prep/confirm",
        headers={
            "Authorization": AUTHORIZATION,
            "Content-Length": declared_length,
            "Content-Type": "application/json",
        },
        content=b"{}",
    )

    assert response.status_code == 422
    assert dispatched is False
    assert [call["kind"] for call in rate_calls] == ["network"]
    _assert_private(response)


def test_duplicate_content_length_is_rejected_before_service_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rate_calls = _install_happy_seams(monkeypatch)
    app = FastAPI()
    app.include_router(route.visit_prep_interviews_api_app)
    app.dependency_overrides[route.aglobal_session] = lambda: None
    client = TestClient(app)

    response = client.post(
        "/external/v1/visit-prep/confirm",
        headers=[
            ("Authorization", AUTHORIZATION),
            ("Content-Length", "2"),
            ("Content-Length", "2"),
            ("Content-Type", "application/json"),
        ],
        content=b"{}",
    )

    assert response.status_code == 422
    assert [call["kind"] for call in rate_calls] == ["network"]
    _assert_private(response)


@pytest.mark.parametrize("declared_length", [None, "1"])
def test_streamed_body_limit_rejects_missing_or_underreported_length(
    monkeypatch: pytest.MonkeyPatch,
    declared_length: str | None,
) -> None:
    rate_calls = _install_happy_seams(monkeypatch)
    app = FastAPI()
    app.include_router(route.visit_prep_interviews_api_app)
    app.dependency_overrides[route.aglobal_session] = lambda: None
    client = TestClient(app)

    def oversized_chunks():
        yield b"{" + b'"first_name":"PRIVATE",'
        yield b'"padding":"' + b"x" * 10_240

    headers = {
        "Authorization": AUTHORIZATION,
        "Content-Type": "application/json",
    }
    if declared_length is not None:
        headers["Content-Length"] = declared_length

    response = client.post(
        "/external/v1/visit-prep/confirm",
        headers=headers,
        content=oversized_chunks(),
    )

    assert response.status_code == 413
    assert "PRIVATE" not in response.text
    assert [call["kind"] for call in rate_calls] == ["network"]
    _assert_private(response)


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (
            "/external/v1/visit-prep/confirm",
            {"first_name": "PRIVATE", "role": "patient", "extra": "PRIVATE"},
        ),
        (
            "/external/v1/visit-prep/responses",
            {
                "question_key": "reason",
                "response_kind": "skipped",
                "answer": None,
                "extra": "PRIVATE",
            },
        ),
    ],
)
def test_extra_body_fields_are_rejected_with_valid_other_fields(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    payload: dict[str, object],
) -> None:
    client = _client(monkeypatch)
    headers = {"Authorization": AUTHORIZATION}
    if path.endswith("/responses"):
        headers["Idempotency-Key"] = IDEMPOTENCY_KEY

    response = client.post(path, headers=headers, json=payload)

    assert response.status_code == 422
    assert response.json() == {"detail": "Visit Prep request is invalid."}
    assert "PRIVATE" not in response.text
    _assert_private(response)


def test_public_state_shape_contains_no_ids_or_prior_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        route.interview_service,
        "get_interview",
        lambda **_: _state(),
    )
    client = _client(monkeypatch)

    response = client.get(
        "/external/v1/visit-prep",
        headers={"Authorization": AUTHORIZATION},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "in_progress",
        "physician_display_name": "Dr. Example",
        "practice_display_name": "Example Practice",
        "scheduled_at": "2026-09-25T14:30:00Z",
        "timezone_name": "America/New_York",
        "submitted_questions": 2,
        "maximum_questions": 5,
        "current_question": {
            "key": "visit_goal",
            "position": 3,
            "prompt": "What would make this visit useful?",
            "helper_text": "Share the outcome you want.",
            "kind": "story",
            "choices": [{"value": "example", "label": "Example choice", "hint": None}],
        },
    }
    serialized = response.text.lower()
    assert '"id"' not in serialized
    assert "patient_id" not in serialized
    assert "practice_member_id" not in serialized
    assert "answer" not in serialized
    assert ACCESS_TOKEN not in response.text
    _assert_private(response)


@pytest.mark.parametrize(
    ("failure", "expected_status", "expected_detail"),
    [
        (
            VisitPrepInterviewUnavailable("sensitive unavailable detail"),
            404,
            "Visit Prep link is unavailable.",
        ),
        (
            VisitPrepInterviewValidation("sensitive validation detail"),
            422,
            "Visit Prep request is invalid.",
        ),
        (
            VisitPrepInterviewConflict("sensitive conflict detail"),
            409,
            "Visit Prep request conflicts with its current state.",
        ),
        (
            SQLAlchemyError("sensitive database detail"),
            503,
            "Visit Prep is temporarily unavailable.",
        ),
    ],
)
def test_domain_and_persistence_errors_map_to_bounded_public_responses(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected_status: int,
    expected_detail: str,
) -> None:
    def fail(**_: object) -> InterviewState:
        raise failure

    monkeypatch.setattr(route.interview_service, "get_interview", fail)
    client = _client(monkeypatch)

    response = client.get(
        "/external/v1/visit-prep",
        headers={"Authorization": AUTHORIZATION},
    )

    assert response.status_code == expected_status
    assert response.json() == {"detail": expected_detail}
    assert "sensitive" not in response.text.lower()
    _assert_private(response)


def test_unexpected_public_failure_is_generic_and_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(**_: object) -> InterviewState:
        raise RuntimeError("sensitive unexpected detail")

    monkeypatch.setattr(route.interview_service, "get_interview", fail)
    _install_happy_seams(monkeypatch)
    client = TestClient(
        api_app,
        base_url=allowed_hosts(with_scheme=True)[0],
        raise_server_exceptions=False,
    )

    response = client.get(
        "/external/v1/visit-prep",
        headers={"Authorization": AUTHORIZATION},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Visit Prep is temporarily unavailable."}
    assert "sensitive" not in response.text.lower()
    _assert_private(response)


def test_unknown_canonical_bearer_is_generic_and_rate_limited_as_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rate_calls = _install_happy_seams(monkeypatch)

    def unavailable(**_: object) -> InterviewState:
        raise VisitPrepInterviewUnavailable("unknown token")

    monkeypatch.setattr(route.interview_service, "get_interview", unavailable)
    app = FastAPI()
    app.include_router(route.visit_prep_interviews_api_app)
    app.dependency_overrides[route.aglobal_session] = lambda: None
    client = TestClient(app)

    response = client.get(
        "/external/v1/visit-prep",
        headers={"Authorization": AUTHORIZATION},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Visit Prep link is unavailable."}
    assert rate_calls[-1]["access_token"] == ACCESS_TOKEN
    _assert_private(response)


def test_public_api_gate_stops_before_bearer_or_credential_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rate_calls = _install_happy_seams(monkeypatch)
    service_calls: list[str] = []
    monkeypatch.setattr(
        route.interview_service,
        "public_interview_api_enabled",
        lambda: False,
    )
    monkeypatch.setattr(
        route.interview_service,
        "get_interview",
        lambda **_: service_calls.append("called") or _state(),
    )
    app = FastAPI()
    app.include_router(route.visit_prep_interviews_api_app)
    app.dependency_overrides[route.aglobal_session] = lambda: None
    client = TestClient(app)

    response = client.get(
        "/external/v1/visit-prep",
        headers={"Authorization": AUTHORIZATION},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Visit Prep link is unavailable."}
    assert [call["kind"] for call in rate_calls] == ["network"]
    assert service_calls == []
    _assert_private(response)


def test_noncanonical_base64_bearer_is_generic_and_rate_limited_as_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    noncanonical_token = f"vp1_{'A' * 42}B"
    rate_calls: list[dict[str, object]] = []

    def network_limit(*, network_signal: str | None) -> None:
        rate_calls.append({"kind": "network", "network_signal": network_signal})

    def credential_limits(
        *,
        network_signal: str | None,
        access_token: str | None,
        confirmation: bool = False,
    ) -> None:
        rate_calls.append(
            {
                "kind": "credential",
                "network_signal": network_signal,
                "access_token": access_token,
                "confirmation": confirmation,
            }
        )

    monkeypatch.setattr(
        route.rate_limits,
        "enforce_network_rate_limit",
        network_limit,
    )
    monkeypatch.setattr(
        route.rate_limits,
        "enforce_credential_rate_limits",
        credential_limits,
    )
    app = FastAPI()
    app.include_router(route.visit_prep_interviews_api_app)
    app.dependency_overrides[route.aglobal_session] = lambda: None
    client = TestClient(app)

    response = client.get(
        "/external/v1/visit-prep",
        headers={"Authorization": f"Bearer {noncanonical_token}"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Visit Prep link is unavailable."}
    assert [call["kind"] for call in rate_calls] == ["network", "credential"]
    assert rate_calls[-1]["access_token"] == noncanonical_token
    _assert_private(response)


def test_network_rate_limit_maps_to_429_before_token_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_calls: list[str] = []
    network_signals: list[str | None] = []
    monkeypatch.setattr(
        route.interview_service,
        "get_interview",
        lambda **kwargs: service_calls.append(str(kwargs["access_token"])) or _state(),
    )

    def limit(*, network_signal: str | None) -> None:
        network_signals.append(network_signal)
        raise route.rate_limits.RateLimited

    monkeypatch.setattr(route.rate_limits, "enforce_network_rate_limit", limit)
    app = FastAPI()
    app.include_router(route.visit_prep_interviews_api_app)
    app.dependency_overrides[route.aglobal_session] = lambda: None
    client = TestClient(app)

    response = client.get(
        "/external/v1/visit-prep",
        headers={
            "Authorization": AUTHORIZATION,
            "X-Forwarded-For": "203.0.113.99",
        },
    )

    assert response.status_code == 429
    assert response.json() == {
        "detail": "Too many Visit Prep requests. Try again later."
    }
    assert response.headers["Retry-After"] == str(route.rate_limits.WINDOW_SECONDS)
    assert service_calls == []
    assert network_signals == ["testclient"]
    _assert_private(response)


def test_unexpected_network_limiter_failure_is_generic_and_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_calls: list[str] = []

    def fail(**_: object) -> None:
        raise RuntimeError("sensitive limiter detail")

    monkeypatch.setattr(route.rate_limits, "enforce_network_rate_limit", fail)
    monkeypatch.setattr(
        route.interview_service,
        "get_interview",
        lambda **_: service_calls.append("called") or _state(),
    )
    isolated_app = FastAPI()
    isolated_app.include_router(route.visit_prep_interviews_api_app)
    isolated_app.dependency_overrides[route.aglobal_session] = lambda: None
    origin = allowed_hosts(with_scheme=True)[0]
    clients = (
        TestClient(isolated_app, raise_server_exceptions=False),
        TestClient(api_app, base_url=origin, raise_server_exceptions=False),
    )

    for client in clients:
        response = client.get(
            "/external/v1/visit-prep",
            headers={"Authorization": AUTHORIZATION, "Origin": origin},
        )
        assert response.status_code == 503
        assert response.json() == {"detail": "Visit Prep is temporarily unavailable."}
        assert "sensitive" not in response.text.lower()
        _assert_private(response)

    assert service_calls == []


@pytest.mark.parametrize("stage", ["network", "credential"])
def test_rate_limit_store_unavailability_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    def network(**_: object) -> None:
        if stage == "network":
            raise route.rate_limits.RateLimitUnavailable

    def credential(**_: object) -> None:
        if stage == "credential":
            raise route.rate_limits.RateLimitUnavailable

    monkeypatch.setattr(route.rate_limits, "enforce_network_rate_limit", network)
    monkeypatch.setattr(
        route.rate_limits,
        "enforce_credential_rate_limits",
        credential,
    )
    app = FastAPI()
    app.include_router(route.visit_prep_interviews_api_app)
    app.dependency_overrides[route.aglobal_session] = lambda: None
    client = TestClient(app)

    response = client.get(
        "/external/v1/visit-prep",
        headers={"Authorization": AUTHORIZATION},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Visit Prep is temporarily unavailable."}
    _assert_private(response)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/external/v1/visit-prep"),
        ("TRACE", "/external/v1/visit-prep/confirm"),
        ("PROPFIND", "/external/v1/visit-prep/confirm"),
        ("GET", "/external/v1/visit-prep/responses"),
        ("DELETE", "/external/v1/visit-prep/complete"),
    ],
)
def test_wrong_methods_are_generic_private_and_network_limited(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
) -> None:
    rate_calls = _install_happy_seams(monkeypatch)
    app = FastAPI()
    app.include_router(route.visit_prep_interviews_api_app)
    app.dependency_overrides[route.aglobal_session] = lambda: None
    client = TestClient(app)

    response = client.request(method, path)

    assert response.status_code == 404
    assert [call["kind"] for call in rate_calls] == ["network"]
    _assert_private(response)


def test_full_app_cors_preflight_crosses_public_network_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rate_calls = _install_happy_seams(monkeypatch)
    origin = allowed_hosts(with_scheme=True)[0]
    client = TestClient(api_app, base_url=origin)

    response = client.options(
        "/external/v1/visit-prep/confirm",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == origin
    assert [call["kind"] for call in rate_calls] == ["network"]
    assert "no-store" in response.headers["Cache-Control"]
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_full_app_happy_path_counts_network_and_credential_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rate_calls = _install_happy_seams(monkeypatch)
    monkeypatch.setattr(
        route.interview_service,
        "get_interview",
        lambda **_: _state(),
    )
    origin = allowed_hosts(with_scheme=True)[0]
    client = TestClient(api_app, base_url=origin)

    response = client.get(
        "/external/v1/visit-prep",
        headers={"Authorization": AUTHORIZATION},
    )

    assert response.status_code == 200
    assert [call["kind"] for call in rate_calls] == ["network", "credential"]
    _assert_private(response)


def test_full_app_network_limit_error_preserves_allowed_origin_cors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[dict[str, object]] = []

    def limit(**_: object) -> None:
        raise route.rate_limits.RateLimited

    monkeypatch.setattr(route.rate_limits, "enforce_network_rate_limit", limit)
    monkeypatch.setattr(
        route.log,
        "warning",
        lambda message, **values: events.append({"message": message, **values}),
    )
    origin = allowed_hosts(with_scheme=True)[0]
    client = TestClient(api_app, base_url=origin)

    response = client.get(
        "/external/v1/visit-prep",
        headers={
            "Authorization": AUTHORIZATION,
            "Origin": origin,
        },
    )

    assert response.status_code == 429
    assert response.headers["Access-Control-Allow-Origin"] == origin
    assert response.headers["Access-Control-Allow-Credentials"] == "true"
    assert response.headers["Vary"] == "Origin"
    assert events == [
        {
            "message": "Visit Prep public request rate limited",
            "action": "visit_prep.public_rate_limited",
            "stage": "network",
        }
    ]
    _assert_private(response)
