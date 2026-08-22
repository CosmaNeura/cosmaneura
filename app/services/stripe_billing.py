"""Fail-closed Stripe billing for practices (ADR 0017 billing v2).

The platform remains the entitlement system of record. Stripe supplies
authenticated commerce state, but that state is accepted only after the
runtime account, livemode, configured price, subscription shape, and event
ordering have all been verified.
"""

import hashlib
import hmac
import json
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Any, cast
from urllib.parse import urlparse

import httpx
from typeid import TypeID
from typeid.errors import TypeIDException

from app import log
from app.configuration import stripe as stripe_config

from activemodel.session_manager import SessionManager, get_session
from app.models.practice import ENTITLED_SUBSCRIPTION_STATUSES, Practice, PracticeMember
from app.models.stripe_event_inbox import StripeEventInbox
from app.models.stripe_event_receipt import StripeEventReceipt
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

STRIPE_API = "https://api.stripe.com/v1"
SIGNATURE_TOLERANCE_SECONDS = 5 * 60
MINIMUM_CHECKOUT_TRIAL_LEAD = timedelta(hours=48)
EXPECTED_PRICE_CURRENCY = "usd"
EXPECTED_PRICE_UNIT_AMOUNT = 8_900
EXPECTED_PRICE_INTERVAL = "month"
MAX_STATE_APPLY_ATTEMPTS = 3

MIRRORED_EVENT_TYPES = {
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "customer.subscription.paused",
    "customer.subscription.resumed",
}
KNOWN_SUBSCRIPTION_STATUSES = {
    "incomplete",
    "incomplete_expired",
    "trialing",
    "active",
    "past_due",
    "canceled",
    "unpaid",
    "paused",
}
TERMINAL_SUBSCRIPTION_STATUSES = {"canceled", "incomplete_expired"}


class StripeNotConfigured(Exception):
    pass


class StripeStateInvalid(Exception):
    """Stripe returned a signed/authenticated state that violates our contract."""


class StripeStateConflict(Exception):
    """Authoritative state changed too often to apply a safe snapshot."""


class StripeSubscriptionContractInvalid(StripeStateInvalid):
    """A fetched subscription is authentic enough to revoke, but not to trust."""

    def __init__(self, subscription: dict[str, Any], message: str):
        self.subscription = subscription
        super().__init__(message)


class StripeTrialCarryoverDeferred(Exception):
    def __init__(self, available_at: datetime):
        self.available_at = available_at
        super().__init__(
            "Checkout is deferred until the platform trial ends so no remaining "
            "trial time is shortened."
        )


class StripeCheckoutAlreadyCompleted(Exception):
    """A delayed/lost webhook was recovered from the durable session id."""


def _isolated_state_session() -> Session:
    """Open a transaction that never reuses FastAPI's request-wide session.

    Tests deliberately bind all sessions to one rollback connection; preserving
    that bind keeps their isolation while production uses a fresh pooled
    connection and a genuinely short transaction.
    """

    manager = SessionManager.get_instance()
    if manager.session_connection is not None:
        return Session(bind=manager.session_connection)
    return Session(manager.get_engine())


