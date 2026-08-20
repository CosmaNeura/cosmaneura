"""Stripe billing for practices (ADR 0017 billing v2).

The platform is the system of record for entitlement; Stripe supplies
verified commerce events that set it. Checkout sessions are created by the
backend (never the website), webhooks mirror subscription state onto the
practice, and the customer portal handles card changes and cancellation.

Uses Stripe's REST API directly via httpx — no SDK dependency.
"""

import hashlib
import hmac
import time
from datetime import datetime, timezone
from typing import Any, cast

import httpx

from app import log
from app.configuration import stripe as stripe_config

from app.models.practice import Practice, PracticeMember

STRIPE_API = "https://api.stripe.com/v1"
SIGNATURE_TOLERANCE_SECONDS = 5 * 60

MIRRORED_EVENT_TYPES = {
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
}


class StripeNotConfigured(Exception):
    pass


def _request(method: str, path: str, data: dict[str, Any] | None = None) -> dict:
    if not stripe_config.STRIPE_SECRET_KEY:
        raise StripeNotConfigured
    response = httpx.request(
        method,
        f"{STRIPE_API}{path}",
        auth=(stripe_config.STRIPE_SECRET_KEY, ""),
        data=data,
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def attested_member_count(practice: Practice) -> int:
    members = PracticeMember.where(
        PracticeMember.practice_id == practice.id,
        PracticeMember.attested_at != None,  # noqa: E711
    ).all()
    return max(1, len(list(members)))


def create_checkout_session(
    practice: Practice, *, customer_email: str | None, success_url: str, cancel_url: str
) -> str:
    """Create a subscription Checkout Session and return its URL.

    The trial already running on the practice is carried into Stripe via
    trial_end so paying early never shortens the free two weeks, a 100%-off
    promotion code completes without a card (payment_method_collection
    if_required), and client_reference_id ties the session to the practice.
    """
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

    now = datetime.now(timezone.utc)
    if (
        practice.trial_ends_at
        and (practice.trial_ends_at - now).total_seconds() > 48 * 3600
    ):
        data["subscription_data[trial_end]"] = str(
            int(practice.trial_ends_at.timestamp())
        )
        data[
            "subscription_data[trial_settings][end_behavior][missing_payment_method]"
        ] = "pause"

    session = _request("POST", "/checkout/sessions", data)
    return cast(str, session["url"])


def create_portal_session(practice: Practice, *, return_url: str) -> str:
    assert practice.stripe_customer_id
    session = _request(
        "POST",
        "/billing_portal/sessions",
        {"customer": practice.stripe_customer_id, "return_url": return_url},
    )
    return cast(str, session["url"])


def sync_subscription_quantity(practice: Practice) -> None:
    """Keep the subscription's quantity equal to the practice's attested
    members ($89 per clinician). Failure-tolerant: billing drift must never
    block a clinician from joining."""
    if not stripe_config.stripe_billing_configured():
        return
    if not practice.stripe_subscription_id:
        return
    try:
        subscription = _request(
            "GET", f"/subscriptions/{practice.stripe_subscription_id}"
        )
        item = subscription["items"]["data"][0]
        quantity = attested_member_count(practice)
        if item["quantity"] != quantity:
            _request(
                "POST", f"/subscription_items/{item['id']}", {"quantity": str(quantity)}
            )
    except Exception:
        log.warning(
            "failed to sync stripe subscription quantity",
            practice_id=str(practice.id),
            exc_info=True,
        )


def verify_webhook_signature(
    payload: bytes, signature_header: str | None, *, now: int | None = None
) -> bool:
    """Verify Stripe's `Stripe-Signature: t=...,v1=...` header: HMAC-SHA256 of
    "{t}.{payload}" keyed by the webhook secret, within a replay tolerance."""
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


def _practice_for_event(obj: dict) -> Practice | None:
    practice_id = obj.get("client_reference_id") or (obj.get("metadata") or {}).get(
        "practice_id"
    )
    if practice_id:
        practice = Practice.get(id=practice_id)
        if practice:
            return practice
    subscription_id = (
        obj.get("subscription")
        if isinstance(obj.get("subscription"), str)
        else obj.get("id")
    )
    if subscription_id:
        return Practice.get(stripe_subscription_id=subscription_id)
    return None


def apply_stripe_event(event: dict) -> bool:
    """Mirror a verified Stripe event onto the practice it references.
    Returns True when a practice was updated."""
    event_type = event.get("type", "")
    if event_type not in MIRRORED_EVENT_TYPES:
        return False

    obj = event.get("data", {}).get("object", {})
    practice = _practice_for_event(obj)
    if practice is None:
        log.warning("stripe event matched no practice", event_type=event_type)
        return False

    if event_type == "checkout.session.completed":
        customer = obj.get("customer")
        subscription = obj.get("subscription")
        if isinstance(customer, str):
            practice.stripe_customer_id = customer
        if isinstance(subscription, str):
            practice.stripe_subscription_id = subscription
            # a completed checkout with a trial carried over reports trialing
            practice.subscription_status = (
                "trialing"
                if obj.get("status") != "complete"
                or obj.get("payment_status") == "unpaid"
                else "active"
            )
            try:
                practice.subscription_status = _request(
                    "GET", f"/subscriptions/{subscription}"
                )["status"]
            except StripeNotConfigured:
                pass
            except Exception:
                log.warning("could not fetch subscription status", exc_info=True)
    else:
        practice.stripe_subscription_id = (
            obj.get("id") or practice.stripe_subscription_id
        )
        if isinstance(obj.get("customer"), str):
            practice.stripe_customer_id = obj["customer"]
        practice.subscription_status = (
            "canceled" if event_type.endswith("deleted") else obj.get("status")
        )

    practice.save()
    log.info(
        "stripe event mirrored",
        event_type=event_type,
        practice_id=str(practice.id),
        subscription_status=practice.subscription_status,
    )
    return True
