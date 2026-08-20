"""Billing endpoints and the Stripe webhook route.

The invariants that matter: a lapsed practice can always reach checkout and
the portal (the 402 gate exempts them), only owners manage billing, nothing
is accepted from Stripe without a verified signature, and the webhook mirror
updates the practice the event references.
"""

import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import status
from fastapi.testclient import TestClient

import app.configuration.stripe as stripe_config
import app.services.stripe_billing as stripe_billing
from app.server import api_app

from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember

from tests.routes.utils import get_clerk_dev_user

STRIPE_WEBHOOK_PATH = "/external/v1/webhooks/stripe"
WEBHOOK_SECRET = "whsec_test_secret"
VALID_ORIGIN = "https://cosma.localhost"


def _dev_doctor() -> Doctor:
    _, _, clerk_user = get_clerk_dev_user()
    doctor = Doctor.get(clerk_id=clerk_user.id)
    assert doctor
    return doctor


def _own_practice(doctor: Doctor) -> Practice:
    member = PracticeMember.get(doctor_id=doctor.id)
    assert member
    practice = Practice.get(id=member.practice_id)
    assert practice
    return practice


def _lapse(practice: Practice) -> None:
    "end the pilot comp and the trial: the 402 gate closes"
    practice.billing_exempt = False
    practice.trial_ends_at = datetime.now(timezone.utc) - timedelta(days=1)
    practice.save()


@pytest.fixture
def stripe_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stripe_config, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(stripe_config, "STRIPE_PRICE_ID", "price_test_x")


def test_lapsed_practice_reaches_checkout_but_gets_503_unconfigured(
    authenticated_client: TestClient,
):
    """Two things at once: the practice gate exempts checkout (a lapsed
    practice would otherwise see 402 everywhere), and without Stripe
    credentials the endpoint declines with 503 instead of erroring."""

    practice = _own_practice(_dev_doctor())
    _lapse(practice)

    gated = authenticated_client.get(api_app.url_path_for("note_list"))
    assert gated.status_code == status.HTTP_402_PAYMENT_REQUIRED

    resp = authenticated_client.post(
        api_app.url_path_for("create_billing_checkout"),
        json={},
        headers={"origin": VALID_ORIGIN},
    )
    assert resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

    portal = authenticated_client.post(
        api_app.url_path_for("create_billing_portal"),
        json={},
        headers={"origin": VALID_ORIGIN},
    )
    assert portal.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


def test_billing_is_owner_only(authenticated_client: TestClient):
    doctor = _dev_doctor()
    member = PracticeMember.get(doctor_id=doctor.id)
    assert member
    member.role = "workforce"
    member.save()

    resp = authenticated_client.post(
        api_app.url_path_for("create_billing_checkout"),
        json={},
        headers={"origin": VALID_ORIGIN},
    )
    assert resp.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.usefixtures("stripe_configured")
def test_checkout_conflicts_when_already_subscribed(
    authenticated_client: TestClient,
):
    practice = _own_practice(_dev_doctor())
    practice.subscription_status = "active"
    practice.save()

    resp = authenticated_client.post(
        api_app.url_path_for("create_billing_checkout"),
        json={},
        headers={"origin": VALID_ORIGIN},
    )
    assert resp.status_code == status.HTTP_409_CONFLICT


