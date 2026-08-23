"""Signature, replay and body-boundary tests for provider webhook ingress."""

import base64
import hashlib
import hmac
import json
import time

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from starlette.requests import Request
from structlog.testing import capture_logs

from app.routes import webhooks
from app.services.external_webhook_inbox import StoredDelivery

ZOOM_WEBHOOK_PATH = "/external/v1/webhooks/zoom"
RECALL_WEBHOOK_PATH = "/external/v1/webhooks/recall"
TEST_ZOOM_SECRET = "test-zoom-secret-token"
TEST_RECALL_KEY = b"test-recall-signing-key"
TEST_RECALL_SECRET = "whsec_" + base64.b64encode(TEST_RECALL_KEY).decode()


def _zoom_sign(body: bytes, timestamp: str, secret: str = TEST_ZOOM_SECRET) -> str:
    message = b"v0:" + timestamp.encode() + b":" + body
    digest = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return f"v0={digest}"


def _recall_sign(
    body: bytes,
    timestamp: str,
    webhook_id: str,
    key: bytes = TEST_RECALL_KEY,
) -> str:
    signed = webhook_id.encode() + b"." + timestamp.encode() + b"." + body
    digest = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    return f"v1,{digest}"


@pytest.fixture
def durable_ingress(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    calls: dict[str, list] = {"stored": [], "published": []}
    monkeypatch.setattr(webhooks, "DURABLE_WEBHOOK_INBOX_ENABLED", True)

    def store(**kwargs):
        calls["stored"].append(kwargs)
        return StoredDelivery("webhook_delivery_01k00000000000000000000000", False)

    monkeypatch.setattr(
        webhooks.external_webhook_inbox,
        "store_verified_external_webhook",
        store,
    )
    monkeypatch.setattr(
        webhooks.external_webhook_inbox,
        "publish_best_effort",
        calls["published"].append,
    )
    return calls


@pytest.fixture
def zoom_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webhooks, "ZOOM_SECRET_TOKEN", TEST_ZOOM_SECRET)


@pytest.fixture
def recall_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webhooks, "RECALL_WEBHOOK_SECRET", TEST_RECALL_SECRET)


@pytest.mark.usefixtures("zoom_secret", "durable_ingress")
def test_zoom_webhook_rejects_missing_headers(client: TestClient) -> None:
    response = client.post(ZOOM_WEBHOOK_PATH, json={"event": "meeting.started"})
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.usefixtures("zoom_secret", "durable_ingress")
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


