"""Synthetic Stripe inventories only; no app imports, credentials or network."""

import copy
import json
import urllib.error
from unittest.mock import MagicMock, Mock

import pytest

from scripts import stripe_configuration_preflight as audit


def fixture():
    portal = {
        "id": "bpc_pinned",
        "livemode": False,
        "active": True,
        "is_default": True,
        "login_page": {"enabled": False},
        "features": {
            "customer_update": {"enabled": False},
            "subscription_update": {"enabled": False},
            "subscription_cancel": {
                "enabled": True,
                "mode": "at_period_end",
                "proration_behavior": "none",
            },
            "invoice_history": {"enabled": True},
            "payment_method_update": {"enabled": True},
        },
    }
    endpoint = {
        "id": "we_owned",
        "livemode": False,
        "status": "enabled",
        "url": "https://staging-app.cosmaneura.com/external/v1/webhooks/stripe",
        "api_version": "2025-09-30.clover",
        "enabled_events": sorted(audit.REQUIRED_EVENTS),
    }
    return {
        "/account": {"id": "acct_owned"},
        "/prices/price_owned": {
            "id": "price_owned",
            "livemode": False,
            "active": True,
            "currency": "usd",
            "unit_amount": 8900,
            "type": "recurring",
            "billing_scheme": "per_unit",
            "transform_quantity": None,
            "recurring": {
                "interval": "month",
                "interval_count": 1,
                "usage_type": "licensed",
            },
        },
        "/billing_portal/configurations/bpc_pinned": portal,
        "/billing_portal/configurations": {
            "data": [copy.deepcopy(portal)],
            "has_more": False,
        },
        "/webhook_endpoints": {"data": [endpoint], "has_more": False},
    }


def run(values):
    return audit.audit(
        lambda path, params: values[path],
        account="acct_owned",
        price="price_owned",
        portal="bpc_pinned",
        live=False,
    )


def test_verified_config_is_not_payment_or_activation_admission():
    report = run(fixture())
    assert report["configuration_verified"] is True
    assert report["read_only"] is True
    assert {
        "payment_acceptance",
        "checkout_drain",
        "worker_reconciliation",
        "portal_email_links_disabled",
    } <= set(report["not_verified"])
    encoded = json.dumps(report)
    assert all(
        value not in encoded
        for value in ("acct_owned", "price_owned", "bpc_pinned", "we_owned")
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("currency", "eur"),
        ("unit_amount", 6000),
        ("active", False),
        ("livemode", True),
        ("type", "one_time"),
        ("billing_scheme", "tiered"),
        ("transform_quantity", {"divide_by": 10}),
    ],
)
def test_price_drift_blocks_report(key, value):
    values = fixture()
    values["/prices/price_owned"][key] = value
    assert "price_contract_mismatch" in run(values)["failures"]


@pytest.mark.parametrize(
    "feature",
    ["customer_update", "subscription_update", "subscription_pause", "future_feature"],
)
def test_seat_bypasses_and_unknown_portal_features_fail(feature):
    values = fixture()
    values["/billing_portal/configurations/bpc_pinned"]["features"][feature] = {
        "enabled": True
    }
    assert "pinned_portal_unsafe" in run(values)["failures"]


@pytest.mark.parametrize(
    "change",
    [
        "login",
        "missing_login",
        "immediate_cancel",
        "proration",
        "missing_maintenance",
        "boolean_shape",
    ],
)
def test_portal_default_cannot_bypass_core(change):
    values = fixture()
    portal = values["/billing_portal/configurations"]["data"][0]
    if change == "login":
        portal["login_page"]["enabled"] = True
    elif change == "missing_login":
        portal.pop("login_page")
    elif change == "immediate_cancel":
        portal["features"]["subscription_cancel"]["mode"] = "immediately"
    elif change == "proration":
        portal["features"]["subscription_cancel"]["proration_behavior"] = (
            "create_prorations"
        )
    elif change == "missing_maintenance":
        portal["features"].pop("payment_method_update")
    else:
        portal["features"]["subscription_update"]["enabled"] = 0
    assert "default_portal_unsafe" in run(values)["failures"]


@pytest.mark.parametrize(
    "change",
    ["missing", "duplicate", "mode", "version", "disabled", "events", "wildcard"],
)
def test_webhook_must_be_exact_enabled_version_and_explicit_events(change):
    values = fixture()
    endpoints = values["/webhook_endpoints"]["data"]
    if change == "missing":
        endpoints.clear()
    elif change == "duplicate":
        endpoints.append({**endpoints[0], "id": "we_second"})
    elif change == "mode":
        endpoints[0]["livemode"] = True
    elif change == "version":
        endpoints[0]["api_version"] = None
    elif change == "disabled":
        endpoints[0]["status"] = "disabled"
    else:
        endpoints[0]["enabled_events"] = ["*"] if change == "wildcard" else []
    assert any(name.startswith("webhook_") for name in run(values)["failures"])


