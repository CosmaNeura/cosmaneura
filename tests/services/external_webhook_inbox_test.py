import threading
from datetime import UTC, datetime, timedelta

from structlog.testing import capture_logs
from typeid import TypeID

from app.services import external_webhook_inbox as inbox
from app.utils.encryption import decrypt_inbox_payload

from activemodel.session_manager import SessionManager, get_engine, get_session
from app.models.external_webhook_delivery import ExternalWebhookDelivery
from app.models.external_webhook_operation import ExternalWebhookOperation
from sqlalchemy import text


def _payload(marker: str = "clinical-sentinel") -> dict:
    return {
        "event": "transcript.done",
        "data": {
            "bot": {"id": "bot-private"},
            "recording": {"id": "recording-private"},
            "transcript": {"id": "transcript-private"},
            "ignored": marker,
        },
    }


def _delivery(delivery_id: str) -> ExternalWebhookDelivery:
    delivery = ExternalWebhookDelivery.get(id=TypeID.from_string(delivery_id))
    assert delivery is not None
    return delivery


def test_verified_delivery_is_encrypted_projected_and_idempotent() -> None:
    first = inbox.store_verified_external_webhook(
        provider="recall",
        provider_event_id="signed-delivery-private",
        event_type="transcript.done",
        payload=_payload(),
    )
    replay = inbox.store_verified_external_webhook(
        provider="recall",
        provider_event_id="signed-delivery-private",
        event_type="transcript.done",
        payload=_payload(),
    )

    assert replay == inbox.StoredDelivery(first.delivery_id, duplicate=True)
    delivery = _delivery(first.delivery_id)
    assert delivery.encrypted_payload is not None
    assert "clinical-sentinel" not in delivery.encrypted_payload
    assert "transcript-private" not in delivery.encrypted_payload
    decrypted = decrypt_inbox_payload(delivery.encrypted_payload)
    assert "transcript-private" in decrypted
    assert "clinical-sentinel" not in decrypted
    assert delivery.provider_event_digest == inbox.digest_provider_reference(
        "signed-delivery-private"
    )
    assert "signed-delivery-private" not in delivery.provider_event_digest

    with get_session() as session:
        ciphertext = session.execute(
            text(
                "SELECT encrypted_payload FROM external_webhook_delivery "
                "WHERE provider_event_digest = :digest"
            ),
            {"digest": delivery.provider_event_digest},
        ).scalar_one()
    assert ciphertext == delivery.encrypted_payload
    assert "transcript-private" not in ciphertext
    assert "clinical-sentinel" not in ciphertext


def test_zoom_teardown_intent_is_encrypted_health_visible_and_reconciled(
    monkeypatch,
) -> None:
    from app.jobs import merge_window

    stored = inbox.store_zoom_teardown_intent(
        action="release",
        meeting_id="zoom-meeting-private",
        bot_id="recall-bot-private",
        occurrence_digest=inbox.digest_provider_reference("occurrence-private"),
        generation=3,
        stop_recording=True,
        cleanup_session=True,
        doctor_id="doctor-private",
        mark_meeting_ended=True,
        now=datetime.now(UTC) - timedelta(minutes=4),
    )

    delivery = _delivery(stored.delivery_id)
    assert delivery.state == "pending"
    assert delivery.encrypted_payload is not None
    assert "recall-bot-private" not in delivery.encrypted_payload
    assert inbox.inbox_health()["pending"] >= 1

    reconciled: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        merge_window,
        "reconcile_teardown_intent",
        lambda event_type, payload: reconciled.append((event_type, payload)),
    )
    inbox.process_delivery(stored.delivery_id)

    assert reconciled[0][0] == "internal.zoom_bot_release"
    assert reconciled[0][1]["teardown_intent"]["bot_id"] == "recall-bot-private"
    assert reconciled[0][1]["teardown_intent"]["doctor_id"] == "doctor-private"
    assert reconciled[0][1]["teardown_intent"]["mark_meeting_ended"] is True
    completed = _delivery(stored.delivery_id)
    assert completed.state == "completed"
    assert completed.encrypted_payload is None


