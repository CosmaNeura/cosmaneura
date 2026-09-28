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
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, cast
from urllib.parse import urlparse

import httpx
from decouple import config
from typeid import TypeID
from typeid.errors import TypeIDException

from app import log
from app.configuration import stripe as stripe_config
from app.services.external_webhook_inbox import provider_log_ref
from app.services.public_entitlements import (
    MAX_ACTIVE_CLINICIAN_SEATS,
    PUBLIC_ENTITLEMENT_CONTRACT_VERSION,
    ClinicianSeatPolicyError,
    active_clinician_seat_count,
)

from activemodel.session_manager import SessionManager, get_session
from app.models.practice import (
    ENTITLED_SUBSCRIPTION_STATUSES,
    TRIAL_LENGTH,
    Practice,
    PracticeAcquisitionSource,
)
from app.models.stripe_event_inbox import StripeEventInbox
from app.models.stripe_event_receipt import StripeEventReceipt
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

STRIPE_API = "https://api.stripe.com/v1"
SIGNATURE_TOLERANCE_SECONDS = 5 * 60
MINIMUM_CHECKOUT_TRIAL_LEAD = timedelta(hours=48)
# Stripe validates the minimum lead against its own request-processing time.
# A small buffer prevents an otherwise-valid 48-hour timestamp from falling
# below that minimum while the Checkout Session request is in flight.
CHECKOUT_TRIAL_LEAD_SAFETY = timedelta(minutes=5)
# Stripe may prune idempotency results once they are 24 hours old. Stop replay
# slightly before that boundary and require an operator to reconcile the
# provider-side result before Core can authorize another attempt.
CHECKOUT_REQUEST_REPLAY_WINDOW = timedelta(hours=23, minutes=55)
# After Stripe's documented idempotency-retention boundary, leave a small
# transport margin before an operator may attest that no Session exists and
# rotate the local attempt. This closes the five-minute gap between Core's
# automatic replay cutoff and Stripe's 24-hour boundary.
CHECKOUT_RECOVERY_TRANSPORT_SAFETY = timedelta(minutes=5)
EXPECTED_PRICE_CURRENCY = "usd"
EXPECTED_PRICE_UNIT_AMOUNT = 8_900
EXPECTED_PRICE_INTERVAL = "month"
CHECKOUT_PAYMENT_METHOD_COLLECTION = "always"
# Changing Checkout request parameters while reusing a Stripe idempotency key
# permanently conflicts with any pre-change request retained by Stripe. Keep an
# explicit policy generation in the key so a successful old request whose local
# cache write was interrupted cannot block the card-required rollout.
PAID_PILOT_CHECKOUT_POLICY_VERSION = "paid-pilot-v3"
LEGACY_CHECKOUT_IDEMPOTENCY_POLICY_VERSION = "card-required-v1"
# The active writer and its idempotency key share one immutable generation.
# This prevents a retry from reusing a legacy key with a different request.
CHECKOUT_IDEMPOTENCY_POLICY_VERSION = PAID_PILOT_CHECKOUT_POLICY_VERSION
CHECKOUT_TRIAL_CLOCK_POLICY = "checkout-fixed-14-day-v2"
LEGACY_TRIAL_CLOCK_POLICY = "legacy-baa-carryover-v1"
CURRENT_STRIPE_ENTITLEMENT_CONTRACT_VERSION = PUBLIC_ENTITLEMENT_CONTRACT_VERSION
LEGACY_UNVERSIONED_STRIPE_CONTRACT = "stripe-legacy-unversioned.v0"
SUPPORTED_STRIPE_ENTITLEMENT_CONTRACT_VERSIONS = frozenset(
    {CURRENT_STRIPE_ENTITLEMENT_CONTRACT_VERSION}
)
SUPPORTED_STRIPE_CONTRACT_PROVENANCE = (
    SUPPORTED_STRIPE_ENTITLEMENT_CONTRACT_VERSIONS
    | {LEGACY_UNVERSIONED_STRIPE_CONTRACT}
)
MAX_STATE_APPLY_ATTEMPTS = 3
REMOTE_QUANTITY_OUT_OF_RANGE_ERROR = "remote_seat_quantity_out_of_range"
LOCAL_SEAT_COUNT_OUT_OF_RANGE_ERROR = (
    "active clinician seat count exceeds public pilot maximum"
)
LOCAL_SEAT_LEDGER_INVALID_ERROR = "active_seat_ledger_invalid"

SUBSCRIPTION_EVENT_TYPES = {
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "customer.subscription.paused",
    "customer.subscription.resumed",
}
INVOICE_SUCCESS_EVENT_TYPES = {
    "invoice.paid",
    "invoice.payment_succeeded",
}
INVOICE_EVENT_TYPES = INVOICE_SUCCESS_EVENT_TYPES | {"invoice.payment_failed"}
MIRRORED_EVENT_TYPES = SUBSCRIPTION_EVENT_TYPES | INVOICE_EVENT_TYPES
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


class StripeCoreBindingInvalid(Exception):
    """Core has a partial or malformed Stripe subscription binding."""


class StripeSeatLimitExceeded(StripeStateInvalid):
    """Core's authoritative active-clinician count is outside pilot bounds."""


class StripeSeatLedgerInvalid(StripeStateInvalid):
    """Core cannot safely derive the authoritative active-clinician count."""


class StripeManagedBilling(StripeStateInvalid):
    """The public paid-pilot Checkout is unavailable to a managed practice."""


class StripeSubscriptionContractInvalid(StripeStateInvalid):
    """A fetched subscription is authentic enough to revoke, but not to trust."""

    def __init__(self, subscription: dict[str, Any], reason: StripeStateInvalid):
        self.subscription = subscription
        self.reason = reason
        super().__init__(str(reason))


class StripeTrialCarryoverDeferred(Exception):
    """Legacy API compatibility for callers that handled the former dead zone."""

    def __init__(self, available_at: datetime):
        self.available_at = available_at
        super().__init__(
            "Checkout is deferred until the platform trial ends so no remaining "
            "trial time is shortened."
        )


class StripeTrialClockReconciliationRequired(Exception):
    """A prior paid-pilot trial cannot be replaced by a fresh trial."""

    def __init__(self, trial_ends_at: datetime):
        self.trial_ends_at = trial_ends_at
        super().__init__("the fixed paid-pilot trial clock cannot be reset or extended")


class StripeCheckoutAlreadyCompleted(Exception):
    """A delayed/lost webhook was recovered from the durable session id."""


class StripeCheckoutRetirementPending(Exception):
    """An expired legacy Checkout still has a nonterminal subscription."""


class StripeCheckoutReconciliationRequired(Exception):
    """A frozen Checkout request is no longer safe to replay automatically."""


def _public_checkout_is_managed(
    practice: Practice, *, at: datetime | None = None
) -> bool:
    """Return whether a practice is outside public self-service billing."""

    return bool(
        practice.negotiated_baa_required
        or practice.organization_id is not None
        or practice.billing_exemption_active(at=at)
    )


def _emit_billing_metric(
    metric: str,
    *,
    outcome: str,
    practice_id: object | None = None,
) -> None:
    """Emit a PHI-free, allowlisted billing lifecycle signal."""

    fields: dict[str, object] = {"metric": metric, "outcome": outcome}
    if practice_id is not None:
        fields["practice_ref"] = provider_log_ref(f"practice:{practice_id}")
    log.info("billing lifecycle metric", **fields)


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
    if (
        not value
        or value != value.strip()
        or "\\" in value
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise StripeStateInvalid("Stripe hosted URL has an unexpected origin")
    try:
        parsed = urlparse(value)
    except ValueError as exc:
        raise StripeStateInvalid("Stripe hosted URL has an unexpected origin") from exc
    # Compare the exact authority, not only urllib's derived hostname. Browsers
    # follow WHATWG parsing rules where a backslash can act as a slash; exact
    # authority also rejects credentials, ports, and trailing dots.
    if (
        parsed.scheme != "https"
        or parsed.netloc != hostname
        or parsed.hostname != hostname
    ):
        raise StripeStateInvalid("Stripe hosted URL has an unexpected origin")
    return value


def _stripe_timestamp(value: object, *, source: str) -> datetime:
    """Return one bounded, timezone-aware timestamp from a Stripe response."""

    if not isinstance(value, int) or isinstance(value, bool):
        raise StripeStateInvalid(f"{source} is invalid")
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc)
    except (OSError, OverflowError, ValueError) as exc:
        raise StripeStateInvalid(f"{source} is out of range") from exc


def _subscription_item(
    subscription: dict[str, Any], *, enforce_quantity_bounds: bool = True
) -> dict[str, Any]:
    items_container = subscription.get("items")
    if not isinstance(items_container, dict):
        raise StripeStateInvalid("subscription items container is invalid")
    items = items_container.get("data")
    if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
        raise StripeStateInvalid("subscription must contain exactly one price item")
    item = cast(dict[str, Any], items[0])
    price = item.get("price")
    price_id = price.get("id") if isinstance(price, dict) else price
    if price_id != stripe_config.STRIPE_PRICE_ID:
        raise StripeStateInvalid("subscription does not use the configured price")
    quantity = item.get("quantity")
    if (
        not isinstance(item.get("id"), str)
        or not isinstance(quantity, int)
        or isinstance(quantity, bool)
    ):
        raise StripeStateInvalid("subscription item is missing id or quantity")
    if enforce_quantity_bounds and not 1 <= quantity <= MAX_ACTIVE_CLINICIAN_SEATS:
        raise StripeSeatLimitExceeded(
            "subscription quantity is outside the 1-20 paid-pilot limit"
        )
    return item


def _subscription_trial_clock_policy(subscription: dict[str, Any]) -> str | None:
    """Validate reserved billing metadata and classify legacy subscriptions."""

    metadata = subscription.get("metadata")
    if metadata is None:
        return None
    if not isinstance(metadata, dict):
        raise StripeStateInvalid("subscription metadata is invalid")

    policy = metadata.get("checkout_policy")
    reserved_keys = {"entitlement_contract", "trial_clock", "trial_days"}
    if policy is None:
        if any(key in metadata for key in reserved_keys):
            raise StripeStateInvalid("subscription policy metadata is incomplete")
        return None
    if policy != PAID_PILOT_CHECKOUT_POLICY_VERSION:
        raise StripeStateInvalid("subscription checkout policy is unknown")
    contract_version = metadata.get("entitlement_contract")
    if (
        not isinstance(contract_version, str)
        or contract_version not in SUPPORTED_STRIPE_ENTITLEMENT_CONTRACT_VERSIONS
    ):
        raise StripeStateInvalid("subscription entitlement contract is invalid")

    trial_clock = metadata.get("trial_clock")
    if trial_clock == CHECKOUT_TRIAL_CLOCK_POLICY:
        if metadata.get("trial_days") != str(TRIAL_LENGTH.days):
            raise StripeStateInvalid("subscription trial policy is invalid")
    elif trial_clock == LEGACY_TRIAL_CLOCK_POLICY:
        if "trial_days" in metadata:
            raise StripeStateInvalid("legacy subscription has new-trial metadata")
    else:
        raise StripeStateInvalid("subscription trial clock policy is unknown")
    return cast(str, trial_clock)


