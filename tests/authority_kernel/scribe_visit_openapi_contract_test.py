"""A clean generated dashboard client must describe the real visit routes."""

import asyncio
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from starlette.requests import Request
from typeid import TypeID

from app.routes.internal import internal_api_app
from app.routes.scribe_visit_extras import PrivateNoteValidationError, router
from app.server import safe_request_validation_handler
from app.utils.openapi import generate_openapi_schema


@pytest.fixture(scope="module")
def contracts():
    committed = json.loads((Path(__file__).parents[2] / "web/openapi.json").read_text())
    actual = generate_openapi_schema(internal_api_app)
    return committed, actual


def test_private_note_generated_client_operations_match_live_route_contract(contracts):
    committed, actual = contracts
    path = "/internal/v1/notes/{appointment_id}/private-note"
    assert committed["paths"][path] == actual["paths"][path]
    assert {
        method: value["operationId"]
        for method, value in committed["paths"][path].items()
    } == {
        "get": "scribe_private_note_read",
        "put": "scribe_private_note_save",
    }


def test_private_author_and_patient_identity_models_match_live_contract(contracts):
    committed, actual = contracts
    for name in (
        "PrivateNoteSave",
        "PrivateNoteView",
        "PrivateNoteValidationError",
        "NoteDetail",
    ):
        assert (
            committed["components"]["schemas"][name]
            == actual["components"]["schemas"][name]
        )
    properties = committed["components"]["schemas"]["NoteDetail"]["properties"]
    assert {
        "canonical_patient",
        "patient_identity_state",
        "private_note_available",
    } <= properties.keys()


@pytest.mark.parametrize("method", ["get", "put"])
def test_private_note_validation_wire_response_matches_sanitized_contract(method):
    app = FastAPI()
    app.include_router(router, prefix="/internal/v1")
    app.add_exception_handler(RequestValidationError, safe_request_validation_handler)
    client = TestClient(app)
    if method == "get":
        # TypeID's shared converter can raise its own exception before FastAPI
        # validation. Exercise the registered sanitizer without changing it.
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/internal/v1/notes/invented/private-note",
                "headers": [],
            }
        )
        response = asyncio.run(
            safe_request_validation_handler(
                request,
                RequestValidationError(
                    [{"input": "invented private body sentinel", "type": "value_error"}]
                ),
            )
        )
        payload = json.loads(response.body)
    else:
        response = client.put(
            f"/internal/v1/notes/{TypeID(prefix='appointment')}/private-note",
            json={"text": "invented private body sentinel", "unexpected": True},
        )
        payload = response.json()
    assert response.status_code == 422
    assert response.headers["Cache-Control"] == "no-store"
    assert payload == {"detail": "Invalid private note request."}
    assert PrivateNoteValidationError.model_validate(payload).model_dump() == payload
