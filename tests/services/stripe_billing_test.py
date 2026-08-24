"""Stripe billing invariants that protect entitlement and prevent duplicates."""

import hashlib
import hmac
import json
import secrets
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

import app.configuration.stripe as stripe_config
from app.services import stripe_billing
from app.services.organization_access import (
    RosterApprovalInput,
    claim_roster_access,
    configure_provider_organization,
    import_roster_approvals,
    revoke_roster_access,
)

from activemodel.session_manager import global_session
from app.models.doctor import Doctor
from app.models.practice import Practice
from app.models.stripe_event_inbox import StripeEventInbox
from app.models.stripe_event_receipt import StripeEventReceipt

WEBHOOK_SECRET = "whsec_test_secret"
NOW = datetime(2026, 8, 20, 20, 0, tzinfo=timezone.utc)


class FakeStripe:
    def __init__(self) -> None:
        self.account_id = "acct_test_cosmaneura"
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
        self.subscriptions: dict[str, dict] = {}
        self.sessions: dict[str, dict] = {}
        self.calls: list[tuple[str, str, dict | None, str | None]] = []
        self.fail_subscription_reads = False

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
            return {"id": self.account_id}
        if path.startswith("/prices/"):
            return deepcopy(self.price)
        if path == "/checkout/sessions":
            assert data
            session = {
                "id": "cs_test_idempotent",
                "url": "https://checkout.stripe.com/c/pay/test",
                "expires_at": int((NOW + timedelta(hours=24)).timestamp()),
                "client_reference_id": data["client_reference_id"],
                "livemode": False,
                "mode": "subscription",
                "status": "open",
                "subscription": None,
            }
            self.sessions[session["id"]] = deepcopy(session)
            return session
        if path.startswith("/checkout/sessions/"):
            return deepcopy(self.sessions[path.rsplit("/", 1)[-1]])
        if path == "/billing_portal/sessions":
            return {"url": "https://billing.stripe.com/p/session/test"}
        if path.startswith("/subscriptions/"):
            if self.fail_subscription_reads:
                raise RuntimeError("Stripe unavailable")
            return deepcopy(self.subscriptions[path.rsplit("/", 1)[-1]])
        if path.startswith("/subscription_items/"):
            item_id = path.rsplit("/", 1)[-1]
            for subscription in self.subscriptions.values():
                item = subscription["items"]["data"][0]
                if item["id"] == item_id:
                    assert data
                    item["quantity"] = int(data["quantity"])
                    return deepcopy(item)
        raise AssertionError(f"unexpected Stripe request: {method} {path}")


@pytest.fixture(autouse=True)
def configured_stripe(monkeypatch: pytest.MonkeyPatch) -> None:
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
def remote(monkeypatch: pytest.MonkeyPatch) -> FakeStripe:
    fake = FakeStripe()
    monkeypatch.setattr(stripe_billing, "_request", fake)
    return fake


def _sign(
    payload: bytes, *, secret: str = WEBHOOK_SECRET, at: int | None = None
) -> str:
    timestamp = at if at is not None else int(time.time())
    digest = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v1={digest}"


def _practice() -> Practice:
    return Practice(
        legal_name="Mirror Test Practice, PLLC",
        join_code=f"M{secrets.token_hex(4).upper()[:7]}",
        email_domain="example.com",
        origin_channel="direct",
    ).save()


def _organization_practice(domain: str, *, now: datetime) -> tuple[Practice, str]:
    practice = Practice(
        legal_name=f"{domain} Billing Test Practice",
        join_code=f"O{secrets.token_hex(4).upper()[:7]}",
        email_domain=domain,
        origin_channel="direct",
        is_solo=False,
        negotiated_baa_on_file=True,
        negotiated_baa_effective_date=now - timedelta(days=1),
        negotiated_baa_reference=f"executed-baa:{domain}",
        billing_exempt=True,
    ).save()
    _, code, _ = configure_provider_organization(
        practice,
        legal_name=f"{domain} Provider Organization",
        slug=f"billing-test-{secrets.token_hex(5)}",
        email_domain=domain,
        operator_ref="stripe-billing-test",
        reason="Configure an organization billing test tenant",
        now=now,
    )
    return practice, code


def _organization_doctor(email: str) -> Doctor:
    return Doctor(
        clerk_id=f"user_stripe_org_{secrets.token_hex(8)}",
        email=email,
        biography="",
    ).save()


def _import_organization_roster(
    practice: Practice,
    emails: list[str],
    *,
    now: datetime,
) -> None:
    import_roster_approvals(
        practice,
        [RosterApprovalInput(email=email) for email in emails],
        operator_ref="stripe-billing-test",
        reason="Approve organization seats for billing tests",
        source_ref="test://stripe-billing-roster",
        source_sha256="b" * 64,
        now=now,
    )


def _subscription(
    practice: Practice,
    *,
    subscription_id: str = "sub_test_123",
    customer_id: str = "cus_test_123",
    status: str = "active",
    quantity: int = 1,
    price_id: str = "price_test_x",
) -> dict:
    return {
        "id": subscription_id,
        "customer": customer_id,
        "status": status,
        "livemode": False,
        "metadata": {"practice_id": str(practice.id)},
        "items": {
            "data": [
                {
                    "id": f"si_{subscription_id.removeprefix('sub_')}",
                    "quantity": quantity,
                    "price": {"id": price_id},
                }
            ]
        },
    }


def _event(
    event_type: str,
    subscription: dict,
    *,
    event_id: str,
    created: int,
) -> dict:
    return {
        "id": event_id,
        "type": event_type,
        "created": created,
        "api_version": "2025-09-30.clover",
        "livemode": False,
        "data": {"object": deepcopy(subscription)},
    }