def _trial_end_from_subscription(subscription: dict[str, Any]) -> datetime | None:
    """Validate and return Stripe's authoritative paid-pilot trial end."""

    trial_start = subscription.get("trial_start")
    trial_end = subscription.get("trial_end")
    trial_clock_policy = _subscription_trial_clock_policy(subscription)
    checkout_clock = trial_clock_policy == CHECKOUT_TRIAL_CLOCK_POLICY
    if trial_start is None and trial_end is None:
        if subscription.get("status") == "trialing" or checkout_clock:
            raise StripeStateInvalid("subscription is missing its required trial clock")
        return None
    if (
        not isinstance(trial_start, int)
        or isinstance(trial_start, bool)
        or not isinstance(trial_end, int)
        or isinstance(trial_end, bool)
        or trial_end <= trial_start
    ):
        raise StripeStateInvalid("subscription trial clock is invalid")
    if checkout_clock and trial_end - trial_start != int(TRIAL_LENGTH.total_seconds()):
        raise StripeStateInvalid("subscription trial is not the fixed 14 days")
    return _stripe_timestamp(trial_end, source="subscription trial clock")


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
    metadata = subscription.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        raise StripeStateInvalid("subscription metadata is invalid")
    _subscription_item(subscription)
    _trial_end_from_subscription(subscription)
    pause_collection = subscription.get("pause_collection")
    if pause_collection is not None and not isinstance(pause_collection, dict):
        raise StripeStateInvalid("subscription pause_collection is invalid")
    return subscription


def _validate_subscription_identity(
    subscription: dict[str, Any], *, expected_id: str
) -> dict[str, Any]:
    """Validate stable identity fields without requiring the current offer."""

    if subscription.get("id") != expected_id:
        raise StripeStateInvalid(
            "Stripe returned a different subscription than requested"
        )
    customer_id = subscription.get("customer")
    if (
        not isinstance(customer_id, str)
        or not customer_id.startswith("cus_")
        or len(customer_id) <= len("cus_")
    ):
        raise StripeStateInvalid("subscription customer id is invalid")
    _validate_livemode(subscription.get("livemode"), source="subscription")
    metadata = subscription.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        raise StripeStateInvalid("subscription metadata is invalid")
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
    allow_bound_terminal_identity: bool = False,
) -> dict[str, Any]:
    used_deleted_fallback = False
    try:
        subscription = _request("GET", f"/subscriptions/{subscription_id}")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404 or deleted_fallback is None:
            raise
        subscription = deleted_fallback
        used_deleted_fallback = True
    try:
        if used_deleted_fallback and subscription.get("status") != "canceled":
            # A 404 can make the signed deletion payload the only remaining
            # authoritative object, but the event name cannot override an
            # internally inconsistent entitled status.
            raise StripeStateInvalid("deleted subscription fallback is not canceled")
        if (
            allow_bound_terminal_identity
            and subscription.get("status") in TERMINAL_SUBSCRIPTION_STATUSES
        ):
            return _validate_subscription_identity(
                subscription,
                expected_id=subscription_id,
            )
        validated = _validate_subscription(subscription)
        if validated["id"] != subscription_id:
            raise StripeStateInvalid(
                "Stripe returned a different subscription than requested"
            )
        return validated
    except StripeStateInvalid as exc:
        raise StripeSubscriptionContractInvalid(subscription, exc) from exc


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

        repair_target: int | None = None
        local_seat_error: StripeStateInvalid | None = None
        if isinstance(invalid.reason, StripeSeatLimitExceeded):
            try:
                repair_target = _authorized_member_count(session, row)
            except (StripeSeatLimitExceeded, StripeSeatLedgerInvalid) as exc:
                local_seat_error = exc
                repair_target = None
        row.subscription_status = "invalid"
        row.stripe_state_version += 1
        if repair_target is not None:
            if (
                not row.stripe_quantity_sync_required
                or row.stripe_quantity_sync_target != repair_target
            ):
                row.stripe_quantity_sync_version += 1
            row.stripe_quantity_sync_required = True
            row.stripe_quantity_sync_target = repair_target
            row.stripe_quantity_sync_error = REMOTE_QUANTITY_OUT_OF_RANGE_ERROR
        elif local_seat_error is not None:
            row.stripe_quantity_sync_required = True
            row.stripe_quantity_sync_target = None
            if not row.stripe_quantity_sync_error:
                row.stripe_quantity_sync_error = (
                    LOCAL_SEAT_COUNT_OUT_OF_RANGE_ERROR
                    if isinstance(local_seat_error, StripeSeatLimitExceeded)
                    else LOCAL_SEAT_LEDGER_INVALID_ERROR
                )
        else:
            row.stripe_quantity_sync_required = False
            row.stripe_quantity_sync_target = None
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
        practice_ref=provider_log_ref(f"practice:{practice.id}"),
        subscription_ref=provider_log_ref(subscription_id),
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
            practice_ref=provider_log_ref(f"practice:{practice.id}"),
            subscription_ref=provider_log_ref(subscription["id"]),
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
    trusted_checkout_session_id: str | None = None,
    invoice_customer_id: str | None = None,
    allow_paid_invoice_bind: bool = False,
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
                select(Practice).where(Practice.id == practice.id).with_for_update()
            ).one()
            invoice_can_attempt_bind = bool(
                allow_paid_invoice_bind
                and event_type in INVOICE_SUCCESS_EVENT_TYPES
                and invoice_customer_id is not None
                and snapshot.stripe_customer_id == invoice_customer_id
                and (
                    snapshot.stripe_subscription_id is None
                    or snapshot.stripe_subscription_id == subscription_id
                    or snapshot.subscription_status in TERMINAL_SUBSCRIPTION_STATUSES
                )
            )
            if (
                event_id is not None
                and snapshot.stripe_subscription_id not in (None, subscription_id)
                and not clear_checkout
                and not invoice_can_attempt_bind
            ):
                if event_created_at is None or event_type is None:
                    raise StripeStateInvalid(
                        "Stripe event receipt metadata is incomplete"
                    )
                session.add(
                    _event_receipt(
                        event_id=event_id,
                        event_type=event_type,
                        event_created_at=event_created_at,
                        practice_id=snapshot.id,
                    )
                )
                session.commit()
                return None, False
            if (
                event_created_at is not None
                and snapshot.stripe_subscription_id == subscription_id
                and snapshot.stripe_last_event_created_at is not None
                and event_created_at < snapshot.stripe_last_event_created_at
            ):
                assert event_id and event_type
                session.add(
                    _event_receipt(
                        event_id=event_id,
                        event_type=event_type,
                        event_created_at=event_created_at,
                        practice_id=snapshot.id,
                    )
                )
                session.commit()
                return None, False
            snapshot_version = snapshot.stripe_state_version
            snapshot_is_exact_bound = snapshot.stripe_subscription_id == subscription_id
            session.commit()

        try:
            subscription = _retrieve_subscription(
                subscription_id,
                deleted_fallback=deleted_fallback,
                allow_bound_terminal_identity=snapshot_is_exact_bound,
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

            same_subscription = row.stripe_subscription_id == subscription["id"]
            metadata_matches = (subscription.get("metadata") or {}).get(
                "practice_id"
            ) == str(row.id)
            trusted_paid_invoice = bool(
                not same_subscription
                and allow_paid_invoice_bind
                and event_type in INVOICE_SUCCESS_EVENT_TYPES
                and invoice_customer_id is not None
                and subscription["customer"] == invoice_customer_id
                and row.stripe_customer_id == invoice_customer_id
                and (
                    row.stripe_subscription_id is None
                    or row.subscription_status in TERMINAL_SUBSCRIPTION_STATUSES
                )
                and metadata_matches
            )
            if not same_subscription and _public_checkout_is_managed(row):
                raise StripeManagedBilling
            trusted_pending_checkout = bool(
                trusted_checkout_session_id is not None
                and row.stripe_checkout_session_id == trusted_checkout_session_id
            )
            if (
                clear_checkout
                and (
                    row.stripe_checkout_request_json is not None
                    or row.stripe_checkout_session_id is not None
                )
                and not trusted_pending_checkout
            ):
                raise StripeStateConflict(
                    "Checkout completion does not match the pending session"
                )
            if (
                not same_subscription
                and (
                    row.stripe_checkout_request_json is not None
                    or row.stripe_checkout_session_id is not None
                )
                and not trusted_pending_checkout
            ):
                raise StripeStateConflict(
                    "an unrelated subscription arrived while Checkout was pending"
                )
            if (
                not same_subscription
                and not trusted_pending_checkout
                and not trusted_paid_invoice
            ):
                # Mutable subscription metadata is only a routing hint.  An
                # initial binding requires the immutable Checkout Session id
                # that Core persisted before Stripe created the subscription,
                # or a paid Invoice whose customer was already persisted for
                # this practice and whose fresh subscription metadata matches.
                raise StripeStateConflict(
                    "initial subscription binding requires exact Checkout completion"
                )

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
            if (
                invoice_customer_id is not None
                and subscription["customer"] != invoice_customer_id
            ):
                invoice_customer_error = StripeStateInvalid(
                    "invoice customer does not match its Stripe subscription"
                )
                if same_subscription:
                    contract_error = invoice_customer_error
                else:
                    raise invoice_customer_error
            mirrored_status = _mirrored_subscription_status(subscription)
            metadata = subscription.get("metadata")
            expected_entitlement_contract = (
                row.stripe_subscription_entitlement_contract
                if same_subscription
                else row.stripe_checkout_entitlement_contract
            )
            if trusted_paid_invoice and expected_entitlement_contract is None:
                # A paid Invoice is recovery evidence, not a second product
                # definition. Only the current source-controlled contract can
                # establish a binding when no frozen Checkout contract remains.
                expected_entitlement_contract = (
                    CURRENT_STRIPE_ENTITLEMENT_CONTRACT_VERSION
                )
            trusted_bound_terminal = bool(
                same_subscription and mirrored_status in TERMINAL_SUBSCRIPTION_STATUSES
            )
            if trusted_bound_terminal:
                # The immutable Core binding plus Stripe account/livemode and
                # customer identity are sufficient to revoke access. Do not
                # strand a canceled customer because its historic price or
                # mutable metadata no longer matches the current offer.
                trial_clock_policy = row.stripe_trial_clock_policy
                remote_entitlement_contract = expected_entitlement_contract
                mirrored_trial_end = None
            else:
                trial_clock_policy = _subscription_trial_clock_policy(subscription)
                remote_entitlement_contract = (
                    LEGACY_UNVERSIONED_STRIPE_CONTRACT
                    if trial_clock_policy is None
                    else cast(dict[str, Any], metadata).get("entitlement_contract")
                )
                mirrored_trial_end = _trial_end_from_subscription(subscription)
            legacy_cached_checkout = bool(
                not same_subscription
                and row.acquisition_source
                != PracticeAcquisitionSource.public_self_service.value
                and trusted_pending_checkout
                and row.stripe_checkout_request_json is None
                and expected_entitlement_contract is None
                and remote_entitlement_contract == LEGACY_UNVERSIONED_STRIPE_CONTRACT
                and metadata == {"practice_id": str(row.id)}
            )
            if legacy_cached_checkout:
                # The pre-contract writer persisted only the exact Session id
                # and practice metadata. Keep that drained, non-public shape
                # readable without teaching the old writer new provenance.
                expected_entitlement_contract = LEGACY_UNVERSIONED_STRIPE_CONTRACT
            provenance_error: StripeStateInvalid | None = None
            if (
                expected_entitlement_contract
                not in SUPPORTED_STRIPE_CONTRACT_PROVENANCE
            ):
                provenance_error = StripeStateInvalid(
                    "Core subscription entitlement contract provenance is missing"
                )
            elif remote_entitlement_contract != expected_entitlement_contract:
                provenance_error = StripeStateInvalid(
                    "subscription entitlement contract cannot change"
                )
            elif (
                not same_subscription
                and expected_entitlement_contract == LEGACY_UNVERSIONED_STRIPE_CONTRACT
                and metadata != {"practice_id": str(row.id)}
            ):
                # The pre-contract Checkout writer put exactly one metadata
                # key on the resulting subscription.  Only an exact cached
                # legacy Session plus that exact shape can establish an
                # initial legacy binding; a bound id remains authoritative for
                # later terminal events even if its mutable metadata drifts.
                provenance_error = StripeStateInvalid(
                    "legacy subscription metadata does not match Checkout provenance"
                )
            if provenance_error is not None:
                if same_subscription:
                    contract_error = provenance_error
                else:
                    raise provenance_error
            if (
                remote_entitlement_contract == LEGACY_UNVERSIONED_STRIPE_CONTRACT
                and expected_entitlement_contract == LEGACY_UNVERSIONED_STRIPE_CONTRACT
            ):
                # The pre-contract release wrote only the
                # practice id onto subscriptions. Trust that exact legacy shape
                # only when it is already bound or when the completing Session
                # is the immutable id cached locally before the rollout drain.
                trial_clock_policy = LEGACY_TRIAL_CLOCK_POLICY
            if (
                require_practice_metadata
                and not metadata_matches
                and not trusted_bound_terminal
            ):
                if row.stripe_subscription_id == subscription["id"]:
                    contract_error = StripeStateInvalid(
                        "bound subscription practice metadata is missing"
                    )
                else:
                    raise StripeStateInvalid(
                        "subscription practice metadata is missing"
                    )
            if (
                require_practice_metadata
                and row.stripe_subscription_id != subscription["id"]
                and mirrored_status not in TERMINAL_SUBSCRIPTION_STATUSES
                and not legacy_cached_checkout
                and trial_clock_policy
                != (
                    row.stripe_trial_clock_policy
                    or (
                        CHECKOUT_TRIAL_CLOCK_POLICY
                        if row.trial_ends_at is None
                        else LEGACY_TRIAL_CLOCK_POLICY
                    )
                )
            ):
                raise StripeStateInvalid(
                    "new subscription trial policy does not match Core state"
                )
            if (
                same_subscription
                and not metadata_matches
                and mirrored_status in ENTITLED_SUBSCRIPTION_STATUSES
            ):
                contract_error = StripeStateInvalid(
                    "bound subscription metadata cannot grant entitlement"
                )
            if (
                same_subscription
                and row.stripe_trial_clock_policy is not None
                and trial_clock_policy != row.stripe_trial_clock_policy
            ):
                contract_error = StripeStateInvalid(
                    "bound subscription trial policy cannot change"
                )
            if (
                same_subscription
                and row.stripe_trial_clock_policy is None
                and trial_clock_policy == CHECKOUT_TRIAL_CLOCK_POLICY
            ):
                # No deployed legacy release could have created this policy.
                # Refuse to infer paid-pilot provenance for an already-bound
                # row because mutable Stripe metadata cannot establish it.
                contract_error = StripeStateInvalid(
                    "bound paid-pilot trial policy has no durable provenance"
                )
            if (
                not trusted_bound_terminal
                and row.stripe_trial_clock_policy == CHECKOUT_TRIAL_CLOCK_POLICY
                and trial_clock_policy == CHECKOUT_TRIAL_CLOCK_POLICY
                and row.trial_ends_at is not None
                and mirrored_trial_end != row.trial_ends_at
            ):
                clock_error = StripeStateInvalid(
                    "bound paid-pilot trial clock cannot be reset or extended"
                )
                if same_subscription:
                    contract_error = clock_error
                else:
                    raise clock_error

            target: int | None = None
            if mirrored_status not in TERMINAL_SUBSCRIPTION_STATUSES:
                try:
                    target = _authorized_member_count(session, row)
                except (StripeSeatLimitExceeded, StripeSeatLedgerInvalid) as exc:
                    # Membership mutation paths own prevention. Billing still
                    # fails closed if drift or an older path violates the seat
                    # invariant, without leaving a stale active entitlement.
                    contract_error = exc

            if contract_error is not None:
                row.subscription_status = "invalid"
                row.stripe_state_version += 1
                if isinstance(
                    contract_error,
                    (StripeSeatLimitExceeded, StripeSeatLedgerInvalid),
                ):
                    row.stripe_quantity_sync_required = True
                    row.stripe_quantity_sync_target = None
                    if not row.stripe_quantity_sync_error:
                        row.stripe_quantity_sync_error = (
                            LOCAL_SEAT_COUNT_OUT_OF_RANGE_ERROR
                            if isinstance(contract_error, StripeSeatLimitExceeded)
                            else LOCAL_SEAT_LEDGER_INVALID_ERROR
                        )
                else:
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
                    "bound Stripe subscription contract invalid; access revoked",
                    practice_ref=provider_log_ref(f"practice:{practice.id}"),
                    subscription_ref=provider_log_ref(subscription_id),
                    error=str(contract_error),
                )
                return subscription, True

            row.stripe_customer_id = subscription["customer"]
            row.stripe_subscription_id = subscription["id"]
            row.stripe_subscription_entitlement_contract = cast(
                str, remote_entitlement_contract
            )
            row.subscription_status = mirrored_status
            if not same_subscription:
                row.stripe_trial_clock_policy = trial_clock_policy
            elif (
                row.stripe_trial_clock_policy is None
                and trial_clock_policy == LEGACY_TRIAL_CLOCK_POLICY
            ):
                # This additive marker may be adopted for the legacy policy;
                # unlike the new clock, it cannot grant a fresh trial.
                row.stripe_trial_clock_policy = trial_clock_policy
            if mirrored_trial_end is not None:
                # This value comes only from a freshly retrieved, validated
                # Stripe subscription. A success URL or browser assertion can
                # never create or extend the local trial clock.
                row.trial_ends_at = mirrored_trial_end
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
            if clear_checkout and trusted_pending_checkout:
                _clear_checkout_session(row, advance=True)

            quantity_sync_required = False
            if target is not None:
                item = _subscription_item(subscription)
                quantity_sync_required = item["quantity"] != target
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
    # The historical function name remains part of the billing service API,
    # but an explicitly roster-authorized organization member is a real seat
    # even though we correctly leave their personal ``attested_at`` null.
    with get_session() as session:
        return _authorized_member_count(session, practice)


