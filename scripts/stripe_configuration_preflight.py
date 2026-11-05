#!/usr/bin/env python3
"""Read-only Stripe configuration audit; never payment or acquisition admission.

No app import (application startup can migrate), database access or write API.
Supply STRIPE_SECRET_KEY, STRIPE_ACCOUNT_ID, STRIPE_PRICE_ID and
STRIPE_PORTAL_CONFIGURATION_ID privately. The only output is a fixed aggregate
report. Run without --live-mode against a test account first.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "https://api.stripe.com/v1"
MAX_PAGES = 10
MAX_BYTES = 2_000_000
REQUIRED_EVENTS = frozenset(
    {
        "checkout.session.completed",
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
        "customer.subscription.paused",
        "customer.subscription.resumed",
        "invoice.payment_failed",
        "invoice.paid",
        "invoice.payment_succeeded",
    }
)
PORTAL_FEATURES = frozenset(
    {
        "customer_update",
        "invoice_history",
        "payment_method_update",
        "subscription_cancel",
        "subscription_pause",
        "subscription_update",
    }
)
Fetch = Callable[[str, list[tuple[str, str]]], dict[str, Any]]


class Refused(RuntimeError):
    """Fixed, sanitized refusal code only."""


def source_constant(path: str, name: str) -> Any:
    for node in ast.parse((ROOT / path).read_text()).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise Refused("source_contract_unavailable")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Refused("stripe_redirect_refused")


def stripe_reader(secret: str, version: str) -> Fetch:
    # No ambient proxy or redirect may receive the credential. A shared
    # request budget prevents starting another page after the audit expires.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    deadline = time.monotonic() + 90

    def get(path: str, params: list[tuple[str, str]]) -> dict[str, Any]:
        if not re.fullmatch(
            r"/(?:account|prices/price_[A-Za-z0-9]+|billing_portal/configurations"
            r"(?:/bpc_[A-Za-z0-9]+)?|webhook_endpoints)",
            path,
        ):
            raise Refused("stripe_path_refused")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Refused("stripe_deadline_exceeded")
        url = ORIGIN + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(
            url,
            method="GET",
            headers={
                "Authorization": f"Bearer {secret}",
                "Stripe-Version": version,
                "User-Agent": "CosmaNeura-Configuration-Preflight/1",
            },
        )
        try:
            with opener.open(request, timeout=min(15, remaining)) as response:
                raw = response.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise Refused("stripe_response_too_large")
                result = json.loads(raw)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            raise Refused("stripe_read_failed") from None
        if not isinstance(result, dict):
            raise Refused("stripe_response_invalid")
        return result

    return get


def inventory(fetch: Fetch, path: str, prefix: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    cursor = ""
    for _ in range(MAX_PAGES):
        params = [("limit", "100")]
        if cursor:
            params.append(("starting_after", cursor))
        page = fetch(path, params)
        items, more = page.get("data"), page.get("has_more")
        if not isinstance(items, list) or len(items) > 100 or type(more) is not bool:
            raise Refused("stripe_inventory_invalid")
        for item in items:
            if not isinstance(item, dict):
                raise Refused("stripe_inventory_invalid")
            identity = item.get("id")
            if not isinstance(identity, str) or not re.fullmatch(
                prefix + r"[A-Za-z0-9]+", identity
            ):
                raise Refused("stripe_inventory_invalid")
            if identity in seen:
                raise Refused("stripe_inventory_duplicate")
            seen.add(identity)
            result.append(item)
        if not more:
            return result
        if not items:
            raise Refused("stripe_inventory_incomplete")
        cursor = items[-1]["id"]
    raise Refused("stripe_inventory_incomplete")


def portal_safe(value: dict[str, Any], live: bool) -> bool:
    if value.get("livemode") is not live or value.get("active") is not True:
        return False
    login, features = value.get("login_page"), value.get("features")
    if not isinstance(login, dict) or login.get("enabled") is not False:
        return False
    if not isinstance(features, dict) or set(features) - PORTAL_FEATURES:
        return False
    if any(not isinstance(feature, dict) for feature in features.values()):
        return False
    for name in ("customer_update", "subscription_update"):
        if features.get(name, {}).get("enabled") is not False:
            return False
    if (
        "subscription_pause" in features
        and features["subscription_pause"].get("enabled") is not False
    ):
        return False
    cancel = features.get("subscription_cancel", {})
    if cancel.get("enabled") is not False and not (
        cancel.get("enabled") is True
        and cancel.get("mode") == "at_period_end"
        and cancel.get("proration_behavior") == "none"
    ):
        return False
    return all(
        features.get(name, {}).get("enabled") is True
        for name in ("invoice_history", "payment_method_update")
    )


def audit(
    fetch: Fetch, *, account: str, price: str, portal: str, live: bool
) -> dict[str, Any]:
    if not all(
        re.fullmatch(prefix + r"[A-Za-z0-9]+", value)
        for prefix, value in (("acct_", account), ("price_", price), ("bpc_", portal))
    ):
        raise Refused("configuration_identifiers_invalid")
    if fetch("/account", []).get("id") != account:
        raise Refused("stripe_account_mismatch")
    version = source_constant(
        "app/configuration/stripe.py", "SUPPORTED_STRIPE_API_VERSION"
    )
    failures: list[str] = []
    current = fetch("/prices/" + price, [])
    recurring = current.get("recurring")
    expected = {
        "currency": "EXPECTED_PRICE_CURRENCY",
        "unit_amount": "EXPECTED_PRICE_UNIT_AMOUNT",
    }
    price_ok = all(
        current.get(k) == source_constant("app/services/stripe_billing.py", v)
        for k, v in expected.items()
    )
    price_ok = (
        price_ok
        and current.get("id") == price
        and current.get("livemode") is live
        and current.get("active") is True
    )
    price_ok = (
        price_ok
        and current.get("type") == "recurring"
        and current.get("billing_scheme") == "per_unit"
        and current.get("transform_quantity") is None
    )
    price_ok = (
        price_ok
        and isinstance(recurring, dict)
        and recurring.get("interval")
        == source_constant("app/services/stripe_billing.py", "EXPECTED_PRICE_INTERVAL")
        and recurring.get("interval_count") == 1
        and type(recurring.get("interval_count")) is int
        and recurring.get("usage_type") == "licensed"
    )
    if not price_ok:
        failures.append("price_contract_mismatch")
    pinned = fetch("/billing_portal/configurations/" + portal, [])
    if pinned.get("id") != portal or not portal_safe(pinned, live):
        failures.append("pinned_portal_unsafe")
    configs = inventory(fetch, "/billing_portal/configurations", "bpc_")
    if any(
        c.get("livemode") is not live
        or type(c.get("is_default")) is not bool
        or type(c.get("active")) is not bool
        for c in configs
    ):
        raise Refused("portal_inventory_invalid")
    pinned_inventory = [c for c in configs if c["id"] == portal]
    if len(pinned_inventory) != 1 or any(
        pinned_inventory[0].get(key) != pinned.get(key)
        for key in ("active", "livemode", "is_default", "features", "login_page")
    ):
        failures.append("portal_inventory_changed")
    defaults = [c for c in configs if c["is_default"]]
    if len(defaults) > 1 or any(not portal_safe(c, live) for c in defaults):
        failures.append("default_portal_unsafe")
    if any(
        not isinstance(c.get("login_page"), dict)
        or c["login_page"].get("enabled") is not False
        for c in configs
        if c.get("active") is True
    ):
        failures.append("portal_login_page_enabled_or_unknown")
    endpoints = inventory(fetch, "/webhook_endpoints", "we_")
    url = (
        "https://"
        + ("app.cosmaneura.com" if live else "staging-app.cosmaneura.com")
        + "/external/v1/webhooks/stripe"
    )
    matching = [e for e in endpoints if e.get("url") == url]
    if len(matching) != 1:
        failures.append("webhook_endpoint_not_unique")
    else:
        endpoint = matching[0]
        events = endpoint.get("enabled_events")
        if not (
            endpoint.get("livemode") is live
            and endpoint.get("status") == "enabled"
            and endpoint.get("api_version") == version
            and isinstance(events, list)
            and all(isinstance(e, str) for e in events)
            and REQUIRED_EVENTS <= set(events)
        ):
            failures.append("webhook_contract_mismatch")
    report = {
        "schema_version": 1,
        "checked_at": datetime.now(UTC).isoformat(),
        "live_mode": live,
        "read_only": True,
        "configuration_verified": not failures,
        "failures": sorted(failures),
        "portal_configurations_scanned": len(configs),
        "default_configurations": len(defaults),
        "webhook_endpoints_scanned": len(endpoints),
        "not_verified": [
            "webhook_secret_and_delivery",
            "worker_reconciliation",
            "portal_email_links_disabled",
            "checkout_drain",
            "legacy_subscription_reconciliation",
            "payment_acceptance",
        ],
    }
    report["sha256"] = hashlib.sha256(
        json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-mode", action="store_true")
    args = parser.parse_args(argv)
    try:
        secret = os.environ.get("STRIPE_SECRET_KEY", "")
        mode = "live" if args.live_mode else "test"
        if not re.fullmatch(r"(?:sk|rk)_" + mode + r"_[A-Za-z0-9]+", secret):
            raise Refused("stripe_key_mode_invalid")
        version = source_constant(
            "app/configuration/stripe.py", "SUPPORTED_STRIPE_API_VERSION"
        )
        report = audit(
            stripe_reader(secret, version),
            account=os.environ.get("STRIPE_ACCOUNT_ID", ""),
            price=os.environ.get("STRIPE_PRICE_ID", ""),
            portal=os.environ.get("STRIPE_PORTAL_CONFIGURATION_ID", ""),
            live=args.live_mode,
        )
    except Refused as exc:
        print(
            json.dumps(
                {"configuration_verified": False, "read_only": True, "error": str(exc)}
            )
        )
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0 if report["configuration_verified"] else 1


if __name__ == "__main__":
    sys_exit = main()
    raise SystemExit(sys_exit)
