"""Stripe billing invariants that protect entitlement and prevent duplicates."""

import hashlib
import hmac
import json
import secrets
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Callable

import httpx
import pytest
from typer.testing import CliRunner

import app.configuration.stripe as stripe_config
from app.cli import app as cli_app
from app.services import stripe_billing
from app.services.organization_access import (
    RosterApprovalInput,
    claim_roster_access,
    configure_provider_organization,
    import_roster_approvals,
    revoke_roster_access,
)

from activemodel.session_manager import get_session, global_session
from app.models.doctor import Doctor
from app.models.practice import (
    ClinicianSeatState,
    Practice,
    PracticeAcquisitionSource,
    PracticeMember,
    PracticeRole,
)
from app.models.practice_access_audit import PracticeAccessAudit
from app.models.stripe_event_inbox import StripeEventInbox
from app.models.stripe_event_receipt import StripeEventReceipt
from sqlalchemy.exc import IntegrityError

WEBHOOK_SECRET = "whsec_test_secret"
NOW = datetime(2026, 8, 20, 20, 0, tzinfo=timezone.utc)
PERIOD_START = NOW - timedelta(days=10)
PERIOD_END = PERIOD_START + timedelta(days=31)


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
        self.drop_quantity_updates = False
        self.dropped_quantity_update_steps: set[int] = set()
        self.quantity_update_count = 0
        self.idempotent_replays: dict[str, dict] = {}
        self.prorated_seats: list[int] = []

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
            return {"data": list(deepcopy(self.sessions).values()), "has_more": False}
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
                "customer": data.get("customer"),
                "payment_method_collection": data["payment_method_collection"],
                "metadata": {
                    key.removeprefix("metadata[").removesuffix("]"): value
                    for key, value in data.items()
                    if key.startswith("metadata[") and key.endswith("]")
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
                    behavior = data.get("proration_behavior")
                    assert behavior in {"create_prorations", "none"}
                    if idempotency_key in self.idempotent_replays:
                        return deepcopy(self.idempotent_replays[idempotency_key])
                    step = self.quantity_update_count
                    self.quantity_update_count += 1
                    if (
                        self.drop_quantity_updates
                        or step in self.dropped_quantity_update_steps
                    ):
                        return deepcopy(item)
                    quantity = int(data["quantity"])
                    if behavior == "create_prorations" and quantity > item["quantity"]:
                        self.prorated_seats.append(quantity - item["quantity"])
                    item["quantity"] = quantity
                    if idempotency_key:
                        self.idempotent_replays[idempotency_key] = deepcopy(item)
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
    monkeypatch.setattr(
        stripe_billing,
        "config",
        lambda *_args, **_kwargs: "app.example,cosma.localhost",
    )


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
            "checkout_policy": stripe_billing.PAID_PILOT_CHECKOUT_POLICY_VERSION,
            "entitlement_contract": "public-capability-entitlements.v1",
            "trial_clock": stripe_billing.LEGACY_TRIAL_CLOCK_POLICY,
        },
        "items": {
            "data": [
                {
                    "id": f"si_{subscription_id.removeprefix('sub_')}",
                    "quantity": quantity,
                    "price": {"id": price_id},
                    "current_period_start": int(PERIOD_START.timestamp()),
                    "current_period_end": int(PERIOD_END.timestamp()),
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


def _invoice_event(
    event_type: str,
    subscription: dict,
    *,
    event_id: str,
    created: int,
    include_practice_hint: bool = True,
) -> dict:
    return {
        "id": event_id,
        "type": event_type,
        "created": created,
        "api_version": "2025-09-30.clover",
        "livemode": False,
        "data": {
            "object": {
                "id": f"in_{event_id.removeprefix('evt_')}",
                "customer": subscription["customer"],
                "customer_email": "sensitive-invoice@example.com",
                "livemode": False,
                "parent": {
                    "type": "subscription_details",
                    "subscription_details": {
                        "subscription": subscription["id"],
                        "metadata": {
                            "practice_id": (
                                subscription["metadata"]["practice_id"]
                                if include_practice_hint
                                else None
                            )
                        },
                    },
                },
            }
        },
    }


def _current_checkout_subscription(practice: Practice, **kwargs: Any) -> dict:
    subscription = _subscription(practice, **kwargs)
    subscription["metadata"]["trial_clock"] = stripe_billing.CHECKOUT_TRIAL_CLOCK_POLICY
    subscription["metadata"]["trial_days"] = "14"
    subscription["trial_start"] = int(NOW.timestamp())
    subscription["trial_end"] = int((NOW + timedelta(days=14)).timestamp())
    return subscription


def _bind_verified_public_subscription(
    practice: Practice,
    subscription: dict,
    *,
    status: str = "active",
    trial_ends_at: datetime | None = None,
) -> None:
    practice.acquisition_source = PracticeAcquisitionSource.public_self_service.value
    practice.stripe_customer_id = subscription["customer"]
    practice.stripe_subscription_id = subscription["id"]
    practice.stripe_subscription_entitlement_contract = subscription["metadata"].get(
        "entitlement_contract",
        stripe_billing.LEGACY_UNVERSIONED_STRIPE_CONTRACT,
    )
    practice.subscription_status = status
    practice.trial_ends_at = trial_ends_at
    practice.stripe_state_version = max(practice.stripe_state_version, 1)
    practice.save()


def _frozen_paid_pilot_checkout_request(
    practice: Practice,
    *,
    contract: str = stripe_billing.CURRENT_STRIPE_ENTITLEMENT_CONTRACT_VERSION,
    quantity: str = "1",
) -> dict[str, str]:
    return {
        "mode": "subscription",
        "line_items[0][price]": stripe_config.STRIPE_PRICE_ID,
        "line_items[0][quantity]": quantity,
        "client_reference_id": str(practice.id),
        "success_url": "https://cosma.localhost/settings?billing=success",
        "cancel_url": "https://cosma.localhost/settings?billing=cancelled",
        "payment_method_collection": "always",
        "metadata[checkout_policy]": (
            stripe_billing.PAID_PILOT_CHECKOUT_POLICY_VERSION
        ),
        "metadata[checkout_attempt]": str(practice.stripe_checkout_attempt),
        "metadata[practice_id]": str(practice.id),
        "metadata[entitlement_contract]": contract,
        "metadata[trial_clock]": stripe_billing.CHECKOUT_TRIAL_CLOCK_POLICY,
        "metadata[trial_days]": "14",
        "subscription_data[metadata][practice_id]": str(practice.id),
        "subscription_data[metadata][checkout_policy]": (
            stripe_billing.PAID_PILOT_CHECKOUT_POLICY_VERSION
        ),
        "subscription_data[metadata][entitlement_contract]": contract,
        "subscription_data[metadata][trial_clock]": (
            stripe_billing.CHECKOUT_TRIAL_CLOCK_POLICY
        ),
        "subscription_data[metadata][trial_days]": "14",
        "subscription_data[trial_period_days]": "14",
        "subscription_data[trial_settings][end_behavior]"
        "[missing_payment_method]": "pause",
        "customer_email": "owner@example.com",
    }


def _store_frozen_paid_pilot_checkout_request(
    practice: Practice,
    *,
    contract: str = stripe_billing.CURRENT_STRIPE_ENTITLEMENT_CONTRACT_VERSION,
) -> dict[str, str]:
    frozen = _frozen_paid_pilot_checkout_request(practice, contract=contract)
    practice.acquisition_source = PracticeAcquisitionSource.public_self_service.value
    practice.stripe_checkout_request_json = json.dumps(
        frozen, sort_keys=True, separators=(",", ":")
    )
    practice.stripe_checkout_request_created_at = NOW
    practice.stripe_checkout_entitlement_contract = contract
    practice.stripe_trial_clock_policy = stripe_billing.CHECKOUT_TRIAL_CLOCK_POLICY
    practice.stripe_checkout_state_version += 1
    practice.stripe_state_version += 1
    practice.save()
    return frozen


def _paid_pilot_checkout_session(
    practice: Practice,
    frozen: dict[str, str],
    *,
    session_id: str = "cs_recovery",
    status: str = "open",
) -> dict[str, Any]:
    return {
        "id": session_id,
        "url": (
            f"https://checkout.stripe.com/c/pay/{session_id}"
            if status == "open"
            else None
        ),
        "expires_at": int((NOW + timedelta(hours=24)).timestamp()),
        "client_reference_id": str(practice.id),
        "livemode": False,
        "mode": "subscription",
        "status": status,
        "subscription": None,
        "customer": frozen.get("customer"),
        "payment_method_collection": "always",
        "metadata": {
            key.removeprefix("metadata[").removesuffix("]"): value
            for key, value in frozen.items()
            if key.startswith("metadata[")
        },
    }


@pytest.mark.parametrize(
    "value",
    [
        " https://checkout.stripe.com/c/pay/test",
        "https://checkout.stripe.com/c/pay/test ",
        "https://evil.example\\@checkout.stripe.com/c/pay/test",
        "https://user@checkout.stripe.com/c/pay/test",
        "https://checkout.stripe.com:443/c/pay/test",
        "https://checkout.stripe.com./c/pay/test",
        "https://checkout.stripe.com/\x00test",
        "https://[invalid",
    ],
)
def test_hosted_url_requires_exact_provider_authority(value: str):
    with pytest.raises(stripe_billing.StripeStateInvalid, match="unexpected origin"):
        stripe_billing._hosted_url(value, hostname="checkout.stripe.com")

    assert (
        stripe_billing._hosted_url(
            "https://checkout.stripe.com/c/pay/test",
            hostname="checkout.stripe.com",
        )
        == "https://checkout.stripe.com/c/pay/test"
    )


@pytest.mark.parametrize("value", [None, True, 10**100, -(10**100)])
def test_checkout_expiry_rejects_noncanonical_timestamps(value: object):
    with pytest.raises(stripe_billing.StripeStateInvalid, match="session expiry"):
        stripe_billing._checkout_session_expiry({"expires_at": value})


@pytest.mark.parametrize("value", [None, True, 10**100, -(10**100)])
def test_event_created_at_rejects_noncanonical_timestamps(value: object):
    with pytest.raises(stripe_billing.StripeStateInvalid, match="creation time"):
        stripe_billing._event_created_at({"created": value})


def test_subscription_trial_end_rejects_out_of_range_timestamp():
    subscription = _subscription(_practice(), status="trialing")
    subscription["trial_start"] = int(NOW.timestamp())
    subscription["trial_end"] = 10**100

    with pytest.raises(stripe_billing.StripeStateInvalid, match="out of range"):
        stripe_billing._trial_end_from_subscription(subscription)


def test_checkout_trial_policy_reader_selects_exact_duration_and_legacy_clock():
    practice = _practice()

    policy, trial_end = stripe_billing._checkout_trial_policy_and_end(
        practice,
        now=NOW + timedelta(microseconds=1),
    )
    assert policy == stripe_billing.CHECKOUT_TRIAL_CLOCK_POLICY
    assert trial_end is None
    assert practice.trial_ends_at is None

    practice.stripe_trial_clock_policy = stripe_billing.LEGACY_TRIAL_CLOCK_POLICY
    practice.trial_ends_at = NOW + timedelta(hours=1)
    assert stripe_billing._checkout_trial_policy_and_end(practice, now=NOW) == (
        stripe_billing.LEGACY_TRIAL_CLOCK_POLICY,
        NOW
        + stripe_billing.MINIMUM_CHECKOUT_TRIAL_LEAD
        + stripe_billing.CHECKOUT_TRIAL_LEAD_SAFETY,
    )


def test_checkout_trial_policy_reader_fails_closed_on_prior_or_invalid_fixed_clock():
    practice = _practice()
    practice.stripe_trial_clock_policy = stripe_billing.CHECKOUT_TRIAL_CLOCK_POLICY
    practice.trial_ends_at = NOW + timedelta(days=14)

    with pytest.raises(stripe_billing.StripeTrialClockReconciliationRequired):
        stripe_billing._checkout_trial_policy_and_end(practice, now=NOW)

    practice.trial_ends_at = datetime(2026, 9, 3)
    with pytest.raises(stripe_billing.StripeStateInvalid, match="trial clock"):
        stripe_billing._checkout_trial_policy_and_end(practice, now=NOW)

    practice.stripe_trial_clock_policy = "unknown-clock"
    with pytest.raises(stripe_billing.StripeStateInvalid, match="trial policy"):
        stripe_billing._checkout_trial_policy_and_end(practice, now=NOW)


def test_bound_customer_reader_accepts_verified_legacy_zero_version(
    remote: FakeStripe,
):
    practice = _practice()
    subscription = _subscription(
        practice,
        subscription_id="sub_reader_legacy",
        customer_id="cus_reader_legacy",
        status="canceled",
    )
    remote.subscriptions[subscription["id"]] = subscription
    practice.acquisition_source = PracticeAcquisitionSource.managed.value
    practice.stripe_subscription_id = subscription["id"]
    practice.stripe_customer_id = subscription["customer"]
    practice.subscription_status = "canceled"
    practice.stripe_state_version = 0
    practice.save()

    binding = stripe_billing._core_subscription_binding(practice)
    assert binding == (subscription["id"], subscription["customer"], 0)
    assert stripe_billing._verified_bound_customer(
        practice,
        expected_binding=binding,
        require_terminal_subscription=True,
    ) == (binding, "canceled")
    assert remote.calls == [("GET", f"/subscriptions/{subscription['id']}", None, None)]


@pytest.mark.parametrize(
    ("customer_id", "subscription_id", "state_version"),
    [
        ("cus_partial", None, 1),
        (None, "sub_partial", 1),
        ("customer_bad", "sub_valid", 1),
        ("cus_valid", "subscription_bad", 1),
        ("cus_public", "sub_public", 0),
    ],
)
def test_verified_bound_customer_rejects_partial_or_untrusted_projection(
    customer_id: str | None,
    subscription_id: str | None,
    state_version: int,
):
    practice = _practice()
    practice.acquisition_source = PracticeAcquisitionSource.public_self_service.value
    practice.stripe_customer_id = customer_id
    practice.stripe_subscription_id = subscription_id
    practice.subscription_status = "canceled"
    practice.stripe_state_version = state_version
    practice.save()

    with pytest.raises(stripe_billing.StripeCoreBindingInvalid, match="authoritative"):
        stripe_billing._verified_bound_customer(
            practice,
            require_terminal_subscription=True,
        )


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("id", "sub_other", "wrong subscription"),
        ("customer", "customer_bad", "customer id"),
        ("livemode", True, "livemode"),
        ("metadata", "bad", "metadata"),
    ],
)
def test_bound_customer_reader_rejects_remote_identity_tampering(
    remote: FakeStripe,
    field: str,
    replacement: object,
    message: str,
):
    practice = _practice()
    subscription = _subscription(
        practice,
        subscription_id="sub_reader_tamper",
        customer_id="cus_reader_tamper",
        status="canceled",
    )
    subscription[field] = replacement
    remote.subscriptions["sub_reader_tamper"] = subscription
    practice.stripe_subscription_id = "sub_reader_tamper"
    practice.stripe_customer_id = "cus_reader_tamper"
    practice.subscription_status = "canceled"
    practice.stripe_state_version = 1
    practice.save()

    with pytest.raises(stripe_billing.StripeStateInvalid, match=message):
        stripe_billing._verified_bound_customer(
            practice,
            require_terminal_subscription=True,
        )
    assert all(call[0] == "GET" for call in remote.calls)


def test_bound_customer_reader_requires_terminal_state_when_requested(
    remote: FakeStripe,
):
    practice = _practice()
    subscription = _subscription(practice, status="active")
    remote.subscriptions[subscription["id"]] = subscription
    _bind_verified_public_subscription(practice, subscription)

    with pytest.raises(stripe_billing.StripeCheckoutAlreadyCompleted):
        stripe_billing._verified_bound_customer(
            practice,
            require_terminal_subscription=True,
        )
    assert all(call[0] == "GET" for call in remote.calls)


def test_checkout_request_replay_deadline_uses_earliest_safety_boundary():
    assert stripe_billing._checkout_request_replay_deadline({}, created_at=NOW) == (
        NOW + stripe_billing.CHECKOUT_REQUEST_REPLAY_WINDOW
    )
    assert stripe_billing._checkout_request_replay_deadline(
        {
            "subscription_data[trial_end]": str(
                int((NOW + timedelta(hours=50)).timestamp())
            )
        },
        created_at=NOW,
    ) == NOW + timedelta(hours=2)


@pytest.mark.parametrize(
    ("frozen_data", "created_at"),
    [
        ({}, datetime(2026, 8, 20, 20, 0)),
        ({"subscription_data[trial_end]": "not-an-integer"}, NOW),
        ({"subscription_data[trial_end]": str(10**100)}, NOW),
        ({"subscription_data[trial_end]": str(-(10**100))}, NOW),
    ],
)
def test_checkout_request_replay_deadline_rejects_invalid_time(
    frozen_data: dict[str, str], created_at: datetime
):
    with pytest.raises(stripe_billing.StripeStateInvalid):
        stripe_billing._checkout_request_replay_deadline(
            frozen_data,
            created_at=created_at,
        )


@pytest.mark.parametrize(
    "contract",
    sorted(stripe_billing.SUPPORTED_STRIPE_ENTITLEMENT_CONTRACT_VERSIONS),
)
def test_frozen_checkout_reader_accepts_supported_contracts_without_io_or_mutation(
    remote: FakeStripe,
    contract: str,
):
    practice = _practice()
    practice.acquisition_source = PracticeAcquisitionSource.public_self_service.value
    practice.stripe_trial_clock_policy = stripe_billing.CHECKOUT_TRIAL_CLOCK_POLICY
    practice.trial_ends_at = None
    practice.stripe_checkout_entitlement_contract = contract
    practice.stripe_checkout_request_created_at = NOW
    practice.stripe_checkout_state_version = 4
    practice.stripe_state_version = 7
    practice.stripe_checkout_session_id = "cs_reader_only"
    frozen = _frozen_paid_pilot_checkout_request(practice, contract=contract)
    practice.stripe_checkout_request_json = json.dumps(
        frozen,
        sort_keys=True,
        separators=(",", ":"),
    )
    practice.save()
    before = practice.model_dump()

    snapshot = stripe_billing._validated_frozen_checkout_request_snapshot(
        practice,
        current=NOW + timedelta(hours=1),
    )

    assert snapshot == (4, 7, "cs_reader_only", 0, frozen)
    assert practice.model_dump() == before
    assert remote.calls == []
    assert stripe_billing._checkout_idempotency_key(
        practice,
        attempt=3,
    ) != stripe_billing._checkout_idempotency_key(practice)
    assert practice.stripe_checkout_attempt == 0


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("metadata[entitlement_contract]", "public-capability-entitlements.v99"),
        ("unexpected_npi", "1234567893"),
        ("line_items[0][price]", "price_wrong"),
        ("metadata[checkout_attempt]", "1"),
        ("client_reference_id", "practice_wrong"),
        ("metadata[checkout_policy]", "unknown-policy"),
        ("metadata[trial_days]", "15"),
        ("subscription_data[trial_period_days]", "15"),
        (
            "subscription_data[trial_end]",
            str(int((NOW + timedelta(days=14)).timestamp())),
        ),
        ("line_items[0][quantity]", "0"),
        ("line_items[0][quantity]", "21"),
        ("line_items[0][quantity]", "01"),
        ("success_url", "https://evil.example/?billing=success"),
        ("cancel_url", "https://evil.example/?billing=cancelled"),
    ],
)
def test_frozen_checkout_reader_rejects_tampering_without_provider_io(
    remote: FakeStripe,
    field: str,
    replacement: str,
):
    practice = _practice()
    practice.acquisition_source = PracticeAcquisitionSource.public_self_service.value
    practice.stripe_trial_clock_policy = stripe_billing.CHECKOUT_TRIAL_CLOCK_POLICY
    practice.trial_ends_at = None
    practice.stripe_checkout_entitlement_contract = (
        stripe_billing.CURRENT_STRIPE_ENTITLEMENT_CONTRACT_VERSION
    )
    practice.stripe_checkout_request_created_at = NOW
    frozen = _frozen_paid_pilot_checkout_request(practice)
    frozen[field] = replacement
    practice.stripe_checkout_request_json = json.dumps(frozen)
    practice.save()

    with pytest.raises(stripe_billing.StripeCheckoutReconciliationRequired):
        stripe_billing._validated_frozen_checkout_request_snapshot(
            practice,
            current=NOW + timedelta(minutes=1),
        )
    assert remote.calls == []