def _authorized_member_count(session: Session, practice: Practice) -> int:
    """Consume Core's canonical active-seat policy and enforce pilot bounds."""

    try:
        count = active_clinician_seat_count(session, practice.id)
    except ClinicianSeatPolicyError as exc:
        raise StripeSeatLedgerInvalid(
            "authoritative active-clinician ledger is invalid"
        ) from exc
    if not 1 <= count <= MAX_ACTIVE_CLINICIAN_SEATS:
        raise StripeSeatLimitExceeded(
            "authoritative active-clinician count is outside the 1-20 pilot limit"
        )
    return count


def checkout_deferred_until(
    practice: Practice, *, now: datetime | None = None
) -> datetime | None:
    """Compatibility signal retained after removal of the Checkout dead zone.

    This field used to expose when Checkout would become available. Checkout
    is now always available when billing is configured, so existing API clients
    continue to receive the field as ``null``.
    """

    del practice, now
    return None


def checkout_trial_end(
    practice: Practice, *, now: datetime | None = None
) -> datetime | None:
    """Return a safe end for a legacy pre-Checkout platform trial.

    New paid-pilot trials use an exact 14-day duration that starts when card
    collection completes and Stripe creates the subscription. Older rows may
    have a BAA-era trial clock; preserve rather than silently reset that promise.
    Stripe requires an absolute legacy ``trial_end`` at least 48 hours ahead,
    so a shorter live legacy window is extended only to that safe minimum.
    """

    current = now or datetime.now(timezone.utc)
    platform_trial_end = practice.trial_ends_at
    if not platform_trial_end or platform_trial_end <= current:
        return None
    minimum_safe_end = (
        current + MINIMUM_CHECKOUT_TRIAL_LEAD + CHECKOUT_TRIAL_LEAD_SAFETY
    )
    return max(platform_trial_end, minimum_safe_end)


def _checkout_trial_policy_and_end(
    practice: Practice, *, now: datetime
) -> tuple[str, datetime | None]:
    """Resolve an exact new 14-day duration or a preserved legacy clock."""

    policy = practice.stripe_trial_clock_policy
    if policy not in {
        None,
        CHECKOUT_TRIAL_CLOCK_POLICY,
        LEGACY_TRIAL_CLOCK_POLICY,
    }:
        raise StripeStateInvalid("stored Checkout trial policy is invalid")
    if policy is None:
        policy = (
            CHECKOUT_TRIAL_CLOCK_POLICY
            if practice.trial_ends_at is None
            else LEGACY_TRIAL_CLOCK_POLICY
        )
    if policy == LEGACY_TRIAL_CLOCK_POLICY:
        return policy, checkout_trial_end(practice, now=now)

    if practice.trial_ends_at is None:
        # Stripe starts the exact 14-day duration when Checkout creates the
        # subscription. Keeping Core's clock empty until the authoritative
        # subscription read prevents an abandoned Session from consuming time.
        return policy, None
    trial_end = practice.trial_ends_at
    if trial_end.tzinfo is None:
        raise StripeStateInvalid("stored paid-pilot trial clock is invalid")
    trial_end = trial_end.astimezone(timezone.utc)
    # A fixed paid-pilot clock proves this practice already received its trial.
    # Starting another 14-day duration would silently grant a second trial and
    # invent the unresolved post-trial conversion policy.
    raise StripeTrialClockReconciliationRequired(trial_end)


def has_nonterminal_subscription(practice: Practice) -> bool:
    return bool(
        practice.stripe_subscription_id
        and practice.subscription_status not in TERMINAL_SUBSCRIPTION_STATUSES
    )


def _has_complete_core_subscription_binding(practice: Practice) -> bool:
    """Reject manual or partial customer rows before privileged Stripe calls.

    Public self-service uses the entitlement receiver's canonical binding
    check. Pre-receiver managed rows may legitimately start at version zero;
    their exact customer/subscription identity is still verified remotely
    before use.
    """

    if (
        practice.acquisition_source
        == PracticeAcquisitionSource.public_self_service.value
    ):
        return practice.has_authoritative_stripe_subscription_binding()

    subscription_id = practice.stripe_subscription_id
    customer_id = practice.stripe_customer_id
    state_version = practice.stripe_state_version
    return bool(
        isinstance(subscription_id, str)
        and subscription_id.startswith("sub_")
        and len(subscription_id) > len("sub_")
        and subscription_id.strip() == subscription_id
        and isinstance(customer_id, str)
        and customer_id.startswith("cus_")
        and len(customer_id) > len("cus_")
        and customer_id.strip() == customer_id
        and isinstance(state_version, int)
        and not isinstance(state_version, bool)
        and state_version >= 0
    )


def _has_core_subscription_projection(practice: Practice) -> bool:
    return bool(
        practice.stripe_customer_id is not None
        or practice.stripe_subscription_id is not None
        or practice.subscription_status is not None
    )


def _core_subscription_binding(practice: Practice) -> tuple[str, str, int] | None:
    if not _has_complete_core_subscription_binding(practice):
        return None
    return (
        cast(str, practice.stripe_subscription_id),
        cast(str, practice.stripe_customer_id),
        practice.stripe_state_version,
    )


def _require_core_subscription_binding(
    practice: Practice,
) -> tuple[str, str, int]:
    binding = _core_subscription_binding(practice)
    if binding is None:
        raise StripeCoreBindingInvalid(
            "Core Stripe customer has no authoritative subscription binding"
        )
    return binding


def _retrieve_subscription_identity(subscription_id: str) -> dict[str, Any]:
    """Authenticate only a bound subscription's stable Stripe identity."""

    subscription = _request("GET", f"/subscriptions/{subscription_id}")
    if subscription.get("id") != subscription_id:
        raise StripeStateInvalid("Stripe returned the wrong subscription")
    customer_id = subscription.get("customer")
    if (
        not isinstance(customer_id, str)
        or not customer_id.startswith("cus_")
        or len(customer_id) <= len("cus_")
    ):
        raise StripeStateInvalid("subscription customer id is invalid")
    _validate_livemode(subscription.get("livemode"), source="subscription")
    metadata = subscription.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        raise StripeStateInvalid("subscription metadata is invalid")
    return subscription


