"""Stripe billing invariants that protect entitlement and prevent duplicates."""

import hashlib
import hmac
import json
import secrets
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone, tzinfo

import httpx
import pytest
from typer.testing import CliRunner

import app.configuration.stripe as stripe_config
from app.cli import app as cli_app
from app.services import organization_access, stripe_billing
from app.services.organization_access import (
    RosterApprovalInput,
    claim_roster_access,
    configure_provider_organization,
    import_roster_approvals,
    revoke_roster_access,
)
from app.services.practice_access import (
    PracticeAccessChangeError,
    set_billing_exemption,
)
from app.services.public_entitlements import ClinicianSeatPolicyError

from activemodel.session_manager import global_session
from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember, PracticeRole
from app.models.practice_access_audit import PracticeAccessAudit
from app.models.stripe_event_inbox import StripeEventInbox
from app.models.stripe_event_receipt import StripeEventReceipt

WEBHOOK_SECRET = "whsec_test_secret"
NOW = datetime(2026, 8, 20, 20, 0, tzinfo=timezone.utc)


def _invalid_seat_ledger(*_args, **_kwargs) -> int:
    raise ClinicianSeatPolicyError("test invalid seat ledger")


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
        if path == "/checkout/sessions" and method == "GET":
            return {
                "object": "list",
                "data": [deepcopy(value) for value in self.sessions.values()],
                "has_more": False,
            }
        if path == "/checkout/sessions" and method == "POST":
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
            if session["status"] != "open":
                raise AssertionError("only open Checkout Sessions can be expired")
            session["status"] = "expired"
            session["url"] = None
            return deepcopy(session)
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
    practice = Practice(
        legal_name="Mirror Test Practice, PLLC",
        join_code=f"M{secrets.token_hex(4).upper()[:7]}",
        email_domain="example.com",
        origin_channel="direct",
    ).save()
    owner = Doctor(
        clerk_id=f"user_stripe_owner_{secrets.token_hex(8)}",
        email=f"owner-{secrets.token_hex(4)}@example.com",
        biography="",
    ).save()
    PracticeMember(
        practice_id=practice.id,
        doctor_id=owner.id,
        role=PracticeRole.owner.value,
        attested_at=NOW,
    ).save()
    return practice


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
        "metadata": {
            "practice_id": str(practice.id),
            "checkout_policy": stripe_billing.CHECKOUT_IDEMPOTENCY_POLICY_VERSION,
            "entitlement_contract": "public-capability-entitlements.v1",
            "trial_clock": stripe_billing.LEGACY_TRIAL_CLOCK_POLICY,
        },
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


def _current_checkout_metadata(practice: Practice) -> dict[str, str]:
    return {
        "checkout_policy": stripe_billing.CHECKOUT_IDEMPOTENCY_POLICY_VERSION,
        "checkout_attempt": str(practice.stripe_checkout_attempt),
        "practice_id": str(practice.id),
        "entitlement_contract": "public-capability-entitlements.v1",
        "trial_clock": (
            practice.stripe_trial_clock_policy
            or stripe_billing.LEGACY_TRIAL_CLOCK_POLICY
        ),
    }


def _paid_pilot_subscription(
    practice: Practice,
    *,
    status: str = "trialing",
    trial_days: int = 14,
) -> dict:
    subscription = _subscription(practice, status=status)
    subscription["metadata"].update(
        {
            "trial_clock": stripe_billing.CHECKOUT_TRIAL_CLOCK_POLICY,
            "trial_days": "14",
        }
    )
    subscription["trial_start"] = int(NOW.timestamp())
    subscription["trial_end"] = int((NOW + timedelta(days=trial_days)).timestamp())
    return subscription


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


def _checkout_completed_event(
    practice: Practice,
    subscription: dict,
    *,
    event_id: str,
    created: int,
) -> dict:
    session_id = f"cs_{event_id.removeprefix('evt_')}"
    practice.stripe_checkout_session_id = session_id
    practice.stripe_trial_clock_policy = subscription["metadata"]["trial_clock"]
    if practice.stripe_trial_clock_policy == stripe_billing.CHECKOUT_TRIAL_CLOCK_POLICY:
        practice.trial_ends_at = datetime.fromtimestamp(
            subscription["trial_end"],
            tz=timezone.utc,
        )
    practice.save()
    return {
        "id": event_id,
        "type": "checkout.session.completed",
        "created": created,
        "api_version": "2025-09-30.clover",
        "livemode": False,
        "data": {
            "object": {
                "id": session_id,
                "client_reference_id": str(practice.id),
                "subscription": subscription["id"],
            }
        },
    }


def _bind_verified_public_subscription(
    practice: Practice,
    subscription: dict,
    *,
    status: str = "active",
    trial_ends_at: datetime | None = None,
) -> None:
    practice.public_onboarding_required = True
    practice.stripe_customer_id = subscription["customer"]
    practice.stripe_subscription_id = subscription["id"]
    practice.subscription_status = status
    practice.trial_ends_at = trial_ends_at
    practice.stripe_state_version = max(practice.stripe_state_version, 1)
    practice.save()


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
    assert not stripe_config.stripe_portal_configured()
    assert not stripe_config.stripe_billing_configured()

    monkeypatch.setattr(stripe_config, "STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setattr(stripe_config, "STRIPE_BILLING_ENABLED", False)
    assert stripe_config.stripe_runtime_configured()
    assert stripe_config.stripe_webhook_configured()
    assert stripe_config.stripe_portal_configured()
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
    assert data["payment_method_collection"] == "always"
    stripe_trial_end = int(data["subscription_data[trial_end]"])
    carried_trial_end = datetime.fromtimestamp(stripe_trial_end, tz=timezone.utc)
    assert carried_trial_end >= practice.trial_ends_at
    assert carried_trial_end < practice.trial_ends_at + timedelta(seconds=1)
    assert idempotency_key and idempotency_key.startswith("cosmas-checkout-")


def test_checkout_rejects_browser_parser_differential_hosted_url(
    remote: FakeStripe, monkeypatch: pytest.MonkeyPatch
):
    practice = _practice()
    original_request = stripe_billing._request

    def return_hostile_url(method, path, data=None, *, idempotency_key=None):
        response = original_request(method, path, data, idempotency_key=idempotency_key)
        if method == "POST" and path == "/checkout/sessions":
            response["url"] = (
                "https://evil.example\\@checkout.stripe.com/c/pay/parser-differential"
            )
        return response

    monkeypatch.setattr(stripe_billing, "_request", return_hostile_url)

    with pytest.raises(stripe_billing.StripeStateInvalid, match="unexpected origin"):
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings?billing=success",
            cancel_url="https://app.example/settings?billing=cancelled",
            now=NOW,
        )

    practice.refresh()
    assert practice.stripe_checkout_session_url is None


def test_checkout_never_sends_or_inspects_legacy_npi(remote: FakeStripe):
    practice = _practice()
    practice.npi = "1234567893"
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
    serialized_request = json.dumps(checkout_call[2], sort_keys=True).lower()
    assert "npi" not in serialized_request
    assert "1234567893" not in serialized_request


def test_cached_checkout_resume_is_fenced_by_concurrent_billing_change(
    remote: FakeStripe, monkeypatch: pytest.MonkeyPatch
):
    practice = _practice()
    practice.stripe_checkout_session_id = "cs_resume_billing_fence"
    practice.stripe_checkout_session_url = "https://checkout.stripe.com/c/pay/fence"
    practice.stripe_checkout_session_expires_at = NOW + timedelta(hours=1)
    practice.stripe_trial_clock_policy = stripe_billing.CHECKOUT_TRIAL_CLOCK_POLICY
    practice.trial_ends_at = NOW + timedelta(days=14)
    practice.save()
    remote.sessions["cs_resume_billing_fence"] = {
        "id": "cs_resume_billing_fence",
        "url": "https://checkout.stripe.com/c/pay/fence",
        "expires_at": int((NOW + timedelta(hours=1)).timestamp()),
        "client_reference_id": str(practice.id),
        "livemode": False,
        "mode": "subscription",
        "status": "open",
        "subscription": None,
        "payment_method_collection": "always",
        "metadata": _current_checkout_metadata(practice),
    }
    original_request = stripe_billing._request

    def bind_subscription_during_session_get(
        method, path, data=None, *, idempotency_key=None
    ):
        result = original_request(method, path, data, idempotency_key=idempotency_key)
        if path == "/checkout/sessions/cs_resume_billing_fence":
            practice.stripe_customer_id = "cus_bound_during_resume"
            practice.stripe_subscription_id = "sub_bound_during_resume"
            practice.subscription_status = "active"
            practice.stripe_state_version += 1
            practice.save()
        return result

    monkeypatch.setattr(
        stripe_billing, "_request", bind_subscription_during_session_get
    )

    with pytest.raises(stripe_billing.StripeStateConflict, match="changed while"):
        stripe_billing._recover_checkout_session(practice, current=NOW)

    practice.refresh()
    assert practice.stripe_subscription_id == "sub_bound_during_resume"
    assert practice.stripe_checkout_session_id == "cs_resume_billing_fence"


def test_cached_checkout_cannot_resume_over_nonterminal_subscription(
    remote: FakeStripe,
):
    practice = _practice()
    practice.stripe_customer_id = "cus_existing_active"
    practice.stripe_subscription_id = "sub_existing_active"
    practice.subscription_status = "active"
    practice.stripe_state_version = 1
    practice.stripe_checkout_session_id = "cs_stale_over_active"
    practice.stripe_checkout_session_url = "https://checkout.stripe.com/c/pay/stale"
    practice.stripe_checkout_session_expires_at = NOW + timedelta(hours=1)
    practice.stripe_trial_clock_policy = stripe_billing.CHECKOUT_TRIAL_CLOCK_POLICY
    practice.save()
    remote.sessions["cs_stale_over_active"] = {
        "id": "cs_stale_over_active",
        "url": "https://checkout.stripe.com/c/pay/stale",
        "expires_at": int((NOW + timedelta(hours=1)).timestamp()),
        "client_reference_id": str(practice.id),
        "livemode": False,
        "mode": "subscription",
        "status": "open",
        "subscription": None,
        "payment_method_collection": "always",
        "metadata": _current_checkout_metadata(practice),
    }

    with pytest.raises(stripe_billing.StripeCheckoutAlreadyCompleted):
        stripe_billing._recover_checkout_session(practice, current=NOW)

    assert not any(
        call[1] == "/checkout/sessions/cs_stale_over_active" for call in remote.calls
    )


def test_new_checkout_anchors_exact_trial_clock_without_local_entitlement(
    remote: FakeStripe,
):
    practice = _practice()
    practice.public_onboarding_required = True
    practice.save()

    url = stripe_billing.create_checkout_session(
        practice,
        customer_email="owner@example.com",
        success_url="https://app.example/settings?billing=success",
        cancel_url="https://app.example/settings?billing=cancelled",
        now=NOW,
    )

    assert url == "https://checkout.stripe.com/c/pay/test"
    checkout_call = next(
        call for call in remote.calls if call[1] == "/checkout/sessions"
    )
    data = checkout_call[2]
    assert data
    assert data["payment_method_collection"] == "always"
    assert "subscription_data[trial_period_days]" not in data
    assert datetime.fromtimestamp(
        int(data["subscription_data[trial_end]"]),
        tz=timezone.utc,
    ) == NOW + timedelta(days=14)
    assert data["metadata[trial_clock]"] == "checkout-fixed-14-day-v2"
    assert data["metadata[entitlement_contract]"] == (
        "public-capability-entitlements.v1"
    )
    assert "allow_promotion_codes" not in data
    practice.refresh()
    assert practice.trial_ends_at == NOW + timedelta(days=14)
    assert practice.stripe_checkout_session_id == "cs_test_idempotent"
    assert not practice.entitled(at=NOW)


def test_delayed_checkout_completion_preserves_only_remaining_trial(
    remote: FakeStripe,
):
    practice = _practice()
    practice.public_onboarding_required = True
    practice.save()
    stripe_billing.create_checkout_session(
        practice,
        customer_email="owner@example.com",
        success_url="https://app.example/settings?billing=success",
        cancel_url="https://app.example/settings?billing=cancelled",
        now=NOW,
    )
    completion_time = NOW + timedelta(days=3)
    subscription = _paid_pilot_subscription(practice)
    subscription["trial_start"] = int(completion_time.timestamp())
    subscription["trial_end"] = int((NOW + timedelta(days=14)).timestamp())
    remote.subscriptions[subscription["id"]] = subscription

    assert stripe_billing.apply_stripe_event(
        {
            "id": "evt_delayed_checkout_completion",
            "type": "checkout.session.completed",
            "created": int(completion_time.timestamp()),
            "api_version": "2025-09-30.clover",
            "livemode": False,
            "data": {
                "object": {
                    "id": "cs_test_idempotent",
                    "client_reference_id": str(practice.id),
                    "subscription": subscription["id"],
                }
            },
        }
    )

    practice.refresh()
    assert practice.subscription_status == "trialing"
    assert practice.trial_ends_at == NOW + timedelta(days=14)
    assert practice.trial_ends_at - completion_time == timedelta(days=11)
    assert practice.entitled(at=completion_time)


@pytest.mark.parametrize("trial_end_offset", [-1, 1, None])
def test_checkout_completion_rejects_shifted_or_missing_fixed_trial_end(
    remote: FakeStripe,
    trial_end_offset: int | None,
):
    practice = _practice()
    practice.public_onboarding_required = True
    practice.save()
    stripe_billing.create_checkout_session(
        practice,
        customer_email="owner@example.com",
        success_url="https://app.example/settings?billing=success",
        cancel_url="https://app.example/settings?billing=cancelled",
        now=NOW,
    )
    subscription = _paid_pilot_subscription(practice)
    if trial_end_offset is None:
        subscription["trial_end"] = None
    else:
        subscription["trial_end"] += trial_end_offset
    remote.subscriptions[subscription["id"]] = subscription

    with pytest.raises(stripe_billing.StripeStateInvalid):
        stripe_billing.apply_stripe_event(
            {
                "id": f"evt_shifted_initial_clock_{trial_end_offset}",
                "type": "checkout.session.completed",
                "created": int((NOW + timedelta(hours=1)).timestamp()),
                "api_version": "2025-09-30.clover",
                "livemode": False,
                "data": {
                    "object": {
                        "id": "cs_test_idempotent",
                        "client_reference_id": str(practice.id),
                        "subscription": subscription["id"],
                    }
                },
            }
        )

    practice.refresh()
    assert practice.stripe_subscription_id is None
    assert practice.trial_ends_at == NOW + timedelta(days=14)
    assert not practice.entitled(at=NOW + timedelta(hours=1))


