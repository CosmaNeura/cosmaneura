#!/usr/bin/env python3
"""Audit and drain open CosmaNeura Checkout Sessions before acquisition.

This command is intentionally standalone: it talks only to Stripe and does not
import the application or mutate its database. Run it after the card-required
code is deployed with acquisition disabled and the last old revision has been
out of service for the full drain interval. Output is aggregate, deterministic
JSON suitable for attaching to the deployment change record; raw session and
practice identifiers are never printed.

Dry run:

    STRIPE_SECRET_KEY=... STRIPE_ACCOUNT_ID=... STRIPE_PRICE_ID=... \
      STRIPE_LEGACY_PRICE_IDS=price_old_1,price_old_2 \
      python scripts/stripe_checkout_activation_preflight.py \
      --operator-ref CHANGE-123 \
      --drain-started-at 2026-08-24T12:00:00Z \
      --live-mode \
      --confirm-acquisition-disabled \
      --confirm-old-code-stopped \
      --confirm-price-inventory-complete

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
CURRENT_POLICY_VERSION = "card-required-v1"
MAX_CHECKOUT_SESSION_LIFETIME = timedelta(hours=24)
DRAIN_SAFETY_BUFFER = timedelta(minutes=5)
MINIMUM_DRAIN = MAX_CHECKOUT_SESSION_LIFETIME + DRAIN_SAFETY_BUFFER
MAX_PAGES = 10_000
PRACTICE_REFERENCE = re.compile(r"^practice_[0-9a-hjkmnp-tv-z]{26}$")


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


def _valid_line_items(session: dict[str, Any], *, price_ids: frozenset[str]) -> bool:
    line_items = session.get("line_items")
    if not isinstance(line_items, dict):
        return False
    data = line_items.get("data")
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        return False
    price = data[0].get("price")
    returned_price_id = price.get("id") if isinstance(price, dict) else price
    quantity = data[0].get("quantity")
    return (
        returned_price_id in price_ids and isinstance(quantity, int) and quantity >= 1
    )


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
            uses_cosmaneura_price = _valid_line_items(session, price_ids=price_ids)
            # Pre-practice website Checkout did not have a practice reference.
            # The account's exact configured CosmaNeura price is therefore a
            # second ownership signal. The exact configured price is sufficient
            # for the retired direct flow, regardless of its reference shape.
            # Anything with either signal but not all safe-expiration invariants
            # is surfaced as ambiguous, never ignored.
            if not valid_reference and not uses_cosmaneura_price:
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


def _session_id_digest(sessions: tuple[dict[str, Any], ...]) -> str:
    ids = sorted(str(session["id"]) for session in sessions)
    return hashlib.sha256("\n".join(ids).encode()).hexdigest()


def _plan_digest(
    *,
    account_id: str,
    price_ids: tuple[str, ...],
    live_mode: bool,
    drain_started_at: datetime,
    scan: SessionScan,
) -> str:
    payload = {
        "account_id": account_id,
        "checkout_policy": CURRENT_POLICY_VERSION,
        "drain_started_at": drain_started_at.isoformat(),
        "eligible_session_ids": sorted(str(session["id"]) for session in scan.eligible),
        "live_mode": live_mode,
        "price_ids": price_ids,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _parse_utc_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PreflightError("drain start must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise PreflightError("drain start must include a timezone")
    return parsed.astimezone(UTC)


def _verify_account_and_prices(
    request: StripeRequest,
    secret_key: str,
    *,
    account_id: str,
    price_id: str,
    legacy_price_ids: tuple[str, ...],
    live_mode: bool,
) -> None:
    account = request(secret_key, "GET", "/account", None, None)
    if account.get("id") != account_id:
        raise PreflightError("Stripe key resolved to the wrong account")
    for candidate in (price_id, *legacy_price_ids):
        price = request(secret_key, "GET", f"/prices/{candidate}", None, None)
        if (
            price.get("id") != candidate
            or price.get("livemode") is not live_mode
            or (candidate == price_id and price.get("active") is not True)
        ):
            raise PreflightError(
                "Stripe price inventory does not match the requested environment"
            )


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
    price_id: str,
    legacy_price_ids: tuple[str, ...],
    live_mode: bool,
    drain_started_at: datetime,
    checked_at: datetime,
    scan: SessionScan,
    applied: bool,
    expired_count: int,
    acquisition_disabled_confirmed: bool,
    old_code_stopped_confirmed: bool,
    price_inventory_confirmed: bool,
) -> dict[str, Any]:
    price_ids = (price_id, *legacy_price_ids)
    drain_complete_at = drain_started_at + MINIMUM_DRAIN
    drain_satisfied = checked_at >= drain_complete_at
    plan_sha256 = _plan_digest(
        account_id=account_id,
        price_ids=price_ids,
        live_mode=live_mode,
        drain_started_at=drain_started_at,
        scan=scan,
    )
    return {
        "account_verified": True,
        "acquisition_disabled_confirmed": acquisition_disabled_confirmed,
        "ambiguous_cosmaneura_open_sessions": len(scan.ambiguous),
        "applied": applied,
        "checkout_policy": CURRENT_POLICY_VERSION,
        "checkout_price_ids": price_ids,
        "checked_at": checked_at.isoformat(),
        "drain_complete_at": drain_complete_at.isoformat(),
        "drain_started_at": drain_started_at.isoformat(),
        "drain_satisfied": drain_satisfied,
        "eligible_cosmaneura_open_sessions": len(scan.eligible),
        "expired_sessions": expired_count,
        "legacy_cosmaneura_open_sessions": scan.legacy_count,
        "live_mode": live_mode,
        "open_session_id_sha256": _session_id_digest((*scan.eligible, *scan.ambiguous)),
        "open_sessions_scanned": scan.total_open,
        "old_code_stopped_confirmed": old_code_stopped_confirmed,
        "operator_ref": operator_ref,
        "pages_scanned": scan.pages,
        "plan_sha256": plan_sha256,
        "price_inventory_confirmed": price_inventory_confirmed,
        "ready_for_acquisition": (
            acquisition_disabled_confirmed
            and old_code_stopped_confirmed
            and price_inventory_confirmed
            and drain_satisfied
            and scan.relevant_count == 0
        ),
        "stripe_account_id": account_id,
    }


def run_preflight(
    request: StripeRequest,
    *,
    secret_key: str,
    account_id: str,
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
) -> dict[str, Any]:
    if not (
        acquisition_disabled_confirmed
        and old_code_stopped_confirmed
        and price_inventory_confirmed
    ):
        raise PreflightError("all activation safety confirmations are required")
    price_ids = (price_id, *legacy_price_ids)
    if len(price_ids) != len(set(price_ids)) or not all(
        candidate.startswith("price_") for candidate in price_ids
    ):
        raise PreflightError("Stripe price inventory is invalid")
    _verify_account_and_prices(
        request,
        secret_key,
        account_id=account_id,
        price_id=price_id,
        legacy_price_ids=legacy_price_ids,
        live_mode=live_mode,
    )
    initial = scan_open_sessions(
        request,
        secret_key,
        price_ids=frozenset(price_ids),
        live_mode=live_mode,
    )
    initial_evidence = _evidence(
        operator_ref=operator_ref,
        account_id=account_id,
        price_id=price_id,
        legacy_price_ids=legacy_price_ids,
        live_mode=live_mode,
        drain_started_at=drain_started_at,
        checked_at=checked_at,
        scan=initial,
        applied=False,
        expired_count=0,
        acquisition_disabled_confirmed=acquisition_disabled_confirmed,
        old_code_stopped_confirmed=old_code_stopped_confirmed,
        price_inventory_confirmed=price_inventory_confirmed,
    )
    if not apply:
        return initial_evidence
    if initial.ambiguous:
        raise PreflightError("ambiguous CosmaNeura Checkout Sessions require review")
    if not confirmed_plan_sha256 or not hmac.compare_digest(
        confirmed_plan_sha256, str(initial_evidence["plan_sha256"])
    ):
        raise PreflightError("confirmed plan digest does not match the current scan")
    for session in initial.eligible:
        _expire_session(request, secret_key, session, live_mode=live_mode)
    final = scan_open_sessions(
        request,
        secret_key,
        price_ids=frozenset(price_ids),
        live_mode=live_mode,
    )
    final_evidence = _evidence(
        operator_ref=operator_ref,
        account_id=account_id,
        price_id=price_id,
        legacy_price_ids=legacy_price_ids,
        live_mode=live_mode,
        drain_started_at=drain_started_at,
        checked_at=checked_at,
        scan=final,
        applied=True,
        expired_count=len(initial.eligible),
        acquisition_disabled_confirmed=acquisition_disabled_confirmed,
        old_code_stopped_confirmed=old_code_stopped_confirmed,
        price_inventory_confirmed=price_inventory_confirmed,
    )
    final_evidence["applied_plan_sha256"] = initial_evidence["plan_sha256"]
    return final_evidence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operator-ref", required=True)
    parser.add_argument("--drain-started-at", required=True)
    parser.add_argument("--live-mode", action="store_true")
    parser.add_argument("--confirm-acquisition-disabled", action="store_true")
    parser.add_argument("--confirm-old-code-stopped", action="store_true")
    parser.add_argument("--confirm-price-inventory-complete", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-plan-sha256")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    secret_key = os.environ.get("STRIPE_SECRET_KEY", "")
    account_id = os.environ.get("STRIPE_ACCOUNT_ID", "")
    price_id = os.environ.get("STRIPE_PRICE_ID", "")
    legacy_price_ids = tuple(
        sorted(
            {
                candidate.strip()
                for candidate in os.environ.get("STRIPE_LEGACY_PRICE_IDS", "").split(
                    ","
                )
                if candidate.strip()
            }
        )
    )
    if not secret_key or not account_id or not price_id:
        print(
            "Stripe activation preflight configuration is incomplete", file=sys.stderr
        )
        return 2
    if not args.operator_ref.strip():
        print("operator reference is required", file=sys.stderr)
        return 2
    if not (
        args.confirm_acquisition_disabled
        and args.confirm_old_code_stopped
        and args.confirm_price_inventory_complete
    ):
        print(
            "confirm acquisition is disabled, all old code is out of service, "
            "and the current/legacy price inventory is complete",
            file=sys.stderr,
        )
        return 2
    try:
        evidence = run_preflight(
            _stripe_request,
            secret_key=secret_key,
            account_id=account_id,
            price_id=price_id,
            legacy_price_ids=legacy_price_ids,
            live_mode=args.live_mode,
            operator_ref=args.operator_ref.strip(),
            drain_started_at=_parse_utc_timestamp(args.drain_started_at),
            checked_at=datetime.now(UTC),
            apply=args.apply,
            confirmed_plan_sha256=args.confirm_plan_sha256,
            acquisition_disabled_confirmed=args.confirm_acquisition_disabled,
            old_code_stopped_confirmed=args.confirm_old_code_stopped,
            price_inventory_confirmed=args.confirm_price_inventory_complete,
        )
    except PreflightError:
        print("Stripe activation preflight failed", file=sys.stderr)
        return 2
    print(json.dumps(evidence, sort_keys=True, separators=(",", ":")))
    return 0 if evidence["ready_for_acquisition"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
