from fastapi import status
from fastapi.testclient import TestClient

from app.server import api_app

from tests.routes.utils import (
    get_clerk_admin_user,
    get_clerk_dev_user,
    get_valid_token,
)


def _clerk_authorization(clerk_user):
    return {"Authorization": f"Bearer {get_valid_token(clerk_user)}"}


def test_usage_dashboard_rejects_non_admin(client: TestClient):
    _, _, clerk_user = get_clerk_dev_user()

    response = client.get(
        api_app.url_path_for("usage_dashboard"),
        headers=_clerk_authorization(clerk_user),
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_usage_dashboard_returns_all_five_sections(client: TestClient):
    _, _, clerk_admin = get_clerk_admin_user()

    response = client.get(
        api_app.url_path_for("usage_dashboard"),
        headers=_clerk_authorization(clerk_admin),
    )

    assert response.status_code == status.HTTP_200_OK
    body = response.json()

    expected_keys = {
        "recent_appointments",
        "doctor_usage",
        "daily_volume",
        "pending_uploads",
        "time_to_note",
    }
    assert set(body.keys()) == expected_keys
    for key in expected_keys:
        assert isinstance(body[key], list), f"{key} should be a list"
