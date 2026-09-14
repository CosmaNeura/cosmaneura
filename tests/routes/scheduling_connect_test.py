"""Tests for the SchedulingProvider OAuth flow (app/services/scheduling_oauth_flow.py).

Acuity is used as the concrete provider under test since scheduling_oauth_state.py /
scheduling_oauth_flow.py are provider-agnostic and Google Calendar exercises the
exact same shared code path (see zoom_connect_test.py for the more exhaustive
version of these safety properties this generalizes from).
"""

import json
import secrets
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from app.configuration.redis import get_redis
from app.server import api_app
from app.services import scheduling_oauth_state
from app.services.scheduling_oauth_flow import ExchangedSchedulingTokens

from app.models.doctor import Doctor
from app.models.doctor_scheduling_integration import DoctorSchedulingIntegration
from app.models.patient_external_identity import SchedulingProviderSource

from tests.routes.utils import get_clerk_dev_user

CLERK_DEV_USER_EMAIL = "dev@test.com"


def _get_or_create_doctor():
    _, _, clerk_user = get_clerk_dev_user()
    doctor = Doctor.find_or_create_by(clerk_id=clerk_user.id)
    if not doctor.email:
        doctor.email = CLERK_DEV_USER_EMAIL
        doctor.save()
    return doctor