@pytest.mark.parametrize("seconds_from_boundary", [0, -1])
def test_cached_checkout_at_minimum_trial_lead_is_retired_without_reset(
    remote: FakeStripe,
    seconds_from_boundary: int,
):
    practice = _practice()
    fixed_end = (
        NOW
        + stripe_billing.MINIMUM_CHECKOUT_TRIAL_LEAD
        + stripe_billing.CHECKOUT_TRIAL_LEAD_SAFETY
        + timedelta(seconds=seconds_from_boundary)
    )
    practice.stripe_checkout_session_id = "cs_trial_clock_boundary"
    practice.stripe_checkout_session_url = "https://checkout.stripe.com/c/pay/boundary"
    practice.stripe_checkout_session_expires_at = NOW + timedelta(hours=1)
    practice.stripe_trial_clock_policy = stripe_billing.CHECKOUT_TRIAL_CLOCK_POLICY
    practice.trial_ends_at = fixed_end
    practice.save()
    metadata = _current_checkout_metadata(practice)
    metadata["trial_days"] = "14"
    remote.sessions["cs_trial_clock_boundary"] = {
        "id": "cs_trial_clock_boundary",
        "url": "https://checkout.stripe.com/c/pay/boundary",
        "expires_at": int((NOW + timedelta(hours=1)).timestamp()),
        "client_reference_id": str(practice.id),
        "livemode": False,
        "mode": "subscription",
        "status": "open",
        "subscription": None,
        "payment_method_collection": "always",
        "metadata": metadata,
    }

    with pytest.raises(stripe_billing.StripeTrialClockReconciliationRequired):
        stripe_billing._recover_checkout_session(practice, current=NOW)

    practice.refresh()
    assert remote.sessions["cs_trial_clock_boundary"]["status"] == "expired"
    assert practice.stripe_checkout_session_id is None
    assert practice.trial_ends_at == fixed_end
    with pytest.raises(stripe_billing.StripeTrialClockReconciliationRequired):
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings?billing=success",
            cancel_url="https://app.example/settings?billing=cancelled",
            now=NOW,
        )
    assert not any(
        method == "POST" and path == "/checkout/sessions"
        for method, path, _data, _key in remote.calls
    )


def test_cached_checkout_above_minimum_trial_lead_remains_resumable(
    remote: FakeStripe,
):
    practice = _practice()
    fixed_end = (
        NOW
        + stripe_billing.MINIMUM_CHECKOUT_TRIAL_LEAD
        + stripe_billing.CHECKOUT_TRIAL_LEAD_SAFETY
        + timedelta(seconds=1)
    )
    practice.stripe_checkout_session_id = "cs_trial_clock_above_boundary"
    practice.stripe_checkout_session_url = "https://checkout.stripe.com/c/pay/above"
    practice.stripe_checkout_session_expires_at = NOW + timedelta(hours=1)
    practice.stripe_trial_clock_policy = stripe_billing.CHECKOUT_TRIAL_CLOCK_POLICY
    practice.trial_ends_at = fixed_end
    practice.save()
    metadata = _current_checkout_metadata(practice)
    metadata["trial_days"] = "14"
    remote.sessions["cs_trial_clock_above_boundary"] = {
        "id": "cs_trial_clock_above_boundary",
        "url": "https://checkout.stripe.com/c/pay/above",
        "expires_at": int((NOW + timedelta(hours=1)).timestamp()),
        "client_reference_id": str(practice.id),
        "livemode": False,
        "mode": "subscription",
        "status": "open",
        "subscription": None,
        "payment_method_collection": "always",
        "metadata": metadata,
    }

    assert (
        stripe_billing._recover_checkout_session(practice, current=NOW)
        == "https://checkout.stripe.com/c/pay/above"
    )
    practice.refresh()
    assert practice.stripe_checkout_session_id == "cs_trial_clock_above_boundary"
    assert practice.trial_ends_at == fixed_end


def test_provider_created_session_consumes_clock_after_local_version_race(
    remote: FakeStripe,
    monkeypatch: pytest.MonkeyPatch,
):
    practice = _practice()
    original_request = stripe_billing._request

    def race_after_creation(method, path, data=None, *, idempotency_key=None):
        response = original_request(method, path, data, idempotency_key=idempotency_key)
        if method == "POST" and path == "/checkout/sessions":
            with stripe_billing._isolated_state_session() as session:
                row = session.get(Practice, practice.id)
                assert row is not None
                row.stripe_state_version += 1
                session.add(row)
                session.commit()
        return response

    monkeypatch.setattr(stripe_billing, "_request", race_after_creation)
    with pytest.raises(stripe_billing.StripeStateConflict, match="changed while"):
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings?billing=success",
            cancel_url="https://app.example/settings?billing=cancelled",
            now=NOW,
        )

    practice.refresh()
    assert remote.sessions["cs_test_idempotent"]["status"] == "expired"
    assert practice.stripe_checkout_session_id is None
    assert practice.trial_ends_at == NOW + timedelta(days=14)


def test_managed_practice_cannot_enter_public_paid_pilot_checkout(remote: FakeStripe):
    practice = _practice()
    practice.negotiated_baa_required = True
    practice.negotiated_baa_on_file = True
    practice.negotiated_baa_effective_date = NOW - timedelta(days=1)
    practice.negotiated_baa_reference = "test://managed-baa"
    practice.billing_exempt = False
    practice.billing_exempt_until = NOW - timedelta(days=1)
    practice.save()

    with pytest.raises(stripe_billing.StripeManagedBilling):
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings",
            cancel_url="https://app.example/settings",
            now=NOW,
        )

    assert remote.calls == []


def test_active_billing_exemption_cannot_enter_public_paid_pilot_checkout(
    remote: FakeStripe,
):
    practice = _practice()
    practice.billing_exempt = True
    practice.billing_exempt_until = None
    practice.save()

    with pytest.raises(stripe_billing.StripeManagedBilling):
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings?billing=success",
            cancel_url="https://app.example/settings?billing=cancelled",
            now=NOW,
        )

    assert remote.calls == []


@pytest.mark.parametrize(
    ("customer_id", "subscription_id", "state_version"),
    [
        ("cus_unbound", None, 1),
        (None, "sub_unbound", 1),
        ("customer_untrusted", "sub_bound", 1),
        ("cus_bound", "subscription_untrusted", 1),
        ("cus_bound", "sub_bound", 0),
    ],
)
def test_checkout_rejects_incomplete_core_binding_before_stripe_io(
    remote: FakeStripe,
    customer_id: str | None,
    subscription_id: str | None,
    state_version: int,
):
    practice = _practice()
    practice.public_onboarding_required = True
    practice.stripe_customer_id = customer_id
    practice.stripe_subscription_id = subscription_id
    practice.subscription_status = "canceled" if subscription_id else None
    practice.stripe_state_version = state_version
    practice.save()

    with pytest.raises(stripe_billing.StripeCoreBindingInvalid, match="authoritative"):
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings?billing=success",
            cancel_url="https://app.example/settings?billing=cancelled",
            now=NOW,
        )

    assert remote.calls == []


def test_checkout_rejects_status_only_core_projection_before_stripe_io(
    remote: FakeStripe,
):
    practice = _practice()
    practice.public_onboarding_required = True
    practice.subscription_status = "active"
    practice.save()

    with pytest.raises(stripe_billing.StripeCoreBindingInvalid, match="authoritative"):
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings?billing=success",
            cancel_url="https://app.example/settings?billing=cancelled",
            now=NOW,
        )

    assert remote.calls == []


def test_legacy_zero_version_binding_can_be_authoritatively_refreshed(
    remote: FakeStripe,
):
    practice = _practice()
    subscription = _subscription(
        practice,
        subscription_id="sub_legacy_zero_version",
        customer_id="cus_legacy_zero_version",
        status="past_due",
    )
    remote.subscriptions[subscription["id"]] = subscription
    practice.public_onboarding_required = False
    practice.stripe_customer_id = subscription["customer"]
    practice.stripe_subscription_id = subscription["id"]
    practice.subscription_status = "past_due"
    practice.stripe_state_version = 0
    practice.save()

    stripe_billing.refresh_subscription_state(practice)

    practice.refresh()
    assert practice.subscription_status == "past_due"
    assert practice.stripe_state_version == 1


def test_checkout_reuses_verified_customer_from_terminal_legacy_subscription(
    remote: FakeStripe,
):
    practice = _practice()
    subscription = _subscription(
        practice,
        subscription_id="sub_terminal_legacy",
        customer_id="cus_terminal_legacy",
        status="canceled",
        price_id="price_retired",
        quantity=37,
    )
    subscription["metadata"] = {"practice_id": str(practice.id)}
    remote.subscriptions[subscription["id"]] = subscription
    _bind_verified_public_subscription(practice, subscription, status="canceled")

    assert (
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings?billing=success",
            cancel_url="https://app.example/settings?billing=cancelled",
            now=NOW,
        )
        == "https://checkout.stripe.com/c/pay/test"
    )

    checkout_call = next(
        call for call in remote.calls if call[1] == "/checkout/sessions"
    )
    assert checkout_call[2]
    assert checkout_call[2]["customer"] == "cus_terminal_legacy"


def test_new_open_checkout_with_subscription_is_retired_before_url_return(
    remote: FakeStripe,
    monkeypatch: pytest.MonkeyPatch,
):
    practice = _practice()
    original_request = stripe_billing._request

    def return_open_session_with_subscription(
        method, path, data=None, *, idempotency_key=None
    ):
        result = original_request(method, path, data, idempotency_key=idempotency_key)
        if method == "POST" and path == "/checkout/sessions":
            result["subscription"] = "sub_unexpected_open_checkout"
        return result

    retired: list[str] = []

    def record_retirement(_practice, session, **_kwargs):
        retired.append(session["id"])

    monkeypatch.setattr(
        stripe_billing,
        "_request",
        return_open_session_with_subscription,
    )
    monkeypatch.setattr(
        stripe_billing,
        "_retire_open_checkout_session",
        record_retirement,
    )

    with pytest.raises(stripe_billing.StripeStateInvalid, match="unexpectedly created"):
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings?billing=success",
            cancel_url="https://app.example/settings?billing=cancelled",
            now=NOW,
        )

    assert retired == ["cs_test_idempotent"]


def test_portal_session_uses_pinned_restricted_configuration(remote: FakeStripe):
    practice = _practice()
    subscription = _subscription(
        practice,
        subscription_id="sub_portal",
        customer_id="cus_portal",
        status="past_due",
    )
    remote.subscriptions[subscription["id"]] = subscription
    _bind_verified_public_subscription(
        practice,
        subscription,
        status="past_due",
    )

    url = stripe_billing.create_portal_session(
        practice, return_url="https://app.example/settings"
    )

    assert url == "https://billing.stripe.com/p/session/test"
    portal_call = next(
        call for call in remote.calls if call[1] == "/billing_portal/sessions"
    )
    assert portal_call[2] == {
        "configuration": "bpc_test_restricted",
        "customer": "cus_portal",
        "return_url": "https://app.example/settings",
    }


def test_portal_rejects_browser_parser_differential_hosted_url(
    remote: FakeStripe, monkeypatch: pytest.MonkeyPatch
):
    practice = _practice()
    subscription = _subscription(
        practice,
        subscription_id="sub_portal_hostile_url",
        customer_id="cus_portal_hostile_url",
        status="past_due",
    )
    remote.subscriptions[subscription["id"]] = subscription
    _bind_verified_public_subscription(practice, subscription, status="past_due")
    original_request = stripe_billing._request

    def return_hostile_url(method, path, data=None, *, idempotency_key=None):
        response = original_request(method, path, data, idempotency_key=idempotency_key)
        if method == "POST" and path == "/billing_portal/sessions":
            response["url"] = (
                "https://evil.example\\@billing.stripe.com/p/parser-differential"
            )
        return response

    monkeypatch.setattr(stripe_billing, "_request", return_hostile_url)

    with pytest.raises(stripe_billing.StripeStateInvalid, match="unexpected origin"):
        stripe_billing.create_portal_session(
            practice, return_url="https://app.example/settings"
        )


def test_portal_session_accepts_verified_terminal_legacy_subscription_identity(
    remote: FakeStripe,
):
    practice = _practice()
    subscription = _subscription(
        practice,
        subscription_id="sub_portal_legacy",
        customer_id="cus_portal_legacy",
        status="canceled",
        price_id="price_retired",
        quantity=37,
    )
    subscription["metadata"] = {"practice_id": str(practice.id)}
    remote.subscriptions[subscription["id"]] = subscription
    _bind_verified_public_subscription(
        practice,
        subscription,
        status="canceled",
    )

    assert (
        stripe_billing.create_portal_session(
            practice, return_url="https://app.example/settings"
        )
        == "https://billing.stripe.com/p/session/test"
    )


def test_portal_accepts_remotely_verified_legacy_zero_version_binding(
    remote: FakeStripe,
):
    practice = _practice()
    subscription = _subscription(
        practice,
        subscription_id="sub_portal_zero_version",
        customer_id="cus_portal_zero_version",
        status="past_due",
    )
    remote.subscriptions[subscription["id"]] = subscription
    practice.public_onboarding_required = False
    practice.stripe_customer_id = subscription["customer"]
    practice.stripe_subscription_id = subscription["id"]
    practice.subscription_status = "past_due"
    practice.stripe_state_version = 0
    practice.save()

    assert (
        stripe_billing.create_portal_session(
            practice, return_url="https://app.example/settings"
        )
        == "https://billing.stripe.com/p/session/test"
    )


def test_portal_session_rejects_customer_only_binding_before_stripe_io(
    remote: FakeStripe,
):
    practice = _practice()
    practice.stripe_customer_id = "cus_unbound"
    practice.save()

    with pytest.raises(stripe_billing.StripeCoreBindingInvalid, match="authoritative"):
        stripe_billing.create_portal_session(
            practice, return_url="https://app.example/settings"
        )

    assert remote.calls == []


def test_portal_session_rejects_remote_customer_mismatch(remote: FakeStripe):
    practice = _practice()
    subscription = _subscription(
        practice,
        subscription_id="sub_portal_mismatch",
        customer_id="cus_expected",
        status="past_due",
    )
    _bind_verified_public_subscription(
        practice,
        subscription,
        status="past_due",
    )
    subscription["customer"] = "cus_other"
    remote.subscriptions[subscription["id"]] = subscription

    with pytest.raises(stripe_billing.StripeStateInvalid, match="binding changed"):
        stripe_billing.create_portal_session(
            practice, return_url="https://app.example/settings"
        )

    assert not any(call[1] == "/billing_portal/sessions" for call in remote.calls)


def test_portal_does_not_return_session_after_core_binding_race(
    remote: FakeStripe,
    monkeypatch: pytest.MonkeyPatch,
):
    practice = _practice()
    subscription = _subscription(
        practice,
        subscription_id="sub_portal_before_race",
        customer_id="cus_portal_stable",
        status="past_due",
    )
    remote.subscriptions[subscription["id"]] = subscription
    _bind_verified_public_subscription(practice, subscription, status="past_due")
    original_request = stripe_billing._request

    def rebind_during_portal_creation(method, path, data=None, *, idempotency_key=None):
        result = original_request(method, path, data, idempotency_key=idempotency_key)
        if path == "/billing_portal/sessions":
            practice.stripe_subscription_id = "sub_portal_after_race"
            practice.stripe_state_version += 1
            practice.save()
        return result

    monkeypatch.setattr(stripe_billing, "_request", rebind_during_portal_creation)

    with pytest.raises(stripe_billing.StripeStateConflict, match="during Portal"):
        stripe_billing.create_portal_session(
            practice, return_url="https://app.example/settings"
        )


def test_portal_session_fails_closed_without_pinned_configuration(
    remote: FakeStripe, monkeypatch: pytest.MonkeyPatch
):
    practice = _practice()
    practice.stripe_customer_id = "cus_portal"
    practice.save()
    monkeypatch.setattr(stripe_config, "STRIPE_PORTAL_CONFIGURATION_ID", "")

    with pytest.raises(stripe_billing.StripeNotConfigured):
        stripe_billing.create_portal_session(
            practice, return_url="https://app.example/settings"
        )

    assert remote.calls == []


def test_billing_metric_uses_only_allowlisted_redacted_fields(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        stripe_billing.log,
        "info",
        lambda event, **fields: captured.append((event, fields)),
    )
    raw_practice_id = "practice_raw_identifier"

    stripe_billing._emit_billing_metric(
        "webhook_delivery",
        outcome="accepted",
        practice_id=raw_practice_id,
        subscription_status="trialing",
        seat_quantity=20,
        webhook_lag_seconds=7,
        attempts=2,
    )

    assert captured[0][0] == "billing lifecycle metric"
    fields = captured[0][1]
    assert set(fields) == {
        "metric",
        "outcome",
        "practice_ref",
        "subscription_status",
        "seat_quantity",
        "webhook_lag_seconds",
        "attempts",
    }
    assert str(fields["practice_ref"]).startswith("hmac:")
    serialized = json.dumps(fields)
    assert raw_practice_id not in serialized
    for forbidden in (
        "subscription_id",
        "customer_id",
        "event_id",
        "email",
        "url",
        "patient",
        "note",
    ):
        assert forbidden not in fields