def _verified_bound_customer(
    practice: Practice,
    *,
    require_terminal_subscription: bool,
    expected_binding: tuple[str, str, int] | None = None,
) -> tuple[tuple[str, str, int], object]:
    """Verify Core's complete binding against Stripe before customer reuse."""

    binding = expected_binding or _require_core_subscription_binding(practice)
    subscription_id, customer_id, _ = binding
    subscription = _retrieve_subscription_identity(subscription_id)
    if subscription["customer"] != customer_id:
        raise StripeStateInvalid("Stripe customer binding changed")
    metadata_practice_id = (subscription.get("metadata") or {}).get("practice_id")
    if metadata_practice_id != str(practice.id):
        # Exact subscription/customer identity is authoritative here. Mutable
        # metadata drift is observable but cannot redirect customer access.
        log.error(
            "stripe subscription metadata drift",
            practice_ref=provider_log_ref(f"practice:{practice.id}"),
            subscription_ref=provider_log_ref(subscription_id),
            metadata_matches=False,
        )
    if (
        require_terminal_subscription
        and subscription.get("status") not in TERMINAL_SUBSCRIPTION_STATUSES
    ):
        raise StripeCheckoutAlreadyCompleted
    return binding, subscription.get("status")


def has_resumable_checkout(practice: Practice) -> bool:
    # A normal subscription-mode Checkout can remain open before Stripe has
    # created or reported a subscription. The durable session id is enough to
    # offer Resume; `_recover_checkout_session` still retrieves Stripe and
    # validates status, livemode, practice binding, expiry, and any eventual
    # subscription binding before returning the hosted URL.
    return bool(
        practice.stripe_checkout_session_id or practice.stripe_checkout_request_json
    )


def refresh_subscription_state(practice: Practice) -> dict[str, Any] | None:
    """Refresh an existing subscription before deciding whether Checkout is safe."""

    if _has_core_subscription_projection(practice):
        _require_core_subscription_binding(practice)
    if not practice.stripe_subscription_id:
        return None
    subscription, _ = _apply_authoritative_subscription(
        practice, practice.stripe_subscription_id
    )
    return subscription


def _checkout_idempotency_key(practice: Practice, *, attempt: int | None = None) -> str:
    checkout_attempt = practice.stripe_checkout_attempt if attempt is None else attempt
    raw = (
        f"cosmas-checkout:{CHECKOUT_IDEMPOTENCY_POLICY_VERSION}:"
        f"{practice.id}:{checkout_attempt}"
    )
    return f"cosmas-checkout-{hashlib.sha256(raw.encode()).hexdigest()}"


def _checkout_expire_idempotency_key(session_id: str) -> str:
    raw = f"cosmas-checkout-expire:{session_id}"
    return f"cosmas-checkout-expire-{hashlib.sha256(raw.encode()).hexdigest()}"


def _checkout_request_replay_deadline(
    frozen_data: dict[str, str], *, created_at: datetime
) -> datetime:
    if created_at.tzinfo is None:
        raise StripeStateInvalid("stored Checkout request timestamp is invalid")
    deadline = created_at.astimezone(timezone.utc) + CHECKOUT_REQUEST_REPLAY_WINDOW
    raw_trial_end = frozen_data.get("subscription_data[trial_end]")
    if raw_trial_end is not None:
        try:
            trial_end_value = int(raw_trial_end)
        except ValueError as exc:
            raise StripeStateInvalid("stored Checkout trial end is invalid") from exc
        trial_end = _stripe_timestamp(
            trial_end_value, source="stored Checkout trial end"
        )
        deadline = min(deadline, trial_end - MINIMUM_CHECKOUT_TRIAL_LEAD)
    return deadline


def _checkout_redirect_is_safe(value: str, *, outcome: str) -> bool:
    if (
        not value
        or value != value.strip()
        or "\\" in value
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        return False
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    allowed_authorities = {
        host.strip().lower()
        for host in config("ALLOWED_HOST_LIST", default="", cast=str).split(",")
        if host.strip()
    }
    allowed_queries = {
        "",
        f"billing={outcome}",
        f"billing={outcome}&returnTo=%2Fonboarding",
    }
    return bool(
        parsed.scheme == "https"
        and parsed.netloc.lower() in allowed_authorities
        and parsed.hostname == parsed.netloc
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
        and parsed.query in allowed_queries
    )


def _validate_frozen_checkout_request(
    frozen_data: dict[str, str],
    *,
    practice: Practice,
    trial_clock_policy: str,
    request_created_at: datetime,
) -> None:
    """Validate the exact provider-bound commercial request on every replay."""

    if frozen_data.get("line_items[0][price]") != stripe_config.STRIPE_PRICE_ID:
        raise StripeCheckoutReconciliationRequired
    raw_quantity = frozen_data.get("line_items[0][quantity]")
    if raw_quantity is None:
        raise StripeStateInvalid("stored Checkout quantity is invalid")
    try:
        quantity = int(raw_quantity)
    except ValueError as exc:
        raise StripeStateInvalid("stored Checkout quantity is invalid") from exc
    if str(quantity) != raw_quantity or not 1 <= quantity <= MAX_ACTIVE_CLINICIAN_SEATS:
        raise StripeStateInvalid("stored Checkout quantity is outside pilot bounds")

    entitlement_contract = frozen_data.get("metadata[entitlement_contract]")
    if (
        not isinstance(entitlement_contract, str)
        or entitlement_contract not in SUPPORTED_STRIPE_ENTITLEMENT_CONTRACT_VERSIONS
    ):
        raise StripeStateInvalid("stored Checkout entitlement contract is invalid")
    if practice.stripe_checkout_entitlement_contract != entitlement_contract:
        raise StripeStateInvalid("stored Checkout entitlement contract changed")

    expected: dict[str, str] = {
        "mode": "subscription",
        "line_items[0][price]": stripe_config.STRIPE_PRICE_ID,
        "line_items[0][quantity]": raw_quantity,
        "client_reference_id": str(practice.id),
        "success_url": frozen_data.get("success_url", ""),
        "cancel_url": frozen_data.get("cancel_url", ""),
        "payment_method_collection": CHECKOUT_PAYMENT_METHOD_COLLECTION,
        "metadata[checkout_policy]": PAID_PILOT_CHECKOUT_POLICY_VERSION,
        "metadata[checkout_attempt]": str(practice.stripe_checkout_attempt),
        "metadata[practice_id]": str(practice.id),
        "metadata[entitlement_contract]": entitlement_contract,
        "metadata[trial_clock]": trial_clock_policy,
        "subscription_data[metadata][practice_id]": str(practice.id),
        "subscription_data[metadata][checkout_policy]": (
            PAID_PILOT_CHECKOUT_POLICY_VERSION
        ),
        "subscription_data[metadata][entitlement_contract]": entitlement_contract,
        "subscription_data[metadata][trial_clock]": trial_clock_policy,
    }
    if not _checkout_redirect_is_safe(expected["success_url"], outcome="success"):
        raise StripeStateInvalid("stored Checkout success URL is invalid")
    if not _checkout_redirect_is_safe(expected["cancel_url"], outcome="cancelled"):
        raise StripeStateInvalid("stored Checkout cancel URL is invalid")

    if practice.stripe_customer_id is not None:
        if (
            frozen_data.get("customer") != practice.stripe_customer_id
            or "customer_email" in frozen_data
        ):
            raise StripeStateConflict(
                "Core customer binding changed after Checkout request construction"
            )
        expected["customer"] = practice.stripe_customer_id
    else:
        if "customer" in frozen_data:
            raise StripeStateConflict(
                "Core customer binding changed after Checkout request construction"
            )
        customer_email = frozen_data.get("customer_email")
        if customer_email is not None:
            expected["customer_email"] = customer_email

    expected_policy, expected_trial_end = _checkout_trial_policy_and_end(
        practice,
        now=request_created_at,
    )
    if expected_policy != trial_clock_policy:
        raise StripeStateInvalid("stored Checkout trial policy changed")

    if trial_clock_policy == CHECKOUT_TRIAL_CLOCK_POLICY:
        assert expected_trial_end is None
        expected.update(
            {
                "subscription_data[trial_period_days]": str(TRIAL_LENGTH.days),
                "metadata[trial_days]": str(TRIAL_LENGTH.days),
                "subscription_data[metadata][trial_days]": str(TRIAL_LENGTH.days),
                "subscription_data[trial_settings][end_behavior]"
                "[missing_payment_method]": "pause",
            }
        )
    elif trial_clock_policy == LEGACY_TRIAL_CLOCK_POLICY:
        if expected_trial_end is not None:
            expected["subscription_data[trial_end]"] = str(
                math.ceil(expected_trial_end.timestamp())
            )
            expected[
                "subscription_data[trial_settings][end_behavior]"
                "[missing_payment_method]"
            ] = "pause"
    else:
        raise StripeStateInvalid("stored Checkout trial policy is invalid")

    if frozen_data != expected:
        raise StripeStateInvalid("stored Checkout request violates policy")


def _validated_frozen_checkout_request_snapshot(
    practice: Practice, *, current: datetime
) -> tuple[int, int, str | None, int, dict[str, str]]:
    """Decode and validate authenticated, still-replayable request bytes."""

    try:
        frozen_data = json.loads(practice.stripe_checkout_request_json or "")
    except (TypeError, json.JSONDecodeError) as exc:
        raise StripeCheckoutReconciliationRequired from exc
    if (
        not isinstance(frozen_data, dict)
        or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in frozen_data.items()
        )
        or frozen_data.get("metadata[trial_clock]")
        != practice.stripe_trial_clock_policy
        or frozen_data.get("subscription_data[metadata][trial_clock]")
        != practice.stripe_trial_clock_policy
        or frozen_data.get("metadata[entitlement_contract]")
        != practice.stripe_checkout_entitlement_contract
    ):
        raise StripeCheckoutReconciliationRequired
    trial_clock_policy = practice.stripe_trial_clock_policy
    request_created_at = practice.stripe_checkout_request_created_at
    if trial_clock_policy is None or request_created_at is None:
        raise StripeCheckoutReconciliationRequired
    try:
        _validate_frozen_checkout_request(
            cast(dict[str, str], frozen_data),
            practice=practice,
            trial_clock_policy=trial_clock_policy,
            request_created_at=request_created_at,
        )
        replay_deadline = _checkout_request_replay_deadline(
            cast(dict[str, str], frozen_data),
            created_at=request_created_at,
        )
        if (
            current.tzinfo is None
            or current.astimezone(timezone.utc) >= replay_deadline
        ):
            raise StripeStateInvalid("stored Checkout request is outside replay window")
    except (
        StripeStateConflict,
        StripeStateInvalid,
        StripeTrialClockReconciliationRequired,
    ) as exc:
        raise StripeCheckoutReconciliationRequired from exc
    return (
        practice.stripe_checkout_state_version,
        practice.stripe_state_version,
        practice.stripe_checkout_session_id,
        practice.stripe_checkout_attempt,
        cast(dict[str, str], frozen_data),
    )


