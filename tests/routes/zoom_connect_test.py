"""Tests for the Zoom OAuth Authorization Code Grant flow."""

import hashlib
import json
import re
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import cast
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from httpx import Response
from structlog.testing import capture_logs

from app.configuration.redis import get_redis
from app.server import api_app
from app.services import doctor_access as doctor_access_service
from app.services import zoom_oauth_state
from app.services.public_capabilities import DEFAULT_CAPABILITY_ROLLOUTS

from app.models.doctor import Doctor
from app.models.doctor_video_integration import DoctorVideoIntegration, VideoProvider
from app.models.practice import Practice, PracticeMember
from app.models.superadmin_capability import (
    SuperadminCapabilityEnvironment,
    SuperadminCapabilityKind,
)
from app.models.view_as import ViewAsSession

from tests.routes.utils import get_clerk_dev_user, grant_test_superadmin_capability

CLERK_DEV_USER_EMAIL = "dev@test.com"


def _get_or_create_doctor():
    _, _, clerk_user = get_clerk_dev_user()
    doctor = Doctor.find_or_create_by(clerk_id=clerk_user.id)
    if not doctor.email:
        doctor.email = CLERK_DEV_USER_EMAIL
        doctor.save()
    return doctor


def _make_integration(
    doctor: Doctor, provider_user_id: str = "zoom_abc123"
) -> DoctorVideoIntegration:
    return DoctorVideoIntegration(
        doctor_id=doctor.id,
        provider=VideoProvider.zoom,
        provider_user_id=provider_user_id,
        access_token="access_token",
        refresh_token="refresh_token",
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    ).save()


