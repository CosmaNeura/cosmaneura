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
        "livemode": False,
        "mode": "subscription",
        "payment_method_collection": policy,
        "status": "open",
        "subscription": subscription,
        "url": f"https://checkout.stripe.com/c/pay/{session_id}",
    }


class FakeStripe:
    def __init__(self, sessions: list[dict[str, Any]]) -> None:
        self.sessions = {str(session["id"]): deepcopy(session) for session in sessions}
        self.calls: list[tuple[str, str, list[tuple[str, str]] | None, str | None]] = []

    def __call__(
        self,
        secret_key: str,
        method: str,
        path: str,
        params: list[tuple[str, str]] | None,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        assert secret_key == "sk_test_not_printed"
        self.calls.append((method, path, params, idempotency_key))
        if path == "/account":
            return {"id": "acct_test_cosmaneura"}
        if path.startswith("/prices/"):
            price_id = path.rsplit("/", 1)[-1]
            return {
                "id": price_id,
                "livemode": False,
                "active": price_id == "price_test_x",
            }
        if path == "/checkout/sessions":
            open_sessions = [
                deepcopy(session)
                for session in self.sessions.values()
                if session["status"] == "open"
            ]
            starting_after = dict(params or []).get("starting_after")
            if starting_after:
                index = next(
                    index
                    for index, session in enumerate(open_sessions)
                    if session["id"] == starting_after
                )
                open_sessions = open_sessions[index + 1 :]
            return {"data": open_sessions[:1], "has_more": len(open_sessions) > 1}
        if path.startswith("/checkout/sessions/") and path.endswith("/expire"):
            session_id = path.removesuffix("/expire").rsplit("/", 1)[-1]
            session = self.sessions[session_id]
            session["status"] = "expired"
            session["url"] = None
            return deepcopy(session)
        raise AssertionError(f"unexpected request: {method} {path}")


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
        [legacy, current, direct_legacy, direct_legacy_unusual_reference, unrelated]
    )

    evidence = preflight.run_preflight(
        fake,
        secret_key="sk_test_not_printed",
        account_id="acct_test_cosmaneura",
        price_id="price_test_x",
        legacy_price_ids=("price_legacy_website",),
        live_mode=False,
        operator_ref="CHANGE-123",
        drain_started_at=NOW - timedelta(hours=25),
        checked_at=NOW,
        apply=False,
        confirmed_plan_sha256=None,
        acquisition_disabled_confirmed=True,
        old_code_stopped_confirmed=True,
        price_inventory_confirmed=True,
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
    serialized = json.dumps(evidence)
    assert "cs_legacy" not in serialized
    assert PRACTICE_ID not in serialized


def test_apply_requires_exact_plan_then_expires_and_rescans_all_open_sessions() -> None:
    preflight = _load_preflight()
    fake = FakeStripe([_session("cs_legacy"), _session("cs_current", policy="always")])
    common = {
        "secret_key": "sk_test_not_printed",
        "account_id": "acct_test_cosmaneura",
        "price_id": "price_test_x",
        "legacy_price_ids": (),
        "live_mode": False,
        "operator_ref": "CHANGE-123",
        "drain_started_at": NOW - timedelta(hours=25),
        "checked_at": NOW,
        "acquisition_disabled_confirmed": True,
        "old_code_stopped_confirmed": True,
        "price_inventory_confirmed": True,
    }
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
    common = {
        "secret_key": "sk_test_not_printed",
        "account_id": "acct_test_cosmaneura",
        "price_id": "price_test_x",
        "legacy_price_ids": (),
        "live_mode": False,
        "operator_ref": "CHANGE-123",
        "drain_started_at": NOW - timedelta(hours=25),
        "checked_at": NOW,
        "acquisition_disabled_confirmed": True,
        "old_code_stopped_confirmed": True,
        "price_inventory_confirmed": True,
    }
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


def test_zero_open_sessions_still_requires_full_maximum_lifetime_drain() -> None:
    preflight = _load_preflight()
    fake = FakeStripe([])

    evidence = preflight.run_preflight(
        fake,
        secret_key="sk_test_not_printed",
        account_id="acct_test_cosmaneura",
        price_id="price_test_x",
        legacy_price_ids=(),
        live_mode=False,
        operator_ref="CHANGE-123",
        drain_started_at=NOW - timedelta(hours=23),
        checked_at=NOW,
        apply=False,
        confirmed_plan_sha256=None,
        acquisition_disabled_confirmed=True,
        old_code_stopped_confirmed=True,
        price_inventory_confirmed=True,
    )

    assert evidence["eligible_cosmaneura_open_sessions"] == 0
    assert evidence["drain_satisfied"] is False
    assert evidence["ready_for_acquisition"] is False
