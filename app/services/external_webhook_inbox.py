"""Durable, encrypted inbox shared by the Zoom and Recall adapters."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, TypedDict, cast

from typeid import TypeID

from app import log

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


def digest_provider_reference(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def provider_log_ref(value: str | None) -> str:
    """Non-reversible, short log correlation token for adapter-private IDs."""

    if not value:
        return "absent"
    return f"sha256:{digest_provider_reference(str(value))[:12]}"


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
            for key in ("id", "user_id", "user_name")
            if participant.get(key) is not None
        }
        projected_obj["participant"] = projected_participant
    return {"event": event_type, "payload": {"object": projected_obj}}


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
    return f"payload:{hashlib.sha256(_canonical_json(payload).encode()).hexdigest()}"


def store_verified_external_webhook(
    *,
    provider: Literal["recall", "zoom"],
    provider_event_id: str,
    event_type: str,
    payload: dict[str, Any],
    now: datetime | None = None,
) -> StoredDelivery:
    """Commit a verified delivery before any broker publication or side effect."""

    now = now or datetime.now(UTC)
    event_digest = digest_provider_reference(provider_event_id)
    projected_json = _canonical_json(
        project_provider_payload(provider, event_type, payload)
    )
    payload_sha256 = hashlib.sha256(projected_json.encode("utf-8")).hexdigest()

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
            encrypted_payload=projected_json,
            payload_sha256=payload_sha256,
            next_attempt_at=now,
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


def store_legacy_queued_webhook(
    *,
    provider: str,
    event_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> StoredDelivery:
    if provider not in {"recall", "zoom"}:
        raise PermanentWebhookError()
    reference = stable_event_reference(provider, payload, event_id)
    return store_verified_external_webhook(
        provider=cast(Literal["recall", "zoom"], provider),
        provider_event_id=reference,
        event_type=event_type,
        payload=payload,
    )


def publish_delivery(delivery_id: str) -> None:
    """Publish only an internal TypeID; payloads and provider IDs stay in PG."""

    from app.jobs.external_webhooks import process_delivery_celery

    process_delivery_celery.delay(delivery_id)


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

        delivery.state = "processing"
        delivery.attempt_count += 1
        delivery.lease_expires_at = now + DELIVERY_LEASE
        delivery.wakeup_after = None
        delivery.last_error_code = None
        payload_json = delivery.encrypted_payload
        fence = delivery.attempt_count
        session.add(delivery)
        session.commit()
        return ClaimedDelivery(
            delivery_id=str(delivery.id),
            provider=delivery.provider,
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
        from app.services import zoom_webhooks

        zoom_webhooks.dispatch(event_type=claim.event_type, payload=payload)
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
        or (health["oldest_actionable_age_seconds"] or 0) > ALERT_AGE.total_seconds()
    ):
        log.error(
            "external webhook inbox requires operator attention",
            dead_letter=health["dead_letter"],
            uncertain_operations=health["uncertain_operations"],
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
    age = max(0.0, (now - oldest).total_seconds()) if oldest is not None else None
    return {
        "pending": counts["pending"],
        "processing": counts["processing"],
        "retry_wait": counts["retry_wait"],
        "completed": counts["completed"],
        "dead_letter": counts["dead_letter"],
        "oldest_actionable_age_seconds": age,
        "uncertain_operations": int(uncertain),
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
