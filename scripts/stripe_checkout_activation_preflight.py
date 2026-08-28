#!/usr/bin/env python3
"""Audit and drain open CosmaNeura Checkout Sessions before acquisition.

This command is intentionally standalone: it talks only to Stripe and does not
import the application or mutate its database. Run it after the card-required
code is deployed with acquisition disabled and the last old revision has been
out of service for the full drain interval. Output is aggregate, deterministic
JSON suitable for attaching to the deployment change record; raw session and
practice identifiers are never printed.

Dry run:

    STRIPE_SECRET_KEY=... STRIPE_ACCOUNT_ID=... STRIPE_PRODUCT_ID=... \
      STRIPE_PRICE_ID=... STRIPE_PORTAL_CONFIGURATION_ID=... \
      STRIPE_RETIRED_PRICE_IDS=price_old_1,price_old_2 \
      STRIPE_WEBHOOK_ENDPOINT_URL=https://example.invalid/external/v1/webhooks/stripe \
      python scripts/stripe_checkout_activation_preflight.py \
      --operator-ref CHANGE-123 \
      --drain-started-at 2026-08-24T12:00:00Z \
      --expected-mode live \
      --confirm-acquisition-disabled \
      --confirm-old-code-stopped \
      --confirm-price-inventory-complete \
      --confirm-secret-configuration \
      --confirm-founder-owner-approval \
      --owner-approval-ref '<approval-record>' \
      --pending-credential-trial-expiry-policy '<owner-approved-policy>' \
      --policy-implementation-evidence-ref '<implementation-record>' \
      --reviewed-source-sha '<reviewed-full-sha>' \
      --deployed-source-sha '<deployed-full-sha>'

If the dry run reports eligible open sessions, review its plan digest and rerun
with ``--apply --confirm-plan-sha256 <digest>``. A successful apply re-scans the
entire open-session collection and exits zero only when no CosmaNeura session is
left open and the maximum-session drain interval has elapsed.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

STRIPE_API = "https://api.stripe.com/v1"
STRIPE_API_VERSION = "2025-09-30.clover"
CURRENT_PAYMENT_METHOD_COLLECTION = "always"
ACTIVATION_POLICY_VERSION = "paid-pilot-v2"
ENTITLEMENT_CONTRACT_VERSION = "public-capability-entitlements.v1"
ENTITLEMENT_POLICY_REVISION = "2026-08-27"
ENTITLEMENT_SCHEMA_SHA256 = (
    "a33ba095ea284575fc8004ecc7b2e05b6aa60c911f5c03add3a0ea1f7ef9e578"
)
EXPECTED_PRICE_CURRENCY = "usd"
EXPECTED_PRICE_UNIT_AMOUNT = 8_900
EXPECTED_PRICE_INTERVAL = "month"
EXPECTED_PRICE_INTERVAL_COUNT = 1
EXPECTED_PRICE_USAGE_TYPE = "licensed"
EXPECTED_PRICE_BILLING_SCHEME = "per_unit"
EXPECTED_WEBHOOK_EVENTS = frozenset(
    {
        "checkout.session.completed",
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
        "customer.subscription.paused",
        "customer.subscription.resumed",
    }
)
PENDING_CREDENTIAL_TRIAL_EXPIRY_POLICIES = frozenset(
    {"charge_scheduled", "extend_trial", "hold_conversion"}
)
MAX_CHECKOUT_SESSION_LIFETIME = timedelta(hours=24)
DRAIN_SAFETY_BUFFER = timedelta(minutes=5)
MINIMUM_DRAIN = MAX_CHECKOUT_SESSION_LIFETIME + DRAIN_SAFETY_BUFFER
MAX_PAGES = 10_000
PRACTICE_REFERENCE = re.compile(r"^practice_[0-9a-hjkmnp-tv-z]{26}$")
SAFE_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,127}$")
GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PreflightError(RuntimeError):
    """Sanitized activation-preflight failure."""


StripeRequest = Callable[
    [str, str, str, list[tuple[str, str]] | None, str | None], dict[str, Any]
]


@dataclass(frozen=True)
class SessionScan:
    pages: int
    total_open: int
    eligible: tuple[dict[str, Any], ...]
    ambiguous: tuple[dict[str, Any], ...]

    @property
    def relevant_count(self) -> int:
        return len(self.eligible) + len(self.ambiguous)

    @property
    def legacy_count(self) -> int:
        return sum(
            session.get("payment_method_collection")
            != CURRENT_PAYMENT_METHOD_COLLECTION
            for session in self.eligible
        )


@dataclass(frozen=True)
class WebhookVerification:
    pages: int
    endpoints_scanned: int


@dataclass(frozen=True)
class PriceInventoryVerification:
    pages: int
    prices_scanned: int
    products: int
    product_prices: int


@dataclass(frozen=True)
class RecoveryScan:
    pages: int
    total_expired: int
    recoverable: tuple[dict[str, Any], ...]
    ambiguous: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class PaymentLinkScan:
    pages: int
    total_active: int
    relevant: tuple[dict[str, Any], ...]
    ambiguous: tuple[dict[str, Any], ...]


def _stripe_request(
    secret_key: str,
    method: str,
    path: str,
    params: list[tuple[str, str]] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    encoded = urllib.parse.urlencode(params or []).encode()
    url = f"{STRIPE_API}{path}"
    data: bytes | None = None
    if method == "GET" and encoded:
        url = f"{url}?{encoded.decode()}"
    elif method == "POST":
        data = encoded
    headers = {
        "Authorization": f"Bearer {secret_key}",
        "Stripe-Version": STRIPE_API_VERSION,
        "User-Agent": "CosmaNeura-Stripe-Activation-Preflight/1",
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise PreflightError("Stripe request failed") from exc
    if not isinstance(payload, dict):
        raise PreflightError("Stripe returned an invalid response")
    return payload


def _line_item_price_ids(resource: dict[str, Any]) -> frozenset[str] | None:
    line_items = resource.get("line_items")
    if not isinstance(line_items, dict):
        return None
    data = line_items.get("data")
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        return None
    result: set[str] = set()
    for item in data:
        price = item.get("price")
        returned_price_id = price.get("id") if isinstance(price, dict) else price
        if not isinstance(returned_price_id, str):
            return None
        result.add(returned_price_id)
    return frozenset(result)


def _valid_line_items(session: dict[str, Any], *, price_ids: frozenset[str]) -> bool:
    line_items = session.get("line_items")
    if not isinstance(line_items, dict):
        return False
    data = line_items.get("data")
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        return False
    returned_price_ids = _line_item_price_ids(session)
    quantity = data[0].get("quantity")
    return (
        returned_price_ids is not None
        and len(returned_price_ids) == 1
        and returned_price_ids <= price_ids
        and isinstance(quantity, int)
        and 1 <= quantity <= 20
    )


def _session_recovery_disabled(session: dict[str, Any]) -> bool:
    after_expiration = session.get("after_expiration")
    if after_expiration is None:
        return True
    if not isinstance(after_expiration, dict):
        return False
    recovery = after_expiration.get("recovery")
    if recovery is None:
        return True
    return isinstance(recovery, dict) and recovery.get("enabled") is False


def _session_is_safely_expirable(
    session: dict[str, Any], *, price_ids: frozenset[str], live_mode: bool
) -> bool:
    created = session.get("created")
    expires_at = session.get("expires_at")
    return bool(
        isinstance(session.get("id"), str)
        and str(session["id"]).startswith("cs_")
        and session.get("status") == "open"
        and session.get("mode") == "subscription"
        and session.get("livemode") is live_mode
        and session.get("subscription") is None
        and isinstance(created, int)
        and isinstance(expires_at, int)
        and created < expires_at
        and expires_at - created <= int(MAX_CHECKOUT_SESSION_LIFETIME.total_seconds())
        and _valid_line_items(session, price_ids=price_ids)
        and _session_recovery_disabled(session)
    )


def scan_open_sessions(
    request: StripeRequest,
    secret_key: str,
    *,
    price_ids: frozenset[str],
    live_mode: bool,
) -> SessionScan:
    """Fully paginate all open sessions and classify CosmaNeura-bound rows."""

    eligible: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    total_open = 0
    starting_after: str | None = None
    for page_number in range(1, MAX_PAGES + 1):
        params = [
            ("status", "open"),
            ("limit", "100"),
            ("expand[]", "data.line_items"),
        ]
        if starting_after:
            params.append(("starting_after", starting_after))
        page = request(secret_key, "GET", "/checkout/sessions", params, None)
        data = page.get("data")
        if not isinstance(data, list) or not all(
            isinstance(item, dict) for item in data
        ):
            raise PreflightError("Stripe session page has an invalid shape")
        total_open += len(data)
        for raw_session in data:
            session = dict(raw_session)
            reference = session.get("client_reference_id")
            valid_reference = isinstance(
                reference, str
            ) and PRACTICE_REFERENCE.fullmatch(reference)
            returned_price_ids = _line_item_price_ids(session)
            # Pre-practice website Checkout did not have a practice reference.
            # The account's exact configured CosmaNeura price is therefore a
            # second ownership signal. The exact configured price is sufficient
            # for the retired direct flow, regardless of its reference shape.
            # Anything with either signal but not all safe-expiration invariants
            # is surfaced as ambiguous, never ignored.
            if not valid_reference:
                if returned_price_ids is None:
                    ambiguous.append(session)
                    continue
                if not returned_price_ids & price_ids:
                    continue
            if _session_is_safely_expirable(
                session, price_ids=price_ids, live_mode=live_mode
            ):
                eligible.append(session)
            else:
                ambiguous.append(session)
        has_more = page.get("has_more")
        if has_more is False:
            return SessionScan(
                pages=page_number,
                total_open=total_open,
                eligible=tuple(eligible),
                ambiguous=tuple(ambiguous),
            )
        if has_more is not True or not data:
            raise PreflightError("Stripe session pagination is invalid")
        last_id = data[-1].get("id")
        if not isinstance(last_id, str) or not last_id.startswith("cs_"):
            raise PreflightError("Stripe session pagination cursor is invalid")
        starting_after = last_id
    raise PreflightError("Stripe session pagination exceeded its safety bound")


def _recovery_is_active(
    session: dict[str, Any], *, checked_at: datetime
) -> bool | None:
    after_expiration = session.get("after_expiration")
    if after_expiration is None:
        return False
    if not isinstance(after_expiration, dict):
        return None
    recovery = after_expiration.get("recovery")
    if recovery is None:
        return False
    if not isinstance(recovery, dict):
        return None
    enabled = recovery.get("enabled")
    if enabled is False:
        return False
    expires_at = recovery.get("expires_at")
    if enabled is not True or not isinstance(expires_at, int):
        return None
    return expires_at > int(checked_at.timestamp())


def scan_expired_recoveries(
    request: StripeRequest,
    secret_key: str,
    *,
    price_ids: frozenset[str],
    checked_at: datetime,
) -> RecoveryScan:
    """Find relevant expired Sessions whose recovery URL could mint a new Session."""

    recoverable: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    total_expired = 0
    starting_after: str | None = None
    for page_number in range(1, MAX_PAGES + 1):
        params = [
            ("status", "expired"),
            ("limit", "100"),
            ("expand[]", "data.line_items"),
        ]
        if starting_after:
            params.append(("starting_after", starting_after))
        page = request(secret_key, "GET", "/checkout/sessions", params, None)
        data = page.get("data")
        if not isinstance(data, list) or not all(
            isinstance(item, dict) for item in data
        ):
            raise PreflightError("Stripe expired-session page has an invalid shape")
        total_expired += len(data)
        for raw_session in data:
            session = dict(raw_session)
            reference = session.get("client_reference_id")
            valid_reference = isinstance(
                reference, str
            ) and PRACTICE_REFERENCE.fullmatch(reference)
            returned_price_ids = _line_item_price_ids(session)
            if not valid_reference:
                if returned_price_ids is None:
                    ambiguous.append(session)
                    continue
                if not returned_price_ids & price_ids:
                    continue
            recovery_active = _recovery_is_active(session, checked_at=checked_at)
            if recovery_active is True:
                recoverable.append(session)
            elif recovery_active is None:
                ambiguous.append(session)
        has_more = page.get("has_more")
        if has_more is False:
            return RecoveryScan(
                pages=page_number,
                total_expired=total_expired,
                recoverable=tuple(recoverable),
                ambiguous=tuple(ambiguous),
            )
        if has_more is not True or not data:
            raise PreflightError("Stripe expired-session pagination is invalid")
        last_id = data[-1].get("id")
        if not isinstance(last_id, str) or not last_id.startswith("cs_"):
            raise PreflightError("Stripe expired-session pagination is invalid")
        starting_after = last_id
    raise PreflightError("Stripe expired-session pagination exceeded its safety bound")


def scan_active_payment_links(
    request: StripeRequest,
    secret_key: str,
    *,
    price_ids: frozenset[str],
) -> PaymentLinkScan:
    """Find active Payment Links that can mint a new paid-pilot Checkout Session."""

    relevant: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    total_active = 0
    starting_after: str | None = None
    for page_number in range(1, MAX_PAGES + 1):
        params = [("active", "true"), ("limit", "100"), ("expand[]", "data.line_items")]
        if starting_after:
            params.append(("starting_after", starting_after))
        page = request(secret_key, "GET", "/payment_links", params, None)
        data = page.get("data")
        if not isinstance(data, list) or not all(
            isinstance(item, dict) for item in data
        ):
            raise PreflightError("Stripe Payment Link page has an invalid shape")
        total_active += len(data)
        for raw_link in data:
            link = dict(raw_link)
            returned_price_ids = _line_item_price_ids(link)
            if returned_price_ids is None:
                ambiguous.append(link)
            elif returned_price_ids & price_ids:
                relevant.append(link)
        has_more = page.get("has_more")
        if has_more is False:
            return PaymentLinkScan(
                pages=page_number,
                total_active=total_active,
                relevant=tuple(relevant),
                ambiguous=tuple(ambiguous),
            )
        if has_more is not True or not data:
            raise PreflightError("Stripe Payment Link pagination is invalid")
        last_id = data[-1].get("id")
        if not isinstance(last_id, str) or not last_id.startswith("plink_"):
            raise PreflightError("Stripe Payment Link pagination is invalid")
        starting_after = last_id
    raise PreflightError("Stripe Payment Link pagination exceeded its safety bound")


def _session_id_digest(sessions: tuple[dict[str, Any], ...]) -> str:
    ids = sorted(str(session["id"]) for session in sessions)
    return hashlib.sha256("\n".join(ids).encode()).hexdigest()


def _plan_digest(
    *,
    account_id: str,
    product_id: str,
    portal_configuration_id: str,
    price_ids: tuple[str, ...],
    live_mode: bool,
    drain_started_at: datetime,
    pending_credential_trial_expiry_policy: str | None,
    completed_session_reconciliation_at: datetime | None,
    completed_session_reconciliation_ref: str,
    completed_session_reconciliation_sha256: str,
    test_mode_lifecycle_evidence_ref: str,
    test_mode_lifecycle_evidence_sha256: str,
    observability_evidence_ref: str,
    observability_evidence_sha256: str,
    owner_approval_ref: str,
    policy_implementation_evidence_ref: str,
    reviewed_source_sha: str,
    deployed_source_sha: str,
    scan: SessionScan,
    recovery_scan: RecoveryScan,
    payment_link_scan: PaymentLinkScan,
    webhook_endpoint_url: str,
) -> str:
    payload = {
        "activation_policy": ACTIVATION_POLICY_VERSION,
        "account_id": account_id,
        "checkout_policy": ACTIVATION_POLICY_VERSION,
        "completed_session_reconciliation_at": (
            completed_session_reconciliation_at.isoformat()
            if completed_session_reconciliation_at is not None
            else None
        ),
        "completed_session_reconciliation_ref": (completed_session_reconciliation_ref),
        "completed_session_reconciliation_sha256": (
            completed_session_reconciliation_sha256
        ),
        "drain_started_at": drain_started_at.isoformat(),
        "entitlement_contract": {
            "schema_sha256": ENTITLEMENT_SCHEMA_SHA256,
            "version": ENTITLEMENT_CONTRACT_VERSION,
        },
        "eligible_session_ids": sorted(str(session["id"]) for session in scan.eligible),
        "ambiguous_session_ids": sorted(
            str(session["id"]) for session in scan.ambiguous
        ),
        "recoverable_expired_session_ids": sorted(
            str(session["id"]) for session in recovery_scan.recoverable
        ),
        "ambiguous_expired_session_ids": sorted(
            str(session["id"]) for session in recovery_scan.ambiguous
        ),
        "relevant_active_payment_link_ids": sorted(
            str(link["id"]) for link in payment_link_scan.relevant
        ),
        "ambiguous_active_payment_link_ids": sorted(
            str(link["id"]) for link in payment_link_scan.ambiguous
        ),
        "expected_price_contract": {
            "billing_scheme": EXPECTED_PRICE_BILLING_SCHEME,
            "currency": EXPECTED_PRICE_CURRENCY,
            "interval": EXPECTED_PRICE_INTERVAL,
            "interval_count": EXPECTED_PRICE_INTERVAL_COUNT,
            "unit_amount": EXPECTED_PRICE_UNIT_AMOUNT,
            "usage_type": EXPECTED_PRICE_USAGE_TYPE,
        },
        "expected_webhook_events": sorted(EXPECTED_WEBHOOK_EVENTS),
        "live_mode": live_mode,
        "pending_credential_trial_expiry_policy": (
            pending_credential_trial_expiry_policy
        ),
        "owner_approval_ref": owner_approval_ref,
        "observability_evidence_ref": observability_evidence_ref,
        "observability_evidence_sha256": observability_evidence_sha256,
        "policy_implementation_evidence_ref": policy_implementation_evidence_ref,
        "price_ids": price_ids,
        "product_id": product_id,
        "portal_configuration_id": portal_configuration_id,
        "reviewed_source_sha": reviewed_source_sha,
        "test_mode_lifecycle_evidence_ref": test_mode_lifecycle_evidence_ref,
        "test_mode_lifecycle_evidence_sha256": test_mode_lifecycle_evidence_sha256,
        "deployed_source_sha": deployed_source_sha,
        "webhook_url_sha256": hashlib.sha256(webhook_endpoint_url.encode()).hexdigest(),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _parse_utc_timestamp(value: str, *, label: str = "timestamp") -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PreflightError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise PreflightError(f"{label} must include a timezone")
    return parsed.astimezone(UTC)


def _verify_secret_mode(secret_key: str, *, live_mode: bool) -> None:
    expected_prefixes = (
        ("sk_live_", "rk_live_") if live_mode else ("sk_test_", "rk_test_")
    )
    if not secret_key.startswith(expected_prefixes):
        raise PreflightError("Stripe secret key mode does not match expected mode")


def _stripe_object_id(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("id"), str):
        return str(value["id"])
    return None


def _verify_account_and_prices(
    request: StripeRequest,
    secret_key: str,
    *,
    account_id: str,
    product_id: str,
    price_id: str,
    legacy_price_ids: tuple[str, ...],
    live_mode: bool,
) -> PriceInventoryVerification:
    account = request(secret_key, "GET", "/account", None, None)
    if account.get("id") != account_id:
        raise PreflightError("Stripe key resolved to the wrong account")
    requirements = account.get("requirements")
    if live_mode and (
        account.get("charges_enabled") is not True
        or account.get("payouts_enabled") is not True
        or account.get("details_submitted") is not True
        or not isinstance(requirements, dict)
        or requirements.get("disabled_reason") is not None
        or requirements.get("currently_due") not in (None, [])
        or requirements.get("past_due") not in (None, [])
    ):
        raise PreflightError("Stripe account is not ready for live billing")
    product = request(secret_key, "GET", f"/products/{product_id}", None, None)
    if (
        product.get("id") != product_id
        or product.get("livemode") is not live_mode
        or product.get("active") is not True
        or _stripe_object_id(product.get("default_price")) != price_id
    ):
        raise PreflightError("Stripe product does not match the paid-pilot offer")
    current_price = request(secret_key, "GET", f"/prices/{price_id}", None, None)
    recurring = current_price.get("recurring")
    if (
        current_price.get("id") != price_id
        or _stripe_object_id(current_price.get("product")) != product_id
        or current_price.get("livemode") is not live_mode
        or current_price.get("active") is not True
        or current_price.get("type") != "recurring"
        or current_price.get("currency") != EXPECTED_PRICE_CURRENCY
        or current_price.get("unit_amount") != EXPECTED_PRICE_UNIT_AMOUNT
        or not isinstance(recurring, dict)
        or recurring.get("interval") != EXPECTED_PRICE_INTERVAL
        or recurring.get("interval_count") != EXPECTED_PRICE_INTERVAL_COUNT
        or recurring.get("usage_type") != EXPECTED_PRICE_USAGE_TYPE
        or current_price.get("billing_scheme") != EXPECTED_PRICE_BILLING_SCHEME
        or current_price.get("transform_quantity") is not None
    ):
        raise PreflightError("Current Stripe price does not match the paid-pilot offer")
    expected_prices_by_product: dict[str, set[str]] = {product_id: {price_id}}
    for candidate in legacy_price_ids:
        retired_price = request(secret_key, "GET", f"/prices/{candidate}", None, None)
        retired_product_id = _stripe_object_id(retired_price.get("product"))
        if (
            retired_price.get("id") != candidate
            or retired_product_id is None
            or not retired_product_id.startswith("prod_")
            or retired_price.get("livemode") is not live_mode
            or retired_price.get("active") is not False
        ):
            raise PreflightError(
                "Retired Stripe price inventory does not match the expected environment"
            )
        if retired_product_id != product_id:
            retired_product = request(
                secret_key, "GET", f"/products/{retired_product_id}", None, None
            )
            if (
                retired_product.get("id") != retired_product_id
                or retired_product.get("livemode") is not live_mode
            ):
                raise PreflightError(
                    "Retired Stripe product inventory does not match the environment"
                )
        expected_prices_by_product.setdefault(retired_product_id, set()).add(candidate)
    product_price_ids: dict[str, set[str]] = {
        expected_product_id: set() for expected_product_id in expected_prices_by_product
    }
    prices_scanned = 0
    pages_scanned = 0
    for active in ("true", "false"):
        starting_after: str | None = None
        for _ in range(MAX_PAGES):
            params = [("active", active), ("limit", "100")]
            if starting_after:
                params.append(("starting_after", starting_after))
            page = request(secret_key, "GET", "/prices", params, None)
            pages_scanned += 1
            data = page.get("data")
            if not isinstance(data, list) or not all(
                isinstance(item, dict) for item in data
            ):
                raise PreflightError("Stripe price inventory page has an invalid shape")
            prices_scanned += len(data)
            for price in data:
                listed_price_id = price.get("id")
                if not isinstance(
                    listed_price_id, str
                ) or not listed_price_id.startswith("price_"):
                    raise PreflightError(
                        "Stripe price inventory page has an invalid shape"
                    )
                listed_product_id = _stripe_object_id(price.get("product"))
                if listed_product_id in product_price_ids:
                    if listed_price_id in product_price_ids[listed_product_id]:
                        raise PreflightError(
                            "Stripe price inventory contains a duplicate"
                        )
                    product_price_ids[listed_product_id].add(listed_price_id)
            has_more = page.get("has_more")
            if has_more is False:
                break
            if has_more is not True or not data:
                raise PreflightError("Stripe price inventory pagination is invalid")
            last_id = data[-1].get("id")
            if not isinstance(last_id, str) or not last_id.startswith("price_"):
                raise PreflightError("Stripe price inventory pagination is invalid")
            starting_after = last_id
        else:
            raise PreflightError(
                "Stripe price inventory pagination exceeded its safety bound"
            )
    if product_price_ids != expected_prices_by_product:
        raise PreflightError("Stripe paid-pilot product price inventory is incomplete")
    return PriceInventoryVerification(
        pages=pages_scanned,
        prices_scanned=prices_scanned,
        products=len(product_price_ids),
        product_prices=sum(len(prices) for prices in product_price_ids.values()),
    )


def _verify_expected_webhook_url(expected_url: str) -> None:
    parsed = urllib.parse.urlsplit(expected_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or bool(parsed.query)
        or bool(parsed.fragment)
    ):
        raise PreflightError("Expected Stripe webhook URL is invalid")


def _verify_portal_configuration(
    request: StripeRequest,
    secret_key: str,
    *,
    configuration_id: str,
    live_mode: bool,
) -> None:
    configuration = request(
        secret_key,
        "GET",
        f"/billing_portal/configurations/{configuration_id}",
        None,
        None,
    )
    features = configuration.get("features")
    if not isinstance(features, dict):
        raise PreflightError("Stripe Billing Portal configuration is invalid")
    payment_method_update = features.get("payment_method_update")
    subscription_cancel = features.get("subscription_cancel")
    subscription_update = features.get("subscription_update")
    customer_update = features.get("customer_update")
    subscription_pause = features.get("subscription_pause")
    if (
        configuration.get("id") != configuration_id
        or configuration.get("active") is not True
        or configuration.get("livemode") is not live_mode
        or not isinstance(payment_method_update, dict)
        or payment_method_update.get("enabled") is not True
        or not isinstance(subscription_cancel, dict)
        or subscription_cancel.get("enabled") is not True
        or subscription_cancel.get("mode") != "at_period_end"
        or not isinstance(subscription_update, dict)
        or subscription_update.get("enabled") is not False
        or not isinstance(customer_update, dict)
        or customer_update.get("enabled") is not False
        or (
            subscription_pause is not None
            and (
                not isinstance(subscription_pause, dict)
                or subscription_pause.get("enabled") is not False
            )
        )
    ):
        raise PreflightError(
            "Stripe Billing Portal configuration is not restricted to safe controls"
        )


def _verify_webhook_endpoint(
    request: StripeRequest,
    secret_key: str,
    *,
    expected_url: str,
    live_mode: bool,
) -> WebhookVerification:
    """Verify one exact endpoint without returning its URL or Stripe identifier."""

    endpoints_scanned = 0
    matches: list[dict[str, Any]] = []
    starting_after: str | None = None
    for page_number in range(1, MAX_PAGES + 1):
        params = [("limit", "100")]
        if starting_after:
            params.append(("starting_after", starting_after))
        page = request(secret_key, "GET", "/webhook_endpoints", params, None)
        data = page.get("data")
        if not isinstance(data, list) or not all(
            isinstance(item, dict) for item in data
        ):
            raise PreflightError("Stripe webhook endpoint page has an invalid shape")
        endpoints_scanned += len(data)
        matches.extend(
            dict(endpoint) for endpoint in data if endpoint.get("url") == expected_url
        )
        has_more = page.get("has_more")
        if has_more is False:
            if len(matches) != 1:
                raise PreflightError(
                    "Expected Stripe webhook endpoint is not uniquely configured"
                )
            endpoint = matches[0]
            enabled_events = endpoint.get("enabled_events")
            if (
                not isinstance(endpoint.get("id"), str)
                or not str(endpoint["id"]).startswith("we_")
                or endpoint.get("status") != "enabled"
                or endpoint.get("livemode") is not live_mode
                or endpoint.get("api_version") != STRIPE_API_VERSION
                or not isinstance(enabled_events, list)
                or not all(isinstance(event, str) for event in enabled_events)
                or len(enabled_events) != len(set(enabled_events))
                or frozenset(enabled_events) != EXPECTED_WEBHOOK_EVENTS
            ):
                raise PreflightError(
                    "Expected Stripe webhook endpoint contract does not match"
                )
            return WebhookVerification(
                pages=page_number,
                endpoints_scanned=endpoints_scanned,
            )
        if has_more is not True or not data:
            raise PreflightError("Stripe webhook endpoint pagination is invalid")
        last_id = data[-1].get("id")
        if not isinstance(last_id, str) or not last_id.startswith("we_"):
            raise PreflightError("Stripe webhook endpoint pagination is invalid")
        starting_after = last_id
    raise PreflightError("Stripe webhook endpoint pagination exceeded its safety bound")


def _expire_session(
    request: StripeRequest,
    secret_key: str,
    session: dict[str, Any],
    *,
    live_mode: bool,
) -> None:
    session_id = str(session["id"])
    digest = hashlib.sha256(f"activation-expire:{session_id}".encode()).hexdigest()
    result = request(
        secret_key,
        "POST",
        f"/checkout/sessions/{session_id}/expire",
        None,
        f"cosmas-activation-expire-{digest}",
    )
    if (
        result.get("id") != session_id
        or result.get("status") != "expired"
        or result.get("livemode") is not live_mode
        or result.get("subscription") is not None
    ):
        raise PreflightError("Stripe did not authoritatively expire a session")


def _evidence(
    *,
    operator_ref: str,
    account_id: str,
    product_id: str,
    portal_configuration_id: str,
    price_id: str,
    legacy_price_ids: tuple[str, ...],
    live_mode: bool,
    drain_started_at: datetime,
    checked_at: datetime,
    scan: SessionScan,
    recovery_scan: RecoveryScan,
    payment_link_scan: PaymentLinkScan,
    applied: bool,
    expired_count: int,
    price_inventory_verification: PriceInventoryVerification,
    webhook_verification: WebhookVerification,
    webhook_endpoint_url: str,
    acquisition_disabled_confirmed: bool,
    old_code_stopped_confirmed: bool,
    price_inventory_confirmed: bool,
    secret_configuration_confirmed: bool,
    completed_session_reconciliation_confirmed: bool,
    completed_session_reconciliation_at: datetime | None,
    completed_session_reconciliation_ref: str,
    completed_session_reconciliation_sha256: str,
    test_mode_lifecycle_confirmed: bool,
    test_mode_lifecycle_evidence_ref: str,
    test_mode_lifecycle_evidence_sha256: str,
    observability_ready_confirmed: bool,
    observability_evidence_ref: str,
    observability_evidence_sha256: str,
    founder_owner_approval_confirmed: bool,
    owner_approval_ref: str,
    pending_credential_trial_expiry_policy: str | None,
    policy_implementation_evidence_ref: str,
    reviewed_source_sha: str,
    deployed_source_sha: str,
) -> dict[str, Any]:
    price_ids = (price_id, *legacy_price_ids)
    drain_complete_at = drain_started_at + MINIMUM_DRAIN
    drain_satisfied = checked_at >= drain_complete_at
    plan_sha256 = _plan_digest(
        account_id=account_id,
        product_id=product_id,
        portal_configuration_id=portal_configuration_id,
        price_ids=price_ids,
        live_mode=live_mode,
        drain_started_at=drain_started_at,
        pending_credential_trial_expiry_policy=(pending_credential_trial_expiry_policy),
        completed_session_reconciliation_at=completed_session_reconciliation_at,
        completed_session_reconciliation_ref=(completed_session_reconciliation_ref),
        completed_session_reconciliation_sha256=(
            completed_session_reconciliation_sha256
        ),
        test_mode_lifecycle_evidence_ref=test_mode_lifecycle_evidence_ref,
        test_mode_lifecycle_evidence_sha256=test_mode_lifecycle_evidence_sha256,
        observability_evidence_ref=observability_evidence_ref,
        observability_evidence_sha256=observability_evidence_sha256,
        owner_approval_ref=owner_approval_ref,
        policy_implementation_evidence_ref=policy_implementation_evidence_ref,
        reviewed_source_sha=reviewed_source_sha,
        deployed_source_sha=deployed_source_sha,
        scan=scan,
        recovery_scan=recovery_scan,
        payment_link_scan=payment_link_scan,
        webhook_endpoint_url=webhook_endpoint_url,
    )
    completed_session_reconciliation_recorded = bool(
        completed_session_reconciliation_confirmed
        and completed_session_reconciliation_at is not None
        and drain_complete_at <= completed_session_reconciliation_at <= checked_at
        and SAFE_REFERENCE.fullmatch(completed_session_reconciliation_ref)
        and SHA256.fullmatch(completed_session_reconciliation_sha256)
    )
    test_mode_lifecycle_evidenced = bool(
        test_mode_lifecycle_confirmed
        and SAFE_REFERENCE.fullmatch(test_mode_lifecycle_evidence_ref)
        and SHA256.fullmatch(test_mode_lifecycle_evidence_sha256)
    )
    observability_readiness_evidenced = bool(
        observability_ready_confirmed
        and SAFE_REFERENCE.fullmatch(observability_evidence_ref)
        and SHA256.fullmatch(observability_evidence_sha256)
    )
    owner_approval_recorded = bool(
        founder_owner_approval_confirmed
        and SAFE_REFERENCE.fullmatch(owner_approval_ref)
    )
    policy_implementation_evidenced = bool(
        pending_credential_trial_expiry_policy is not None
        and SAFE_REFERENCE.fullmatch(policy_implementation_evidence_ref)
    )
    reviewed_source_sha_valid = bool(GIT_SHA.fullmatch(reviewed_source_sha))
    deployed_source_sha_valid = bool(GIT_SHA.fullmatch(deployed_source_sha))
    reviewed_source_deployed = bool(
        reviewed_source_sha_valid
        and deployed_source_sha_valid
        and hmac.compare_digest(reviewed_source_sha, deployed_source_sha)
    )
    prerequisites = {
        "acquisition_disabled_confirmed": acquisition_disabled_confirmed,
        "account_and_mode_verified": True,
        "card_required_code_confirmed": old_code_stopped_confirmed,
        "completed_session_reconciliation_recorded": (
            completed_session_reconciliation_recorded
        ),
        "current_offer_verified": True,
        "drain_satisfied": drain_satisfied,
        "founder_owner_approval_recorded": owner_approval_recorded,
        "no_ambiguous_open_sessions": not scan.ambiguous,
        "no_eligible_open_sessions": not scan.eligible,
        "no_ambiguous_expired_recoveries": not recovery_scan.ambiguous,
        "no_recoverable_expired_sessions": not recovery_scan.recoverable,
        "no_ambiguous_active_payment_links": not payment_link_scan.ambiguous,
        "no_active_paid_pilot_payment_links": not payment_link_scan.relevant,
        "observability_readiness_evidenced": observability_readiness_evidenced,
        "pending_credential_trial_expiry_policy_implemented": (
            policy_implementation_evidenced
        ),
        "pending_credential_trial_expiry_policy_resolved": (
            pending_credential_trial_expiry_policy is not None
        ),
        "price_inventory_confirmed": price_inventory_confirmed,
        "restricted_portal_configuration_verified": True,
        "production_mode_verified": live_mode,
        "retired_price_inventory_verified": True,
        "reviewed_source_matches_deployed": reviewed_source_deployed,
        "secret_configuration_confirmed": secret_configuration_confirmed,
        "test_mode_lifecycle_evidenced": test_mode_lifecycle_evidenced,
        "webhook_endpoint_and_events_verified": True,
    }
    webhook_event_contract_sha256 = hashlib.sha256(
        "\n".join(sorted(EXPECTED_WEBHOOK_EVENTS)).encode()
    ).hexdigest()
    return {
        "activation_policy": ACTIVATION_POLICY_VERSION,
        "account_verified": True,
        "acquisition_disabled_confirmed": acquisition_disabled_confirmed,
        "activation_prerequisites": prerequisites,
        "ambiguous_cosmaneura_open_sessions": len(scan.ambiguous),
        "applied": applied,
        "checkout_policy": ACTIVATION_POLICY_VERSION,
        "checkout_price_ids": price_ids,
        "checked_at": checked_at.isoformat(),
        "completed_session_reconciliation_confirmed": (
            completed_session_reconciliation_confirmed
        ),
        "completed_session_reconciliation_at": (
            completed_session_reconciliation_at.isoformat()
            if completed_session_reconciliation_at is not None
            else None
        ),
        "completed_session_reconciliation_ref": (
            completed_session_reconciliation_ref
            if completed_session_reconciliation_recorded
            else None
        ),
        "completed_session_reconciliation_sha256": (
            completed_session_reconciliation_sha256
            if completed_session_reconciliation_recorded
            else None
        ),
        "current_offer": {
            "billing_scheme": EXPECTED_PRICE_BILLING_SCHEME,
            "currency": EXPECTED_PRICE_CURRENCY,
            "interval": EXPECTED_PRICE_INTERVAL,
            "interval_count": EXPECTED_PRICE_INTERVAL_COUNT,
            "unit_amount": EXPECTED_PRICE_UNIT_AMOUNT,
            "usage_type": EXPECTED_PRICE_USAGE_TYPE,
        },
        "current_offer_verified": True,
        "current_price_id": price_id,
        "entitlement_contract": {
            "policy_revision": ENTITLEMENT_POLICY_REVISION,
            "schema_sha256": ENTITLEMENT_SCHEMA_SHA256,
            "version": ENTITLEMENT_CONTRACT_VERSION,
        },
        "drain_complete_at": drain_complete_at.isoformat(),
        "drain_started_at": drain_started_at.isoformat(),
        "drain_satisfied": drain_satisfied,
        "eligible_cosmaneura_open_sessions": len(scan.eligible),
        "expired_sessions_scanned": recovery_scan.total_expired,
        "expired_session_pages_scanned": recovery_scan.pages,
        "recoverable_expired_cosmaneura_sessions": len(recovery_scan.recoverable),
        "ambiguous_expired_cosmaneura_sessions": len(recovery_scan.ambiguous),
        "expired_sessions": expired_count,
        "founder_owner_approval_confirmed": founder_owner_approval_confirmed,
        "owner_approval_ref": owner_approval_ref if owner_approval_recorded else None,
        "legacy_cosmaneura_open_sessions": scan.legacy_count,
        "live_mode": live_mode,
        "open_session_id_sha256": _session_id_digest((*scan.eligible, *scan.ambiguous)),
        "open_sessions_scanned": scan.total_open,
        "active_payment_links_scanned": payment_link_scan.total_active,
        "active_payment_link_pages_scanned": payment_link_scan.pages,
        "active_paid_pilot_payment_links": len(payment_link_scan.relevant),
        "ambiguous_active_payment_links": len(payment_link_scan.ambiguous),
        "old_code_stopped_confirmed": old_code_stopped_confirmed,
        "operator_ref": operator_ref,
        "observability_ready_confirmed": observability_ready_confirmed,
        "observability_evidence_ref": (
            observability_evidence_ref if observability_readiness_evidenced else None
        ),
        "observability_evidence_sha256": (
            observability_evidence_sha256 if observability_readiness_evidenced else None
        ),
        "pages_scanned": scan.pages,
        "pending_credential_trial_expiry_policy": (
            pending_credential_trial_expiry_policy
        ),
        "pending_credential_trial_expiry_policy_resolved": (
            pending_credential_trial_expiry_policy is not None
        ),
        "policy_implementation_evidence_ref": (
            policy_implementation_evidence_ref
            if policy_implementation_evidenced
            else None
        ),
        "policy_implementation_evidenced": policy_implementation_evidenced,
        "plan_sha256": plan_sha256,
        "price_inventory_confirmed": price_inventory_confirmed,
        "price_inventory_pages_scanned": price_inventory_verification.pages,
        "prices_scanned": price_inventory_verification.prices_scanned,
        "products_verified": price_inventory_verification.products,
        "product_price_count": price_inventory_verification.product_prices,
        "ready_for_acquisition": all(prerequisites.values()),
        "retired_price_count": len(legacy_price_ids),
        "retired_price_ids": legacy_price_ids,
        "retired_price_inventory_verified": True,
        "secret_configuration_confirmed": secret_configuration_confirmed,
        "test_mode_lifecycle_confirmed": test_mode_lifecycle_confirmed,
        "test_mode_lifecycle_evidence_ref": (
            test_mode_lifecycle_evidence_ref if test_mode_lifecycle_evidenced else None
        ),
        "test_mode_lifecycle_evidence_sha256": (
            test_mode_lifecycle_evidence_sha256
            if test_mode_lifecycle_evidenced
            else None
        ),
        "reviewed_source_sha": (
            reviewed_source_sha if reviewed_source_sha_valid else None
        ),
        "deployed_source_sha": deployed_source_sha
        if deployed_source_sha_valid
        else None,
        "reviewed_source_matches_deployed": reviewed_source_deployed,
        "stripe_account_id": account_id,
        "stripe_mode_verified": True,
        "stripe_product_id": product_id,
        "stripe_product_verified": True,
        "stripe_portal_configuration_id": portal_configuration_id,
        "stripe_portal_configuration_verified": True,
        "webhook_endpoint_verified": True,
        "webhook_endpoints_scanned": webhook_verification.endpoints_scanned,
        "webhook_event_contract_sha256": webhook_event_contract_sha256,
        "webhook_event_count": len(EXPECTED_WEBHOOK_EVENTS),
        "webhook_pages_scanned": webhook_verification.pages,
    }


def run_preflight(
    request: StripeRequest,
    *,
    secret_key: str,
    account_id: str,
    product_id: str,
    portal_configuration_id: str,
    price_id: str,
    legacy_price_ids: tuple[str, ...],
    live_mode: bool,
    operator_ref: str,
    drain_started_at: datetime,
    checked_at: datetime,
    apply: bool,
    confirmed_plan_sha256: str | None,
    acquisition_disabled_confirmed: bool,
    old_code_stopped_confirmed: bool,
    price_inventory_confirmed: bool,
    webhook_endpoint_url: str = "",
    secret_configuration_confirmed: bool = False,
    completed_session_reconciliation_confirmed: bool = False,
    completed_session_reconciliation_at: datetime | None = None,
    completed_session_reconciliation_ref: str = "",
    completed_session_reconciliation_sha256: str = "",
    test_mode_lifecycle_confirmed: bool = False,
    test_mode_lifecycle_evidence_ref: str = "",
    test_mode_lifecycle_evidence_sha256: str = "",
    observability_ready_confirmed: bool = False,
    observability_evidence_ref: str = "",
    observability_evidence_sha256: str = "",
    founder_owner_approval_confirmed: bool = False,
    owner_approval_ref: str = "",
    pending_credential_trial_expiry_policy: str | None = None,
    policy_implementation_evidence_ref: str = "",
    reviewed_source_sha: str = "",
    deployed_source_sha: str = "",
) -> dict[str, Any]:
    if apply and not (
        acquisition_disabled_confirmed
        and old_code_stopped_confirmed
        and price_inventory_confirmed
    ):
        raise PreflightError("all session-expiration safety confirmations are required")
    if apply and checked_at < drain_started_at + MINIMUM_DRAIN:
        raise PreflightError("the full Checkout Session drain has not elapsed")
    if (
        pending_credential_trial_expiry_policy is not None
        and pending_credential_trial_expiry_policy
        not in PENDING_CREDENTIAL_TRIAL_EXPIRY_POLICIES
    ):
        raise PreflightError(
            "Pending-credential trial-expiry policy is not an allowed value"
        )
    if not SAFE_REFERENCE.fullmatch(operator_ref):
        raise PreflightError("Operator reference is required")
    if (
        not account_id.startswith("acct_")
        or not product_id.startswith("prod_")
        or not price_id.startswith("price_")
        or not portal_configuration_id.startswith("bpc_")
    ):
        raise PreflightError(
            "Expected Stripe account, product, price, or portal configuration is invalid"
        )
    price_ids = (price_id, *legacy_price_ids)
    if len(price_ids) != len(set(price_ids)) or not all(
        candidate.startswith("price_") for candidate in price_ids
    ):
        raise PreflightError("Stripe price inventory is invalid")
    _verify_secret_mode(secret_key, live_mode=live_mode)
    _verify_expected_webhook_url(webhook_endpoint_url)
    price_inventory_verification = _verify_account_and_prices(
        request,
        secret_key,
        account_id=account_id,
        product_id=product_id,
        price_id=price_id,
        legacy_price_ids=legacy_price_ids,
        live_mode=live_mode,
    )
    webhook_verification = _verify_webhook_endpoint(
        request,
        secret_key,
        expected_url=webhook_endpoint_url,
        live_mode=live_mode,
    )
    _verify_portal_configuration(
        request,
        secret_key,
        configuration_id=portal_configuration_id,
        live_mode=live_mode,
    )
    initial = scan_open_sessions(
        request,
        secret_key,
        price_ids=frozenset(price_ids),
        live_mode=live_mode,
    )
    initial_recoveries = scan_expired_recoveries(
        request,
        secret_key,
        price_ids=frozenset(price_ids),
        checked_at=checked_at,
    )
    initial_payment_links = scan_active_payment_links(
        request,
        secret_key,
        price_ids=frozenset(price_ids),
    )
    initial_evidence = _evidence(
        operator_ref=operator_ref,
        account_id=account_id,
        product_id=product_id,
        portal_configuration_id=portal_configuration_id,
        price_id=price_id,
        legacy_price_ids=legacy_price_ids,
        live_mode=live_mode,
        drain_started_at=drain_started_at,
        checked_at=checked_at,
        scan=initial,
        recovery_scan=initial_recoveries,
        payment_link_scan=initial_payment_links,
        applied=False,
        expired_count=0,
        price_inventory_verification=price_inventory_verification,
        webhook_verification=webhook_verification,
        webhook_endpoint_url=webhook_endpoint_url,
        acquisition_disabled_confirmed=acquisition_disabled_confirmed,
        old_code_stopped_confirmed=old_code_stopped_confirmed,
        price_inventory_confirmed=price_inventory_confirmed,
        secret_configuration_confirmed=secret_configuration_confirmed,
        completed_session_reconciliation_confirmed=(
            completed_session_reconciliation_confirmed
        ),
        completed_session_reconciliation_at=completed_session_reconciliation_at,
        completed_session_reconciliation_ref=completed_session_reconciliation_ref,
        completed_session_reconciliation_sha256=(
            completed_session_reconciliation_sha256
        ),
        test_mode_lifecycle_confirmed=test_mode_lifecycle_confirmed,
        test_mode_lifecycle_evidence_ref=test_mode_lifecycle_evidence_ref,
        test_mode_lifecycle_evidence_sha256=test_mode_lifecycle_evidence_sha256,
        observability_ready_confirmed=observability_ready_confirmed,
        observability_evidence_ref=observability_evidence_ref,
        observability_evidence_sha256=observability_evidence_sha256,
        founder_owner_approval_confirmed=founder_owner_approval_confirmed,
        owner_approval_ref=owner_approval_ref,
        pending_credential_trial_expiry_policy=(pending_credential_trial_expiry_policy),
        policy_implementation_evidence_ref=policy_implementation_evidence_ref,
        reviewed_source_sha=reviewed_source_sha,
        deployed_source_sha=deployed_source_sha,
    )
    if not apply:
        return initial_evidence
    if (
        initial.ambiguous
        or initial_recoveries.ambiguous
        or initial_recoveries.recoverable
        or initial_payment_links.ambiguous
        or initial_payment_links.relevant
    ):
        raise PreflightError("ambiguous or renewable Checkout surfaces require review")
    if not confirmed_plan_sha256 or not hmac.compare_digest(
        confirmed_plan_sha256, str(initial_evidence["plan_sha256"])
    ):
        raise PreflightError("confirmed plan digest does not match the current scan")
    for session in initial.eligible:
        _expire_session(request, secret_key, session, live_mode=live_mode)
    final_price_inventory_verification = _verify_account_and_prices(
        request,
        secret_key,
        account_id=account_id,
        product_id=product_id,
        price_id=price_id,
        legacy_price_ids=legacy_price_ids,
        live_mode=live_mode,
    )
    final_webhook_verification = _verify_webhook_endpoint(
        request,
        secret_key,
        expected_url=webhook_endpoint_url,
        live_mode=live_mode,
    )
    _verify_portal_configuration(
        request,
        secret_key,
        configuration_id=portal_configuration_id,
        live_mode=live_mode,
    )
    final = scan_open_sessions(
        request,
        secret_key,
        price_ids=frozenset(price_ids),
        live_mode=live_mode,
    )
    final_recoveries = scan_expired_recoveries(
        request,
        secret_key,
        price_ids=frozenset(price_ids),
        checked_at=checked_at,
    )
    final_payment_links = scan_active_payment_links(
        request,
        secret_key,
        price_ids=frozenset(price_ids),
    )
    final_evidence = _evidence(
        operator_ref=operator_ref,
        account_id=account_id,
        product_id=product_id,
        portal_configuration_id=portal_configuration_id,
        price_id=price_id,
        legacy_price_ids=legacy_price_ids,
        live_mode=live_mode,
        drain_started_at=drain_started_at,
        checked_at=checked_at,
        scan=final,
        recovery_scan=final_recoveries,
        payment_link_scan=final_payment_links,
        applied=True,
        expired_count=len(initial.eligible),
        price_inventory_verification=final_price_inventory_verification,
        webhook_verification=final_webhook_verification,
        webhook_endpoint_url=webhook_endpoint_url,
        acquisition_disabled_confirmed=acquisition_disabled_confirmed,
        old_code_stopped_confirmed=old_code_stopped_confirmed,
        price_inventory_confirmed=price_inventory_confirmed,
        secret_configuration_confirmed=secret_configuration_confirmed,
        completed_session_reconciliation_confirmed=(
            completed_session_reconciliation_confirmed
        ),
        completed_session_reconciliation_at=completed_session_reconciliation_at,
        completed_session_reconciliation_ref=completed_session_reconciliation_ref,
        completed_session_reconciliation_sha256=(
            completed_session_reconciliation_sha256
        ),
        test_mode_lifecycle_confirmed=test_mode_lifecycle_confirmed,
        test_mode_lifecycle_evidence_ref=test_mode_lifecycle_evidence_ref,
        test_mode_lifecycle_evidence_sha256=test_mode_lifecycle_evidence_sha256,
        observability_ready_confirmed=observability_ready_confirmed,
        observability_evidence_ref=observability_evidence_ref,
        observability_evidence_sha256=observability_evidence_sha256,
        founder_owner_approval_confirmed=founder_owner_approval_confirmed,
        owner_approval_ref=owner_approval_ref,
        pending_credential_trial_expiry_policy=(pending_credential_trial_expiry_policy),
        policy_implementation_evidence_ref=policy_implementation_evidence_ref,
        reviewed_source_sha=reviewed_source_sha,
        deployed_source_sha=deployed_source_sha,
    )
    final_evidence["applied_plan_sha256"] = initial_evidence["plan_sha256"]
    return final_evidence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operator-ref", required=True)
    parser.add_argument("--drain-started-at", required=True)
    parser.add_argument("--expected-mode", choices=("test", "live"), required=True)
    parser.add_argument("--confirm-acquisition-disabled", action="store_true")
    parser.add_argument("--confirm-old-code-stopped", action="store_true")
    parser.add_argument("--confirm-price-inventory-complete", action="store_true")
    parser.add_argument("--confirm-secret-configuration", action="store_true")
    parser.add_argument(
        "--confirm-completed-session-reconciliation", action="store_true"
    )
    parser.add_argument("--completed-session-reconciliation-at")
    parser.add_argument("--completed-session-reconciliation-ref", default="")
    parser.add_argument("--completed-session-reconciliation-sha256", default="")
    parser.add_argument("--confirm-test-mode-lifecycle", action="store_true")
    parser.add_argument("--test-mode-lifecycle-evidence-ref", default="")
    parser.add_argument("--test-mode-lifecycle-evidence-sha256", default="")
    parser.add_argument("--confirm-observability-ready", action="store_true")
    parser.add_argument("--observability-evidence-ref", default="")
    parser.add_argument("--observability-evidence-sha256", default="")
    parser.add_argument("--confirm-founder-owner-approval", action="store_true")
    parser.add_argument("--owner-approval-ref", default="")
    parser.add_argument(
        "--pending-credential-trial-expiry-policy",
        choices=tuple(sorted(PENDING_CREDENTIAL_TRIAL_EXPIRY_POLICIES)),
    )
    parser.add_argument("--policy-implementation-evidence-ref", default="")
    parser.add_argument("--reviewed-source-sha", default="")
    parser.add_argument("--deployed-source-sha", default="")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-plan-sha256")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    secret_key = os.environ.get("STRIPE_SECRET_KEY", "")
    account_id = os.environ.get("STRIPE_ACCOUNT_ID", "")
    product_id = os.environ.get("STRIPE_PRODUCT_ID", "")
    price_id = os.environ.get("STRIPE_PRICE_ID", "")
    portal_configuration_id = os.environ.get("STRIPE_PORTAL_CONFIGURATION_ID", "")
    webhook_endpoint_url = os.environ.get("STRIPE_WEBHOOK_ENDPOINT_URL", "")
    retired_price_source = os.environ.get("STRIPE_RETIRED_PRICE_IDS")
    legacy_price_source = os.environ.get("STRIPE_LEGACY_PRICE_IDS")
    if (
        retired_price_source is not None
        and legacy_price_source is not None
        and retired_price_source != legacy_price_source
    ):
        print("Stripe retired price inventory is ambiguous", file=sys.stderr)
        return 2
    raw_retired_price_ids = (
        retired_price_source
        if retired_price_source is not None
        else legacy_price_source or ""
    )
    retired_price_ids = tuple(
        sorted(
            {
                candidate.strip()
                for candidate in raw_retired_price_ids.split(",")
                if candidate.strip()
            }
        )
    )
    if not all(
        (
            secret_key,
            account_id,
            product_id,
            price_id,
            portal_configuration_id,
            webhook_endpoint_url,
        )
    ):
        print(
            "Stripe activation preflight configuration is incomplete", file=sys.stderr
        )
        return 2
    if not args.operator_ref.strip():
        print("operator reference is required", file=sys.stderr)
        return 2
    try:
        evidence = run_preflight(
            _stripe_request,
            secret_key=secret_key,
            account_id=account_id,
            product_id=product_id,
            portal_configuration_id=portal_configuration_id,
            price_id=price_id,
            legacy_price_ids=retired_price_ids,
            live_mode=args.expected_mode == "live",
            operator_ref=args.operator_ref.strip(),
            drain_started_at=_parse_utc_timestamp(
                args.drain_started_at, label="drain start"
            ),
            checked_at=datetime.now(UTC),
            apply=args.apply,
            confirmed_plan_sha256=args.confirm_plan_sha256,
            acquisition_disabled_confirmed=args.confirm_acquisition_disabled,
            old_code_stopped_confirmed=args.confirm_old_code_stopped,
            price_inventory_confirmed=args.confirm_price_inventory_complete,
            webhook_endpoint_url=webhook_endpoint_url,
            secret_configuration_confirmed=args.confirm_secret_configuration,
            completed_session_reconciliation_confirmed=(
                args.confirm_completed_session_reconciliation
            ),
            completed_session_reconciliation_at=(
                _parse_utc_timestamp(
                    args.completed_session_reconciliation_at,
                    label="completed-session reconciliation time",
                )
                if args.completed_session_reconciliation_at
                else None
            ),
            completed_session_reconciliation_ref=(
                args.completed_session_reconciliation_ref
            ),
            completed_session_reconciliation_sha256=(
                args.completed_session_reconciliation_sha256.lower()
            ),
            test_mode_lifecycle_confirmed=args.confirm_test_mode_lifecycle,
            test_mode_lifecycle_evidence_ref=args.test_mode_lifecycle_evidence_ref,
            test_mode_lifecycle_evidence_sha256=(
                args.test_mode_lifecycle_evidence_sha256.lower()
            ),
            observability_ready_confirmed=args.confirm_observability_ready,
            observability_evidence_ref=args.observability_evidence_ref,
            observability_evidence_sha256=(args.observability_evidence_sha256.lower()),
            founder_owner_approval_confirmed=(args.confirm_founder_owner_approval),
            owner_approval_ref=args.owner_approval_ref,
            pending_credential_trial_expiry_policy=(
                args.pending_credential_trial_expiry_policy
            ),
            policy_implementation_evidence_ref=(
                args.policy_implementation_evidence_ref
            ),
            reviewed_source_sha=args.reviewed_source_sha.lower(),
            deployed_source_sha=args.deployed_source_sha.lower(),
        )
    except PreflightError:
        print("Stripe activation preflight failed", file=sys.stderr)
        return 2
    print(json.dumps(evidence, sort_keys=True, separators=(",", ":")))
    return 0 if evidence["ready_for_acquisition"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
