"""Actual entitlement methods and extracted runtime bodies, with synthetic I/O."""

from __future__ import annotations
import __future__

import ast
import asyncio
import copy
import json
from collections.abc import Iterable
from dataclasses import dataclass  # noqa: F401 - compiled runtime globals
from datetime import datetime, timedelta, timezone
from enum import Enum  # noqa: F401 - compiled runtime globals
from pathlib import Path
from types import SimpleNamespace
from typing import (
    Any,
    cast,  # noqa: F401 - compiled runtime globals
)
from unittest.mock import Mock

import pytest
from fastapi import HTTPException, status  # noqa: F401 - compiled runtime globals
from starlette.responses import JSONResponse

from app.models.practice import Practice as EntitlementPractice

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 16, tzinfo=timezone.utc)


def load(path: str, names: Iterable[str], namespace: dict[str, Any], scope=None):
    tree = ast.parse((ROOT / path).read_text())
    body = tree.body
    if scope:
        body = next(
            n.body for n in body if isinstance(n, ast.ClassDef) and n.name == scope
        )
    selected = []
    for name in names:
        matches = [n for n in body if getattr(n, "name", None) == name]
        assert len(matches) == 1, f"source contract changed: {path}:{name}"
        node = copy.deepcopy(matches[0])
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            node.decorator_list = []  # route registration is an I/O boundary
        selected.append(node)
    module = ast.Module(body=selected, type_ignores=[])
    exec(
        compile(
            ast.fix_missing_locations(module),
            path,
            "exec",
            flags=__future__.annotations.compiler_flag,
        ),
        namespace,
    )


class Query:
    def __init__(self, target):
        self.target = target

    def where(self, *args):
        return self

    with_for_update = where


class Rows:
    def __init__(self, row):
        self.row = row

    def one(self):
        assert self.row is not None
        return self.row

    def one_or_none(self):
        return self.row


class SyntheticPractice(SimpleNamespace):
    id = "practice_test_synthetic"
    stripe_subscription_id = "sub_test_synthetic"


class SyntheticInbox(SimpleNamespace):
    event_id = "evt_test_synthetic"
    id = "inbox_test_synthetic"


class Session:
    def __init__(self, practice):
        self.practice = practice
        self.added = []

    def exec(self, query):
        return Rows(None if query.target is SyntheticInbox else self.practice)

    def add(self, row):
        self.added.append(row)

    def commit(self, *args):
        pass

    refresh = commit

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def harness(kind: str):
    namespace: dict[str, Any] = {
        **globals(),
        "log": Mock(),
        "select": Query,
        "Practice": SyntheticPractice,
        "StripeEventInbox": SyntheticInbox,
        "IntegrityError": type("SyntheticIntegrityError", (Exception,), {}),
        "StripeStateInvalid": type("SyntheticStripeStateInvalid", (Exception,), {}),
        "StripeSeatLimitExceeded": type("SyntheticSeatLimit", (Exception,), {}),
        "StripeSeatLedgerInvalid": type("SyntheticSeatLedger", (Exception,), {}),
    }
    config = {
        "STRIPE_INTEGRATION_ENABLED": False,
        "STRIPE_BILLING_ENABLED": False,
        "STRIPE_LIVE_MODE": False,
        "STRIPE_SECRET_KEY": "sk_test_synthetic",
        "STRIPE_WEBHOOK_SECRET": "whsec_synthetic",
        "STRIPE_ACCOUNT_ID": "acct_test_synthetic",
        "STRIPE_PRICE_ID": "price_test_synthetic",
        "STRIPE_MCD_PRICE_ID": "price_test_mcd_synthetic",
        "STRIPE_PORTAL_CONFIGURATION_ID": "bpc_test_synthetic",
        "STRIPE_API_VERSION": "2025-09-30.clover",
        "SUPPORTED_STRIPE_API_VERSION": "2025-09-30.clover",
        "ACCEPTED_EVENT_API_VERSIONS": {"2025-09-30.clover"},
    }
    load(
        "app/configuration/stripe.py",
        "_secret_mode_matches stripe_runtime_configured stripe_webhook_configured stripe_portal_configured stripe_billing_configured".split(),
        config,
    )
    namespace["stripe_config"] = SimpleNamespace(**config)
    practice = SyntheticPractice(
        id="practice_test_synthetic",
        subscription_status=None,
        billing_exempt=kind in {"exempt", "exempt_bound"},
        billing_exempt_until=None,
        trial_ends_at=NOW + timedelta(days=5) if kind == "legacy_trial" else None,
        acquisition_source="managed",
        stripe_customer_id=None,
        stripe_subscription_id=None,
        stripe_state_version=0,
        stripe_last_event_created_at=None,
        stripe_quantity_sync_required=False,
        stripe_quantity_sync_target=None,
        stripe_quantity_sync_error=None,
        negotiated_baa_required=False,
        organization_id=None,
    )
    if kind in {"paid", "exempt_bound"}:
        practice.subscription_status = "active"
        practice.stripe_customer_id = "cus_test_synthetic"
        practice.stripe_subscription_id = "sub_test_synthetic"
        practice.stripe_state_version = 1
    for name in "billing_exemption_active has_authoritative_stripe_subscription_binding entitled".split():
        setattr(practice, name, getattr(EntitlementPractice, name).__get__(practice))
    practice.baa_in_force = lambda **kwargs: True
    practice.refresh = lambda: None
    pilot = (
        SimpleNamespace(practice_id_snapshot=practice.id)
        if kind == "legacy_pilot"
        else None
    )
    doctor = SimpleNamespace(onboarding_completed_at=NOW - timedelta(days=30))
    member = SimpleNamespace()
    namespace.update(
        {
            "active_pilot_access_grant": lambda *args, **kwargs: pilot,
            "member_authorization_basis": lambda *args,
            **kwargs: "workforce_attestation",
            "member_access_state": lambda *args, **kwargs: "active",
            "MemberAccessState": SimpleNamespace(
                permanently_revoked="revoked", revoked="inactive"
            ),
        }
    )
    load(
        "app/services/doctor_access.py",
        "DoctorAccessDecision _denied _membership_tombstone _has_valid_legacy_pilot_access _decide".split(),
        namespace,
    )

    def decide(**kwargs):
        return namespace["_decide"](
            doctor,
            member,
            practice,
            require_entitlement=kwargs.get("require_entitlement", True),
            at=NOW,
        )

    namespace.update(
        {
            "resolve_doctor_access": lambda *args, **kwargs: decide(**kwargs),
            "_session_prep_active_account_route": lambda *args: False,
            "EXEMPT_ROUTE_NAMES": set(),
            "ADMIN_PATH_PREFIX": "/internal/v1/admin",
            "ENTITLEMENT_EXEMPT_ROUTE_NAMES": {
                "create_billing_checkout",
                "create_billing_portal",
            },
            "CANCELLATION_READ_ROUTE_NAMES": set(),
        }
    )
    load(
        "app/routes/dependencies/practice_gate.py",
        ("require_practice_in_force",),
        namespace,
    )
    return namespace, config, practice, decide