def test_account_mismatch_stops_before_other_reads():
    fetch = Mock(return_value={"id": "acct_foreign"})
    with pytest.raises(audit.Refused, match="stripe_account_mismatch"):
        audit.audit(
            fetch,
            account="acct_owned",
            price="price_owned",
            portal="bpc_pinned",
            live=False,
        )
    fetch.assert_called_once_with("/account", [])


def test_pinned_configuration_must_still_appear_in_complete_inventory():
    values = fixture()
    values["/billing_portal/configurations"]["data"] = []
    assert "portal_inventory_changed" in run(values)["failures"]


def test_no_saved_default_is_compatible_but_multiple_defaults_are_not():
    values = fixture()
    values["/billing_portal/configurations/bpc_pinned"]["is_default"] = False
    values["/billing_portal/configurations"]["data"][0]["is_default"] = False
    assert run(values)["configuration_verified"]
    values = fixture()
    extra = copy.deepcopy(values["/billing_portal/configurations"]["data"][0])
    extra["id"] = "bpc_second"
    values["/billing_portal/configurations"]["data"].append(extra)
    assert "default_portal_unsafe" in run(values)["failures"]


def test_malformed_active_flag_and_recurring_boolean_do_not_pass():
    values = fixture()
    values["/billing_portal/configurations"]["data"][0]["active"] = "true"
    with pytest.raises(audit.Refused, match="portal_inventory_invalid"):
        run(values)
    values = fixture()
    values["/prices/price_owned"]["recurring"]["interval_count"] = True
    assert "price_contract_mismatch" in run(values)["failures"]


def test_inventory_follows_every_page_and_rejects_repeated_cursor():
    fetch = Mock(
        side_effect=[
            {"data": [{"id": "we_one"}], "has_more": True},
            {"data": [{"id": "we_two"}], "has_more": False},
        ]
    )
    assert len(audit.inventory(fetch, "/webhook_endpoints", "we_")) == 2
    assert fetch.call_args.args[1] == [("limit", "100"), ("starting_after", "we_one")]
    fetch = Mock(return_value={"data": [{"id": "we_one"}], "has_more": True})
    with pytest.raises(audit.Refused, match="stripe_inventory_duplicate"):
        audit.inventory(fetch, "/webhook_endpoints", "we_")


@pytest.mark.parametrize(
    "page",
    [
        {"data": [], "has_more": True},
        {"data": [], "has_more": 0},
        {"data": [{}], "has_more": False},
    ],
)
def test_partial_or_malformed_inventory_never_passes(page):
    with pytest.raises(audit.Refused):
        audit.inventory(lambda *_: page, "/webhook_endpoints", "we_")


def test_cli_mode_mismatch_does_not_send_or_print_key(monkeypatch, capsys):
    secret = "sk_live_NEVERPRINT"
    monkeypatch.setenv("STRIPE_SECRET_KEY", secret)
    reader = Mock(side_effect=AssertionError("must not send"))
    monkeypatch.setattr(audit, "stripe_reader", reader)
    assert audit.main([]) == 1
    assert secret not in capsys.readouterr().out
    reader.assert_not_called()


def test_transport_is_get_only_and_does_not_forward_redirect_credentials(monkeypatch):
    opener = MagicMock()
    response = opener.open.return_value.__enter__.return_value
    response.read.return_value = b'{"id":"acct_owned"}'
    build = Mock(return_value=opener)
    monkeypatch.setattr(audit.urllib.request, "build_opener", build)
    fetch = audit.stripe_reader("sk_test_NEVERPRINT", "2025-09-30.clover")
    assert fetch("/account", []) == {"id": "acct_owned"}
    request = opener.open.call_args.args[0]
    assert request.method == "GET"
    assert request.full_url == "https://api.stripe.com/v1/account"
    assert build.call_args.args[0].proxies == {}
    with pytest.raises(audit.Refused, match="stripe_redirect_refused"):
        build.call_args.args[1].redirect_request(
            request, None, 302, "", {}, "https://evil.invalid"
        )
    with pytest.raises(audit.Refused, match="stripe_path_refused"):
        fetch("/prices/../../secret", [])
    opener.open.side_effect = urllib.error.URLError("sk_test_NEVERPRINT")
    with pytest.raises(audit.Refused, match="^stripe_read_failed$"):
        fetch("/account", [])
