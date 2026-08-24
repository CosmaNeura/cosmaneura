from fastapi import status
from fastapi.testclient import TestClient

from app.server import api_app

from app.models.doctor import Doctor
from app.models.superadmin_capability import (
    SuperadminCapabilityEnvironment,
    SuperadminCapabilityKind,
)

from tests.routes.utils import (
    MOCK_CLERK_ISSUER,
    get_clerk_admin_user,
    grant_test_superadmin_capability,
)


def _enable_staging_capabilities(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.superadmin_capability.configured_superadmin_capability_environment",
        lambda: SuperadminCapabilityEnvironment.staging,
    )
    monkeypatch.setattr(
        "app.services.superadmin_capability.configured_superadmin_clerk_issuer",
        lambda: MOCK_CLERK_ISSUER,
    )


def test_usage_dashboard_rejects_role_admin_without_capability(
    authenticated_client: TestClient,
    monkeypatch,
):
    _enable_staging_capabilities(monkeypatch)
    _, _, clerk_admin = get_clerk_admin_user()
    authenticated_client.override_user(clerk_admin)  # type: ignore[attr-defined]

    response = authenticated_client.get(api_app.url_path_for("usage_dashboard"))

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_usage_dashboard_returns_all_five_sections(
    authenticated_client: TestClient,
    monkeypatch,
):
    _enable_staging_capabilities(monkeypatch)
    _, _, clerk_admin = get_clerk_admin_user()
    admin = Doctor.get(clerk_id=clerk_admin.id)
    assert admin is not None
    grant_test_superadmin_capability(
        admin,
        SuperadminCapabilityKind.platform_admin,
    )
    authenticated_client.override_user(clerk_admin)  # type: ignore[attr-defined]

    response = authenticated_client.get(api_app.url_path_for("usage_dashboard"))

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