def test_signature_verification_rejects_tampering_replay_and_wrong_secret(
    monkeypatch: pytest.MonkeyPatch,
):
    payload = b'{"id":"evt_1"}'
    assert stripe_billing.verify_webhook_signature(payload, _sign(payload))
    assert not stripe_billing.verify_webhook_signature(
        b'{"id":"evt_TAMPERED"}', _sign(payload)
    )
    stale = int(time.time()) - stripe_billing.SIGNATURE_TOLERANCE_SECONDS - 1
    assert not stripe_billing.verify_webhook_signature(
        payload, _sign(payload, at=stale)
    )
    assert not stripe_billing.verify_webhook_signature(
        payload, _sign(payload, secret="whsec_other")
    )
    monkeypatch.setattr(stripe_config, "STRIPE_WEBHOOK_SECRET", "")
    assert not stripe_billing.verify_webhook_signature(payload, _sign(payload))


def test_event_api_version_must_match_source_pin(remote: FakeStripe):
    practice = _practice()
    subscription = _subscription(practice)
    event = _event(
        "customer.subscription.created",
        subscription,
        event_id="evt_wrong_api_version",
        created=int(NOW.timestamp()),
    )
    event["api_version"] = "2025-07-01.changed"

    with pytest.raises(stripe_billing.StripeStateInvalid, match="API version"):
        stripe_billing.apply_stripe_event(event)
    assert remote.calls == []

    event["api_version"] = "2025-09-30.clover"
    event["account"] = "acct_connected_other"
    with pytest.raises(stripe_billing.StripeStateInvalid, match="Connect"):
        stripe_billing.apply_stripe_event(event)
    assert remote.calls == []


def test_authoritative_state_transactions_ignore_request_global_session():
    with global_session() as request_session:
        with stripe_billing._isolated_state_session() as isolated_session:
            assert isolated_session is not request_session


