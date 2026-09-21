"""Durable, encrypted inbox shared by the Zoom and Recall adapters."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, TypedDict, cast

from typeid import TypeID

from app import log
from app.utils.encryption import (
    DecryptionError,
    decrypt_inbox_payload,
    encrypt_inbox_payload,
    pseudonymous_digest,
)

from activemodel.session_manager import get_session
from app.models.external_webhook_delivery import ExternalWebhookDelivery
from app.models.external_webhook_operation import ExternalWebhookOperation
from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

PAYLOAD_RETENTION = timedelta(hours=6)
DELIVERY_LEASE = timedelta(minutes=10)
WAKEUP_COOLDOWN = timedelta(minutes=1)
MAX_ATTEMPTS = 5
RECONCILE_BATCH_SIZE = 50
ALERT_AGE = timedelta(minutes=5)
ZOOM_TEARDOWN_RECOVERY_DELAY = timedelta(minutes=3)
ZOOM_TEARDOWN_EVENTS = frozenset(
    {"internal.zoom_bot_pause", "internal.zoom_bot_release"}
)
ZOOM_WATCHDOG_ARM_EVENT = "internal.zoom_watchdog_arm"
ZOOM_WATCHDOG_MAX_CHECKS = 5
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_PSEUDONYMOUS_EVENT_PREFIX = "hmac-v1:"
# Keep aligned with app.services.recall.BOT_NAME without importing that module
# here (recall itself imports inbox logging helpers during application startup).
_ZOOM_BOT_PARTICIPANT_NAME = "Cosmas AI Note-Taker"


class WebhookInboxError(Exception):
    """Base class whose message must never be emitted to logs."""


class WebhookConflictError(WebhookInboxError):
    pass


class RetryableWebhookError(WebhookInboxError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class PermanentWebhookError(WebhookInboxError):
    def __init__(self, code: str = "invalid_payload"):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class StoredDelivery:
    delivery_id: str
    duplicate: bool


@dataclass(frozen=True)
class ClaimedDelivery:
    delivery_id: str
    provider: str
    provider_event_digest: str
    event_type: str
    payload_json: str
    fence: int


class InboxHealth(TypedDict):
    pending: int
    processing: int
    retry_wait: int
    completed: int
    dead_letter: int
    oldest_actionable_age_seconds: float | None
    uncertain_operations: int
    failed_operations: int


def digest_provider_reference(value: str) -> str:
    return pseudonymous_digest(value, context="provider-reference")


def queued_provider_event_reference(value: str) -> str:
    """Encode a provider delivery digest for the legacy broker bridge."""

    return f"{_PSEUDONYMOUS_EVENT_PREFIX}{digest_provider_reference(value)}"


def provider_log_ref(value: str | None) -> str:
    """Non-reversible, short log correlation token for adapter-private IDs."""

    if not value:
        return "absent"
    return f"hmac:{pseudonymous_digest(str(value), context='provider-log')[:12]}"


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def _project_recall(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    if not isinstance(data, dict):
        data = {}
    projected_data: dict[str, dict[str, str]] = {}
    for key in ("bot", "recording", "transcript"):
        item = data.get(key)
        if isinstance(item, dict) and item.get("id") is not None:
            projected_data[key] = {"id": str(item["id"])}
    return {"event": event_type, "data": projected_data}


def _project_zoom(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if event_type == ZOOM_WATCHDOG_ARM_EVENT:
        intent = payload.get("watchdog_intent")
        if not isinstance(intent, dict):
            intent = {}
        projected = {
            key: intent[key]
            for key in (
                "meeting_record_id",
                "meeting_id",
                "occurrence_digest",
                "check_number",
            )
            if intent.get(key) is not None
        }
        return {"event": event_type, "watchdog_intent": projected}
    if event_type in ZOOM_TEARDOWN_EVENTS:
        intent = payload.get("teardown_intent")
        if not isinstance(intent, dict):
            intent = {}
        projected_intent = {
            key: intent[key]
            for key in (
                "meeting_id",
                "bot_id",
                "doctor_id",
                "occurrence_digest",
                "generation",
                "stop_recording",
                "cleanup_session",
                "mark_meeting_ended",
            )
            if intent.get(key) is not None
        }
        return {"event": event_type, "teardown_intent": projected_intent}

    payload_value = payload.get("payload")
    obj = payload_value.get("object") if isinstance(payload_value, dict) else None
    if not isinstance(obj, dict):
        obj = {}
    projected_obj: dict[str, Any] = {}
    for key in ("id", "uuid", "host_id"):
        if obj.get(key) is not None:
            projected_obj[key] = obj[key]
    participant = obj.get("participant")
    if isinstance(participant, dict):
        projected_participant = {
            key: participant[key]
            for key in ("id", "user_id")
            if participant.get(key) is not None
        }
        # The handlers need to exclude our bot, but no clinical/display name
        # needs to survive ingress. Persist only the derived classification.
        projected_participant["is_bot"] = (
            participant.get("user_name") == _ZOOM_BOT_PARTICIPANT_NAME
        )
        projected_obj["participant"] = projected_participant
    projected: dict[str, Any] = {
        "event": event_type,
        "payload": {"object": projected_obj},
    }
    # Zoom's signed millisecond event timestamp is ordering evidence, not
    # clinical content. Retain only its normalized integer form so independent
    # participant deliveries can be applied monotonically after broker races.
    event_ts = payload.get("event_ts")
    if isinstance(event_ts, int) and not isinstance(event_ts, bool) and event_ts >= 0:
        projected["event_ts"] = event_ts
    return projected


def project_provider_payload(
    provider: Literal["recall", "zoom"],
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if provider == "recall":
        return _project_recall(event_type, payload)
    return _project_zoom(event_type, payload)


def stable_event_reference(
    provider: str,
    payload: dict[str, Any],
    provider_event_id: str | None = None,
) -> str:
    """Return an adapter-private delivery reference before it is digested.

    Signed Recall deliveries always supply the webhook/Svix ID.  Zoom's
    tracking header is preferred; the normalized payload digest is a stable
    fallback for legacy queued work and providers that omit a delivery ID.
    """

    if provider_event_id:
        return provider_event_id
    candidate = payload.get("event_id") or payload.get("id") or payload.get("uuid")
    if candidate:
        return str(candidate)
    return "payload:" + pseudonymous_digest(
        _canonical_json(payload),
        context=f"{provider}-event-fallback",
    )


def store_verified_external_webhook(
    *,
    provider: Literal["recall", "zoom"],
    provider_event_id: str,
    event_type: str,
    payload: dict[str, Any],
    now: datetime | None = None,
    _provider_event_digest: str | None = None,
    _next_attempt_at: datetime | None = None,
) -> StoredDelivery:
    """Commit a verified delivery before any broker publication or side effect."""

    now = now or datetime.now(UTC)
    event_digest = _provider_event_digest or digest_provider_reference(
        provider_event_id
    )
    if not _DIGEST_RE.fullmatch(event_digest):
        raise PermanentWebhookError()
    projected_json = _canonical_json(
        project_provider_payload(provider, event_type, payload)
    )
    payload_sha256 = pseudonymous_digest(
        projected_json,
        context="webhook-payload",
    )

    with get_session() as session:
        existing = session.exec(
            select(ExternalWebhookDelivery).where(
                ExternalWebhookDelivery.provider == provider,
                ExternalWebhookDelivery.provider_event_digest == event_digest,
            )
        ).one_or_none()
        if existing is not None:
            if (
                existing.event_type != event_type
                or existing.payload_sha256 != payload_sha256
            ):
                raise WebhookConflictError("delivery identifier conflict")
            return StoredDelivery(str(existing.id), duplicate=True)

        delivery = ExternalWebhookDelivery(
            provider=provider,
            provider_event_digest=event_digest,
            event_type=event_type,
            encrypted_payload=encrypt_inbox_payload(projected_json),
            payload_sha256=payload_sha256,
            next_attempt_at=_next_attempt_at or now,
            received_at=now,
            verified_at=now,
            payload_expires_at=now + PAYLOAD_RETENTION,
        )
        session.add(delivery)
        try:
            session.commit()
            session.refresh(delivery)
        except IntegrityError:
            session.rollback()
            existing = session.exec(
                select(ExternalWebhookDelivery).where(
                    ExternalWebhookDelivery.provider == provider,
                    ExternalWebhookDelivery.provider_event_digest == event_digest,
                )
            ).one()
            if (
                existing.event_type != event_type
                or existing.payload_sha256 != payload_sha256
            ):
                raise WebhookConflictError("delivery identifier conflict") from None
            return StoredDelivery(str(existing.id), duplicate=True)
        return StoredDelivery(str(delivery.id), duplicate=False)


def store_zoom_teardown_intent(
    *,
    action: Literal["pause", "release"],
    meeting_id: str,
    bot_id: str,
    occurrence_digest: str | None,
    generation: int | None,
    stop_recording: bool,
    cleanup_session: bool,
    doctor_id: str | None = None,
    mark_meeting_ended: bool = False,
    recovery_delay: timedelta = ZOOM_TEARDOWN_RECOVERY_DELAY,
    now: datetime | None = None,
) -> StoredDelivery:
    """Persist an encrypted bot teardown command before provider mutation.

    The row is intentionally left pending. The initiating worker completes it
    after stop/leave/queue succeeds; if that worker dies or a provider/broker
    call is ambiguous, the ordinary inbox reconciler discovers and retries it.
    """

    now = now or datetime.now(UTC)
    event_type = f"internal.zoom_bot_{action}"
    if event_type not in ZOOM_TEARDOWN_EVENTS:
        raise ValueError("unsupported Zoom teardown action")
    if not meeting_id or not bot_id:
        raise ValueError("missing Zoom teardown target")
    if doctor_id is not None and not doctor_id:
        raise ValueError("invalid Zoom teardown doctor")
    if occurrence_digest is not None and not _DIGEST_RE.fullmatch(occurrence_digest):
        raise ValueError("invalid Zoom occurrence digest")
    if generation is not None and generation < 0:
        raise ValueError("invalid Zoom teardown generation")
    intent = {
        "meeting_id": meeting_id,
        "bot_id": bot_id,
        "doctor_id": doctor_id,
        "occurrence_digest": occurrence_digest,
        "generation": generation,
        "stop_recording": stop_recording,
        "cleanup_session": cleanup_session,
        "mark_meeting_ended": mark_meeting_ended,
    }
    # Raw provider IDs are used only as HMAC input and inside encrypted
    # payload. Neither identifier reaches a DB index, task argument, or log.
    provider_event_id = (
        f"{event_type}:{meeting_id}:{bot_id}:"
        f"{occurrence_digest or 'legacy'}:{generation if generation is not None else 'none'}:"
        f"stop={int(stop_recording)}:cleanup={int(cleanup_session)}:"
        f"ended={int(mark_meeting_ended)}"
    )
    return store_verified_external_webhook(
        provider="zoom",
        provider_event_id=provider_event_id,
        event_type=event_type,
        payload={"event": event_type, "teardown_intent": intent},
        now=now,
        _next_attempt_at=now + recovery_delay,
    )


def store_zoom_watchdog_intent(
    *,
    meeting_record_id: str,
    meeting_id: str,
    occurrence_digest: str,
    check_number: int = 1,
    delay_seconds: int = 0,
    now: datetime | None = None,
) -> StoredDelivery:
    """Persist one occurrence/check-scoped publication for reconciliation."""
    if (
        not _is_typeid(meeting_record_id, "zmtg")
        or not meeting_id
        or not _DIGEST_RE.fullmatch(occurrence_digest)
        or not isinstance(check_number, int)
        or isinstance(check_number, bool)
        or check_number < 1
        or check_number > ZOOM_WATCHDOG_MAX_CHECKS
        or not isinstance(delay_seconds, int)
        or isinstance(delay_seconds, bool)
        or delay_seconds < 0
    ):
        raise ValueError("invalid Zoom watchdog target")
    intent = {
        "meeting_record_id": meeting_record_id,
        "meeting_id": meeting_id,
        "occurrence_digest": occurrence_digest,
        "check_number": check_number,
    }
    return store_verified_external_webhook(
        provider="zoom",
        provider_event_id=(
            f"{ZOOM_WATCHDOG_ARM_EVENT}:{meeting_id}:{occurrence_digest}:{check_number}"
        ),
        event_type=ZOOM_WATCHDOG_ARM_EVENT,
        payload={"event": ZOOM_WATCHDOG_ARM_EVENT, "watchdog_intent": intent},
        now=now,
        _next_attempt_at=(now or datetime.now(UTC)) + timedelta(seconds=delay_seconds),
    )


def complete_zoom_teardown_intent(delivery_id: str) -> bool:
    """Atomically retire a pending teardown intent after confirmed success."""

    completed_at = datetime.now(UTC)
    with get_session() as session:
        delivery = session.exec(
            select(ExternalWebhookDelivery)
            .where(ExternalWebhookDelivery.id == _delivery_id(delivery_id))
            .with_for_update()
        ).one_or_none()
        if delivery is None or delivery.event_type not in ZOOM_TEARDOWN_EVENTS:
            return False
        if delivery.state == "completed":
            return True
        # A reconciler that already owns the delivery must complete its own
        # fenced claim; the direct worker may not overwrite that lease.
        if delivery.state not in {"pending", "retry_wait"}:
            return False
        delivery.state = "completed"
        delivery.processed_at = completed_at
        delivery.encrypted_payload = None
        delivery.payload_purged_at = completed_at
        delivery.lease_expires_at = None
        delivery.wakeup_after = None
        delivery.last_error_code = None
        session.add(delivery)
        session.commit()
        return True


def store_legacy_queued_webhook(
    *,
    provider: str,
    event_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> StoredDelivery:
    if provider not in {"recall", "zoom"}:
        raise PermanentWebhookError()
    precomputed_digest: str | None = None
    if event_id.startswith(_PSEUDONYMOUS_EVENT_PREFIX):
        candidate = event_id.removeprefix(_PSEUDONYMOUS_EVENT_PREFIX)
        if not _DIGEST_RE.fullmatch(candidate):
            raise PermanentWebhookError()
        precomputed_digest = candidate
    reference = stable_event_reference(provider, payload, event_id)
    return store_verified_external_webhook(
        provider=cast(Literal["recall", "zoom"], provider),
        provider_event_id=reference,
        event_type=event_type,
        payload=payload,
        _provider_event_digest=precomputed_digest,
    )


def publish_delivery(delivery_id: str, *, countdown: int | None = None) -> None:
    """Publish only an internal TypeID; payloads and provider IDs stay in PG."""

    from app.jobs.external_webhooks import process_delivery_celery

    if countdown is None:
        process_delivery_celery.delay(delivery_id)
    else:
        process_delivery_celery.apply_async(args=(delivery_id,), countdown=countdown)


def publish_best_effort(delivery_id: str) -> None:
    try:
        publish_delivery(delivery_id)
    except Exception as exc:
        # The committed row is the recovery source.  Never log the broker
        # exception string; it may include serialized task arguments.
        log.warning(
            "durable webhook publish deferred to reconciler",
            delivery_id=delivery_id,
            error_type=type(exc).__name__,
        )


def _is_typeid(value: object, prefix: str) -> bool:
    try:
        return isinstance(value, str) and TypeID.from_string(value).prefix == prefix
    except Exception:
        return False


def _delivery_id(value: str) -> TypeID:
    try:
        parsed = TypeID.from_string(value)
    except Exception as exc:
        raise PermanentWebhookError() from exc
    if parsed.prefix != "webhook_delivery":
        raise PermanentWebhookError()
    return parsed


def claim_delivery(
    delivery_id: str, *, now: datetime | None = None
) -> ClaimedDelivery | None:
    now = now or datetime.now(UTC)
    parsed_id = _delivery_id(delivery_id)
    with get_session() as session:
        delivery = session.exec(
            select(ExternalWebhookDelivery)
            .where(ExternalWebhookDelivery.id == parsed_id)
            .with_for_update(skip_locked=True)
        ).one_or_none()
        if delivery is None or delivery.state in {"completed", "dead_letter"}:
            return None
        if delivery.payload_expires_at <= now or delivery.encrypted_payload is None:
            delivery.state = "dead_letter"
            delivery.last_error_code = "payload_expired"
            delivery.encrypted_payload = None
            delivery.payload_purged_at = now
            delivery.lease_expires_at = None
            session.add(delivery)
            session.commit()
            return None

        eligible = (
            delivery.state in {"pending", "retry_wait"}
            and delivery.next_attempt_at <= now
        ) or (
            delivery.state == "processing"
            and delivery.lease_expires_at is not None
            and delivery.lease_expires_at <= now
        )
        if not eligible:
            return None

        try:
            payload_json = decrypt_inbox_payload(delivery.encrypted_payload)
        except DecryptionError:
            # Decryption must occur while the row is locked so a retired key
            # or corrupt token becomes a bounded terminal state instead of a
            # poison row that the reconciler reclaims forever.
            delivery.state = "dead_letter"
            delivery.last_error_code = "decrypt_failed"
            delivery.encrypted_payload = None
            delivery.payload_purged_at = now
            delivery.lease_expires_at = None
            delivery.wakeup_after = None
            session.add(delivery)
            session.commit()
            return None

        delivery.state = "processing"
        delivery.attempt_count += 1
        delivery.lease_expires_at = now + DELIVERY_LEASE
        delivery.wakeup_after = None
        delivery.last_error_code = None
        fence = delivery.attempt_count
        session.add(delivery)
        session.commit()
        return ClaimedDelivery(
            delivery_id=str(delivery.id),
            provider=delivery.provider,
            provider_event_digest=delivery.provider_event_digest,
            event_type=delivery.event_type,
            payload_json=payload_json,
            fence=fence,
        )


def _locked_fenced_delivery(session, claim: ClaimedDelivery):
    return session.exec(
        select(ExternalWebhookDelivery)
        .where(
            ExternalWebhookDelivery.id == _delivery_id(claim.delivery_id),
            ExternalWebhookDelivery.state == "processing",
            ExternalWebhookDelivery.attempt_count == claim.fence,
        )
        .with_for_update()
    ).one_or_none()


def complete_claim(claim: ClaimedDelivery) -> None:
    completed_at = datetime.now(UTC)
    with get_session() as session:
        delivery = _locked_fenced_delivery(session, claim)
        if delivery is None:
            return
        delivery.state = "completed"
        delivery.processed_at = completed_at
        delivery.encrypted_payload = None
        delivery.payload_purged_at = completed_at
        delivery.lease_expires_at = None
        delivery.wakeup_after = None
        delivery.last_error_code = None
        session.add(delivery)
        session.commit()


def fail_claim(
    claim: ClaimedDelivery,
    *,
    error_code: str,
    permanent: bool = False,
) -> None:
    failed_at = datetime.now(UTC)
    with get_session() as session:
        delivery = _locked_fenced_delivery(session, claim)
        if delivery is None:
            return
        exhausted = claim.fence >= MAX_ATTEMPTS
        expired = delivery.payload_expires_at <= failed_at
        if permanent or exhausted or expired:
            delivery.state = "dead_letter"
            if expired:
                error_code = "payload_expired"
                delivery.encrypted_payload = None
                delivery.payload_purged_at = failed_at
        else:
            delivery.state = "retry_wait"
            delay_seconds = min(30 * (2 ** (claim.fence - 1)), 15 * 60)
            delivery.next_attempt_at = failed_at + timedelta(seconds=delay_seconds)
        delivery.last_error_code = error_code
        delivery.lease_expires_at = None
        delivery.wakeup_after = None
        session.add(delivery)
        session.commit()


def _dispatch(claim: ClaimedDelivery, payload: dict[str, Any]) -> None:
    if claim.provider == "recall":
        from app.services import recall_webhooks

        recall_webhooks.dispatch(event_type=claim.event_type, payload=payload)
        return
    if claim.provider == "zoom":
        if claim.event_type == ZOOM_WATCHDOG_ARM_EVENT:
            from app.jobs import verify_bot_joined

            intent = payload.get("watchdog_intent")
            meeting_record_id = (
                intent.get("meeting_record_id") if isinstance(intent, dict) else None
            )
            meeting_id = intent.get("meeting_id") if isinstance(intent, dict) else None
            occurrence = (
                intent.get("occurrence_digest") if isinstance(intent, dict) else None
            )
            check_number = (
                intent.get("check_number", 1) if isinstance(intent, dict) else None
            )
            if (
                not _is_typeid(meeting_record_id, "zmtg")
                or not isinstance(meeting_id, str)
                or not meeting_id
                or not isinstance(occurrence, str)
                or not _DIGEST_RE.fullmatch(occurrence)
                or isinstance(check_number, bool)
                or not isinstance(check_number, int)
                or not 1 <= check_number <= verify_bot_joined.MAX_CHECKS
            ):
                raise PermanentWebhookError()
            try:
                verify_bot_joined.perform(
                    meeting_id,
                    check_number=check_number,
                    occurrence_digest=occurrence,
                    meeting_record_id=meeting_record_id,
                )
            except Exception as exc:
                from app.services import alerting

                try:
                    alerting.alert(
                        alerting.WatchdogArmingFailed,
                        meeting_ref=provider_log_ref(meeting_id),
                        error_type=type(exc).__name__,
                    )
                except Exception:
                    log.exception("failed to report watchdog execution failure")
                raise RetryableWebhookError("handler_error") from exc
            return
        if claim.event_type in ZOOM_TEARDOWN_EVENTS:
            from app.jobs import merge_window

            merge_window.reconcile_teardown_intent(claim.event_type, payload)
            return
        from app.services import zoom_webhooks

        zoom_webhooks.dispatch(
            event_type=claim.event_type,
            payload=payload,
            provider_event_digest=claim.provider_event_digest,
        )
        return
    raise PermanentWebhookError()


def process_delivery(delivery_id: str) -> None:
    try:
        claim = claim_delivery(delivery_id)
    except PermanentWebhookError:
        return
    if claim is None:
        return

    try:
        payload = json.loads(claim.payload_json)
        if not isinstance(payload, dict):
            raise PermanentWebhookError()
        _dispatch(claim, payload)
    except RetryableWebhookError as exc:
        fail_claim(claim, error_code=exc.code)
        return
    except PermanentWebhookError as exc:
        fail_claim(claim, error_code=exc.code, permanent=True)
        return
    except Exception as exc:
        log.warning(
            "durable webhook handler failed",
            delivery_id=delivery_id,
            provider=claim.provider,
            event_type=claim.event_type,
            error_type=type(exc).__name__,
        )
        fail_claim(claim, error_code="handler_error")
        return
    complete_claim(claim)


def discover_due_delivery_ids(
    *, now: datetime | None = None, limit: int = RECONCILE_BATCH_SIZE
) -> list[str]:
    now = now or datetime.now(UTC)
    with get_session() as session:
        due = session.exec(
            select(ExternalWebhookDelivery)
            .where(
                ExternalWebhookDelivery.encrypted_payload.is_not(None),  # type: ignore[attr-defined]
                or_(
                    and_(
                        cast(Any, ExternalWebhookDelivery.state).in_(
                            ["pending", "retry_wait"]
                        ),
                        cast(Any, ExternalWebhookDelivery.next_attempt_at) <= now,
                        or_(
                            ExternalWebhookDelivery.wakeup_after.is_(None),  # type: ignore[attr-defined]
                            cast(Any, ExternalWebhookDelivery.wakeup_after) <= now,
                        ),
                    ),
                    and_(
                        cast(Any, ExternalWebhookDelivery.state) == "processing",
                        cast(Any, ExternalWebhookDelivery.lease_expires_at) <= now,
                    ),
                ),
            )
            .order_by(cast(Any, ExternalWebhookDelivery.received_at))
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        for delivery in due:
            delivery.wakeup_after = now + WAKEUP_COOLDOWN
            session.add(delivery)
        session.commit()
        return [str(delivery.id) for delivery in due]


def purge_expired_payloads(*, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    with get_session() as session:
        rows = session.exec(
            select(ExternalWebhookDelivery)
            .where(
                cast(Any, ExternalWebhookDelivery.payload_expires_at) <= now,
                ExternalWebhookDelivery.encrypted_payload.is_not(None),  # type: ignore[attr-defined]
                or_(
                    cast(Any, ExternalWebhookDelivery.state) != "processing",
                    cast(Any, ExternalWebhookDelivery.lease_expires_at) <= now,
                ),
            )
            .with_for_update(skip_locked=True)
        ).all()
        for delivery in rows:
            delivery.encrypted_payload = None
            delivery.payload_purged_at = now
            if delivery.state != "completed":
                delivery.state = "dead_letter"
                delivery.last_error_code = "payload_expired"
            delivery.lease_expires_at = None
            session.add(delivery)
        session.commit()
        return len(rows)


def reconcile() -> dict[str, int | float | None]:
    delivery_ids = discover_due_delivery_ids()
    for delivery_id in delivery_ids:
        publish_best_effort(delivery_id)
    purged = purge_expired_payloads()

    from app.services import recall_webhook_operations

    pipelines = recall_webhook_operations.publish_due_pipeline_operations()
    health = inbox_health()
    if (
        health["dead_letter"] > 0
        or health["uncertain_operations"] > 0
        or health["failed_operations"] > 0
        or (health["oldest_actionable_age_seconds"] or 0) > ALERT_AGE.total_seconds()
    ):
        log.error(
            "external webhook inbox requires operator attention",
            dead_letter=health["dead_letter"],
            uncertain_operations=health["uncertain_operations"],
            failed_operations=health["failed_operations"],
            oldest_actionable_age_seconds=health["oldest_actionable_age_seconds"],
        )
    return cast(
        dict[str, int | float | None],
        {
            **health,
            "published": len(delivery_ids),
            "purged": purged,
            "pipelines": pipelines,
        },
    )


def inbox_health(*, now: datetime | None = None) -> InboxHealth:
    now = now or datetime.now(UTC)
    with get_session() as session:
        counts = {
            state: 0
            for state in (
                "pending",
                "processing",
                "retry_wait",
                "completed",
                "dead_letter",
            )
        }
        for state, count in session.exec(
            select(ExternalWebhookDelivery.state, func.count()).group_by(
                ExternalWebhookDelivery.state
            )
        ).all():
            counts[str(state)] = int(count)
        oldest = session.exec(
            select(func.min(ExternalWebhookDelivery.received_at)).where(
                cast(Any, ExternalWebhookDelivery.state).in_(
                    ["pending", "processing", "retry_wait"]
                )
            )
        ).one()
        uncertain = session.exec(
            select(func.count())
            .select_from(ExternalWebhookOperation)
            .where(
                or_(
                    cast(Any, ExternalWebhookOperation.state) == "uncertain",
                    and_(
                        cast(Any, ExternalWebhookOperation.state) == "in_flight",
                        cast(Any, ExternalWebhookOperation.created_at)
                        <= now - ALERT_AGE,
                    ),
                )
            )
        ).one()
        failed = session.exec(
            select(func.count())
            .select_from(ExternalWebhookOperation)
            .where(ExternalWebhookOperation.state == "failed")
        ).one()
    age = max(0.0, (now - oldest).total_seconds()) if oldest is not None else None
    return {
        "pending": counts["pending"],
        "processing": counts["processing"],
        "retry_wait": counts["retry_wait"],
        "completed": counts["completed"],
        "dead_letter": counts["dead_letter"],
        "oldest_actionable_age_seconds": age,
        "uncertain_operations": int(uncertain),
        "failed_operations": int(failed),
    }


def redrive_delivery(delivery_id: str, *, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    parsed_id = _delivery_id(delivery_id)
    with get_session() as session:
        delivery = session.exec(
            select(ExternalWebhookDelivery)
            .where(ExternalWebhookDelivery.id == parsed_id)
            .with_for_update()
        ).one_or_none()
        if (
            delivery is None
            or delivery.state != "dead_letter"
            or delivery.encrypted_payload is None
            or delivery.payload_expires_at <= now
        ):
            return False
        delivery.state = "pending"
        delivery.attempt_count = 0
        delivery.next_attempt_at = now
        delivery.lease_expires_at = None
        delivery.wakeup_after = None
        delivery.last_error_code = None
        session.add(delivery)
        session.commit()
    publish_best_effort(delivery_id)
    return True