def test_zoom_watchdog_intent_is_encrypted_idempotent_and_retried(
    monkeypatch, captured_alerts
) -> None:
    from app.jobs import verify_bot_joined
    from app.services import alerting

    now = datetime.now(UTC)
    occurrence = inbox.digest_provider_reference("watchdog-occurrence-private")
    meeting_record_id = str(TypeID("zmtg"))
    stored = inbox.store_zoom_watchdog_intent(
        meeting_record_id=meeting_record_id,
        meeting_id="zoom-watchdog-private",
        occurrence_digest=occurrence,
        check_number=2,
        delay_seconds=180,
        now=now,
    )
    replay = inbox.store_zoom_watchdog_intent(
        meeting_record_id=meeting_record_id,
        meeting_id="zoom-watchdog-private",
        occurrence_digest=occurrence,
        check_number=2,
    )
    assert replay == inbox.StoredDelivery(stored.delivery_id, duplicate=True)
    assert "zoom-watchdog-private" not in (
        _delivery(stored.delivery_id).encrypted_payload or ""
    )
    assert (
        inbox.claim_delivery(stored.delivery_id, now=now + timedelta(seconds=179))
        is None
    )
    delivery = _delivery(stored.delivery_id)
    delivery.next_attempt_at = now
    delivery.save()

    attempts: list[tuple[str, int, str | None, str | None]] = []

    def perform(
        meeting_id, check_number=1, occurrence_digest=None, meeting_record_id=None
    ):
        attempts.append(
            (meeting_id, check_number, occurrence_digest, meeting_record_id)
        )
        if len(attempts) == 1:
            raise ConnectionError("watchdog dependency unavailable")

    monkeypatch.setattr(verify_bot_joined, "perform", perform)
    inbox.process_delivery(stored.delivery_id)
    failed = _delivery(stored.delivery_id)
    assert failed.state == "retry_wait" and failed.last_error_code == "handler_error"
    assert [call["type"] for call in captured_alerts] == [alerting.WatchdogArmingFailed]

    failed.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    failed.save()
    inbox.process_delivery(stored.delivery_id)

    assert attempts == [
        ("zoom-watchdog-private", 2, occurrence, meeting_record_id),
        ("zoom-watchdog-private", 2, occurrence, meeting_record_id),
    ]
    completed = _delivery(stored.delivery_id)
    assert completed.state == "completed" and completed.encrypted_payload is None


def test_zoom_projection_discards_participant_name_and_keeps_bot_classification() -> (
    None
):
    stored = inbox.store_verified_external_webhook(
        provider="zoom",
        provider_event_id="zoom-name-minimization",
        event_type="meeting.participant_joined",
        payload={
            "event": "meeting.participant_joined",
            "event_ts": 1_700_000_000_123,
            "payload": {
                "object": {
                    "id": "meeting-private",
                    "uuid": "occurrence-private",
                    "participant": {
                        "id": "participant-private",
                        "user_name": "Cosmas AI Note-Taker",
                    },
                }
            },
        },
    )

    delivery = _delivery(stored.delivery_id)
    assert delivery.encrypted_payload is not None
    projected = decrypt_inbox_payload(delivery.encrypted_payload)
    assert "Cosmas AI Note-Taker" not in projected
    assert '"is_bot":true' in projected
    assert '"user_name"' not in projected
    assert '"event_ts":1700000000123' in projected