@pytest.mark.parametrize("payload", [None, "", "[]", '{"mode": true}'])
def test_frozen_checkout_reader_rejects_malformed_or_non_string_json(
    remote: FakeStripe,
    payload: str | None,
):
    practice = _practice()
    practice.stripe_checkout_request_json = payload
    practice.stripe_checkout_request_created_at = NOW
    practice.stripe_trial_clock_policy = stripe_billing.CHECKOUT_TRIAL_CLOCK_POLICY
    practice.stripe_checkout_entitlement_contract = (
        stripe_billing.CURRENT_STRIPE_ENTITLEMENT_CONTRACT_VERSION
    )

    with pytest.raises(stripe_billing.StripeCheckoutReconciliationRequired):
        stripe_billing._validated_frozen_checkout_request_snapshot(
            practice,
            current=NOW + timedelta(minutes=1),
        )
    assert remote.calls == []


def test_checkout_session_binding_enforces_expected_customer():
    practice = _practice()
    session = {
        "id": "cs_reader_binding",
        "livemode": False,
        "mode": "subscription",
        "client_reference_id": str(practice.id),
        "customer": "cus_reader_binding",
    }
    stripe_billing._validate_checkout_session_binding(
        session,
        practice,
        expected_id="cs_reader_binding",
        expected_customer_id="cus_reader_binding",
    )

    for field, replacement in (
        ("id", "cs_other"),
        ("livemode", True),
        ("mode", "payment"),
        ("client_reference_id", "practice_other"),
        ("customer", "cus_other"),
    ):
        tampered = dict(session)
        tampered[field] = replacement
        with pytest.raises(stripe_billing.StripeStateInvalid):
            stripe_billing._validate_checkout_session_binding(
                tampered,
                practice,
                expected_id="cs_reader_binding",
                expected_customer_id="cus_reader_binding",
            )


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


