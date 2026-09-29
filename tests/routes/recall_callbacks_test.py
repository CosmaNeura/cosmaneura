"""Tests for the just-in-time ZAK callback that Recall hits when a bot joins."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from typing import cast

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from redis.exceptions import ResponseError

from app.configuration.redis import get_redis
from app.routes.recall_callbacks import zak_token_redis_key
from app.services import zoom as zoom_service

from app.models.doctor_video_integration import (
    DoctorVideoIntegration,
    VideoProvider,
)

from tests.factories import DoctorFactory, save_practice_member
from tests.routes.utils import provision_practice_in_force


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
    provision_practice_in_force(doctor)
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
    calls = []

    def get_zak_token(doctor):
        calls.append(doctor.id)
        return "fresh-zak-value"

    monkeypatch.setattr(zoom_service, "get_zak_token", get_zak_token)

    token = "test-token-abc"
    fresh_redis.set(zak_token_redis_key(token), str(doctor_with_zoom.id))

    response = client.get(f"/external/v1/recall/zak/{token}")

    assert response.status_code == status.HTTP_200_OK
    # Recall's contract: plain-text body holding the raw ZAK.
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == "fresh-zak-value"
    assert fresh_redis.get(zak_token_redis_key(token)) is None

    replay = client.get(f"/external/v1/recall/zak/{token}")
    assert replay.status_code == status.HTTP_404_NOT_FOUND
    assert calls == [doctor_with_zoom.id]


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
    assert fresh_redis.get(zak_token_redis_key(token)) is None


def test_revoked_doctor_access_returns_404_without_fetching_zak(
    client: TestClient,
    fresh_redis,
    doctor_with_zoom,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.models.practice import PracticeMember

    member = PracticeMember.get(doctor_id=doctor_with_zoom.id)
    assert member is not None
    member.access_revoked_at = datetime.now(timezone.utc)
    member.access_revoked_by_ref = "test-suite"
    member.access_revocation_reason = "Test offboarding"
    member.access_records_retain_until = datetime.now(timezone.utc) + timedelta(
        days=365 * 8
    )
    save_practice_member(member)

    calls: list[object] = []

    def _unexpected_get_zak(doctor):
        calls.append(doctor.id)
        return "must-not-be-returned"

    monkeypatch.setattr(zoom_service, "get_zak_token", _unexpected_get_zak)
    token = "token-revoked-doctor"
    fresh_redis.set(zak_token_redis_key(token), str(doctor_with_zoom.id))

    response = client.get(f"/external/v1/recall/zak/{token}")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert calls == []
    assert fresh_redis.get(zak_token_redis_key(token)) is None


def test_atomic_consume_falls_back_to_lua_for_legacy_redis() -> None:
    from app.routes import recall_callbacks

    class LegacyRedis:
        def __init__(self) -> None:
            self.eval_args = None

        def getdel(self, key: str):
            raise ResponseError("unknown command")

        def eval(self, script: str, key_count: int, key: str):
            self.eval_args = (script, key_count, key)
            return b"doctor-internal-id"

    redis = LegacyRedis()
    result = recall_callbacks._consume_zak_token(redis, "opaque-bearer")

    assert result == b"doctor-internal-id"
    assert redis.eval_args == (
        recall_callbacks._ATOMIC_GET_DELETE,
        1,
        zak_token_redis_key("opaque-bearer"),
    )


def test_atomic_consume_has_one_concurrent_winner(fresh_redis) -> None:
    from app.routes import recall_callbacks

    token = "concurrent-opaque-bearer"
    key = zak_token_redis_key(token)
    fresh_redis.set(key, "doctor-internal-id")
    barrier = Barrier(2)

    def consume() -> bytes | str | None:
        barrier.wait()
        return recall_callbacks._consume_zak_token(get_redis(), token)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: consume(), range(2)))

    assert sum(result is not None for result in results) == 1
    assert fresh_redis.get(key) is None