@pytest.mark.usefixtures("zoom_secret", "durable_ingress")
def test_zoom_webhook_rejects_stale_timestamp(client: TestClient) -> None:
    body = b'{"event":"meeting.started"}'
    ts = str(int(time.time()) - 3600)
    response = client.post(
        ZOOM_WEBHOOK_PATH,
        content=body,
        headers={
            "x-zm-request-timestamp": ts,
            "x-zm-signature": _zoom_sign(body, ts),
        },
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.usefixtures("zoom_secret")
def test_zoom_signature_rejects_non_ascii_timestamp() -> None:
    # HTTP clients correctly refuse non-ASCII header values, but keep the
    # verifier fail-closed if an ASGI server or direct caller supplies one.
    assert not webhooks._verify_zoom_signature(
        raw_body=b'{"event":"meeting.started"}',
        timestamp="\N{ARABIC-INDIC DIGIT ONE}",
        signature="v0=invalid",
    )


@pytest.mark.usefixtures("zoom_secret")
def test_zoom_webhook_accepts_valid_signature_for_url_validation(
    client: TestClient,
) -> None:
    plain_token = "abcd-1234"
    body = json.dumps(
        {"event": "endpoint.url_validation", "payload": {"plainToken": plain_token}}
    ).encode()
    ts = str(int(time.time()))
    response = client.post(
        ZOOM_WEBHOOK_PATH,
        content=body,
        headers={
            "x-zm-request-timestamp": ts,
            "x-zm-signature": _zoom_sign(body, ts),
        },
    )
    assert response.status_code == status.HTTP_200_OK
    expected = hmac.new(
        TEST_ZOOM_SECRET.encode(), plain_token.encode(), hashlib.sha256
    ).hexdigest()
    assert response.json() == {"plainToken": plain_token, "encryptedToken": expected}


def test_zoom_webhook_fails_closed_when_secret_unset(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(webhooks, "ZOOM_SECRET_TOKEN", "")
    response = client.post(ZOOM_WEBHOOK_PATH, content=b"{}")
    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json() == {"status": "unavailable"}


@pytest.mark.usefixtures("zoom_secret")
def test_zoom_webhook_rejects_oversized_declared_body(client: TestClient) -> None:
    response = client.post(
        ZOOM_WEBHOOK_PATH,
        content=b"x" * (webhooks.MAX_WEBHOOK_BODY_BYTES + 1),
    )
    assert response.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE


async def test_bounded_body_rejects_chunked_body_without_content_length() -> None:
    chunks = iter(
        (
            {
                "type": "http.request",
                "body": b"x" * webhooks.MAX_WEBHOOK_BODY_BYTES,
                "more_body": True,
            },
            {"type": "http.request", "body": b"x", "more_body": False},
        )
    )

    async def receive():
        return next(chunks)

    request = Request({"type": "http", "headers": []}, receive=receive)
    with pytest.raises(webhooks.WebhookBodyTooLarge):
        await webhooks._read_bounded_body(request)


async def test_bounded_body_rejects_one_oversized_chunk_before_buffering() -> None:
    async def receive():
        return {
            "type": "http.request",
            "body": b"x" * (webhooks.MAX_WEBHOOK_BODY_BYTES + 1),
            "more_body": False,
        }

    request = Request({"type": "http", "headers": []}, receive=receive)
    with pytest.raises(webhooks.WebhookBodyTooLarge):
        await webhooks._read_bounded_body(request)


@pytest.mark.usefixtures("zoom_secret", "durable_ingress")
@pytest.mark.parametrize("event", [None, {"patient": "secret"}, "x" * 97])
def test_zoom_webhook_rejects_unbounded_or_non_string_event_types(
    client: TestClient, event: object
) -> None:
    body = json.dumps({"event": event}).encode()
    ts = str(int(time.time()))
    response = client.post(
        ZOOM_WEBHOOK_PATH,
        content=body,
        headers={
            "x-zm-request-timestamp": ts,
            "x-zm-signature": _zoom_sign(body, ts),
        },
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_zoom_supported_event_allowlist_is_exact() -> None:
    assert webhooks.ZOOM_SUPPORTED_EVENT_TYPES == frozenset(
        {
            "meeting.started",
            "meeting.participant_joined",
            "meeting.participant_left",
            "meeting.ended",
        }
    )


def test_recall_supported_event_allowlist_is_exact() -> None:
    assert webhooks.RECALL_SUPPORTED_EVENT_TYPES == frozenset(
        {
            "recording.done",
            "transcript.done",
            "transcript.failed",
            "transcript.processing",
        }
    )


@pytest.mark.usefixtures("zoom_secret")
@pytest.mark.parametrize("event_type", sorted(webhooks.ZOOM_SUPPORTED_EVENT_TYPES))
def test_zoom_webhook_accepts_only_supported_external_event_types(
    client: TestClient,
    durable_ingress: dict[str, list],
    event_type: str,
) -> None:
    body = json.dumps({"event": event_type, "payload": {}}).encode()
    ts = str(int(time.time()))
    response = client.post(
        ZOOM_WEBHOOK_PATH,
        content=body,
        headers={
            "x-zm-request-timestamp": ts,
            "x-zm-signature": _zoom_sign(body, ts),
        },
    )

    assert response.status_code == status.HTTP_200_OK
    assert durable_ingress["stored"][0]["event_type"] == event_type


@pytest.mark.usefixtures("zoom_secret")
@pytest.mark.parametrize(
    "event_type",
    [
        "meeting.created",
        "internal.zoom_bot_pause",
        "internal.zoom_bot_release",
    ],
)
def test_zoom_webhook_rejects_signed_unknown_and_internal_events_before_storage(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    event_type: str,
) -> None:
    monkeypatch.setattr(
        webhooks,
        "_durable_ack",
        lambda **_: (_ for _ in ()).throw(
            AssertionError("unsupported event must not be stored or published")
        ),
    )
    body = json.dumps({"event": event_type, "payload": {}}).encode()
    ts = str(int(time.time()))
    response = client.post(
        ZOOM_WEBHOOK_PATH,
        content=body,
        headers={
            "x-zm-request-timestamp": ts,
            "x-zm-signature": _zoom_sign(body, ts),
        },
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json() == {"status": "unsupported event"}


@pytest.mark.usefixtures("recall_secret")
def test_recall_webhook_accepts_valid_signature_and_publishes_internal_id(
    client: TestClient, durable_ingress: dict[str, list]
) -> None:
    body = b'{"event":"recording.done","data":{"recording":{"id":"rec-1"}}}'
    ts = str(int(time.time()))
    webhook_id = "msg_valid"
    response = client.post(
        RECALL_WEBHOOK_PATH,
        content=body,
        headers={
            "webhook-id": webhook_id,
            "webhook-timestamp": ts,
            "webhook-signature": _recall_sign(body, ts, webhook_id),
        },
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"status": "ok", "duplicate": False}
    assert durable_ingress["stored"][0]["provider_event_id"] == webhook_id
    assert durable_ingress["published"] == [
        "webhook_delivery_01k00000000000000000000000"
    ]


@pytest.mark.usefixtures("recall_secret", "durable_ingress")
def test_recall_webhook_accepts_legacy_svix_headers_and_rotation_signature(
    client: TestClient,
) -> None:
    body = b'{"event":"transcript.processing","data":{"transcript":{"id":"t"}}}'
    ts = str(int(time.time()))
    webhook_id = "msg_rotation"
    response = client.post(
        RECALL_WEBHOOK_PATH,
        content=body,
        headers={
            "svix-id": webhook_id,
            "svix-timestamp": ts,
            "svix-signature": "v1,bad " + _recall_sign(body, ts, webhook_id),
        },
    )
    assert response.status_code == status.HTTP_200_OK


@pytest.mark.usefixtures("recall_secret", "durable_ingress")
@pytest.mark.parametrize("failure", ["missing", "invalid", "stale"])
def test_recall_webhook_rejects_invalid_signatures(
    client: TestClient, failure: str
) -> None:
    body = b'{"event":"recording.done"}'
    ts = str(int(time.time()) - (3600 if failure == "stale" else 0))
    webhook_id = "msg_invalid"
    headers = {
        "webhook-id": webhook_id,
        "webhook-timestamp": ts,
        "webhook-signature": _recall_sign(body, ts, webhook_id),
    }
    if failure == "missing":
        headers.pop("webhook-signature")
    elif failure == "invalid":
        headers["webhook-signature"] = "v1,bad"
    response = client.post(RECALL_WEBHOOK_PATH, content=body, headers=headers)
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.parametrize("secret", ["", "not-whsec", "whsec_not-base64!"])
def test_recall_webhook_fails_closed_for_missing_or_malformed_secret(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, secret: str
) -> None:
    monkeypatch.setattr(webhooks, "RECALL_WEBHOOK_SECRET", secret)
    response = client.post(RECALL_WEBHOOK_PATH, content=b"{}")
    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json() == {"status": "unavailable"}


@pytest.mark.usefixtures("recall_secret", "durable_ingress")
def test_recall_webhook_rejects_oversized_body(client: TestClient) -> None:
    response = client.post(
        RECALL_WEBHOOK_PATH,
        content=b"x" * (webhooks.MAX_WEBHOOK_BODY_BYTES + 1),
    )
    assert response.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE


@pytest.mark.usefixtures("recall_secret")
def test_recall_replay_returns_duplicate_without_exposing_delivery_id(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(webhooks, "DURABLE_WEBHOOK_INBOX_ENABLED", True)
    monkeypatch.setattr(
        webhooks.external_webhook_inbox,
        "store_verified_external_webhook",
        lambda **_: StoredDelivery("webhook_delivery_01k00000000000000000000000", True),
    )
    monkeypatch.setattr(
        webhooks.external_webhook_inbox, "publish_best_effort", lambda _: None
    )
    body = b'{"event":"recording.done"}'
    ts = str(int(time.time()))
    webhook_id = "msg_replay"
    response = client.post(
        RECALL_WEBHOOK_PATH,
        content=body,
        headers={
            "webhook-id": webhook_id,
            "webhook-timestamp": ts,
            "webhook-signature": _recall_sign(body, ts, webhook_id),
        },
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"status": "ok", "duplicate": True}
    assert webhook_id not in response.text


@pytest.mark.usefixtures("recall_secret", "durable_ingress")
def test_webhook_logging_does_not_include_body_headers_or_provider_id(
    client: TestClient,
) -> None:
    sentinel = "patient-secret-sentinel"
    body = json.dumps({"event": "recording.done", "note": sentinel}).encode()
    ts = str(int(time.time()))
    with capture_logs() as logs:
        response = client.post(
            RECALL_WEBHOOK_PATH,
            content=body,
            headers={
                "webhook-id": sentinel,
                "webhook-timestamp": ts,
                "webhook-signature": "v1,invalid",
                "x-private": sentinel,
            },
        )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert sentinel not in json.dumps(logs)


@pytest.mark.usefixtures("recall_secret")
def test_default_false_keeps_signed_legacy_path_operational(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.jobs import webhooks as legacy_webhook_jobs

    queued: list[dict] = []
    monkeypatch.setattr(webhooks, "DURABLE_WEBHOOK_INBOX_ENABLED", False)
    monkeypatch.setattr(
        legacy_webhook_jobs, "queue", lambda **kwargs: queued.append(kwargs)
    )
    monkeypatch.setattr(
        webhooks.external_webhook_inbox,
        "store_verified_external_webhook",
        lambda **_: (_ for _ in ()).throw(AssertionError("durable path must be off")),
    )
    body = b'{"event":"recording.done"}'
    ts = str(int(time.time()))
    webhook_id = "msg_quiesced"
    response = client.post(
        RECALL_WEBHOOK_PATH,
        content=body,
        headers={
            "webhook-id": webhook_id,
            "webhook-timestamp": ts,
            "webhook-signature": _recall_sign(body, ts, webhook_id),
        },
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"status": "ok", "duplicate": False}
    assert len(queued) == 1
    assert queued[0]["provider"] == "recall"
    assert queued[0]["event_id"] != webhook_id
    assert webhook_id not in queued[0]["event_id"]
    assert queued[0]["event_id"].startswith("hmac-v1:")
    assert queued[0]["event_id"].removeprefix("hmac-v1:") == (
        webhooks.external_webhook_inbox.digest_provider_reference(webhook_id)
    )
    assert queued[0]["headers"] == {}


@pytest.mark.usefixtures("zoom_secret")
def test_default_false_keeps_signed_zoom_path_operational(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.jobs import webhooks as legacy_webhook_jobs

    queued: list[dict] = []
    monkeypatch.setattr(webhooks, "DURABLE_WEBHOOK_INBOX_ENABLED", False)
    monkeypatch.setattr(
        legacy_webhook_jobs, "queue", lambda **kwargs: queued.append(kwargs)
    )
    body = b'{"event":"meeting.started","payload":{"object":{"id":"42"}}}'
    ts = str(int(time.time()))

    response = client.post(
        ZOOM_WEBHOOK_PATH,
        content=body,
        headers={
            "x-zm-request-timestamp": ts,
            "x-zm-signature": _zoom_sign(body, ts),
            "x-zm-trackingid": "zoom-private-delivery",
        },
    )

    assert response.status_code == status.HTTP_200_OK
    assert len(queued) == 1
    assert queued[0]["provider"] == "zoom"
    assert queued[0]["event_id"].startswith("hmac-v1:")
    assert "zoom-private-delivery" not in queued[0]["event_id"]
    assert queued[0]["headers"] == {}


@pytest.mark.usefixtures("recall_secret")
def test_true_uses_only_durable_commit_before_publish_path(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.jobs import webhooks as legacy_webhook_jobs

    stored: list[dict] = []
    published: list[str] = []
    monkeypatch.setattr(webhooks, "DURABLE_WEBHOOK_INBOX_ENABLED", True)
    monkeypatch.setattr(
        legacy_webhook_jobs,
        "queue",
        lambda **_: (_ for _ in ()).throw(AssertionError("legacy path must be off")),
    )
    monkeypatch.setattr(
        webhooks.external_webhook_inbox,
        "store_verified_external_webhook",
        lambda **kwargs: (
            stored.append(kwargs)
            or StoredDelivery("webhook_delivery_01k00000000000000000000000", False)
        ),
    )
    monkeypatch.setattr(
        webhooks.external_webhook_inbox,
        "publish_best_effort",
        published.append,
    )
    body = b'{"event":"recording.done"}'
    ts = str(int(time.time()))
    webhook_id = "msg_durable_only"
    response = client.post(
        RECALL_WEBHOOK_PATH,
        content=body,
        headers={
            "webhook-id": webhook_id,
            "webhook-timestamp": ts,
            "webhook-signature": _recall_sign(body, ts, webhook_id),
        },
    )

    assert response.status_code == status.HTTP_200_OK
    assert len(stored) == 1
    assert published == ["webhook_delivery_01k00000000000000000000000"]


@pytest.mark.usefixtures("recall_secret")
def test_desktop_sdk_events_fail_closed_until_exact_capture_binding_exists(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.jobs import webhooks as legacy_webhook_jobs

    monkeypatch.setattr(
        legacy_webhook_jobs,
        "queue",
        lambda **_: (_ for _ in ()).throw(
            AssertionError("unsupported SDK event must not be queued")
        ),
    )
    monkeypatch.setattr(
        webhooks.external_webhook_inbox,
        "store_verified_external_webhook",
        lambda **_: (_ for _ in ()).throw(
            AssertionError("unsupported SDK event must not be persisted")
        ),
    )
    body = b'{"event":"sdk_upload.complete","data":{}}'
    ts = str(int(time.time()))
    webhook_id = "msg-desktop-not-enabled"

    response = client.post(
        RECALL_WEBHOOK_PATH,
        content=body,
        headers={
            "webhook-id": webhook_id,
            "webhook-timestamp": ts,
            "webhook-signature": _recall_sign(body, ts, webhook_id),
        },
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json() == {"status": "unsupported event"}
