from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest

from app.services import external_webhook_inbox as inbox
from app.services import zoom_webhooks

from activemodel.session_manager import SessionManager, get_engine, get_session
from app.models.external_webhook_delivery import ExternalWebhookDelivery
from app.models.external_webhook_operation import ExternalWebhookOperation
from sqlalchemy import text
from sqlmodel import select


def _zoom_payload(event_type: str) -> dict:
    participant = {"id": "participant-private", "user_name": "Patient Private"}
    return {
        "event": event_type,
        "payload": {
            "object": {
                "id": "meeting-private",
                "host_id": "host-private",
                "participant": participant,
            }
        },
    }


def _operation(provider_event_digest: str) -> ExternalWebhookOperation:
    with get_session() as session:
        operation = session.exec(
            select(ExternalWebhookOperation).where(
                ExternalWebhookOperation.provider == "zoom",
                ExternalWebhookOperation.provider_resource_digest
                == provider_event_digest,
            )
        ).one()
        session.expunge(operation)
        return operation


@pytest.mark.parametrize(
    "event_type",
    [
        "meeting.started",
        "meeting.participant_joined",
        "meeting.participant_left",
    ],
)
def test_inbox_lease_replay_never_repeats_an_ambiguous_zoom_side_effect(
    monkeypatch: pytest.MonkeyPatch,
    event_type: str,
) -> None:
    payload = _zoom_payload(event_type)
    stored = inbox.store_verified_external_webhook(
        provider="zoom",
        provider_event_id=f"delivery-{event_type}",
        event_type=event_type,
        payload=payload,
    )
    first = inbox.claim_delivery(stored.delivery_id)
    assert first is not None
    effects: list[str] = []

    def crash_after_effect(_payload, before_side_effect) -> None:
        before_side_effect()
        effects.append(event_type)
        raise SystemExit("simulated worker death")

    monkeypatch.setitem(zoom_webhooks.HANDLERS, event_type, crash_after_effect)
    with pytest.raises(SystemExit, match="simulated worker death"):
        inbox._dispatch(first, payload)

    operation = _operation(first.provider_event_digest)
    assert operation.state == "in_flight"
    operation.execution_lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    operation.save()

    second = inbox.claim_delivery(
        stored.delivery_id,
        now=datetime.now(UTC) + inbox.DELIVERY_LEASE + timedelta(seconds=1),
    )
    assert second is not None
    with pytest.raises(inbox.PermanentWebhookError) as error:
        inbox._dispatch(second, payload)
    assert error.value.code == "provider_operation_uncertain"
    inbox.fail_claim(second, error_code=error.value.code, permanent=True)

    assert effects == [event_type]
    assert _operation(first.provider_event_digest).state == "uncertain"
    delivery = ExternalWebhookDelivery.get(id=stored.delivery_id)
    assert delivery is not None
    assert delivery.state == "dead_letter"


def test_concurrent_zoom_delivery_runs_side_effect_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_type = "meeting.participant_joined"
    digest = inbox.digest_provider_reference("zoom-concurrent-delivery-private")
    effect_started = threading.Event()
    release_effect = threading.Event()
    effects: list[str] = []
    errors: list[BaseException] = []
    manager = SessionManager.get_instance()
    test_connection = manager.session_connection

    def blocking_handler(_payload, before_side_effect) -> None:
        before_side_effect()
        effects.append("started")
        effect_started.set()
        assert release_effect.wait(timeout=5)

    def first_worker() -> None:
        try:
            zoom_webhooks.dispatch(
                event_type,
                _zoom_payload(event_type),
                provider_event_digest=digest,
            )
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setitem(zoom_webhooks.HANDLERS, event_type, blocking_handler)
    manager.session_connection = None
    thread = threading.Thread(target=first_worker)
    try:
        thread.start()
        assert effect_started.wait(timeout=5)
        with pytest.raises(inbox.RetryableWebhookError) as busy:
            zoom_webhooks.dispatch(
                event_type,
                _zoom_payload(event_type),
                provider_event_digest=digest,
            )
        assert busy.value.code == "handler_error"
        release_effect.set()
        thread.join(timeout=10)

        assert not thread.is_alive()
        assert errors == []
        assert effects == ["started"]
        assert _operation(digest).state == "succeeded"
    finally:
        release_effect.set()
        thread.join(timeout=10)
        with get_engine().begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM external_webhook_operation "
                    "WHERE provider = 'zoom' AND provider_resource_digest = :digest"
                ),
                {"digest": digest},
            )
        manager.session_connection = test_connection
