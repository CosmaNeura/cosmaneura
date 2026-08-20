"""Unit tests for the Stripe billing service: webhook signature verification
and mirroring verified events onto the practice (ADR 0017 billing v2)."""

import hashlib
import hmac
import json
import secrets
import time

import pytest

import app.configuration.stripe as stripe_config
from app.services import stripe_billing

from app.models.practice import Practice

WEBHOOK_SECRET = "whsec_test_secret"


def _sign(
    payload: bytes, *, secret: str = WEBHOOK_SECRET, at: int | None = None
) -> str:
    timestamp = at if at is not None else int(time.time())
    digest = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v1={digest}"


@pytest.fixture
def webhook_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stripe_config, "STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET)


@pytest.mark.usefixtures("webhook_secret")
def test_signature_accepts_valid_header():
    payload = b'{"id":"evt_1"}'
    assert stripe_billing.verify_webhook_signature(payload, _sign(payload))


@pytest.mark.usefixtures("webhook_secret")
def test_signature_rejects_wrong_digest_and_tampered_payload():
    payload = b'{"id":"evt_1"}'
    ts = int(time.time())
    assert not stripe_billing.verify_webhook_signature(payload, f"t={ts},v1={'0' * 64}")
    assert not stripe_billing.verify_webhook_signature(
        b'{"id":"evt_TAMPERED"}', _sign(payload)
    )


@pytest.mark.usefixtures("webhook_secret")
def test_signature_rejects_replays_outside_tolerance():
    payload = b'{"id":"evt_1"}'
    stale = int(time.time()) - stripe_billing.SIGNATURE_TOLERANCE_SECONDS - 1
    assert not stripe_billing.verify_webhook_signature(
        payload, _sign(payload, at=stale)
    )


@pytest.mark.usefixtures("webhook_secret")
def test_signature_rejects_malformed_headers():
    payload = b"{}"
    assert not stripe_billing.verify_webhook_signature(payload, None)
    assert not stripe_billing.verify_webhook_signature(payload, "")
    assert not stripe_billing.verify_webhook_signature(payload, "v1=abc")
    assert not stripe_billing.verify_webhook_signature(payload, "t=notanumber,v1=abc")


def test_signature_rejects_everything_without_secret(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(stripe_config, "STRIPE_WEBHOOK_SECRET", "")
    payload = b'{"id":"evt_1"}'
    assert not stripe_billing.verify_webhook_signature(payload, _sign(payload))


def _practice() -> Practice:
    return Practice(
        legal_name="Mirror Test Practice, PLLC",
        join_code=f"M{secrets.token_hex(4).upper()[:7]}",
        email_domain="example.com",
        origin_channel="direct",
    ).save()


def test_apply_subscription_updated_mirrors_status():
    practice = _practice()
    event = {
        "id": "evt_test_1",
        "type": "customer.subscription.updated",
        "data": {
            "object": {
                "id": "sub_test_123",
                "customer": "cus_test_123",
                "status": "active",
                "metadata": {"practice_id": str(practice.id)},
            }
        },
    }

    assert stripe_billing.apply_stripe_event(event) is True

    practice.refresh()
    assert practice.subscription_status == "active"
    assert practice.stripe_subscription_id == "sub_test_123"
    assert practice.stripe_customer_id == "cus_test_123"
    assert practice.entitled()


def test_apply_subscription_deleted_revokes_entitlement():
    practice = _practice()
    practice.stripe_subscription_id = "sub_test_456"
    practice.subscription_status = "active"
    practice.save()

    # deletions carry no practice metadata; lookup falls back to the sub id
    event = {
        "id": "evt_test_2",
        "type": "customer.subscription.deleted",
        "data": {"object": {"id": "sub_test_456", "status": "canceled"}},
    }

    assert stripe_billing.apply_stripe_event(event) is True

    practice.refresh()
    assert practice.subscription_status == "canceled"
    assert not practice.entitled()


def test_apply_checkout_completed_records_ids():
    practice = _practice()
    event = {
        "id": "evt_test_3",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test_1",
                "client_reference_id": str(practice.id),
                "customer": "cus_test_789",
                "subscription": "sub_test_789",
                "status": "complete",
                "payment_status": "paid",
            }
        },
    }

    # Stripe is unconfigured in tests, so the post-checkout status fetch is
    # skipped and the session-derived status stands
    assert stripe_billing.apply_stripe_event(event) is True

    practice.refresh()
    assert practice.stripe_customer_id == "cus_test_789"
    assert practice.stripe_subscription_id == "sub_test_789"
    assert practice.subscription_status == "active"


def test_apply_ignores_unknown_practices_and_types():
    assert (
        stripe_billing.apply_stripe_event(
            {
                "id": "evt_test_4",
                "type": "customer.subscription.updated",
                "data": {"object": {"id": "sub_nobody", "status": "active"}},
            }
        )
        is False
    )
    assert (
        stripe_billing.apply_stripe_event(
            {"id": "evt_test_5", "type": "invoice.finalized", "data": {"object": {}}}
        )
        is False
    )


@pytest.mark.usefixtures("webhook_secret")
def test_signature_rejects_header_signed_with_another_secret():
    event = {"id": "evt_test_6", "type": "customer.subscription.updated"}
    payload = json.dumps(event).encode()
    header = _sign(payload, secret="whsec_other")
    assert not stripe_billing.verify_webhook_signature(payload, header)
