from datetime import UTC, datetime

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.configuration import session_prep as session_prep_configuration
from app.configuration.session_prep import SessionPrepProviderSettings
from app.server import api_app
from app.services.session_prep_provider_access import (
    SessionPrepProviderAccessDecision,
)

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


def _settings(*, enabled: bool) -> SessionPrepProviderSettings:
    return SessionPrepProviderSettings(enabled=enabled)


def test_provider_rollout_is_global_and_has_no_manual_account_cohort() -> None:
    enabled = _settings(enabled=True)

    assert enabled.allows("user_existing_provider")
    assert enabled.allows("user_newly_onboarded_provider")
    assert not enabled.allows("")
    assert not _settings(enabled=False).allows("user_existing_provider")


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
        _settings(enabled=False),
    )

    response = authenticated_client.get(api_app.url_path_for("settings_data"))

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()["session_prep"] == {
        "eligible": False,
        "can_review_sources": False,
    }


def test_settings_session_prep_discovers_every_current_provider_when_globally_enabled(
    authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doctor = _dev_doctor()
    doctor.onboarding_completed_at = datetime.now(UTC)
    doctor.save()
    monkeypatch.setattr(
        session_prep_configuration,
        "SESSION_PREP_PROVIDER_SETTINGS",
        _settings(enabled=True),
    )

    response = authenticated_client.get(api_app.url_path_for("settings_data"))

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()["session_prep"] == {
        "eligible": True,
        "can_review_sources": True,
    }


def test_settings_incomplete_or_unentitled_provider_is_not_eligible(
    authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        session_prep_configuration,
        "SESSION_PREP_PROVIDER_SETTINGS",
        _settings(enabled=True),
    )

    def access_decision(_doctor: Doctor) -> SessionPrepProviderAccessDecision:
        return SessionPrepProviderAccessDecision(
            allowed=False,
            denial_reason="entitlement_not_in_force",
        )

    monkeypatch.setattr(
        "app.services.session_prep_provider_access.resolve_session_prep_provider_access",
        access_decision,
    )

    response = authenticated_client.get(api_app.url_path_for("settings_data"))

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()["session_prep"] == {
        "eligible": False,
        "can_review_sources": False,
    }


def test_settings_new_provider_becomes_eligible_when_onboarding_access_is_ready(
    authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        session_prep_configuration,
        "SESSION_PREP_PROVIDER_SETTINGS",
        _settings(enabled=True),
    )
    ready = False

    def access_decision(_doctor: Doctor) -> SessionPrepProviderAccessDecision:
        return SessionPrepProviderAccessDecision(
            allowed=ready,
            denial_reason=None if ready else "practice_membership_required",
        )

    monkeypatch.setattr(
        "app.services.session_prep_provider_access.resolve_session_prep_provider_access",
        access_decision,
    )

    before = authenticated_client.get(api_app.url_path_for("settings_data"))
    assert before.status_code == status.HTTP_200_OK, before.text
    assert before.json()["session_prep"] == {
        "eligible": False,
        "can_review_sources": False,
    }

    ready = True
    after = authenticated_client.get(api_app.url_path_for("settings_data"))
    assert after.status_code == status.HTTP_200_OK, after.text
    assert after.json()["session_prep"] == {
        "eligible": True,
        "can_review_sources": True,
    }


def test_settings_session_prep_view_as_is_ineligible(
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
        _settings(enabled=True),
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
        "eligible": False,
        "can_review_sources": False,
    }
