"""Tests for the Zoom webhook signature verification."""

import hashlib
import hmac
import json
import time

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.routes import webhooks

ZOOM_WEBHOOK_PATH = "/external/v1/webhooks/zoom"
TEST_SECRET = "test-zoom-secret-token"


def _sign(body: bytes, timestamp: str, secret: str = TEST_SECRET) -> str:
    message = b"v0:" + timestamp.encode() + b":" + body
    digest = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return f"v0={digest}"


@pytest.fixture
def zoom_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webhooks, "ZOOM_SECRET_TOKEN", TEST_SECRET)


@pytest.mark.usefixtures("zoom_secret")
def test_zoom_webhook_rejects_missing_headers(client: TestClient) -> None:
    response = client.post(ZOOM_WEBHOOK_PATH, json={"event": "meeting.started"})
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.usefixtures("zoom_secret")
def test_zoom_webhook_rejects_wrong_signature(client: TestClient) -> None:
    body = b'{"event":"meeting.started"}'
    ts = str(int(time.time()))
    response = client.post(
        ZOOM_WEBHOOK_PATH,
        content=body,
        headers={
            "x-zm-request-timestamp": ts,
            "x-zm-signature": "v0=deadbeef",
            "content-type": "application/json",
        },
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.usefixtures("zoom_secret")
def test_zoom_webhook_rejects_stale_timestamp(client: TestClient) -> None:
    body = b'{"event":"meeting.started"}'
    ts = str(int(time.time()) - 3600)  # 1 hour old
    response = client.post(
        ZOOM_WEBHOOK_PATH,
        content=body,
        headers={
            "x-zm-request-timestamp": ts,
            "x-zm-signature": _sign(body, ts),
            "content-type": "application/json",
        },
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.usefixtures("zoom_secret")
def test_zoom_webhook_accepts_valid_signature_for_url_validation(
    client: TestClient,
) -> None:
    plain_token = "abcd-1234"
    payload = {
        "event": "endpoint.url_validation",
        "payload": {"plainToken": plain_token},
    }
    body = json.dumps(payload).encode()
    ts = str(int(time.time()))

    response = client.post(
        ZOOM_WEBHOOK_PATH,
        content=body,
        headers={
            "x-zm-request-timestamp": ts,
            "x-zm-signature": _sign(body, ts),
            "content-type": "application/json",
        },
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["plainToken"] == plain_token
    expected_encrypted = hmac.new(
        TEST_SECRET.encode(), plain_token.encode(), hashlib.sha256
    ).hexdigest()
    assert data["encryptedToken"] == expected_encrypted


def test_zoom_webhook_skips_verification_when_secret_unset(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(webhooks, "ZOOM_SECRET_TOKEN", "")
    response = client.post(
        ZOOM_WEBHOOK_PATH,
        json={
            "event": "endpoint.url_validation",
            "payload": {"plainToken": "x"},
        },
    )
    assert response.status_code == status.HTTP_200_OK


@pytest.mark.usefixtures("zoom_secret")
def test_verify_zoom_signature_rejects_non_integer_timestamp() -> None:
    assert (
        webhooks._verify_zoom_signature(
            raw_body=b"{}", timestamp="not-a-timestamp", signature="v0=anything"
        )
        is False
    )