def test_acquisition_requires_webhook_but_maintenance_survives_kill_switch(
    monkeypatch: pytest.MonkeyPatch,
):
    assert stripe_config.stripe_runtime_configured()
    assert stripe_config.stripe_webhook_configured()
    assert stripe_config.stripe_billing_configured()

    monkeypatch.setattr(stripe_config, "STRIPE_WEBHOOK_SECRET", "")
    assert stripe_config.stripe_runtime_configured()
    assert not stripe_config.stripe_webhook_configured()
    assert not stripe_config.stripe_billing_configured()

    monkeypatch.setattr(stripe_config, "STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setattr(stripe_config, "STRIPE_BILLING_ENABLED", False)
    assert stripe_config.stripe_runtime_configured()
    assert stripe_config.stripe_webhook_configured()
    assert not stripe_config.stripe_billing_configured()


def test_runtime_requires_exact_source_controlled_api_version(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(stripe_config, "STRIPE_API_VERSION", "2025-07-01.changed")
    assert not stripe_config.stripe_runtime_configured()


def test_checkout_is_idempotent_and_carries_exact_long_trial(remote: FakeStripe):
    practice = _practice()
    practice.trial_ends_at = NOW + timedelta(days=10, seconds=17, microseconds=1)
    practice.save()

    first = stripe_billing.create_checkout_session(
        practice,
        customer_email="owner@example.com",
        success_url="https://app.example/settings?billing=success",
        cancel_url="https://app.example/settings?billing=cancelled",
        now=NOW,
    )
    second = stripe_billing.create_checkout_session(
        practice,
        customer_email="owner@example.com",
        success_url="https://app.example/settings?billing=success",
        cancel_url="https://app.example/settings?billing=cancelled",
        now=NOW,
    )

    assert first == second == "https://checkout.stripe.com/c/pay/test"
    checkout_calls = [call for call in remote.calls if call[1] == "/checkout/sessions"]
    assert len(checkout_calls) == 1
    _, _, data, idempotency_key = checkout_calls[0]
    assert data
    stripe_trial_end = int(data["subscription_data[trial_end]"])
    carried_trial_end = datetime.fromtimestamp(stripe_trial_end, tz=timezone.utc)
    assert carried_trial_end >= practice.trial_ends_at
    assert carried_trial_end < practice.trial_ends_at + timedelta(seconds=1)
    assert idempotency_key and idempotency_key.startswith("cosmas-checkout-")


def test_checkout_extends_final_48_hours_instead_of_shortening_trial(
    remote: FakeStripe,
):
    practice = _practice()
    practice.trial_ends_at = NOW + timedelta(hours=47)
    practice.save()

    assert stripe_billing.checkout_deferred_until(practice, now=NOW) is None
    checkout_url = stripe_billing.create_checkout_session(
        practice,
        customer_email="owner@example.com",
        success_url="https://app.example/settings",
        cancel_url="https://app.example/settings",
        now=NOW,
    )

    assert checkout_url == "https://checkout.stripe.com/c/pay/test"
    checkout_call = next(
        call for call in remote.calls if call[1] == "/checkout/sessions"
    )
    assert checkout_call[2]
    stripe_trial_end = datetime.fromtimestamp(
        int(checkout_call[2]["subscription_data[trial_end]"]), tz=timezone.utc
    )
    assert stripe_trial_end >= practice.trial_ends_at
    minimum_safe_end = (
        NOW
        + stripe_billing.MINIMUM_CHECKOUT_TRIAL_LEAD
        + stripe_billing.CHECKOUT_TRIAL_LEAD_SAFETY
    )
    assert stripe_trial_end >= minimum_safe_end
    assert stripe_trial_end < minimum_safe_end + timedelta(seconds=1)


def test_checkout_remeasures_minimum_trial_lead_after_remote_validation(
    remote: FakeStripe, monkeypatch: pytest.MonkeyPatch
):
    practice = _practice()
    practice.trial_ends_at = NOW + timedelta(hours=47)
    practice.save()
    after_validation = NOW + timedelta(minutes=10)
    now_calls = 0

    class AdvancingDateTime(datetime):
        @classmethod
        def now(cls, tz: timezone | None = None) -> datetime:
            nonlocal now_calls
            now_calls += 1
            value = NOW if now_calls == 1 else after_validation
            return value if tz else value.replace(tzinfo=None)

    monkeypatch.setattr(stripe_billing, "datetime", AdvancingDateTime)
    stripe_billing.create_checkout_session(
        practice,
        customer_email="owner@example.com",
        success_url="https://app.example/settings",
        cancel_url="https://app.example/settings",
    )

    checkout_call = next(
        call for call in remote.calls if call[1] == "/checkout/sessions"
    )
    assert checkout_call[2]
    stripe_trial_end = datetime.fromtimestamp(
        int(checkout_call[2]["subscription_data[trial_end]"]), tz=timezone.utc
    )
    assert stripe_trial_end >= (
        after_validation
        + stripe_billing.MINIMUM_CHECKOUT_TRIAL_LEAD
        + stripe_billing.CHECKOUT_TRIAL_LEAD_SAFETY
    )


def test_expired_trial_checkout_starts_without_new_stripe_trial(remote: FakeStripe):
    practice = _practice()
    practice.trial_ends_at = NOW - timedelta(seconds=1)
    practice.save()

    stripe_billing.create_checkout_session(
        practice,
        customer_email="owner@example.com",
        success_url="https://app.example/settings",
        cancel_url="https://app.example/settings",
        now=NOW,
    )

    checkout_call = next(
        call for call in remote.calls if call[1] == "/checkout/sessions"
    )
    assert checkout_call[2]
    assert "subscription_data[trial_end]" not in checkout_call[2]


def test_paused_trial_without_card_revokes_then_resumed_restores_entitlement(
    remote: FakeStripe,
):
    practice = _practice()
    practice.trial_ends_at = NOW - timedelta(seconds=1)
    practice.save()
    paused = _subscription(practice, status="paused")
    remote.subscriptions[paused["id"]] = paused

    assert stripe_billing.apply_stripe_event(
        _event(
            "customer.subscription.paused",
            paused,
            event_id="evt_paused",
            created=int(NOW.timestamp()),
        )
    )
    practice.refresh()
    assert practice.subscription_status == "paused"
    assert not practice.entitled(at=NOW)

    resumed = _subscription(practice, status="active")
    remote.subscriptions[resumed["id"]] = resumed
    assert stripe_billing.apply_stripe_event(
        _event(
            "customer.subscription.resumed",
            resumed,
            event_id="evt_resumed",
            created=int((NOW + timedelta(seconds=1)).timestamp()),
        )
    )
    practice.refresh()
    assert practice.subscription_status == "active"
    assert practice.entitled(at=NOW + timedelta(seconds=1))


def test_pause_collection_revokes_even_when_stripe_status_stays_active(
    remote: FakeStripe,
):
    practice = _practice()
    subscription = _subscription(practice, status="active")
    subscription["pause_collection"] = {"behavior": "keep_as_draft"}
    remote.subscriptions[subscription["id"]] = subscription

    assert stripe_billing.apply_stripe_event(
        _event(
            "customer.subscription.updated",
            subscription,
            event_id="evt_pause_collection",
            created=int(NOW.timestamp()),
        )
    )
    practice.refresh()
    assert practice.subscription_status == "paused"
    assert not practice.entitled(at=NOW)

    subscription["pause_collection"] = None
    remote.subscriptions[subscription["id"]] = subscription
    assert stripe_billing.apply_stripe_event(
        _event(
            "customer.subscription.updated",
            subscription,
            event_id="evt_pause_collection_cleared",
            created=int((NOW + timedelta(seconds=1)).timestamp()),
        )
    )
    practice.refresh()
    assert practice.subscription_status == "active"


def test_terminal_status_takes_precedence_over_pause_collection(remote: FakeStripe):
    practice = _practice()
    canceled = _subscription(practice, status="canceled")
    canceled["pause_collection"] = {"behavior": "keep_as_draft"}
    remote.subscriptions[canceled["id"]] = canceled

    assert stripe_billing.apply_stripe_event(
        _event(
            "customer.subscription.deleted",
            canceled,
            event_id="evt_canceled_while_paused",
            created=int(NOW.timestamp()),
        )
    )
    practice.refresh()
    assert practice.subscription_status == "canceled"
    assert not stripe_billing.has_nonterminal_subscription(practice)


def test_duplicate_event_receipt_prevents_double_checkout_clear(
    remote: FakeStripe,
):
    practice = _practice()
    practice.stripe_checkout_session_id = "cs_once"
    practice.stripe_checkout_session_url = "https://checkout.stripe.com/c/pay/once"
    practice.stripe_checkout_session_expires_at = NOW + timedelta(hours=1)
    practice.save()
    subscription = _subscription(practice, subscription_id="sub_once")
    remote.subscriptions[subscription["id"]] = subscription
    event = {
        "id": "evt_checkout_once",
        "type": "checkout.session.completed",
        "created": int(NOW.timestamp()),
        "api_version": "2025-09-30.clover",
        "livemode": False,
        "data": {
            "object": {
                "id": "cs_once",
                "client_reference_id": str(practice.id),
                "subscription": subscription["id"],
            }
        },
    }

    assert stripe_billing.apply_stripe_event(event)
    practice.refresh()
    first_attempt = practice.stripe_checkout_attempt
    first_version = practice.stripe_state_version
    assert not stripe_billing.apply_stripe_event(event)
    practice.refresh()
    assert practice.stripe_checkout_attempt == first_attempt == 1
    assert practice.stripe_state_version == first_version
    assert (
        StripeEventReceipt.where(
            StripeEventReceipt.event_id == "evt_checkout_once"
        ).count()
        == 1
    )


def test_verified_event_is_durable_and_deduplicated_before_stripe_io(
    remote: FakeStripe,
):
    practice = _practice()
    subscription = _subscription(practice, status="canceled")
    remote.subscriptions[subscription["id"]] = subscription
    practice.stripe_customer_id = subscription["customer"]
    practice.stripe_subscription_id = subscription["id"]
    practice.subscription_status = "active"
    practice.save()
    event = _event(
        "customer.subscription.deleted",
        subscription,
        event_id="evt_durable_inbox",
        created=int(NOW.timestamp()),
    )
    event["data"]["object"]["customer_details"] = {
        "email": "sentinel@example.com",
        "address": {"line1": "SENSITIVE SENTINEL"},
    }
    event["data"]["object"]["customer"] = {
        "id": subscription["customer"],
        "email": "expanded-sentinel@example.com",
    }
    event["data"]["object"]["metadata"]["unexpected"] = "PHI SENTINEL"
    event["data"]["object"]["metadata"]["practice_id"] = "PHI PRACTICE SENTINEL"

    first_id, first_duplicate = stripe_billing.enqueue_stripe_event(event)
    second_id, second_duplicate = stripe_billing.enqueue_stripe_event(event)
    assert first_id == second_id
    assert first_duplicate is False
    assert second_duplicate is True
    assert remote.calls == []
    assert (
        StripeEventInbox.where(StripeEventInbox.event_id == "evt_durable_inbox").count()
        == 1
    )
    inbox = StripeEventInbox.get(id=first_id)
    assert inbox
    assert "sentinel@example.com" not in inbox.payload_json
    assert "expanded-sentinel@example.com" not in inbox.payload_json
    assert "SENSITIVE SENTINEL" not in inbox.payload_json
    assert "PHI SENTINEL" not in inbox.payload_json
    assert "PHI PRACTICE SENTINEL" not in inbox.payload_json

    checkout_inbox_id, _ = stripe_billing.enqueue_stripe_event(
        {
            "id": "evt_checkout_sanitized",
            "type": "checkout.session.completed",
            "created": int(NOW.timestamp()),
            "api_version": "2025-09-30.clover",
            "livemode": False,
            "data": {
                "object": {
                    "id": "cs_sanitized",
                    "client_reference_id": "PHI CHECKOUT SENTINEL",
                    "subscription": subscription["id"],
                    "customer_details": {"email": "checkout@example.com"},
                }
            },
        }
    )
    checkout_inbox = StripeEventInbox.get(id=checkout_inbox_id)
    assert checkout_inbox
    assert "PHI CHECKOUT SENTINEL" not in checkout_inbox.payload_json
    assert "checkout@example.com" not in checkout_inbox.payload_json

    assert stripe_billing.process_stripe_event_inbox(first_id)
    practice.refresh()
    assert practice.subscription_status == "canceled"


def test_stale_nonentitling_inbox_event_does_not_revoke_newer_active_state(
    remote: FakeStripe,
):
    practice = _practice()
    active = _subscription(practice, status="active")
    remote.subscriptions[active["id"]] = active
    practice.stripe_customer_id = active["customer"]
    practice.stripe_subscription_id = active["id"]
    practice.subscription_status = "active"
    practice.stripe_last_event_created_at = NOW + timedelta(seconds=10)
    practice.stripe_last_event_id = "evt_newer_active"
    practice.save()
    stale_canceled = _subscription(practice, status="canceled")
    event = _event(
        "customer.subscription.deleted",
        stale_canceled,
        event_id="evt_stale_canceled_inbox",
        created=int(NOW.timestamp()),
    )

    inbox_id, duplicate = stripe_billing.enqueue_stripe_event(event)
    assert not duplicate
    practice.refresh()
    assert practice.subscription_status == "active"
    assert stripe_billing.process_stripe_event_inbox(inbox_id)
    practice.refresh()
    assert practice.subscription_status == "active"


def test_bound_wrong_price_payload_revokes_pending_before_worker_io(
    remote: FakeStripe,
):
    practice = _practice()
    subscription = _subscription(practice, status="active")
    practice.stripe_customer_id = subscription["customer"]
    practice.stripe_subscription_id = subscription["id"]
    practice.subscription_status = "active"
    practice.save()
    subscription["items"]["data"][0]["price"] = {"id": "price_wrong"}
    event = _event(
        "customer.subscription.updated",
        subscription,
        event_id="evt_wrong_price_pending",
        created=int(NOW.timestamp()),
    )

    inbox_id, duplicate = stripe_billing.enqueue_stripe_event(event)
    assert inbox_id and not duplicate
    assert remote.calls == []
    practice.refresh()
    assert practice.subscription_status == "pending_verification"


def test_past_due_is_nonterminal_and_not_entitled(remote: FakeStripe):
    practice = _practice()
    practice.trial_ends_at = NOW - timedelta(days=1)
    practice.save()
    past_due = _subscription(practice, status="past_due")
    remote.subscriptions[past_due["id"]] = past_due

    assert stripe_billing.apply_stripe_event(
        _event(
            "customer.subscription.updated",
            past_due,
            event_id="evt_past_due",
            created=int(NOW.timestamp()),
        )
    )
    practice.refresh()
    assert stripe_billing.has_nonterminal_subscription(practice)
    assert not practice.entitled(at=NOW)


def test_delete_wins_and_delayed_older_active_event_cannot_restore_access(
    remote: FakeStripe,
):
    practice = _practice()
    practice.trial_ends_at = NOW - timedelta(days=1)
    practice.save()
    canceled = _subscription(practice, status="canceled")
    remote.subscriptions[canceled["id"]] = canceled

    assert stripe_billing.apply_stripe_event(
        _event(
            "customer.subscription.deleted",
            canceled,
            event_id="evt_deleted",
            created=int((NOW + timedelta(seconds=10)).timestamp()),
        )
    )
    delayed_active = _subscription(practice, status="active")
    assert not stripe_billing.apply_stripe_event(
        _event(
            "customer.subscription.updated",
            delayed_active,
            event_id="evt_old_active",
            created=int(NOW.timestamp()),
        )
    )
    practice.refresh()
    assert practice.subscription_status == "canceled"
    assert not stripe_billing.has_nonterminal_subscription(practice)
    assert not practice.entitled(at=NOW + timedelta(seconds=10))


def test_bound_subscription_id_outranks_drifted_metadata_on_cancel(
    remote: FakeStripe,
):
    bound = _practice()
    metadata_target = _practice()
    canceled = _subscription(bound, status="canceled")
    canceled["metadata"] = {"practice_id": str(metadata_target.id)}
    remote.subscriptions[canceled["id"]] = canceled
    bound.stripe_customer_id = canceled["customer"]
    bound.stripe_subscription_id = canceled["id"]
    bound.subscription_status = "active"
    bound.save()

    assert stripe_billing.apply_stripe_event(
        _event(
            "customer.subscription.deleted",
            canceled,
            event_id="evt_metadata_drift_cancel",
            created=int(NOW.timestamp()),
        )
    )

    bound.refresh()
    metadata_target.refresh()
    assert bound.subscription_status == "canceled"
    assert metadata_target.stripe_subscription_id is None
    assert metadata_target.subscription_status is None


def test_initial_bind_requires_fresh_subscription_metadata(remote: FakeStripe):
    practice = _practice()
    subscription = _subscription(practice)
    subscription["metadata"] = {}
    remote.subscriptions[subscription["id"]] = subscription

    with pytest.raises(stripe_billing.StripeStateInvalid, match="metadata is missing"):
        stripe_billing.apply_stripe_event(
            _event(
                "customer.subscription.created",
                _subscription(practice),
                event_id="evt_initial_missing_metadata",
                created=int(NOW.timestamp()),
            )
        )
    practice.refresh()
    assert practice.stripe_subscription_id is None


def test_checkout_completion_uses_fresh_subscription_and_clears_cached_session(
    remote: FakeStripe,
):
    practice = _practice()
    practice.stripe_checkout_session_id = "cs_old"
    practice.stripe_checkout_session_url = "https://checkout.stripe.com/c/pay/old"
    practice.stripe_checkout_session_expires_at = NOW + timedelta(hours=1)
    practice.save()
    subscription = _subscription(practice, subscription_id="sub_checkout")
    remote.subscriptions[subscription["id"]] = subscription
    event = {
        "id": "evt_checkout",
        "type": "checkout.session.completed",
        "created": int(NOW.timestamp()),
        "api_version": "2025-09-30.clover",
        "livemode": False,
        "data": {
            "object": {
                "id": "cs_old",
                "client_reference_id": str(practice.id),
                "subscription": subscription["id"],
            }
        },
    }

    assert stripe_billing.apply_stripe_event(event)
    practice.refresh()
    assert practice.stripe_subscription_id == "sub_checkout"
    assert practice.stripe_checkout_session_id is None
    assert practice.stripe_checkout_attempt == 1


def test_expired_local_checkout_recovers_completed_subscription_before_retry(
    remote: FakeStripe,
):
    practice = _practice()
    practice.stripe_checkout_session_id = "cs_completed_late"
    practice.stripe_checkout_session_url = "https://checkout.stripe.com/c/pay/old"
    practice.stripe_checkout_session_expires_at = NOW - timedelta(seconds=1)
    practice.save()
    subscription = _subscription(practice, subscription_id="sub_recovered")
    remote.subscriptions[subscription["id"]] = subscription
    remote.sessions["cs_completed_late"] = {
        "id": "cs_completed_late",
        "url": None,
        "expires_at": int((NOW - timedelta(seconds=1)).timestamp()),
        "client_reference_id": str(practice.id),
        "livemode": False,
        "mode": "subscription",
        "status": "complete",
        "subscription": subscription["id"],
    }

    with pytest.raises(stripe_billing.StripeCheckoutAlreadyCompleted):
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings",
            cancel_url="https://app.example/settings",
            now=NOW,
        )
    practice.refresh()
    assert practice.stripe_subscription_id == "sub_recovered"
    assert practice.stripe_checkout_session_id is None
    assert not any(call[1] == "/checkout/sessions" for call in remote.calls)


def test_incomplete_subscription_resumes_exact_open_checkout_session(
    remote: FakeStripe,
):
    practice = _practice()
    practice.trial_ends_at = NOW + timedelta(hours=1)
    subscription = _subscription(practice, status="incomplete")
    remote.subscriptions[subscription["id"]] = subscription
    practice.stripe_customer_id = subscription["customer"]
    practice.stripe_subscription_id = subscription["id"]
    practice.subscription_status = "incomplete"
    practice.stripe_checkout_session_id = "cs_resume"
    practice.stripe_checkout_session_url = "https://checkout.stripe.com/c/pay/resume"
    practice.stripe_checkout_session_expires_at = NOW + timedelta(hours=1)
    practice.save()
    remote.price["active"] = False
    remote.sessions["cs_resume"] = {
        "id": "cs_resume",
        "url": "https://checkout.stripe.com/c/pay/resume",
        "expires_at": int((NOW + timedelta(hours=1)).timestamp()),
        "client_reference_id": str(practice.id),
        "livemode": False,
        "mode": "subscription",
        "status": "open",
        "subscription": subscription["id"],
    }

    assert (
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings",
            cancel_url="https://app.example/settings",
            now=NOW,
        )
        == "https://checkout.stripe.com/c/pay/resume"
    )
    assert not any(call[1] == "/checkout/sessions" for call in remote.calls)