@pytest.mark.usefixtures("stripe_configured")
def test_checkout_requires_recognized_origin(authenticated_client: TestClient):
    resp = authenticated_client.post(
        api_app.url_path_for("create_billing_checkout"),
        json={},
        headers={"origin": "https://evil.example.com"},
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.usefixtures("stripe_configured")
def test_checkout_builds_stripe_session_from_practice(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    practice = _own_practice(_dev_doctor())
    practice.trial_ends_at = datetime.now(timezone.utc) + timedelta(days=10)
    practice.billing_exempt = False
    practice.save()

    captured: dict = {}

    def fake_request(method: str, path: str, data=None):
        captured["method"] = method
        captured["path"] = path
        captured["data"] = data
        return {"url": "https://checkout.stripe.com/c/pay/test"}

    monkeypatch.setattr(stripe_billing, "_request", fake_request)

    resp = authenticated_client.post(
        api_app.url_path_for("create_billing_checkout"),
        json={"success_path": "/settings?billing=success", "cancel_path": "//evil"},
        headers={"origin": VALID_ORIGIN},
    )
    assert resp.status_code == status.HTTP_200_OK, resp.text
    assert resp.json()["url"] == "https://checkout.stripe.com/c/pay/test"

    data = captured["data"]
    assert captured["path"] == "/checkout/sessions"
    assert data["client_reference_id"] == str(practice.id)
    assert data["success_url"] == f"{VALID_ORIGIN}/settings?billing=success"
    # a protocol-relative cancel path may not escape our origin
    assert data["cancel_url"] == f"{VALID_ORIGIN}/settings"
    # one attested clinician (the owner) → quantity 1
    assert data["line_items[0][quantity]"] == "1"
    # the running trial is carried into Stripe so paying early costs nothing
    assert data["subscription_data[trial_end]"] == str(
        int(practice.trial_ends_at.timestamp())
    )
    assert data["allow_promotion_codes"] == "true"
    assert data["payment_method_collection"] == "if_required"


@pytest.mark.usefixtures("stripe_configured")
def test_portal_conflicts_without_billing_account(
    authenticated_client: TestClient,
):
    resp = authenticated_client.post(
        api_app.url_path_for("create_billing_portal"),
        json={},
        headers={"origin": VALID_ORIGIN},
    )
    assert resp.status_code == status.HTTP_409_CONFLICT


def _sign(payload: bytes, *, secret: str = WEBHOOK_SECRET) -> str:
    timestamp = int(time.time())
    digest = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v1={digest}"


@pytest.fixture
def webhook_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stripe_config, "STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET)


def test_stripe_webhook_unavailable_without_secret(client: TestClient):
    "billing events are never accepted unverifiable"
    resp = client.post(
        STRIPE_WEBHOOK_PATH,
        content=b'{"id":"evt_x","type":"customer.subscription.updated"}',
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


@pytest.mark.usefixtures("webhook_secret")
def test_stripe_webhook_rejects_bad_signature(client: TestClient):
    body = b'{"id":"evt_x","type":"customer.subscription.updated"}'
    resp = client.post(
        STRIPE_WEBHOOK_PATH,
        content=body,
        headers={
            "content-type": "application/json",
            "stripe-signature": f"t={int(time.time())},v1={'0' * 64}",
        },
    )
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    missing = client.post(
        STRIPE_WEBHOOK_PATH,
        content=body,
        headers={"content-type": "application/json"},
    )
    assert missing.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.usefixtures("webhook_secret")
def test_stripe_webhook_mirrors_event_and_dedupes(client: TestClient):
    practice = Practice(
        legal_name="Webhook Test Practice, PLLC",
        join_code=f"W{secrets.token_hex(4).upper()[:7]}",
        email_domain="example.com",
        origin_channel="direct",
    ).save()

    event_id = f"evt_{secrets.token_hex(8)}"
    body = json.dumps(
        {
            "id": event_id,
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": "sub_hook_1",
                    "customer": "cus_hook_1",
                    "status": "active",
                    "metadata": {"practice_id": str(practice.id)},
                }
            },
        }
    ).encode()

    resp = client.post(
        STRIPE_WEBHOOK_PATH,
        content=body,
        headers={
            "content-type": "application/json",
            "stripe-signature": _sign(body),
        },
    )
    assert resp.status_code == status.HTTP_200_OK, resp.text
    assert resp.json() == {"status": "ok", "duplicate": False}

    practice.refresh()
    assert practice.subscription_status == "active"
    assert practice.stripe_subscription_id == "sub_hook_1"
    assert practice.stripe_customer_id == "cus_hook_1"
    assert practice.entitled()

    # Stripe retries are acknowledged without reprocessing
    again = client.post(
        STRIPE_WEBHOOK_PATH,
        content=body,
        headers={
            "content-type": "application/json",
            "stripe-signature": _sign(body),
        },
    )
    assert again.status_code == status.HTTP_200_OK
    assert again.json() == {"status": "ok", "duplicate": True}