def test_stripe_authority_pins_exact_supported_entitlement_contracts():
    assert stripe_billing.LEGACY_CHECKOUT_IDEMPOTENCY_POLICY_VERSION == (
        "card-required-v1"
    )
    assert stripe_billing.CHECKOUT_IDEMPOTENCY_POLICY_VERSION == "paid-pilot-v3"
    assert stripe_billing.PAID_PILOT_CHECKOUT_POLICY_VERSION == "paid-pilot-v3"
    assert stripe_billing.CURRENT_STRIPE_ENTITLEMENT_CONTRACT_VERSION == (
        "public-capability-entitlements.v1"
    )
    assert stripe_billing.SUPPORTED_STRIPE_ENTITLEMENT_CONTRACT_VERSIONS == {
        "public-capability-entitlements.v1"
    }


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
    assert data["payment_method_collection"] == "always"
    stripe_trial_end = int(data["subscription_data[trial_end]"])
    carried_trial_end = datetime.fromtimestamp(stripe_trial_end, tz=timezone.utc)
    assert carried_trial_end >= practice.trial_ends_at
    assert carried_trial_end < practice.trial_ends_at + timedelta(seconds=1)
    assert idempotency_key and idempotency_key.startswith("cosmas-checkout-")


def test_new_checkout_freezes_exact_trial_and_contract_until_card_collection(
    remote: FakeStripe,
):
    practice = _practice()
    practice.acquisition_source = PracticeAcquisitionSource.public_self_service.value
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
    assert data is not None
    assert data["subscription_data[trial_period_days]"] == "14"
    assert "subscription_data[trial_end]" not in data
    assert data["payment_method_collection"] == "always"
    assert data["metadata[checkout_policy]"] == "paid-pilot-v3"
    assert data["metadata[checkout_attempt]"] == "0"
    assert data["metadata[entitlement_contract]"] == (
        stripe_billing.CURRENT_STRIPE_ENTITLEMENT_CONTRACT_VERSION
    )
    assert data["metadata[trial_clock]"] == (stripe_billing.CHECKOUT_TRIAL_CLOCK_POLICY)
    assert data["metadata[trial_days]"] == "14"
    expected_key = hashlib.sha256(
        f"cosmas-checkout:paid-pilot-v3:{practice.id}:0".encode()
    ).hexdigest()
    assert checkout_call[3] == f"cosmas-checkout-{expected_key}"

    practice.refresh()
    assert practice.trial_ends_at is None
    assert practice.stripe_trial_clock_policy == (
        stripe_billing.CHECKOUT_TRIAL_CLOCK_POLICY
    )
    assert practice.stripe_checkout_entitlement_contract == (
        stripe_billing.CURRENT_STRIPE_ENTITLEMENT_CONTRACT_VERSION
    )
    assert practice.stripe_checkout_request_json is None
    assert practice.stripe_checkout_request_created_at is None
    assert practice.stripe_checkout_session_id == "cs_test_idempotent"