def test_checkout_post_response_validation_failure_emits_metric(
    remote: FakeStripe, monkeypatch: pytest.MonkeyPatch
):
    practice = _practice()
    emitted: list[tuple[str, str]] = []
    original_request = stripe_billing._request

    def invalid_checkout_response(method, path, data=None, *, idempotency_key=None):
        response = original_request(method, path, data, idempotency_key=idempotency_key)
        if path == "/checkout/sessions" and method == "POST":
            response["metadata"]["trial_clock"] = "tampered-clock"
        return response

    monkeypatch.setattr(stripe_billing, "_request", invalid_checkout_response)
    monkeypatch.setattr(
        stripe_billing,
        "_emit_billing_metric",
        lambda metric, *, outcome, **_fields: emitted.append((metric, outcome)),
    )

    with pytest.raises(stripe_billing.StripeStateInvalid):
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings",
            cancel_url="https://app.example/settings",
            now=NOW,
        )

    assert ("checkout_creation", "failed") in emitted


def test_checkout_retry_reuses_frozen_request_after_lost_response(
    remote: FakeStripe, monkeypatch: pytest.MonkeyPatch
):
    practice = _practice()
    original_request = stripe_billing._request
    lost_once = False

    def lose_first_response(method, path, data=None, *, idempotency_key=None):
        nonlocal lost_once
        response = original_request(method, path, data, idempotency_key=idempotency_key)
        if path == "/checkout/sessions" and not lost_once:
            lost_once = True
            raise httpx.ReadTimeout("response lost after Stripe committed")
        return response

    monkeypatch.setattr(stripe_billing, "_request", lose_first_response)
    with pytest.raises(httpx.ReadTimeout):
        stripe_billing.create_checkout_session(
            practice,
            customer_email="first-owner@example.com",
            success_url="https://app.example/first-success",
            cancel_url="https://app.example/first-cancel",
            now=NOW,
        )

    practice.refresh()
    assert practice.stripe_checkout_session_id is None
    assert practice.stripe_checkout_request_json is not None
    second_doctor = Doctor(
        clerk_id=f"user_checkout_retry_{secrets.token_hex(8)}",
        email="second-owner@example.com",
        biography="",
    ).save()
    PracticeMember(
        practice_id=practice.id,
        doctor_id=second_doctor.id,
        role=PracticeRole.workforce.value,
    ).save()

    url = stripe_billing.create_checkout_session(
        practice,
        customer_email="changed-owner@example.com",
        success_url="https://app.example/changed-success",
        cancel_url="https://app.example/changed-cancel",
        now=NOW + timedelta(minutes=1),
    )

    checkout_calls = [call for call in remote.calls if call[1] == "/checkout/sessions"]
    assert url == "https://checkout.stripe.com/c/pay/test"
    assert len(checkout_calls) == 2
    assert checkout_calls[0][2] == checkout_calls[1][2]
    assert checkout_calls[0][3] == checkout_calls[1][3]
    assert checkout_calls[1][2]["line_items[0][quantity]"] == "1"
    assert checkout_calls[1][2]["customer_email"] == "first-owner@example.com"
    assert checkout_calls[1][2]["subscription_data[trial_end]"] == str(
        int((NOW + timedelta(days=14)).timestamp())
    )
    practice.refresh()
    assert practice.stripe_checkout_request_json is None
    assert practice.stripe_checkout_request_created_at is None
    assert practice.trial_ends_at == NOW + timedelta(days=14)


@pytest.mark.parametrize(
    ("legacy_trial_end", "retry_delay"),
    [
        (None, stripe_billing.CHECKOUT_REQUEST_REPLAY_WINDOW),
        (NOW + timedelta(hours=1), timedelta(minutes=6)),
    ],
)
def test_checkout_request_outside_safe_replay_window_requires_reconciliation(
    remote: FakeStripe,
    monkeypatch: pytest.MonkeyPatch,
    legacy_trial_end: datetime | None,
    retry_delay: timedelta,
):
    practice = _practice()
    practice.trial_ends_at = legacy_trial_end
    practice.save()
    original_request = stripe_billing._request

    def lose_first_response(method, path, data=None, *, idempotency_key=None):
        response = original_request(method, path, data, idempotency_key=idempotency_key)
        if path == "/checkout/sessions" and method == "POST":
            raise httpx.ReadTimeout("response lost after Stripe committed")
        return response

    monkeypatch.setattr(stripe_billing, "_request", lose_first_response)
    with pytest.raises(httpx.ReadTimeout):
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings?billing=success",
            cancel_url="https://app.example/settings?billing=cancelled",
            now=NOW,
        )

    practice.refresh()
    assert practice.stripe_checkout_request_created_at == NOW
    checkout_calls_before_retry = len(
        [call for call in remote.calls if call[1] == "/checkout/sessions"]
    )
    with pytest.raises(stripe_billing.StripeCheckoutReconciliationRequired):
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings?billing=success",
            cancel_url="https://app.example/settings?billing=cancelled",
            now=NOW + retry_delay,
        )

    assert (
        len([call for call in remote.calls if call[1] == "/checkout/sessions"])
        == checkout_calls_before_retry
    )
    practice.refresh()
    assert practice.stripe_checkout_request_json is not None
    assert practice.stripe_checkout_request_created_at == NOW


def test_operator_recovery_expires_discovered_open_checkout_and_advances(
    remote: FakeStripe, monkeypatch: pytest.MonkeyPatch
):
    practice = _practice()
    original_request = stripe_billing._request

    def lose_response(method, path, data=None, *, idempotency_key=None):
        response = original_request(method, path, data, idempotency_key=idempotency_key)
        if path == "/checkout/sessions" and method == "POST":
            raise httpx.ReadTimeout("response lost after Stripe committed")
        return response

    monkeypatch.setattr(stripe_billing, "_request", lose_response)
    with pytest.raises(httpx.ReadTimeout):
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings?billing=success",
            cancel_url="https://app.example/settings?billing=cancelled",
            now=NOW,
        )

    recovery_args = {
        "operator_ref": "ticket-CHECKOUT-RECOVERY-1",
        "reason": "Resolve the retained Session before rotating the attempt",
        "evidence_reference": "evidence://stripe/checkout-recovery-1.json",
        "evidence_sha256": "a" * 64,
        "now": NOW + stripe_billing.CHECKOUT_REQUEST_REPLAY_WINDOW,
    }
    planned_outcome, planned_audit = stripe_billing.reconcile_frozen_checkout_request(
        practice,
        **recovery_args,
    )
    assert planned_outcome == "would_expire_open_session"
    assert planned_audit is None
    assert remote.sessions["cs_test_idempotent"]["status"] == "open"
    practice.refresh()
    assert practice.stripe_checkout_request_json is not None

    outcome, audit_id = stripe_billing.reconcile_frozen_checkout_request(
        practice,
        apply=True,
        **recovery_args,
    )

    assert outcome == "operator_expired_open_session"
    assert PracticeAccessAudit.get(id=audit_id)
    assert remote.sessions["cs_test_idempotent"]["status"] == "expired"
    practice.refresh()
    assert practice.stripe_checkout_request_json is None
    assert practice.stripe_checkout_request_created_at is None
    assert practice.stripe_checkout_session_id is None
    assert practice.stripe_checkout_attempt == 1
    assert practice.trial_ends_at == NOW + timedelta(days=14)


def test_operator_recovery_requires_evidenced_confirmation_when_session_absent(
    remote: FakeStripe, monkeypatch: pytest.MonkeyPatch
):
    practice = _practice()
    original_request = stripe_billing._request

    def lose_response(method, path, data=None, *, idempotency_key=None):
        response = original_request(method, path, data, idempotency_key=idempotency_key)
        if path == "/checkout/sessions" and method == "POST":
            raise httpx.ReadTimeout("response lost after Stripe committed")
        return response

    monkeypatch.setattr(stripe_billing, "_request", lose_response)
    with pytest.raises(httpx.ReadTimeout):
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings?billing=success",
            cancel_url="https://app.example/settings?billing=cancelled",
            now=NOW,
        )
    remote.sessions.clear()

    recovery_args = {
        "operator_ref": "ticket-CHECKOUT-RECOVERY-2",
        "reason": "Provider inventory contains no matching Session",
        "evidence_reference": "evidence://stripe/checkout-recovery-2.json",
        "evidence_sha256": "b" * 64,
        "now": (
            NOW
            + stripe_billing.CHECKOUT_REQUEST_REPLAY_WINDOW
            + stripe_billing.CHECKOUT_RECOVERY_TRANSPORT_SAFETY
        ),
    }
    outcome, audit_id = stripe_billing.reconcile_frozen_checkout_request(
        practice, **recovery_args
    )
    assert outcome == "no_matching_session"
    assert audit_id is None
    practice.refresh()
    assert practice.stripe_checkout_request_json is not None

    with pytest.raises(stripe_billing.StripeCheckoutReconciliationRequired):
        stripe_billing.reconcile_frozen_checkout_request(
            practice,
            apply=True,
            **recovery_args,
        )
    monkeypatch.setattr(stripe_config, "STRIPE_BILLING_ENABLED", False)

    outcome, audit_id = stripe_billing.reconcile_frozen_checkout_request(
        practice,
        confirm_absent=True,
        apply=True,
        **recovery_args,
    )
    assert outcome == "operator_confirmed_absent"
    assert PracticeAccessAudit.get(id=audit_id)
    practice.refresh()
    assert practice.stripe_checkout_request_json is None
    assert practice.stripe_checkout_attempt == 1
    assert practice.trial_ends_at is None


def test_operator_recovery_refuses_partially_matching_checkout_inventory(
    remote: FakeStripe, monkeypatch: pytest.MonkeyPatch
):
    practice = _practice()
    original_request = stripe_billing._request

    def lose_response(method, path, data=None, *, idempotency_key=None):
        response = original_request(method, path, data, idempotency_key=idempotency_key)
        if path == "/checkout/sessions" and method == "POST":
            raise httpx.ReadTimeout("response lost after Stripe committed")
        return response

    monkeypatch.setattr(stripe_billing, "_request", lose_response)
    with pytest.raises(httpx.ReadTimeout):
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings?billing=success",
            cancel_url="https://app.example/settings?billing=cancelled",
            now=NOW,
        )

    # Stripe Session metadata can be edited after creation. The immutable
    # client reference still associates this partial record with the practice,
    # so it must block any operator assertion that the attempt is absent.
    remote.sessions["cs_test_idempotent"]["metadata"].pop("checkout_attempt")
    monkeypatch.setattr(stripe_config, "STRIPE_BILLING_ENABLED", False)
    with pytest.raises(stripe_billing.StripeStateConflict, match="ambiguous"):
        stripe_billing.reconcile_frozen_checkout_request(
            practice,
            operator_ref="ticket-CHECKOUT-RECOVERY-AMBIGUOUS",
            reason="Refuse to rotate a partially matching provider record",
            evidence_reference="evidence://stripe/checkout-recovery-ambiguous.json",
            evidence_sha256="c" * 64,
            confirm_absent=True,
            apply=True,
            now=(
                NOW
                + stripe_billing.CHECKOUT_REQUEST_REPLAY_WINDOW
                + stripe_billing.CHECKOUT_RECOVERY_TRANSPORT_SAFETY
            ),
        )

    practice.refresh()
    assert practice.stripe_checkout_request_json is not None
    assert practice.stripe_checkout_attempt == 0


def test_operator_recovery_binds_checkout_that_completes_during_expiration(
    remote: FakeStripe, monkeypatch: pytest.MonkeyPatch
):
    practice = _practice()
    original_request = stripe_billing._request

    def lose_response(method, path, data=None, *, idempotency_key=None):
        response = original_request(method, path, data, idempotency_key=idempotency_key)
        if path == "/checkout/sessions" and method == "POST":
            raise httpx.ReadTimeout("response lost after Stripe committed")
        return response

    monkeypatch.setattr(stripe_billing, "_request", lose_response)
    with pytest.raises(httpx.ReadTimeout):
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings?billing=success",
            cancel_url="https://app.example/settings?billing=cancelled",
            now=NOW,
        )

    subscription = _paid_pilot_subscription(practice, status="active")
    remote.subscriptions[subscription["id"]] = subscription

    def complete_during_expiration(method, path, data=None, *, idempotency_key=None):
        if path == "/checkout/sessions/cs_test_idempotent/expire":
            remote.calls.append((method, path, data, idempotency_key))
            remote.sessions["cs_test_idempotent"].update(
                status="complete", subscription=subscription["id"], url=None
            )
            return deepcopy(remote.sessions["cs_test_idempotent"])
        return remote(method, path, data, idempotency_key=idempotency_key)

    monkeypatch.setattr(stripe_billing, "_request", complete_during_expiration)
    outcome, audit_id = stripe_billing.reconcile_frozen_checkout_request(
        practice,
        operator_ref="ticket-CHECKOUT-RECOVERY-COMPLETE",
        reason="Bind the subscription that completed during Session retirement",
        evidence_reference="evidence://stripe/checkout-recovery-complete.json",
        evidence_sha256="d" * 64,
        apply=True,
        now=NOW + stripe_billing.CHECKOUT_REQUEST_REPLAY_WINDOW,
    )

    assert outcome == "operator_bound_completed_subscription"
    assert PracticeAccessAudit.get(id=audit_id)
    practice.refresh()
    assert practice.stripe_subscription_id == subscription["id"]
    assert practice.subscription_status == "active"
    assert practice.stripe_checkout_session_id is None


def test_operator_recovery_binds_incomplete_subscription_without_replacement(
    remote: FakeStripe, monkeypatch: pytest.MonkeyPatch
):
    practice = _practice()
    original_request = stripe_billing._request

    def lose_response(method, path, data=None, *, idempotency_key=None):
        response = original_request(method, path, data, idempotency_key=idempotency_key)
        if path == "/checkout/sessions" and method == "POST":
            raise httpx.ReadTimeout("response lost after Stripe committed")
        return response

    monkeypatch.setattr(stripe_billing, "_request", lose_response)
    with pytest.raises(httpx.ReadTimeout):
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings?billing=success",
            cancel_url="https://app.example/settings?billing=cancelled",
            now=NOW,
        )

    subscription = _paid_pilot_subscription(practice, status="incomplete")
    remote.subscriptions[subscription["id"]] = subscription
    remote.sessions["cs_test_idempotent"]["subscription"] = subscription["id"]
    monkeypatch.setattr(stripe_billing, "_request", remote)

    outcome, audit_id = stripe_billing.reconcile_frozen_checkout_request(
        practice,
        operator_ref="ticket-CHECKOUT-RECOVERY-INCOMPLETE",
        reason="Retain the incomplete subscription as the billing authority",
        evidence_reference="evidence://stripe/checkout-recovery-incomplete.json",
        evidence_sha256="e" * 64,
        apply=True,
        now=NOW + stripe_billing.CHECKOUT_REQUEST_REPLAY_WINDOW,
    )

    assert outcome == "operator_bound_incomplete_subscription"
    assert PracticeAccessAudit.get(id=audit_id)
    practice.refresh()
    assert practice.stripe_subscription_id == subscription["id"]
    assert practice.subscription_status == "incomplete"
    assert practice.stripe_checkout_session_id is None
    assert practice.stripe_checkout_attempt == 1