def test_cached_open_checkout_without_subscription_resumes_when_acquisition_is_off(
    remote: FakeStripe,
    monkeypatch: pytest.MonkeyPatch,
):
    practice = _practice()
    practice.trial_ends_at = NOW + timedelta(hours=1)
    practice.stripe_checkout_session_id = "cs_open_without_subscription"
    practice.stripe_checkout_session_url = (
        "https://checkout.stripe.com/c/pay/open-without-subscription"
    )
    practice.stripe_checkout_session_expires_at = NOW + timedelta(hours=1)
    practice.save()
    remote.price["active"] = False
    remote.sessions["cs_open_without_subscription"] = {
        "id": "cs_open_without_subscription",
        "url": "https://checkout.stripe.com/c/pay/open-without-subscription",
        "expires_at": int((NOW + timedelta(hours=1)).timestamp()),
        "client_reference_id": str(practice.id),
        "livemode": False,
        "mode": "subscription",
        "status": "open",
        "subscription": None,
    }
    monkeypatch.setattr(stripe_config, "STRIPE_BILLING_ENABLED", False)

    assert stripe_billing.has_resumable_checkout(practice)
    assert (
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings",
            cancel_url="https://app.example/settings",
            now=NOW,
        )
        == "https://checkout.stripe.com/c/pay/open-without-subscription"
    )
    assert not any(call[1] == "/checkout/sessions" for call in remote.calls)


