from types import SimpleNamespace

import pytest
from fastapi import HTTPException, status
from starlette.requests import Request

from app.routes import admin

from app.models.doctor import DoctorRole


def _request(role: DoctorRole | None) -> Request:
    request = Request({"type": "http", "headers": []})
    if role is not None:
        request.state.doctor = SimpleNamespace(role=role)
    return request


@pytest.mark.parametrize("role", [None, DoctorRole.normal])
def test_webhook_admin_routes_fail_closed_for_missing_or_non_admin(
    role: DoctorRole | None,
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        admin.require_admin(_request(role))
    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED


def test_webhook_admin_routes_accept_admin() -> None:
    assert admin.require_admin(_request(DoctorRole.admin)) is None


def test_webhook_inbox_health_is_metadata_only(monkeypatch) -> None:
    health = {
        "pending": 1,
        "processing": 2,
        "retry_wait": 3,
        "completed": 4,
        "dead_letter": 5,
        "oldest_actionable_age_seconds": 6.0,
        "uncertain_operations": 7,
    }
    monkeypatch.setattr(admin.external_webhook_inbox, "inbox_health", lambda: health)

    response = admin.webhook_inbox_health().model_dump()

    assert response == health
    assert "payload" not in response
    assert "provider" not in response


def test_webhook_inbox_redrive_returns_generic_result(monkeypatch) -> None:
    monkeypatch.setattr(
        admin.external_webhook_inbox, "redrive_delivery", lambda _delivery_id: True
    )
    assert admin.webhook_inbox_redrive("opaque-internal-id").status == "accepted"

    monkeypatch.setattr(
        admin.external_webhook_inbox, "redrive_delivery", lambda _delivery_id: False
    )
    with pytest.raises(HTTPException) as exc_info:
        admin.webhook_inbox_redrive("opaque-internal-id")
    assert exc_info.value.status_code == status.HTTP_409_CONFLICT
    assert exc_info.value.detail == "Delivery cannot be redriven."