def test_checkout_recovery_cli_defaults_to_dry_run(
    monkeypatch: pytest.MonkeyPatch,
):
    practice = _practice()
    observed_apply: list[bool] = []

    def fake_recovery(*_args, apply=False, **_kwargs):
        observed_apply.append(apply)
        return "would_expire_open_session", None

    monkeypatch.setattr(
        stripe_billing, "reconcile_frozen_checkout_request", fake_recovery
    )
    result = CliRunner().invoke(
        cli_app,
        [
            "reconcile-stripe-checkout",
            "--practice-id",
            str(practice.id),
            "--operator-ref",
            "ticket-CHECKOUT-RECOVERY-DRY-RUN",
            "--reason",
            "Preview the exact recovery action",
            "--evidence-reference",
            "evidence://stripe/checkout-recovery-dry-run.json",
            "--evidence-sha256",
            "f" * 64,
        ],
    )

    assert result.exit_code == 0
    assert "mode=dry-run" in result.output
    assert "outcome=would_expire_open_session" in result.output
    assert observed_apply == [False]


def test_lost_checkout_response_that_later_expires_rotates_safely(
    remote: FakeStripe, monkeypatch: pytest.MonkeyPatch
):
    practice = _practice()
    original_request = stripe_billing._request
    checkout_posts = 0

    def replay_committed_response(method, path, data=None, *, idempotency_key=None):
        nonlocal checkout_posts
        if path == "/checkout/sessions" and method == "POST":
            checkout_posts += 1
            if checkout_posts == 2:
                remote.calls.append((method, path, data, idempotency_key))
                return deepcopy(remote.sessions["cs_test_idempotent"])
        response = original_request(method, path, data, idempotency_key=idempotency_key)
        if path == "/checkout/sessions" and checkout_posts == 1:
            raise httpx.ReadTimeout("response lost after Stripe committed")
        return response

    monkeypatch.setattr(stripe_billing, "_request", replay_committed_response)
    with pytest.raises(httpx.ReadTimeout):
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings",
            cancel_url="https://app.example/settings",
            now=NOW,
        )

    remote.sessions["cs_test_idempotent"]["status"] = "expired"
    remote.sessions["cs_test_idempotent"]["url"] = None
    with pytest.raises(stripe_billing.StripeStateConflict, match="had expired"):
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings",
            cancel_url="https://app.example/settings",
            now=NOW,
        )

    practice.refresh()
    assert practice.stripe_checkout_request_json is None
    assert practice.stripe_checkout_attempt == 1
    assert practice.trial_ends_at == NOW + timedelta(days=14)
    assert (
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings",
            cancel_url="https://app.example/settings",
            now=NOW,
        )
        == "https://checkout.stripe.com/c/pay/test"
    )
    checkout_calls = [call for call in remote.calls if call[1] == "/checkout/sessions"]
    assert len(checkout_calls) == 3
    assert checkout_calls[0][3] == checkout_calls[1][3]
    assert checkout_calls[2][3] != checkout_calls[1][3]
    assert {
        call[2]["subscription_data[trial_end]"] for call in checkout_calls if call[2]
    } == {str(int((NOW + timedelta(days=14)).timestamp()))}


def test_frozen_checkout_request_blocks_exemption_cutover_during_stripe_io(
    remote: FakeStripe, monkeypatch: pytest.MonkeyPatch
):
    practice = _practice()
    original_request = stripe_billing._request

    def attempt_cutover(method, path, data=None, *, idempotency_key=None):
        if path == "/checkout/sessions":
            with pytest.raises(PracticeAccessChangeError, match="Stripe checkout"):
                set_billing_exemption(
                    practice,
                    operator_ref="stripe-billing-test",
                    reason="Prove Checkout and exemption cutover serialize",
                    expires_at=None,
                    now=NOW,
                )
        return original_request(method, path, data, idempotency_key=idempotency_key)

    monkeypatch.setattr(stripe_billing, "_request", attempt_cutover)

    assert (
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings",
            cancel_url="https://app.example/settings",
            now=NOW,
        )
        == "https://checkout.stripe.com/c/pay/test"
    )
    practice.refresh()
    assert not practice.billing_exempt
    assert practice.stripe_checkout_session_id == "cs_test_idempotent"


def test_checkout_request_rejects_customer_binding_race_before_stripe_io(
    remote: FakeStripe, monkeypatch: pytest.MonkeyPatch
):
    practice = _practice()
    original_snapshot = stripe_billing._checkout_request_snapshot

    def bind_terminal_subscription_before_snapshot(
        row: Practice,
        proposed_data: dict,
        *,
        current: datetime,
        verified_binding: tuple[str, str, int] | None = None,
    ):
        row.stripe_customer_id = "cus_bound_during_checkout"
        row.stripe_subscription_id = "sub_terminal_during_checkout"
        row.subscription_status = "canceled"
        row.stripe_state_version += 1
        row.save()
        return original_snapshot(
            row,
            proposed_data,
            current=current,
            verified_binding=verified_binding,
        )

    monkeypatch.setattr(
        stripe_billing,
        "_checkout_request_snapshot",
        bind_terminal_subscription_before_snapshot,
    )

    with pytest.raises(stripe_billing.StripeStateConflict, match="Stripe binding"):
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings?billing=success",
            cancel_url="https://app.example/settings?billing=cancelled",
            now=NOW,
        )

    assert not any(call[1] == "/checkout/sessions" for call in remote.calls)


def test_checkout_rejects_same_customer_rebind_after_remote_verification(
    remote: FakeStripe,
    monkeypatch: pytest.MonkeyPatch,
):
    practice = _practice()
    old_subscription = _subscription(
        practice,
        subscription_id="sub_terminal_old",
        customer_id="cus_stable",
        status="canceled",
    )
    remote.subscriptions[old_subscription["id"]] = old_subscription
    _bind_verified_public_subscription(
        practice,
        old_subscription,
        status="canceled",
    )
    original_request = stripe_billing._request

    def rebind_after_subscription_get(method, path, data=None, *, idempotency_key=None):
        result = original_request(method, path, data, idempotency_key=idempotency_key)
        if path == "/subscriptions/sub_terminal_old":
            practice.stripe_subscription_id = "sub_terminal_new"
            practice.stripe_state_version += 1
            practice.save()
        return result

    monkeypatch.setattr(stripe_billing, "_request", rebind_after_subscription_get)

    with pytest.raises(stripe_billing.StripeStateConflict, match="Stripe binding"):
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings?billing=success",
            cancel_url="https://app.example/settings?billing=cancelled",
            now=NOW,
        )

    assert not any(call[1] == "/checkout/sessions" for call in remote.calls)


def test_checkout_retry_rejects_customer_bound_after_lost_response(
    remote: FakeStripe, monkeypatch: pytest.MonkeyPatch
):
    practice = _practice()
    original_request = stripe_billing._request
    lost_once = False

    def lose_first_response(method, path, data=None, *, idempotency_key=None):
        nonlocal lost_once
        response = original_request(method, path, data, idempotency_key=idempotency_key)
        if path == "/checkout/sessions" and not lost_once:
            lost_once = True
            raise httpx.ReadTimeout("response lost after Stripe committed")
        return response

    monkeypatch.setattr(stripe_billing, "_request", lose_first_response)
    with pytest.raises(httpx.ReadTimeout):
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings?billing=success",
            cancel_url="https://app.example/settings?billing=cancelled",
            now=NOW,
        )

    practice.stripe_customer_id = "cus_bound_after_timeout"
    practice.stripe_subscription_id = "sub_terminal_after_timeout"
    practice.subscription_status = "canceled"
    practice.stripe_state_version += 1
    practice.save()
    remote.subscriptions["sub_terminal_after_timeout"] = _subscription(
        practice,
        subscription_id="sub_terminal_after_timeout",
        customer_id="cus_bound_after_timeout",
        status="canceled",
    )
    checkout_calls_before_retry = len(
        [call for call in remote.calls if call[1] == "/checkout/sessions"]
    )

    with pytest.raises(stripe_billing.StripeCheckoutReconciliationRequired):
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings?billing=success",
            cancel_url="https://app.example/settings?billing=cancelled",
            now=NOW + timedelta(minutes=1),
        )

    assert (
        len([call for call in remote.calls if call[1] == "/checkout/sessions"])
        == checkout_calls_before_retry
    )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("mode", "payment"),
        ("payment_method_collection", "if_required"),
        ("line_items[0][quantity]", "21"),
        (
            "subscription_data[trial_end]",
            str(int((NOW + timedelta(days=15)).timestamp())),
        ),
        ("metadata[practice_id]", "practice_wrong"),
        ("subscription_data[metadata][checkout_policy]", "unknown-v9"),
        (
            "subscription_data[trial_settings][end_behavior][missing_payment_method]",
            "cancel",
        ),
        ("unexpected_private_field", "private"),
    ],
)
def test_frozen_checkout_request_rejects_contract_tampering(
    remote: FakeStripe,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    replacement: str,
):
    practice = _practice()
    original_request = stripe_billing._request

    def lose_response(method, path, data=None, *, idempotency_key=None):
        response = original_request(method, path, data, idempotency_key=idempotency_key)
        if path == "/checkout/sessions":
            raise httpx.ReadTimeout("response lost after Stripe committed")
        return response

    monkeypatch.setattr(stripe_billing, "_request", lose_response)
    with pytest.raises(httpx.ReadTimeout):
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings?billing=success",
            cancel_url="https://app.example/settings?billing=cancelled",
            now=NOW,
        )

    practice.refresh()
    frozen = json.loads(practice.stripe_checkout_request_json or "{}")
    frozen[field] = replacement
    practice.stripe_checkout_request_json = json.dumps(frozen)
    practice.save()

    with pytest.raises(stripe_billing.StripeCheckoutReconciliationRequired):
        stripe_billing._checkout_request_snapshot(
            practice, {}, current=NOW + timedelta(minutes=1)
        )


def test_frozen_checkout_request_rejects_configured_price_rotation(
    remote: FakeStripe,
    monkeypatch: pytest.MonkeyPatch,
):
    practice = _practice()
    original_request = stripe_billing._request

    def lose_response(method, path, data=None, *, idempotency_key=None):
        response = original_request(method, path, data, idempotency_key=idempotency_key)
        if path == "/checkout/sessions" and method == "POST":
            raise httpx.ReadTimeout("response lost after Stripe committed")
        return response

    monkeypatch.setattr(stripe_billing, "_request", lose_response)
    with pytest.raises(httpx.ReadTimeout):
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings?billing=success",
            cancel_url="https://app.example/settings?billing=cancelled",
            now=NOW,
        )

    monkeypatch.setattr(stripe_config, "STRIPE_PRICE_ID", "price_rotated")
    with pytest.raises(stripe_billing.StripeCheckoutReconciliationRequired):
        stripe_billing._checkout_request_snapshot(
            practice, {}, current=NOW + timedelta(minutes=1)
        )


def test_signed_paid_pilot_subscription_mirrors_exact_trial_clock(
    remote: FakeStripe,
):
    practice = _practice()
    subscription = _paid_pilot_subscription(practice)
    remote.subscriptions[subscription["id"]] = subscription

    with pytest.raises(
        stripe_billing.StripeStateConflict,
        match="exact Checkout completion",
    ):
        stripe_billing.apply_stripe_event(
            _event(
                "customer.subscription.created",
                subscription,
                event_id="evt_paid_pilot_created_first",
                created=int(NOW.timestamp()),
            )
        )
    practice.refresh()
    assert practice.stripe_subscription_id is None

    assert stripe_billing.apply_stripe_event(
        _checkout_completed_event(
            practice,
            subscription,
            event_id="evt_paid_pilot_trial",
            created=int(NOW.timestamp()),
        )
    )

    practice.refresh()
    assert practice.subscription_status == "trialing"
    assert practice.trial_ends_at == NOW + timedelta(days=14)
    assert practice.entitled(at=NOW)


def test_new_paid_pilot_rejects_trial_extending_beyond_fixed_window(
    remote: FakeStripe,
):
    practice = _practice()
    trial_days = 15
    subscription = _paid_pilot_subscription(practice, trial_days=trial_days)
    remote.subscriptions[subscription["id"]] = subscription

    with pytest.raises(
        stripe_billing.StripeSubscriptionContractInvalid,
        match="extends beyond the fixed 14 days",
    ):
        stripe_billing.apply_stripe_event(
            _event(
                "customer.subscription.created",
                subscription,
                event_id=f"evt_trial_{trial_days}",
                created=int(NOW.timestamp()),
            )
        )
    practice.refresh()
    assert practice.stripe_subscription_id is None
    assert not practice.entitled(at=NOW)


@pytest.mark.parametrize(
    "trial_patch",
    [
        {"trial_end": None},
        {"trial_end": 10**30},
    ],
)
def test_new_paid_pilot_rejects_missing_or_unrepresentable_trial_clock(
    remote: FakeStripe, trial_patch: dict[str, int | None]
):
    practice = _practice()
    subscription = _paid_pilot_subscription(practice)
    subscription.update(trial_patch)
    remote.subscriptions[subscription["id"]] = subscription

    with pytest.raises(stripe_billing.StripeSubscriptionContractInvalid):
        stripe_billing.apply_stripe_event(
            _event(
                "customer.subscription.created",
                subscription,
                event_id=f"evt_bad_clock_{secrets.token_hex(4)}",
                created=int(NOW.timestamp()),
            )
        )


@pytest.mark.parametrize(
    "metadata_patch",
    [
        {
            "practice_id": "preserve",
            "checkout_policy": "unknown-v9",
            "entitlement_contract": "public-capability-entitlements.v1",
            "trial_clock": stripe_billing.LEGACY_TRIAL_CLOCK_POLICY,
        },
        {
            "practice_id": "preserve",
            "checkout_policy": stripe_billing.CHECKOUT_IDEMPOTENCY_POLICY_VERSION,
            "entitlement_contract": "public-capability-entitlements.v1",
            "trial_clock": stripe_billing.LEGACY_TRIAL_CLOCK_POLICY,
        },
        {"practice_id": "preserve"},
    ],
)
def test_initial_bind_requires_current_paid_pilot_policy(
    remote: FakeStripe, metadata_patch: dict[str, str]
):
    practice = _practice()
    subscription = _subscription(practice)
    subscription["metadata"] = {
        key: (str(practice.id) if value == "preserve" else value)
        for key, value in metadata_patch.items()
    }
    remote.subscriptions[subscription["id"]] = subscription

    with pytest.raises(stripe_billing.StripeStateInvalid):
        stripe_billing.apply_stripe_event(
            _event(
                "customer.subscription.created",
                subscription,
                event_id=f"evt_policy_{secrets.token_hex(4)}",
                created=int(NOW.timestamp()),
            )
        )
    practice.refresh()
    assert practice.stripe_subscription_id is None
    assert not practice.entitled(at=NOW)


def test_initial_legacy_trial_rejects_checkout_clock_policy(remote: FakeStripe):
    practice = _practice()
    practice.trial_ends_at = NOW + timedelta(days=10)
    practice.save()
    subscription = _paid_pilot_subscription(practice)
    remote.subscriptions[subscription["id"]] = subscription

    with pytest.raises(stripe_billing.StripeStateInvalid, match="does not match"):
        stripe_billing.apply_stripe_event(
            _event(
                "customer.subscription.created",
                subscription,
                event_id="evt_legacy_row_checkout_clock",
                created=int(NOW.timestamp()),
            )
        )


