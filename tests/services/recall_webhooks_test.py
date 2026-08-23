import json
from types import SimpleNamespace

import pytest
from structlog.testing import capture_logs

from app.services import recall_webhooks
from app.services.external_webhook_inbox import (
    PermanentWebhookError,
    RetryableWebhookError,
)

from app.models.external_webhook_operation import ExternalWebhookOperation


def _recording_payload(recording_id: str) -> dict:
    return {
        "event": "recording.done",
        "data": {"recording": {"id": recording_id}},
    }


def _transcript_payload() -> dict:
    return {
        "event": "transcript.done",
        "data": {
            "bot": {"id": "bot-private-sentinel"},
            "recording": {"id": "recording-private-sentinel"},
            "transcript": {"id": "transcript-private-sentinel"},
        },
    }


def _allow_recording_mapping(monkeypatch, recording_id: str) -> None:
    class FakeRedis:
        def get(self, key):
            if key == f"recall:recording:{recording_id}:doctor_id":
                return b"doctor-authorized"
            return None

    monkeypatch.setattr(recall_webhooks, "get_redis", lambda: FakeRedis())
    monkeypatch.setattr(
        recall_webhooks.Doctor,
        "get",
        lambda **_kwargs: SimpleNamespace(deleted_at=None),
    )


def test_recording_done_creates_transcript_once(monkeypatch) -> None:
    calls: list[str] = []
    _allow_recording_mapping(monkeypatch, "recording-once-private")
    monkeypatch.setattr(
        recall_webhooks.recall_service,
        "create_async_transcript",
        lambda recording_id: calls.append(recording_id),
    )

    payload = _recording_payload("recording-once-private")
    recall_webhooks.handle_recording_done(payload)
    recall_webhooks.handle_recording_done(payload)

    assert calls == ["recording-once-private"]


def test_ambiguous_create_failure_is_not_retried(monkeypatch) -> None:
    calls: list[str] = []
    _allow_recording_mapping(monkeypatch, "recording-ambiguous-private")

    def fail(recording_id: str) -> None:
        calls.append(recording_id)
        raise TimeoutError("provider-url-and-body-sentinel")

    monkeypatch.setattr(recall_webhooks.recall_service, "create_async_transcript", fail)
    payload = _recording_payload("recording-ambiguous-private")

    with pytest.raises(RetryableWebhookError) as first:
        recall_webhooks.handle_recording_done(payload)
    with pytest.raises(RetryableWebhookError) as retry:
        recall_webhooks.handle_recording_done(payload)

    assert first.value.code == "provider_operation_uncertain"
    assert retry.value.code == "provider_operation_uncertain"
    assert calls == ["recording-ambiguous-private"]
    receipt = ExternalWebhookOperation.get(
        provider_resource_digest=recall_webhooks.recall_webhook_operations._resource_digest(
            "recording-ambiguous-private"
        )
    )
    assert receipt is not None
    assert receipt.state == "uncertain"


def test_recording_done_missing_tenant_mapping_does_not_reserve_operation(
    monkeypatch,
) -> None:
    class FakeRedis:
        def get(self, _key):
            return None

    recording_id = "recording-mapping-retry-private"
    monkeypatch.setattr(recall_webhooks, "get_redis", lambda: FakeRedis())
    monkeypatch.setattr(
        recall_webhooks.recall_service,
        "create_async_transcript",
        lambda _recording_id: (_ for _ in ()).throw(
            AssertionError("provider must not be called without tenant authority")
        ),
    )

    with pytest.raises(RetryableWebhookError) as error:
        recall_webhooks.handle_recording_done(_recording_payload(recording_id))

    assert error.value.code == "tenant_mapping_unavailable"
    receipt = ExternalWebhookOperation.get(
        provider_resource_digest=recall_webhooks.recall_webhook_operations._resource_digest(
            recording_id
        )
    )
    assert receipt is None


def test_transcript_done_retries_when_redis_tenant_binding_is_missing(
    monkeypatch,
) -> None:
    redis_values = {}

    class FakeRedis:
        def get(self, key):
            return redis_values.get(key)

    monkeypatch.setattr(recall_webhooks, "get_redis", lambda: FakeRedis())
    monkeypatch.setattr(
        recall_webhooks.recall_service,
        "get_transcript",
        lambda _transcript_id: (_ for _ in ()).throw(
            AssertionError("provider must not be called without tenant authority")
        ),
    )

    with capture_logs() as logs, pytest.raises(RetryableWebhookError) as error:
        recall_webhooks.handle_transcript_done(_transcript_payload())

    assert error.value.code == "tenant_mapping_unavailable"
    serialized = json.dumps(logs, sort_keys=True)
    for raw_identifier in (
        "bot-private-sentinel",
        "recording-private-sentinel",
        "transcript-private-sentinel",
    ):
        assert raw_identifier not in serialized
    assert "hmac:" in serialized


def test_transcript_done_rejects_bot_scoped_fallback_binding(monkeypatch) -> None:
    """A second recording ID must not inherit the first patient's bot mapping."""
    redis_values = {
        "recall:bot:bot-private-sentinel:doctor_id": b"doctor-a",
        "recall:bot:bot-private-sentinel:appointment_id": b"appointment-a",
        "recall:bot:bot-private-sentinel:patient_id": b"patient-a",
    }

    class FakeRedis:
        def get(self, key):
            return redis_values.get(key)

    monkeypatch.setattr(recall_webhooks, "get_redis", lambda: FakeRedis())
    monkeypatch.setattr(
        recall_webhooks.recall_service,
        "get_transcript",
        lambda _transcript_id: (_ for _ in ()).throw(
            AssertionError("provider must not be called for an unbound recording")
        ),
    )

    with pytest.raises(RetryableWebhookError) as error:
        recall_webhooks.handle_transcript_done(_transcript_payload())

    assert error.value.code == "tenant_mapping_unavailable"


def test_transcript_status_logging_contains_only_hashed_reference() -> None:
    payload = {
        "event": "transcript.processing",
        "data": {
            "recording": {"id": "recording-status-private"},
            "transcript": {"id": "transcript-status-private"},
        },
    }
    with capture_logs() as logs:
        recall_webhooks.handle_transcript_processing(payload)

    serialized = json.dumps(logs, sort_keys=True)
    assert "transcript-status-private" not in serialized
    assert "recording-status-private" not in serialized
    assert "hmac:" in serialized


def test_transcript_failed_persists_failure_and_dead_letters_delivery() -> None:
    payload = {
        "event": "transcript.failed",
        "data": {
            "recording": {"id": "recording-failed-private"},
            "transcript": {"id": "transcript-failed-private"},
        },
    }

    with pytest.raises(PermanentWebhookError) as failure:
        recall_webhooks.handle_transcript_failed(payload)

    assert failure.value.code == "provider_transcript_failed"
    receipt = ExternalWebhookOperation.get(
        provider_resource_digest=recall_webhooks.recall_webhook_operations._resource_digest(
            "recording-failed-private"
        )
    )
    assert receipt is not None
    assert receipt.state == "failed"
    assert receipt.last_error_code == "provider_transcript_failed"