def test_expired_cached_checkout_cannot_bypass_acquisition_kill_switch(
    remote: FakeStripe,
    monkeypatch: pytest.MonkeyPatch,
):
    practice = _practice()
    practice.stripe_checkout_session_id = "cs_expired_while_disabled"
    practice.stripe_checkout_session_url = "https://checkout.stripe.com/c/pay/expired"
    practice.stripe_checkout_session_expires_at = NOW - timedelta(seconds=1)
    practice.save()
    remote.sessions["cs_expired_while_disabled"] = {
        "id": "cs_expired_while_disabled",
        "url": None,
        "expires_at": int((NOW - timedelta(seconds=1)).timestamp()),
        "client_reference_id": str(practice.id),
        "livemode": False,
        "mode": "subscription",
        "status": "expired",
        "subscription": None,
    }
    monkeypatch.setattr(stripe_config, "STRIPE_BILLING_ENABLED", False)

    with pytest.raises(stripe_billing.StripeNotConfigured):
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings",
            cancel_url="https://app.example/settings",
            now=NOW,
        )
    practice.refresh()
    assert practice.stripe_checkout_session_id is None
    assert not any(call[1] == "/checkout/sessions" for call in remote.calls)


def test_incomplete_expired_clears_stale_cache_before_new_checkout(
    remote: FakeStripe,
):
    practice = _practice()
    old = _subscription(practice, status="incomplete_expired")
    practice.stripe_customer_id = old["customer"]
    practice.stripe_subscription_id = old["id"]
    practice.subscription_status = "incomplete_expired"
    practice.stripe_checkout_session_id = "cs_expired_old"
    practice.stripe_checkout_session_url = "https://checkout.stripe.com/c/pay/old"
    practice.stripe_checkout_session_expires_at = NOW - timedelta(seconds=1)
    practice.save()
    remote.sessions["cs_expired_old"] = {
        "id": "cs_expired_old",
        "url": None,
        "expires_at": int((NOW - timedelta(seconds=1)).timestamp()),
        "client_reference_id": str(practice.id),
        "livemode": False,
        "mode": "subscription",
        "status": "expired",
        "subscription": None,
    }

    url = stripe_billing.create_checkout_session(
        practice,
        customer_email="owner@example.com",
        success_url="https://app.example/settings",
        cancel_url="https://app.example/settings",
        now=NOW,
    )
    practice.refresh()
    assert url == "https://checkout.stripe.com/c/pay/test"
    assert practice.stripe_checkout_session_id == "cs_test_idempotent"
    assert practice.stripe_checkout_attempt == 1