def test_bound_paid_pilot_trial_clock_cannot_shift(remote: FakeStripe):
    practice = _practice()
    subscription = _paid_pilot_subscription(practice)
    remote.subscriptions[subscription["id"]] = subscription
    assert stripe_billing.apply_stripe_event(
        _checkout_completed_event(
            practice,
            subscription,
            event_id="evt_original_trial_clock",
            created=int(NOW.timestamp()),
        )
    )
    original_end = NOW + timedelta(days=14)

    shifted = deepcopy(subscription)
    shifted["trial_start"] += 24 * 60 * 60
    shifted["trial_end"] += 24 * 60 * 60
    remote.subscriptions[subscription["id"]] = shifted
    assert stripe_billing.apply_stripe_event(
        _event(
            "customer.subscription.updated",
            shifted,
            event_id="evt_shifted_trial_clock",
            created=int((NOW + timedelta(seconds=1)).timestamp()),
        )
    )

    practice.refresh()
    assert practice.subscription_status == "invalid"
    assert practice.trial_ends_at == original_end
    assert not practice.entitled(at=NOW)


def test_bound_paid_pilot_trial_policy_cannot_be_downgraded(remote: FakeStripe):
    practice = _practice()
    subscription = _paid_pilot_subscription(practice)
    remote.subscriptions[subscription["id"]] = subscription
    assert stripe_billing.apply_stripe_event(
        _checkout_completed_event(
            practice,
            subscription,
            event_id="evt_original_trial_policy",
            created=int(NOW.timestamp()),
        )
    )
    practice.refresh()
    original_end = practice.trial_ends_at
    assert practice.stripe_trial_clock_policy == (
        stripe_billing.CHECKOUT_TRIAL_CLOCK_POLICY
    )

    downgraded = deepcopy(subscription)
    downgraded["metadata"] = {"practice_id": str(practice.id)}
    downgraded["trial_start"] += 24 * 60 * 60
    downgraded["trial_end"] += 24 * 60 * 60
    remote.subscriptions[subscription["id"]] = downgraded
    assert stripe_billing.apply_stripe_event(
        _event(
            "customer.subscription.updated",
            downgraded,
            event_id="evt_downgraded_trial_policy",
            created=int((NOW + timedelta(seconds=1)).timestamp()),
        )
    )

    practice.refresh()
    assert practice.subscription_status == "invalid"
    assert practice.stripe_trial_clock_policy == (
        stripe_billing.CHECKOUT_TRIAL_CLOCK_POLICY
    )
    assert practice.trial_ends_at == original_end
    assert not practice.entitled(at=NOW)


@pytest.mark.parametrize(
    "trial_length", [timedelta(days=10), timedelta(hours=48, minutes=5)]
)
def test_bound_legacy_subscription_preserves_valid_historical_trial(
    remote: FakeStripe, trial_length: timedelta
):
    practice = _practice()
    subscription = _subscription(practice, status="trialing")
    subscription["metadata"] = {"practice_id": str(practice.id)}
    subscription["trial_start"] = int(NOW.timestamp())
    subscription["trial_end"] = int((NOW + trial_length).timestamp())
    remote.subscriptions[subscription["id"]] = subscription
    practice.stripe_customer_id = subscription["customer"]
    practice.stripe_subscription_id = subscription["id"]
    practice.subscription_status = "pending_verification"
    practice.stripe_state_version = 1
    practice.save()

    stripe_billing.refresh_subscription_state(practice)

    practice.refresh()
    assert practice.subscription_status == "trialing"
    assert practice.trial_ends_at == NOW + trial_length


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
        def now(cls, tz: tzinfo | None = None) -> datetime:
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


def test_checkout_accepts_twenty_active_seats_and_rejects_twenty_one(
    remote: FakeStripe,
):
    practice = _practice()
    for index in range(19):
        doctor = Doctor(
            clerk_id=f"user_seat_{index}_{secrets.token_hex(5)}",
            email=f"seat-{index}-{secrets.token_hex(3)}@example.com",
            biography="",
        ).save()
        PracticeMember(
            practice_id=practice.id,
            doctor_id=doctor.id,
            role=PracticeRole.workforce.value,
        ).save()

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
    assert checkout_call[2]["line_items[0][quantity]"] == "20"

    practice.stripe_checkout_session_id = None
    practice.stripe_checkout_session_url = None
    practice.stripe_checkout_session_expires_at = None
    practice.save()
    extra = Doctor(
        clerk_id=f"user_seat_overflow_{secrets.token_hex(5)}",
        email=f"seat-overflow-{secrets.token_hex(3)}@example.com",
        biography="",
    ).save()
    PracticeMember(
        practice_id=practice.id,
        doctor_id=extra.id,
        role=PracticeRole.workforce.value,
    ).save()

    prior_checkout_calls = len(
        [call for call in remote.calls if call[1] == "/checkout/sessions"]
    )
    with pytest.raises(stripe_billing.StripeSeatLimitExceeded):
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings",
            cancel_url="https://app.example/settings",
            now=NOW,
        )
    assert (
        len([call for call in remote.calls if call[1] == "/checkout/sessions"])
        == prior_checkout_calls
    )


def test_checkout_fails_closed_when_canonical_seat_ledger_is_invalid(
    remote: FakeStripe, monkeypatch: pytest.MonkeyPatch
):
    practice = _practice()
    monkeypatch.setattr(
        stripe_billing,
        "active_clinician_seat_count",
        _invalid_seat_ledger,
    )

    with pytest.raises(
        stripe_billing.StripeSeatLedgerInvalid,
        match="active-clinician ledger is invalid",
    ):
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings",
            cancel_url="https://app.example/settings",
            now=NOW,
        )

    assert not any(call[1] == "/checkout/sessions" for call in remote.calls)