def flags(namespace, config, enabled):
    config["STRIPE_INTEGRATION_ENABLED"] = enabled
    config["STRIPE_BILLING_ENABLED"] = enabled
    namespace["stripe_config"].STRIPE_INTEGRATION_ENABLED = enabled
    namespace["stripe_config"].STRIPE_BILLING_ENABLED = enabled


def clinical_gate(namespace):
    namespace["require_practice_in_force"](
        SimpleNamespace(
            scope={"route": SimpleNamespace(name="note_create")},
            url=SimpleNamespace(path="/internal/v1/notes"),
            method="POST",
            state=SimpleNamespace(doctor=SimpleNamespace()),
        )
    )


@pytest.mark.parametrize(
    "kind,allowed",
    [
        ("exempt", True),
        ("legacy_pilot", True),
        ("paid", True),
        ("legacy_trial", True),
        ("never_paid", False),
    ],
)
def test_flags_alone_preserve_current_access_and_stored_state(kind, allowed):
    namespace, config, practice, decide = harness(kind)
    before = decide()
    stored = dict(vars(practice))
    flags(namespace, config, True)
    assert config["stripe_billing_configured"]() is True
    assert config["stripe_runtime_configured"]() is True
    assert decide() == before and before.allowed is allowed
    assert vars(practice) == stored
    if allowed:
        clinical_gate(namespace)
    else:
        with pytest.raises(HTTPException) as caught:
            clinical_gate(namespace)
        assert caught.value.status_code == 402  # pre-existing, with flags off too


def deliver_failed_invoice(namespace, config, practice):
    invoice_types = set(
        "invoice.payment_failed invoice.paid invoice.payment_succeeded".split()
    )
    namespace.update(
        _isolated_state_session=lambda: Session(practice),
        INVOICE_EVENT_TYPES=invoice_types,
        MIRRORED_EVENT_TYPES=invoice_types,
    )
    load(
        "app/services/stripe_billing.py",
        "_validate_livemode _stripe_timestamp _event_created_at _stripe_reference_id _invoice_event_details _event_subscription_id validate_stripe_event_envelope _minimal_stripe_event enqueue_stripe_event".split(),
        namespace,
    )
    namespace.update(
        {
            "stripe_billing": SimpleNamespace(
                verify_webhook_signature=lambda *args: True,
                enqueue_stripe_event=namespace["enqueue_stripe_event"],
            ),
            "JSONResponse": JSONResponse,
            "WebhookAck": SimpleNamespace,
            "_publish_stripe_inbox": Mock(),
        }
    )

    async def threadpool(callback, *args):
        return callback(*args)

    namespace["run_in_threadpool"] = threadpool
    load("app/routes/webhooks.py", ("stripe_webhook",), namespace)
    event = {
        "id": "evt_test_synthetic",
        "type": "invoice.payment_failed",
        "created": int(NOW.timestamp()),
        "api_version": config["STRIPE_API_VERSION"],
        "livemode": False,
        "account": config["STRIPE_ACCOUNT_ID"],
        "data": {
            "object": {
                "id": "in_test_synthetic",
                "livemode": False,
                "subscription": "sub_test_synthetic",
                "customer": "cus_test_synthetic",
            }
        },
    }

    async def body():
        return json.dumps(event).encode()

    return asyncio.run(
        namespace["stripe_webhook"](SimpleNamespace(body=body, headers={}), Mock())
    )


