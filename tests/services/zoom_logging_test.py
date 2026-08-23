from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ZOOM_SERVICE = ROOT / "app/services/zoom.py"
ALLOWED_OPERATIONS = {
    "delete_meeting",
    "get_meeting",
    "get_user_meetings",
    "get_user_profile",
    "get_zak_token",
    "refresh_token",
    "revoke_token",
}
ALLOWED_STATUSES = {"failed", "retrying", "succeeded"}
ALLOWED_ERROR_TYPES = {"invalid_grant", "provider_http_error", "unauthorized"}


def _log_calls() -> list[ast.Call]:
    tree = ast.parse(ZOOM_SERVICE.read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "log"
    ]


def test_zoom_logs_use_hashed_references_and_bounded_fields() -> None:
    calls = _log_calls()
    assert calls

    forbidden_fields = {
        "body",
        "doctor_id",
        "meeting_id",
        "path",
        "provider_user_id",
        "response",
        "zoom_meeting_id",
    }
    allowed_fields = {
        "count",
        "doctor_ref",
        "error_type",
        "meeting_ref",
        "operation",
        "provider_status",
        "status",
    }
    for call in calls:
        assert all(isinstance(argument, ast.Constant) for argument in call.args)
        fields = {keyword.arg for keyword in call.keywords}
        assert not fields.intersection(forbidden_fields)
        assert fields <= allowed_fields
        for keyword in call.keywords:
            if keyword.arg in {"doctor_ref", "meeting_ref"}:
                assert isinstance(keyword.value, ast.Call)
                assert isinstance(keyword.value.func, ast.Name)
                assert keyword.value.func.id == "provider_log_ref"
            elif keyword.arg == "operation":
                if isinstance(keyword.value, ast.Name):
                    assert keyword.value.id == "operation"
                else:
                    assert isinstance(keyword.value, ast.Constant)
                    assert keyword.value.value in ALLOWED_OPERATIONS
            elif keyword.arg == "status":
                assert isinstance(keyword.value, ast.Constant)
                assert keyword.value.value in ALLOWED_STATUSES
            elif keyword.arg == "error_type":
                assert isinstance(keyword.value, ast.Constant)
                assert keyword.value.value in ALLOWED_ERROR_TYPES


def test_zoom_logs_never_include_provider_response_bodies_or_api_paths() -> None:
    source = ZOOM_SERVICE.read_text(encoding="utf-8")
    calls = _log_calls()

    assert "body=response.text" not in source
    assert "path=path" not in source
    assert "doctor_id=str(" not in source
    for call in calls:
        assert not any(
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "response"
            and node.attr == "text"
            for node in ast.walk(call)
        )
        assert "provider_user_id" not in ast.dump(call)