def test_paused_trial_without_card_revokes_then_resumed_restores_entitlement(
    remote: FakeStripe,
):
    practice = _practice()
    practice.trial_ends_at = NOW - timedelta(seconds=1)
    paused = _subscription(practice, status="paused")
    practice.stripe_customer_id = paused["customer"]
    practice.stripe_subscription_id = paused["id"]
    practice.subscription_status = "pending_verification"
    practice.stripe_trial_clock_policy = stripe_billing.LEGACY_TRIAL_CLOCK_POLICY
    practice.save()
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
    subscription = _paid_pilot_subscription(practice, status="active")
    subscription["pause_collection"] = {"behavior": "keep_as_draft"}
    remote.subscriptions[subscription["id"]] = subscription

    assert stripe_billing.apply_stripe_event(
        _checkout_completed_event(
            practice,
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
    subscription = _paid_pilot_subscription(practice)
    subscription["id"] = "sub_once"
    subscription["items"]["data"][0]["id"] = "si_once"
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
    event["data"]["object"]["metadata"]["checkout_policy"] = (
        "PHI CHECKOUT POLICY SENTINEL"
    )
    event["data"]["object"]["metadata"]["entitlement_contract"] = (
        "PHI ENTITLEMENT SENTINEL"
    )
    event["data"]["object"]["metadata"]["trial_clock"] = "PHI TRIAL CLOCK SENTINEL"
    event["data"]["object"]["metadata"]["trial_days"] = "PHI TRIAL DAYS SENTINEL"

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
    assert "PHI CHECKOUT POLICY SENTINEL" not in inbox.payload_json
    assert "PHI ENTITLEMENT SENTINEL" not in inbox.payload_json
    assert "PHI TRIAL CLOCK SENTINEL" not in inbox.payload_json
    assert "PHI TRIAL DAYS SENTINEL" not in inbox.payload_json
    stored_metadata = json.loads(inbox.payload_json)["data"]["object"]["metadata"]
    assert stored_metadata == {
        "checkout_policy": "invalid",
        "entitlement_contract": "invalid",
        "practice_id": None,
        "trial_clock": "invalid",
        "trial_days": "invalid",
    }

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


@pytest.mark.parametrize(
    ("failure_mode", "expected_error_code"),
    [
        ("account", "stripe_state_invalid"),
        ("price", "stripe_state_invalid"),
        ("not_found", "stripe_http_error"),
        ("wrong_subscription", "stripe_state_invalid"),
        ("livemode", "stripe_state_invalid"),
    ],
)
def test_failed_authoritative_webhook_verification_demotes_public_access(
    remote: FakeStripe,
    monkeypatch: pytest.MonkeyPatch,
    failure_mode: str,
    expected_error_code: str,
):
    practice = _practice()
    subscription = _subscription(
        practice,
        subscription_id="sub_sensitive_failure_identifier",
        customer_id="cus_sensitive_failure_identifier",
        status="active",
    )
    remote.subscriptions[subscription["id"]] = deepcopy(subscription)
    _bind_verified_public_subscription(practice, subscription)
    initial_version = practice.stripe_state_version
    event = _event(
        "customer.subscription.updated",
        subscription,
        event_id=f"evt_failed_verification_{failure_mode}",
        created=int(NOW.timestamp()),
    )
    inbox_id, duplicate = stripe_billing.enqueue_stripe_event(event)
    assert inbox_id and not duplicate

    if failure_mode == "account":
        remote.account_id = "acct_wrong_sensitive"
    elif failure_mode == "price":
        remote.price["unit_amount"] = 8_800
    elif failure_mode == "wrong_subscription":
        remote.subscriptions[subscription["id"]]["id"] = "sub_wrong_sensitive"
    elif failure_mode == "livemode":
        remote.subscriptions[subscription["id"]]["livemode"] = True
    elif failure_mode == "not_found":
        original_request = stripe_billing._request

        def return_sensitive_not_found(
            method, path, data=None, *, idempotency_key=None
        ):
            if path == f"/subscriptions/{subscription['id']}":
                request = httpx.Request(
                    "GET",
                    f"https://api.stripe.test/v1{path}",
                )
                response = httpx.Response(404, request=request)
                raise httpx.HTTPStatusError(
                    "PHI SENTINEL provider response for sub_sensitive_failure_identifier",
                    request=request,
                    response=response,
                )
            return original_request(method, path, data, idempotency_key=idempotency_key)

        monkeypatch.setattr(stripe_billing, "_request", return_sensitive_not_found)

    with pytest.raises(stripe_billing.StripeEventProcessingDeferred) as raised:
        stripe_billing.process_stripe_event_inbox(inbox_id)

    assert str(raised.value) == "Stripe event processing is deferred for reconciliation"
    assert "sensitive" not in str(raised.value).lower()
    practice.refresh()
    assert practice.subscription_status == "pending_verification"
    assert practice.stripe_state_version == initial_version + 1
    assert not practice.entitled(at=NOW)
    inbox = StripeEventInbox.get(id=inbox_id)
    assert inbox
    assert inbox.state == "pending"
    assert inbox.attempts == 1
    assert inbox.next_attempt_at is not None
    assert inbox.last_error == expected_error_code
    assert "sensitive" not in (inbox.last_error or "").lower()
    assert StripeEventReceipt.get(event_id=event["id"]) is None

    with pytest.raises(stripe_billing.StripeEventProcessingDeferred):
        stripe_billing.process_stripe_event_inbox(inbox_id)
    practice.refresh()
    assert practice.stripe_state_version == initial_version + 1
    inbox.refresh()
    assert inbox.attempts == 2


def test_temporary_billing_exemption_does_not_preserve_stale_active_projection(
    remote: FakeStripe,
):
    practice = _practice()
    subscription = _subscription(practice, status="active")
    remote.subscriptions[subscription["id"]] = subscription
    _bind_verified_public_subscription(practice, subscription)
    exemption_end = datetime.now(timezone.utc) + timedelta(hours=1)
    practice.billing_exempt = True
    practice.billing_exempt_until = exemption_end
    practice.save()
    event = _event(
        "customer.subscription.updated",
        subscription,
        event_id="evt_failure_during_temporary_exemption",
        created=int(NOW.timestamp()),
    )
    inbox_id, _ = stripe_billing.enqueue_stripe_event(event)
    remote.account_id = "acct_wrong"

    with pytest.raises(stripe_billing.StripeEventProcessingDeferred):
        stripe_billing.process_stripe_event_inbox(inbox_id)

    practice.refresh()
    assert practice.subscription_status == "pending_verification"
    assert practice.entitled(at=exemption_end - timedelta(seconds=1))
    assert not practice.entitled(at=exemption_end + timedelta(seconds=1))


@pytest.mark.parametrize(
    ("trial_is_future", "expected_status"),
    [
        (True, "pending_verification"),
        (False, "trialing"),
    ],
)
def test_failed_webhook_only_demotes_an_in_force_public_trial(
    remote: FakeStripe,
    trial_is_future: bool,
    expected_status: str,
):
    practice = _practice()
    subscription = _paid_pilot_subscription(practice)
    current = datetime.now(timezone.utc)
    trial_end = (
        current + timedelta(days=1)
        if trial_is_future
        else current - timedelta(seconds=1)
    )
    _bind_verified_public_subscription(
        practice,
        subscription,
        status="trialing",
        trial_ends_at=trial_end,
    )
    event = _event(
        "customer.subscription.updated",
        subscription,
        event_id=f"evt_trial_failure_{trial_is_future}",
        created=int(NOW.timestamp()),
    )
    inbox_id, _ = stripe_billing.enqueue_stripe_event(event)
    remote.account_id = "acct_wrong"

    with pytest.raises(stripe_billing.StripeEventProcessingDeferred):
        stripe_billing.process_stripe_event_inbox(inbox_id)

    practice.refresh()
    assert practice.subscription_status == expected_status


def test_successful_webhook_retry_restores_pending_public_subscription(
    remote: FakeStripe,
):
    practice = _practice()
    subscription = _subscription(practice, status="active")
    remote.subscriptions[subscription["id"]] = subscription
    _bind_verified_public_subscription(practice, subscription)
    event = _event(
        "customer.subscription.updated",
        subscription,
        event_id="evt_retry_restores_public_access",
        created=int(NOW.timestamp()),
    )
    inbox_id, _ = stripe_billing.enqueue_stripe_event(event)
    remote.account_id = "acct_wrong"
    with pytest.raises(stripe_billing.StripeEventProcessingDeferred):
        stripe_billing.process_stripe_event_inbox(inbox_id)
    practice.refresh()
    assert practice.subscription_status == "pending_verification"

    remote.account_id = "acct_test_cosmaneura"
    assert stripe_billing.process_stripe_event_inbox(inbox_id)
    practice.refresh()
    assert practice.subscription_status == "active"
    inbox = StripeEventInbox.get(id=inbox_id)
    assert inbox and inbox.state == "processed"
    assert StripeEventReceipt.get(event_id=event["id"])


def test_failed_stale_or_superseded_event_does_not_demote_public_access(
    remote: FakeStripe,
):
    stale_practice = _practice()
    stale_subscription = _subscription(
        stale_practice,
        subscription_id="sub_stale_failure",
        customer_id="cus_stale_failure",
    )
    _bind_verified_public_subscription(stale_practice, stale_subscription)
    stale_practice.stripe_last_event_created_at = NOW + timedelta(seconds=1)
    stale_practice.stripe_last_event_id = "evt_newer_success"
    stale_practice.save()
    stale_inbox_id, _ = stripe_billing.enqueue_stripe_event(
        _event(
            "customer.subscription.updated",
            stale_subscription,
            event_id="evt_stale_failure",
            created=int(NOW.timestamp()),
        )
    )

    rebound_practice = _practice()
    old_subscription = _subscription(
        rebound_practice,
        subscription_id="sub_old_failure",
        customer_id="cus_old_failure",
    )
    _bind_verified_public_subscription(rebound_practice, old_subscription)
    rebound_inbox_id, _ = stripe_billing.enqueue_stripe_event(
        _event(
            "customer.subscription.updated",
            old_subscription,
            event_id="evt_old_binding_failure",
            created=int(NOW.timestamp()),
        )
    )
    rebound_practice.stripe_subscription_id = "sub_new_binding"
    rebound_practice.stripe_customer_id = "cus_new_binding"
    rebound_practice.stripe_state_version += 1
    rebound_practice.save()
    remote.account_id = "acct_wrong"

    for inbox_id in (stale_inbox_id, rebound_inbox_id):
        with pytest.raises(stripe_billing.StripeEventProcessingDeferred):
            stripe_billing.process_stripe_event_inbox(inbox_id)

    stale_practice.refresh()
    rebound_practice.refresh()
    assert stale_practice.subscription_status == "active"
    assert rebound_practice.subscription_status == "active"


def test_failed_event_with_committed_receipt_does_not_demote_public_access(
    remote: FakeStripe,
    monkeypatch: pytest.MonkeyPatch,
):
    practice = _practice()
    subscription = _subscription(practice, status="active")
    _bind_verified_public_subscription(practice, subscription)
    event = _event(
        "customer.subscription.updated",
        subscription,
        event_id="evt_receipt_won_race",
        created=int(NOW.timestamp()),
    )
    inbox_id, _ = stripe_billing.enqueue_stripe_event(event)

    def receipt_then_fail(_event_payload):
        with stripe_billing._isolated_state_session() as session:
            session.add(
                StripeEventReceipt(
                    event_id=event["id"],
                    event_type=event["type"],
                    event_created_at=NOW,
                    practice_id=practice.id,
                )
            )
            session.commit()
        raise RuntimeError("synthetic post-commit worker failure")

    monkeypatch.setattr(stripe_billing, "apply_stripe_event", receipt_then_fail)
    with pytest.raises(stripe_billing.StripeEventProcessingDeferred):
        stripe_billing.process_stripe_event_inbox(inbox_id)

    practice.refresh()
    assert practice.subscription_status == "active"


def test_deleted_subscription_fallback_preserves_paid_pilot_policy(
    remote: FakeStripe, monkeypatch: pytest.MonkeyPatch
):
    practice = _practice()
    subscription = _paid_pilot_subscription(practice)
    remote.subscriptions[subscription["id"]] = subscription
    assert stripe_billing.apply_stripe_event(
        _checkout_completed_event(
            practice,
            subscription,
            event_id="evt_bind_before_hard_delete",
            created=int(NOW.timestamp()),
        )
    )

    canceled = deepcopy(subscription)
    canceled["status"] = "canceled"
    inbox_id, duplicate = stripe_billing.enqueue_stripe_event(
        _event(
            "customer.subscription.deleted",
            canceled,
            event_id="evt_hard_deleted_fallback",
            created=int((NOW + timedelta(seconds=1)).timestamp()),
        )
    )
    assert inbox_id and not duplicate
    original_request = stripe_billing._request

    subscription_gets = 0

    def return_not_found(method, path, data=None, *, idempotency_key=None):
        nonlocal subscription_gets
        if path == f"/subscriptions/{subscription['id']}":
            subscription_gets += 1
            request = httpx.Request("GET", f"https://api.stripe.com/v1{path}")
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError(
                "subscription no longer retrievable",
                request=request,
                response=response,
            )
        return original_request(method, path, data, idempotency_key=idempotency_key)

    monkeypatch.setattr(stripe_billing, "_request", return_not_found)

    assert stripe_billing.process_stripe_event_inbox(inbox_id)
    practice.refresh()
    assert practice.subscription_status == "canceled"
    assert practice.stripe_trial_clock_policy == (
        stripe_billing.CHECKOUT_TRIAL_CLOCK_POLICY
    )

    stale = deepcopy(subscription)
    stale["status"] = "active"
    assert not stripe_billing.apply_stripe_event(
        _event(
            "customer.subscription.updated",
            stale,
            event_id="evt_stale_after_hard_delete",
            created=int((NOW - timedelta(seconds=1)).timestamp()),
        )
    )
    assert subscription_gets == 1
    assert StripeEventReceipt.get(event_id="evt_stale_after_hard_delete")


def test_wrong_checkout_completion_cannot_clear_current_cached_session(
    remote: FakeStripe,
):
    practice = _practice()
    subscription = _paid_pilot_subscription(practice, status="active")
    remote.subscriptions[subscription["id"]] = subscription
    practice.stripe_customer_id = subscription["customer"]
    practice.stripe_subscription_id = subscription["id"]
    practice.subscription_status = "active"
    practice.stripe_trial_clock_policy = stripe_billing.CHECKOUT_TRIAL_CLOCK_POLICY
    practice.trial_ends_at = NOW + timedelta(days=14)
    practice.stripe_checkout_session_id = "cs_current_pending"
    practice.stripe_checkout_session_url = "https://checkout.stripe.com/c/pay/current"
    practice.stripe_checkout_session_expires_at = NOW + timedelta(hours=1)
    practice.save()
    event = _checkout_completed_event(
        practice,
        subscription,
        event_id="evt_old_checkout_completion",
        created=int(NOW.timestamp()),
    )
    event["data"]["object"]["id"] = "cs_old_completed"

    with pytest.raises(
        stripe_billing.StripeStateConflict,
        match="does not match the pending session",
    ):
        stripe_billing.apply_stripe_event(event)

    practice.refresh()
    assert practice.stripe_checkout_session_id == "cs_old_checkout_completion"


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


@pytest.mark.parametrize(
    ("event_type", "superseded_status"),
    [
        ("customer.subscription.updated", "active"),
        ("customer.subscription.deleted", "canceled"),
    ],
)
def test_newer_event_for_superseded_subscription_is_durably_ignored(
    remote: FakeStripe,
    event_type: str,
    superseded_status: str,
):
    practice = _practice()
    current = _subscription(practice, status="active")
    current["id"] = "sub_current_binding"
    current["items"]["data"][0]["id"] = "si_current_binding"
    practice.stripe_customer_id = current["customer"]
    practice.stripe_subscription_id = current["id"]
    practice.subscription_status = "active"
    practice.stripe_trial_clock_policy = stripe_billing.LEGACY_TRIAL_CLOCK_POLICY
    practice.save()

    superseded = _subscription(practice, status=superseded_status)
    superseded["id"] = "sub_superseded_binding"
    superseded["items"]["data"][0]["id"] = "si_superseded_binding"
    remote.subscriptions[superseded["id"]] = superseded
    event_id = f"evt_superseded_{superseded_status}"
    inbox_id, duplicate = stripe_billing.enqueue_stripe_event(
        _event(
            event_type,
            superseded,
            event_id=event_id,
            created=int((NOW + timedelta(hours=1)).timestamp()),
        )
    )

    assert inbox_id and not duplicate
    assert stripe_billing.process_stripe_event_inbox(inbox_id)
    assert not any(
        call[1] == f"/subscriptions/{superseded['id']}" for call in remote.calls
    )
    assert StripeEventReceipt.get(event_id=event_id)
    practice.refresh()
    assert practice.stripe_subscription_id == current["id"]
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


def test_malformed_signed_items_fail_closed_before_worker_io(remote: FakeStripe):
    practice = _practice()
    subscription = _subscription(practice, status="active")
    practice.stripe_customer_id = subscription["customer"]
    practice.stripe_subscription_id = subscription["id"]
    practice.subscription_status = "active"
    practice.trial_ends_at = NOW + timedelta(days=1)
    practice.save()
    event = _event(
        "customer.subscription.updated",
        subscription,
        event_id="evt_malformed_items_pending",
        created=int(NOW.timestamp()),
    )
    event["data"]["object"]["items"] = "malformed"

    inbox_id, duplicate = stripe_billing.enqueue_stripe_event(event)

    assert inbox_id and not duplicate
    practice.refresh()
    assert practice.subscription_status == "pending_verification"
    assert not practice.entitled(at=NOW)
    inbox = StripeEventInbox.get(id=inbox_id)
    assert inbox
    assert json.loads(inbox.payload_json)["data"]["object"]["items"] == {"data": []}


def test_malformed_signed_metadata_fails_closed_before_worker_io(remote: FakeStripe):
    practice = _practice()
    subscription = _subscription(practice, status="active")
    practice.stripe_customer_id = subscription["customer"]
    practice.stripe_subscription_id = subscription["id"]
    practice.subscription_status = "active"
    practice.trial_ends_at = NOW + timedelta(days=1)
    practice.save()
    event = _event(
        "customer.subscription.updated",
        subscription,
        event_id="evt_malformed_metadata_pending",
        created=int(NOW.timestamp()),
    )
    event["data"]["object"]["metadata"] = "malformed"

    inbox_id, duplicate = stripe_billing.enqueue_stripe_event(event)

    assert inbox_id and not duplicate
    practice.refresh()
    assert practice.subscription_status == "pending_verification"
    assert not practice.entitled(at=NOW)
    inbox = StripeEventInbox.get(id=inbox_id)
    assert inbox
    assert json.loads(inbox.payload_json)["data"]["object"]["metadata"] == {}


def test_malformed_event_data_fails_closed_before_remote_io(remote: FakeStripe):
    event = {
        "id": "evt_malformed_data",
        "type": "customer.subscription.updated",
        "created": int(NOW.timestamp()),
        "api_version": "2025-09-30.clover",
        "livemode": False,
        "data": "malformed",
    }

    with pytest.raises(stripe_billing.StripeStateInvalid, match="event data"):
        stripe_billing.enqueue_stripe_event(event)
    assert remote.calls == []


@pytest.mark.parametrize("created", [True, 10**100, -(10**100)])
def test_malformed_event_timestamp_fails_closed_before_remote_io(
    remote: FakeStripe, created: object
):
    practice = _practice()
    subscription = _subscription(practice)
    event = _event(
        "customer.subscription.updated",
        subscription,
        event_id="evt_malformed_created",
        created=int(NOW.timestamp()),
    )
    event["created"] = created

    with pytest.raises(stripe_billing.StripeStateInvalid, match="creation time"):
        stripe_billing.enqueue_stripe_event(event)
    assert remote.calls == []


@pytest.mark.parametrize("expires_at", [True, 10**100, -(10**100)])
def test_malformed_checkout_expiry_is_controlled_invalid_state(expires_at: object):
    with pytest.raises(stripe_billing.StripeStateInvalid, match="session expiry"):
        stripe_billing._checkout_session_expiry({"expires_at": expires_at})


def test_past_due_is_nonterminal_and_not_entitled(remote: FakeStripe):
    practice = _practice()
    practice.trial_ends_at = NOW - timedelta(days=1)
    past_due = _subscription(practice, status="past_due")
    practice.stripe_customer_id = past_due["customer"]
    practice.stripe_subscription_id = past_due["id"]
    practice.subscription_status = "pending_verification"
    practice.stripe_trial_clock_policy = stripe_billing.LEGACY_TRIAL_CLOCK_POLICY
    practice.save()
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


def test_unpaid_remote_lifecycle_mirrors_non_entitled(remote: FakeStripe):
    practice = _practice()
    unpaid = _subscription(practice, status="unpaid")
    _bind_verified_public_subscription(
        practice,
        unpaid,
        status="active",
        trial_ends_at=NOW - timedelta(days=1),
    )
    practice.stripe_trial_clock_policy = stripe_billing.LEGACY_TRIAL_CLOCK_POLICY
    practice.save()
    remote.subscriptions[unpaid["id"]] = unpaid

    assert stripe_billing.apply_stripe_event(
        _event(
            "customer.subscription.updated",
            unpaid,
            event_id="evt_unpaid",
            created=int(NOW.timestamp()),
        )
    )
    practice.refresh()
    assert practice.subscription_status == "unpaid"
    assert stripe_billing.has_nonterminal_subscription(practice)
    assert not practice.entitled(at=NOW)


@pytest.mark.parametrize(
    "subscription_status, expected",
    [
        ("trialing", True),
        ("active", True),
        ("past_due", False),
        ("paused", False),
        ("canceled", False),
        ("invalid", False),
        ("pending_verification", False),
    ],
)
def test_bound_subscription_status_outranks_future_local_trial(
    subscription_status: str, expected: bool
):
    practice = _practice()
    practice.stripe_subscription_id = "sub_bound_precedence"
    practice.subscription_status = subscription_status
    practice.trial_ends_at = NOW + timedelta(days=14)

    assert practice.entitled(at=NOW) is expected


@pytest.mark.parametrize(
    "subscription_status", ["invalid", "pending_verification", "future_state"]
)
def test_unbound_explicit_nonentitling_status_outranks_future_legacy_trial(
    subscription_status: str,
):
    practice = _practice()
    practice.subscription_status = subscription_status
    practice.trial_ends_at = NOW + timedelta(days=14)

    assert not practice.entitled(at=NOW)


@pytest.mark.parametrize("subscription_status", ["trialing", "active"])
def test_unbound_entitled_status_fails_closed(subscription_status: str):
    practice = _practice()
    practice.subscription_status = subscription_status
    practice.trial_ends_at = NOW + timedelta(days=14)

    assert not practice.entitled(at=NOW)


@pytest.mark.parametrize("subscription_id", ["", "not_a_stripe_subscription"])
@pytest.mark.parametrize("subscription_status", ["trialing", "active"])
def test_malformed_subscription_id_fails_closed(
    subscription_id: str,
    subscription_status: str,
):
    practice = _practice()
    practice.stripe_subscription_id = subscription_id
    practice.subscription_status = subscription_status
    practice.trial_ends_at = NOW + timedelta(days=14)

    assert not practice.entitled(at=NOW)


@pytest.mark.parametrize(
    "trial_end",
    [None, NOW, NOW - timedelta(seconds=1)],
)
def test_bound_trialing_subscription_requires_live_clock(
    trial_end: datetime | None,
):
    practice = _practice()
    practice.stripe_subscription_id = "sub_trial_clock_required"
    practice.subscription_status = "trialing"
    practice.trial_ends_at = trial_end

    assert not practice.entitled(at=NOW)


def test_bound_remote_quantity_above_pilot_limit_revokes_entitlement(
    remote: FakeStripe,
):
    practice = _practice()
    subscription = _subscription(practice, quantity=21)
    remote.subscriptions[subscription["id"]] = subscription
    practice.stripe_customer_id = subscription["customer"]
    practice.stripe_subscription_id = subscription["id"]
    practice.subscription_status = "active"
    practice.trial_ends_at = NOW + timedelta(days=14)
    practice.stripe_state_version = 1
    practice.save()

    stripe_billing.refresh_subscription_state(practice)

    practice.refresh()
    assert practice.subscription_status == "invalid"
    assert not practice.entitled(at=NOW)
    assert practice.stripe_quantity_sync_required
    assert practice.stripe_quantity_sync_target == 1

    assert stripe_billing.sync_subscription_quantity(practice, raise_on_error=True)
    practice.refresh()
    assert practice.subscription_status == "active"
    assert not practice.stripe_quantity_sync_required
    assert subscription["items"]["data"][0]["quantity"] == 1
    assert remote.subscriptions[subscription["id"]]["items"]["data"][0]["quantity"] == 1


def test_refresh_revokes_stale_entitlement_when_canonical_seat_ledger_is_invalid(
    remote: FakeStripe, monkeypatch: pytest.MonkeyPatch
):
    practice = _practice()
    subscription = _subscription(practice)
    remote.subscriptions[subscription["id"]] = subscription
    practice.stripe_customer_id = subscription["customer"]
    practice.stripe_subscription_id = subscription["id"]
    practice.subscription_status = "active"
    practice.stripe_state_version = 1
    practice.save()
    monkeypatch.setattr(
        stripe_billing,
        "active_clinician_seat_count",
        _invalid_seat_ledger,
    )

    stripe_billing.refresh_subscription_state(practice)

    practice.refresh()
    assert practice.subscription_status == "invalid"
    assert practice.stripe_quantity_sync_required is True
    assert practice.stripe_quantity_sync_target is None
    assert practice.stripe_quantity_sync_error == "active_seat_ledger_invalid"
    assert not practice.entitled(at=NOW)


def test_webhook_revokes_stale_entitlement_when_canonical_seat_ledger_is_invalid(
    remote: FakeStripe, monkeypatch: pytest.MonkeyPatch
):
    practice = _practice()
    subscription = _subscription(practice)
    remote.subscriptions[subscription["id"]] = subscription
    practice.stripe_customer_id = subscription["customer"]
    practice.stripe_subscription_id = subscription["id"]
    practice.subscription_status = "active"
    practice.stripe_state_version = 1
    practice.save()
    monkeypatch.setattr(
        stripe_billing,
        "active_clinician_seat_count",
        _invalid_seat_ledger,
    )

    assert stripe_billing.apply_stripe_event(
        _event(
            "customer.subscription.updated",
            subscription,
            event_id="evt_invalid_seat_ledger",
            created=int(NOW.timestamp()),
        )
    )

    practice.refresh()
    assert practice.subscription_status == "invalid"
    assert practice.stripe_quantity_sync_required is True
    assert practice.stripe_quantity_sync_target is None
    assert practice.stripe_quantity_sync_error == "active_seat_ledger_invalid"
    assert StripeEventReceipt.get(event_id="evt_invalid_seat_ledger") is not None
    assert not practice.entitled(at=NOW)


def test_zero_local_seats_fail_closed_but_do_not_block_cancellation(
    remote: FakeStripe,
):
    practice = _practice()
    member = PracticeMember.get(practice_id=practice.id)
    assert member
    member.seat_state = "inactive"
    member.save()
    subscription = _subscription(practice, status="active")
    remote.subscriptions[subscription["id"]] = subscription
    practice.stripe_customer_id = subscription["customer"]
    practice.stripe_subscription_id = subscription["id"]
    practice.subscription_status = "active"
    practice.stripe_state_version = 1
    practice.save()

    stripe_billing.refresh_subscription_state(practice)
    practice.refresh()
    assert practice.subscription_status == "invalid"

    subscription["status"] = "canceled"
    remote.subscriptions[subscription["id"]] = subscription
    assert stripe_billing.apply_stripe_event(
        _event(
            "customer.subscription.deleted",
            subscription,
            event_id="evt_zero_seat_cancel",
            created=int((NOW + timedelta(seconds=1)).timestamp()),
        )
    )
    practice.refresh()
    assert practice.subscription_status == "canceled"


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


def test_unversioned_legacy_subscription_requires_exact_cached_checkout(
    remote: FakeStripe,
):
    practice = _practice()
    practice.trial_ends_at = NOW + timedelta(days=7)
    practice.save()
    subscription = _subscription(practice)
    subscription["metadata"] = {"practice_id": str(practice.id)}
    remote.subscriptions[subscription["id"]] = subscription

    with pytest.raises(
        stripe_billing.StripeStateInvalid,
        match="trial policy does not match Core state",
    ):
        stripe_billing.apply_stripe_event(
            _event(
                "customer.subscription.created",
                subscription,
                event_id="evt_unversioned_without_checkout",
                created=int(NOW.timestamp()),
            )
        )

    practice.refresh()
    assert practice.stripe_subscription_id is None


def test_unversioned_subscription_rejects_wrong_cached_checkout(
    remote: FakeStripe,
):
    practice = _practice()
    practice.trial_ends_at = NOW + timedelta(days=7)
    practice.stripe_checkout_session_id = "cs_expected_legacy"
    practice.save()
    subscription = _subscription(practice)
    subscription["metadata"] = {"practice_id": str(practice.id)}
    remote.subscriptions[subscription["id"]] = subscription
    event = _event(
        "checkout.session.completed",
        subscription,
        event_id="evt_wrong_cached_checkout",
        created=int(NOW.timestamp()),
    )
    event["data"]["object"] = {
        "id": "cs_different_legacy",
        "client_reference_id": str(practice.id),
        "subscription": subscription["id"],
    }

    with pytest.raises(
        stripe_billing.StripeStateConflict,
        match="does not match the pending session",
    ):
        stripe_billing.apply_stripe_event(event)

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
    subscription = _paid_pilot_subscription(practice)
    subscription["id"] = "sub_checkout"
    subscription["items"]["data"][0]["id"] = "si_checkout"
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
    subscription = _paid_pilot_subscription(practice)
    subscription["id"] = "sub_recovered"
    subscription["items"]["data"][0]["id"] = "si_recovered"
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
    practice.stripe_state_version = 1
    practice.stripe_trial_clock_policy = stripe_billing.LEGACY_TRIAL_CLOCK_POLICY
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
        "customer": subscription["customer"],
        "payment_method_collection": "always",
        "metadata": _current_checkout_metadata(practice),
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


def test_cached_checkout_rejects_customer_mismatch_before_resuming(
    remote: FakeStripe,
):
    practice = _practice()
    practice.trial_ends_at = NOW + timedelta(hours=1)
    subscription = _subscription(practice, status="incomplete")
    remote.subscriptions[subscription["id"]] = subscription
    _bind_verified_public_subscription(
        practice,
        subscription,
        status="incomplete",
        trial_ends_at=practice.trial_ends_at,
    )
    practice.stripe_trial_clock_policy = stripe_billing.LEGACY_TRIAL_CLOCK_POLICY
    practice.stripe_checkout_session_id = "cs_resume_wrong_customer"
    practice.stripe_checkout_session_url = (
        "https://checkout.stripe.com/c/pay/resume-wrong-customer"
    )
    practice.stripe_checkout_session_expires_at = NOW + timedelta(hours=1)
    practice.save()
    remote.sessions["cs_resume_wrong_customer"] = {
        "id": "cs_resume_wrong_customer",
        "url": "https://checkout.stripe.com/c/pay/resume-wrong-customer",
        "expires_at": int((NOW + timedelta(hours=1)).timestamp()),
        "client_reference_id": str(practice.id),
        "livemode": False,
        "mode": "subscription",
        "status": "open",
        "subscription": subscription["id"],
        "customer": "cus_other",
        "payment_method_collection": "always",
        "metadata": _current_checkout_metadata(practice),
    }

    with pytest.raises(stripe_billing.StripeStateInvalid, match="customer binding"):
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings",
            cancel_url="https://app.example/settings",
            now=NOW,
        )

    assert not any(
        method == "POST" and path == "/checkout/sessions"
        for method, path, _data, _key in remote.calls
    )


def test_cached_checkout_rejects_remote_nonterminal_subscription_over_stale_terminal(
    remote: FakeStripe,
):
    practice = _practice()
    subscription = _subscription(
        practice,
        subscription_id="sub_remote_active_over_stale_terminal",
        customer_id="cus_remote_active_over_stale_terminal",
        status="active",
    )
    remote.subscriptions[subscription["id"]] = subscription
    _bind_verified_public_subscription(
        practice,
        subscription,
        status="canceled",
    )
    practice.stripe_checkout_session_id = "cs_must_not_resume_over_remote_active"
    practice.stripe_checkout_session_url = (
        "https://checkout.stripe.com/c/pay/must-not-resume"
    )
    practice.stripe_checkout_session_expires_at = NOW + timedelta(hours=1)
    practice.save()

    with pytest.raises(stripe_billing.StripeCheckoutAlreadyCompleted):
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings",
            cancel_url="https://app.example/settings",
            now=NOW,
        )

    assert not any(
        path.startswith("/checkout/sessions")
        for _method, path, _data, _key in remote.calls
    )


