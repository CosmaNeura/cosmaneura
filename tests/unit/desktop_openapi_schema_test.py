import json

from app.server import api_app
from app.utils.desktop_openapi import (
    DESKTOP_API_PREFIX,
    DESKTOP_BEARER_SCHEME,
    DESKTOP_OPENAPI_PATH,
    generate_desktop_openapi_schema,
    render_desktop_openapi_schema,
)

EXPECTED_OPERATIONS = {
    "/desktop/v1/capture-sessions/{capture_session_id}/heartbeats": (
        "record_desktop_capture_heartbeat"
    ),
    "/desktop/v1/capture-sessions/{capture_session_id}/segments/start-authorizations": (
        "authorize_desktop_capture_segment_start"
    ),
    "/desktop/v1/capture-sessions/{capture_session_id}/stop-requests": (
        "request_desktop_capture_stop"
    ),
    "/desktop/v1/oauth/token": "exchange_desktop_capture_code",
}


def test_desktop_openapi_schema_matches_generated_file() -> None:
    assert DESKTOP_OPENAPI_PATH.read_text() == render_desktop_openapi_schema(api_app), (
        "Desktop OpenAPI schema is stale. Run:\njust desktop-openapi"
    )


def test_desktop_openapi_exposes_only_stable_desktop_operations() -> None:
    schema = generate_desktop_openapi_schema(api_app)

    assert set(schema["paths"]) == set(EXPECTED_OPERATIONS)
    assert all(path.startswith(f"{DESKTOP_API_PREFIX}/") for path in schema["paths"])
    assert {
        path: operations["post"]["operationId"]
        for path, operations in schema["paths"].items()
    } == EXPECTED_OPERATIONS


def test_desktop_openapi_models_core_only_bearer_and_required_idempotency() -> None:
    schema = generate_desktop_openapi_schema(api_app)

    assert schema["components"]["securitySchemes"][DESKTOP_BEARER_SCHEME] == {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "opaque",
        "description": (
            "Short-lived opaque desktop session bearer. Keep it in the Tauri "
            "core process and never expose it to the webview."
        ),
    }
    for path, path_item in schema["paths"].items():
        operation = path_item["post"]
        if path == f"{DESKTOP_API_PREFIX}/oauth/token":
            assert "security" not in operation
            continue
        assert operation["security"] == [{DESKTOP_BEARER_SCHEME: []}]
        headers = {
            parameter["name"].lower(): parameter
            for parameter in operation["parameters"]
            if parameter["in"] == "header"
        }
        assert "authorization" not in headers
        assert headers["idempotency-key"]["required"] is True


def test_desktop_openapi_documents_value_free_error_shape() -> None:
    schema = generate_desktop_openapi_schema(api_app)

    assert schema["components"]["schemas"]["DesktopApiErrorResponse"] == {
        "additionalProperties": False,
        "description": (
            "Value-free error body used by every `/desktop/v1` failure response."
        ),
        "properties": {"detail": {"title": "Detail", "type": "string"}},
        "required": ["detail"],
        "title": "DesktopApiErrorResponse",
        "type": "object",
    }
    for path_item in schema["paths"].values():
        validation_response = path_item["post"]["responses"]["422"]
        assert validation_response["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/DesktopApiErrorResponse"
        }


def test_desktop_openapi_uses_tauri_boundary_language() -> None:
    rendered = json.dumps(generate_desktop_openapi_schema(api_app))

    assert "Electron" not in rendered
    assert "Tauri" in rendered