def _request(
    method: str,
    path: str,
    data: dict[str, Any] | None = None,
    *,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    if not stripe_config.stripe_runtime_configured():
        raise StripeNotConfigured
    headers = {"Stripe-Version": stripe_config.STRIPE_API_VERSION}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    response = httpx.request(
        method,
        f"{STRIPE_API}{path}",
        auth=(stripe_config.STRIPE_SECRET_KEY, ""),
        data=data if method != "GET" else None,
        params=data if method == "GET" else None,
        headers=headers,
        timeout=20,
    )
    response.raise_for_status()
    return cast(dict[str, Any], response.json())


def _validate_runtime_account() -> None:
    account = _request("GET", "/account")
    if account.get("id") != stripe_config.STRIPE_ACCOUNT_ID:
        raise StripeStateInvalid("Stripe secret key resolved to the wrong account")


def _validate_runtime_price(*, require_active: bool) -> None:
    price = _request("GET", f"/prices/{stripe_config.STRIPE_PRICE_ID}")
    recurring = price.get("recurring")
    if price.get("id") != stripe_config.STRIPE_PRICE_ID:
        raise StripeStateInvalid("Stripe returned the wrong configured price")
    _validate_livemode(price.get("livemode"), source="price")
    if require_active and price.get("active") is not True:
        raise StripeStateInvalid("configured Stripe price is not active")
    if price.get("type") != "recurring":
        raise StripeStateInvalid("configured Stripe price is not recurring")
    if (
        price.get("currency") != EXPECTED_PRICE_CURRENCY
        or price.get("unit_amount") != EXPECTED_PRICE_UNIT_AMOUNT
        or not isinstance(recurring, dict)
        or recurring.get("interval") != EXPECTED_PRICE_INTERVAL
        or recurring.get("interval_count") != 1
        or recurring.get("usage_type") != "licensed"
        or price.get("billing_scheme") != "per_unit"
        or price.get("transform_quantity") is not None
    ):
        raise StripeStateInvalid(
            "configured Stripe price does not match $89/clinician/month"
        )


def _validate_runtime_configuration(*, require_active_price: bool = False) -> None:
    _validate_runtime_account()
    # Archiving a price must stop new sales without disabling cancellation,
    # past-due, or deletion events for existing subscriptions.
    _validate_runtime_price(require_active=require_active_price)


def _validate_livemode(value: object, *, source: str) -> None:
    if value is not stripe_config.STRIPE_LIVE_MODE:
        raise StripeStateInvalid(f"{source} livemode does not match this deployment")


def _hosted_url(value: object, *, hostname: str) -> str:
    if not isinstance(value, str):
        raise StripeStateInvalid("Stripe hosted URL is missing")
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname != hostname:
        raise StripeStateInvalid("Stripe hosted URL has an unexpected origin")
    return value


def _subscription_item(subscription: dict[str, Any]) -> dict[str, Any]:
    items = (subscription.get("items") or {}).get("data") or []
    if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
        raise StripeStateInvalid("subscription must contain exactly one price item")
    item = cast(dict[str, Any], items[0])
    price = item.get("price")
    price_id = price.get("id") if isinstance(price, dict) else price
    if price_id != stripe_config.STRIPE_PRICE_ID:
        raise StripeStateInvalid("subscription does not use the configured price")
    if not isinstance(item.get("id"), str) or not isinstance(item.get("quantity"), int):
        raise StripeStateInvalid("subscription item is missing id or quantity")
    return item


def _validate_subscription(subscription: dict[str, Any]) -> dict[str, Any]:
    subscription_id = subscription.get("id")
    customer_id = subscription.get("customer")
    status = subscription.get("status")
    if not isinstance(subscription_id, str) or not subscription_id.startswith("sub_"):
        raise StripeStateInvalid("subscription id is invalid")
    if not isinstance(customer_id, str) or not customer_id.startswith("cus_"):
        raise StripeStateInvalid("subscription customer id is invalid")
    if status not in KNOWN_SUBSCRIPTION_STATUSES:
        raise StripeStateInvalid("subscription status is unknown")
    _validate_livemode(subscription.get("livemode"), source="subscription")
    _subscription_item(subscription)
    metadata = subscription.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        raise StripeStateInvalid("subscription metadata is invalid")
    pause_collection = subscription.get("pause_collection")
    if pause_collection is not None and not isinstance(pause_collection, dict):
        raise StripeStateInvalid("subscription pause_collection is invalid")
    return subscription


def _mirrored_subscription_status(subscription: dict[str, Any]) -> str:
    # Portal pause_collection does not change Stripe's top-level `active`
    # status, but it does stop collection. Fail closed locally until cleared.
    status = cast(str, subscription["status"])
    if status in TERMINAL_SUBSCRIPTION_STATUSES:
        return status
    if subscription.get("pause_collection") is not None:
        return "paused"
    return status


def _retrieve_subscription(
    subscription_id: str,
    *,
    deleted_fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        subscription = _request("GET", f"/subscriptions/{subscription_id}")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404 or deleted_fallback is None:
            raise
        subscription = deleted_fallback
    try:
        return _validate_subscription(subscription)
    except StripeStateInvalid as exc:
        raise StripeSubscriptionContractInvalid(subscription, str(exc)) from exc


def _contract_snapshot_matches_binding(
    subscription: dict[str, Any], practice: Practice, subscription_id: str
) -> bool:
    return bool(
        practice.stripe_subscription_id == subscription_id
        and subscription.get("id") == subscription_id
        and subscription.get("livemode") is stripe_config.STRIPE_LIVE_MODE
    )


def _event_receipt(
    *,
    event_id: str,
    event_type: str,
    event_created_at: datetime,
    practice_id: Any,
) -> StripeEventReceipt:
    return StripeEventReceipt(
        event_id=event_id,
        event_type=event_type,
        event_created_at=event_created_at,
        practice_id=practice_id,
    )


def _fail_closed_invalid_bound_subscription(
    practice: Practice,
    subscription_id: str,
    invalid: StripeSubscriptionContractInvalid,
    *,
    expected_state_version: int,
    event_created_at: datetime | None,
    event_id: str | None,
    event_type: str | None,
) -> bool | None:
    """Revoke stale entitlement when a fresh bound contract becomes invalid."""

    with _isolated_state_session() as session:
        row = session.exec(
            select(Practice).where(Practice.id == practice.id).with_for_update()
        ).one()
        if row.stripe_state_version != expected_state_version:
            session.commit()
            return None
        if not _contract_snapshot_matches_binding(
            invalid.subscription, row, subscription_id
        ):
            raise invalid
        if event_id is not None:
            receipt = session.exec(
                select(StripeEventReceipt).where(
                    StripeEventReceipt.event_id == event_id
                )
            ).one_or_none()
            if receipt is not None:
                session.commit()
                return False
        if (
            event_created_at is not None
            and row.stripe_last_event_created_at is not None
            and event_created_at < row.stripe_last_event_created_at
        ):
            assert event_id and event_type
            session.add(
                _event_receipt(
                    event_id=event_id,
                    event_type=event_type,
                    event_created_at=event_created_at,
                    practice_id=row.id,
                )
            )
            session.commit()
            return False

        row.subscription_status = "invalid"
        row.stripe_state_version += 1
        row.stripe_quantity_sync_required = False
        row.stripe_quantity_sync_error = str(invalid)[:512]
        if event_created_at is not None:
            assert event_id and event_type
            row.stripe_last_event_created_at = event_created_at
            row.stripe_last_event_id = event_id
            session.add(
                _event_receipt(
                    event_id=event_id,
                    event_type=event_type,
                    event_created_at=event_created_at,
                    practice_id=row.id,
                )
            )
        session.add(row)
        session.commit()
    practice.refresh()
    log.error(
        "bound Stripe subscription violated the commercial contract; access revoked",
        practice_id=str(practice.id),
        subscription_id=subscription_id,
        error=str(invalid),
    )
    return True


def _assert_subscription_belongs_to_practice(
    subscription: dict[str, Any],
    practice: Practice,
    *,
    allow_terminal_rebind: bool,
) -> None:
    existing_subscription = practice.stripe_subscription_id
    if (
        existing_subscription
        and existing_subscription != subscription["id"]
        and (
            not allow_terminal_rebind
            or practice.subscription_status not in TERMINAL_SUBSCRIPTION_STATUSES
        )
    ):
        raise StripeStateInvalid("practice is already bound to another subscription")
    existing_customer = practice.stripe_customer_id
    if existing_customer and existing_customer != subscription["customer"]:
        raise StripeStateInvalid("practice is already bound to another Stripe customer")
    metadata_practice_id = (subscription.get("metadata") or {}).get("practice_id")
    if existing_subscription == subscription["id"] and metadata_practice_id != str(
        practice.id
    ):
        # Metadata is mutable in Stripe. Once a subscription is bound by its
        # unique id, missing/drifted metadata must not redirect a cancellation.
        log.error(
            "stripe subscription metadata drift",
            practice_id=str(practice.id),
            subscription_id=subscription["id"],
            metadata_matches=False,
        )
    elif metadata_practice_id not in (None, str(practice.id)):
        raise StripeStateInvalid("subscription metadata belongs to another practice")


def _apply_authoritative_subscription(
    practice: Practice,
    subscription_id: str,
    *,
    event_created_at: datetime | None = None,
    event_id: str | None = None,
    event_type: str | None = None,
    clear_checkout: bool = False,
    require_practice_metadata: bool = False,
    allow_terminal_rebind: bool = False,
    deleted_fallback: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, bool]:
    """Fetch outside a transaction, then apply through a short optimistic lock.

    Stripe calls can consume the full HTTP timeout, so no database row lock may
    span them. A durable version detects an intervening webhook/refresh; on a
    mismatch we refetch rather than applying a stale entitlement snapshot.
    """

    _validate_runtime_configuration()
    for _attempt in range(MAX_STATE_APPLY_ATTEMPTS):
        with _isolated_state_session() as session:
            if event_id is not None:
                receipt = session.exec(
                    select(StripeEventReceipt).where(
                        StripeEventReceipt.event_id == event_id
                    )
                ).one_or_none()
                if receipt is not None:
                    return None, False
            snapshot = session.exec(
                select(Practice).where(Practice.id == practice.id)
            ).one()
            snapshot_version = snapshot.stripe_state_version

        try:
            subscription = _retrieve_subscription(
                subscription_id, deleted_fallback=deleted_fallback
            )
        except StripeSubscriptionContractInvalid as invalid:
            applied = _fail_closed_invalid_bound_subscription(
                practice,
                subscription_id,
                invalid,
                expected_state_version=snapshot_version,
                event_created_at=event_created_at,
                event_id=event_id,
                event_type=event_type,
            )
            if applied is None:
                continue
            return invalid.subscription, applied

        with _isolated_state_session() as session:
            row = session.exec(
                select(Practice).where(Practice.id == practice.id).with_for_update()
            ).one()
            if event_id is not None:
                existing_receipt = session.exec(
                    select(StripeEventReceipt).where(
                        StripeEventReceipt.event_id == event_id
                    )
                ).one_or_none()
                if existing_receipt is not None:
                    session.commit()
                    return None, False
            if (
                event_created_at is not None
                and row.stripe_last_event_created_at is not None
                and event_created_at < row.stripe_last_event_created_at
            ):
                assert event_id and event_type
                session.add(
                    _event_receipt(
                        event_id=event_id,
                        event_type=event_type,
                        event_created_at=event_created_at,
                        practice_id=row.id,
                    )
                )
                session.commit()
                return None, False
            if row.stripe_state_version != snapshot_version:
                session.commit()
                continue

            contract_error: StripeStateInvalid | None = None
            try:
                _assert_subscription_belongs_to_practice(
                    subscription,
                    row,
                    allow_terminal_rebind=allow_terminal_rebind,
                )
            except StripeStateInvalid as exc:
                if row.stripe_subscription_id == subscription["id"]:
                    contract_error = exc
                else:
                    raise
            metadata_matches = (subscription.get("metadata") or {}).get(
                "practice_id"
            ) == str(row.id)
            mirrored_status = _mirrored_subscription_status(subscription)
            if require_practice_metadata and not metadata_matches:
                if row.stripe_subscription_id == subscription["id"]:
                    contract_error = StripeStateInvalid(
                        "bound subscription practice metadata is missing"
                    )
                else:
                    raise StripeStateInvalid(
                        "subscription practice metadata is missing"
                    )
            if (
                row.stripe_subscription_id == subscription["id"]
                and not metadata_matches
                and mirrored_status in ENTITLED_SUBSCRIPTION_STATUSES
            ):
                contract_error = StripeStateInvalid(
                    "bound subscription metadata cannot grant entitlement"
                )

            if contract_error is not None:
                row.subscription_status = "invalid"
                row.stripe_state_version += 1
                row.stripe_quantity_sync_required = False
                row.stripe_quantity_sync_error = str(contract_error)[:512]
                if event_created_at is not None:
                    assert event_id and event_type
                    row.stripe_last_event_created_at = event_created_at
                    row.stripe_last_event_id = event_id
                    session.add(
                        _event_receipt(
                            event_id=event_id,
                            event_type=event_type,
                            event_created_at=event_created_at,
                            practice_id=row.id,
                        )
                    )
                session.add(row)
                session.commit()
                practice.refresh()
                log.error(
                    "bound Stripe subscription identity drift; access revoked",
                    practice_id=str(practice.id),
                    subscription_id=subscription_id,
                    error=str(contract_error),
                )
                return subscription, True

            row.stripe_customer_id = subscription["customer"]
            row.stripe_subscription_id = subscription["id"]
            row.subscription_status = mirrored_status
            row.stripe_state_version += 1
            if event_created_at is not None:
                assert event_id and event_type
                row.stripe_last_event_created_at = event_created_at
                row.stripe_last_event_id = event_id
                session.add(
                    _event_receipt(
                        event_id=event_id,
                        event_type=event_type,
                        event_created_at=event_created_at,
                        practice_id=row.id,
                    )
                )
            if clear_checkout:
                _clear_checkout_session(row, advance=True)

            count = session.exec(
                select(func.count())
                .select_from(PracticeMember)
                .where(
                    PracticeMember.practice_id == row.id,
                    col(PracticeMember.attested_at).is_not(None),
                )
            ).one()
            target = max(1, int(count))
            item = _subscription_item(subscription)
            quantity_sync_required = (
                mirrored_status not in TERMINAL_SUBSCRIPTION_STATUSES
                and item["quantity"] != target
            )
            if quantity_sync_required and (
                not row.stripe_quantity_sync_required
                or row.stripe_quantity_sync_target != target
            ):
                row.stripe_quantity_sync_version += 1
            row.stripe_quantity_sync_required = quantity_sync_required
            row.stripe_quantity_sync_target = (
                target
                if mirrored_status not in TERMINAL_SUBSCRIPTION_STATUSES
                else None
            )
            if not quantity_sync_required:
                row.stripe_quantity_synced_at = datetime.now(timezone.utc)
            row.stripe_quantity_sync_error = None
            session.add(row)
            session.commit()
        practice.refresh()
        return subscription, True

    raise StripeStateConflict(
        "Stripe subscription state changed during every reconciliation attempt"
    )


def attested_member_count(practice: Practice) -> int:
    members = PracticeMember.where(
        PracticeMember.practice_id == practice.id,
        PracticeMember.attested_at != None,  # noqa: E711
    ).all()
    return max(1, len(list(members)))


def checkout_deferred_until(
    practice: Practice, *, now: datetime | None = None
) -> datetime | None:
    """Return the trial end when Checkout cannot preserve an exact short trial.

    Stripe Checkout requires ``trial_end`` to be at least 48 hours away. We
    fail closed during that final window instead of silently charging early or
    extending the platform's exact trial promise.
    """

    current = now or datetime.now(timezone.utc)
    if not practice.trial_ends_at:
        return None
    remaining = practice.trial_ends_at - current
    if timedelta(0) < remaining <= MINIMUM_CHECKOUT_TRIAL_LEAD:
        return practice.trial_ends_at
    return None


def has_nonterminal_subscription(practice: Practice) -> bool:
    return bool(
        practice.stripe_subscription_id
        and practice.subscription_status not in TERMINAL_SUBSCRIPTION_STATUSES
    )


def has_resumable_checkout(practice: Practice) -> bool:
    # A normal subscription-mode Checkout can remain open before Stripe has
    # created or reported a subscription. The durable session id is enough to
    # offer Resume; `_recover_checkout_session` still retrieves Stripe and
    # validates status, livemode, practice binding, expiry, and any eventual
    # subscription binding before returning the hosted URL.
    return bool(practice.stripe_checkout_session_id)


def refresh_subscription_state(practice: Practice) -> dict[str, Any] | None:
    """Refresh an existing subscription before deciding whether Checkout is safe."""

    if not practice.stripe_subscription_id:
        return None
    subscription, _ = _apply_authoritative_subscription(
        practice, practice.stripe_subscription_id
    )
    return subscription


def _checkout_idempotency_key(practice: Practice) -> str:
    raw = f"cosmas-checkout:{practice.id}:{practice.stripe_checkout_attempt}"
    return f"cosmas-checkout-{hashlib.sha256(raw.encode()).hexdigest()}"


def _clear_checkout_session(practice: Practice, *, advance: bool) -> None:
    if advance:
        practice.stripe_checkout_attempt += 1
    practice.stripe_checkout_session_id = None
    practice.stripe_checkout_session_url = None
    practice.stripe_checkout_session_expires_at = None
    practice.stripe_checkout_state_version += 1


def _checkout_cache_snapshot(practice_id: Any) -> tuple[int, str | None]:
    with _isolated_state_session() as session:
        row = session.exec(select(Practice).where(Practice.id == practice_id)).one()
        return row.stripe_checkout_state_version, row.stripe_checkout_session_id


def _persist_checkout_cache(
    practice: Practice,
    *,
    expected_version: int,
    expected_session_id: str | None,
    session_id: str | None,
    url: str | None,
    expires_at: datetime | None,
    advance: bool = False,
) -> None:
    """Write/clear the hosted-session cache without resurrecting stale state."""

    with _isolated_state_session() as session:
        row = session.exec(
            select(Practice).where(Practice.id == practice.id).with_for_update()
        ).one()
        if (
            row.stripe_checkout_state_version != expected_version
            or row.stripe_checkout_session_id != expected_session_id
        ):
            session.commit()
            raise StripeStateConflict("Checkout session state changed while in flight")
        if advance:
            row.stripe_checkout_attempt += 1
        row.stripe_checkout_session_id = session_id
        row.stripe_checkout_session_url = url
        row.stripe_checkout_session_expires_at = expires_at
        row.stripe_checkout_state_version += 1
        session.add(row)
        session.commit()
    practice.refresh()


def _checkout_session_expiry(session: dict[str, Any]) -> datetime:
    expires_at = session.get("expires_at")
    if not isinstance(expires_at, int):
        raise StripeStateInvalid("checkout session expiry is invalid")
    return datetime.fromtimestamp(expires_at, tz=timezone.utc)


def _validate_checkout_session_binding(
    session: dict[str, Any], practice: Practice, *, expected_id: str | None = None
) -> None:
    session_id = session.get("id")
    if not isinstance(session_id, str) or not session_id.startswith("cs_"):
        raise StripeStateInvalid("checkout session id is invalid")
    if expected_id is not None and session_id != expected_id:
        raise StripeStateInvalid("Stripe returned the wrong checkout session")
    _validate_livemode(session.get("livemode"), source="checkout session")
    if session.get("mode") != "subscription":
        raise StripeStateInvalid("checkout session is not a subscription")
    if session.get("client_reference_id") != str(practice.id):
        raise StripeStateInvalid("checkout session practice binding changed")


def _recover_checkout_session(practice: Practice, *, current: datetime) -> str | None:
    """Resolve a durable prior Checkout attempt before rotating its key.

    An expired local URL is not proof that Checkout was abandoned: the session
    might have completed while its webhook was delayed. Retrieve it from Stripe
    and bind any resulting subscription before a new attempt is allowed.
    """

    expected_version, session_id = _checkout_cache_snapshot(practice.id)
    if not session_id:
        return None
    session = _request("GET", f"/checkout/sessions/{session_id}")
    _validate_checkout_session_binding(session, practice, expected_id=session_id)
    session_status = session.get("status")
    if session_status == "open":
        session_subscription = session.get("subscription")
        if practice.subscription_status == "incomplete" and (
            session_subscription != practice.stripe_subscription_id
        ):
            raise StripeStateInvalid(
                "open checkout does not match the incomplete subscription"
            )
        if (
            isinstance(session_subscription, str)
            and practice.stripe_subscription_id
            and session_subscription != practice.stripe_subscription_id
        ):
            raise StripeStateInvalid("checkout subscription binding changed")
        expiry = _checkout_session_expiry(session)
        if expiry <= current:
            raise StripeStateInvalid(
                "Stripe reports an open but expired checkout session"
            )
        url = _hosted_url(session.get("url"), hostname="checkout.stripe.com")
        _persist_checkout_cache(
            practice,
            expected_version=expected_version,
            expected_session_id=session_id,
            session_id=session_id,
            url=url,
            expires_at=expiry,
        )
        return url
    if session_status == "complete":
        subscription_id = session.get("subscription")
        if not isinstance(subscription_id, str) or not subscription_id.startswith(
            "sub_"
        ):
            raise StripeStateInvalid("completed checkout has no subscription")
        subscription, _ = _apply_authoritative_subscription(
            practice,
            subscription_id,
            clear_checkout=True,
            require_practice_metadata=True,
            allow_terminal_rebind=True,
        )
        assert subscription is not None
        raise StripeCheckoutAlreadyCompleted
    if session_status == "expired":
        if session.get("subscription") is not None:
            raise StripeStateInvalid("expired checkout unexpectedly has a subscription")
        _persist_checkout_cache(
            practice,
            expected_version=expected_version,
            expected_session_id=session_id,
            session_id=None,
            url=None,
            expires_at=None,
            advance=True,
        )
        return None
    raise StripeStateInvalid("checkout session status is unknown")


def create_checkout_session(
    practice: Practice,
    *,
    customer_email: str | None,
    success_url: str,
    cancel_url: str,
    now: datetime | None = None,
) -> str:
    """Get or create one idempotent hosted Checkout Session for the practice."""

    current = now or datetime.now(timezone.utc)
    # A previously-created exact session is not a new sale. Revalidate and
    # return it before applying new-session trial/active-price gates, otherwise
    # the final 48h or a later price archive would strand payment completion.
    if practice.stripe_checkout_session_id:
        _validate_runtime_configuration()
        recovered_url = _recover_checkout_session(practice, current=current)
        if recovered_url:
            return recovered_url
    if practice.subscription_status == "incomplete":
        if not practice.stripe_checkout_session_id:
            raise StripeStateInvalid(
                "incomplete subscription has no resumable Checkout session"
            )
        raise StripeStateInvalid("incomplete Checkout session is no longer resumable")

    # A cached session that proved expired/abandoned is no longer a resume.
    # Re-enforce acquisition readiness before rotating the idempotency key or
    # creating a replacement sale; maintenance credentials alone are not
    # authorization to bypass the new-checkout kill switch.
    if not stripe_config.stripe_billing_configured():
        raise StripeNotConfigured

    deferred_until = checkout_deferred_until(practice, now=current)
    if deferred_until:
        raise StripeTrialCarryoverDeferred(deferred_until)
    _validate_runtime_configuration(require_active_price=True)
    if now is None:
        deferred_until = checkout_deferred_until(
            practice, now=datetime.now(timezone.utc)
        )
        if deferred_until:
            raise StripeTrialCarryoverDeferred(deferred_until)

    data: dict[str, Any] = {
        "mode": "subscription",
        "line_items[0][price]": stripe_config.STRIPE_PRICE_ID,
        "line_items[0][quantity]": str(attested_member_count(practice)),
        "client_reference_id": str(practice.id),
        "success_url": success_url,
        "cancel_url": cancel_url,
        "allow_promotion_codes": "true",
        "payment_method_collection": "if_required",
        "subscription_data[metadata][practice_id]": str(practice.id),
    }
    if practice.stripe_customer_id:
        data["customer"] = practice.stripe_customer_id
    elif customer_email:
        data["customer_email"] = customer_email

    if practice.trial_ends_at and practice.trial_ends_at > current:
        data["subscription_data[trial_end]"] = str(
            math.ceil(practice.trial_ends_at.timestamp())
        )
        data[
            "subscription_data[trial_settings][end_behavior][missing_payment_method]"
        ] = "pause"

    expected_version, expected_session_id = _checkout_cache_snapshot(practice.id)
    session = _request(
        "POST",
        "/checkout/sessions",
        data,
        idempotency_key=_checkout_idempotency_key(practice),
    )
    _validate_checkout_session_binding(session, practice)
    if session.get("status") != "open":
        raise StripeStateInvalid("new checkout session is not open")
    session_id = cast(str, session["id"])
    expiry = _checkout_session_expiry(session)
    if expiry <= current:
        raise StripeStateInvalid("checkout session was already expired")
    url = _hosted_url(session.get("url"), hostname="checkout.stripe.com")

    try:
        _persist_checkout_cache(
            practice,
            expected_version=expected_version,
            expected_session_id=expected_session_id,
            session_id=session_id,
            url=url,
            expires_at=expiry,
        )
    except StripeStateConflict:
        practice.refresh()
        if practice.stripe_checkout_session_id:
            recovered_url = _recover_checkout_session(practice, current=current)
            if recovered_url:
                return recovered_url
        if has_nonterminal_subscription(practice):
            raise StripeCheckoutAlreadyCompleted
        raise
    return url


def create_portal_session(practice: Practice, *, return_url: str) -> str:
    assert practice.stripe_customer_id
    _validate_runtime_configuration()
    session = _request(
        "POST",
        "/billing_portal/sessions",
        {"customer": practice.stripe_customer_id, "return_url": return_url},
    )
    return _hosted_url(session.get("url"), hostname="billing.stripe.com")


def _quantity_sync_snapshot(
    practice_id: Any, *, force_new_version: bool
) -> tuple[int, int, str] | None:
    """Persist and return a member-count obligation under a row lock."""

    with get_session() as session:
        row = session.exec(
            select(Practice).where(Practice.id == practice_id).with_for_update()
        ).one()
        if not row.stripe_subscription_id:
            row.stripe_quantity_sync_required = False
            row.stripe_quantity_sync_target = None
            session.add(row)
            session.commit()
            return None
        count = session.exec(
            select(func.count())
            .select_from(PracticeMember)
            .where(
                PracticeMember.practice_id == row.id,
                col(PracticeMember.attested_at).is_not(None),
            )
        ).one()
        target = max(1, int(count))
        if (
            force_new_version
            or not row.stripe_quantity_sync_required
            or row.stripe_quantity_sync_target != target
        ):
            row.stripe_quantity_sync_version += 1
        row.stripe_quantity_sync_required = True
        row.stripe_quantity_sync_target = target
        row.stripe_quantity_sync_error = None
        version = row.stripe_quantity_sync_version
        session.add(row)
        session.commit()
        assert row.stripe_subscription_id
        return target, version, row.stripe_subscription_id


def mark_quantity_sync_required(practice: Practice) -> None:
    _quantity_sync_snapshot(practice.id, force_new_version=True)


def _finish_quantity_sync(
    practice_id: Any,
    *,
    subscription_id: str,
    version: int,
    target: int,
    error: Exception | None = None,
) -> bool:
    """Finish only the exact obligation that initiated the remote request."""

    with get_session() as session:
        row = session.exec(
            select(Practice).where(Practice.id == practice_id).with_for_update()
        ).one()
        if row.stripe_subscription_id != subscription_id:
            # The practice was rebound while the old subscription request was
            # in flight. Never clear or poison the new subscription's repair.
            session.commit()
            return False
        if error is not None:
            row.stripe_quantity_sync_required = True
            row.stripe_quantity_sync_attempts += 1
            row.stripe_quantity_sync_error = str(error)[:512] or type(error).__name__
            session.add(row)
            session.commit()
            return False
        if row.stripe_quantity_sync_version != version:
            # A join/leave landed while Stripe was in flight. Never let this
            # older response clear the newer target; the next sweep repairs it.
            row.stripe_quantity_sync_required = True
            session.add(row)
            session.commit()
            return False
        row.stripe_quantity_sync_required = False
        row.stripe_quantity_sync_target = (
            None
            if row.subscription_status in TERMINAL_SUBSCRIPTION_STATUSES
            else target
        )
        row.stripe_quantity_sync_error = None
        row.stripe_quantity_synced_at = datetime.now(timezone.utc)
        session.add(row)
        session.commit()
        return True


def sync_subscription_quantity(
    practice: Practice, *, raise_on_error: bool = False
) -> bool:
    """Reconcile the paid quantity, leaving durable retry state on failure."""

    snapshot = _quantity_sync_snapshot(practice.id, force_new_version=False)
    if snapshot is None:
        return True
    target, version, subscription_id = snapshot
    try:
        practice.refresh()
        assert practice.stripe_subscription_id
        subscription = refresh_subscription_state(practice)
        assert subscription is not None
        practice.refresh()
        if (
            practice.subscription_status == "invalid"
            or practice.stripe_subscription_id != subscription_id
            or subscription["id"] != subscription_id
            or subscription["customer"] != practice.stripe_customer_id
        ):
            raise StripeStateInvalid(
                "Stripe subscription identity or contract is not safe for quantity sync"
            )
        if subscription["status"] in TERMINAL_SUBSCRIPTION_STATUSES:
            return _finish_quantity_sync(
                practice.id,
                subscription_id=subscription_id,
                version=version,
                target=target,
            )
        else:
            item = _subscription_item(subscription)
            if item["quantity"] != target:
                key_source = f"quantity:{subscription['id']}:{target}:version:{version}"
                _request(
                    "POST",
                    f"/subscription_items/{item['id']}",
                    {"quantity": str(target)},
                    idempotency_key=(
                        "cosmas-quantity-"
                        + hashlib.sha256(key_source.encode()).hexdigest()
                    ),
                )
                # Do not trust a successful HTTP status alone. Retrieve the
                # authoritative subscription and confirm the exact quantity.
                subscription = _retrieve_subscription(practice.stripe_subscription_id)
                confirmed = _subscription_item(subscription)
                if confirmed["quantity"] != target:
                    raise StripeStateInvalid(
                        "Stripe did not confirm the requested subscription quantity"
                    )
            return _finish_quantity_sync(
                practice.id,
                subscription_id=subscription_id,
                version=version,
                target=target,
            )
    except Exception as exc:
        _finish_quantity_sync(
            practice.id,
            subscription_id=subscription_id,
            version=version,
            target=target,
            error=exc,
        )
        log.warning(
            "failed to sync stripe subscription quantity",
            practice_id=str(practice.id),
            target=target,
            exc_info=True,
        )
        if raise_on_error:
            raise
        return False


def reconcile_pending_quantity_syncs() -> tuple[int, int]:
    # Sweep every bound subscription, not just the flagged rows. This is the
    # fail-safe for a process death between membership commit and obligation
    # marking, and bounds any repair gap to the scheduler interval.
    pending = cast(
        list[Practice],
        list(
            Practice.where(Practice.stripe_subscription_id != None).all()  # noqa: E711
        ),
    )
    succeeded = sum(sync_subscription_quantity(practice) for practice in pending)
    return succeeded, len(pending) - succeeded


def verify_webhook_signature(
    payload: bytes, signature_header: str | None, *, now: int | None = None
) -> bool:
    """Verify Stripe's timestamped HMAC signature with replay tolerance."""

    secret = stripe_config.STRIPE_WEBHOOK_SECRET
    if not secret or not signature_header:
        return False
    timestamp = ""
    candidates: list[str] = []
    for part in signature_header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            timestamp = value
        elif key == "v1":
            candidates.append(value)
    if not timestamp or not candidates:
        return False
    try:
        signed_at = int(timestamp)
    except ValueError:
        return False
    current = now if now is not None else int(time.time())
    if abs(current - signed_at) > SIGNATURE_TOLERANCE_SECONDS:
        return False
    expected = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.".encode("utf-8") + payload,
        hashlib.sha256,
    ).hexdigest()
    return any(hmac.compare_digest(expected, candidate) for candidate in candidates)


def _event_created_at(event: dict[str, Any]) -> datetime:
    created = event.get("created")
    if not isinstance(created, int):
        raise StripeStateInvalid("Stripe event creation time is missing")
    return datetime.fromtimestamp(created, tz=timezone.utc)


def _event_subscription_id(event_type: str, obj: dict[str, Any]) -> str:
    subscription_id = (
        obj.get("subscription")
        if event_type == "checkout.session.completed"
        else obj.get("id")
    )
    if not isinstance(subscription_id, str) or not subscription_id.startswith("sub_"):
        raise StripeStateInvalid("Stripe event has no valid subscription id")
    return subscription_id


def validate_stripe_event_envelope(
    event: dict[str, Any],
) -> tuple[str, str, datetime, dict[str, Any], str] | None:
    """Validate signed fields that require no Stripe network call."""

    event_type = event.get("type")
    if event_type not in MIRRORED_EVENT_TYPES:
        return None
    event_id = event.get("id")
    if not isinstance(event_id, str) or not event_id.startswith("evt_"):
        raise StripeStateInvalid("Stripe event id is invalid")
    if event.get("api_version") != stripe_config.SUPPORTED_STRIPE_API_VERSION:
        raise StripeStateInvalid(
            "Stripe event API version does not match the pinned contract"
        )
    if event.get("account") not in (None, stripe_config.STRIPE_ACCOUNT_ID):
        raise StripeStateInvalid("Stripe Connect event account does not match")
    _validate_livemode(event.get("livemode"), source="event")
    event_created_at = _event_created_at(event)
    obj = (event.get("data") or {}).get("object")
    if not isinstance(obj, dict):
        raise StripeStateInvalid("Stripe event object is invalid")
    subscription_id = _event_subscription_id(event_type, obj)
    return event_type, event_id, event_created_at, obj, subscription_id


def _minimal_stripe_event(event: dict[str, Any], obj: dict[str, Any]) -> dict[str, Any]:
    """Retain only fields needed for reconciliation; never billing details."""

    event_type = cast(str, event["type"])

    def practice_hint(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        try:
            parsed = TypeID.from_string(value)
        except (TypeError, ValueError, TypeIDException):
            return None
        return value if parsed.prefix == "practice" and str(parsed) == value else None

    if event_type == "checkout.session.completed":
        minimal_object = {
            "id": obj.get("id"),
            "client_reference_id": practice_hint(obj.get("client_reference_id")),
            "subscription": obj.get("subscription"),
        }
    else:
        items: list[dict[str, Any]] = []
        for item in (obj.get("items") or {}).get("data") or []:
            if isinstance(item, dict):
                price = item.get("price")
                items.append(
                    {
                        "id": item.get("id"),
                        "quantity": item.get("quantity"),
                        "price": {
                            "id": price.get("id") if isinstance(price, dict) else price
                        },
                    }
                )
        raw_metadata = obj.get("metadata")
        metadata: dict[str, Any] = (
            raw_metadata if isinstance(raw_metadata, dict) else {}
        )
        customer = obj.get("customer")
        customer_id = customer.get("id") if isinstance(customer, dict) else customer
        pause_collection = obj.get("pause_collection")
        minimal_pause = (
            {
                "behavior": pause_collection.get("behavior"),
                "resumes_at": pause_collection.get("resumes_at"),
            }
            if isinstance(pause_collection, dict)
            else None
        )
        minimal_object = {
            "id": obj.get("id"),
            "customer": customer_id,
            "status": obj.get("status"),
            "livemode": obj.get("livemode"),
            "metadata": {"practice_id": practice_hint(metadata.get("practice_id"))},
            "items": {"data": items},
            "pause_collection": minimal_pause,
        }
    return {
        "id": event["id"],
        "type": event_type,
        "created": event["created"],
        "api_version": event["api_version"],
        "livemode": event["livemode"],
        "account": event.get("account"),
        "data": {"object": minimal_object},
    }


def enqueue_stripe_event(event: dict[str, Any]) -> tuple[Any | None, bool]:
    """Durably accept a verified event before returning success to Stripe."""

    envelope = validate_stripe_event_envelope(event)
    if envelope is None:
        return None, False
    event_type, event_id, event_created_at, obj, subscription_id = envelope
    payload_json = json.dumps(
        _minimal_stripe_event(event, obj), separators=(",", ":"), sort_keys=True
    )

    try:
        with _isolated_state_session() as session:
            existing = session.exec(
                select(StripeEventInbox).where(StripeEventInbox.event_id == event_id)
            ).one_or_none()
            if existing is not None:
                return existing.id, True

            # A signed non-entitling or contract-invalid payload can safely
            # revoke while the worker obtains fresh state; payloads never grant.
            if event_type != "checkout.session.completed":
                practice = session.exec(
                    select(Practice)
                    .where(Practice.stripe_subscription_id == subscription_id)
                    .with_for_update()
                ).one_or_none()
                if practice is not None:
                    payload_contract_invalid = False
                    try:
                        _validate_subscription(obj)
                    except StripeStateInvalid:
                        payload_contract_invalid = True
                    payload_metadata_id = (obj.get("metadata") or {}).get("practice_id")
                    payload_identity_invalid = bool(
                        obj.get("customer") != practice.stripe_customer_id
                        or payload_metadata_id != str(practice.id)
                    )
                    should_revoke_pending = bool(
                        obj.get("status") not in ENTITLED_SUBSCRIPTION_STATUSES
                        or obj.get("pause_collection") is not None
                        or payload_contract_invalid
                        or payload_identity_invalid
                    )
                    if should_revoke_pending and (
                        practice.stripe_last_event_created_at is None
                        or event_created_at >= practice.stripe_last_event_created_at
                    ):
                        practice.subscription_status = "pending_verification"
                        practice.stripe_state_version += 1
                        session.add(practice)

            inbox = StripeEventInbox(
                event_id=event_id,
                event_type=event_type,
                payload_json=payload_json,
            )
            session.add(inbox)
            session.commit()
            session.refresh(inbox)
            return inbox.id, False
    except IntegrityError:
        # Concurrent deliveries race only on the unique event id; the winner's
        # committed inbox row is the durable acknowledgement.
        with _isolated_state_session() as session:
            existing = session.exec(
                select(StripeEventInbox).where(StripeEventInbox.event_id == event_id)
            ).one()
            return existing.id, True


def process_stripe_event_inbox(inbox_id: Any) -> bool:
    with _isolated_state_session() as session:
        inbox = session.exec(
            select(StripeEventInbox).where(StripeEventInbox.id == inbox_id)
        ).one()
        if inbox.state == "processed":
            return True
        payload_json = inbox.payload_json

    try:
        apply_stripe_event(cast(dict[str, Any], json.loads(payload_json)))
    except Exception as exc:
        with _isolated_state_session() as session:
            inbox = session.exec(
                select(StripeEventInbox)
                .where(StripeEventInbox.id == inbox_id)
                .with_for_update()
            ).one()
            if inbox.state == "processed":
                session.commit()
                return True
            inbox.state = "pending"
            inbox.attempts += 1
            inbox.last_error = (str(exc) or type(exc).__name__)[:512]
            backoff_minutes = min(2 ** min(inbox.attempts, 6), 60)
            inbox.next_attempt_at = datetime.now(timezone.utc) + timedelta(
                minutes=backoff_minutes
            )
            session.add(inbox)
            session.commit()
        raise

    with _isolated_state_session() as session:
        inbox = session.exec(
            select(StripeEventInbox)
            .where(StripeEventInbox.id == inbox_id)
            .with_for_update()
        ).one()
        inbox.state = "processed"
        inbox.attempts += 1
        inbox.last_error = None
        inbox.next_attempt_at = None
        inbox.processed_at = datetime.now(timezone.utc)
        inbox.payload_json = "{}"
        session.add(inbox)
        session.commit()
    return True


def reconcile_pending_stripe_events() -> tuple[int, int]:
    current = datetime.now(timezone.utc)
    inbox_id_column = col(StripeEventInbox.id)
    next_attempt_column = col(StripeEventInbox.next_attempt_at)
    with _isolated_state_session() as session:
        pending_ids = list(
            session.exec(
                select(inbox_id_column)
                .where(
                    StripeEventInbox.state == "pending",
                    or_(
                        next_attempt_column.is_(None),
                        next_attempt_column <= current,
                    ),
                )
                .order_by(
                    next_attempt_column.asc().nullsfirst(),
                    col(StripeEventInbox.created_at),
                    inbox_id_column,
                )
                .limit(100)
            ).all()
        )
    succeeded = 0
    failed = 0
    for inbox_id in pending_ids:
        try:
            succeeded += int(process_stripe_event_inbox(inbox_id))
        except Exception:
            failed += 1
            log.warning(
                "durable Stripe inbox reconciliation failed",
                inbox_id=str(inbox_id),
                exc_info=True,
            )
    return succeeded, failed


def apply_stripe_event(event: dict[str, Any]) -> bool:
    """Mirror a verified event only after fresh Stripe state validation.

    Different event IDs can be delivered out of order. Strictly older events
    are ignored, while current/equal-time events retrieve the subscription from
    Stripe so their payload order cannot regress entitlement.
    """

    envelope = validate_stripe_event_envelope(event)
    if envelope is None:
        return False
    event_type, event_id, event_created_at, obj, subscription_id = envelope

    # An immutable local subscription binding outranks mutable Stripe metadata.
    # Metadata is used only for an initial bind; otherwise drift cannot redirect
    # a revocation to a different practice.
    practice = Practice.get(stripe_subscription_id=subscription_id)
    resolved_from_metadata = False
    if practice is None and event_type == "checkout.session.completed":
        candidate_practice_id = obj.get("client_reference_id")
        practice = (
            Practice.get(id=candidate_practice_id)
            if isinstance(candidate_practice_id, str)
            else None
        )
    elif practice is None:
        metadata = obj.get("metadata") or {}
        candidate_practice_id = (
            metadata.get("practice_id") if isinstance(metadata, dict) else None
        )
        practice = (
            Practice.get(id=candidate_practice_id)
            if isinstance(candidate_practice_id, str)
            else None
        )
        resolved_from_metadata = practice is not None
    if practice is None:
        log.warning("stripe event matched no practice", event_type=event_type)
        return False

    subscription, applied = _apply_authoritative_subscription(
        practice,
        subscription_id,
        event_created_at=event_created_at,
        event_id=event_id,
        event_type=event_type,
        clear_checkout=event_type == "checkout.session.completed",
        require_practice_metadata=(
            event_type == "checkout.session.completed" or resolved_from_metadata
        ),
        allow_terminal_rebind=event_type == "checkout.session.completed",
        deleted_fallback=(
            obj if event_type == "customer.subscription.deleted" else None
        ),
    )
    if not applied:
        log.info(
            "ignored stale stripe event",
            event_id=event_id,
            practice_id=str(practice.id),
        )
        return False
    assert subscription is not None
    log.info(
        "stripe event mirrored",
        event_type=event_type,
        event_id=event_id,
        practice_id=str(practice.id),
        subscription_status=practice.subscription_status,
    )
    return True
