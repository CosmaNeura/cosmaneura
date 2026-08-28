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
from structlog.testing import capture_logs

import app.configuration.stripe as stripe_config
import app.jobs.stripe_events
import app.services.stripe_billing as stripe_billing
from app.routes import webhooks
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
        self.sessions: dict[str, dict] = {}
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
            session = {
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
                "customer": data.get("customer"),
                "payment_method_collection": data["payment_method_collection"],
                "metadata": {
                    "checkout_policy": data["metadata[checkout_policy]"],
                    "checkout_attempt": data["metadata[checkout_attempt]"],
                    "practice_id": data["metadata[practice_id]"],
                    "entitlement_contract": data["metadata[entitlement_contract]"],
                    "trial_clock": data["metadata[trial_clock]"],
                    **(
                        {"trial_days": data["metadata[trial_days]"]}
                        if "metadata[trial_days]" in data
                        else {}
                    ),
                },
            }
            self.sessions[session["id"]] = deepcopy(session)
            return session
        if path.startswith("/checkout/sessions/") and path.endswith("/expire"):
            session_id = path.removesuffix("/expire").rsplit("/", 1)[-1]
            session = self.sessions[session_id]
            session["status"] = "expired"
            session["url"] = None
            return deepcopy(session)
        if path.startswith("/checkout/sessions/"):
            return deepcopy(self.sessions[path.rsplit("/", 1)[-1]])
        if path == "/billing_portal/sessions":
            return {"url": "https://billing.stripe.com/p/session/route-test"}
        raise AssertionError(f"unexpected Stripe request: {method} {path}")


@pytest.mark.parametrize(
    ("task", "dependency"),
    [
        (app.jobs.stripe_events.process, "process_stripe_event_inbox"),
        (app.jobs.stripe_events.perform, "reconcile_pending_stripe_events"),
    ],
)
def test_stripe_worker_tasks_sanitize_unexpected_failures(
    monkeypatch: pytest.MonkeyPatch,
    task,
    dependency: str,
) -> None:
    def fail(*_args, **_kwargs):
        raise RuntimeError("sensitive provider failure")

    monkeypatch.setattr(app.jobs.stripe_events, dependency, fail)
    args = ("inbox_sensitive",) if dependency == "process_stripe_event_inbox" else ()

    with pytest.raises(stripe_billing.StripeEventProcessingDeferred) as raised:
        task.run(*args)

    assert str(raised.value) == "Stripe event processing is deferred for reconciliation"
    assert "sensitive" not in str(raised.value).lower()


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
        "metadata": {
            "practice_id": str(practice.id),
            "checkout_policy": stripe_billing.CHECKOUT_IDEMPOTENCY_POLICY_VERSION,
            "entitlement_contract": "public-capability-entitlements.v1",
            "trial_clock": stripe_billing.LEGACY_TRIAL_CLOCK_POLICY,
        },
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
    monkeypatch.setattr(
        stripe_config, "STRIPE_PORTAL_CONFIGURATION_ID", "bpc_test_restricted"
    )
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
    practice.stripe_subscription_entitlement_contract = subscription["metadata"][
        "entitlement_contract"
    ]
    practice.subscription_status = "past_due"
    practice.stripe_state_version = 1
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

    settings = authenticated_client.get(api_app.url_path_for("settings_data"))
    assert settings.status_code == status.HTTP_200_OK
    assert settings.json()["practice_ready"] is True
    assert settings.json()["billing_recovery_required"] is True

    checkout = authenticated_client.post(
        api_app.url_path_for("create_billing_checkout"),
        json={},
        headers={"origin": VALID_ORIGIN},
    )
    assert checkout.status_code == status.HTTP_200_OK, checkout.text
    assert checkout.json()["url"].startswith("https://checkout.stripe.com/")
    checkout_call = next(
        call for call in route_stripe.calls if call[1] == "/checkout/sessions"
    )
    assert checkout_call[2]
    assert checkout_call[2]["success_url"] == (
        f"{VALID_ORIGIN}/settings?billing=success"
    )
    assert checkout_call[2]["cancel_url"] == (
        f"{VALID_ORIGIN}/settings?billing=cancelled"
    )




