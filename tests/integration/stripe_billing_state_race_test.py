"""Postgres regression for stale Stripe refreshes racing lifecycle webhooks."""

import secrets
import threading
from copy import deepcopy
from datetime import datetime, timezone

import pytest

import app.configuration.stripe as stripe_config
from app.services import stripe_billing

from activemodel.session_manager import SessionManager, get_session
from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember, PracticeRole
from app.models.stripe_event_inbox import StripeEventInbox
from sqlalchemy import event as sqlalchemy_event
from sqlmodel import select


def _practice_with_active_owner(**kwargs) -> Practice:
    practice = Practice(**kwargs).save()
    doctor = Doctor(
        clerk_id=f"user_stripe_race_{secrets.token_hex(8)}",
        email=f"stripe-race-{secrets.token_hex(5)}@example.com",
        biography="",
    ).save()
    PracticeMember(
        practice_id=practice.id,
        doctor_id=doctor.id,
        role=PracticeRole.owner.value,
        attested_at=datetime.now(timezone.utc),
    ).save()
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


def test_checkout_retires_remote_session_when_billing_state_changes_in_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in {
        "STRIPE_INTEGRATION_ENABLED": True,
        "STRIPE_BILLING_ENABLED": True,
        "STRIPE_SECRET_KEY": "sk_test_x",
        "STRIPE_ACCOUNT_ID": "acct_test_cosmaneura",
        "STRIPE_PRICE_ID": "price_test_x",
        "STRIPE_PORTAL_CONFIGURATION_ID": "bpc_test_restricted",
        "STRIPE_WEBHOOK_SECRET": "whsec_test_x",
        "STRIPE_API_VERSION": "2025-09-30.clover",
        "SUPPORTED_STRIPE_API_VERSION": "2025-09-30.clover",
        "STRIPE_LIVE_MODE": False,
    }.items():
        monkeypatch.setattr(stripe_config, name, value)

    practice = _practice_with_active_owner(
        legal_name="Checkout Fence Race Practice, PLLC",
        join_code=f"Q{secrets.token_hex(4).upper()[:7]}",
        email_domain="checkout-fence.example.com",
        origin_channel="direct",
    )
    post_started = threading.Event()
    release_post = threading.Event()
    checkout_session = {
        "id": "cs_checkout_fence",
        "url": "https://checkout.stripe.com/c/pay/checkout-fence",
        "expires_at": int(datetime.now(timezone.utc).timestamp()) + 3600,
        "client_reference_id": str(practice.id),
        "livemode": False,
        "mode": "subscription",
        "status": "open",
        "subscription": None,
        "payment_method_collection": "always",
        "metadata": {
            "checkout_policy": stripe_billing.CHECKOUT_IDEMPOTENCY_POLICY_VERSION,
            "checkout_attempt": "0",
            "practice_id": str(practice.id),
            "entitlement_contract": (
                stripe_billing.PUBLIC_ENTITLEMENT_CONTRACT_VERSION
            ),
            "trial_clock": stripe_billing.CHECKOUT_TRIAL_CLOCK_POLICY,
            "trial_days": "14",
        },
    }

    def fake_request(method, path, data=None, *, idempotency_key=None):
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
        if path == "/checkout/sessions":
            post_started.set()
            assert release_post.wait(timeout=10)
            return deepcopy(checkout_session)
        if path == "/checkout/sessions/cs_checkout_fence/expire":
            checkout_session["status"] = "expired"
            checkout_session["url"] = None
            return deepcopy(checkout_session)
        raise AssertionError(f"unexpected Stripe request: {method} {path}")

    monkeypatch.setattr(stripe_billing, "_request", fake_request)
    errors: list[BaseException] = []

    def create_checkout() -> None:
        try:
            local_practice = Practice.get(id=practice.id)
            assert local_practice
            stripe_billing.create_checkout_session(
                local_practice,
                customer_email="owner@example.com",
                success_url="https://app.example/settings",
                cancel_url="https://app.example/settings",
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    thread = threading.Thread(target=create_checkout, name="checkout-create")
    thread.start()
    assert post_started.wait(timeout=10)

    with get_session() as session:
        row = session.exec(
            select(Practice).where(Practice.id == practice.id).with_for_update()
        ).one()
        row.stripe_customer_id = "cus_concurrent_binding"
        row.stripe_subscription_id = "sub_concurrent_binding"
        row.subscription_status = "active"
        row.stripe_state_version += 1
        session.add(row)
        session.commit()

    release_post.set()
    thread.join(timeout=10)

    assert not thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], stripe_billing.StripeCheckoutAlreadyCompleted)
    final = Practice.get(id=practice.id)
    assert final
    assert final.stripe_subscription_id == "sub_concurrent_binding"
    assert final.stripe_checkout_session_id is None
    assert checkout_session["status"] == "expired"


