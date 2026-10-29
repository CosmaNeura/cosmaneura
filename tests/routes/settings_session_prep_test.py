import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.configuration import session_prep as session_prep_configuration
from app.configuration.session_prep import SessionPrepProviderSettings
from app.server import api_app
from app.services.doctor_access import DoctorAccessDecision

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


def _settings(*subjects: str) -> SessionPrepProviderSettings:
    return SessionPrepProviderSettings(
        enabled=bool(subjects),
        clerk_subjects=frozenset(subjects),
    )


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


def test_settings_session_prep_defaults_to_ineligible(
    authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        session_prep_configuration,
        "SESSION_PREP_PROVIDER_SETTINGS",
        _settings(),
    )

    response = authenticated_client.get(api_app.url_path_for("settings_data"))

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()["session_prep"] == {
        "eligible": False,
        "can_review_sources": False,
    }


def test_settings_session_prep_discovers_selected_provider_without_granting_more(
    authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doctor = _dev_doctor()
    monkeypatch.setattr(
        session_prep_configuration,
        "SESSION_PREP_PROVIDER_SETTINGS",
        _settings(doctor.clerk_id),
    )

    response = authenticated_client.get(api_app.url_path_for("settings_data"))

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()["session_prep"] == {
        "eligible": True,
        "can_review_sources": True,
    }


def test_settings_selected_provider_without_current_access_cannot_review(
    authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doctor = _dev_doctor()
    monkeypatch.setattr(
        session_prep_configuration,
        "SESSION_PREP_PROVIDER_SETTINGS",
        _settings(doctor.clerk_id),
    )

    def access_decision(
        _doctor: Doctor, *, require_entitlement: bool = True
    ) -> DoctorAccessDecision:
        if not require_entitlement:
            return DoctorAccessDecision(
                allowed=True,
                authorization_basis="organization_roster",
                denial_reason=None,
                baa_in_force=True,
                entitlement_in_force=False,
            )
        return DoctorAccessDecision(
            allowed=False,
            authorization_basis=None,
            denial_reason="entitlement_not_in_force",
            baa_in_force=True,
            entitlement_in_force=False,
        )

    monkeypatch.setattr("app.routes.internal.resolve_doctor_access", access_decision)

    response = authenticated_client.get(api_app.url_path_for("settings_data"))

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()["session_prep"] == {
        "eligible": True,
        "can_review_sources": False,
    }


def test_settings_session_prep_view_as_is_discovery_only(
    authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_staging_view_as(monkeypatch)
    actor = _dev_doctor()
    grant_test_superadmin_capability(actor, SuperadminCapabilityKind.view_as)

    target = Doctor(
        clerk_id="user_settings_session_prep_view_as_target",
        email="settings-session-prep-view-as-target@example.net",
        biography="",
    ).save()
    provision_practice_in_force(target)
    monkeypatch.setattr(
        session_prep_configuration,
        "SESSION_PREP_PROVIDER_SETTINGS",
        _settings(target.clerk_id),
    )

    began = authenticated_client.post(
        api_app.url_path_for("begin_view_as"),
        json={"target_doctor_id": str(target.id)},
    )
    assert began.status_code == status.HTTP_200_OK, began.text
    authenticated_client.headers["X-Cosmaneura-View-As-Binding"] = began.json()[
        "request_binding"
    ]

    response = authenticated_client.get(api_app.url_path_for("settings_data"))

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()["doctor_id"] == str(target.id)
    assert response.json()["session_prep"] == {
        "eligible": True,
        "can_review_sources": False,
    }
    # The actor's own Clerk subject never becomes the target's review authority.
    assert actor.clerk_id not in _settings(target.clerk_id).clerk_subjects