def test_late_checkout_response_cannot_resurrect_cache_after_completion(
    remote: FakeStripe,
    monkeypatch: pytest.MonkeyPatch,
):
    practice = _practice()
    original_request = remote.__call__

    def racing_request(method, path, data=None, *, idempotency_key=None):
        result = original_request(method, path, data, idempotency_key=idempotency_key)
        if path == "/checkout/sessions":
            subscription = _subscription(practice, subscription_id="sub_race_complete")
            remote.subscriptions[subscription["id"]] = subscription
            stripe_billing.apply_stripe_event(
                {
                    "id": "evt_race_complete",
                    "type": "checkout.session.completed",
                    "created": int(NOW.timestamp()),
                    "api_version": "2025-09-30.clover",
                    "livemode": False,
                    "data": {
                        "object": {
                            "id": result["id"],
                            "client_reference_id": str(practice.id),
                            "subscription": subscription["id"],
                        }
                    },
                }
            )
        return result

    monkeypatch.setattr(stripe_billing, "_request", racing_request)
    with pytest.raises(stripe_billing.StripeCheckoutAlreadyCompleted):
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings",
            cancel_url="https://app.example/settings",
            now=NOW,
        )
    practice.refresh()
    assert practice.subscription_status == "active"
    assert practice.stripe_checkout_session_id is None