def test_cached_open_checkout_rejects_subscription_without_core_binding(
    remote: FakeStripe,
):
    practice = _practice()
    practice.stripe_checkout_session_id = "cs_open_with_unbound_subscription"
    practice.stripe_checkout_session_url = (
        "https://checkout.stripe.com/c/pay/open-with-unbound-subscription"
    )
    practice.stripe_checkout_session_expires_at = NOW + timedelta(hours=1)
    practice.stripe_trial_clock_policy = stripe_billing.LEGACY_TRIAL_CLOCK_POLICY
    practice.save()
    remote.sessions[practice.stripe_checkout_session_id] = {
        "id": practice.stripe_checkout_session_id,
        "url": practice.stripe_checkout_session_url,
        "expires_at": int(practice.stripe_checkout_session_expires_at.timestamp()),
        "client_reference_id": str(practice.id),
        "livemode": False,
        "mode": "subscription",
        "status": "open",
        "subscription": "sub_unbound_open_checkout",
        "customer": "cus_unbound_open_checkout",
        "payment_method_collection": "always",
        "metadata": _current_checkout_metadata(practice),
    }

    with pytest.raises(stripe_billing.StripeStateInvalid, match="unbound subscription"):
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings",
            cancel_url="https://app.example/settings",
            now=NOW,
        )

    assert not any(
        method == "POST" and path == "/checkout/sessions"
        for method, path, _data, _key in remote.calls
    )


def test_cached_pre_v2_checkout_is_retired_when_acquisition_is_off(
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
        "payment_method_collection": "always",
        "metadata": {},
    }
    monkeypatch.setattr(stripe_config, "STRIPE_BILLING_ENABLED", False)

    assert stripe_billing.has_resumable_checkout(practice)
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
    assert any(call[1].endswith("/expire") for call in remote.calls)


def test_legacy_open_checkout_is_expired_and_replaced_under_current_policy(
    remote: FakeStripe,
):
    practice = _practice()
    practice.stripe_checkout_session_id = "cs_legacy_cardless"
    practice.stripe_checkout_session_url = (
        "https://checkout.stripe.com/c/pay/legacy-cardless"
    )
    practice.stripe_checkout_session_expires_at = NOW + timedelta(hours=1)
    practice.save()
    remote.sessions["cs_legacy_cardless"] = {
        "id": "cs_legacy_cardless",
        "url": "https://checkout.stripe.com/c/pay/legacy-cardless",
        "expires_at": int((NOW + timedelta(hours=1)).timestamp()),
        "client_reference_id": str(practice.id),
        "livemode": False,
        "mode": "subscription",
        "status": "open",
        "subscription": None,
        "payment_method_collection": "if_required",
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
    assert remote.sessions["cs_legacy_cardless"]["status"] == "expired"
    assert practice.stripe_checkout_session_id == "cs_test_idempotent"
    assert practice.stripe_checkout_attempt == 1
    expire_call = next(
        call
        for call in remote.calls
        if call[1] == "/checkout/sessions/cs_legacy_cardless/expire"
    )
    assert expire_call[3] == stripe_billing._checkout_expire_idempotency_key(
        "cs_legacy_cardless"
    )
    create_call = next(call for call in remote.calls if call[1] == "/checkout/sessions")
    assert create_call[2]
    assert (
        create_call[2]["payment_method_collection"]
        == stripe_billing.CHECKOUT_PAYMENT_METHOD_COLLECTION
    )
    assert (
        create_call[2]["metadata[checkout_policy]"]
        == stripe_billing.CHECKOUT_IDEMPOTENCY_POLICY_VERSION
    )
    assert create_call[2]["metadata[practice_id]"] == str(practice.id)
    assert create_call[3] == stripe_billing._checkout_idempotency_key(practice)
    legacy_raw = f"cosmas-checkout:{practice.id}:{practice.stripe_checkout_attempt}"
    legacy_key = f"cosmas-checkout-{hashlib.sha256(legacy_raw.encode()).hexdigest()}"
    assert create_call[3] != legacy_key


def test_legacy_open_checkout_is_retired_but_not_replaced_when_acquisition_is_off(
    remote: FakeStripe,
    monkeypatch: pytest.MonkeyPatch,
):
    practice = _practice()
    practice.stripe_checkout_session_id = "cs_legacy_disabled"
    practice.stripe_checkout_session_url = (
        "https://checkout.stripe.com/c/pay/legacy-disabled"
    )
    practice.stripe_checkout_session_expires_at = NOW + timedelta(hours=1)
    practice.save()
    remote.sessions["cs_legacy_disabled"] = {
        "id": "cs_legacy_disabled",
        "url": "https://checkout.stripe.com/c/pay/legacy-disabled",
        "expires_at": int((NOW + timedelta(hours=1)).timestamp()),
        "client_reference_id": str(practice.id),
        "livemode": False,
        "mode": "subscription",
        "status": "open",
        "subscription": None,
        "payment_method_collection": "if_required",
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
    assert remote.sessions["cs_legacy_disabled"]["status"] == "expired"
    assert practice.stripe_checkout_session_id is None
    assert practice.stripe_checkout_attempt == 1
    assert not any(call[1] == "/checkout/sessions" for call in remote.calls)


def test_legacy_open_checkout_with_incomplete_subscription_waits_for_terminal_state(
    remote: FakeStripe,
):
    practice = _practice()
    subscription = _subscription(
        practice,
        subscription_id="sub_legacy_incomplete",
        status="incomplete",
    )
    remote.subscriptions[subscription["id"]] = subscription
    practice.stripe_customer_id = subscription["customer"]
    practice.stripe_subscription_id = subscription["id"]
    practice.subscription_status = "incomplete"
    practice.stripe_state_version = 1
    practice.stripe_checkout_session_id = "cs_legacy_incomplete"
    practice.stripe_checkout_session_url = (
        "https://checkout.stripe.com/c/pay/legacy-incomplete"
    )
    practice.stripe_checkout_session_expires_at = NOW + timedelta(hours=1)
    practice.save()
    remote.sessions["cs_legacy_incomplete"] = {
        "id": "cs_legacy_incomplete",
        "url": "https://checkout.stripe.com/c/pay/legacy-incomplete",
        "expires_at": int((NOW + timedelta(hours=1)).timestamp()),
        "client_reference_id": str(practice.id),
        "livemode": False,
        "mode": "subscription",
        "status": "open",
        "subscription": subscription["id"],
        "customer": subscription["customer"],
        "payment_method_collection": "if_required",
    }

    with pytest.raises(stripe_billing.StripeCheckoutRetirementPending):
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings",
            cancel_url="https://app.example/settings",
            now=NOW,
        )

    practice.refresh()
    assert remote.sessions["cs_legacy_incomplete"]["status"] == "expired"
    assert practice.stripe_checkout_session_id is None
    assert practice.stripe_checkout_attempt == 1
    assert practice.stripe_subscription_id == subscription["id"]
    assert practice.subscription_status == "incomplete"
    assert not any(call[1] == "/checkout/sessions" for call in remote.calls)

    remote.subscriptions[subscription["id"]]["status"] = "incomplete_expired"
    stripe_billing.refresh_subscription_state(practice)
    remote.calls.clear()
    replacement = stripe_billing.create_checkout_session(
        practice,
        customer_email="owner@example.com",
        success_url="https://app.example/settings",
        cancel_url="https://app.example/settings",
        now=NOW,
    )

    practice.refresh()
    assert replacement == "https://checkout.stripe.com/c/pay/test"
    assert practice.stripe_checkout_session_id == "cs_test_idempotent"
    assert practice.stripe_checkout_attempt == 1
    assert len([call for call in remote.calls if call[1] == "/checkout/sessions"]) == 1


def test_legacy_checkout_completion_race_binds_subscription_without_replacement(
    remote: FakeStripe,
    monkeypatch: pytest.MonkeyPatch,
):
    practice = _practice()
    practice.stripe_checkout_session_id = "cs_legacy_completed_race"
    practice.stripe_checkout_session_url = (
        "https://checkout.stripe.com/c/pay/legacy-completed-race"
    )
    practice.stripe_checkout_session_expires_at = NOW + timedelta(hours=1)
    practice.trial_ends_at = NOW + timedelta(days=7)
    practice.save()
    subscription = _subscription(practice, subscription_id="sub_legacy_race")
    subscription["metadata"] = {"practice_id": str(practice.id)}
    remote.subscriptions[subscription["id"]] = subscription
    remote.sessions["cs_legacy_completed_race"] = {
        "id": "cs_legacy_completed_race",
        "url": "https://checkout.stripe.com/c/pay/legacy-completed-race",
        "expires_at": int((NOW + timedelta(hours=1)).timestamp()),
        "client_reference_id": str(practice.id),
        "livemode": False,
        "mode": "subscription",
        "status": "open",
        "subscription": None,
        "payment_method_collection": "if_required",
    }
    original_request = remote.__call__

    def completing_request(method, path, data=None, *, idempotency_key=None):
        if path == "/checkout/sessions/cs_legacy_completed_race/expire":
            remote.calls.append((method, path, data, idempotency_key))
            remote.sessions["cs_legacy_completed_race"].update(
                status="complete", subscription=subscription["id"], url=None
            )
            request = httpx.Request("POST", f"https://api.stripe.com/v1{path}")
            response = httpx.Response(400, request=request)
            raise httpx.HTTPStatusError(
                "Checkout Session is no longer expirable",
                request=request,
                response=response,
            )
        return original_request(method, path, data, idempotency_key=idempotency_key)

    monkeypatch.setattr(stripe_billing, "_request", completing_request)
    with pytest.raises(stripe_billing.StripeCheckoutAlreadyCompleted):
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings",
            cancel_url="https://app.example/settings",
            now=NOW,
        )

    practice.refresh()
    assert practice.stripe_subscription_id == subscription["id"]
    assert practice.subscription_status == "active"
    assert (
        practice.stripe_trial_clock_policy == stripe_billing.LEGACY_TRIAL_CLOCK_POLICY
    )
    assert practice.stripe_checkout_session_id is None
    assert not any(call[1] == "/checkout/sessions" for call in remote.calls)