def _checkout_request_snapshot(
    practice: Practice,
    proposed_data: dict[str, str],
    *,
    current: datetime,
    verified_binding: tuple[str, str, int] | None,
) -> tuple[int, int, str | None, int, dict[str, str]]:
    """Durably freeze one exact Checkout request before provider I/O."""

    if current.tzinfo is None:
        raise StripeStateInvalid("Checkout request timestamp is invalid")
    normalized_now = current.astimezone(timezone.utc)
    proposed_json = json.dumps(proposed_data, sort_keys=True, separators=(",", ":"))

    with _isolated_state_session() as session:
        row = session.exec(
            select(Practice).where(Practice.id == practice.id).with_for_update()
        ).one()
        if _public_checkout_is_managed(row, at=normalized_now):
            raise StripeManagedBilling

        has_stored_binding = _has_core_subscription_projection(row)
        current_binding = _core_subscription_binding(row)
        if has_stored_binding and current_binding is None:
            raise StripeCoreBindingInvalid(
                "Core Stripe customer has no authoritative subscription binding"
            )
        if current_binding != verified_binding:
            raise StripeStateConflict(
                "Core Stripe binding changed before Checkout request creation"
            )
        if row.stripe_subscription_id is not None and (
            row.subscription_status not in TERMINAL_SUBSCRIPTION_STATUSES
        ):
            raise StripeCheckoutAlreadyCompleted
        if row.stripe_subscription_id is None and row.subscription_status is not None:
            raise StripeStateInvalid(
                "unbound billing state must be reconciled before Checkout"
            )
        if row.stripe_checkout_session_id is not None:
            raise StripeStateConflict(
                "Checkout session appeared before request creation"
            )

        if row.stripe_checkout_request_json is None:
            locked_quantity = _authorized_member_count(session, row)
            trial_policy, trial_end = _checkout_trial_policy_and_end(
                row,
                now=normalized_now,
            )
            proposed_trial_end = proposed_data.get("subscription_data[trial_end]")
            if (
                proposed_data.get("line_items[0][quantity]") != str(locked_quantity)
                or proposed_data.get("metadata[trial_clock]") != trial_policy
                or proposed_data.get("subscription_data[metadata][trial_clock]")
                != trial_policy
                or proposed_trial_end
                != (
                    str(math.ceil(trial_end.timestamp()))
                    if trial_end is not None
                    else None
                )
            ):
                raise StripeStateConflict(
                    "Core billing state changed before Checkout request creation"
                )
            row.stripe_trial_clock_policy = trial_policy
            row.stripe_checkout_entitlement_contract = cast(
                str, proposed_data["metadata[entitlement_contract]"]
            )
            _validate_frozen_checkout_request(
                proposed_data,
                practice=row,
                trial_clock_policy=trial_policy,
                request_created_at=normalized_now,
            )
            row.stripe_checkout_request_json = proposed_json
            row.stripe_checkout_request_created_at = normalized_now
            row.stripe_checkout_state_version += 1
            row.stripe_state_version += 1
            session.add(row)
            session.commit()

        snapshot = _validated_frozen_checkout_request_snapshot(
            row,
            current=normalized_now,
        )

    practice.refresh()
    return snapshot


def _clear_checkout_session(practice: Practice, *, advance: bool) -> None:
    if advance:
        practice.stripe_checkout_attempt += 1
    practice.stripe_checkout_session_id = None
    practice.stripe_checkout_session_url = None
    practice.stripe_checkout_session_expires_at = None
    practice.stripe_checkout_request_json = None
    practice.stripe_checkout_request_created_at = None
    practice.stripe_checkout_entitlement_contract = None
    practice.stripe_checkout_state_version += 1


def _checkout_cache_snapshot(practice_id: Any) -> tuple[int, str | None]:
    with _isolated_state_session() as session:
        row = session.exec(select(Practice).where(Practice.id == practice_id)).one()
        return row.stripe_checkout_state_version, row.stripe_checkout_session_id


def _persist_checkout_cache(
    practice: Practice,
    *,
    expected_version: int,
    expected_billing_version: int | None = None,
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
            or (
                expected_billing_version is not None
                and row.stripe_state_version != expected_billing_version
            )
        ):
            session.commit()
            raise StripeStateConflict("Checkout session state changed while in flight")
        if session_id is not None and _public_checkout_is_managed(row):
            session.commit()
            raise StripeManagedBilling
        if (
            session_id is not None
            and expected_billing_version is not None
            and row.stripe_checkout_entitlement_contract
            not in SUPPORTED_STRIPE_CONTRACT_PROVENANCE
        ):
            raise StripeStateInvalid(
                "Checkout entitlement contract provenance is missing"
            )
        if advance:
            row.stripe_checkout_attempt += 1
        row.stripe_checkout_session_id = session_id
        row.stripe_checkout_session_url = url
        row.stripe_checkout_session_expires_at = expires_at
        row.stripe_checkout_request_json = None
        row.stripe_checkout_request_created_at = None
        if session_id is None:
            row.stripe_checkout_entitlement_contract = None
        row.stripe_checkout_state_version += 1
        session.add(row)
        session.commit()
    practice.refresh()


def _checkout_session_expiry(session: dict[str, Any]) -> datetime:
    return _stripe_timestamp(
        session.get("expires_at"), source="checkout session expiry"
    )