def test_completed_checkout_rebinds_after_terminal_old_subscription(
    remote: FakeStripe,
):
    practice = _practice()
    practice.stripe_customer_id = "cus_test_123"
    practice.stripe_subscription_id = "sub_old_canceled"
    practice.subscription_status = "canceled"
    practice.stripe_checkout_session_id = "cs_new_complete"
    practice.save()
    new = _subscription(practice, subscription_id="sub_new_active")
    remote.subscriptions[new["id"]] = new
    remote.sessions["cs_new_complete"] = {
        "id": "cs_new_complete",
        "url": None,
        "expires_at": int((NOW + timedelta(hours=1)).timestamp()),
        "client_reference_id": str(practice.id),
        "livemode": False,
        "mode": "subscription",
        "status": "complete",
        "subscription": new["id"],
    }

    with pytest.raises(stripe_billing.StripeCheckoutAlreadyCompleted):
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings",
            cancel_url="https://app.example/settings",
            now=NOW,
        )
    practice.refresh()
    assert practice.stripe_subscription_id == "sub_new_active"
    assert practice.subscription_status == "active"


@pytest.mark.parametrize("mismatch", ["account", "price"])
def test_account_and_price_mismatch_fail_closed(remote: FakeStripe, mismatch: str):
    practice = _practice()
    subscription = _subscription(
        practice,
        price_id="price_wrong" if mismatch == "price" else "price_test_x",
    )
    remote.subscriptions[subscription["id"]] = subscription
    if mismatch == "account":
        remote.account_id = "acct_wrong"

    with pytest.raises(stripe_billing.StripeStateInvalid):
        stripe_billing.apply_stripe_event(
            _event(
                "customer.subscription.updated",
                subscription,
                event_id=f"evt_bad_{mismatch}",
                created=int(NOW.timestamp()),
            )
        )
    practice.refresh()
    assert practice.subscription_status is None


@pytest.mark.parametrize("contract_break", ["wrong_price", "multiple_items"])
def test_bound_invalid_subscription_contract_revokes_stale_active_access(
    remote: FakeStripe,
    contract_break: str,
):
    practice = _practice()
    subscription = _subscription(practice)
    practice.stripe_customer_id = subscription["customer"]
    practice.stripe_subscription_id = subscription["id"]
    practice.subscription_status = "active"
    practice.save()
    if contract_break == "wrong_price":
        subscription["items"]["data"][0]["price"] = {"id": "price_other"}
    else:
        subscription["items"]["data"].append(
            {
                "id": "si_extra",
                "quantity": 1,
                "price": {"id": "price_test_x"},
            }
        )
    remote.subscriptions[subscription["id"]] = subscription

    assert stripe_billing.apply_stripe_event(
        _event(
            "customer.subscription.updated",
            subscription,
            event_id=f"evt_contract_{contract_break}",
            created=int(NOW.timestamp()),
        )
    )
    practice.refresh()
    assert practice.subscription_status == "invalid"
    assert practice.stripe_subscription_id == subscription["id"]
    assert practice.stripe_customer_id == subscription["customer"]
    assert not practice.entitled(at=NOW)


def test_unbound_invalid_subscription_contract_never_binds(remote: FakeStripe):
    practice = _practice()
    subscription = _subscription(practice, price_id="price_other")
    remote.subscriptions[subscription["id"]] = subscription

    with pytest.raises(stripe_billing.StripeStateInvalid):
        stripe_billing.apply_stripe_event(
            _event(
                "customer.subscription.created",
                subscription,
                event_id="evt_unbound_invalid_contract",
                created=int(NOW.timestamp()),
            )
        )
    practice.refresh()
    assert practice.stripe_subscription_id is None
    assert practice.subscription_status is None


@pytest.mark.parametrize(
    ("field", "value"),
    [("billing_scheme", "tiered"), ("transform_quantity", {"divide_by": 10})],
)
def test_transformed_or_tiered_price_is_rejected(
    remote: FakeStripe,
    field: str,
    value: object,
):
    remote.price[field] = value
    with pytest.raises(stripe_billing.StripeStateInvalid):
        stripe_billing.create_checkout_session(
            _practice(),
            customer_email="owner@example.com",
            success_url="https://app.example/settings",
            cancel_url="https://app.example/settings",
            now=NOW,
        )


def test_archived_expected_price_allows_maintenance_but_blocks_new_checkout(
    remote: FakeStripe,
):
    practice = _practice()
    subscription = _subscription(practice)
    remote.subscriptions[subscription["id"]] = subscription
    practice.stripe_subscription_id = subscription["id"]
    practice.stripe_customer_id = subscription["customer"]
    practice.save()
    remote.price["active"] = False

    stripe_billing.refresh_subscription_state(practice)
    assert practice.subscription_status == "active"
    with pytest.raises(stripe_billing.StripeStateInvalid, match="not active"):
        stripe_billing.create_checkout_session(
            _practice(),
            customer_email="owner@example.com",
            success_url="https://app.example/settings",
            cancel_url="https://app.example/settings",
            now=NOW,
        )


def test_organization_seat_count_only_includes_live_claimed_roster_members():
    domain = f"seat-count-{secrets.token_hex(4)}.example.com"
    practice, code = _organization_practice(domain, now=NOW)
    claimed = [_organization_doctor(f"claimed-{index}@{domain}") for index in range(3)]
    unclaimed = _organization_doctor(f"unclaimed@{domain}")
    _import_organization_roster(
        practice,
        [doctor.email or "" for doctor in [*claimed, unclaimed]],
        now=NOW,
    )

    claimed_members = [
        claim_roster_access(
            doctor,
            supplied_code=code,
            now=NOW + timedelta(seconds=index + 1),
        )[1]
        for index, doctor in enumerate(claimed)
    ]
    revoke_roster_access(
        practice,
        claimed[-1].email or "",
        operator_ref="stripe-billing-test",
        reason="Exclude the revoked organization seat",
        now=NOW + timedelta(seconds=10),
    )

    assert all(member.attested_at is None for member in claimed_members)
    assert stripe_billing.attested_member_count(practice) == 2