def test_lost_checkout_response_replays_identical_frozen_request(
    remote: FakeStripe,
    monkeypatch: pytest.MonkeyPatch,
):
    practice = _practice()
    practice.acquisition_source = PracticeAcquisitionSource.public_self_service.value
    practice.save()
    first_post = True

    def lose_first_response(
        method: str,
        path: str,
        data: dict | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> dict:
        nonlocal first_post
        response = remote(method, path, data, idempotency_key=idempotency_key)
        if path == "/checkout/sessions" and first_post:
            first_post = False
            raise httpx.ReadTimeout("simulated lost Checkout response")
        return response

    monkeypatch.setattr(stripe_billing, "_request", lose_first_response)
    kwargs = {
        "customer_email": "owner@example.com",
        "success_url": "https://app.example/settings?billing=success",
        "cancel_url": "https://app.example/settings?billing=cancelled",
        "now": NOW,
    }

    with pytest.raises(httpx.ReadTimeout, match="lost Checkout response"):
        stripe_billing.create_checkout_session(practice, **kwargs)

    practice.refresh()
    frozen_request = practice.stripe_checkout_request_json
    assert frozen_request is not None
    assert practice.stripe_checkout_session_id is None
    assert practice.trial_ends_at is None

    assert stripe_billing.create_checkout_session(practice, **kwargs) == (
        "https://checkout.stripe.com/c/pay/test"
    )
    posts = [call for call in remote.calls if call[1] == "/checkout/sessions"]
    assert len(posts) == 2
    assert posts[0][2:] == posts[1][2:]
    assert json.dumps(posts[0][2], sort_keys=True, separators=(",", ":")) == (
        frozen_request
    )

    practice.refresh()
    assert practice.stripe_checkout_request_json is None
    assert practice.stripe_checkout_session_id == "cs_test_idempotent"


def test_bound_subscription_contract_version_cannot_change(
    remote: FakeStripe,
):
    practice = _practice()
    subscription = _subscription(practice)
    bound_contract = stripe_billing.CURRENT_STRIPE_ENTITLEMENT_CONTRACT_VERSION
    subscription["metadata"]["entitlement_contract"] = bound_contract
    _bind_verified_public_subscription(practice, subscription)
    subscription["metadata"]["entitlement_contract"] = (
        "public-capability-entitlements.v2"
    )
    remote.subscriptions[subscription["id"]] = subscription

    stripe_billing.refresh_subscription_state(practice)

    practice.refresh()
    assert practice.subscription_status == "invalid"
    assert practice.stripe_subscription_entitlement_contract == bound_contract


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
    practice.acquisition_source = PracticeAcquisitionSource.managed.value
    practice.stripe_customer_id = subscription["customer"]
    practice.stripe_subscription_id = subscription["id"]
    practice.stripe_subscription_entitlement_contract = (
        stripe_billing.CURRENT_STRIPE_ENTITLEMENT_CONTRACT_VERSION
    )
    practice.subscription_status = "past_due"
    practice.stripe_state_version = 0
    practice.save()

    stripe_billing.refresh_subscription_state(practice)

    practice.refresh()
    assert practice.subscription_status == "past_due"
    assert practice.stripe_state_version == 1


@pytest.mark.parametrize("status", ["active", "trialing"])
def test_bound_current_v1_subscription_remains_supported(
    remote: FakeStripe,
    status: str,
):
    practice = _practice()
    trial_end = NOW + timedelta(days=7) if status == "trialing" else None
    subscription = _subscription(practice, status=status)
    if trial_end is not None:
        subscription["trial_start"] = int(NOW.timestamp())
        subscription["trial_end"] = int(trial_end.timestamp())
    assert subscription["metadata"]["entitlement_contract"] == (
        stripe_billing.CURRENT_STRIPE_ENTITLEMENT_CONTRACT_VERSION
    )
    _bind_verified_public_subscription(
        practice,
        subscription,
        status=status,
        trial_ends_at=trial_end,
    )
    practice.stripe_trial_clock_policy = stripe_billing.LEGACY_TRIAL_CLOCK_POLICY
    practice.save()
    remote.subscriptions[subscription["id"]] = subscription

    refreshed = stripe_billing.refresh_subscription_state(practice)

    assert refreshed is not None
    practice.refresh()
    assert practice.subscription_status == status
    assert practice.entitled(at=NOW)


@pytest.mark.parametrize(
    ("trial_offset_seconds", "expected_status"),
    [
        (0, "trialing"),
        (-1, "invalid"),
        (1, "invalid"),
    ],
)
def test_checkout_trial_policy_requires_exactly_fourteen_days(
    remote: FakeStripe,
    trial_offset_seconds: int,
    expected_status: str,
):
    practice = _practice()
    subscription = _subscription(
        practice,
        subscription_id=f"sub_fixed_trial_{trial_offset_seconds:+d}",
        customer_id=f"cus_fixed_trial_{trial_offset_seconds:+d}",
        status="trialing",
    )
    subscription["metadata"]["trial_clock"] = stripe_billing.CHECKOUT_TRIAL_CLOCK_POLICY
    subscription["metadata"]["trial_days"] = "14"
    subscription["trial_start"] = int(NOW.timestamp())
    trial_end = NOW + timedelta(days=14, seconds=trial_offset_seconds)
    subscription["trial_end"] = int(trial_end.timestamp())
    remote.subscriptions[subscription["id"]] = subscription
    _bind_verified_public_subscription(
        practice,
        subscription,
        status="pending_verification",
    )
    practice.stripe_trial_clock_policy = stripe_billing.CHECKOUT_TRIAL_CLOCK_POLICY
    practice.save()

    stripe_billing.refresh_subscription_state(practice)

    practice.refresh()
    assert practice.subscription_status == expected_status
    if expected_status == "trialing":
        assert practice.trial_ends_at == trial_end
    else:
        assert practice.trial_ends_at is None


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
    practice.stripe_subscription_entitlement_contract = (
        stripe_billing.LEGACY_UNVERSIONED_STRIPE_CONTRACT
    )
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


def test_paused_trial_without_card_revokes_then_resumed_restores_entitlement(
    remote: FakeStripe,
):
    practice = _practice()
    practice.trial_ends_at = NOW - timedelta(seconds=1)
    paused = _subscription(practice, status="paused")
    practice.stripe_customer_id = paused["customer"]
    practice.stripe_subscription_id = paused["id"]
    practice.stripe_subscription_entitlement_contract = (
        stripe_billing.CURRENT_STRIPE_ENTITLEMENT_CONTRACT_VERSION
    )
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
    subscription = _subscription(practice, status="active")
    subscription["pause_collection"] = {"behavior": "keep_as_draft"}
    remote.subscriptions[subscription["id"]] = subscription
    _bind_verified_public_subscription(
        practice,
        subscription,
        status="pending_verification",
    )
    practice.stripe_trial_clock_policy = stripe_billing.LEGACY_TRIAL_CLOCK_POLICY
    practice.save()

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
    _bind_verified_public_subscription(practice, canceled, status="active")

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
    practice.stripe_checkout_entitlement_contract = (
        stripe_billing.CURRENT_STRIPE_ENTITLEMENT_CONTRACT_VERSION
    )
    practice.stripe_trial_clock_policy = stripe_billing.LEGACY_TRIAL_CLOCK_POLICY
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
    practice.stripe_subscription_entitlement_contract = (
        stripe_billing.CURRENT_STRIPE_ENTITLEMENT_CONTRACT_VERSION
    )
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


@pytest.mark.parametrize("event_type", ["invoice.paid", "invoice.payment_succeeded"])
def test_paid_invoice_for_known_customer_durably_restores_subscription_mirror(
    remote: FakeStripe,
    event_type: str,
):
    practice = _practice()
    subscription = _current_checkout_subscription(
        practice,
        subscription_id=f"sub_{event_type.replace('.', '_')}",
        customer_id=f"cus_{event_type.replace('.', '_')}",
    )
    remote.subscriptions[subscription["id"]] = subscription
    practice.stripe_customer_id = subscription["customer"]
    practice.save()
    event = _invoice_event(
        event_type,
        subscription,
        event_id=f"evt_{event_type.replace('.', '_')}",
        created=int((NOW + timedelta(days=30)).timestamp()),
        include_practice_hint=False,
    )

    inbox_id, duplicate = stripe_billing.enqueue_stripe_event(event)
    duplicate_id, duplicate_again = stripe_billing.enqueue_stripe_event(event)

    assert inbox_id is not None
    assert duplicate_id == inbox_id
    assert duplicate is False
    assert duplicate_again is True
    assert remote.calls == []
    practice.refresh()
    assert practice.stripe_subscription_id is None
    inbox = StripeEventInbox.get(id=inbox_id)
    assert inbox
    assert "sensitive-invoice@example.com" not in inbox.payload_json

    assert stripe_billing.process_stripe_event_inbox(inbox_id)
    practice.refresh()
    assert practice.stripe_customer_id == subscription["customer"]
    assert practice.stripe_subscription_id == subscription["id"]
    assert practice.stripe_subscription_entitlement_contract == (
        stripe_billing.CURRENT_STRIPE_ENTITLEMENT_CONTRACT_VERSION
    )
    assert practice.stripe_trial_clock_policy == (
        stripe_billing.CHECKOUT_TRIAL_CLOCK_POLICY
    )
    assert practice.trial_ends_at == NOW + timedelta(days=14)
    assert practice.subscription_status == "active"


def test_paid_invoice_does_not_bind_from_mutable_metadata_alone(
    remote: FakeStripe,
):
    practice = _practice()
    subscription = _current_checkout_subscription(
        practice,
        subscription_id="sub_invoice_practice_hint",
        customer_id="cus_invoice_practice_hint",
    )
    remote.subscriptions[subscription["id"]] = subscription
    event = _invoice_event(
        "invoice.paid",
        subscription,
        event_id="evt_invoice_practice_hint",
        created=int((NOW + timedelta(days=30)).timestamp()),
    )

    inbox_id, duplicate = stripe_billing.enqueue_stripe_event(event)

    assert inbox_id is not None
    assert duplicate is False
    assert stripe_billing.process_stripe_event_inbox(inbox_id)
    assert remote.calls == []
    practice.refresh()
    assert practice.stripe_customer_id is None
    assert practice.stripe_subscription_id is None
    assert practice.subscription_status is None
    inbox = StripeEventInbox.get(id=inbox_id)
    assert inbox
    assert inbox.state == "processed"


def test_paid_invoice_recovers_only_through_exact_completed_checkout(
    remote: FakeStripe,
):
    practice = _practice()
    subscription = _current_checkout_subscription(
        practice,
        subscription_id="sub_invoice_checkout_recovery",
        customer_id="cus_invoice_checkout_recovery",
    )
    remote.subscriptions[subscription["id"]] = subscription
    practice.stripe_customer_id = subscription["customer"]
    frozen = _store_frozen_paid_pilot_checkout_request(practice)
    practice.refresh()
    practice.stripe_checkout_session_id = "cs_invoice_checkout_recovery"
    practice.stripe_checkout_session_url = (
        "https://checkout.stripe.com/c/pay/cs_invoice_checkout_recovery"
    )
    practice.stripe_checkout_session_expires_at = NOW + timedelta(hours=24)
    practice.stripe_checkout_request_json = None
    practice.stripe_checkout_request_created_at = None
    practice.save()
    checkout_session = _paid_pilot_checkout_session(
        practice,
        frozen,
        session_id="cs_invoice_checkout_recovery",
        status="complete",
    )
    checkout_session["customer"] = subscription["customer"]
    checkout_session["subscription"] = subscription["id"]
    remote.sessions[checkout_session["id"]] = checkout_session
    event = _invoice_event(
        "invoice.paid",
        subscription,
        event_id="evt_invoice_checkout_recovery",
        created=int((NOW + timedelta(days=30)).timestamp()),
    )

    inbox_id, duplicate = stripe_billing.enqueue_stripe_event(event)

    assert inbox_id is not None
    assert duplicate is False
    assert stripe_billing.process_stripe_event_inbox(inbox_id)
    practice.refresh()
    assert practice.stripe_customer_id == subscription["customer"]
    assert practice.stripe_subscription_id == subscription["id"]
    assert practice.subscription_status == "active"
    assert practice.stripe_checkout_session_id is None
    assert practice.stripe_checkout_request_json is None
    assert practice.stripe_checkout_attempt == 1


def test_paid_invoice_cannot_bypass_an_open_checkout_session(remote: FakeStripe):
    practice = _practice()
    subscription = _current_checkout_subscription(
        practice,
        subscription_id="sub_invoice_open_checkout",
        customer_id="cus_invoice_open_checkout",
    )
    remote.subscriptions[subscription["id"]] = subscription
    practice.stripe_customer_id = subscription["customer"]
    frozen = _store_frozen_paid_pilot_checkout_request(practice)
    practice.refresh()
    practice.stripe_checkout_session_id = "cs_invoice_open_checkout"
    practice.stripe_checkout_session_url = (
        "https://checkout.stripe.com/c/pay/cs_invoice_open_checkout"
    )
    practice.stripe_checkout_session_expires_at = NOW + timedelta(hours=24)
    practice.stripe_checkout_request_json = None
    practice.stripe_checkout_request_created_at = None
    practice.save()
    checkout_session = _paid_pilot_checkout_session(
        practice,
        frozen,
        session_id="cs_invoice_open_checkout",
        status="open",
    )
    checkout_session["customer"] = subscription["customer"]
    remote.sessions[checkout_session["id"]] = checkout_session

    with pytest.raises(stripe_billing.StripeStateConflict, match="still pending"):
        stripe_billing.apply_stripe_event(
            _invoice_event(
                "invoice.paid",
                subscription,
                event_id="evt_invoice_open_checkout",
                created=int(NOW.timestamp()),
            )
        )

    practice.refresh()
    assert practice.stripe_subscription_id is None
    assert practice.stripe_checkout_session_id == "cs_invoice_open_checkout"


def test_bound_invoice_customer_mismatch_is_consumed_and_revokes_access(
    remote: FakeStripe,
):
    practice = _practice()
    subscription = _current_checkout_subscription(practice)
    remote.subscriptions[subscription["id"]] = subscription
    _bind_verified_public_subscription(practice, subscription)
    practice.stripe_trial_clock_policy = stripe_billing.CHECKOUT_TRIAL_CLOCK_POLICY
    practice.trial_ends_at = NOW + timedelta(days=14)
    practice.save()
    event = _invoice_event(
        "invoice.paid",
        subscription,
        event_id="evt_invoice_customer_mismatch",
        created=int(NOW.timestamp()),
    )
    event["data"]["object"]["customer"] = "cus_unrelated"

    inbox_id, duplicate = stripe_billing.enqueue_stripe_event(event)

    assert inbox_id is not None
    assert duplicate is False
    practice.refresh()
    assert practice.subscription_status == "pending_verification"
    assert stripe_billing.process_stripe_event_inbox(inbox_id)
    practice.refresh()
    assert practice.subscription_status == "invalid"
    assert practice.stripe_subscription_id == subscription["id"]
    assert practice.stripe_quantity_sync_error == (
        "invoice customer does not match its Stripe subscription"
    )
    inbox = StripeEventInbox.get(id=inbox_id)
    assert inbox
    assert inbox.state == "processed"


def test_invoice_envelope_accepts_legacy_and_expanded_subscription_references():
    practice = _practice()
    subscription = _current_checkout_subscription(practice)
    legacy = _invoice_event(
        "invoice.paid",
        subscription,
        event_id="evt_invoice_legacy_shape",
        created=int(NOW.timestamp()),
    )
    legacy_object = legacy["data"]["object"]
    legacy_object["subscription"] = {"id": subscription["id"]}
    legacy_object["customer"] = {"id": subscription["customer"]}
    del legacy_object["parent"]

    envelope = stripe_billing.validate_stripe_event_envelope(legacy)

    assert envelope is not None
    assert envelope[-1] == subscription["id"]


def test_invoice_envelope_rejects_conflicting_subscription_references():
    practice = _practice()
    subscription = _current_checkout_subscription(practice)
    event = _invoice_event(
        "invoice.paid",
        subscription,
        event_id="evt_invoice_conflicting_shape",
        created=int(NOW.timestamp()),
    )
    event["data"]["object"]["subscription"] = "sub_conflicting"

    with pytest.raises(stripe_billing.StripeStateInvalid, match="references conflict"):
        stripe_billing.validate_stripe_event_envelope(event)


def test_one_off_invoice_is_ignored_without_durable_storage(remote: FakeStripe):
    practice = _practice()
    subscription = _current_checkout_subscription(practice)
    event = _invoice_event(
        "invoice.paid",
        subscription,
        event_id="evt_one_off_invoice",
        created=int(NOW.timestamp()),
    )
    event["data"]["object"]["parent"] = {
        "type": "quote_details",
        "quote_details": {"quote": "qt_one_off"},
    }

    assert stripe_billing.enqueue_stripe_event(event) == (None, False)
    assert (
        StripeEventInbox.where(
            StripeEventInbox.event_id == "evt_one_off_invoice"
        ).count()
        == 0
    )
    assert remote.calls == []


def test_invoice_payment_failed_preserves_bound_fail_closed_lifecycle(
    remote: FakeStripe,
):
    practice = _practice()
    subscription = _subscription(practice, status="past_due")
    remote.subscriptions[subscription["id"]] = subscription
    _bind_verified_public_subscription(practice, subscription, status="active")
    event = _invoice_event(
        "invoice.payment_failed",
        subscription,
        event_id="evt_invoice_payment_failed",
        created=int(NOW.timestamp()),
    )

    inbox_id, duplicate = stripe_billing.enqueue_stripe_event(event)

    assert inbox_id is not None
    assert duplicate is False
    practice.refresh()
    assert practice.subscription_status == "pending_verification"
    assert stripe_billing.process_stripe_event_inbox(inbox_id)
    practice.refresh()
    assert practice.stripe_customer_id == subscription["customer"]
    assert practice.stripe_subscription_id == subscription["id"]
    assert practice.subscription_status == "past_due"
    assert not practice.entitled(at=NOW)


@pytest.mark.parametrize("event_type", ["invoice.paid", "invoice.payment_succeeded"])
@pytest.mark.parametrize("mismatch", ["account", "mode", "price"])
def test_paid_invoice_initial_binding_fails_closed_on_authority_mismatch(
    remote: FakeStripe,
    event_type: str,
    mismatch: str,
):
    practice = _practice()
    subscription = _current_checkout_subscription(
        practice,
        subscription_id=f"sub_invoice_bad_{mismatch}_{event_type.replace('.', '_')}",
        customer_id=f"cus_invoice_bad_{mismatch}_{event_type.replace('.', '_')}",
    )
    remote.subscriptions[subscription["id"]] = subscription
    practice.stripe_customer_id = subscription["customer"]
    practice.save()
    event = _invoice_event(
        event_type,
        subscription,
        event_id=f"evt_invoice_bad_{mismatch}_{event_type.replace('.', '_')}",
        created=int(NOW.timestamp()),
    )
    if mismatch == "account":
        event["account"] = "acct_other"
    elif mismatch == "mode":
        event["data"]["object"]["livemode"] = True
    else:
        remote.subscriptions[subscription["id"]]["items"]["data"][0]["price"] = {
            "id": "price_other"
        }

    with pytest.raises(stripe_billing.StripeStateInvalid):
        stripe_billing.apply_stripe_event(event)

    practice.refresh()
    assert practice.stripe_customer_id == subscription["customer"]
    assert practice.stripe_subscription_id is None
    assert practice.subscription_status is None


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
    past_due = _subscription(practice, status="past_due")
    practice.stripe_customer_id = past_due["customer"]
    practice.stripe_subscription_id = past_due["id"]
    practice.stripe_subscription_entitlement_contract = (
        stripe_billing.CURRENT_STRIPE_ENTITLEMENT_CONTRACT_VERSION
    )
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
    practice.acquisition_source = PracticeAcquisitionSource.public_self_service.value
    practice.stripe_customer_id = "cus_bound_precedence"
    practice.stripe_subscription_id = "sub_bound_precedence"
    practice.stripe_state_version = 1
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
    practice.acquisition_source = PracticeAcquisitionSource.public_self_service.value
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
    practice.acquisition_source = PracticeAcquisitionSource.public_self_service.value
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
    practice.acquisition_source = PracticeAcquisitionSource.public_self_service.value
    practice.stripe_customer_id = "cus_trial_clock_required"
    practice.stripe_subscription_id = "sub_trial_clock_required"
    practice.stripe_state_version = 1
    practice.subscription_status = "trialing"
    practice.trial_ends_at = trial_end

    assert not practice.entitled(at=NOW)


def test_delete_wins_and_delayed_older_active_event_cannot_restore_access(
    remote: FakeStripe,
):
    practice = _practice()
    practice.trial_ends_at = NOW - timedelta(days=1)
    practice.save()
    canceled = _subscription(practice, status="canceled")
    remote.subscriptions[canceled["id"]] = canceled
    _bind_verified_public_subscription(practice, canceled, status="active")

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
    canceled["metadata"]["practice_id"] = str(metadata_target.id)
    remote.subscriptions[canceled["id"]] = canceled
    bound.stripe_customer_id = canceled["customer"]
    bound.stripe_subscription_id = canceled["id"]
    bound.stripe_subscription_entitlement_contract = (
        stripe_billing.CURRENT_STRIPE_ENTITLEMENT_CONTRACT_VERSION
    )
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

    with pytest.raises(
        stripe_billing.StripeStateConflict,
        match="exact Checkout completion",
    ):
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
    practice.stripe_checkout_entitlement_contract = (
        stripe_billing.CURRENT_STRIPE_ENTITLEMENT_CONTRACT_VERSION
    )
    practice.stripe_trial_clock_policy = stripe_billing.LEGACY_TRIAL_CLOCK_POLICY
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
    practice.stripe_checkout_entitlement_contract = (
        stripe_billing.CURRENT_STRIPE_ENTITLEMENT_CONTRACT_VERSION
    )
    practice.stripe_trial_clock_policy = stripe_billing.LEGACY_TRIAL_CLOCK_POLICY
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
    practice.stripe_subscription_entitlement_contract = (
        stripe_billing.CURRENT_STRIPE_ENTITLEMENT_CONTRACT_VERSION
    )
    practice.subscription_status = "incomplete"
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
        "payment_method_collection": "always",
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


def test_cached_open_checkout_with_empty_metadata_resumes_when_acquisition_is_off(
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
    assert not any(call[1].endswith("/expire") for call in remote.calls)


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
    practice.stripe_subscription_entitlement_contract = (
        stripe_billing.CURRENT_STRIPE_ENTITLEMENT_CONTRACT_VERSION
    )
    practice.subscription_status = "incomplete"
    practice.stripe_trial_clock_policy = stripe_billing.LEGACY_TRIAL_CLOCK_POLICY
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
    practice.stripe_checkout_entitlement_contract = (
        stripe_billing.CURRENT_STRIPE_ENTITLEMENT_CONTRACT_VERSION
    )
    practice.stripe_trial_clock_policy = stripe_billing.LEGACY_TRIAL_CLOCK_POLICY
    practice.save()
    subscription = _subscription(practice, subscription_id="sub_legacy_race")
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
    practice.stripe_customer_id = old["customer"]
    practice.stripe_subscription_id = old["id"]
    practice.subscription_status = "incomplete_expired"
    practice.stripe_checkout_session_id = "cs_expired_old"
    practice.stripe_checkout_session_url = "https://checkout.stripe.com/c/pay/old"
    practice.stripe_checkout_session_expires_at = NOW - timedelta(seconds=1)
    practice.save()
    remote.subscriptions[old["id"]] = old
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


def test_checkout_completion_before_session_cache_fails_closed(
    remote: FakeStripe,
    monkeypatch: pytest.MonkeyPatch,
):
    practice = _practice()
    original_request = remote.__call__
    deferred_event: dict = {}

    def racing_request(method, path, data=None, *, idempotency_key=None):
        result = original_request(method, path, data, idempotency_key=idempotency_key)
        if path == "/checkout/sessions":
            assert data is not None
            subscription = _subscription(
                practice,
                subscription_id="sub_race_complete",
                status="trialing",
            )
            subscription["metadata"] = {
                key.removeprefix("subscription_data[metadata][").removesuffix(
                    "]"
                ): value
                for key, value in data.items()
                if key.startswith("subscription_data[metadata][") and key.endswith("]")
            }
            subscription["trial_start"] = int(NOW.timestamp())
            subscription["trial_end"] = int((NOW + timedelta(days=14)).timestamp())
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
                match="pending session",
            ):
                stripe_billing.apply_stripe_event(deferred_event)
        return result

    monkeypatch.setattr(stripe_billing, "_request", racing_request)
    url = stripe_billing.create_checkout_session(
        practice,
        customer_email="owner@example.com",
        success_url="https://app.example/settings",
        cancel_url="https://app.example/settings",
        now=NOW,
    )
    practice.refresh()
    assert url == "https://checkout.stripe.com/c/pay/test"
    assert practice.subscription_status is None
    assert practice.stripe_subscription_id is None
    assert practice.stripe_checkout_session_id == "cs_test_idempotent"
    assert practice.stripe_checkout_entitlement_contract == (
        stripe_billing.CURRENT_STRIPE_ENTITLEMENT_CONTRACT_VERSION
    )
    assert practice.stripe_trial_clock_policy == (
        stripe_billing.CHECKOUT_TRIAL_CLOCK_POLICY
    )

    assert stripe_billing.apply_stripe_event(deferred_event)
    practice.refresh()
    assert practice.subscription_status == "trialing"
    assert practice.stripe_subscription_id == "sub_race_complete"
    assert practice.stripe_subscription_entitlement_contract == (
        stripe_billing.CURRENT_STRIPE_ENTITLEMENT_CONTRACT_VERSION
    )
    assert practice.stripe_checkout_session_id is None


def test_operator_recovery_expires_discovered_open_checkout_and_audits(
    remote: FakeStripe,
):
    practice = _practice()
    frozen = _store_frozen_paid_pilot_checkout_request(practice)
    remote.sessions["cs_recovery"] = _paid_pilot_checkout_session(practice, frozen)
    recovery_args: dict[str, Any] = {
        "operator_ref": "ticket-CHECKOUT-RECOVERY-1",
        "reason": "Resolve the retained Session before rotating the attempt",
        "evidence_reference": "evidence://stripe/checkout-recovery-1.json",
        "evidence_sha256": "a" * 64,
        "now": NOW + stripe_billing.CHECKOUT_REQUEST_REPLAY_WINDOW,
    }

    assert stripe_billing.reconcile_frozen_checkout_request(
        practice, **recovery_args
    ) == ("would_expire_open_session", None)
    practice.refresh()
    assert practice.stripe_checkout_request_json is not None
    assert remote.sessions["cs_recovery"]["status"] == "open"

    outcome, audit_id = stripe_billing.reconcile_frozen_checkout_request(
        practice, apply=True, **recovery_args
    )

    assert outcome == "operator_expired_open_session"
    assert audit_id is not None
    audit = PracticeAccessAudit.get(id=audit_id)
    assert audit is not None
    assert audit.action == "stripe_checkout_recovery_started"
    assert audit.operator_ref == recovery_args["operator_ref"]
    assert audit.document_reference == recovery_args["evidence_reference"]
    assert audit.evidence_sha256 == recovery_args["evidence_sha256"]
    practice.refresh()
    assert remote.sessions["cs_recovery"]["status"] == "expired"
    assert practice.stripe_checkout_request_json is None
    assert practice.stripe_checkout_session_id is None
    assert practice.stripe_checkout_attempt == 1
    # The trial begins only after Stripe authoritatively reports completion.
    assert practice.trial_ends_at is None


def test_operator_recovery_absence_requires_interlocks_and_evidence(
    remote: FakeStripe,
    monkeypatch: pytest.MonkeyPatch,
):
    practice = _practice()
    _store_frozen_paid_pilot_checkout_request(practice)
    recovery_args: dict[str, Any] = {
        "operator_ref": "ticket-CHECKOUT-RECOVERY-2",
        "reason": "Provider inventory contains no matching Session",
        "evidence_reference": "evidence://stripe/checkout-recovery-2.json",
        "evidence_sha256": "b" * 64,
    }
    recovery_boundary = (
        NOW
        + stripe_billing.CHECKOUT_REQUEST_REPLAY_WINDOW
        + stripe_billing.CHECKOUT_RECOVERY_TRANSPORT_SAFETY
    )

    assert stripe_billing.reconcile_frozen_checkout_request(
        practice, now=recovery_boundary, **recovery_args
    ) == ("no_matching_session", None)
    with pytest.raises(stripe_billing.StripeCheckoutReconciliationRequired):
        stripe_billing.reconcile_frozen_checkout_request(
            practice, apply=True, now=recovery_boundary, **recovery_args
        )
    with pytest.raises(stripe_billing.StripeStateInvalid, match="disable Checkout"):
        stripe_billing.reconcile_frozen_checkout_request(
            practice,
            confirm_absent=True,
            apply=True,
            now=recovery_boundary,
            **recovery_args,
        )
    monkeypatch.setattr(stripe_config, "STRIPE_BILLING_ENABLED", False)
    with pytest.raises(stripe_billing.StripeStateInvalid, match="safety window"):
        stripe_billing.reconcile_frozen_checkout_request(
            practice,
            confirm_absent=True,
            apply=True,
            now=recovery_boundary - timedelta(seconds=1),
            **recovery_args,
        )

    outcome, audit_id = stripe_billing.reconcile_frozen_checkout_request(
        practice,
        confirm_absent=True,
        apply=True,
        now=recovery_boundary,
        **recovery_args,
    )

    assert outcome == "operator_confirmed_absent"
    assert PracticeAccessAudit.get(id=audit_id)
    practice.refresh()
    assert practice.stripe_checkout_request_json is None
    assert practice.stripe_checkout_attempt == 1
    assert practice.trial_ends_at is None


def test_operator_absence_never_shortens_the_full_transport_boundary(
    remote: FakeStripe,
    monkeypatch: pytest.MonkeyPatch,
):
    practice = _practice()
    trial_end = NOW + timedelta(hours=50)
    practice.trial_ends_at = trial_end
    practice.stripe_trial_clock_policy = stripe_billing.LEGACY_TRIAL_CLOCK_POLICY
    practice.stripe_checkout_entitlement_contract = (
        stripe_billing.CURRENT_STRIPE_ENTITLEMENT_CONTRACT_VERSION
    )
    practice.stripe_checkout_request_created_at = NOW
    frozen = _frozen_paid_pilot_checkout_request(practice)
    frozen["metadata[trial_clock]"] = stripe_billing.LEGACY_TRIAL_CLOCK_POLICY
    frozen["subscription_data[metadata][trial_clock]"] = (
        stripe_billing.LEGACY_TRIAL_CLOCK_POLICY
    )
    frozen.pop("metadata[trial_days]")
    frozen.pop("subscription_data[metadata][trial_days]")
    frozen.pop("subscription_data[trial_period_days]")
    frozen["subscription_data[trial_end]"] = str(int(trial_end.timestamp()))
    practice.stripe_checkout_request_json = json.dumps(
        frozen, sort_keys=True, separators=(",", ":")
    )
    practice.stripe_checkout_state_version += 1
    practice.stripe_state_version += 1
    practice.save()
    monkeypatch.setattr(stripe_config, "STRIPE_BILLING_ENABLED", False)

    # The legacy trial lead stops automatic replay after two hours, but absence
    # cannot be attested until the full idempotency transport boundary passes.
    with pytest.raises(stripe_billing.StripeStateInvalid, match="safety window"):
        stripe_billing.reconcile_frozen_checkout_request(
            practice,
            operator_ref="ticket-CHECKOUT-RECOVERY-LEGACY",
            reason="Do not shorten the provider absence window",
            evidence_reference="evidence://stripe/checkout-recovery-legacy.json",
            evidence_sha256="c" * 64,
            confirm_absent=True,
            apply=True,
            now=NOW + timedelta(hours=2, minutes=5),
        )

    practice.refresh()
    assert practice.stripe_checkout_request_json is not None
    assert practice.stripe_checkout_attempt == 0


@pytest.mark.parametrize("inventory_shape", ["partial", "duplicate"])
def test_operator_recovery_rejects_ambiguous_inventory_without_mutation(
    remote: FakeStripe,
    inventory_shape: str,
):
    practice = _practice()
    frozen = _store_frozen_paid_pilot_checkout_request(practice)
    exact = _paid_pilot_checkout_session(practice, frozen)
    if inventory_shape == "partial":
        exact["metadata"].pop("checkout_attempt")
        remote.sessions[exact["id"]] = exact
        error = "ambiguous"
    else:
        remote.sessions[exact["id"]] = exact
        second = deepcopy(exact)
        second["id"] = "cs_recovery_duplicate"
        remote.sessions[second["id"]] = second
        error = "multiple"

    with pytest.raises(stripe_billing.StripeStateConflict, match=error):
        stripe_billing.reconcile_frozen_checkout_request(
            practice,
            operator_ref="ticket-CHECKOUT-RECOVERY-AMBIGUOUS",
            reason="Refuse ambiguous provider inventory",
            now=NOW + stripe_billing.CHECKOUT_REQUEST_REPLAY_WINDOW,
        )

    practice.refresh()
    assert practice.stripe_checkout_request_json is not None
    assert practice.stripe_checkout_attempt == 0


def test_operator_recovery_rejects_wrong_bound_customer(remote: FakeStripe):
    practice = _practice()
    practice.stripe_customer_id = "cus_expected"
    practice.save()
    frozen = _store_frozen_paid_pilot_checkout_request(practice)
    frozen.pop("customer_email")
    frozen["customer"] = "cus_expected"
    practice.stripe_checkout_request_json = json.dumps(
        frozen, sort_keys=True, separators=(",", ":")
    )
    practice.save()
    session = _paid_pilot_checkout_session(practice, frozen)
    session["customer"] = "cus_wrong"
    remote.sessions[session["id"]] = session

    with pytest.raises(stripe_billing.StripeStateInvalid, match="customer binding"):
        stripe_billing.reconcile_frozen_checkout_request(
            practice,
            operator_ref="ticket-CHECKOUT-RECOVERY-CUSTOMER",
            reason="Refuse a mismatched customer binding",
            now=NOW + stripe_billing.CHECKOUT_REQUEST_REPLAY_WINDOW,
        )

    practice.refresh()
    assert practice.stripe_checkout_request_json is not None


def test_operator_recovery_binds_completion_during_expiration(
    remote: FakeStripe, monkeypatch: pytest.MonkeyPatch
):
    practice = _practice()
    frozen = _store_frozen_paid_pilot_checkout_request(practice)
    session = _paid_pilot_checkout_session(practice, frozen)
    remote.sessions[session["id"]] = session
    subscription = _subscription(practice, status="trialing")
    subscription["metadata"].update(
        entitlement_contract=(
            stripe_billing.CURRENT_STRIPE_ENTITLEMENT_CONTRACT_VERSION
        ),
        trial_clock=stripe_billing.CHECKOUT_TRIAL_CLOCK_POLICY,
        trial_days="14",
    )
    subscription["trial_start"] = int(NOW.timestamp())
    subscription["trial_end"] = int((NOW + timedelta(days=14)).timestamp())
    remote.subscriptions[subscription["id"]] = subscription
    original_request = remote.__call__

    def complete_during_expiration(method, path, data=None, *, idempotency_key=None):
        if path == "/checkout/sessions/cs_recovery/expire":
            remote.calls.append((method, path, data, idempotency_key))
            remote.sessions["cs_recovery"].update(
                status="complete", subscription=subscription["id"], url=None
            )
            return deepcopy(remote.sessions["cs_recovery"])
        return original_request(method, path, data, idempotency_key=idempotency_key)

    monkeypatch.setattr(stripe_billing, "_request", complete_during_expiration)
    outcome, audit_id = stripe_billing.reconcile_frozen_checkout_request(
        practice,
        operator_ref="ticket-CHECKOUT-RECOVERY-COMPLETE",
        reason="Bind completion raced with retirement",
        evidence_reference="evidence://stripe/checkout-recovery-complete.json",
        evidence_sha256="d" * 64,
        apply=True,
        now=NOW + stripe_billing.CHECKOUT_REQUEST_REPLAY_WINDOW,
    )

    assert outcome == "operator_bound_completed_subscription"
    assert PracticeAccessAudit.get(id=audit_id)
    practice.refresh()
    assert practice.stripe_subscription_id == subscription["id"]
    assert practice.subscription_status == "trialing"
    assert practice.trial_ends_at == NOW + timedelta(days=14)
    assert practice.stripe_checkout_session_id is None


def test_checkout_inventory_recovery_paginates_to_exact_attempt(
    monkeypatch: pytest.MonkeyPatch,
):
    practice = _practice()
    frozen = _store_frozen_paid_pilot_checkout_request(practice)
    exact = _paid_pilot_checkout_session(practice, frozen)
    unrelated = deepcopy(exact)
    unrelated["id"] = "cs_unrelated"
    unrelated["client_reference_id"] = "practice_other"
    unrelated["metadata"]["practice_id"] = "practice_other"
    observed: list[dict[str, str]] = []

    def paginated_request(
        method: str,
        path: str,
        data: dict[str, str] | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        assert method == "GET"
        assert path == "/checkout/sessions"
        assert idempotency_key is None
        assert data is not None
        observed.append(data)
        if len(observed) == 1:
            return {"data": [unrelated], "has_more": True}
        return {"data": [exact], "has_more": False}

    monkeypatch.setattr(stripe_billing, "_request", paginated_request)
    matches, ambiguous = stripe_billing._matching_frozen_checkout_sessions(
        practice,
        checkout_attempt=practice.stripe_checkout_attempt,
        entitlement_contract=(
            stripe_billing.CURRENT_STRIPE_ENTITLEMENT_CONTRACT_VERSION
        ),
        request_created_at=NOW,
    )

    assert [match["id"] for match in matches] == ["cs_recovery"]
    assert ambiguous == []
    assert observed[1]["starting_after"] == "cs_unrelated"


def test_checkout_recovery_cli_defaults_to_dry_run_and_guards_confirmation(
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
    arguments = [
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
    ]

    result = CliRunner().invoke(cli_app, arguments)
    assert result.exit_code == 0
    assert "mode=dry-run" in result.output
    assert observed_apply == [False]

    rejected = CliRunner().invoke(cli_app, [*arguments, "--confirm-absent"])
    assert rejected.exit_code != 0
    assert observed_apply == [False]

    applied = CliRunner().invoke(cli_app, [*arguments, "--apply"])
    assert applied.exit_code == 0
    assert "mode=applied" in applied.output
    assert observed_apply == [False, True]


def test_new_writer_rejects_unversioned_subscription_completion(
    remote: FakeStripe,
):
    practice = _practice()
    practice.acquisition_source = PracticeAcquisitionSource.public_self_service.value
    practice.save()

    stripe_billing.create_checkout_session(
        practice,
        customer_email="owner@example.com",
        success_url="https://app.example/settings",
        cancel_url="https://app.example/settings",
        now=NOW,
    )
    practice.refresh()
    assert practice.stripe_checkout_session_id == "cs_test_idempotent"
    assert practice.stripe_checkout_entitlement_contract == (
        stripe_billing.CURRENT_STRIPE_ENTITLEMENT_CONTRACT_VERSION
    )

    subscription = _subscription(practice, subscription_id="sub_public_legacy_writer")
    subscription["metadata"] = {"practice_id": str(practice.id)}
    remote.subscriptions[subscription["id"]] = subscription
    event = _event(
        "checkout.session.completed",
        subscription,
        event_id="evt_public_legacy_writer",
        created=int(NOW.timestamp()),
    )
    event["data"]["object"] = {
        "id": "cs_test_idempotent",
        "client_reference_id": str(practice.id),
        "subscription": subscription["id"],
    }

    with pytest.raises(stripe_billing.StripeStateInvalid, match="cannot change"):
        stripe_billing.apply_stripe_event(event)
    practice.refresh()
    assert practice.stripe_subscription_id is None


def test_completed_checkout_rebinds_after_terminal_old_subscription(
    remote: FakeStripe,
):
    practice = _practice()
    practice.stripe_customer_id = "cus_test_123"
    practice.stripe_subscription_id = "sub_old_canceled"
    practice.stripe_subscription_entitlement_contract = (
        stripe_billing.CURRENT_STRIPE_ENTITLEMENT_CONTRACT_VERSION
    )
    practice.subscription_status = "canceled"
    practice.stripe_checkout_session_id = "cs_new_complete"
    practice.stripe_checkout_entitlement_contract = (
        stripe_billing.CURRENT_STRIPE_ENTITLEMENT_CONTRACT_VERSION
    )
    practice.stripe_trial_clock_policy = stripe_billing.LEGACY_TRIAL_CLOCK_POLICY
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
    practice.stripe_subscription_entitlement_contract = (
        stripe_billing.CURRENT_STRIPE_ENTITLEMENT_CONTRACT_VERSION
    )
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
    subscription = _subscription(practice, quantity=2)
    remote.subscriptions[subscription["id"]] = subscription
    practice.stripe_customer_id = subscription["customer"]
    practice.stripe_subscription_id = subscription["id"]
    practice.stripe_subscription_entitlement_contract = (
        stripe_billing.CURRENT_STRIPE_ENTITLEMENT_CONTRACT_VERSION
    )
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


@pytest.mark.parametrize("remote_quantity", [0, 21])
def test_quantity_sync_repairs_remote_quantity_outside_pilot_bounds(
    remote: FakeStripe,
    remote_quantity: int,
):
    practice = _practice()
    subscription = _subscription(
        practice,
        subscription_id=f"sub_quantity_repair_{remote_quantity}",
        customer_id=f"cus_quantity_repair_{remote_quantity}",
        quantity=remote_quantity,
    )
    remote.subscriptions[subscription["id"]] = subscription
    _bind_verified_public_subscription(practice, subscription)
    practice.stripe_trial_clock_policy = stripe_billing.LEGACY_TRIAL_CLOCK_POLICY
    practice.save()

    stripe_billing.mark_quantity_sync_required(practice)

    assert stripe_billing.sync_subscription_quantity(practice)
    practice.refresh()
    assert practice.subscription_status == "active"
    assert practice.stripe_quantity_sync_required is False
    assert practice.stripe_quantity_sync_target == 1
    assert practice.stripe_quantity_sync_error is None
    assert remote.subscriptions[subscription["id"]]["items"]["data"][0]["quantity"] == 1
    quantity_updates = _quantity_updates(remote)
    assert len(quantity_updates) == 1
    assert quantity_updates[0][0]["proration_behavior"] == "none"


def test_quantity_sync_never_mutates_a_bound_subscription_with_identity_drift(
    remote: FakeStripe,
):
    practice = _practice()
    subscription = _subscription(practice, quantity=2)
    practice.stripe_customer_id = subscription["customer"]
    practice.stripe_subscription_id = subscription["id"]
    practice.stripe_subscription_entitlement_contract = (
        stripe_billing.CURRENT_STRIPE_ENTITLEMENT_CONTRACT_VERSION
    )
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
    practice.stripe_subscription_entitlement_contract = (
        stripe_billing.CURRENT_STRIPE_ENTITLEMENT_CONTRACT_VERSION
    )
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


def _add_attested_clinician(practice: Practice) -> None:
    clinician = Doctor(
        clerk_id=f"user_stripe_clinician_{secrets.token_hex(8)}",
        email=f"clinician-{secrets.token_hex(4)}@example.com",
        biography="",
    ).save()
    PracticeMember(
        practice_id=practice.id,
        doctor_id=clinician.id,
        role=PracticeRole.workforce.value,
        attested_at=NOW,
    ).save()


def _quantity_sync_practice(
    remote: FakeStripe, *, status: str, remote_quantity: int, seats: int
) -> Practice:
    practice = _practice()
    for _ in range(seats - 1):
        _add_attested_clinician(practice)
    subscription = _subscription(
        practice,
        subscription_id=f"sub_proration_{secrets.token_hex(4)}",
        customer_id=f"cus_proration_{secrets.token_hex(4)}",
        status=status,
        quantity=remote_quantity,
    )
    trial_end = None
    if status == "trialing":
        trial_end = NOW + timedelta(days=7)
        subscription["trial_start"] = int(NOW.timestamp())
        subscription["trial_end"] = int(trial_end.timestamp())
    remote.subscriptions[subscription["id"]] = subscription
    _bind_verified_public_subscription(
        practice, subscription, status=status, trial_ends_at=trial_end
    )
    practice.stripe_trial_clock_policy = stripe_billing.LEGACY_TRIAL_CLOCK_POLICY
    practice.save()
    return practice


def _quantity_updates(remote: FakeStripe) -> list[tuple[dict, str | None]]:
    return [
        (data or {}, key)
        for _method, path, data, key in remote.calls
        if path.startswith("/subscription_items/")
    ]


def _deactivate_one_clinician(practice: Practice) -> None:
    member = PracticeMember.where(
        PracticeMember.practice_id == practice.id,
        PracticeMember.role == PracticeRole.workforce.value,
        PracticeMember.seat_state == ClinicianSeatState.active.value,
    ).first()
    assert isinstance(member, PracticeMember)
    member.seat_state = ClinicianSeatState.inactive.value
    member.save()


def _sync_seats(practice: Practice) -> bool:
    stripe_billing.mark_quantity_sync_required(practice)
    return stripe_billing.sync_subscription_quantity(practice)


def _remote_item(remote: FakeStripe, practice: Practice) -> dict:
    assert practice.stripe_subscription_id
    return remote.subscriptions[practice.stripe_subscription_id]["items"]["data"][0]


def _start_period(remote: FakeStripe, practice: Practice, start: datetime) -> None:
    item = _remote_item(remote, practice)
    item["current_period_start"] = int(start.timestamp())
    item["current_period_end"] = int((start + timedelta(days=31)).timestamp())


def _posted(remote: FakeStripe) -> list[tuple[str, str]]:
    return [
        (data["quantity"], data["proration_behavior"])
        for data, _key in _quantity_updates(remote)
    ]


@pytest.mark.parametrize(
    ("status", "remote_quantity", "seats", "expected"),
    [
        ("active", 1, 2, "create_prorations"),
        ("past_due", 1, 2, "create_prorations"),
        ("unpaid", 1, 2, "create_prorations"),
        ("paused", 1, 2, "none"),
        ("incomplete", 1, 2, "none"),
        ("active", 2, 1, "none"),
        ("past_due", 2, 1, "none"),
        ("paused", 2, 1, "none"),
        ("trialing", 1, 2, "none"),
        ("trialing", 2, 1, "none"),
    ],
)
def test_quantity_sync_charges_only_mid_cycle_seat_additions(
    remote: FakeStripe,
    status: str,
    remote_quantity: int,
    seats: int,
    expected: str,
):
    practice = _quantity_sync_practice(
        remote, status=status, remote_quantity=remote_quantity, seats=seats
    )

    assert _sync_seats(practice)

    assert _posted(remote) == [(str(seats), expected)]
    practice.refresh()
    assert practice.stripe_paid_seat_high_water == max(seats, remote_quantity)
    assert practice.stripe_paid_seat_period_start == PERIOD_START


def test_portal_paused_collection_never_charges_a_seat_addition(remote: FakeStripe):
    practice = _quantity_sync_practice(
        remote, status="active", remote_quantity=1, seats=2
    )
    assert practice.stripe_subscription_id
    # A customer-portal pause keeps Stripe's top-level status "active".
    remote.subscriptions[practice.stripe_subscription_id]["pause_collection"] = {
        "behavior": "void"
    }

    stripe_billing.mark_quantity_sync_required(practice)
    stripe_billing.sync_subscription_quantity(practice)

    updates = _quantity_updates(remote)
    assert updates
    assert {data["proration_behavior"] for data, _key in updates} == {"none"}


def test_seat_swap_across_sweeps_is_not_charged_twice(remote: FakeStripe):
    practice = _quantity_sync_practice(
        remote, status="active", remote_quantity=2, seats=2
    )
    assert _sync_seats(practice)
    practice.refresh()
    assert practice.stripe_paid_seat_high_water == 2

    _deactivate_one_clinician(practice)
    assert _sync_seats(practice)
    _add_attested_clinician(practice)
    assert _sync_seats(practice)

    assert _posted(remote) == [("1", "none"), ("2", "none")]
    assert remote.prorated_seats == []


def test_seat_addition_prorates_only_above_the_period_high_water(
    remote: FakeStripe,
):
    practice = _quantity_sync_practice(
        remote, status="active", remote_quantity=1, seats=2
    )
    assert _sync_seats(practice)
    _deactivate_one_clinician(practice)
    assert _sync_seats(practice)
    _add_attested_clinician(practice)
    assert _sync_seats(practice)

    assert _posted(remote) == [
        ("2", "create_prorations"),
        ("1", "none"),
        ("2", "none"),
    ]
    assert remote.prorated_seats == [1]
    practice.refresh()
    assert practice.stripe_paid_seat_high_water == 2


def test_readding_past_the_high_water_splits_paid_and_new_seats(
    remote: FakeStripe,
):
    practice = _quantity_sync_practice(
        remote, status="active", remote_quantity=2, seats=2
    )
    assert _sync_seats(practice)
    _deactivate_one_clinician(practice)
    assert _sync_seats(practice)
    _add_attested_clinician(practice)
    _add_attested_clinician(practice)
    assert _sync_seats(practice)

    assert _posted(remote) == [("1", "none"), ("2", "none"), ("3", "create_prorations")]
    assert remote.prorated_seats == [1]
    keys = [key for _data, key in _quantity_updates(remote)]
    assert all(keys) and len(set(keys)) == len(keys)
    practice.refresh()
    assert practice.stripe_paid_seat_high_water == 3


def test_new_billing_period_resets_the_high_water_to_the_renewed_quantity(
    remote: FakeStripe,
):
    practice = _quantity_sync_practice(
        remote, status="active", remote_quantity=2, seats=2
    )
    assert _sync_seats(practice)
    _deactivate_one_clinician(practice)
    assert _sync_seats(practice)

    renewal = PERIOD_END
    _start_period(remote, practice, renewal)
    _add_attested_clinician(practice)
    assert _sync_seats(practice)

    assert _posted(remote) == [("1", "none"), ("2", "create_prorations")]
    assert remote.prorated_seats == [1]
    practice.refresh()
    assert practice.stripe_paid_seat_high_water == 2
    assert practice.stripe_paid_seat_period_start == renewal


def test_trial_seats_are_never_prorated_and_trial_end_starts_a_paid_period(
    remote: FakeStripe,
):
    practice = _quantity_sync_practice(
        remote, status="trialing", remote_quantity=1, seats=3
    )
    assert practice.stripe_subscription_id
    subscription = remote.subscriptions[practice.stripe_subscription_id]
    trial_start = datetime.fromtimestamp(subscription["trial_start"], tz=timezone.utc)
    trial_end = datetime.fromtimestamp(subscription["trial_end"], tz=timezone.utc)
    item = _remote_item(remote, practice)
    item["current_period_start"] = int(trial_start.timestamp())
    item["current_period_end"] = int(trial_end.timestamp())
    assert _sync_seats(practice)
    _deactivate_one_clinician(practice)
    assert _sync_seats(practice)
    practice.refresh()
    assert practice.stripe_paid_seat_high_water == 3

    # Stripe bills the quantity it holds at trial end for the first paid period.
    subscription["status"] = "active"
    _start_period(remote, practice, trial_end)
    _add_attested_clinician(practice)
    assert _sync_seats(practice)

    assert _posted(remote) == [
        ("3", "none"),
        ("2", "none"),
        ("3", "create_prorations"),
    ]
    assert remote.prorated_seats == [1]


def test_quantity_repair_is_never_prorated_but_counts_toward_the_high_water(
    remote: FakeStripe,
):
    practice = _practice()
    subscription = _subscription(
        practice,
        subscription_id="sub_quantity_repair_high_water",
        customer_id="cus_quantity_repair_high_water",
        quantity=0,
    )
    remote.subscriptions[subscription["id"]] = subscription
    _bind_verified_public_subscription(practice, subscription)
    practice.stripe_trial_clock_policy = stripe_billing.LEGACY_TRIAL_CLOCK_POLICY
    practice.save()

    assert _sync_seats(practice)

    assert _posted(remote) == [("1", "none")]
    practice.refresh()
    assert practice.stripe_paid_seat_high_water == 1
    assert practice.stripe_paid_seat_period_start == PERIOD_START


def test_retry_after_a_dropped_second_step_charges_the_new_seat_once(
    remote: FakeStripe,
):
    practice = _quantity_sync_practice(
        remote, status="active", remote_quantity=2, seats=2
    )
    assert _sync_seats(practice)
    _deactivate_one_clinician(practice)
    assert _sync_seats(practice)
    _add_attested_clinician(practice)
    _add_attested_clinician(practice)
    remote.dropped_quantity_update_steps = {remote.quantity_update_count + 1}

    stripe_billing.mark_quantity_sync_required(practice)
    assert not stripe_billing.sync_subscription_quantity(practice)
    practice.refresh()
    assert practice.stripe_quantity_sync_required
    assert practice.stripe_paid_seat_high_water == 2
    assert _remote_item(remote, practice)["quantity"] == 2

    assert stripe_billing.sync_subscription_quantity(practice)

    assert _posted(remote) == [
        ("1", "none"),
        ("2", "none"),
        ("3", "create_prorations"),
        ("3", "create_prorations"),
    ]
    first_try_key = _quantity_updates(remote)[2][1]
    retry_key = _quantity_updates(remote)[3][1]
    assert first_try_key != retry_key
    assert remote.prorated_seats == [1]
    practice.refresh()
    assert practice.stripe_paid_seat_high_water == 3


def test_lost_confirmation_after_a_billable_raise_is_not_charged_again(
    remote: FakeStripe, monkeypatch: pytest.MonkeyPatch
):
    practice = _quantity_sync_practice(
        remote, status="active", remote_quantity=1, seats=2
    )
    retrieve = stripe_billing._retrieve_subscription
    reads = {"count": 0}

    def lose_confirmation(subscription_id: str, **kwargs: Any) -> dict:
        reads["count"] += 1
        if remote.quantity_update_count and reads["count"] == 2:
            raise httpx.ConnectError("confirmation lost")
        return retrieve(subscription_id, **kwargs)

    monkeypatch.setattr(stripe_billing, "_retrieve_subscription", lose_confirmation)
    assert not _sync_seats(practice)
    practice.refresh()
    assert practice.stripe_paid_seat_high_water == 1

    assert stripe_billing.sync_subscription_quantity(practice)
    practice.refresh()
    assert practice.stripe_paid_seat_high_water == 2
    _deactivate_one_clinician(practice)
    assert _sync_seats(practice)
    _add_attested_clinician(practice)
    assert _sync_seats(practice)

    assert remote.prorated_seats == [1]
    assert _posted(remote)[-1] == ("2", "none")


def test_unconfirmed_target_is_never_recorded_as_paid(
    remote: FakeStripe,
):
    practice = _quantity_sync_practice(
        remote, status="active", remote_quantity=1, seats=2
    )
    remote.drop_quantity_updates = True

    assert not _sync_seats(practice)

    practice.refresh()
    assert practice.stripe_quantity_sync_error
    assert practice.stripe_paid_seat_high_water == 1
    assert practice.stripe_paid_seat_period_start == PERIOD_START


def _lose_next_confirmation(
    remote: FakeStripe, monkeypatch: pytest.MonkeyPatch
) -> Callable[[], None]:
    retrieve = stripe_billing._retrieve_subscription
    armed: dict[str, int | None] = {"after": None}

    def lose_confirmation(subscription_id: str, **kwargs: Any) -> dict:
        after = armed["after"]
        if after is not None and remote.quantity_update_count > after:
            armed["after"] = None
            raise httpx.ConnectError("confirmation lost")
        return retrieve(subscription_id, **kwargs)

    monkeypatch.setattr(stripe_billing, "_retrieve_subscription", lose_confirmation)

    def arm() -> None:
        armed["after"] = remote.quantity_update_count

    return arm


def test_two_lost_confirmations_never_charge_a_seat_twice(
    remote: FakeStripe, monkeypatch: pytest.MonkeyPatch
):
    practice = _quantity_sync_practice(
        remote, status="active", remote_quantity=2, seats=2
    )
    assert _sync_seats(practice)
    lose_next_confirmation = _lose_next_confirmation(remote, monkeypatch)

    _add_attested_clinician(practice)
    lose_next_confirmation()
    assert not _sync_seats(practice)
    _deactivate_one_clinician(practice)
    lose_next_confirmation()
    assert not _sync_seats(practice)
    practice.refresh()
    assert practice.stripe_paid_seat_high_water == 3

    _add_attested_clinician(practice)
    assert _sync_seats(practice)

    assert _posted(remote) == [
        ("3", "create_prorations"),
        ("2", "none"),
        ("3", "none"),
    ]
    assert remote.prorated_seats == [1]


def _two_step_seat_raise(remote: FakeStripe) -> Practice:
    """Leave Stripe at 1 below a paid high-water of 2 with 3 seats in Core."""

    practice = _quantity_sync_practice(
        remote, status="active", remote_quantity=2, seats=2
    )
    assert _sync_seats(practice)
    _deactivate_one_clinician(practice)
    assert _sync_seats(practice)
    _add_attested_clinician(practice)
    _add_attested_clinician(practice)
    stripe_billing.mark_quantity_sync_required(practice)
    return practice


def test_retry_after_an_out_of_band_edit_prorates_only_the_new_seat(
    remote: FakeStripe,
):
    practice = _two_step_seat_raise(remote)
    remote.dropped_quantity_update_steps = {remote.quantity_update_count + 1}
    assert not stripe_billing.sync_subscription_quantity(practice)
    assert _remote_item(remote, practice)["quantity"] == 2

    # Someone lowers the quantity in the Stripe Dashboard before the next
    # sweep retries the same obligation version.
    _remote_item(remote, practice)["quantity"] = 1
    assert stripe_billing.sync_subscription_quantity(practice)

    assert remote.prorated_seats == [1]
    assert _remote_item(remote, practice)["quantity"] == 3


def test_billable_step_requires_the_previous_step_in_stripe(
    remote: FakeStripe, monkeypatch: pytest.MonkeyPatch
):
    practice = _two_step_seat_raise(remote)
    edits = {"pending": True}

    def edit_after_restoring_paid_seats(
        method: str,
        path: str,
        data: dict | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> dict:
        result = remote(method, path, data, idempotency_key=idempotency_key)
        if path.startswith("/subscription_items/") and edits["pending"]:
            edits["pending"] = False
            _remote_item(remote, practice)["quantity"] = 1
        return result

    monkeypatch.setattr(stripe_billing, "_request", edit_after_restoring_paid_seats)

    assert not stripe_billing.sync_subscription_quantity(practice)
    assert _posted(remote)[-1] == ("2", "none")
    assert remote.prorated_seats == []
    practice.refresh()
    assert "between seat updates" in (practice.stripe_quantity_sync_error or "")

    assert stripe_billing.sync_subscription_quantity(practice)
    assert remote.prorated_seats == [1]


def test_renewal_during_a_quantity_update_fails_the_sync(
    remote: FakeStripe, monkeypatch: pytest.MonkeyPatch
):
    practice = _quantity_sync_practice(
        remote, status="active", remote_quantity=3, seats=3
    )
    assert _sync_seats(practice)
    _deactivate_one_clinician(practice)
    assert _sync_seats(practice)
    _add_attested_clinician(practice)
    renewals = {"pending": True}

    def renew_before_the_update(
        method: str,
        path: str,
        data: dict | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> dict:
        if path.startswith("/subscription_items/") and renewals["pending"]:
            renewals["pending"] = False
            _start_period(remote, practice, PERIOD_END)
        return remote(method, path, data, idempotency_key=idempotency_key)

    monkeypatch.setattr(stripe_billing, "_request", renew_before_the_update)

    assert not _sync_seats(practice)
    practice.refresh()
    assert "billing period" in (practice.stripe_quantity_sync_error or "")
    assert practice.stripe_paid_seat_period_start == PERIOD_START

    assert stripe_billing.sync_subscription_quantity(practice)
    practice.refresh()
    assert practice.stripe_paid_seat_high_water == 3
    assert practice.stripe_paid_seat_period_start == PERIOD_END


def test_missing_item_billing_period_fails_closed_before_any_update(
    remote: FakeStripe,
):
    practice = _quantity_sync_practice(
        remote, status="active", remote_quantity=1, seats=2
    )
    del _remote_item(remote, practice)["current_period_start"]

    assert not _sync_seats(practice)

    assert _quantity_updates(remote) == []
    practice.refresh()
    assert practice.stripe_quantity_sync_required
    assert "billing period" in (practice.stripe_quantity_sync_error or "")
    assert practice.stripe_paid_seat_high_water is None


def test_quantity_sync_in_flight_blocks_an_overlapping_sync(remote: FakeStripe):
    practice = _quantity_sync_practice(
        remote, status="active", remote_quantity=1, seats=2
    )

    with stripe_billing._quantity_sync_lease(practice.id) as leased:
        assert leased
        assert not _sync_seats(practice)
        with pytest.raises(stripe_billing.StripeStateConflict):
            stripe_billing.sync_subscription_quantity(practice, raise_on_error=True)
    assert _quantity_updates(remote) == []
    assert not any(
        path.startswith("/subscriptions/") for _m, path, _d, _k in remote.calls
    )

    assert stripe_billing.sync_subscription_quantity(practice)
    assert _posted(remote) == [("2", "create_prorations")]


@pytest.mark.parametrize(
    ("high_water", "period_start"),
    [(0, PERIOD_START), (21, PERIOD_START), (1, None), (None, PERIOD_START)],
)
def test_paid_seat_high_water_is_bounded_and_paired_with_its_period(
    high_water: int | None, period_start: datetime | None
):
    practice = _practice()
    with get_session() as session:
        row = session.get(Practice, practice.id)
        assert row
        row.stripe_paid_seat_high_water = high_water
        row.stripe_paid_seat_period_start = period_start
        session.add(row)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_quantity_idempotency_key_binds_the_proration_behavior(remote: FakeStripe):
    practice = _quantity_sync_practice(
        remote, status="trialing", remote_quantity=1, seats=2
    )
    stripe_billing.mark_quantity_sync_required(practice)
    practice.refresh()
    version = practice.stripe_quantity_sync_version
    remote.drop_quantity_updates = True
    assert not stripe_billing.sync_subscription_quantity(practice)
    [(uncharged, uncharged_key)] = _quantity_updates(remote)
    assert uncharged["proration_behavior"] == "none"

    # The trial ends before the retry of the same obligation. Stripe rejects a
    # reused idempotency key with different parameters, so the key must change.
    assert practice.stripe_subscription_id
    remote.subscriptions[practice.stripe_subscription_id]["status"] = "active"
    remote.drop_quantity_updates = False
    assert stripe_billing.sync_subscription_quantity(practice)
    practice.refresh()
    assert practice.stripe_quantity_sync_version == version
    [_, (charged, charged_key)] = _quantity_updates(remote)
    assert charged["quantity"] == uncharged["quantity"] == "2"
    assert charged["proration_behavior"] == "create_prorations"
    assert uncharged_key and charged_key and uncharged_key != charged_key


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