def test_corrupt_ciphertext_dead_letters_without_logging_or_reclaim() -> None:
    stored = inbox.store_verified_external_webhook(
        provider="recall",
        provider_event_id="signed-corrupt-ciphertext",
        event_type="transcript.processing",
        payload=_payload(),
    )
    sentinel = "corrupt-private-ciphertext-sentinel"
    digest = inbox.digest_provider_reference("signed-corrupt-ciphertext")
    with get_session() as session:
        session.execute(
            text(
                "UPDATE external_webhook_delivery SET encrypted_payload = :ciphertext "
                "WHERE provider_event_digest = :digest"
            ),
            {
                "ciphertext": sentinel,
                "digest": digest,
            },
        )
        session.commit()

    with capture_logs() as logs:
        assert inbox.claim_delivery(stored.delivery_id) is None

    delivery = _delivery(stored.delivery_id)
    assert delivery.state == "dead_letter"
    assert delivery.last_error_code == "decrypt_failed"
    assert delivery.encrypted_payload is None
    assert delivery.payload_purged_at is not None
    assert inbox.claim_delivery(stored.delivery_id) is None
    assert sentinel not in str(logs)


def test_delivery_identifier_conflict_is_rejected() -> None:
    inbox.store_verified_external_webhook(
        provider="recall",
        provider_event_id="signed-delivery-conflict",
        event_type="transcript.done",
        payload=_payload(),
    )

    try:
        inbox.store_verified_external_webhook(
            provider="recall",
            provider_event_id="signed-delivery-conflict",
            event_type="transcript.failed",
            payload={"event": "transcript.failed", "data": {}},
        )
    except inbox.WebhookConflictError:
        pass
    else:
        raise AssertionError("changed delivery replay must be rejected")


def test_new_legacy_queue_reference_converges_with_direct_durable_delivery() -> None:
    provider_event_id = "signed-cutover-delivery-private"
    queued_reference = inbox.queued_provider_event_reference(provider_event_id)
    legacy = inbox.store_legacy_queued_webhook(
        provider="recall",
        event_id=queued_reference,
        event_type="transcript.done",
        payload=_payload(),
    )
    direct = inbox.store_verified_external_webhook(
        provider="recall",
        provider_event_id=provider_event_id,
        event_type="transcript.done",
        payload=_payload(),
    )

    assert direct == inbox.StoredDelivery(legacy.delivery_id, duplicate=True)


def test_concurrent_duplicate_delivery_converges_on_one_row() -> None:
    """Exercise the real unique-key race on independent PostgreSQL sessions."""

    event_id = "signed-concurrent-private"
    digest = inbox.digest_provider_reference(event_id)
    barrier = threading.Barrier(2)
    results: list[inbox.StoredDelivery] = []
    errors: list[Exception] = []
    manager = SessionManager.get_instance()
    test_connection = manager.session_connection

    def ingest() -> None:
        try:
            barrier.wait(timeout=5)
            results.append(
                inbox.store_verified_external_webhook(
                    provider="recall",
                    provider_event_id=event_id,
                    event_type="transcript.done",
                    payload=_payload(),
                )
            )
        except Exception as exc:
            errors.append(exc)

    # The normal unit-test fixture intentionally shares one savepoint. Disable
    # that override only for these worker threads so this is a real DB race.
    manager.session_connection = None
    threads = [threading.Thread(target=ingest), threading.Thread(target=ingest)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert not any(thread.is_alive() for thread in threads)
        assert errors == []
        assert len(results) == 2
        assert len({result.delivery_id for result in results}) == 1
        assert sorted(result.duplicate for result in results) == [False, True]
    finally:
        with get_engine().begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM external_webhook_delivery "
                    "WHERE provider = 'recall' AND provider_event_digest = :digest"
                ),
                {"digest": digest},
            )
        manager.session_connection = test_connection


def test_commit_survives_publish_failure_and_is_discovered(monkeypatch) -> None:
    stored = inbox.store_verified_external_webhook(
        provider="zoom",
        provider_event_id="zoom-commit-publish-crash",
        event_type="meeting.started",
        payload={"event": "meeting.started", "payload": {"object": {"id": 123}}},
    )

    monkeypatch.setattr(
        inbox,
        "publish_delivery",
        lambda _delivery_id: (_ for _ in ()).throw(RuntimeError("broker sentinel")),
    )
    inbox.publish_best_effort(stored.delivery_id)

    assert _delivery(stored.delivery_id).state == "pending"
    assert stored.delivery_id in inbox.discover_due_delivery_ids()