def test_organization_claim_and_revoke_mark_quantity_reconciliation():
    domain = f"seat-sync-{secrets.token_hex(4)}.example.com"
    practice, code = _organization_practice(domain, now=NOW)
    doctors = [
        _organization_doctor(f"clinician-{index}@{domain}") for index in range(2)
    ]
    _import_organization_roster(
        practice,
        [doctor.email or "" for doctor in doctors],
        now=NOW,
    )
    practice.stripe_customer_id = "cus_org_seat_sync"
    practice.stripe_subscription_id = "sub_org_seat_sync"
    practice.subscription_status = "active"
    practice.save()

    _, first_member = claim_roster_access(
        doctors[0], supplied_code=code, now=NOW + timedelta(seconds=1)
    )
    practice.refresh()
    first_version = practice.stripe_quantity_sync_version
    assert first_member.attested_at is None
    assert practice.stripe_quantity_sync_required
    assert practice.stripe_quantity_sync_target == 1
    assert first_version > 0

    claim_roster_access(doctors[1], supplied_code=code, now=NOW + timedelta(seconds=2))
    practice.refresh()
    second_version = practice.stripe_quantity_sync_version
    assert practice.stripe_quantity_sync_required
    assert practice.stripe_quantity_sync_target == 2
    assert second_version > first_version

    revoked = revoke_roster_access(
        practice,
        doctors[1].email or "",
        operator_ref="stripe-billing-test",
        reason="Remove the organization seat from billing",
        now=NOW + timedelta(seconds=3),
    )
    practice.refresh()
    assert revoked.member is not None
    assert revoked.member.access_revoked_at is not None
    assert practice.stripe_quantity_sync_required
    assert practice.stripe_quantity_sync_target == 1
    assert practice.stripe_quantity_sync_version > second_version


def test_quantity_sync_failure_is_durable_and_reconciles_later(remote: FakeStripe):
    practice = _practice()
    subscription = _subscription(practice, quantity=0)
    remote.subscriptions[subscription["id"]] = subscription
    practice.stripe_customer_id = subscription["customer"]
    practice.stripe_subscription_id = subscription["id"]
    practice.subscription_status = "active"
    practice.save()

    stripe_billing.mark_quantity_sync_required(practice)
    remote.fail_subscription_reads = True
    assert not stripe_billing.sync_subscription_quantity(practice)
    practice.refresh()
    assert practice.stripe_quantity_sync_required
    assert practice.stripe_quantity_sync_target == 1
    assert practice.stripe_quantity_sync_attempts == 1
    assert practice.stripe_quantity_sync_error

    remote.fail_subscription_reads = False
    assert stripe_billing.sync_subscription_quantity(practice)
    practice.refresh()
    assert not practice.stripe_quantity_sync_required
    assert practice.stripe_quantity_synced_at is not None
    assert remote.subscriptions[subscription["id"]]["items"]["data"][0]["quantity"] == 1


def test_quantity_sync_never_mutates_a_bound_subscription_with_identity_drift(
    remote: FakeStripe,
):
    practice = _practice()
    subscription = _subscription(practice, quantity=2)
    practice.stripe_customer_id = subscription["customer"]
    practice.stripe_subscription_id = subscription["id"]
    practice.subscription_status = "active"
    practice.save()
    subscription["customer"] = "cus_wrong_customer"
    remote.subscriptions[subscription["id"]] = subscription

    stripe_billing.mark_quantity_sync_required(practice)

    assert not stripe_billing.sync_subscription_quantity(practice)
    practice.refresh()
    assert practice.subscription_status == "invalid"
    assert not any(
        path.startswith("/subscription_items/")
        for _method, path, _data, _key in remote.calls
    )


def test_repeated_quantity_target_uses_a_new_idempotency_key_per_obligation(
    remote: FakeStripe,
):
    practice = _practice()
    subscription = _subscription(practice, quantity=2)
    remote.subscriptions[subscription["id"]] = subscription
    practice.stripe_customer_id = subscription["customer"]
    practice.stripe_subscription_id = subscription["id"]
    practice.subscription_status = "active"
    practice.save()

    stripe_billing.mark_quantity_sync_required(practice)
    assert stripe_billing.sync_subscription_quantity(practice)
    first_key = [
        call[3] for call in remote.calls if call[1].startswith("/subscription_items/")
    ][-1]

    remote.subscriptions[subscription["id"]]["items"]["data"][0]["quantity"] = 2
    stripe_billing.mark_quantity_sync_required(practice)
    assert stripe_billing.sync_subscription_quantity(practice)
    second_key = [
        call[3] for call in remote.calls if call[1].startswith("/subscription_items/")
    ][-1]

    assert first_key and second_key and first_key != second_key


def test_unsupported_event_type_is_ignored_without_remote_call(remote: FakeStripe):
    assert not stripe_billing.apply_stripe_event(
        {
            "id": "evt_invoice",
            "type": "invoice.finalized",
            "created": int(NOW.timestamp()),
            "livemode": False,
            "data": {"object": {}},
        }
    )
    assert remote.calls == []


def test_signature_rejects_malformed_headers():
    payload = json.dumps({"id": "evt_test"}).encode()
    assert not stripe_billing.verify_webhook_signature(payload, None)
    assert not stripe_billing.verify_webhook_signature(payload, "v1=abc")
    assert not stripe_billing.verify_webhook_signature(payload, "t=notanumber,v1=abc")
