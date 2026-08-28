from __future__ import annotations

import importlib.util
import json
import sys
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "stripe_checkout_activation_preflight.py"
NOW = datetime(2026, 8, 24, 18, 0, tzinfo=UTC)
PRACTICE_ID = "practice_01arz3ndektsv4rrffq69g5fav"
PRODUCT_ID = "prod_cosmaneura_paid_pilot"
PORTAL_CONFIGURATION_ID = "bpc_cosmaneura_paid_pilot"
WEBHOOK_URL = "https://app.example.test/external/v1/webhooks/stripe"
WEBHOOK_ID = "we_sensitive_endpoint_identifier"
SOURCE_SHA = "a" * 40
EVIDENCE_SHA256 = "b" * 64
EXPECTED_WEBHOOK_EVENTS = [
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "customer.subscription.paused",
    "customer.subscription.resumed",
]


def _load_preflight() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "standalone_stripe_checkout_activation_preflight", SCRIPT
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _session(
    session_id: str,
    *,
    policy: str = "if_required",
    subscription: str | None = None,
    price_id: str = "price_test_x",
    reference: str | None = PRACTICE_ID,
    live_mode: bool = False,
) -> dict[str, Any]:
    created = int((NOW - timedelta(hours=2)).timestamp())
    return {
        "id": session_id,
        "client_reference_id": reference,
        "created": created,
        "expires_at": created + 24 * 60 * 60,
        "line_items": {
            "data": [
                {
                    "price": {"id": price_id},
                    "quantity": 1,
                }
            ]
        },
        "livemode": live_mode,
        "mode": "subscription",
        "payment_method_collection": policy,
        "status": "open",
        "subscription": subscription,
        "url": f"https://checkout.stripe.com/c/pay/{session_id}",
    }