def test_legacy_checkout_ambiguous_expiration_fails_closed_without_replacement(
    remote: FakeStripe,
    monkeypatch: pytest.MonkeyPatch,
):
    practice = _practice()
    practice.stripe_checkout_session_id = "cs_legacy_expire_ambiguous"
    practice.stripe_checkout_session_url = (
        "https://checkout.stripe.com/c/pay/legacy-expire-ambiguous"
    )
    practice.stripe_checkout_session_expires_at = NOW + timedelta(hours=1)
    practice.save()
    remote.sessions["cs_legacy_expire_ambiguous"] = {
        "id": "cs_legacy_expire_ambiguous",
        "url": "https://checkout.stripe.com/c/pay/legacy-expire-ambiguous",
        "expires_at": int((NOW + timedelta(hours=1)).timestamp()),
        "client_reference_id": str(practice.id),
        "livemode": False,
        "mode": "subscription",
        "status": "open",
        "subscription": None,
        "payment_method_collection": "if_required",
    }
    original_request = remote.__call__

    def ambiguous_expire_request(method, path, data=None, *, idempotency_key=None):
        if path == "/checkout/sessions/cs_legacy_expire_ambiguous/expire":
            remote.calls.append((method, path, data, idempotency_key))
            request = httpx.Request("POST", f"https://api.stripe.com/v1{path}")
            raise httpx.ReadTimeout("expiration result unknown", request=request)
        return original_request(method, path, data, idempotency_key=idempotency_key)

    monkeypatch.setattr(stripe_billing, "_request", ambiguous_expire_request)
    with pytest.raises(
        stripe_billing.StripeStateInvalid,
        match="could not be retired safely",
    ):
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings",
            cancel_url="https://app.example/settings",
            now=NOW,
        )

    practice.refresh()
    assert remote.sessions["cs_legacy_expire_ambiguous"]["status"] == "open"
    assert practice.stripe_checkout_session_id == "cs_legacy_expire_ambiguous"
    assert practice.stripe_checkout_attempt == 0
    assert not any(call[1] == "/checkout/sessions" for call in remote.calls)


@pytest.mark.parametrize("wrong_policy_component", ["collection", "metadata"])
def test_new_checkout_with_wrong_policy_is_retired_and_not_cached(
    remote: FakeStripe,
    monkeypatch: pytest.MonkeyPatch,
    wrong_policy_component: str,
):
    practice = _practice()
    original_request = remote.__call__

    def wrong_policy_request(method, path, data=None, *, idempotency_key=None):
        response = original_request(method, path, data, idempotency_key=idempotency_key)
        if path == "/checkout/sessions":
            if wrong_policy_component == "collection":
                response["payment_method_collection"] = "if_required"
                remote.sessions[response["id"]]["payment_method_collection"] = (
                    "if_required"
                )
            else:
                response["metadata"]["checkout_policy"] = "legacy-policy"
                remote.sessions[response["id"]]["metadata"]["checkout_policy"] = (
                    "legacy-policy"
                )
        return response

    monkeypatch.setattr(stripe_billing, "_request", wrong_policy_request)
    with pytest.raises(
        stripe_billing.StripeStateInvalid,
        match="did not enforce paid-pilot policy",
    ):
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings",
            cancel_url="https://app.example/settings",
            now=NOW,
        )

    practice.refresh()
    assert remote.sessions["cs_test_idempotent"]["status"] == "expired"
    assert practice.stripe_checkout_session_id is None
    assert practice.stripe_checkout_attempt == 1


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
    remote.subscriptions[old["id"]] = old
    practice.stripe_customer_id = old["customer"]
    practice.stripe_subscription_id = old["id"]
    practice.subscription_status = "incomplete_expired"
    practice.stripe_state_version = 1
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
        "customer": old["customer"],
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


def test_subscription_event_waits_for_exact_checkout_session_cache(
    remote: FakeStripe,
    monkeypatch: pytest.MonkeyPatch,
):
    practice = _practice()
    original_request = remote.__call__
    deferred_event: dict = {}

    def racing_request(method, path, data=None, *, idempotency_key=None):
        result = original_request(method, path, data, idempotency_key=idempotency_key)
        if path == "/checkout/sessions":
            subscription = _paid_pilot_subscription(practice)
            subscription["id"] = "sub_race_complete"
            subscription["items"]["data"][0]["id"] = "si_race_complete"
            remote.subscriptions[subscription["id"]] = subscription
            deferred_event.update(
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
            with pytest.raises(
                stripe_billing.StripeStateConflict,
                match="does not match the pending session",
            ):
                stripe_billing.apply_stripe_event(deferred_event)
        return result

    monkeypatch.setattr(stripe_billing, "_request", racing_request)
    assert (
        stripe_billing.create_checkout_session(
            practice,
            customer_email="owner@example.com",
            success_url="https://app.example/settings",
            cancel_url="https://app.example/settings",
            now=NOW,
        )
        == "https://checkout.stripe.com/c/pay/test"
    )
    practice.refresh()
    assert practice.stripe_subscription_id is None
    assert practice.stripe_checkout_session_id == "cs_test_idempotent"

    assert stripe_billing.apply_stripe_event(deferred_event)
    practice.refresh()
    assert practice.subscription_status == "trialing"
    assert practice.stripe_checkout_session_id is None


def test_completed_checkout_rebinds_after_terminal_old_subscription(
    remote: FakeStripe,
):
    practice = _practice()
    practice.stripe_customer_id = "cus_test_123"
    practice.stripe_subscription_id = "sub_old_canceled"
    practice.subscription_status = "canceled"
    practice.stripe_state_version = 1
    practice.stripe_checkout_session_id = "cs_new_complete"
    practice.trial_ends_at = NOW - timedelta(days=1)
    practice.save()
    old = _subscription(
        practice,
        subscription_id="sub_old_canceled",
        customer_id="cus_test_123",
        status="canceled",
    )
    remote.subscriptions[old["id"]] = old
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
        "customer": "cus_test_123",
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


@pytest.mark.parametrize(
    "contract_break", ["wrong_price", "multiple_items", "unknown_status"]
)
def test_bound_invalid_subscription_contract_revokes_stale_active_access(
    remote: FakeStripe,
    contract_break: str,
):
    practice = _practice()
    subscription = _subscription(practice)
    _bind_verified_public_subscription(practice, subscription)
    if contract_break == "wrong_price":
        subscription["items"]["data"][0]["price"] = {"id": "price_other"}
    elif contract_break == "multiple_items":
        subscription["items"]["data"].append(
            {
                "id": "si_extra",
                "quantity": 1,
                "price": {"id": "price_test_x"},
            }
        )
    else:
        subscription["status"] = "future_state"
    remote.subscriptions[subscription["id"]] = subscription
    event_id = f"evt_contract_{contract_break}"

    assert stripe_billing.apply_stripe_event(
        _event(
            "customer.subscription.updated",
            subscription,
            event_id=event_id,
            created=int(NOW.timestamp()),
        )
    )
    practice.refresh()
    assert practice.subscription_status == "invalid"
    assert practice.stripe_subscription_id == subscription["id"]
    assert practice.stripe_customer_id == subscription["customer"]
    assert not practice.entitled(at=NOW)
    assert StripeEventReceipt.get(event_id=event_id) is not None


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
    practice.stripe_state_version = 1
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


def test_organization_revoke_and_quantity_obligation_roll_back_together(
    monkeypatch: pytest.MonkeyPatch,
):
    domain = f"seat-rollback-{secrets.token_hex(4)}.example.com"
    practice, code = _organization_practice(domain, now=NOW)
    doctor = _organization_doctor(f"clinician@{domain}")
    _import_organization_roster(practice, [doctor.email or ""], now=NOW)
    _, member = claim_roster_access(
        doctor,
        supplied_code=code,
        now=NOW + timedelta(seconds=1),
    )
    approval_id = member.organization_roster_approval_id
    assert approval_id is not None
    practice.stripe_customer_id = "cus_org_seat_rollback"
    practice.stripe_subscription_id = "sub_org_seat_rollback"
    practice.subscription_status = "active"
    practice.save()

    def fail_quantity_target(*_args, **_kwargs) -> int:
        raise ClinicianSeatPolicyError("synthetic ledger failure")

    monkeypatch.setattr(
        organization_access,
        "record_active_clinician_seat_quantity",
        fail_quantity_target,
    )
    with pytest.raises(
        organization_access.OrganizationAccessError,
        match="seat ledger is unavailable",
    ):
        revoke_roster_access(
            practice,
            doctor.email or "",
            operator_ref="stripe-billing-test",
            reason="Prove revocation and quantity target roll back together",
            now=NOW + timedelta(seconds=2),
        )

    member.refresh()
    doctor.refresh()
    practice.refresh()
    approval = organization_access.OrganizationRosterApproval.get(id=approval_id)
    assert approval is not None
    assert approval.revoked_at is None
    assert member.access_revoked_at is None
    assert doctor.deleted_at is None
    assert practice.stripe_quantity_sync_required is False
    assert practice.stripe_quantity_sync_target is None


def test_quantity_sync_failure_is_durable_and_reconciles_later(remote: FakeStripe):
    practice = _practice()
    subscription = _subscription(practice, quantity=2)
    remote.subscriptions[subscription["id"]] = subscription
    practice.stripe_customer_id = subscription["customer"]
    practice.stripe_subscription_id = subscription["id"]
    practice.subscription_status = "active"
    practice.stripe_state_version = 1
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


def test_reconcile_pending_quantity_syncs_continues_after_bound_row_failure(
    remote: FakeStripe,
):
    healthy = _practice()
    healthy_subscription = _subscription(
        healthy,
        subscription_id="sub_bulk_healthy",
        customer_id="cus_bulk_healthy",
        quantity=2,
    )
    remote.subscriptions[healthy_subscription["id"]] = healthy_subscription
    _bind_verified_public_subscription(healthy, healthy_subscription)

    failing = _practice()
    failing_subscription = _subscription(
        failing,
        subscription_id="sub_bulk_failing",
        customer_id="cus_bulk_failing",
        quantity=2,
    )
    _bind_verified_public_subscription(failing, failing_subscription)

    unbound = _practice()
    unbound.stripe_quantity_sync_required = True
    unbound.stripe_quantity_sync_target = 1
    unbound.stripe_quantity_sync_version = 1
    unbound.save()

    assert not healthy.stripe_quantity_sync_required
    assert not failing.stripe_quantity_sync_required
    assert stripe_billing.reconcile_pending_quantity_syncs() == (1, 1)

    healthy.refresh()
    failing.refresh()
    unbound.refresh()
    assert not healthy.stripe_quantity_sync_required
    assert healthy.stripe_quantity_sync_error is None
    assert healthy_subscription["items"]["data"][0]["quantity"] == 1
    assert failing.stripe_quantity_sync_required
    assert failing.stripe_quantity_sync_attempts == 1
    assert failing.stripe_quantity_sync_error
    assert unbound.stripe_quantity_sync_required
    assert unbound.stripe_quantity_sync_attempts == 0


def test_over_cap_quantity_marker_is_preserved_without_stripe_io(
    remote: FakeStripe,
):
    practice = _practice()
    for ordinal in range(21):
        doctor = Doctor(
            clerk_id=f"user_over_cap_{ordinal}_{secrets.token_hex(5)}",
            email=f"over-cap-{ordinal}-{secrets.token_hex(3)}@example.com",
            biography="",
        ).save()
        PracticeMember(
            practice_id=practice.id,
            doctor_id=doctor.id,
            role=PracticeRole.workforce.value,
            attested_at=NOW,
        ).save()

    subscription = _subscription(practice, quantity=20)
    remote.subscriptions[subscription["id"]] = subscription
    practice.stripe_customer_id = subscription["customer"]
    practice.stripe_subscription_id = subscription["id"]
    practice.subscription_status = "active"
    practice.stripe_quantity_sync_required = True
    practice.stripe_quantity_sync_target = None
    practice.stripe_quantity_sync_version = 11
    practice.stripe_quantity_sync_error = (
        stripe_billing.LOCAL_SEAT_COUNT_OUT_OF_RANGE_ERROR
    )
    practice.save()

    assert not stripe_billing.sync_subscription_quantity(practice)

    practice.refresh()
    assert practice.subscription_status == "invalid"
    assert practice.stripe_quantity_sync_required is True
    assert practice.stripe_quantity_sync_target is None
    assert practice.stripe_quantity_sync_version == 11
    assert (
        practice.stripe_quantity_sync_error
        == stripe_billing.LOCAL_SEAT_COUNT_OUT_OF_RANGE_ERROR
    )
    assert not any(
        path.startswith("/subscription_items/")
        for _method, path, _data, _key in remote.calls
    )


def test_quantity_sync_fails_closed_when_canonical_seat_ledger_is_invalid(
    remote: FakeStripe, monkeypatch: pytest.MonkeyPatch
):
    practice = _practice()
    subscription = _subscription(practice)
    remote.subscriptions[subscription["id"]] = subscription
    practice.stripe_customer_id = subscription["customer"]
    practice.stripe_subscription_id = subscription["id"]
    practice.subscription_status = "active"
    practice.stripe_quantity_sync_required = True
    practice.stripe_quantity_sync_target = 1
    practice.save()
    monkeypatch.setattr(
        stripe_billing,
        "active_clinician_seat_count",
        _invalid_seat_ledger,
    )

    assert not stripe_billing.sync_subscription_quantity(practice)

    practice.refresh()
    assert practice.subscription_status == "invalid"
    assert practice.stripe_quantity_sync_required is True
    assert practice.stripe_quantity_sync_target is None
    assert practice.stripe_quantity_sync_error == "active_seat_ledger_invalid"
    assert not any(
        path.startswith("/subscription_items/")
        for _method, path, _data, _key in remote.calls
    )


def test_quantity_sync_never_mutates_a_bound_subscription_with_identity_drift(
    remote: FakeStripe,
):
    practice = _practice()
    subscription = _subscription(practice, quantity=2)
    practice.stripe_customer_id = subscription["customer"]
    practice.stripe_subscription_id = subscription["id"]
    practice.subscription_status = "active"
    practice.stripe_state_version = 1
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
    practice.stripe_state_version = 1
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