@pytest.mark.usefixtures("stripe_configured")
def test_active_standalone_billing_exemption_blocks_checkout_before_stripe_io(
    authenticated_client: TestClient,
    route_stripe: RouteStripe,
):
    practice = _own_practice(_dev_doctor())
    practice.billing_exempt = True
    practice.billing_exempt_until = None
    practice.save()

    response = authenticated_client.post(
        api_app.url_path_for("create_billing_checkout"),
        json={},
        headers={"origin": VALID_ORIGIN},
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert "active billing exemption" in response.json()["detail"]
    assert route_stripe.calls == []


@pytest.mark.usefixtures("stripe_configured")
def test_legacy_incomplete_checkout_returns_retryable_conflict_then_recovers(
    authenticated_client: TestClient,
    route_stripe: RouteStripe,
):
    practice = _own_practice(_dev_doctor())
    _lapse(practice)
    subscription = _subscription(practice, status_value="incomplete")
    route_stripe.subscriptions[subscription["id"]] = subscription
    practice.stripe_customer_id = subscription["customer"]
    practice.stripe_subscription_id = subscription["id"]
    practice.stripe_subscription_entitlement_contract = subscription["metadata"][
        "entitlement_contract"
    ]
    practice.subscription_status = "incomplete"
    practice.stripe_state_version = 1
    practice.stripe_checkout_session_id = "cs_route_legacy_incomplete"
    practice.stripe_checkout_entitlement_contract = subscription["metadata"][
        "entitlement_contract"
    ]
    practice.stripe_checkout_session_url = (
        "https://checkout.stripe.com/c/pay/route-legacy-incomplete"
    )
    practice.stripe_checkout_session_expires_at = datetime.now(
        timezone.utc
    ) + timedelta(hours=1)
    practice.save()
    route_stripe.sessions["cs_route_legacy_incomplete"] = {
        "id": "cs_route_legacy_incomplete",
        "url": "https://checkout.stripe.com/c/pay/route-legacy-incomplete",
        "expires_at": int(
            (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()
        ),
        "client_reference_id": str(practice.id),
        "livemode": False,
        "mode": "subscription",
        "status": "open",
        "subscription": subscription["id"],
        "customer": subscription["customer"],
        "payment_method_collection": "if_required",
    }

    first = authenticated_client.post(
        api_app.url_path_for("create_billing_checkout"),
        json={},
        headers={"origin": VALID_ORIGIN},
    )

    assert first.status_code == status.HTTP_409_CONFLICT
    assert "still finalizing its incomplete subscription" in first.json()["detail"]
    practice.refresh()
    assert practice.stripe_subscription_id == subscription["id"]
    assert practice.subscription_status == "incomplete"
    assert route_stripe.sessions["cs_route_legacy_incomplete"]["status"] == "expired"
    assert not any(call[1] == "/checkout/sessions" for call in route_stripe.calls)

    # Production's isolated billing transaction commits on its own connection.
    # The test harness deliberately binds all sessions to one rollback-only
    # connection, so the HTTPException rolls that local cache clear back. The
    # service-level regression proves the committed transition; mirror it here
    # to exercise the next complete API request through its authoritative
    # subscription refresh.
    practice.stripe_checkout_session_id = None
    practice.stripe_checkout_session_url = None
    practice.stripe_checkout_session_expires_at = None
    practice.stripe_checkout_attempt += 1
    practice.stripe_checkout_state_version += 1
    practice.save()
    route_stripe.calls.clear()
    still_pending = authenticated_client.post(
        api_app.url_path_for("create_billing_checkout"),
        json={},
        headers={"origin": VALID_ORIGIN},
    )

    assert still_pending.status_code == status.HTTP_409_CONFLICT
    assert (
        "still finalizing its incomplete subscription" in still_pending.json()["detail"]
    )
    assert any(
        call[1] == f"/subscriptions/{subscription['id']}" for call in route_stripe.calls
    )
    assert not any(call[1] == "/checkout/sessions" for call in route_stripe.calls)

    route_stripe.subscriptions[subscription["id"]]["status"] = "incomplete_expired"
    route_stripe.calls.clear()
    recovered = authenticated_client.post(
        api_app.url_path_for("create_billing_checkout"),
        json={},
        headers={"origin": VALID_ORIGIN},
    )

    assert recovered.status_code == status.HTTP_200_OK, recovered.text
    assert recovered.json()["url"] == "https://checkout.stripe.com/c/pay/route-test"
    assert any(
        call[1] == f"/subscriptions/{subscription['id']}" for call in route_stripe.calls
    )
    assert (
        len([call for call in route_stripe.calls if call[1] == "/checkout/sessions"])
        == 1
    )


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
    monkeypatch: pytest.MonkeyPatch,
):
    doctor = _dev_doctor()
    practice = _own_practice(doctor)
    domain = f"billing-{secrets.token_hex(5)}.example.test"
    doctor.email = f"billing-owner@{domain}"
    doctor.save()
    monkeypatch.setattr(
        "app.routes.dependencies.doctor.verified_primary_email",
        lambda _clerk_id: doctor.email,
    )
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
def test_managed_practice_blocks_checkout_without_active_billing_exemption(
    authenticated_client: TestClient,
    route_stripe: RouteStripe,
):
    doctor = _dev_doctor()
    practice = _own_practice(doctor)
    practice.baa_version = None
    practice.baa_accepted_at = None
    practice.baa_accepted_by_doctor_id = None
    practice.baa_signature_name = None
    practice.baa_signature_title = None
    practice.trial_ends_at = None
    practice.negotiated_baa_required = True
    practice.negotiated_baa_on_file = True
    practice.negotiated_baa_effective_date = datetime.now(timezone.utc) - timedelta(
        days=1
    )
    practice.negotiated_baa_reference = "test://managed-baa"
    practice.billing_exempt = False
    practice.billing_exempt_until = datetime.now(timezone.utc) - timedelta(days=1)
    practice.save()

    response = authenticated_client.post(
        api_app.url_path_for("create_billing_checkout"),
        json={},
        headers={"origin": VALID_ORIGIN},
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert "managed" in response.json()["detail"]
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
    practice.stripe_state_version = 1
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
def test_terminal_retired_price_can_start_replacement_checkout(
    authenticated_client: TestClient,
    route_stripe: RouteStripe,
):
    practice = _own_practice(_dev_doctor())
    _lapse(practice)
    subscription = _subscription(practice, status_value="canceled")
    subscription["items"]["data"][0]["price"] = {"id": "price_retired"}
    subscription["items"]["data"][0]["quantity"] = 37
    subscription["metadata"] = {"practice_id": str(practice.id)}
    route_stripe.subscriptions[subscription["id"]] = subscription
    practice.stripe_customer_id = subscription["customer"]
    practice.stripe_subscription_id = subscription["id"]
    practice.stripe_subscription_entitlement_contract = (
        stripe_billing.LEGACY_UNVERSIONED_STRIPE_CONTRACT
    )
    practice.subscription_status = "canceled"
    practice.stripe_state_version = 1
    practice.save()

    checkout = authenticated_client.post(
        api_app.url_path_for("create_billing_checkout"),
        json={},
        headers={"origin": VALID_ORIGIN},
    )

    assert checkout.status_code == status.HTTP_200_OK, checkout.text
    create_call = next(
        call for call in route_stripe.calls if call[1] == "/checkout/sessions"
    )
    assert create_call[2]
    assert create_call[2]["customer"] == subscription["customer"]


@pytest.mark.usefixtures("stripe_configured")
def test_stale_terminal_projection_cannot_resume_checkout_over_remote_active(
    authenticated_client: TestClient,
    route_stripe: RouteStripe,
):
    practice = _own_practice(_dev_doctor())
    _lapse(practice)
    subscription = _subscription(practice, status_value="active")
    route_stripe.subscriptions[subscription["id"]] = subscription
    practice.stripe_customer_id = subscription["customer"]
    practice.stripe_subscription_id = subscription["id"]
    practice.stripe_subscription_entitlement_contract = subscription["metadata"][
        "entitlement_contract"
    ]
    practice.subscription_status = "canceled"
    practice.stripe_state_version = 1
    practice.stripe_checkout_session_id = "cs_must_not_resume_over_remote_active"
    practice.stripe_checkout_entitlement_contract = subscription["metadata"][
        "entitlement_contract"
    ]
    practice.stripe_checkout_session_url = (
        "https://checkout.stripe.com/c/pay/must-not-resume"
    )
    practice.stripe_checkout_session_expires_at = datetime.now(
        timezone.utc
    ) + timedelta(hours=1)
    practice.save()

    checkout = authenticated_client.post(
        api_app.url_path_for("create_billing_checkout"),
        json={},
        headers={"origin": VALID_ORIGIN},
    )

    assert checkout.status_code == status.HTTP_409_CONFLICT
    assert not any(
        path.startswith("/checkout/sessions")
        for _method, path, _data, _key in route_stripe.calls
    )


@pytest.mark.usefixtures("stripe_configured")
def test_final_48_hours_checkout_extends_trial_without_charging_early(
    authenticated_client: TestClient,
    route_stripe: RouteStripe,
):
    practice = _own_practice(_dev_doctor())
    practice.billing_exempt = False
    requested_at = datetime.now(timezone.utc)
    practice.trial_ends_at = requested_at + timedelta(hours=47)
    practice.save()

    checkout = authenticated_client.post(
        api_app.url_path_for("create_billing_checkout"),
        json={},
        headers={"origin": VALID_ORIGIN},
    )
    assert checkout.status_code == status.HTTP_200_OK
    checkout_call = next(
        call for call in route_stripe.calls if call[1] == "/checkout/sessions"
    )
    assert checkout_call[2]
    stripe_trial_end = datetime.fromtimestamp(
        int(checkout_call[2]["subscription_data[trial_end]"]), tz=timezone.utc
    )
    assert stripe_trial_end >= practice.trial_ends_at
    assert stripe_trial_end >= (
        requested_at
        + stripe_billing.MINIMUM_CHECKOUT_TRIAL_LEAD
        + stripe_billing.CHECKOUT_TRIAL_LEAD_SAFETY
    )


@pytest.mark.usefixtures("stripe_configured")
def test_checkout_requires_recognized_origin(authenticated_client: TestClient):
    response = authenticated_client.post(
        api_app.url_path_for("create_billing_checkout"),
        json={},
        headers={"origin": "https://evil.example.com"},
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.usefixtures("stripe_configured")
def test_checkout_rejects_incomplete_core_binding_before_stripe_io(
    authenticated_client: TestClient,
    route_stripe: RouteStripe,
):
    practice = _own_practice(_dev_doctor())
    practice.billing_exempt = False
    practice.stripe_customer_id = "cus_unbound"
    practice.save()

    response = authenticated_client.post(
        api_app.url_path_for("create_billing_checkout"),
        json={},
        headers={"origin": VALID_ORIGIN},
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["detail"] == (
        "The Core billing account is incomplete and requires operator reconciliation."
    )
    assert route_stripe.calls == []


@pytest.mark.usefixtures("stripe_configured")
def test_checkout_rejects_status_only_core_projection_as_local_conflict(
    authenticated_client: TestClient,
    route_stripe: RouteStripe,
):
    practice = _own_practice(_dev_doctor())
    practice.billing_exempt = False
    practice.stripe_customer_id = None
    practice.stripe_subscription_id = None
    practice.subscription_status = "active"
    practice.save()

    response = authenticated_client.post(
        api_app.url_path_for("create_billing_checkout"),
        json={},
        headers={"origin": VALID_ORIGIN},
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["detail"] == (
        "The Core billing account is incomplete and requires operator reconciliation."
    )
    assert route_stripe.calls == []


@pytest.mark.usefixtures("stripe_configured")
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("success_path", "/settings?billing=success&patient=private"),
        ("success_path", "/settings?billing=success&returnTo=%2Fnotes"),
        ("cancel_path", "/settings#private"),
        ("success_path", "/\\evil.example/private"),
    ],
)
def test_checkout_rejects_noncanonical_provider_redirects_before_stripe_io(
    authenticated_client: TestClient,
    route_stripe: RouteStripe,
    field: str,
    value: str,
):
    response = authenticated_client.post(
        api_app.url_path_for("create_billing_checkout"),
        json={field: value},
        headers={"origin": VALID_ORIGIN},
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert route_stripe.calls == []


@pytest.mark.usefixtures("stripe_configured")
def test_portal_conflicts_without_billing_account(authenticated_client: TestClient):
    response = authenticated_client.post(
        api_app.url_path_for("create_billing_portal"),
        json={},
        headers={"origin": VALID_ORIGIN},
    )
    assert response.status_code == status.HTTP_409_CONFLICT


@pytest.mark.usefixtures("stripe_configured")
def test_portal_rejects_customer_only_binding_before_stripe_io(
    authenticated_client: TestClient,
    route_stripe: RouteStripe,
):
    practice = _own_practice(_dev_doctor())
    practice.stripe_customer_id = "cus_unbound"
    practice.save()

    response = authenticated_client.post(
        api_app.url_path_for("create_billing_portal"),
        json={},
        headers={"origin": VALID_ORIGIN},
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["detail"] == (
        "The Core billing account is incomplete and requires operator reconciliation."
    )
    assert route_stripe.calls == []


@pytest.mark.usefixtures("stripe_configured")
def test_portal_is_unavailable_without_webhook_verification(
    authenticated_client: TestClient,
    route_stripe: RouteStripe,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(stripe_config, "STRIPE_WEBHOOK_SECRET", "")

    response = authenticated_client.post(
        api_app.url_path_for("create_billing_portal"),
        json={},
        headers={"origin": VALID_ORIGIN},
    )

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert route_stripe.calls == []


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
def test_stripe_webhook_rejects_oversized_body_before_signature(
    client: TestClient,
):
    response = client.post(
        STRIPE_WEBHOOK_PATH,
        content=b"x" * (webhooks.MAX_WEBHOOK_BODY_BYTES + 1),
        headers={"stripe-signature": "untrusted"},
    )
    assert response.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE


@pytest.mark.usefixtures("stripe_configured")
def test_stripe_webhook_rejects_signed_non_object_json(client: TestClient):
    body = b"[]"
    response = client.post(
        STRIPE_WEBHOOK_PATH,
        content=body,
        headers={"stripe-signature": _sign(body)},
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.usefixtures("stripe_configured")
def test_stripe_webhook_rejects_signed_invalid_utf8(client: TestClient):
    body = b'"\xff"'
    response = client.post(
        STRIPE_WEBHOOK_PATH,
        content=body,
        headers={"stripe-signature": _sign(body)},
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.usefixtures("stripe_configured")
def test_stripe_webhook_rejects_boolean_event_timestamp(client: TestClient):
    body = json.dumps(
        {
            "id": "evt_boolean_created",
            "type": "customer.subscription.updated",
            "created": True,
            "api_version": "2025-09-30.clover",
            "livemode": False,
            "data": {"object": {"id": "sub_boolean_created"}},
        }
    ).encode()
    response = client.post(
        STRIPE_WEBHOOK_PATH,
        content=body,
        headers={"stripe-signature": _sign(body)},
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.usefixtures("stripe_configured")
@pytest.mark.parametrize(
    "event_type",
    ["unsupported-private-sentinel", ["malformed-private-sentinel"]],
)
def test_stripe_webhook_bounds_unsupported_event_type_telemetry(
    client: TestClient, event_type: object
):
    body = json.dumps({"id": "evt_unsupported", "type": event_type}).encode()

    with capture_logs() as logs:
        response = client.post(
            STRIPE_WEBHOOK_PATH,
            content=body,
            headers={"stripe-signature": _sign(body)},
        )

    assert response.status_code == status.HTTP_200_OK
    rendered_logs = json.dumps(logs)
    assert "private-sentinel" not in rendered_logs
    assert '"event_type": "unsupported"' in rendered_logs


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
    owner = Doctor(
        clerk_id=f"user_webhook_owner_{secrets.token_hex(5)}",
        email=f"webhook-owner-{secrets.token_hex(3)}@example.com",
        biography="",
    ).save()
    PracticeMember(
        practice_id=practice.id,
        doctor_id=owner.id,
        role="owner",
    ).save()
    subscription = _subscription(practice, status_value="active")
    subscription["metadata"].update(
        {
            "trial_clock": stripe_billing.CHECKOUT_TRIAL_CLOCK_POLICY,
            "trial_days": "14",
        }
    )
    trial_start = int(time.time())
    subscription["trial_start"] = trial_start
    subscription["trial_end"] = trial_start + int(timedelta(days=14).total_seconds())
    route_stripe.subscriptions[subscription["id"]] = subscription
    practice.stripe_checkout_session_id = "cs_route_webhook"
    practice.stripe_checkout_entitlement_contract = subscription["metadata"][
        "entitlement_contract"
    ]
    practice.stripe_trial_clock_policy = stripe_billing.CHECKOUT_TRIAL_CLOCK_POLICY
    practice.save()
    event_id = f"evt_{secrets.token_hex(8)}"
    body = json.dumps(
        {
            "id": event_id,
            "type": "checkout.session.completed",
            "created": int(time.time()),
            "api_version": "2025-09-30.clover",
            "livemode": False,
            "data": {
                "object": {
                    "id": "cs_route_webhook",
                    "client_reference_id": str(practice.id),
                    "subscription": subscription["id"],
                }
            },
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