def test_stale_worker_is_fenced_after_lease_reclaim() -> None:
    now = datetime.now(UTC)
    stored = inbox.store_verified_external_webhook(
        provider="recall",
        provider_event_id="signed-stale-lease",
        event_type="transcript.processing",
        payload=_payload(),
        now=now,
    )

    first = inbox.claim_delivery(stored.delivery_id, now=now)
    assert first is not None
    assert (
        inbox.claim_delivery(stored.delivery_id, now=now + timedelta(minutes=9)) is None
    )
    second = inbox.claim_delivery(stored.delivery_id, now=now + timedelta(minutes=11))
    assert second is not None
    assert second.fence == first.fence + 1

    inbox.complete_claim(first)
    assert _delivery(stored.delivery_id).state == "processing"
    inbox.complete_claim(second)
    completed = _delivery(stored.delivery_id)
    assert completed.state == "completed"
    assert completed.encrypted_payload is None
    assert completed.payload_purged_at is not None


def test_retry_exhaustion_dead_letters_and_can_be_redriven(monkeypatch) -> None:
    now = datetime.now(UTC)
    stored = inbox.store_verified_external_webhook(
        provider="recall",
        provider_event_id="signed-retry-exhaustion",
        event_type="transcript.processing",
        payload=_payload(),
        now=now,
    )

    for attempt in range(1, inbox.MAX_ATTEMPTS + 1):
        claim = inbox.claim_delivery(
            stored.delivery_id, now=now + timedelta(minutes=attempt * 30)
        )
        assert claim is not None
        assert claim.fence == attempt
        inbox.fail_claim(claim, error_code="handler_error")

    delivery = _delivery(stored.delivery_id)
    assert delivery.state == "dead_letter"
    assert delivery.last_error_code == "handler_error"
    published: list[str] = []
    monkeypatch.setattr(inbox, "publish_best_effort", published.append)
    assert inbox.redrive_delivery(stored.delivery_id, now=now + timedelta(hours=3))
    assert published == [stored.delivery_id]
    assert _delivery(stored.delivery_id).state == "pending"


def test_expired_payload_is_purged_and_not_redrivable() -> None:
    now = datetime.now(UTC)
    stored = inbox.store_verified_external_webhook(
        provider="recall",
        provider_event_id="signed-expired",
        event_type="transcript.processing",
        payload=_payload(),
        now=now - timedelta(hours=7),
    )

    assert inbox.purge_expired_payloads(now=now) == 1
    delivery = _delivery(stored.delivery_id)
    assert delivery.state == "dead_letter"
    assert delivery.encrypted_payload is None
    assert delivery.last_error_code == "payload_expired"
    assert inbox.redrive_delivery(stored.delivery_id, now=now) is False


def test_health_reports_stale_provider_mutations_without_provider_ids() -> None:
    now = datetime.now(UTC)
    operation = ExternalWebhookOperation(
        operation_type="create_transcript",
        provider_resource_digest=inbox.digest_provider_reference(
            "provider-resource-private"
        ),
        state="in_flight",
    ).save()
    operation.created_at = now - inbox.ALERT_AGE - timedelta(seconds=1)
    operation.save()

    health = inbox.inbox_health(now=now)

    assert health["uncertain_operations"] == 1
    assert "provider-resource-private" not in str(health)


def test_transcript_failed_is_durable_and_visible_as_dead_letter() -> None:
    stored = inbox.store_verified_external_webhook(
        provider="recall",
        provider_event_id="signed-transcript-failed",
        event_type="transcript.failed",
        payload={
            "event": "transcript.failed",
            "data": {
                "recording": {"id": "recording-failed-private"},
                "transcript": {"id": "transcript-failed-private"},
            },
        },
    )

    inbox.process_delivery(stored.delivery_id)

    delivery = _delivery(stored.delivery_id)
    assert delivery.state == "dead_letter"
    assert delivery.last_error_code == "provider_transcript_failed"
    health = inbox.inbox_health()
    assert health["dead_letter"] == 1
    assert health["failed_operations"] == 1