def _validate_checkout_session_binding(
    session: dict[str, Any],
    practice: Practice,
    *,
    expected_id: str | None = None,
    expected_customer_id: str | None = None,
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
    if (
        expected_customer_id is not None
        and session.get("customer") != expected_customer_id
    ):
        raise StripeStateInvalid("checkout customer binding changed")


def _checkout_session_uses_current_policy(
    session: dict[str, Any],
    practice: Practice,
    *,
    require_metadata: bool,
) -> bool:
    if session.get("payment_method_collection") != CHECKOUT_PAYMENT_METHOD_COLLECTION:
        return False
    metadata = session.get("metadata")
    expected_contract = practice.stripe_checkout_entitlement_contract
    expected_trial_clock = practice.stripe_trial_clock_policy
    if expected_contract in SUPPORTED_STRIPE_ENTITLEMENT_CONTRACT_VERSIONS:
        if not isinstance(metadata, dict):
            return False
        return bool(
            metadata.get("checkout_policy") == PAID_PILOT_CHECKOUT_POLICY_VERSION
            and metadata.get("checkout_attempt")
            == str(practice.stripe_checkout_attempt)
            and metadata.get("practice_id") == str(practice.id)
            and metadata.get("entitlement_contract") == expected_contract
            and metadata.get("trial_clock") == expected_trial_clock
            and (
                metadata.get("trial_days") == str(TRIAL_LENGTH.days)
                if expected_trial_clock == CHECKOUT_TRIAL_CLOCK_POLICY
                else "trial_days" not in metadata
            )
        )
    if metadata is None or metadata == {}:
        # Pre-contract sessions were persisted by immutable id, but Stripe
        # serialized their unset metadata as an empty map on retrieval.
        return not require_metadata
    return bool(
        isinstance(metadata, dict)
        and metadata.get("checkout_policy")
        == LEGACY_CHECKOUT_IDEMPOTENCY_POLICY_VERSION
        and metadata.get("practice_id") == str(practice.id)
    )


def _recover_completed_checkout_session(
    practice: Practice, session: dict[str, Any]
) -> None:
    subscription_id = session.get("subscription")
    if not isinstance(subscription_id, str) or not subscription_id.startswith("sub_"):
        raise StripeStateInvalid("completed checkout has no subscription")
    subscription, _ = _apply_authoritative_subscription(
        practice,
        subscription_id,
        clear_checkout=True,
        require_practice_metadata=True,
        allow_terminal_rebind=True,
        trusted_checkout_session_id=cast(str, session["id"]),
    )
    assert subscription is not None
    raise StripeCheckoutAlreadyCompleted


def _resolve_expired_checkout_subscription(
    practice: Practice,
    session: dict[str, Any],
    *,
    expected_version: int,
    expected_session_id: str | None,
) -> None:
    """Bind an expired session's subscription before deciding on replacement.

    Although ordinary subscription-mode Checkout does not attach a subscription
    until completion, handling the state explicitly prevents a future Stripe
    behavior change or partial legacy flow from either creating a duplicate or
    leaving an opaque cache entry forever. Incomplete subscriptions remain the
    authoritative blocker until Stripe makes them terminal; retries refresh that
    state and can then start a new policy-compliant attempt.
    """

    subscription_id = session.get("subscription")
    if not isinstance(subscription_id, str) or not subscription_id.startswith("sub_"):
        raise StripeStateInvalid("expired checkout subscription id is invalid")
    subscription, _ = _apply_authoritative_subscription(
        practice,
        subscription_id,
        require_practice_metadata=True,
        allow_terminal_rebind=True,
        trusted_checkout_session_id=cast(str, session["id"]),
    )
    assert subscription is not None
    _persist_checkout_cache(
        practice,
        expected_version=expected_version,
        expected_session_id=expected_session_id,
        session_id=None,
        url=None,
        expires_at=None,
        advance=True,
    )
    if practice.subscription_status in TERMINAL_SUBSCRIPTION_STATUSES:
        return
    if practice.subscription_status == "incomplete":
        raise StripeCheckoutRetirementPending
    if has_nonterminal_subscription(practice):
        raise StripeCheckoutAlreadyCompleted
    raise StripeStateInvalid("expired checkout subscription state is invalid")


def _retire_open_checkout_session(
    practice: Practice,
    session: dict[str, Any],
    *,
    expected_version: int,
    expected_billing_version: int | None = None,
    expected_session_id: str | None,
) -> None:
    """Expire an open session before rotating its local idempotency attempt.

    Stripe expiration is the boundary that makes replacement safe: a Checkout
    Session that Stripe has expired cannot subsequently complete. If the
    expiration request races with completion or has an uncertain transport
    result, retrieve the session again and only clear local state after Stripe
    reports it expired. A completed session is bound instead of replaced.
    """

    session_id = cast(str, session["id"])
    try:
        resolved = _request(
            "POST",
            f"/checkout/sessions/{session_id}/expire",
            idempotency_key=_checkout_expire_idempotency_key(session_id),
        )
    except httpx.HTTPError:
        resolved = _request("GET", f"/checkout/sessions/{session_id}")

    _validate_checkout_session_binding(resolved, practice, expected_id=session_id)
    status = resolved.get("status")
    if status == "complete":
        _recover_completed_checkout_session(practice, resolved)
    if status != "expired":
        raise StripeStateInvalid("open checkout session could not be retired safely")
    if resolved.get("subscription") is not None:
        _resolve_expired_checkout_subscription(
            practice,
            resolved,
            expected_version=expected_version,
            expected_session_id=expected_session_id,
        )
        return
    _persist_checkout_cache(
        practice,
        expected_version=expected_version,
        expected_billing_version=expected_billing_version,
        expected_session_id=expected_session_id,
        session_id=None,
        url=None,
        expires_at=None,
        advance=True,
    )


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
        if not _checkout_session_uses_current_policy(
            session, practice, require_metadata=False
        ):
            _retire_open_checkout_session(
                practice,
                session,
                expected_version=expected_version,
                expected_session_id=session_id,
            )
            return None
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
        _recover_completed_checkout_session(practice, session)
    if session_status == "expired":
        if session.get("subscription") is not None:
            _resolve_expired_checkout_subscription(
                practice,
                session,
                expected_version=expected_version,
                expected_session_id=session_id,
            )
            return None
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


def _frozen_checkout_recovery_snapshot(
    practice: Practice,
) -> tuple[int, int, int, str, str, datetime, datetime, str | None]:
    """Lock and validate the exact Core state an operator may reconcile."""

    with _isolated_state_session() as session:
        row = session.exec(
            select(Practice).where(Practice.id == practice.id).with_for_update()
        ).one()
        if _public_checkout_is_managed(row):
            raise StripeManagedBilling
        if row.stripe_checkout_session_id is not None:
            raise StripeStateConflict(
                "the Checkout Session is already cached; use normal recovery"
            )
        if (
            row.stripe_checkout_request_json is None
            or row.stripe_checkout_request_created_at is None
            or row.stripe_trial_clock_policy is None
        ):
            raise StripeStateInvalid("there is no frozen Checkout request to recover")
        try:
            frozen = json.loads(row.stripe_checkout_request_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise StripeStateInvalid("stored Checkout request is invalid") from exc
        entitlement_contract = (
            frozen.get("metadata[entitlement_contract]")
            if isinstance(frozen, dict)
            else None
        )
        if (
            not isinstance(frozen, dict)
            or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in frozen.items()
            )
            or frozen.get("client_reference_id") != str(row.id)
            or frozen.get("metadata[practice_id]") != str(row.id)
            or frozen.get("metadata[checkout_policy]")
            != PAID_PILOT_CHECKOUT_POLICY_VERSION
            or frozen.get("metadata[checkout_attempt]")
            != str(row.stripe_checkout_attempt)
            or not isinstance(entitlement_contract, str)
            or entitlement_contract
            not in SUPPORTED_STRIPE_ENTITLEMENT_CONTRACT_VERSIONS
            or row.stripe_checkout_entitlement_contract != entitlement_contract
            or frozen.get("metadata[trial_clock]") != row.stripe_trial_clock_policy
        ):
            raise StripeStateInvalid("stored Checkout recovery identity is invalid")
        _validate_frozen_checkout_request(
            cast(dict[str, str], frozen),
            practice=row,
            trial_clock_policy=row.stripe_trial_clock_policy,
            request_created_at=row.stripe_checkout_request_created_at,
        )
        replay_deadline = _checkout_request_replay_deadline(
            cast(dict[str, str], frozen),
            created_at=row.stripe_checkout_request_created_at,
        )
        return (
            row.stripe_checkout_state_version,
            row.stripe_state_version,
            row.stripe_checkout_attempt,
            row.stripe_trial_clock_policy,
            entitlement_contract,
            row.stripe_checkout_request_created_at,
            replay_deadline,
            cast(str | None, frozen.get("customer")),
        )


def _matching_frozen_checkout_sessions(
    practice: Practice,
    *,
    checkout_attempt: int,
    entitlement_contract: str,
    request_created_at: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """List the bounded provider inventory for one frozen Checkout attempt."""

    matches: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    starting_after: str | None = None
    created_gte = max(0, int(request_created_at.timestamp()) - 300)
    for _page_number in range(10):
        params = {"limit": "100", "created[gte]": str(created_gte)}
        if starting_after is not None:
            params["starting_after"] = starting_after
        page = _request("GET", "/checkout/sessions", params)
        sessions = page.get("data")
        has_more = page.get("has_more")
        if not isinstance(sessions, list) or not isinstance(has_more, bool):
            raise StripeStateInvalid("Stripe Checkout recovery listing is invalid")
        for candidate in sessions:
            if not isinstance(candidate, dict):
                raise StripeStateInvalid(
                    "Stripe Checkout recovery listing contains an invalid Session"
                )
            metadata = candidate.get("metadata")
            exact_match = (
                candidate.get("client_reference_id") == str(practice.id)
                and isinstance(metadata, dict)
                and metadata.get("practice_id") == str(practice.id)
                and metadata.get("checkout_policy")
                == PAID_PILOT_CHECKOUT_POLICY_VERSION
                and metadata.get("checkout_attempt") == str(checkout_attempt)
                and metadata.get("entitlement_contract") == entitlement_contract
            )
            if exact_match:
                matches.append(candidate)
            elif candidate.get("client_reference_id") == str(practice.id) or (
                isinstance(metadata, dict)
                and metadata.get("practice_id") == str(practice.id)
            ):
                ambiguous.append(candidate)
        if not has_more:
            return matches, ambiguous
        if not sessions:
            raise StripeStateInvalid("Stripe Checkout recovery pagination is invalid")
        last_id = sessions[-1].get("id")
        if not isinstance(last_id, str) or not last_id.startswith("cs_"):
            raise StripeStateInvalid("Stripe Checkout recovery cursor is invalid")
        starting_after = last_id
    raise StripeStateInvalid("Stripe Checkout recovery exceeded its page limit")


def _checkout_session_matches_frozen_policy(
    session: dict[str, Any],
    *,
    practice: Practice,
    checkout_attempt: int,
    trial_clock_policy: str,
    entitlement_contract: str,
) -> bool:
    """Compare a remotely retrieved Session to immutable frozen policy fields."""

    if session.get("payment_method_collection") != CHECKOUT_PAYMENT_METHOD_COLLECTION:
        return False
    metadata = session.get("metadata")
    if not isinstance(metadata, dict):
        return False
    return bool(
        metadata.get("checkout_policy") == PAID_PILOT_CHECKOUT_POLICY_VERSION
        and metadata.get("checkout_attempt") == str(checkout_attempt)
        and metadata.get("practice_id") == str(practice.id)
        and metadata.get("entitlement_contract") == entitlement_contract
        and metadata.get("trial_clock") == trial_clock_policy
        and (
            metadata.get("trial_days") == str(TRIAL_LENGTH.days)
            if trial_clock_policy == CHECKOUT_TRIAL_CLOCK_POLICY
            else "trial_days" not in metadata
        )
    )


def _record_checkout_recovery_attempt(
    practice: Practice,
    *,
    operator_ref: str,
    reason: str,
    evidence_reference: str,
    evidence_sha256: str,
    current: datetime,
) -> str:
    """Append PHI-free evidence before applying a privileged recovery action."""

    from app.models.practice_access_audit import (
        PracticeAccessAction,
        PracticeAccessAudit,
    )

    operator = operator_ref.strip()
    explanation = reason.strip()
    reference = evidence_reference.strip()
    digest = evidence_sha256.strip().lower()
    if not operator or len(operator) > 255:
        raise StripeStateInvalid("checkout recovery operator reference is invalid")
    if not explanation or len(explanation) > 512:
        raise StripeStateInvalid("checkout recovery reason is invalid")
    if not reference or len(reference) > 255:
        raise StripeStateInvalid("checkout recovery evidence reference is invalid")
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise StripeStateInvalid("checkout recovery evidence digest is invalid")
    audit = PracticeAccessAudit(
        practice_id=practice.id,
        action=PracticeAccessAction.stripe_checkout_recovery_started.value,
        operator_ref=operator,
        reason=explanation,
        effective_at=current,
        document_reference=reference,
        evidence_sha256=digest,
    )
    with _isolated_state_session() as session:
        session.add(audit)
        session.commit()
        session.refresh(audit)
    return str(audit.id)


def reconcile_frozen_checkout_request(
    practice: Practice,
    *,
    operator_ref: str,
    reason: str,
    evidence_reference: str | None = None,
    evidence_sha256: str | None = None,
    confirm_absent: bool = False,
    apply: bool = False,
    now: datetime | None = None,
) -> tuple[str, str | None]:
    """Plan or audit/apply recovery without creating a replacement Session."""

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise StripeStateInvalid("Checkout recovery timestamp is invalid")
    current = current.astimezone(timezone.utc)
    _validate_runtime_configuration()
    (
        expected_version,
        expected_billing_version,
        checkout_attempt,
        trial_clock_policy,
        entitlement_contract,
        request_created_at,
        replay_deadline,
        expected_customer_id,
    ) = _frozen_checkout_recovery_snapshot(practice)
    matches, ambiguous = _matching_frozen_checkout_sessions(
        practice,
        checkout_attempt=checkout_attempt,
        entitlement_contract=entitlement_contract,
        request_created_at=request_created_at,
    )
    if ambiguous:
        raise StripeStateConflict(
            "ambiguous Stripe Checkout Sessions require manual provider resolution"
        )
    if len(matches) > 1:
        raise StripeStateConflict(
            "multiple Stripe Checkout Sessions match one Core attempt"
        )
    if not matches:
        if not apply:
            return "no_matching_session", None
        if not confirm_absent:
            raise StripeCheckoutReconciliationRequired
        if stripe_config.STRIPE_BILLING_ENABLED:
            raise StripeStateInvalid(
                "disable Checkout acquisition before confirming a Session absent"
            )
        absence_not_before = (
            request_created_at.astimezone(timezone.utc)
            + CHECKOUT_REQUEST_REPLAY_WINDOW
            + CHECKOUT_RECOVERY_TRANSPORT_SAFETY
        )
        if current < absence_not_before:
            raise StripeStateInvalid(
                "the Checkout recovery transport-safety window has not elapsed"
            )
        audit_id = _record_checkout_recovery_attempt(
            practice,
            operator_ref=operator_ref,
            reason=reason,
            evidence_reference=evidence_reference or "",
            evidence_sha256=evidence_sha256 or "",
            current=current,
        )
        _persist_checkout_cache(
            practice,
            expected_version=expected_version,
            expected_billing_version=expected_billing_version,
            expected_session_id=None,
            session_id=None,
            url=None,
            expires_at=None,
            advance=True,
        )
        _emit_billing_metric(
            "checkout_recovery",
            outcome="operator_confirmed_absent",
            practice_id=practice.id,
        )
        return "operator_confirmed_absent", audit_id

    listed_session = matches[0]
    session_id = listed_session.get("id")
    if not isinstance(session_id, str) or not session_id.startswith("cs_"):
        raise StripeStateInvalid("matched Checkout Session id is invalid")
    remote_session = _request("GET", f"/checkout/sessions/{session_id}")
    _validate_checkout_session_binding(
        remote_session,
        practice,
        expected_id=session_id,
        expected_customer_id=expected_customer_id,
    )
    if not _checkout_session_matches_frozen_policy(
        remote_session,
        practice=practice,
        checkout_attempt=checkout_attempt,
        trial_clock_policy=trial_clock_policy,
        entitlement_contract=entitlement_contract,
    ):
        raise StripeStateInvalid("matched Checkout Session violates policy")

    session_status = remote_session.get("status")
    if session_status not in {"open", "complete", "expired"}:
        raise StripeStateInvalid("matched Checkout Session status is invalid")
    subscription_id = remote_session.get("subscription")
    if subscription_id is not None and (
        not isinstance(subscription_id, str) or not subscription_id.startswith("sub_")
    ):
        raise StripeStateInvalid("matched Checkout subscription id is invalid")
    if session_status == "complete" and subscription_id is None:
        raise StripeStateInvalid("completed Checkout Session has no subscription")

    expiry = _checkout_session_expiry(remote_session)
    hosted_url = (
        _hosted_url(remote_session.get("url"), hostname="checkout.stripe.com")
        if session_status == "open"
        else None
    )
    planned_outcome = {
        "open": "would_expire_open_session",
        "complete": "would_bind_completed_session",
        "expired": (
            "would_resolve_expired_subscription"
            if subscription_id is not None
            else "would_clear_expired_session"
        ),
    }[cast(str, session_status)]
    if not apply:
        return planned_outcome, None
    if current < replay_deadline:
        raise StripeStateInvalid(
            "the frozen Checkout request is still safely replayable"
        )

    audit_id = _record_checkout_recovery_attempt(
        practice,
        operator_ref=operator_ref,
        reason=reason,
        evidence_reference=evidence_reference or "",
        evidence_sha256=evidence_sha256 or "",
        current=current,
    )
    _persist_checkout_cache(
        practice,
        expected_version=expected_version,
        expected_billing_version=expected_billing_version,
        expected_session_id=None,
        session_id=session_id,
        url=hosted_url,
        expires_at=expiry,
    )
    recovered_version, recovered_session_id = _checkout_cache_snapshot(practice.id)
    if session_status == "open":
        try:
            _retire_open_checkout_session(
                practice,
                remote_session,
                expected_version=recovered_version,
                expected_billing_version=expected_billing_version,
                expected_session_id=recovered_session_id,
            )
        except StripeCheckoutRetirementPending:
            return "operator_bound_incomplete_subscription", audit_id
        except StripeCheckoutAlreadyCompleted:
            return "operator_bound_completed_subscription", audit_id
        _emit_billing_metric(
            "checkout_recovery",
            outcome="operator_expired_open_session",
            practice_id=practice.id,
        )
        return "operator_expired_open_session", audit_id
    if session_status == "expired" and subscription_id is None:
        _persist_checkout_cache(
            practice,
            expected_version=recovered_version,
            expected_billing_version=expected_billing_version,
            expected_session_id=recovered_session_id,
            session_id=None,
            url=None,
            expires_at=None,
            advance=True,
        )
        _emit_billing_metric(
            "checkout_recovery",
            outcome="operator_cleared_expired_session",
            practice_id=practice.id,
        )
        return "operator_cleared_expired_session", audit_id
    if session_status == "complete":
        try:
            _recover_completed_checkout_session(practice, remote_session)
        except StripeCheckoutAlreadyCompleted:
            return "operator_bound_completed_session", audit_id
        raise StripeStateInvalid("completed Checkout Session was not bound")

    try:
        _resolve_expired_checkout_subscription(
            practice,
            remote_session,
            expected_version=recovered_version,
            expected_session_id=recovered_session_id,
        )
    except StripeCheckoutRetirementPending:
        return "operator_bound_incomplete_subscription", audit_id
    except StripeCheckoutAlreadyCompleted:
        return "operator_resolved_expired_subscription", audit_id
    return "operator_resolved_expired_subscription", audit_id


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
    if _public_checkout_is_managed(practice, at=current):
        raise StripeManagedBilling
    has_stored_binding = _has_core_subscription_projection(practice)
    if has_stored_binding and not _has_complete_core_subscription_binding(practice):
        raise StripeCoreBindingInvalid(
            "Core Stripe customer has no authoritative subscription binding"
        )

    # A previously-created session is not a new sale. Revalidate and
    # return it before applying new-session trial/active-price gates, otherwise
    # a later price archive would strand payment completion.
    if practice.stripe_checkout_session_id:
        _validate_runtime_configuration()
        recovered_url = _recover_checkout_session(practice, current=current)
        if recovered_url:
            return recovered_url
    if practice.subscription_status == "incomplete":
        if not practice.stripe_checkout_session_id:
            raise StripeCheckoutRetirementPending
        raise StripeStateInvalid("incomplete Checkout session is no longer resumable")

    # A cached session that proved expired/abandoned is no longer a resume.
    # Re-enforce acquisition readiness before rotating the idempotency key or
    # creating a replacement sale; maintenance credentials alone are not
    # authorization to bypass the new-checkout kill switch.
    if not stripe_config.stripe_billing_configured():
        raise StripeNotConfigured

    _validate_runtime_configuration(require_active_price=True)
    verified_binding: tuple[str, str, int] | None = None
    if has_stored_binding:
        verified_binding, _ = _verified_bound_customer(
            practice,
            require_terminal_subscription=True,
        )
    if now is None:
        # Re-read the clock after remote validation so the minimum Stripe lead
        # is measured from as near as practical to Checkout Session creation.
        current = datetime.now(timezone.utc)

    seat_quantity = attested_member_count(practice)
    trial_clock_policy, stripe_trial_end = _checkout_trial_policy_and_end(
        practice,
        now=current,
    )
    entitlement_contract = CURRENT_STRIPE_ENTITLEMENT_CONTRACT_VERSION
    data: dict[str, str] = {
        "mode": "subscription",
        "line_items[0][price]": stripe_config.STRIPE_PRICE_ID,
        "line_items[0][quantity]": str(seat_quantity),
        "client_reference_id": str(practice.id),
        "success_url": success_url,
        "cancel_url": cancel_url,
        # Existing pilots bypass Checkout through explicit grants/exemptions.
        # Public self-serve Checkout must collect a payment method so the
        # subscription can continue when its free trial ends.
        "payment_method_collection": CHECKOUT_PAYMENT_METHOD_COLLECTION,
        "metadata[checkout_policy]": PAID_PILOT_CHECKOUT_POLICY_VERSION,
        "metadata[checkout_attempt]": str(practice.stripe_checkout_attempt),
        "metadata[practice_id]": str(practice.id),
        "metadata[entitlement_contract]": entitlement_contract,
        "metadata[trial_clock]": trial_clock_policy,
        "subscription_data[metadata][practice_id]": str(practice.id),
        "subscription_data[metadata][checkout_policy]": (
            PAID_PILOT_CHECKOUT_POLICY_VERSION
        ),
        "subscription_data[metadata][entitlement_contract]": entitlement_contract,
        "subscription_data[metadata][trial_clock]": trial_clock_policy,
    }
    if verified_binding is not None:
        data["customer"] = verified_binding[1]
    elif customer_email:
        data["customer_email"] = customer_email

    if trial_clock_policy == CHECKOUT_TRIAL_CLOCK_POLICY:
        assert stripe_trial_end is None
        data["subscription_data[trial_period_days]"] = str(TRIAL_LENGTH.days)
        data["metadata[trial_days]"] = str(TRIAL_LENGTH.days)
        data["subscription_data[metadata][trial_days]"] = str(TRIAL_LENGTH.days)
    elif stripe_trial_end is not None:
        data["subscription_data[trial_end]"] = str(
            math.ceil(stripe_trial_end.timestamp())
        )
    if (
        trial_clock_policy == CHECKOUT_TRIAL_CLOCK_POLICY
        or stripe_trial_end is not None
    ):
        data[
            "subscription_data[trial_settings][end_behavior][missing_payment_method]"
        ] = "pause"

    (
        expected_version,
        expected_billing_version,
        expected_session_id,
        checkout_attempt,
        frozen_data,
    ) = _checkout_request_snapshot(
        practice,
        data,
        current=current,
        verified_binding=verified_binding,
    )
    session = _request(
        "POST",
        "/checkout/sessions",
        frozen_data,
        idempotency_key=_checkout_idempotency_key(
            practice,
            attempt=checkout_attempt,
        ),
    )
    _validate_checkout_session_binding(
        session,
        practice,
        expected_customer_id=(
            verified_binding[1] if verified_binding is not None else None
        ),
    )
    session_status = session.get("status")
    if session_status not in {"open", "complete", "expired"}:
        raise StripeStateInvalid("new checkout session status is invalid")
    session_id = cast(str, session["id"])
    expiry = _checkout_session_expiry(session)
    if not _checkout_session_uses_current_policy(
        session, practice, require_metadata=True
    ):
        if session_status == "open":
            _retire_open_checkout_session(
                practice,
                session,
                expected_version=expected_version,
                expected_billing_version=expected_billing_version,
                expected_session_id=expected_session_id,
            )
        raise StripeStateInvalid(
            "new checkout session did not enforce paid-pilot policy"
        )
    if session_status == "open" and session.get("subscription") is not None:
        _retire_open_checkout_session(
            practice,
            session,
            expected_version=expected_version,
            expected_billing_version=expected_billing_version,
            expected_session_id=expected_session_id,
        )
        raise StripeStateInvalid(
            "new open checkout unexpectedly created a subscription"
        )
    if session_status == "complete":
        _persist_checkout_cache(
            practice,
            expected_version=expected_version,
            expected_billing_version=expected_billing_version,
            expected_session_id=expected_session_id,
            session_id=session_id,
            url=None,
            expires_at=expiry,
        )
        _recover_completed_checkout_session(practice, session)
    if session_status == "expired":
        if session.get("subscription") is not None:
            _persist_checkout_cache(
                practice,
                expected_version=expected_version,
                expected_billing_version=expected_billing_version,
                expected_session_id=expected_session_id,
                session_id=session_id,
                url=None,
                expires_at=expiry,
            )
            recovered_version = _checkout_cache_snapshot(practice.id)[0]
            _resolve_expired_checkout_subscription(
                practice,
                session,
                expected_version=recovered_version,
                expected_session_id=session_id,
            )
        else:
            _persist_checkout_cache(
                practice,
                expected_version=expected_version,
                expected_billing_version=expected_billing_version,
                expected_session_id=expected_session_id,
                session_id=None,
                url=None,
                expires_at=None,
                advance=True,
            )
        raise StripeStateConflict(
            "A recovered Checkout request had expired; retry with a new attempt"
        )
    if expiry <= current:
        _retire_open_checkout_session(
            practice,
            session,
            expected_version=expected_version,
            expected_billing_version=expected_billing_version,
            expected_session_id=expected_session_id,
        )
        raise StripeStateInvalid("checkout session was already expired")
    url = _hosted_url(session.get("url"), hostname="checkout.stripe.com")

    try:
        _persist_checkout_cache(
            practice,
            expected_version=expected_version,
            expected_billing_version=expected_billing_version,
            expected_session_id=expected_session_id,
            session_id=session_id,
            url=url,
            expires_at=expiry,
        )
    except (StripeStateConflict, StripeManagedBilling):
        practice.refresh()
        if practice.stripe_checkout_session_id == session_id:
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
        target = _authorized_member_count(session, row)
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


def _subscription_item_period_start(item: dict[str, Any]) -> datetime:
    # API 2025-09-30.clover carries the billing period on the item, not on
    # the subscription; during a trial it is the trial period.
    start = item.get("current_period_start")
    end = item.get("current_period_end")
    if (
        not isinstance(start, int)
        or isinstance(start, bool)
        or not isinstance(end, int)
        or isinstance(end, bool)
        or end <= start
    ):
        raise StripeStateInvalid(
            "subscription item billing period is missing or invalid"
        )
    return _stripe_timestamp(start, source="subscription item billing period")


def _paid_seat_high_water(
    practice: Practice, *, quantity: int, period_start: datetime, repairing: bool
) -> int | None:
    """Return the highest seat count already paid in Stripe's current period.

    A new period starts from the quantity Stripe billed at renewal. Folding in
    the current quantity also covers a sync whose confirmation was lost after
    Stripe applied a billable raise. An out-of-range quantity under repair was
    never a paid seat count.
    """

    recorded = (
        practice.stripe_paid_seat_high_water
        if practice.stripe_paid_seat_period_start == period_start
        else None
    )
    observed = None if repairing else quantity
    return max((v for v in (recorded, observed) if v is not None), default=None)


def _quantity_update_steps(
    *, current: int, target: int, high_water: int | None, billable: bool
) -> list[tuple[int, str]]:
    """Plan absolute quantity updates so only seats above the high-water prorate."""

    paid = high_water or 0
    if target < current or not billable or target <= paid:
        return [(target, "none")]
    if current < paid:
        # Stripe prorates the whole current-to-target difference in one call,
        # so restore the already-paid seats first without proration.
        return [(paid, "none"), (target, "create_prorations")]
    return [(target, "create_prorations")]


def _record_paid_seat_high_water(
    row: Practice, *, high_water: int, period_start: datetime
) -> None:
    row.stripe_paid_seat_high_water = high_water
    row.stripe_paid_seat_period_start = period_start


def _finish_quantity_sync(
    practice_id: Any,
    *,
    subscription_id: str,
    version: int,
    target: int,
    error: Exception | None = None,
    paid_seats: tuple[int, datetime] | None = None,
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
        if paid_seats is not None:
            # The high-water stands whether or not Core's target has moved on
            # or the update failed: a failed sync may already have charged the
            # seats Stripe held when it planned.
            _record_paid_seat_high_water(
                row, high_water=paid_seats[0], period_start=paid_seats[1]
            )
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


@contextmanager
def _quantity_sync_lease(practice_id: Any) -> Iterator[bool]:
    """Serialize one practice's Stripe quantity mutations across processes.

    Stripe has no compare-and-set on quantity, so an overlapping sync could
    lower Stripe back to the high-water after another sync's billable raise
    and then prorate the same seats again. A session-level advisory lock on
    its own autocommit connection spans the HTTP calls without holding a
    transaction or row lock, and PostgreSQL releases it if the process dies.
    """

    key = int.from_bytes(
        hashlib.sha256(f"stripe-quantity-sync:{practice_id}".encode()).digest()[:8],
        byteorder="big",
        signed=True,
    )
    with SessionManager.get_instance().get_engine().connect() as connection:
        connection.execution_options(isolation_level="AUTOCOMMIT")
        acquired = bool(
            connection.execute(select(func.pg_try_advisory_lock(key))).scalar_one()
        )
        try:
            yield acquired
        finally:
            if acquired:
                connection.execute(select(func.pg_advisory_unlock(key)))


def sync_subscription_quantity(
    practice: Practice, *, raise_on_error: bool = False
) -> bool:
    """Reconcile the paid quantity, leaving durable retry state on failure."""

    with _quantity_sync_lease(practice.id) as leased:
        if not leased:
            # The in-flight sync re-reads Core's target when it finishes, and
            # any newer obligation stays durably required for the next sweep.
            if raise_on_error:
                raise StripeStateConflict("a quantity sync is already in flight")
            return False
        return _sync_subscription_quantity_leased(
            practice, raise_on_error=raise_on_error
        )


def _sync_subscription_quantity_leased(
    practice: Practice, *, raise_on_error: bool
) -> bool:
    snapshot = _quantity_sync_snapshot(practice.id, force_new_version=False)
    if snapshot is None:
        return True
    target, version, subscription_id = snapshot
    paid_seats: tuple[int, datetime] | None = None
    try:
        practice.refresh()
        assert practice.stripe_subscription_id
        subscription = refresh_subscription_state(practice)
        assert subscription is not None
        practice.refresh()
        repairing_remote_quantity = bool(
            practice.subscription_status == "invalid"
            and practice.stripe_quantity_sync_error
            == REMOTE_QUANTITY_OUT_OF_RANGE_ERROR
            and practice.stripe_subscription_id == subscription_id
            and subscription["id"] == subscription_id
            and subscription["customer"] == practice.stripe_customer_id
        )
        if (
            (
                practice.subscription_status == "invalid"
                and not repairing_remote_quantity
            )
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
            item = _subscription_item(
                subscription,
                enforce_quantity_bounds=not repairing_remote_quantity,
            )
            if item["quantity"] != target:
                period_start = _subscription_item_period_start(item)
                high_water = _paid_seat_high_water(
                    practice,
                    quantity=item["quantity"],
                    period_start=period_start,
                    repairing=repairing_remote_quantity,
                )
                if high_water is not None:
                    paid_seats = (high_water, period_start)
                # The part-month is queued on the next invoice, never charged
                # now, so a declined card cannot make an active practice
                # past_due mid-cycle. The mirrored status treats a portal
                # pause_collection as paused (Stripe's raw status stays
                # "active"). A trial, a paused subscription and an incomplete
                # first payment have nothing to prorate, and a drift repair
                # realigns Stripe to Core rather than adding a seat.
                billable = (
                    not repairing_remote_quantity
                    and _mirrored_subscription_status(subscription)
                    in {"active", "past_due", "unpaid"}
                )
                steps = _quantity_update_steps(
                    current=item["quantity"],
                    target=target,
                    high_water=high_water,
                    billable=billable,
                )
                for step, (quantity, proration_behavior) in enumerate(steps):
                    if step:
                        # An out-of-band edit, or a step replayed after a crash
                        # (Stripe returns the cached response without reapplying
                        # it), would widen this proration; only a fresh read
                        # shows where it starts.
                        applied = _subscription_item(
                            _retrieve_subscription(subscription_id)
                        )
                        if applied["quantity"] != steps[step - 1][0]:
                            raise StripeStateInvalid(
                                "Stripe quantity changed between seat updates"
                            )
                    # The attempt makes a retry after a failure re-send every
                    # step, since Stripe may have moved in between.
                    key_source = (
                        f"quantity:{subscription['id']}:{target}:version:{version}"
                        f":attempt:{practice.stripe_quantity_sync_attempts}"
                        f":step:{step}:{quantity}:proration:{proration_behavior}"
                    )
                    _request(
                        "POST",
                        f"/subscription_items/{item['id']}",
                        {
                            "quantity": str(quantity),
                            "proration_behavior": proration_behavior,
                        },
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
                if _subscription_item_period_start(confirmed) != period_start:
                    # Stripe renewed at the old quantity before this update, so
                    # the plan used the old period's high-water. The next sweep
                    # plans against the new period, but cannot charge a raise
                    # Stripe already applied here without proration.
                    raise StripeStateInvalid(
                        "Stripe renewed the billing period during the quantity sync"
                    )
                paid_seats = (max(high_water or 0, target), period_start)
            else:
                try:
                    period_start = _subscription_item_period_start(item)
                except StripeStateInvalid:
                    period_start = None
                if period_start is not None:
                    high_water = _paid_seat_high_water(
                        practice,
                        quantity=item["quantity"],
                        period_start=period_start,
                        repairing=repairing_remote_quantity,
                    )
                    if high_water is not None:
                        paid_seats = (high_water, period_start)
            if repairing_remote_quantity:
                # The first refresh intentionally revoked entitlement before
                # repair. Only a second fully validated in-range read may
                # restore the authoritative subscription projection.
                repaired = refresh_subscription_state(practice)
                if repaired is None or practice.subscription_status == "invalid":
                    raise StripeStateInvalid(
                        "Stripe quantity repair did not restore a valid subscription"
                    )
            return _finish_quantity_sync(
                practice.id,
                subscription_id=subscription_id,
                version=version,
                target=target,
                paid_seats=paid_seats,
            )
    except Exception as exc:
        _finish_quantity_sync(
            practice.id,
            subscription_id=subscription_id,
            version=version,
            target=target,
            error=exc,
            paid_seats=paid_seats,
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
    return _stripe_timestamp(event.get("created"), source="Stripe event creation time")


def _stripe_reference_id(value: object, *, prefix: str, source: str) -> str:
    if isinstance(value, dict):
        value = value.get("id")
    if (
        not isinstance(value, str)
        or not value.startswith(prefix)
        or len(value) <= len(prefix)
    ):
        raise StripeStateInvalid(f"{source} is invalid")
    return value


def _practice_id_hint(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = TypeID.from_string(value)
    except (TypeError, ValueError, TypeIDException):
        return None
    return value if parsed.prefix == "practice" and str(parsed) == value else None


def _invoice_event_details(
    obj: dict[str, Any],
) -> tuple[str, str] | None:
    """Return a subscription Invoice's canonical subscription/customer ids."""

    _stripe_reference_id(obj.get("id"), prefix="in_", source="invoice id")
    _validate_livemode(obj.get("livemode"), source="invoice")

    top_level_subscription = obj.get("subscription")
    top_level_subscription_id = (
        _stripe_reference_id(
            top_level_subscription,
            prefix="sub_",
            source="invoice subscription",
        )
        if top_level_subscription is not None
        else None
    )

    parent = obj.get("parent")
    parent_subscription_id: str | None = None
    if parent is not None:
        if not isinstance(parent, dict):
            raise StripeStateInvalid("invoice parent is invalid")
        parent_type = parent.get("type")
        if parent_type == "subscription_details":
            details = parent.get("subscription_details")
            if not isinstance(details, dict):
                raise StripeStateInvalid("invoice subscription details are invalid")
            parent_subscription_id = _stripe_reference_id(
                details.get("subscription"),
                prefix="sub_",
                source="invoice subscription",
            )
        elif top_level_subscription_id is None:
            # One-off invoices have no subscription state to mirror.
            return None

    if parent_subscription_id is not None and top_level_subscription_id not in (
        None,
        parent_subscription_id,
    ):
        raise StripeStateInvalid("invoice subscription references conflict")
    subscription_id = parent_subscription_id or top_level_subscription_id
    if subscription_id is None:
        return None

    customer_id = _stripe_reference_id(
        obj.get("customer"), prefix="cus_", source="invoice customer"
    )
    return subscription_id, customer_id


def _event_subscription_id(event_type: str, obj: dict[str, Any]) -> str | None:
    if event_type in INVOICE_EVENT_TYPES:
        details = _invoice_event_details(obj)
        return details[0] if details is not None else None
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
    if subscription_id is None:
        return None
    return event_type, event_id, event_created_at, obj, subscription_id


def _minimal_stripe_event(event: dict[str, Any], obj: dict[str, Any]) -> dict[str, Any]:
    """Retain only fields needed for reconciliation; never billing details."""

    event_type = cast(str, event["type"])

    if event_type == "checkout.session.completed":
        minimal_object = {
            "id": obj.get("id"),
            "client_reference_id": _practice_id_hint(obj.get("client_reference_id")),
            "subscription": obj.get("subscription"),
        }
    elif event_type in INVOICE_EVENT_TYPES:
        invoice_details = _invoice_event_details(obj)
        assert invoice_details is not None
        subscription_id, customer_id = invoice_details
        minimal_object = {
            "id": obj.get("id"),
            "customer": customer_id,
            "livemode": obj.get("livemode"),
            "parent": {
                "type": "subscription_details",
                "subscription_details": {
                    "subscription": subscription_id,
                },
            },
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
            "metadata": {"practice_id": _practice_id_hint(metadata.get("practice_id"))},
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
            if event_type in INVOICE_EVENT_TYPES:
                practice = session.exec(
                    select(Practice)
                    .where(Practice.stripe_subscription_id == subscription_id)
                    .with_for_update()
                ).one_or_none()
                if practice is not None:
                    invoice_details = _invoice_event_details(obj)
                    assert invoice_details is not None
                    _, invoice_customer_id = invoice_details
                    should_revoke_pending = bool(
                        event_type == "invoice.payment_failed"
                        or practice.stripe_customer_id != invoice_customer_id
                    )
                    if should_revoke_pending and (
                        practice.stripe_last_event_created_at is None
                        or event_created_at >= practice.stripe_last_event_created_at
                    ):
                        practice.subscription_status = "pending_verification"
                        practice.stripe_state_version += 1
                        session.add(practice)
            elif event_type != "checkout.session.completed":
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
    # Initial invoice recovery routes only through a Stripe customer id already
    # persisted by Core; fresh subscription metadata must then confirm it.
    practice = Practice.get(stripe_subscription_id=subscription_id)
    resolved_from_metadata = False
    resolved_from_paid_invoice = False
    invoice_customer_id: str | None = None
    trusted_invoice_checkout_session_id: str | None = None
    if practice is None and event_type == "checkout.session.completed":
        candidate_practice_id = obj.get("client_reference_id")
        practice = (
            Practice.get(id=candidate_practice_id)
            if isinstance(candidate_practice_id, str)
            else None
        )
    elif event_type in INVOICE_EVENT_TYPES:
        invoice_details = _invoice_event_details(obj)
        assert invoice_details is not None
        _, invoice_customer_id = invoice_details
        if practice is None and event_type in INVOICE_SUCCESS_EVENT_TYPES:
            practice = Practice.get(stripe_customer_id=invoice_customer_id)
            resolved_from_paid_invoice = practice is not None
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

    if resolved_from_paid_invoice and practice.stripe_checkout_session_id is not None:
        _validate_runtime_configuration()
        checkout_session_id = practice.stripe_checkout_session_id
        checkout_session = _request("GET", f"/checkout/sessions/{checkout_session_id}")
        _validate_checkout_session_binding(
            checkout_session,
            practice,
            expected_id=checkout_session_id,
            expected_customer_id=invoice_customer_id,
        )
        if checkout_session.get("status") != "complete":
            raise StripeStateConflict(
                "paid invoice arrived while Checkout was still pending"
            )
        checkout_subscription_id = _stripe_reference_id(
            checkout_session.get("subscription"),
            prefix="sub_",
            source="checkout subscription",
        )
        if checkout_subscription_id != subscription_id:
            raise StripeStateConflict(
                "paid invoice does not match the pending Checkout Session"
            )
        if not _checkout_session_uses_current_policy(
            checkout_session, practice, require_metadata=True
        ):
            raise StripeStateInvalid(
                "completed Checkout Session did not enforce paid-pilot policy"
            )
        trusted_invoice_checkout_session_id = checkout_session_id

    trusted_checkout_session_id = (
        cast(str, obj["id"])
        if event_type == "checkout.session.completed" and isinstance(obj.get("id"), str)
        else trusted_invoice_checkout_session_id
    )

    subscription, applied = _apply_authoritative_subscription(
        practice,
        subscription_id,
        event_created_at=event_created_at,
        event_id=event_id,
        event_type=event_type,
        clear_checkout=(
            event_type == "checkout.session.completed"
            or trusted_invoice_checkout_session_id is not None
        ),
        require_practice_metadata=(
            event_type == "checkout.session.completed"
            or resolved_from_metadata
            or resolved_from_paid_invoice
        ),
        allow_terminal_rebind=(
            event_type == "checkout.session.completed" or resolved_from_paid_invoice
        ),
        trusted_checkout_session_id=trusted_checkout_session_id,
        invoice_customer_id=invoice_customer_id,
        allow_paid_invoice_bind=resolved_from_paid_invoice,
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
