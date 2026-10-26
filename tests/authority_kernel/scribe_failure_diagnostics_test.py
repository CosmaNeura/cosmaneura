"""Diagnostics expose only fixed metadata and preserve original HTTP outcomes."""

import asyncio
import json
import time
from types import SimpleNamespace
from typing import Any, cast

import pytest
from starlette.requests import Request

from app.routes import authority_errors, scribe_capture, scribe_encounters
from app.services import scribe_failure_diagnostics as diagnostics
from app.services.companion_records import RecordBudget, RecordUnavailable

from sqlalchemy.exc import OperationalError, SQLAlchemyError

SECRET = "never-log-this-synthetic-sql-token-or-patient-value"


@pytest.fixture
def events(monkeypatch):
    output = []
    monkeypatch.setattr(
        authority_errors, "new_trace_id", lambda: "synthetic-fixed-trace-id"
    )
    monkeypatch.setattr(diagnostics, "is_staging", lambda: True)
    monkeypatch.setattr(
        diagnostics.structlog,
        "get_logger",
        lambda *_: SimpleNamespace(
            warning=lambda event, **fields: output.append((event, fields))
        ),
    )
    return output


def test_sqlalchemy_chain_keeps_code_without_sql_parameters_or_messages(events):
    class LockNotAvailable(Exception):
        sqlstate = "55P03"

    original = LockNotAvailable(SECRET)
    error = OperationalError(SECRET, {"secret": SECRET}, original)
    diagnostics.report_unconfirmed(
        boundary="capture_operation", operation="authorize_upload", error=error
    )
    payload = events[0][1]
    assert [x["error_type"] for x in payload["failures"]] == [
        "OperationalError",
        "LockNotAvailable",
    ]
    assert payload["failures"][1]["sqlstate"] == "55P03"
    assert SECRET not in json.dumps(events)


def test_only_allowlisted_sites_codes_and_operations_are_logged(events):
    scope: dict[str, Any] = {
        "__name__": "app.services.scribe_capture_gateway",
        "secret": SECRET,
    }
    exec("def fail():\n    raise TypeError(secret)\n", scope)
    try:
        scope["fail"]()
    except TypeError as error:
        diagnostics.report_unconfirmed(
            boundary="capture_operation", operation=SECRET, error=error
        )
    row = events[0][1]
    assert row["operation"] == "other"
    assert row["failures"] == [
        {
            "error_type": "TypeError",
            "sqlstate": None,
            "sites": [{"module": "app.services.scribe_capture_gateway", "line": 2}],
        }
    ]
    assert SECRET not in json.dumps(events)


def test_exception_cycles_unknown_types_and_messages_are_bounded(events):
    class Unknown(Exception):
        sqlstate = SECRET

        def __str__(self):
            raise AssertionError("Exception text must not be rendered")

    errors = [Unknown(SECRET) for _ in range(8)]
    for index, error in enumerate(errors):
        error.__context__ = errors[(index + 1) % len(errors)]
    diagnostics.report_unconfirmed(
        boundary="encounter", operation="lookup", error=errors[0]
    )
    assert len(events[0][1]["failures"]) == 4
    assert all(
        x == {"error_type": "other", "sqlstate": None, "sites": []}
        for x in events[0][1]["failures"]
    )


@pytest.mark.parametrize("staging,boundary", [(False, "encounter"), (True, SECRET)])
def test_production_and_unknown_boundary_emit_nothing(
    events, monkeypatch, staging, boundary
):
    monkeypatch.setattr(diagnostics, "is_staging", lambda: staging)
    diagnostics.report_unconfirmed(
        boundary=boundary, operation="lookup", error=TypeError(SECRET)
    )
    assert events == []


def test_broken_logger_does_not_replace_original_response(monkeypatch):
    monkeypatch.setattr(diagnostics, "is_staging", lambda: True)

    def broken(*_, **__):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(diagnostics.structlog, "get_logger", broken)
    diagnostics.report_unconfirmed(
        boundary="encounter", operation="lookup", error=TypeError(SECRET)
    )


@pytest.mark.parametrize(
    "error", [RecordUnavailable(), SQLAlchemyError(SECRET), TypeError(SECRET)]
)
def test_encounter_route_preserves_503_and_adds_no_error_values(
    events, monkeypatch, error
):
    async def body(_):
        return SimpleNamespace(action="lookup")

    def fail(**_):
        raise error

    monkeypatch.setattr(scribe_encounters, "read_body", body)
    monkeypatch.setattr(scribe_encounters.scribe_encounters, "encounter_workflow", fail)
    request = Request(
        {
            "type": "http",
            "headers": [(b"x-cosmaneura-scribe-encounter-version", b"1")],
            "state": {"companion_record_deadline": RecordBudget(time.monotonic() + 5)},
        }
    )
    result = asyncio.run(
        scribe_encounters.scribe_encounter_workflow(
            request,
            cast(
                Any,
                SimpleNamespace(
                    issuer="synthetic",
                    subject="synthetic",
                    clerk_session_id="synthetic",
                ),
            ),
            cast(Any, SimpleNamespace(enabled=True)),
            cast(Any, SimpleNamespace()),
        )
    )
    assert result.status_code == 503
    assert result.body == scribe_encounters.problem(503).body
    assert len(events) == 1 and events[0][1]["boundary"] == "encounter"
    assert SECRET not in bytes(result.body).decode() + json.dumps(events)


@pytest.mark.parametrize(
    "error",
    [
        scribe_capture.admission.AdmissionUnconfirmed(),
        scribe_capture.gateway.CaptureOperationUnconfirmed(),
        SQLAlchemyError(SECRET),
    ],
)
def test_capture_route_keeps_retryable_response_and_closes_client(
    events, monkeypatch, error
):
    closed = []

    async def close():
        closed.append(True)

    async def body(*_):
        return (
            cast(
                Any,
                SimpleNamespace(
                    expected_session_id=None,
                    binding_ref="synthetic",
                    workspace_id="synthetic",
                    operation=SimpleNamespace(kind="register_chunk"),
                ),
            ),
            cast(Any, SimpleNamespace(model_dump=lambda: {})),
        )

    async def fail(**_):
        raise error

    monkeypatch.setattr(scribe_capture, "_validated_request", body)
    monkeypatch.setattr(scribe_capture, "check_capture_deadline", lambda: None)
    monkeypatch.setattr(scribe_capture, "DeadlineScribeClient", lambda client: client)
    monkeypatch.setattr(scribe_capture.admission, "CaptureCaller", lambda **_: None)
    monkeypatch.setattr(scribe_capture.gateway, "execute_capture_operation", fail)
    response = asyncio.run(
        scribe_capture.perform_capture_operation(
            Request(
                {
                    "type": "http",
                    "headers": [(b"idempotency-key", b"synthetic-fixed-key")],
                }
            ),
            cast(Any, SimpleNamespace(client_id="synthetic")),
            cast(Any, SimpleNamespace(enabled=True, upload_origins=())),
            cast(Any, SimpleNamespace()),
            lambda: cast(Any, SimpleNamespace(close=close)),
        )
    )
    expected = scribe_capture._admission_problem(
        503, "CAPTURE_OPERATION_UNCONFIRMED", retryable=True
    )
    assert (
        response.status_code == expected.status_code and response.body == expected.body
    )
    assert dict(response.headers) == dict(expected.headers)
    assert closed == [True]
    assert len(events) == 1 and events[0][1]["boundary"] == "capture_operation"
    assert SECRET not in bytes(response.body).decode() + json.dumps(events)
