"""Postgres regression for stale Stripe refreshes racing lifecycle webhooks."""

import secrets
import threading
from copy import deepcopy
from datetime import datetime, timezone

import pytest

import app.configuration.stripe as stripe_config
from app.services import stripe_billing

from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember, PracticeRole

from tests.factories import save_practice_member


def _practice_with_active_owner(**kwargs) -> Practice:
    practice = Practice(**kwargs).save()
    doctor = Doctor(
        clerk_id=f"user_stripe_race_{secrets.token_hex(8)}",
        email=f"stripe-race-{secrets.token_hex(5)}@example.com",
        biography="",
    ).save()
    save_practice_member(
        PracticeMember(
            practice_id=practice.id,
            doctor_id=doctor.id,
            role=PracticeRole.owner.value,
            attested_at=datetime.now(timezone.utc),
        )
    )
    return practice


def test_cancel_commits_while_older_active_refresh_is_in_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in {
        "STRIPE_INTEGRATION_ENABLED": True,
        "STRIPE_BILLING_ENABLED": True,
        "STRIPE_SECRET_KEY": "sk_test_x",
        "STRIPE_ACCOUNT_ID": "acct_test_cosmaneura",
        "STRIPE_PRICE_ID": "price_test_x",
        "STRIPE_WEBHOOK_SECRET": "whsec_test_x",
        "STRIPE_API_VERSION": "2025-09-30.clover",
        "SUPPORTED_STRIPE_API_VERSION": "2025-09-30.clover",
        "STRIPE_LIVE_MODE": False,
    }.items():
        monkeypatch.setattr(stripe_config, name, value)

    practice = _practice_with_active_owner(
        legal_name="Stripe Race Practice, PLLC",
        join_code=f"R{secrets.token_hex(4).upper()[:7]}",
        email_domain="example.com",
        origin_channel="direct",
        stripe_customer_id="cus_race",
        stripe_subscription_id="sub_race",
        stripe_subscription_entitlement_contract=(
            stripe_billing.LEGACY_UNVERSIONED_STRIPE_CONTRACT
        ),
        subscription_status="active",
    )
    subscription = {
        "id": "sub_race",
        "customer": "cus_race",
        "status": "active",
        "livemode": False,
        "metadata": {"practice_id": str(practice.id)},
        "items": {
            "data": [
                {
                    "id": "si_race",
                    "quantity": 1,
                    "price": {"id": "price_test_x"},
                }
            ]
        },
    }
    refresh_fetched = threading.Event()
    release_refresh = threading.Event()
    refresh_blocked = False

    def fake_request(method, path, data=None, *, idempotency_key=None):
        nonlocal refresh_blocked
        if path == "/account":
            return {"id": "acct_test_cosmaneura"}
        if path == "/prices/price_test_x":
            return {
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
        if path == "/subscriptions/sub_race":
            response = deepcopy(subscription)
            if (
                threading.current_thread().name == "stale-active-refresh"
                and not refresh_blocked
            ):
                refresh_blocked = True
                refresh_fetched.set()
                assert release_refresh.wait(timeout=10)
            return response
        raise AssertionError(f"unexpected Stripe request: {method} {path}")

    monkeypatch.setattr(stripe_billing, "_request", fake_request)
    errors: list[BaseException] = []

    def stale_refresh() -> None:
        try:
            local_practice = Practice.get(id=practice.id)
            assert local_practice
            stripe_billing.refresh_subscription_state(local_practice)
        except BaseException as exc:  # pragma: no cover - asserted in parent thread
            errors.append(exc)

    thread = threading.Thread(target=stale_refresh, name="stale-active-refresh")
    thread.start()
    assert refresh_fetched.wait(timeout=10)

    subscription["status"] = "canceled"
    assert stripe_billing.apply_stripe_event(
        {
            "id": "evt_cancel_race",
            "type": "customer.subscription.deleted",
            "created": int(datetime.now(timezone.utc).timestamp()),
            "api_version": "2025-09-30.clover",
            "livemode": False,
            "data": {"object": deepcopy(subscription)},
        }
    )
    release_refresh.set()
    thread.join(timeout=10)

    assert not thread.is_alive()
    assert errors == []
    final = Practice.get(id=practice.id)
    assert final
    assert final.subscription_status == "canceled"
    assert final.stripe_state_version >= 2


def test_stale_invalid_contract_cannot_revoke_newer_valid_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in {
        "STRIPE_INTEGRATION_ENABLED": True,
        "STRIPE_BILLING_ENABLED": True,
        "STRIPE_SECRET_KEY": "sk_test_x",
        "STRIPE_ACCOUNT_ID": "acct_test_cosmaneura",
        "STRIPE_PRICE_ID": "price_test_x",
        "STRIPE_WEBHOOK_SECRET": "whsec_test_x",
        "STRIPE_API_VERSION": "2025-09-30.clover",
        "SUPPORTED_STRIPE_API_VERSION": "2025-09-30.clover",
        "STRIPE_LIVE_MODE": False,
    }.items():
        monkeypatch.setattr(stripe_config, name, value)

    practice = _practice_with_active_owner(
        legal_name="Stripe Contract Race, PLLC",
        join_code=f"C{secrets.token_hex(4).upper()[:7]}",
        email_domain="example.com",
        origin_channel="direct",
        stripe_customer_id="cus_contract_race",
        stripe_subscription_id="sub_contract_race",
        stripe_subscription_entitlement_contract=(
            stripe_billing.LEGACY_UNVERSIONED_STRIPE_CONTRACT
        ),
        subscription_status="active",
    )
    subscription = {
        "id": "sub_contract_race",
        "customer": "cus_contract_race",
        "status": "active",
        "livemode": False,
        "metadata": {"practice_id": str(practice.id)},
        "items": {
            "data": [
                {
                    "id": "si_contract_race",
                    "quantity": 1,
                    "price": {"id": "price_wrong_stale"},
                }
            ]
        },
    }
    stale_fetched = threading.Event()
    release_stale = threading.Event()
    blocked = False

    def fake_request(method, path, data=None, *, idempotency_key=None):
        nonlocal blocked
        if path == "/account":
            return {"id": "acct_test_cosmaneura"}
        if path == "/prices/price_test_x":
            return {
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
        if path == "/subscriptions/sub_contract_race":
            response = deepcopy(subscription)
            if threading.current_thread().name == "stale-invalid" and not blocked:
                blocked = True
                stale_fetched.set()
                assert release_stale.wait(timeout=10)
            return response
        raise AssertionError(f"unexpected Stripe request: {method} {path}")

    monkeypatch.setattr(stripe_billing, "_request", fake_request)
    errors: list[BaseException] = []

    def stale_invalid_refresh() -> None:
        try:
            local_practice = Practice.get(id=practice.id)
            assert local_practice
            stripe_billing.refresh_subscription_state(local_practice)
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    thread = threading.Thread(target=stale_invalid_refresh, name="stale-invalid")
    thread.start()
    assert stale_fetched.wait(timeout=10)

    subscription["items"]["data"][0]["price"] = {"id": "price_test_x"}
    assert stripe_billing.apply_stripe_event(
        {
            "id": "evt_newer_valid_contract",
            "type": "customer.subscription.updated",
            "created": int(datetime.now(timezone.utc).timestamp()),
            "api_version": "2025-09-30.clover",
            "livemode": False,
            "data": {"object": deepcopy(subscription)},
        }
    )
    release_stale.set()
    thread.join(timeout=10)

    assert not thread.is_alive()
    assert errors == []
    final = Practice.get(id=practice.id)
    assert final
    assert final.subscription_status == "active"
    assert final.stripe_state_version >= 2