def _issue_oauth_state(client: TestClient) -> tuple[str, str]:
    response = client.get(api_app.url_path_for("acuity_auth_url"))
    assert response.status_code == status.HTTP_200_OK
    url = response.json()["url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    return url, state


def _assert_authorization_failed(response) -> None:
    assert response.status_code == status.HTTP_302_FOUND
    assert response.headers["location"].endswith(
        "?scheduling_error=authorization_failed"
    )


_FAKE_TOKENS = ExchangedSchedulingTokens(
    access_token="new_access",
    refresh_token=None,
    token_expires_at=None,
    provider_calendar_ref="calendar-123",
)


def test_acuity_auth_url_returns_start_url(authenticated_client: TestClient):
    doctor = _get_or_create_doctor()

    response = authenticated_client.get(api_app.url_path_for("acuity_auth_url"))

    assert response.status_code == status.HTTP_200_OK
    url = response.json()["url"]
    assert "/external/v1/acuity-oauth-start" in url
    assert doctor.clerk_id not in url
    assert str(doctor.id) not in url


def test_acuity_oauth_start_redirects_only_for_issued_state(
    authenticated_client: TestClient,
):
    start_url, state_value = _issue_oauth_state(authenticated_client)
    response = authenticated_client.get(start_url, follow_redirects=False)

    assert response.status_code == status.HTTP_302_FOUND
    assert "acuityscheduling.com/oauth2/authorize" in response.headers["location"]
    assert parse_qs(urlparse(response.headers["location"]).query)["state"] == [
        state_value
    ]


def test_acuity_oauth_start_rejects_forged_state(client: TestClient):
    response = client.get(
        api_app.url_path_for("acuity_oauth_start"),
        params={"state": secrets.token_urlsafe(32)},
        follow_redirects=False,
    )

    _assert_authorization_failed(response)
    assert "acuityscheduling.com" not in response.headers["location"]


def test_acuity_oauth_callback_creates_integration_and_queues_sync(
    authenticated_client: TestClient,
):
    """The connect flow must actually populate the roster, not just store a
    credential (2026-09-14: found the sync/backfill jobs had no caller at all)."""
    doctor = _get_or_create_doctor()
    _, state_value = _issue_oauth_state(authenticated_client)

    with (
        patch(
            "app.routes.acuity_auth._exchange_code_for_tokens",
            return_value=_FAKE_TOKENS,
        ),
        patch(
            "app.routes.acuity_auth.ACUITY_SUCCESS_REDIRECT",
            "https://example.com/settings",
        ),
        patch(
            "app.services.scheduling_oauth_flow.backfill_scheduling_roster.queue"
        ) as backfill_queue,
        patch(
            "app.services.scheduling_oauth_flow.sync_scheduling_provider.queue"
        ) as sync_queue,
        capture_logs() as logs,
    ):
        response = authenticated_client.get(
            api_app.url_path_for("acuity_oauth_callback"),
            params={"code": "auth_code_123", "state": state_value},
            follow_redirects=False,
        )

    assert response.status_code == status.HTTP_302_FOUND
    integration = DoctorSchedulingIntegration.get(
        doctor_id=doctor.id, source=SchedulingProviderSource.acuity
    )
    assert integration is not None
    assert integration.access_token == "new_access"
    assert integration.provider_calendar_ref == "calendar-123"

    backfill_queue.assert_called_once_with(str(doctor.id), "acuity")
    sync_queue.assert_called_once_with(str(doctor.id), "acuity")

    serialized = json.dumps(logs, sort_keys=True)
    for private_value in (
        str(doctor.id),
        doctor.clerk_id,
        state_value,
        "auth_code_123",
        "new_access",
    ):
        assert private_value not in serialized
    assert "hmac:" in serialized


def test_acuity_oauth_callback_reconnect_requeues_backfill(
    authenticated_client: TestClient,
):
    """A reconnect (e.g. after a revoked grant) must re-queue the backfill too:
    perform() itself is idempotent via last_backfilled_at, so it's always safe
    to ask again here rather than only doing it for a brand-new integration."""
    doctor = _get_or_create_doctor()
    DoctorSchedulingIntegration(
        doctor_id=doctor.id,
        source=SchedulingProviderSource.acuity,
        provider_calendar_ref="old-calendar",
        access_token="old_access",
        last_backfilled_at=datetime.now(timezone.utc) - timedelta(days=1),
    ).save()
    _, state_value = _issue_oauth_state(authenticated_client)

    with (
        patch(
            "app.routes.acuity_auth._exchange_code_for_tokens",
            return_value=_FAKE_TOKENS,
        ),
        patch(
            "app.routes.acuity_auth.ACUITY_SUCCESS_REDIRECT",
            "https://example.com/settings",
        ),
        patch(
            "app.services.scheduling_oauth_flow.backfill_scheduling_roster.queue"
        ) as backfill_queue,
        patch("app.services.scheduling_oauth_flow.sync_scheduling_provider.queue"),
    ):
        response = authenticated_client.get(
            api_app.url_path_for("acuity_oauth_callback"),
            params={"code": "auth_code_123", "state": state_value},
            follow_redirects=False,
        )

    assert response.status_code == status.HTTP_302_FOUND
    backfill_queue.assert_called_once_with(str(doctor.id), "acuity")
    integration = DoctorSchedulingIntegration.get(
        doctor_id=doctor.id, source=SchedulingProviderSource.acuity
    )
    assert integration is not None
    assert integration.provider_calendar_ref == "calendar-123"


@pytest.mark.parametrize("state_value", [None, "not-an-issued-state", "A" * 43])
def test_acuity_oauth_callback_rejects_invalid_state_before_provider_calls(
    client: TestClient, state_value: str | None
):
    with patch("app.routes.acuity_auth._exchange_code_for_tokens") as exchange:
        response = client.get(
            api_app.url_path_for("acuity_oauth_callback"),
            params={"code": "must-not-be-exchanged", "state": state_value},
            follow_redirects=False,
        )

    _assert_authorization_failed(response)
    exchange.assert_not_called()


def test_acuity_oauth_callback_rejects_expired_state_before_provider_calls(
    authenticated_client: TestClient,
):
    _, state_value = _issue_oauth_state(authenticated_client)
    get_redis().delete(
        scheduling_oauth_state.scheduling_oauth_state_redis_key(
            SchedulingProviderSource.acuity, state_value
        )
    )

    with patch("app.routes.acuity_auth._exchange_code_for_tokens") as exchange:
        response = authenticated_client.get(
            api_app.url_path_for("acuity_oauth_callback"),
            params={"code": "must-not-be-exchanged", "state": state_value},
            follow_redirects=False,
        )

    _assert_authorization_failed(response)
    exchange.assert_not_called()


def test_acuity_oauth_callback_replay_does_not_call_provider_twice(
    authenticated_client: TestClient,
):
    _, state_value = _issue_oauth_state(authenticated_client)

    with (
        patch(
            "app.routes.acuity_auth._exchange_code_for_tokens",
            return_value=_FAKE_TOKENS,
        ) as exchange,
        patch(
            "app.routes.acuity_auth.ACUITY_SUCCESS_REDIRECT",
            "https://example.com/settings",
        ),
        patch("app.services.scheduling_oauth_flow.backfill_scheduling_roster.queue"),
        patch("app.services.scheduling_oauth_flow.sync_scheduling_provider.queue"),
    ):
        first = authenticated_client.get(
            api_app.url_path_for("acuity_oauth_callback"),
            params={"code": "first-code", "state": state_value},
            follow_redirects=False,
        )
        second = authenticated_client.get(
            api_app.url_path_for("acuity_oauth_callback"),
            params={"code": "replayed-code", "state": state_value},
            follow_redirects=False,
        )

    assert first.status_code == status.HTTP_302_FOUND
    _assert_authorization_failed(second)
    exchange.assert_called_once_with("first-code")


# --- acuity-connect (disconnect) ---


def test_acuity_disconnect_revokes_and_deletes(authenticated_client: TestClient):
    doctor = _get_or_create_doctor()
    DoctorSchedulingIntegration(
        doctor_id=doctor.id,
        source=SchedulingProviderSource.acuity,
        provider_calendar_ref="calendar-1",
        access_token="access_token",
    ).save()

    with patch(
        "app.services.scheduling_acuity.httpx.Client"
    ) as mock_client_cls:
        mock_client = mock_client_cls.return_value.__enter__.return_value
        mock_client.post.return_value.is_success = True
        mock_client.post.return_value.status_code = 200
        response = authenticated_client.delete(
            api_app.url_path_for("acuity_disconnect")
        )

    assert response.status_code == status.HTTP_204_NO_CONTENT
    mock_client.post.assert_called_once()
    assert (
        DoctorSchedulingIntegration.get(
            doctor_id=doctor.id, source=SchedulingProviderSource.acuity
        )
        is None
    )


def test_acuity_disconnect_not_connected_is_a_noop(authenticated_client: TestClient):
    _get_or_create_doctor()

    response = authenticated_client.delete(api_app.url_path_for("acuity_disconnect"))

    assert response.status_code == status.HTTP_204_NO_CONTENT
