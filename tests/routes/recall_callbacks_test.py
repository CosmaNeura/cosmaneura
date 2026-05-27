"""Tests for the just-in-time ZAK callback that Recall hits when a bot joins."""

from datetime import datetime, timedelta, timezone
from typing import cast

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.configuration.redis import get_redis
from app.routes.recall_callbacks import zak_token_redis_key
from app.services import zoom as zoom_service

from app.models.doctor_video_integration import (
    DoctorVideoIntegration,
    VideoProvider,
)

from tests.factories import DoctorFactory


@pytest.fixture
def fresh_redis():
    """Each test gets a clean slate for the zak_url_token namespace."""
    redis = get_redis()

    def _drop_token_keys() -> None:
        keys = cast(list, redis.keys("recall:zak_url_token:*") or [])
        for k in keys:
            redis.delete(k)

    _drop_token_keys()
    yield redis
    _drop_token_keys()


@pytest.fixture
def doctor_with_zoom():
    doctor = DoctorFactory.build().save()
    DoctorVideoIntegration(
        doctor_id=doctor.id,
        provider=VideoProvider.zoom,
        provider_user_id="zoom-user-123",
        access_token="tok",
        refresh_token="rtok",
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    ).save()
    return doctor


def test_returns_plain_text_zak_for_valid_token(
    client: TestClient,
    fresh_redis,
    doctor_with_zoom,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(zoom_service, "get_zak_token", lambda doctor: "fresh-zak-value")

    token = "test-token-abc"
    fresh_redis.set(zak_token_redis_key(token), str(doctor_with_zoom.id))

    response = client.get(f"/external/v1/recall/zak/{token}")

    assert response.status_code == status.HTTP_200_OK
    # Recall's contract: plain-text body holding the raw ZAK.
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == "fresh-zak-value"


def test_unknown_token_returns_404(client: TestClient, fresh_redis) -> None:
    response = client.get("/external/v1/recall/zak/no-such-token")
    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_doctor_without_zoom_integration_returns_404(
    client: TestClient,
    fresh_redis,
) -> None:
    doctor = DoctorFactory.build().save()
    token = "token-no-integration"
    fresh_redis.set(zak_token_redis_key(token), str(doctor.id))

    response = client.get(f"/external/v1/recall/zak/{token}")

    assert response.status_code == status.HTTP_404_NOT_FOUND
