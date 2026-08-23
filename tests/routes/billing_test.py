"""Billing route, gate, rollout, and webhook integration tests."""

import hashlib
import hmac
import json
import secrets
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import status
from fastapi.testclient import TestClient

import app.configuration.stripe as stripe_config
import app.jobs.stripe_events
import app.services.stripe_billing as stripe_billing
from app.server import api_app
from app.services.organization_access import (
    RosterApprovalInput,
    claim_roster_access,
    configure_provider_organization,
    import_roster_approvals,
)

from app.models.doctor import Doctor
from app.models.pilot_access_grandfather_grant import PilotAccessGrandfatherGrant
from app.models.practice import Practice, PracticeMember
from app.models.stripe_event_inbox import StripeEventInbox

from tests.routes.utils import get_clerk_dev_user

STRIPE_WEBHOOK_PATH = "/external/v1/webhooks/stripe"
WEBHOOK_SECRET = "whsec_test_secret"
VALID_ORIGIN = "https://cosma.localhost"


class RouteStripe:
    def __init__(self) -> None:
        self.subscriptions: dict[str, dict] = {}
        self.price = {
            "id": "price_test_x",
            "active": True,
            "livemode": False,
            "type": "recurring",
            "currency": "usd",
            "unit_amount": 8900,
            "billing_scheme": "per_unit",
            "transform_quantity": None,
            "recurring": {
                "interval": "month",
                "interval_count": 1,
                "usage_type": "licensed",
            },
        }
        self.calls: list[tuple[str, str, dict | None, str | None]] = []

    def __call__(
        self,
        method: str,
        path: str,
        data: dict | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> dict:
        self.calls.append((method, path, data, idempotency_key))
        if path == "/account":
            return {"id": "acct_test_cosmaneura"}
        if path.startswith("/prices/"):
            return deepcopy(self.price)
        if path.startswith("/subscriptions/"):
            return deepcopy(self.subscriptions[path.rsplit("/", 1)[-1]])
        if path == "/checkout/sessions":
            assert data
            return {
                "id": "cs_route_test",
                "url": "https://checkout.stripe.com/c/pay/route-test",
                "expires_at": int(
                    (datetime.now(timezone.utc) + timedelta(hours=24)).timestamp()
                ),
                "client_reference_id": data["client_reference_id"],
                "livemode": False,
                "mode": "subscription",
                "status": "open",
                "subscription": None,
            }
        if path == "/billing_portal/sessions":
            return {"url": "https://billing.stripe.com/p/session/route-test"}
        raise AssertionError(f"unexpected Stripe request: {method} {path}")


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


def _lapse(practice: Practice, *, expired_comp: bool = False) -> None:
    practice.billing_exempt = expired_comp
    practice.billing_exempt_until = (
        datetime.now(timezone.utc) - timedelta(days=1) if expired_comp else None
    )
    practice.trial_ends_at = datetime.now(timezone.utc) - timedelta(days=1)
    practice.subscription_status = None
    practice.save()


def _subscription(practice: Practice, *, status_value: str) -> dict:
    return {
        "id": "sub_route_test",
        "customer": "cus_route_test",
        "status": status_value,
        "livemode": False,
        "metadata": {"practice_id": str(practice.id)},
        "items": {
            "data": [
                {
                    "id": "si_route_test",
                    "quantity": 1,
                    "price": {"id": "price_test_x"},
                }
            ]
        },
    }


@pytest.fixture
def stripe_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stripe_config, "STRIPE_INTEGRATION_ENABLED", True)
    monkeypatch.setattr(stripe_config, "STRIPE_BILLING_ENABLED", True)
    monkeypatch.setattr(stripe_config, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(stripe_config, "STRIPE_ACCOUNT_ID", "acct_test_cosmaneura")
    monkeypatch.setattr(stripe_config, "STRIPE_PRICE_ID", "price_test_x")
    monkeypatch.setattr(stripe_config, "STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setattr(stripe_config, "STRIPE_API_VERSION", "2025-09-30.clover")
    monkeypatch.setattr(
        stripe_config, "SUPPORTED_STRIPE_API_VERSION", "2025-09-30.clover"
    )
    monkeypatch.setattr(stripe_config, "STRIPE_LIVE_MODE", False)


@pytest.fixture
def route_stripe(monkeypatch: pytest.MonkeyPatch) -> RouteStripe:
    fake = RouteStripe()
    monkeypatch.setattr(stripe_billing, "_request", fake)
    return fake


def _sign(payload: bytes, *, secret: str = WEBHOOK_SECRET) -> str:
    timestamp = int(time.time())
    digest = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v1={digest}"


def test_feature_disabled_is_visible_and_billing_routes_return_503(
    authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(stripe_config, "STRIPE_BILLING_ENABLED", False)
    practice = _own_practice(_dev_doctor())
    _lapse(practice)

    practice_status = authenticated_client.get(api_app.url_path_for("practice_status"))
    assert practice_status.status_code == status.HTTP_200_OK
    assert practice_status.json()["billing_enabled"] is False

    checkout = authenticated_client.post(
        api_app.url_path_for("create_billing_checkout"),
        json={},
        headers={"origin": VALID_ORIGIN},
    )
    assert checkout.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


@pytest.mark.usefixtures("stripe_configured")
def test_acquisition_kill_switch_keeps_existing_portal_and_webhooks_alive(
    authenticated_client: TestClient,
    client: TestClient,
    route_stripe: RouteStripe,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(stripe_config, "STRIPE_BILLING_ENABLED", False)
    practice = _own_practice(_dev_doctor())
    _lapse(practice)
    subscription = _subscription(practice, status_value="past_due")
    route_stripe.subscriptions[subscription["id"]] = subscription
    practice.stripe_customer_id = subscription["customer"]
    practice.stripe_subscription_id = subscription["id"]
    practice.subscription_status = "past_due"
    practice.save()

    body = authenticated_client.get(api_app.url_path_for("practice_status")).json()
    assert body["billing_enabled"] is False
    assert body["billing_management_enabled"] is True
    portal = authenticated_client.post(
        api_app.url_path_for("create_billing_portal"),
        json={},
        headers={"origin": VALID_ORIGIN},
    )
    assert portal.status_code == status.HTTP_200_OK
    checkout = authenticated_client.post(
        api_app.url_path_for("create_billing_checkout"),
        json={},
        headers={"origin": VALID_ORIGIN},
    )
    assert checkout.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

    subscription["status"] = "canceled"
    route_stripe.subscriptions[subscription["id"]] = subscription
    remote_calls_before_webhook = len(route_stripe.calls)
    monkeypatch.setattr(
        app.jobs.stripe_events.process,
        "delay",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("broker down")),
    )
    event_id = f"evt_{secrets.token_hex(8)}"
    event_body = json.dumps(
        {
            "id": event_id,
            "type": "customer.subscription.deleted",
            "created": int(time.time()),
            "api_version": "2025-09-30.clover",
            "livemode": False,
            "data": {"object": subscription},
        }
    ).encode()
    webhook = client.post(
        STRIPE_WEBHOOK_PATH,
        content=event_body,
        headers={
            "content-type": "application/json",
            "stripe-signature": _sign(event_body),
        },
    )
    assert webhook.status_code == status.HTTP_200_OK
    practice.refresh()
    assert practice.subscription_status == "pending_verification"
    assert len(route_stripe.calls) == remote_calls_before_webhook
    inbox = StripeEventInbox.get(event_id=event_id)
    assert inbox and inbox.state == "pending"

    succeeded, failed = stripe_billing.reconcile_pending_stripe_events()
    assert (succeeded, failed) == (1, 0)
    inbox.refresh()
    assert inbox.state == "processed"
    practice.refresh()
    assert practice.subscription_status == "canceled"


@pytest.mark.usefixtures("stripe_configured")
def test_expired_comp_is_reported_inactive_and_can_reactivate(
    authenticated_client: TestClient,
    route_stripe: RouteStripe,
):
    practice = _own_practice(_dev_doctor())
    _lapse(practice, expired_comp=True)

    practice_status = authenticated_client.get(api_app.url_path_for("practice_status"))
    body = practice_status.json()
    assert body["billing_exempt"] is True
    assert body["billing_exempt_active"] is False
    assert body["entitled"] is False
    assert body["billing_enabled"] is True

    checkout = authenticated_client.post(
        api_app.url_path_for("create_billing_checkout"),
        json={},
        headers={"origin": VALID_ORIGIN},
    )
    assert checkout.status_code == status.HTTP_200_OK, checkout.text
    assert checkout.json()["url"].startswith("https://checkout.stripe.com/")
    assert any(call[1] == "/checkout/sessions" for call in route_stripe.calls)


def test_entitlement_exemption_does_not_bypass_baa_or_attestation(
    authenticated_client: TestClient,
):
    doctor = _dev_doctor()
    practice = _own_practice(doctor)
    practice.baa_accepted_at = None
    practice.negotiated_baa_on_file = False
    practice.save()

    checkout = authenticated_client.post(
        api_app.url_path_for("create_billing_checkout"),
        json={},
        headers={"origin": VALID_ORIGIN},
    )
    assert checkout.status_code == status.HTTP_403_FORBIDDEN


def test_billing_is_owner_only(authenticated_client: TestClient):
    doctor = _dev_doctor()
    member = PracticeMember.get(doctor_id=doctor.id)
    assert member
    member.role = "workforce"
    member.save()

    response = authenticated_client.post(
        api_app.url_path_for("create_billing_checkout"),
        json={},
        headers={"origin": VALID_ORIGIN},
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.usefixtures("stripe_configured")
def test_active_organization_billing_exemption_blocks_checkout(
    authenticated_client: TestClient,
    route_stripe: RouteStripe,
):
    doctor = _dev_doctor()
    practice = _own_practice(doctor)
    domain = f"billing-{secrets.token_hex(5)}.example.test"
    doctor.email = f"billing-owner@{domain}"
    doctor.save()
    practice.email_domain = domain
    now = datetime.now(timezone.utc)
    practice.negotiated_baa_on_file = True
    practice.negotiated_baa_effective_date = now - timedelta(days=1)
    practice.negotiated_baa_reference = "test://billing-managed-baa"
    practice.billing_exempt = True
    practice.billing_exempt_until = None
    practice.save()
    _, code, _ = configure_provider_organization(
        practice,
        legal_name="Billing Managed Organization",
        slug=f"billing-managed-{secrets.token_hex(4)}",
        email_domain=practice.email_domain,
        operator_ref="test-billing-managed",
        reason="Test organization-managed billing guard",
        now=now,
    )
    import_roster_approvals(
        practice,
        [RosterApprovalInput(email=doctor.email or "")],
        operator_ref="test-billing-managed",
        reason="Authorize billing guard test owner",
        source_ref="test://billing-managed-roster",
        source_sha256="8" * 64,
        now=now,
    )
    claim_roster_access(doctor, supplied_code=code, now=now)

    response = authenticated_client.post(
        api_app.url_path_for("create_billing_checkout"),
        json={},
        headers={"origin": VALID_ORIGIN},
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert "managed by its organization" in response.json()["detail"]
    assert route_stripe.calls == []


@pytest.mark.usefixtures("stripe_configured")
def test_active_pilot_grant_blocks_new_checkout_without_hiding_portal(
    authenticated_client: TestClient,
    route_stripe: RouteStripe,
    monkeypatch,
):
    doctor = _dev_doctor()
    practice = _own_practice(doctor)
    _lapse(practice)
    email = (doctor.email or "").strip().lower()
    PilotAccessGrandfatherGrant(
        clerk_id=doctor.clerk_id,
        doctor_id_snapshot=doctor.id,
        practice_id_snapshot=practice.id,
        email_snapshot=email,
        email_sha256=hashlib.sha256(email.encode()).hexdigest(),
        source_ref="test:billing-route",
        source_sha256="a" * 64,
        grant_plan_sha256="b" * 64,
        granted_by_ref="test-suite",
        grant_reason="Test pilot billing bypass",
        granted_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    ).save()
    monkeypatch.setattr(
        "app.routes.dependencies.doctor._fetch_clerk_email",
        lambda _clerk_id: email,
    )

    checkout = authenticated_client.post(
        api_app.url_path_for("create_billing_checkout"),
        json={},
        headers={"origin": VALID_ORIGIN},
    )

    assert checkout.status_code == status.HTTP_409_CONFLICT
    assert "does not require a new subscription" in checkout.json()["detail"]
    assert route_stripe.calls == []


@pytest.mark.usefixtures("stripe_configured")
def test_past_due_subscription_uses_portal_instead_of_duplicate_checkout(
    authenticated_client: TestClient,
    route_stripe: RouteStripe,
):
    practice = _own_practice(_dev_doctor())
    _lapse(practice)
    subscription = _subscription(practice, status_value="past_due")
    route_stripe.subscriptions[subscription["id"]] = subscription
    practice.stripe_customer_id = subscription["customer"]
    practice.stripe_subscription_id = subscription["id"]
    practice.subscription_status = "past_due"
    practice.save()

    checkout = authenticated_client.post(
        api_app.url_path_for("create_billing_checkout"),
        json={},
        headers={"origin": VALID_ORIGIN},
    )
    assert checkout.status_code == status.HTTP_409_CONFLICT
    assert not any(call[1] == "/checkout/sessions" for call in route_stripe.calls)

    portal = authenticated_client.post(
        api_app.url_path_for("create_billing_portal"),
        json={},
        headers={"origin": VALID_ORIGIN},
    )
    assert portal.status_code == status.HTTP_200_OK


@pytest.mark.usefixtures("stripe_configured")
def test_final_48_hours_return_truthful_deferred_checkout(
    authenticated_client: TestClient,
    route_stripe: RouteStripe,
):
    practice = _own_practice(_dev_doctor())
    practice.billing_exempt = False
    practice.trial_ends_at = datetime.now(timezone.utc) + timedelta(hours=47)
    practice.save()

    checkout = authenticated_client.post(
        api_app.url_path_for("create_billing_checkout"),
        json={},
        headers={"origin": VALID_ORIGIN},
    )
    assert checkout.status_code == status.HTTP_409_CONFLICT
    assert "no trial time will be shortened" in checkout.json()["detail"]
    assert not any(call[1] == "/checkout/sessions" for call in route_stripe.calls)


@pytest.mark.usefixtures("stripe_configured")
def test_checkout_requires_recognized_origin(authenticated_client: TestClient):
    response = authenticated_client.post(
        api_app.url_path_for("create_billing_checkout"),
        json={},
        headers={"origin": "https://evil.example.com"},
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.usefixtures("stripe_configured")
def test_portal_conflicts_without_billing_account(authenticated_client: TestClient):
    response = authenticated_client.post(
        api_app.url_path_for("create_billing_portal"),
        json={},
        headers={"origin": VALID_ORIGIN},
    )
    assert response.status_code == status.HTTP_409_CONFLICT


def test_stripe_webhook_unavailable_until_full_rollout_config(client: TestClient):
    response = client.post(
        STRIPE_WEBHOOK_PATH,
        content=b'{"id":"evt_x","type":"customer.subscription.updated"}',
        headers={"content-type": "application/json"},
    )
    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


@pytest.mark.usefixtures("stripe_configured")
def test_stripe_webhook_rejects_bad_signature(client: TestClient):
    body = b'{"id":"evt_x","type":"customer.subscription.updated"}'
    response = client.post(
        STRIPE_WEBHOOK_PATH,
        content=body,
        headers={
            "content-type": "application/json",
            "stripe-signature": f"t={int(time.time())},v1={'0' * 64}",
        },
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.usefixtures("stripe_configured")
def test_stripe_webhook_mirrors_fresh_state_and_dedupes(
    client: TestClient,
    route_stripe: RouteStripe,
):
    practice = Practice(
        legal_name="Webhook Test Practice, PLLC",
        join_code=f"W{secrets.token_hex(4).upper()[:7]}",
        email_domain="example.com",
        origin_channel="direct",
    ).save()
    subscription = _subscription(practice, status_value="active")
    route_stripe.subscriptions[subscription["id"]] = subscription
    event_id = f"evt_{secrets.token_hex(8)}"
    body = json.dumps(
        {
            "id": event_id,
            "type": "customer.subscription.updated",
            "created": int(time.time()),
            "api_version": "2025-09-30.clover",
            "livemode": False,
            "data": {"object": subscription},
        }
    ).encode()

    response = client.post(
        STRIPE_WEBHOOK_PATH,
        content=body,
        headers={
            "content-type": "application/json",
            "stripe-signature": _sign(body),
        },
    )
    assert response.status_code == status.HTTP_200_OK, response.text
    inbox = StripeEventInbox.get(event_id=event_id)
    assert inbox and inbox.state == "pending"
    assert stripe_billing.process_stripe_event_inbox(inbox.id)
    practice.refresh()
    assert practice.subscription_status == "active"
    assert practice.entitled()

    again = client.post(
        STRIPE_WEBHOOK_PATH,
        content=body,
        headers={
            "content-type": "application/json",
            "stripe-signature": _sign(body),
        },
    )
    assert again.status_code == status.HTTP_200_OK
    assert again.json()["duplicate"] is True
