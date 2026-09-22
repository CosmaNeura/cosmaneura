from types import SimpleNamespace

import pytest
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
    get_clerk_dev_user,
    grant_test_superadmin_capability,
    provision_practice_in_force,
)


def _dev_doctor() -> Doctor:
    _, _, clerk_user = get_clerk_dev_user()
    doctor = Doctor.get(clerk_id=clerk_user.id)
    assert doctor is not None
    return doctor


def _enable_staging_view_as(monkeypatch: pytest.MonkeyPatch) -> None:
    def staging_environment() -> SuperadminCapabilityEnvironment:
        return SuperadminCapabilityEnvironment.staging

    for path in (
        "app.routes.dependencies.view_as.configured_superadmin_capability_environment",
        "app.routes.view_as.configured_superadmin_capability_environment",
        "app.services.superadmin_capability.configured_superadmin_capability_environment",
    ):
        monkeypatch.setattr(path, staging_environment)
    monkeypatch.setattr(
        "app.services.superadmin_capability.configured_superadmin_clerk_issuer",
        lambda: MOCK_CLERK_ISSUER,
    )

    def current_email(clerk_id: str) -> str | None:
        doctor = Doctor.get(clerk_id=clerk_id)
        return doctor.email.strip().lower() if doctor and doctor.email else None

    monkeypatch.setattr("app.services.view_as.verified_primary_email", current_email)


def test_settings_visit_prep_defaults_to_disabled(
    authenticated_client: TestClient,
) -> None:
    response = authenticated_client.get(api_app.url_path_for("settings_data"))

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()["visit_prep"] == {
        "eligible": False,
        "can_manage": False,
        "can_send_email": False,
        "can_send_sms": False,
    }


def test_settings_visit_prep_reads_target_eligibility_during_view_as(
    authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_staging_view_as(monkeypatch)
    actor = _dev_doctor()
    grant_test_superadmin_capability(actor, SuperadminCapabilityKind.view_as)

    target = Doctor(
        clerk_id="user_settings_visit_prep_view_as_target",
        email="settings-visit-prep-view-as-target@example.net",
        biography="",
    ).save()
    provision_practice_in_force(target)

    began = authenticated_client.post(
        api_app.url_path_for("begin_view_as"),
        json={"target_doctor_id": str(target.id)},
    )
    assert began.status_code == status.HTTP_200_OK, began.text
    authenticated_client.headers["X-Cosmaneura-View-As-Binding"] = began.json()[
        "request_binding"
    ]

    resolved: list[tuple[str, bool]] = []

    def target_access(doctor: Doctor, *, view_as_active: bool = False):
        resolved.append((str(doctor.id), view_as_active))
        return SimpleNamespace(
            eligible=doctor.id == target.id,
            can_manage=False,
            can_send_email=False,
            can_send_sms=False,
        )

    monkeypatch.setattr(
        "app.routes.internal.visit_prep_access.resolve_visit_prep_access",
        target_access,
    )

    response = authenticated_client.get(api_app.url_path_for("settings_data"))

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()["doctor_id"] == str(target.id)
    assert response.json()["visit_prep"] == {
        "eligible": True,
        "can_manage": False,
        "can_send_email": False,
        "can_send_sms": False,
    }
    assert resolved == [(str(target.id), True)]