def _issue_oauth_state(client: TestClient) -> tuple[str, str]:
    response = client.get(api_app.url_path_for("zoom_auth_url"))
    assert response.status_code == status.HTTP_200_OK
    url = response.json()["url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    return url, state


def _assert_authorization_failed(response: Response) -> None:
    assert response.status_code == status.HTTP_302_FOUND
    assert response.headers["location"].endswith("?zoom_error=authorization_failed")


def _configure_public_soap_rollout(
    monkeypatch: pytest.MonkeyPatch,
    doctor: Doctor,
    *,
    rollout: str,
    public_pilot_cohort: bool,
) -> None:
    """Exercise the real capability helper while isolating its rollout layer."""

    member = PracticeMember.get(doctor_id=doctor.id)
    assert member is not None
    practice = Practice.get(id=member.practice_id)
    assert practice is not None
    practice.public_onboarding_required = True
    practice.public_pilot_cohort = public_pilot_cohort
    practice.save()
    monkeypatch.setitem(DEFAULT_CAPABILITY_ROLLOUTS, "soap_note", rollout)
    monkeypatch.setattr(
        doctor_access_service,
        "resolve_doctor_access",
        lambda *_args, **_kwargs: doctor_access_service.DoctorAccessDecision(
            allowed=True,
            authorization_basis="workforce_attestation",
            denial_reason=None,
            baa_in_force=True,
            entitlement_in_force=True,
        ),
    )


# --- zoom-auth-url ---


def test_zoom_auth_url_returns_start_url(authenticated_client: TestClient):
    """The authenticated endpoint issues opaque state and a hardened cookie."""
    doctor = _get_or_create_doctor()

    response = authenticated_client.get(api_app.url_path_for("zoom_auth_url"))

    assert response.status_code == status.HTTP_200_OK
    url = response.json()["url"]
    assert "/external/v1/zoom-oauth-start" in url
    state = parse_qs(urlparse(url).query)["state"][0]
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", state)
    assert doctor.clerk_id not in url
    assert str(doctor.id) not in url

    redis_key = zoom_oauth_state.zoom_oauth_state_redis_key(state)
    stored = cast(bytes | None, get_redis().get(redis_key))
    assert stored is not None
    assert state not in redis_key
    assert str(doctor.id).encode() not in stored
    browser_binding = authenticated_client.cookies.get(
        zoom_oauth_state.ZOOM_OAUTH_BINDING_COOKIE
    )
    assert browser_binding is not None
    assert browser_binding.encode() not in stored
    ttl = cast(int, get_redis().ttl(redis_key))
    assert 0 < ttl <= zoom_oauth_state.ZOOM_OAUTH_STATE_TTL_SECONDS

    set_cookie = response.headers["set-cookie"].lower()
    assert zoom_oauth_state.ZOOM_OAUTH_BINDING_COOKIE.lower() in set_cookie
    assert "httponly" in set_cookie
    assert "secure" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "max-age=300" in set_cookie
    assert "path=/" in set_cookie
    assert "domain=" not in set_cookie
    assert zoom_oauth_state.ZOOM_OAUTH_BINDING_COOKIE.startswith("__Host-")


# --- zoom-oauth-start ---


def test_zoom_oauth_start_redirects_only_for_issued_state(
    authenticated_client: TestClient,
):
    """GET /zoom-oauth-start validates state before redirecting to Zoom."""
    start_url, state_value = _issue_oauth_state(authenticated_client)
    response = authenticated_client.get(start_url, follow_redirects=False)

    assert response.status_code == status.HTTP_302_FOUND
    assert "zoom.us/oauth/authorize" in response.headers["location"]
    assert parse_qs(urlparse(response.headers["location"]).query)["state"] == [
        state_value
    ]


def test_zoom_oauth_start_rejects_forged_state(client: TestClient):
    response = client.get(
        api_app.url_path_for("zoom_oauth_start"),
        params={"state": secrets.token_urlsafe(32)},
        follow_redirects=False,
    )

    _assert_authorization_failed(response)
    assert "zoom.us" not in response.headers["location"]


# --- zoom-oauth-callback ---

_FAKE_TOKENS = {
    "access_token": "new_access",
    "refresh_token": "new_refresh",
    "expires_in": 3600,
}


def test_zoom_oauth_callback_creates_integration(authenticated_client: TestClient):
    """Callback with valid code + state upserts a DoctorVideoIntegration."""
    doctor = _get_or_create_doctor()
    _, state_value = _issue_oauth_state(authenticated_client)

    with (
        patch(
            "app.routes.zoom_auth._exchange_code_for_tokens", return_value=_FAKE_TOKENS
        ),
        patch("app.routes.zoom_auth._get_zoom_user_id", return_value="zoom_xyz"),
        patch(
            "app.routes.zoom_auth.ZOOM_SUCCESS_REDIRECT", "https://example.com/settings"
        ),
        capture_logs() as logs,
    ):
        response = authenticated_client.get(
            api_app.url_path_for("zoom_oauth_callback"),
            params={"code": "auth_code_123", "state": state_value},
            follow_redirects=False,
        )

    assert response.status_code == status.HTTP_302_FOUND
    integration = DoctorVideoIntegration.get(
        doctor_id=doctor.id, provider=VideoProvider.zoom
    )
    assert integration is not None
    assert integration.provider_user_id == "zoom_xyz"
    assert integration.access_token == "new_access"

    serialized = json.dumps(logs, sort_keys=True)
    for private_value in (
        "zoom_xyz",
        str(doctor.id),
        doctor.clerk_id,
        state_value,
        "auth_code_123",
        "new_access",
        "new_refresh",
    ):
        assert private_value not in serialized
    assert "hmac:" in serialized


def test_zoom_oauth_callback_updates_existing_integration(
    authenticated_client: TestClient,
):
    """Callback upserts when an integration already exists."""
    doctor = _get_or_create_doctor()
    _make_integration(doctor, provider_user_id="old_zoom_id")
    _, state_value = _issue_oauth_state(authenticated_client)

    with (
        patch(
            "app.routes.zoom_auth._exchange_code_for_tokens", return_value=_FAKE_TOKENS
        ),
        patch("app.routes.zoom_auth._get_zoom_user_id", return_value="new_zoom_id"),
        patch(
            "app.routes.zoom_auth.ZOOM_SUCCESS_REDIRECT", "https://example.com/settings"
        ),
    ):
        response = authenticated_client.get(
            api_app.url_path_for("zoom_oauth_callback"),
            params={"code": "auth_code_123", "state": state_value},
            follow_redirects=False,
        )

    assert response.status_code == status.HTTP_302_FOUND
    integration = DoctorVideoIntegration.get(
        doctor_id=doctor.id, provider=VideoProvider.zoom
    )
    assert integration is not None
    assert integration.provider_user_id == "new_zoom_id"


@pytest.mark.parametrize(
    ("rollout", "public_pilot_cohort"),
    [
        ("disabled", True),
        ("cohort", False),
    ],
)
def test_zoom_oauth_callback_real_public_rollout_denial_consumes_state(
    authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    rollout: str,
    public_pilot_cohort: bool,
):
    """Disabled and cohort-ineligible public callbacks fail before exchange."""

    doctor = _get_or_create_doctor()
    _, state_value = _issue_oauth_state(authenticated_client)
    _configure_public_soap_rollout(
        monkeypatch,
        doctor,
        rollout=rollout,
        public_pilot_cohort=public_pilot_cohort,
    )

    with (
        patch("app.routes.zoom_auth._exchange_code_for_tokens") as exchange,
        patch("app.routes.zoom_auth._get_zoom_user_id") as get_user,
    ):
        response = authenticated_client.get(
            api_app.url_path_for("zoom_oauth_callback"),
            params={"code": "must-not-be-exchanged", "state": state_value},
            follow_redirects=False,
        )

    _assert_authorization_failed(response)
    exchange.assert_not_called()
    get_user.assert_not_called()
    assert not zoom_oauth_state.is_issued_zoom_oauth_state(state_value)
    assert (
        DoctorVideoIntegration.get(
            doctor_id=doctor.id,
            provider=VideoProvider.zoom,
        )
        is None
    )


def test_zoom_oauth_callback_real_public_cohort_eligible_writes_integration(
    authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    """The real helper permits an otherwise-authorized cohort member."""

    doctor = _get_or_create_doctor()
    _, state_value = _issue_oauth_state(authenticated_client)
    _configure_public_soap_rollout(
        monkeypatch,
        doctor,
        rollout="cohort",
        public_pilot_cohort=True,
    )

    with (
        patch(
            "app.routes.zoom_auth._exchange_code_for_tokens", return_value=_FAKE_TOKENS
        ),
        patch("app.routes.zoom_auth._get_zoom_user_id", return_value="zoom_cohort"),
    ):
        response = authenticated_client.get(
            api_app.url_path_for("zoom_oauth_callback"),
            params={"code": "cohort-code", "state": state_value},
            follow_redirects=False,
        )

    assert response.status_code == status.HTTP_302_FOUND
    integration = DoctorVideoIntegration.get(
        doctor_id=doctor.id,
        provider=VideoProvider.zoom,
    )
    assert integration is not None
    assert integration.provider_user_id == "zoom_cohort"


def test_zoom_oauth_callback_rechecks_capability_after_provider_exchange(
    authenticated_client: TestClient,
):
    """An in-flight rollout revocation cannot persist returned credentials."""

    doctor = _get_or_create_doctor()
    _, state_value = _issue_oauth_state(authenticated_client)

    with (
        patch(
            "app.routes.zoom_auth.doctor_capability_access_in_force",
            side_effect=[True, False],
        ) as capability_access,
        patch(
            "app.routes.zoom_auth._exchange_code_for_tokens", return_value=_FAKE_TOKENS
        ) as exchange,
        patch(
            "app.routes.zoom_auth._get_zoom_user_id", return_value="zoom_revoked"
        ) as get_user,
    ):
        response = authenticated_client.get(
            api_app.url_path_for("zoom_oauth_callback"),
            params={"code": "in-flight-code", "state": state_value},
            follow_redirects=False,
        )

    _assert_authorization_failed(response)
    assert capability_access.call_count == 2
    exchange.assert_called_once_with("in-flight-code")
    get_user.assert_called_once_with("new_access")
    assert not zoom_oauth_state.is_issued_zoom_oauth_state(state_value)
    assert (
        DoctorVideoIntegration.get(
            doctor_id=doctor.id,
            provider=VideoProvider.zoom,
        )
        is None
    )


def test_zoom_oauth_callback_without_app_cookie_cannot_write_during_view_as(
    authenticated_client: TestClient,
):
    """Server-side view-as state blocks a pre-issued cookie-bound callback."""

    doctor = _get_or_create_doctor()
    _, state_value = _issue_oauth_state(authenticated_client)
    target = Doctor(
        clerk_id=f"user_zoom_view_as_target_{secrets.token_hex(5)}",
        email=f"zoom-view-as-target-{secrets.token_hex(5)}@example.com",
        biography="",
    ).save()
    grant = grant_test_superadmin_capability(
        doctor,
        SuperadminCapabilityKind.view_as,
        environment=SuperadminCapabilityEnvironment.staging,
    )
    now = datetime.now(timezone.utc)
    ViewAsSession(
        environment=SuperadminCapabilityEnvironment.staging.value,
        capability_grant_id=grant.id,
        actor_doctor_id=doctor.id,
        target_doctor_id=target.id,
        actor_issuer_sha256=grant.issuer_sha256,
        actor_subject_sha256=grant.subject_sha256,
        clerk_session_sha256=hashlib.sha256(b"zoom-session").hexdigest(),
        browser_nonce_sha256=hashlib.sha256(b"zoom-nonce").hexdigest(),
        started_at=now,
        absolute_expires_at=now + timedelta(minutes=15),
    ).save()

    # The direct service-created view-as record deliberately leaves the
    # browser without the app's signed view-as cookie while preserving the
    # independent OAuth binding cookie issued above.
    with (
        patch("app.routes.zoom_auth._exchange_code_for_tokens") as exchange,
        patch("app.routes.zoom_auth._get_zoom_user_id") as get_user,
    ):
        response = authenticated_client.get(
            api_app.url_path_for("zoom_oauth_callback"),
            params={"code": "must-not-be-exchanged", "state": state_value},
            follow_redirects=False,
        )

    _assert_authorization_failed(response)
    exchange.assert_not_called()
    get_user.assert_not_called()
    assert (
        DoctorVideoIntegration.get(
            doctor_id=doctor.id,
            provider=VideoProvider.zoom,
        )
        is None
    )


def test_zoom_oauth_callback_error_param_consumes_state(
    authenticated_client: TestClient,
):
    """A provider rejection is generic and consumes the issued state."""
    doctor = _get_or_create_doctor()
    provider_error = "access_denied-private-clinical-sentinel"
    _, state_value = _issue_oauth_state(authenticated_client)

    with (
        patch(
            "app.routes.zoom_auth.ZOOM_SUCCESS_REDIRECT",
            "https://example.com/settings",
        ),
        patch("app.routes.zoom_auth._exchange_code_for_tokens") as exchange,
        capture_logs() as logs,
    ):
        response = authenticated_client.get(
            api_app.url_path_for("zoom_oauth_callback"),
            params={"error": provider_error, "state": state_value},
            follow_redirects=False,
        )

    assert response.status_code == status.HTTP_302_FOUND
    assert response.headers["location"] == (
        "https://example.com/settings?zoom_error=authorization_failed"
    )
    assert provider_error not in response.headers["location"]
    serialized = json.dumps(logs, sort_keys=True)
    assert provider_error not in serialized
    assert doctor.clerk_id not in serialized
    assert state_value not in serialized
    assert not zoom_oauth_state.is_issued_zoom_oauth_state(state_value)
    exchange.assert_not_called()
    assert any(
        entry.get("event_code") == "zoom_oauth_provider_rejected" for entry in logs
    )


@pytest.mark.parametrize("state_value", [None, "not-an-issued-state", "A" * 43])
def test_zoom_oauth_callback_rejects_invalid_state_before_provider_calls(
    client: TestClient, state_value: str | None
):
    with (
        patch("app.routes.zoom_auth._exchange_code_for_tokens") as exchange,
        patch("app.routes.zoom_auth._get_zoom_user_id") as get_user,
    ):
        response = client.get(
            api_app.url_path_for("zoom_oauth_callback"),
            params={"code": "must-not-be-exchanged", "state": state_value},
            follow_redirects=False,
        )

    _assert_authorization_failed(response)
    exchange.assert_not_called()
    get_user.assert_not_called()


def test_zoom_oauth_callback_rejects_expired_state_before_provider_calls(
    authenticated_client: TestClient,
):
    _, state_value = _issue_oauth_state(authenticated_client)
    get_redis().delete(zoom_oauth_state.zoom_oauth_state_redis_key(state_value))

    with patch("app.routes.zoom_auth._exchange_code_for_tokens") as exchange:
        response = authenticated_client.get(
            api_app.url_path_for("zoom_oauth_callback"),
            params={"code": "must-not-be-exchanged", "state": state_value},
            follow_redirects=False,
        )

    _assert_authorization_failed(response)
    exchange.assert_not_called()


def test_zoom_oauth_callback_is_bound_to_the_issuing_browser(
    authenticated_client: TestClient, client: TestClient
):
    doctor = _get_or_create_doctor()
    _, state_value = _issue_oauth_state(authenticated_client)

    with (
        patch(
            "app.routes.zoom_auth._exchange_code_for_tokens", return_value=_FAKE_TOKENS
        ) as exchange,
        patch("app.routes.zoom_auth._get_zoom_user_id", return_value="zoom_bound"),
    ):
        wrong_browser = client.get(
            api_app.url_path_for("zoom_oauth_callback"),
            params={"code": "wrong-browser-code", "state": state_value},
            headers={
                "cookie": (
                    f"{zoom_oauth_state.ZOOM_OAUTH_BINDING_COOKIE}="
                    f"{secrets.token_urlsafe(32)}"
                )
            },
            follow_redirects=False,
        )
        right_browser = authenticated_client.get(
            api_app.url_path_for("zoom_oauth_callback"),
            params={"code": "right-browser-code", "state": state_value},
            follow_redirects=False,
        )

    _assert_authorization_failed(wrong_browser)
    assert right_browser.status_code == status.HTTP_302_FOUND
    assert right_browser.headers["location"] != wrong_browser.headers["location"]
    exchange.assert_called_once_with("right-browser-code")
    assert DoctorVideoIntegration.get(doctor_id=doctor.id, provider=VideoProvider.zoom)


def test_zoom_oauth_callback_replay_does_not_call_provider_twice(
    authenticated_client: TestClient,
):
    _, state_value = _issue_oauth_state(authenticated_client)

    with (
        patch(
            "app.routes.zoom_auth._exchange_code_for_tokens", return_value=_FAKE_TOKENS
        ) as exchange,
        patch("app.routes.zoom_auth._get_zoom_user_id", return_value="zoom_once"),
    ):
        first = authenticated_client.get(
            api_app.url_path_for("zoom_oauth_callback"),
            params={"code": "first-code", "state": state_value},
            follow_redirects=False,
        )
        second = authenticated_client.get(
            api_app.url_path_for("zoom_oauth_callback"),
            params={"code": "replayed-code", "state": state_value},
            follow_redirects=False,
        )

    assert first.status_code == status.HTTP_302_FOUND
    _assert_authorization_failed(second)
    exchange.assert_called_once_with("first-code")


def test_zoom_oauth_state_concurrent_consume_succeeds_exactly_once():
    doctor = _get_or_create_doctor()
    issued = zoom_oauth_state.issue_zoom_oauth_state(str(doctor.id))
    barrier = threading.Barrier(2)

    def consume():
        barrier.wait()
        return zoom_oauth_state.consume_zoom_oauth_state(
            issued.state, issued.browser_binding
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: consume(), range(2)))

    assert [str(result) if result is not None else None for result in results].count(
        str(doctor.id)
    ) == 1
    assert results.count(None) == 1


# --- zoom-disconnect ---


def test_zoom_disconnect_revokes_and_deletes(authenticated_client: TestClient):
    """DELETE /zoom-connect calls revoke_token, deleting the integration."""
    doctor = _get_or_create_doctor()
    _make_integration(doctor)

    with patch("app.routes.internal.zoom_service.revoke_token") as mock_revoke:
        response = authenticated_client.delete(api_app.url_path_for("zoom_disconnect"))

    assert response.status_code == status.HTTP_204_NO_CONTENT
    mock_revoke.assert_called_once()


def test_zoom_disconnect_not_connected_raises(authenticated_client: TestClient):
    """DELETE /zoom-connect raises AssertionError when no integration exists."""
    _get_or_create_doctor()

    with (
        patch(
            "app.routes.internal.zoom_service.revoke_token", side_effect=AssertionError
        ),
        pytest.raises(AssertionError),
    ):
        authenticated_client.delete(api_app.url_path_for("zoom_disconnect"))