class FakeStripe:
    def __init__(
        self,
        sessions: list[dict[str, Any]],
        *,
        live_mode: bool = False,
        returned_account_id: str = "acct_test_cosmaneura",
        product_overrides: dict[str, Any] | None = None,
        price_overrides: dict[str, dict[str, Any]] | None = None,
        account_price_ids: tuple[str, ...] = ("price_test_x",),
        webhook_endpoints: list[dict[str, Any]] | None = None,
        payment_links: list[dict[str, Any]] | None = None,
        account_overrides: dict[str, Any] | None = None,
        portal_configuration_overrides: dict[str, Any] | None = None,
    ) -> None:
        self.sessions = {str(session["id"]): deepcopy(session) for session in sessions}
        self.live_mode = live_mode
        self.returned_account_id = returned_account_id
        self.account_overrides = account_overrides or {}
        self.expected_secret = (
            "sk_live_not_printed" if live_mode else "sk_test_not_printed"
        )
        self.product_overrides = product_overrides or {}
        self.price_overrides = price_overrides or {}
        self.account_price_ids = account_price_ids
        self.webhook_endpoints = (
            webhook_endpoints
            if webhook_endpoints is not None
            else [
                {
                    "id": WEBHOOK_ID,
                    "api_version": "2025-09-30.clover",
                    "enabled_events": EXPECTED_WEBHOOK_EVENTS,
                    "livemode": live_mode,
                    "status": "enabled",
                    "url": WEBHOOK_URL,
                }
            ]
        )
        self.payment_links = deepcopy(payment_links or [])
        self.portal_configuration = {
            "id": PORTAL_CONFIGURATION_ID,
            "active": True,
            "livemode": live_mode,
            "features": {
                "customer_update": {"enabled": False},
                "payment_method_update": {"enabled": True},
                "subscription_cancel": {
                    "enabled": True,
                    "mode": "at_period_end",
                },
                "subscription_pause": {"enabled": False},
                "subscription_update": {"enabled": False},
            },
        }
        for key, value in (portal_configuration_overrides or {}).items():
            if key == "features" and isinstance(value, dict):
                self.portal_configuration["features"].update(value)
            else:
                self.portal_configuration[key] = value
        self.calls: list[tuple[str, str, list[tuple[str, str]] | None, str | None]] = []

    def _price(self, price_id: str) -> dict[str, Any]:
        price = {
            "id": price_id,
            "livemode": self.live_mode,
            "active": price_id == "price_test_x",
            "billing_scheme": "per_unit",
            "currency": "usd",
            "product": PRODUCT_ID,
            "recurring": {
                "interval": "month",
                "interval_count": 1,
                "usage_type": "licensed",
            },
            "transform_quantity": None,
            "type": "recurring",
            "unit_amount": 8_900,
        }
        price.update(self.price_overrides.get(price_id, {}))
        return price

    def __call__(
        self,
        secret_key: str,
        method: str,
        path: str,
        params: list[tuple[str, str]] | None,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        assert secret_key == self.expected_secret
        self.calls.append((method, path, params, idempotency_key))
        if path == "/account":
            account = {
                "id": self.returned_account_id,
                "charges_enabled": True,
                "details_submitted": True,
                "payouts_enabled": True,
                "requirements": {
                    "currently_due": [],
                    "disabled_reason": None,
                    "past_due": [],
                },
            }
            account.update(self.account_overrides)
            return account
        if path.startswith("/products/"):
            product_id = path.rsplit("/", 1)[-1]
            product = {
                "id": product_id,
                "active": True,
                "default_price": "price_test_x",
                "livemode": self.live_mode,
            }
            product.update(self.product_overrides)
            return product
        if path.startswith("/prices/"):
            price_id = path.rsplit("/", 1)[-1]
            return self._price(price_id)
        if path == "/prices":
            prices = [self._price(price_id) for price_id in self.account_price_ids]
            active = dict(params or []).get("active")
            if active in {"true", "false"}:
                expected_active = active == "true"
                prices = [
                    price for price in prices if price["active"] is expected_active
                ]
            starting_after = dict(params or []).get("starting_after")
            if starting_after:
                index = next(
                    index
                    for index, price in enumerate(prices)
                    if price["id"] == starting_after
                )
                prices = prices[index + 1 :]
            return {"data": prices[:1], "has_more": len(prices) > 1}
        if path == "/webhook_endpoints":
            endpoints = deepcopy(self.webhook_endpoints)
            starting_after = dict(params or []).get("starting_after")
            if starting_after:
                index = next(
                    index
                    for index, endpoint in enumerate(endpoints)
                    if endpoint["id"] == starting_after
                )
                endpoints = endpoints[index + 1 :]
            return {"data": endpoints[:1], "has_more": len(endpoints) > 1}
        if path == f"/billing_portal/configurations/{PORTAL_CONFIGURATION_ID}":
            return deepcopy(self.portal_configuration)
        if path == "/checkout/sessions":
            status = dict(params or []).get("status")
            matching_sessions = [
                deepcopy(session)
                for session in self.sessions.values()
                if session["status"] == status
            ]
            starting_after = dict(params or []).get("starting_after")
            if starting_after:
                index = next(
                    index
                    for index, session in enumerate(matching_sessions)
                    if session["id"] == starting_after
                )
                matching_sessions = matching_sessions[index + 1 :]
            return {
                "data": matching_sessions[:1],
                "has_more": len(matching_sessions) > 1,
            }
        if path == "/payment_links":
            links = [link for link in deepcopy(self.payment_links) if link["active"]]
            starting_after = dict(params or []).get("starting_after")
            if starting_after:
                index = next(
                    index
                    for index, link in enumerate(links)
                    if link["id"] == starting_after
                )
                links = links[index + 1 :]
            return {"data": links[:1], "has_more": len(links) > 1}
        if path.startswith("/checkout/sessions/") and path.endswith("/expire"):
            session_id = path.removesuffix("/expire").rsplit("/", 1)[-1]
            session = self.sessions[session_id]
            session["status"] = "expired"
            session["url"] = None
            return deepcopy(session)
        raise AssertionError(f"unexpected request: {method} {path}")


def _common(*, live_mode: bool = False) -> dict[str, Any]:
    return {
        "secret_key": ("sk_live_not_printed" if live_mode else "sk_test_not_printed"),
        "account_id": "acct_test_cosmaneura",
        "product_id": PRODUCT_ID,
        "portal_configuration_id": PORTAL_CONFIGURATION_ID,
        "price_id": "price_test_x",
        "legacy_price_ids": (),
        "live_mode": live_mode,
        "operator_ref": "CHANGE-123",
        "drain_started_at": NOW - timedelta(hours=25),
        "checked_at": NOW,
        "acquisition_disabled_confirmed": True,
        "old_code_stopped_confirmed": True,
        "price_inventory_confirmed": True,
        "webhook_endpoint_url": WEBHOOK_URL,
        "secret_configuration_confirmed": True,
        "completed_session_reconciliation_confirmed": True,
        "completed_session_reconciliation_at": NOW,
        "completed_session_reconciliation_ref": "CHANGE-COMPLETED-789",
        "completed_session_reconciliation_sha256": EVIDENCE_SHA256,
        "test_mode_lifecycle_confirmed": True,
        "test_mode_lifecycle_evidence_ref": "CHANGE-TEST-LIFECYCLE-234",
        "test_mode_lifecycle_evidence_sha256": EVIDENCE_SHA256,
        "observability_ready_confirmed": True,
        "observability_evidence_ref": "CHANGE-OBSERVABILITY-345",
        "observability_evidence_sha256": EVIDENCE_SHA256,
        "founder_owner_approval_confirmed": True,
        "owner_approval_ref": "APPROVAL-123",
        "pending_credential_trial_expiry_policy": "hold_conversion",
        "policy_implementation_evidence_ref": "CHANGE-IMPLEMENTATION-456",
        "reviewed_source_sha": SOURCE_SHA,
        "deployed_source_sha": SOURCE_SHA,
    }


def test_preflight_fully_paginates_and_classifies_without_exposing_ids() -> None:
    preflight = _load_preflight()
    legacy = _session("cs_legacy")
    current = _session("cs_current", policy="always")
    direct_legacy = _session("cs_direct_legacy", reference=None)
    direct_legacy_unusual_reference = _session(
        "cs_direct_legacy_unusual_reference",
        reference="retired-website-checkout",
        price_id="price_legacy_website",
    )
    unrelated = _session(
        "cs_unrelated",
        reference="not-a-cosmaneura-practice-reference",
        price_id="price_unrelated",
    )
    fake = FakeStripe(
        [legacy, current, direct_legacy, direct_legacy_unusual_reference, unrelated],
        account_price_ids=("price_test_x", "price_legacy_website"),
    )
    common = _common()
    common["legacy_price_ids"] = ("price_legacy_website",)

    evidence = preflight.run_preflight(
        fake,
        **common,
        apply=False,
        confirmed_plan_sha256=None,
    )

    assert evidence["pages_scanned"] == 5
    assert evidence["open_sessions_scanned"] == 5
    assert evidence["eligible_cosmaneura_open_sessions"] == 4
    assert evidence["legacy_cosmaneura_open_sessions"] == 3
    assert evidence["ambiguous_cosmaneura_open_sessions"] == 0
    assert evidence["drain_satisfied"] is True
    assert evidence["ready_for_acquisition"] is False
    assert evidence["stripe_account_id"] == "acct_test_cosmaneura"
    assert evidence["checkout_price_ids"] == (
        "price_test_x",
        "price_legacy_website",
    )
    assert evidence["drain_started_at"] == (NOW - timedelta(hours=25)).isoformat()
    assert evidence["acquisition_disabled_confirmed"] is True
    assert evidence["old_code_stopped_confirmed"] is True
    assert evidence["price_inventory_confirmed"] is True
    assert evidence["current_offer"] == {
        "billing_scheme": "per_unit",
        "currency": "usd",
        "interval": "month",
        "interval_count": 1,
        "unit_amount": 8_900,
        "usage_type": "licensed",
    }
    assert evidence["activation_policy"] == "paid-pilot-v2"
    assert evidence["checkout_policy"] == "paid-pilot-v2"
    assert evidence["entitlement_contract"] == {
        "policy_revision": "2026-08-27",
        "schema_sha256": (
            "a33ba095ea284575fc8004ecc7b2e05b6aa60c911f5c03add3a0ea1f7ef9e578"
        ),
        "version": "public-capability-entitlements.v1",
    }
    assert evidence["retired_price_ids"] == ("price_legacy_website",)
    assert evidence["price_inventory_pages_scanned"] == 2
    assert evidence["product_price_count"] == 2
    assert evidence["stripe_product_verified"] is True
    assert evidence["completed_session_reconciliation_confirmed"] is True
    assert evidence["completed_session_reconciliation_ref"] == ("CHANGE-COMPLETED-789")
    assert evidence["webhook_endpoint_verified"] is True
    assert evidence["webhook_event_count"] == len(EXPECTED_WEBHOOK_EVENTS)
    serialized = json.dumps(evidence)
    assert "cs_legacy" not in serialized
    assert PRACTICE_ID not in serialized
    assert WEBHOOK_URL not in serialized
    assert WEBHOOK_ID not in serialized


def test_apply_requires_exact_plan_then_expires_and_rescans_all_open_sessions() -> None:
    preflight = _load_preflight()
    fake = FakeStripe(
        [
            _session("cs_legacy", live_mode=True),
            _session("cs_current", policy="always", live_mode=True),
        ],
        live_mode=True,
    )
    common = _common(live_mode=True)
    dry_run = preflight.run_preflight(
        fake,
        **common,
        apply=False,
        confirmed_plan_sha256=None,
    )

    with pytest.raises(preflight.PreflightError, match="plan digest"):
        preflight.run_preflight(
            fake,
            **common,
            apply=True,
            confirmed_plan_sha256="0" * 64,
        )

    applied = preflight.run_preflight(
        fake,
        **common,
        apply=True,
        confirmed_plan_sha256=dry_run["plan_sha256"],
    )

    assert applied["applied"] is True
    assert applied["expired_sessions"] == 2
    assert applied["applied_plan_sha256"] == dry_run["plan_sha256"]
    assert applied["eligible_cosmaneura_open_sessions"] == 0
    assert applied["ambiguous_cosmaneura_open_sessions"] == 0
    assert applied["ready_for_acquisition"] is True
    expire_calls = [call for call in fake.calls if call[1].endswith("/expire")]
    assert len(expire_calls) == 2
    assert all(
        call[3] and call[3].startswith("cosmas-activation-expire-")
        for call in expire_calls
    )


def test_attached_subscription_is_ambiguous_and_never_expired() -> None:
    preflight = _load_preflight()
    fake = FakeStripe([_session("cs_ambiguous", subscription="sub_incomplete")])
    common = _common()
    dry_run = preflight.run_preflight(
        fake,
        **common,
        apply=False,
        confirmed_plan_sha256=None,
    )

    assert dry_run["ambiguous_cosmaneura_open_sessions"] == 1
    assert dry_run["ready_for_acquisition"] is False
    with pytest.raises(preflight.PreflightError, match="require review"):
        preflight.run_preflight(
            fake,
            **common,
            apply=True,
            confirmed_plan_sha256=dry_run["plan_sha256"],
        )
    assert not any(call[1].endswith("/expire") for call in fake.calls)


@pytest.mark.parametrize(
    "line_items",
    [
        {
            "data": [
                {"price": {"id": "price_test_x"}, "quantity": 1},
                {"price": {"id": "price_unrelated"}, "quantity": 1},
            ]
        },
        {"data": [{"price": {"id": "price_test_x"}, "quantity": 0}]},
    ],
)
def test_direct_inventoried_session_with_unsafe_shape_is_ambiguous(
    line_items: dict[str, Any],
) -> None:
    preflight = _load_preflight()
    session = _session("cs_unsafe_direct", reference=None)
    session["line_items"] = line_items

    evidence = preflight.run_preflight(
        FakeStripe([session]),
        **_common(),
        apply=False,
        confirmed_plan_sha256=None,
    )

    assert evidence["eligible_cosmaneura_open_sessions"] == 0
    assert evidence["ambiguous_cosmaneura_open_sessions"] == 1
    assert evidence["ready_for_acquisition"] is False


@pytest.mark.parametrize("session_status", ["open", "expired"])
def test_unattributed_session_with_unexpanded_line_items_is_ambiguous(
    session_status: str,
) -> None:
    preflight = _load_preflight()
    session = _session("cs_unknown_shape", reference=None)
    session["status"] = session_status
    session["line_items"] = {"url": "/v1/checkout/sessions/cs_unknown_shape/line_items"}

    evidence = preflight.run_preflight(
        FakeStripe([session]),
        **_common(),
        apply=False,
        confirmed_plan_sha256=None,
    )

    key = (
        "ambiguous_cosmaneura_open_sessions"
        if session_status == "open"
        else "ambiguous_expired_cosmaneura_sessions"
    )
    assert evidence[key] == 1
    assert evidence["ready_for_acquisition"] is False


def test_recovery_enabled_open_and_expired_sessions_block_activation() -> None:
    preflight = _load_preflight()
    open_session = _session("cs_open_recovery", reference=None)
    open_session["after_expiration"] = {"recovery": {"enabled": True}}
    expired_session = _session("cs_expired_recovery", reference=None)
    expired_session["status"] = "expired"
    expired_session["after_expiration"] = {
        "recovery": {
            "enabled": True,
            "expires_at": int((NOW + timedelta(hours=1)).timestamp()),
            "url": "https://checkout.stripe.com/c/pay/recovery-sensitive",
        }
    }

    evidence = preflight.run_preflight(
        FakeStripe([open_session, expired_session]),
        **_common(),
        apply=False,
        confirmed_plan_sha256=None,
    )

    assert evidence["ambiguous_cosmaneura_open_sessions"] == 1
    assert evidence["recoverable_expired_cosmaneura_sessions"] == 1
    assert evidence["ready_for_acquisition"] is False
    assert "recovery-sensitive" not in json.dumps(evidence)


def test_active_paid_pilot_payment_link_blocks_activation() -> None:
    preflight = _load_preflight()
    payment_link = {
        "id": "plink_sensitive_identifier",
        "active": True,
        "line_items": {"data": [{"price": {"id": "price_test_x"}, "quantity": 1}]},
        "url": "https://buy.stripe.com/sensitive",
    }

    evidence = preflight.run_preflight(
        FakeStripe([], live_mode=True, payment_links=[payment_link]),
        **_common(live_mode=True),
        apply=False,
        confirmed_plan_sha256=None,
    )

    assert evidence["active_paid_pilot_payment_links"] == 1
    assert evidence["ready_for_acquisition"] is False
    assert "plink_sensitive" not in json.dumps(evidence)


def test_zero_open_sessions_still_requires_full_maximum_lifetime_drain() -> None:
    preflight = _load_preflight()
    fake = FakeStripe([])

    evidence = preflight.run_preflight(
        fake,
        **{
            **_common(),
            "drain_started_at": NOW - timedelta(hours=23),
        },
        apply=False,
        confirmed_plan_sha256=None,
    )

    assert evidence["eligible_cosmaneura_open_sessions"] == 0
    assert evidence["drain_satisfied"] is False
    assert evidence["ready_for_acquisition"] is False


def test_apply_cannot_expire_sessions_before_full_drain() -> None:
    preflight = _load_preflight()
    fake = FakeStripe([_session("cs_pre_drain")])
    common = {
        **_common(),
        "drain_started_at": NOW - timedelta(hours=23),
    }
    dry_run = preflight.run_preflight(
        fake,
        **common,
        apply=False,
        confirmed_plan_sha256=None,
    )

    with pytest.raises(preflight.PreflightError, match="full Checkout Session drain"):
        preflight.run_preflight(
            fake,
            **common,
            apply=True,
            confirmed_plan_sha256=dry_run["plan_sha256"],
        )

    assert not any(call[1].endswith("/expire") for call in fake.calls)


@pytest.mark.parametrize(
    ("override", "failed_prerequisite"),
    [
        ({"secret_configuration_confirmed": False}, "secret_configuration_confirmed"),
        (
            {"founder_owner_approval_confirmed": False},
            "founder_owner_approval_recorded",
        ),
        ({"owner_approval_ref": ""}, "founder_owner_approval_recorded"),
        (
            {"pending_credential_trial_expiry_policy": None},
            "pending_credential_trial_expiry_policy_resolved",
        ),
        (
            {"policy_implementation_evidence_ref": ""},
            "pending_credential_trial_expiry_policy_implemented",
        ),
        ({"deployed_source_sha": "b" * 40}, "reviewed_source_matches_deployed"),
        ({"acquisition_disabled_confirmed": False}, "acquisition_disabled_confirmed"),
        ({"old_code_stopped_confirmed": False}, "card_required_code_confirmed"),
        ({"price_inventory_confirmed": False}, "price_inventory_confirmed"),
        (
            {"completed_session_reconciliation_confirmed": False},
            "completed_session_reconciliation_recorded",
        ),
        (
            {"completed_session_reconciliation_ref": ""},
            "completed_session_reconciliation_recorded",
        ),
        (
            {"completed_session_reconciliation_at": NOW - timedelta(hours=26)},
            "completed_session_reconciliation_recorded",
        ),
        (
            {"completed_session_reconciliation_sha256": ""},
            "completed_session_reconciliation_recorded",
        ),
        ({"test_mode_lifecycle_confirmed": False}, "test_mode_lifecycle_evidenced"),
        (
            {"test_mode_lifecycle_evidence_ref": ""},
            "test_mode_lifecycle_evidenced",
        ),
        (
            {"test_mode_lifecycle_evidence_sha256": ""},
            "test_mode_lifecycle_evidenced",
        ),
        (
            {"observability_ready_confirmed": False},
            "observability_readiness_evidenced",
        ),
        (
            {"observability_evidence_ref": ""},
            "observability_readiness_evidenced",
        ),
        (
            {"observability_evidence_sha256": ""},
            "observability_readiness_evidenced",
        ),
    ],
)
def test_missing_explicit_prerequisite_keeps_activation_not_ready(
    override: dict[str, Any], failed_prerequisite: str
) -> None:
    preflight = _load_preflight()
    fake = FakeStripe([], live_mode=True)
    common = {**_common(live_mode=True), **override}

    evidence = preflight.run_preflight(
        fake,
        **common,
        apply=False,
        confirmed_plan_sha256=None,
    )

    assert evidence["activation_prerequisites"][failed_prerequisite] is False
    assert evidence["ready_for_acquisition"] is False


@pytest.mark.parametrize(
    "policy", ["charge_scheduled", "extend_trial", "hold_conversion"]
)
def test_each_explicit_owner_policy_can_satisfy_the_policy_gate(policy: str) -> None:
    preflight = _load_preflight()
    fake = FakeStripe([], live_mode=True)
    common = {
        **_common(live_mode=True),
        "pending_credential_trial_expiry_policy": policy,
    }

    evidence = preflight.run_preflight(
        fake,
        **common,
        apply=False,
        confirmed_plan_sha256=None,
    )

    assert evidence["pending_credential_trial_expiry_policy"] == policy
    assert evidence["ready_for_acquisition"] is True


def test_omitted_policy_is_the_parser_default_and_never_inferred() -> None:
    preflight = _load_preflight()

    args = preflight._parser().parse_args(
        [
            "--operator-ref",
            "CHANGE-123",
            "--drain-started-at",
            "2026-08-24T12:00:00Z",
            "--expected-mode",
            "live",
        ]
    )

    assert args.pending_credential_trial_expiry_policy is None


def test_unknown_pending_credential_policy_fails_before_stripe_calls() -> None:
    preflight = _load_preflight()
    fake = FakeStripe([], live_mode=True)
    common = {
        **_common(live_mode=True),
        "pending_credential_trial_expiry_policy": "invented_policy",
    }

    with pytest.raises(preflight.PreflightError, match="not an allowed value"):
        preflight.run_preflight(
            fake,
            **common,
            apply=False,
            confirmed_plan_sha256=None,
        )

    assert fake.calls == []


@pytest.mark.parametrize(
    "price_override",
    [
        {"unit_amount": 8_800},
        {"currency": "eur"},
        {
            "recurring": {
                "interval": "year",
                "interval_count": 1,
                "usage_type": "licensed",
            }
        },
        {
            "recurring": {
                "interval": "month",
                "interval_count": 1,
                "usage_type": "metered",
            }
        },
        {"billing_scheme": "tiered"},
        {"active": False},
    ],
)
def test_current_price_must_exactly_match_the_89_dollar_licensed_monthly_offer(
    price_override: dict[str, Any],
) -> None:
    preflight = _load_preflight()
    fake = FakeStripe(
        [],
        live_mode=True,
        price_overrides={"price_test_x": price_override},
    )

    with pytest.raises(preflight.PreflightError, match="paid-pilot offer"):
        preflight.run_preflight(
            fake,
            **_common(live_mode=True),
            apply=False,
            confirmed_plan_sha256=None,
        )


def test_every_retired_price_must_be_exactly_inventoried_and_inactive() -> None:
    preflight = _load_preflight()
    fake = FakeStripe(
        [],
        live_mode=True,
        price_overrides={"price_retired": {"active": True}},
        account_price_ids=("price_test_x", "price_retired"),
    )
    common = {**_common(live_mode=True), "legacy_price_ids": ("price_retired",)}

    with pytest.raises(preflight.PreflightError, match="Retired Stripe price"):
        preflight.run_preflight(
            fake,
            **common,
            apply=False,
            confirmed_plan_sha256=None,
        )


def test_retired_price_may_belong_to_a_separately_inventoried_product() -> None:
    preflight = _load_preflight()
    fake = FakeStripe(
        [],
        live_mode=True,
        price_overrides={
            "price_retired": {
                "active": False,
                "product": "prod_retired_website",
            }
        },
        account_price_ids=("price_test_x", "price_retired"),
    )
    common = {**_common(live_mode=True), "legacy_price_ids": ("price_retired",)}

    evidence = preflight.run_preflight(
        fake,
        **common,
        apply=False,
        confirmed_plan_sha256=None,
    )

    assert evidence["products_verified"] == 2
    assert evidence["product_price_count"] == 2


def test_account_wide_price_scan_rejects_an_uninventoried_product_price() -> None:
    preflight = _load_preflight()
    fake = FakeStripe(
        [],
        live_mode=True,
        account_price_ids=("price_test_x", "price_uninventoried"),
    )

    with pytest.raises(preflight.PreflightError, match="inventory is incomplete"):
        preflight.run_preflight(
            fake,
            **_common(live_mode=True),
            apply=False,
            confirmed_plan_sha256=None,
        )

    inventory_calls = [call for call in fake.calls if call[1] == "/prices"]
    assert len(inventory_calls) == 2


@pytest.mark.parametrize(
    "product_override",
    [
        {"active": False},
        {"default_price": "price_different"},
        {"livemode": False},
    ],
)
def test_product_must_match_the_exact_active_paid_pilot_product(
    product_override: dict[str, Any],
) -> None:
    preflight = _load_preflight()
    fake = FakeStripe(
        [],
        live_mode=True,
        product_overrides=product_override,
    )

    with pytest.raises(preflight.PreflightError, match="Stripe product"):
        preflight.run_preflight(
            fake,
            **_common(live_mode=True),
            apply=False,
            confirmed_plan_sha256=None,
        )


@pytest.mark.parametrize(
    "endpoint_override",
    [
        {"status": "disabled"},
        {"livemode": False},
        {"api_version": "2024-06-20"},
        {"enabled_events": [*EXPECTED_WEBHOOK_EVENTS, "invoice.finalized"]},
        {"enabled_events": EXPECTED_WEBHOOK_EVENTS[:-1]},
    ],
)
def test_webhook_must_be_active_in_expected_mode_with_exact_event_coverage(
    endpoint_override: dict[str, Any],
) -> None:
    preflight = _load_preflight()
    endpoint = {
        "id": WEBHOOK_ID,
        "api_version": "2025-09-30.clover",
        "enabled_events": EXPECTED_WEBHOOK_EVENTS,
        "livemode": True,
        "status": "enabled",
        "url": WEBHOOK_URL,
        **endpoint_override,
    }
    fake = FakeStripe([], live_mode=True, webhook_endpoints=[endpoint])

    with pytest.raises(preflight.PreflightError, match="webhook endpoint"):
        preflight.run_preflight(
            fake,
            **_common(live_mode=True),
            apply=False,
            confirmed_plan_sha256=None,
        )


def test_expected_webhook_url_must_match_exactly_once() -> None:
    preflight = _load_preflight()
    endpoint = {
        "id": WEBHOOK_ID,
        "api_version": "2025-09-30.clover",
        "enabled_events": EXPECTED_WEBHOOK_EVENTS,
        "livemode": True,
        "status": "enabled",
        "url": "https://different.example.test/external/v1/webhooks/stripe",
    }
    fake = FakeStripe([], live_mode=True, webhook_endpoints=[endpoint])

    with pytest.raises(preflight.PreflightError, match="uniquely configured"):
        preflight.run_preflight(
            fake,
            **_common(live_mode=True),
            apply=False,
            confirmed_plan_sha256=None,
        )


def test_secret_key_mode_must_match_the_explicit_expected_mode() -> None:
    preflight = _load_preflight()
    fake = FakeStripe([], live_mode=True)
    common = {**_common(live_mode=True), "secret_key": "sk_test_not_printed"}

    with pytest.raises(preflight.PreflightError, match="mode"):
        preflight.run_preflight(
            fake,
            **common,
            apply=False,
            confirmed_plan_sha256=None,
        )

    assert fake.calls == []


def test_account_must_match_the_exact_expected_account() -> None:
    preflight = _load_preflight()
    fake = FakeStripe(
        [],
        live_mode=True,
        returned_account_id="acct_different",
    )

    with pytest.raises(preflight.PreflightError, match="wrong account"):
        preflight.run_preflight(
            fake,
            **_common(live_mode=True),
            apply=False,
            confirmed_plan_sha256=None,
        )


@pytest.mark.parametrize(
    "account_overrides",
    [
        {"charges_enabled": False},
        {"payouts_enabled": False},
        {"details_submitted": False},
        {"requirements": {"disabled_reason": "requirements.past_due"}},
        {"requirements": {"disabled_reason": None, "currently_due": ["tos"]}},
    ],
)
def test_live_account_must_be_ready_for_billing(
    account_overrides: dict[str, Any],
) -> None:
    preflight = _load_preflight()
    fake = FakeStripe([], live_mode=True, account_overrides=account_overrides)

    with pytest.raises(preflight.PreflightError, match="not ready for live billing"):
        preflight.run_preflight(
            fake,
            **_common(live_mode=True),
            apply=False,
            confirmed_plan_sha256=None,
        )


@pytest.mark.parametrize(
    "feature_override",
    [
        {"payment_method_update": {"enabled": False}},
        {"subscription_cancel": {"enabled": True, "mode": "immediately"}},
        {"subscription_pause": {"enabled": True}},
        {"subscription_update": {"enabled": True}},
        {"customer_update": {"enabled": True}},
    ],
)
def test_portal_configuration_rejects_unsafe_feature_policy(
    feature_override: dict[str, Any],
) -> None:
    preflight = _load_preflight()
    fake = FakeStripe(
        [],
        live_mode=True,
        portal_configuration_overrides={"features": feature_override},
    )

    with pytest.raises(preflight.PreflightError, match="restricted to safe controls"):
        preflight.run_preflight(
            fake,
            **_common(live_mode=True),
            apply=False,
            confirmed_plan_sha256=None,
        )


def test_main_wires_documented_environment_and_activation_arguments(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    preflight = _load_preflight()
    fake = FakeStripe([], live_mode=True)
    monkeypatch.setattr(preflight, "_stripe_request", fake)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_not_printed")
    monkeypatch.setenv("STRIPE_ACCOUNT_ID", "acct_test_cosmaneura")
    monkeypatch.setenv("STRIPE_PRODUCT_ID", PRODUCT_ID)
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_test_x")
    monkeypatch.setenv("STRIPE_PORTAL_CONFIGURATION_ID", PORTAL_CONFIGURATION_ID)
    monkeypatch.setenv("STRIPE_RETIRED_PRICE_IDS", "")
    monkeypatch.delenv("STRIPE_LEGACY_PRICE_IDS", raising=False)
    monkeypatch.setenv("STRIPE_WEBHOOK_ENDPOINT_URL", WEBHOOK_URL)

    result = preflight.main(
        [
            "--operator-ref",
            "CHANGE-123",
            "--drain-started-at",
            "2026-08-24T12:00:00Z",
            "--expected-mode",
            "live",
            "--confirm-acquisition-disabled",
            "--confirm-old-code-stopped",
            "--confirm-price-inventory-complete",
            "--confirm-secret-configuration",
            "--confirm-completed-session-reconciliation",
            "--completed-session-reconciliation-at",
            "2026-08-26T12:00:00Z",
            "--completed-session-reconciliation-ref",
            "CHANGE-COMPLETED-789",
            "--completed-session-reconciliation-sha256",
            EVIDENCE_SHA256,
            "--confirm-test-mode-lifecycle",
            "--test-mode-lifecycle-evidence-ref",
            "CHANGE-TEST-LIFECYCLE-234",
            "--test-mode-lifecycle-evidence-sha256",
            EVIDENCE_SHA256,
            "--confirm-observability-ready",
            "--observability-evidence-ref",
            "CHANGE-OBSERVABILITY-345",
            "--observability-evidence-sha256",
            EVIDENCE_SHA256,
            "--confirm-founder-owner-approval",
            "--owner-approval-ref",
            "APPROVAL-123",
            "--pending-credential-trial-expiry-policy",
            "hold_conversion",
            "--policy-implementation-evidence-ref",
            "CHANGE-IMPLEMENTATION-456",
            "--reviewed-source-sha",
            SOURCE_SHA,
            "--deployed-source-sha",
            SOURCE_SHA,
        ]
    )

    evidence = json.loads(capsys.readouterr().out)
    assert result == 0
    assert evidence["ready_for_acquisition"] is True
    assert evidence["stripe_product_id"] == PRODUCT_ID
    assert evidence["stripe_portal_configuration_id"] == PORTAL_CONFIGURATION_ID
    assert WEBHOOK_URL not in json.dumps(evidence)
    assert WEBHOOK_ID not in json.dumps(evidence)