def test_concurrent_checkout_callers_share_one_open_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in {
        "STRIPE_INTEGRATION_ENABLED": True,
        "STRIPE_BILLING_ENABLED": True,
        "STRIPE_SECRET_KEY": "sk_test_x",
        "STRIPE_ACCOUNT_ID": "acct_test_cosmaneura",
        "STRIPE_PRICE_ID": "price_test_x",
        "STRIPE_PORTAL_CONFIGURATION_ID": "bpc_test_restricted",
        "STRIPE_WEBHOOK_SECRET": "whsec_test_x",
        "STRIPE_API_VERSION": "2025-09-30.clover",
        "SUPPORTED_STRIPE_API_VERSION": "2025-09-30.clover",
        "STRIPE_LIVE_MODE": False,
    }.items():
        monkeypatch.setattr(stripe_config, name, value)

    practice = _practice_with_active_owner(
        legal_name="Concurrent Checkout Practice, PLLC",
        join_code=f"D{secrets.token_hex(4).upper()[:7]}",
        email_domain="concurrent-checkout.example.com",
        origin_channel="direct",
    )
    post_barrier = threading.Barrier(2)
    post_keys: list[str | None] = []
    expire_calls: list[str] = []
    checkout_session = {
        "id": "cs_shared_checkout",
        "url": "https://checkout.stripe.com/c/pay/shared-checkout",
        "expires_at": int(datetime.now(timezone.utc).timestamp()) + 3600,
        "client_reference_id": str(practice.id),
        "livemode": False,
        "mode": "subscription",
        "status": "open",
        "subscription": None,
        "payment_method_collection": "always",
        "metadata": {
            "checkout_policy": stripe_billing.CHECKOUT_IDEMPOTENCY_POLICY_VERSION,
            "checkout_attempt": "0",
            "practice_id": str(practice.id),
            "entitlement_contract": (
                stripe_billing.PUBLIC_ENTITLEMENT_CONTRACT_VERSION
            ),
            "trial_clock": stripe_billing.CHECKOUT_TRIAL_CLOCK_POLICY,
            "trial_days": "14",
        },
    }

    def fake_request(method, path, data=None, *, idempotency_key=None):
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
        if path == "/checkout/sessions":
            post_keys.append(idempotency_key)
            post_barrier.wait(timeout=10)
            return deepcopy(checkout_session)
        if path == "/checkout/sessions/cs_shared_checkout":
            return deepcopy(checkout_session)
        if path == "/checkout/sessions/cs_shared_checkout/expire":
            expire_calls.append(path)
            checkout_session["status"] = "expired"
            checkout_session["url"] = None
            return deepcopy(checkout_session)
        raise AssertionError(f"unexpected Stripe request: {method} {path}")

    monkeypatch.setattr(stripe_billing, "_request", fake_request)
    results: list[str] = []
    errors: list[BaseException] = []

    def create_checkout() -> None:
        try:
            local_practice = Practice.get(id=practice.id)
            assert local_practice
            results.append(
                stripe_billing.create_checkout_session(
                    local_practice,
                    customer_email="owner@example.com",
                    success_url="https://app.example/settings",
                    cancel_url="https://app.example/settings",
                )
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [
        threading.Thread(target=create_checkout, name=f"checkout-create-{index}")
        for index in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert results == [checkout_session["url"], checkout_session["url"]]
    assert len(set(post_keys)) == 1
    assert expire_calls == []
    final = Practice.get(id=practice.id)
    assert final
    assert final.stripe_checkout_session_id == "cs_shared_checkout"


def test_concurrent_duplicate_webhooks_share_one_durable_inbox_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in {
        "STRIPE_ACCOUNT_ID": "acct_test_cosmaneura",
        "STRIPE_API_VERSION": "2025-09-30.clover",
        "SUPPORTED_STRIPE_API_VERSION": "2025-09-30.clover",
        "STRIPE_LIVE_MODE": False,
    }.items():
        monkeypatch.setattr(stripe_config, name, value)

    initial_state_version = 7
    practice = _practice_with_active_owner(
        legal_name="Concurrent Webhook Inbox Practice, PLLC",
        join_code=f"W{secrets.token_hex(4).upper()[:7]}",
        email_domain="concurrent-webhook.example.com",
        origin_channel="direct",
        stripe_customer_id="cus_concurrent_webhook",
        stripe_subscription_id="sub_concurrent_webhook",
        subscription_status="active",
        stripe_state_version=initial_state_version,
    )
    event_id = f"evt_concurrent_{secrets.token_hex(8)}"
    event_payload = {
        "id": event_id,
        "type": "customer.subscription.updated",
        "created": int(datetime.now(timezone.utc).timestamp()),
        "api_version": "2025-09-30.clover",
        "livemode": False,
        "data": {
            "object": {
                "id": "sub_concurrent_webhook",
                "customer": "cus_concurrent_webhook",
                "status": "past_due",
                "livemode": False,
                "metadata": {"practice_id": str(practice.id)},
                "items": {
                    "data": [
                        {
                            "id": "si_concurrent_webhook",
                            "quantity": 1,
                            "price": {"id": "price_test_x"},
                        }
                    ]
                },
            }
        },
    }

    initial_select_barrier = threading.Barrier(2)
    synchronized_threads: set[str] = set()
    synchronized_threads_lock = threading.Lock()
    thread_name_prefix = "stripe-webhook-enqueue-"

    def synchronize_initial_inbox_select(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        thread_name = threading.current_thread().name
        if (
            not thread_name.startswith(thread_name_prefix)
            or "from stripe_event_inbox" not in statement.lower()
            or "stripe_event_inbox.event_id" not in statement.lower()
        ):
            return
        with synchronized_threads_lock:
            if thread_name in synchronized_threads:
                return
            synchronized_threads.add(thread_name)
        initial_select_barrier.wait(timeout=10)

    engine = SessionManager.get_instance().get_engine()
    sqlalchemy_event.listen(
        engine, "after_cursor_execute", synchronize_initial_inbox_select
    )
    results: list[tuple[object | None, bool]] = []
    errors: list[BaseException] = []

    def enqueue_duplicate() -> None:
        try:
            results.append(stripe_billing.enqueue_stripe_event(deepcopy(event_payload)))
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [
        threading.Thread(
            target=enqueue_duplicate,
            name=f"{thread_name_prefix}{index}",
        )
        for index in range(2)
    ]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
    finally:
        sqlalchemy_event.remove(
            engine, "after_cursor_execute", synchronize_initial_inbox_select
        )

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert synchronized_threads == {
        f"{thread_name_prefix}0",
        f"{thread_name_prefix}1",
    }
    assert len(results) == 2
    assert len({inbox_id for inbox_id, _duplicate in results}) == 1
    assert sorted(duplicate for _inbox_id, duplicate in results) == [False, True]

    with get_session() as session:
        inbox_rows = session.exec(
            select(StripeEventInbox).where(StripeEventInbox.event_id == event_id)
        ).all()
        final_practice = session.exec(
            select(Practice).where(Practice.id == practice.id)
        ).one()
        assert len(inbox_rows) == 1
        assert final_practice.subscription_status == "pending_verification"
        assert final_practice.stripe_state_version == initial_state_version + 1