@pytest.mark.parametrize("kind", ["paid", "exempt_bound", "legacy_pilot"])
def test_signed_failure_event_preserves_existing_access(kind):
    namespace, config, practice, decide = harness(kind)
    if kind == "legacy_pilot":
        practice.stripe_subscription_id = "sub_test_synthetic"
        practice.stripe_customer_id = "cus_test_synthetic"
        practice.subscription_status = "active"
    assert decide().allowed
    assert deliver_failed_invoice(namespace, config, practice).status_code == 503
    assert decide().allowed  # integration off: no inbox projection changed
    flags(namespace, config, True)
    deliver_failed_invoice(namespace, config, practice)
    assert practice.subscription_status == "active"
    assert decide().allowed
    clinical_gate(namespace)


@pytest.mark.parametrize("kind", ["paid", "exempt_bound"])
def test_existing_access_should_survive_billing_on_and_signed_failure(kind):
    namespace, config, practice, decide = harness(kind)
    assert decide().allowed
    flags(namespace, config, True)
    deliver_failed_invoice(namespace, config, practice)
    assert decide().allowed


@pytest.mark.parametrize("kind", ["exempt", "legacy_pilot", "legacy_trial"])
def test_unbound_existing_accounts_are_not_matched_by_subscription_events(kind):
    namespace, config, practice, decide = harness(kind)
    # Only database matching is mocked: no bound subscription means no row.
    original = Session.exec
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Session, "exec", lambda self, query: Rows(None))
        before = decide()
        flags(namespace, config, True)
        deliver_failed_invoice(namespace, config, practice)
        assert decide() == before and practice.subscription_status is None
    assert Session.exec is original


def invalidate_bound_contract(namespace, practice):
    namespace.update(
        {
            "_isolated_state_session": lambda: Session(practice),
            "_contract_snapshot_matches_binding": lambda *args: True,
            "provider_log_ref": lambda value: "synthetic-log-reference",
            "_emit_billing_metric": Mock(),
        }
    )
    load(
        "app/services/stripe_billing.py",
        ("_fail_closed_invalid_bound_subscription",),
        namespace,
    )
    namespace["_fail_closed_invalid_bound_subscription"](
        practice,
        practice.stripe_subscription_id,
        SimpleNamespace(
            subscription={}, reason=ValueError("synthetic invalid contract")
        ),
        expected_state_version=practice.stripe_state_version,
        event_created_at=None,
        event_id=None,
        event_type=None,
    )


def test_active_exemption_outranks_fresh_invalid_bound_contract():
    namespace, config, practice, decide = harness("exempt_bound")
    assert decide().allowed
    flags(namespace, config, True)
    invalidate_bound_contract(namespace, practice)
    assert practice.billing_exemption_active(at=NOW)
    assert practice.subscription_status == "invalid" and decide().allowed


def test_existing_exempt_access_should_survive_invalid_bound_contract():
    namespace, config, practice, decide = harness("exempt_bound")
    assert decide().allowed
    flags(namespace, config, True)
    invalidate_bound_contract(namespace, practice)
    assert decide().allowed


def test_trace_names_every_runtime_flag_and_readiness_call_site():
    trace = (ROOT / "docs/stripe-billing-existing-user-trace.md").read_text()
    helpers = set(
        "stripe_runtime_configured stripe_webhook_configured stripe_portal_configured stripe_billing_configured _request _validate_runtime_configuration".split()
    )
    flag_names = {"STRIPE_BILLING_ENABLED", "STRIPE_INTEGRATION_ENABLED"}

    def identifier(node):
        return getattr(node, "id", getattr(node, "attr", ""))

    def relevant(node):
        return identifier(node) in flag_names or (
            isinstance(node, ast.Call) and identifier(node.func) in helpers
        )

    sites = {
        function.name
        for path in (ROOT / "app").rglob("*.py")
        for function in ast.walk(ast.parse(path.read_text()))
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(relevant(node) for node in ast.walk(function))
    }
    assert sites
    assert {name for name in sites if name not in trace} == set()
