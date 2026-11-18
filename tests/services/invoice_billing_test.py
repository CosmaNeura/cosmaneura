"""Synthetic invoice-lane boundaries: commerce cannot rewrite clinical access."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import Mock

import pytest
from structlog.testing import capture_logs
from typer.testing import CliRunner

from app.cli import app as cli_app
from app.services import invoice_billing as invoice
from app.services import stripe_billing as stripe

from activemodel.session_manager import get_session
from app.models.practice import Practice
from app.models.practice_access_audit import PracticeAccessAudit
from app.models.practice_invoice_billing import PracticeInvoiceBilling

from tests.services.stripe_billing_test import (
    NOW,
    _organization_doctor,
    _practice,
    _subscription,
)
from tests.services.stripe_billing_test import (
    _event as _subscription_event,
)
from tests.services.stripe_billing_test import configured_stripe as configured_stripe

ALERT = "invoice-only billing needs operator attention"


def _access(practice):
    practice.refresh()
    return {
        name: getattr(practice, name)
        for name in Practice.model_fields
        if name.startswith("stripe_")
        or name
        in "subscription_status billing_exempt billing_exempt_until trial_ends_at".split()
    }


def _row(practice):
    return PracticeInvoiceBilling(
        practice_id=practice.id,
        stripe_customer_id="cus_invoice",
        stripe_subscription_id="sub_invoice",
        price_tier="direct",
        quantity=1,
        subscription_status="active",
        operator_ref="test:F1",
        reason="Synthetic emailed invoice",
    ).save()


def _event(row, event_type, status=None, *, offset=0):
    event = _subscription_event(
        event_type,
        dict(
            id=row.stripe_subscription_id,
            customer=row.stripe_customer_id,
            status=status,
            livemode=False,
        ),
        event_id=f"evt_invoice_{event_type}_{status}_{offset}",
        created=int(NOW.timestamp()) + offset,
    )
    if event_type.startswith("invoice."):
        event["data"]["object"].update(
            id="in_synthetic", subscription=row.stripe_subscription_id
        )
    return event


@pytest.fixture
def invoice_remote(monkeypatch, practice):
    calls = []
    subscription = _subscription(practice)

    def request(method, path, data: Any = None, *, idempotency_key=None):
        calls.append((method, path, deepcopy(data), idempotency_key))
        if path == "/customers":
            return {"id": "cus_new_invoice_synthetic", "livemode": False}
        if path == "/subscriptions":
            subscription.update(
                customer=data["customer"],
                collection_method=data["collection_method"],
                metadata=dict(
                    billing_lane=data["metadata[billing_lane]"],
                    account_reference=data["metadata[account_reference]"],
                ),
            )
            item = subscription["items"]["data"][0]
            item.update(
                price={"id": data["items[0][price]"]},
                quantity=int(data["items[0][quantity]"]),
            )
        elif method == "POST":
            subscription["items"]["data"][0]["quantity"] = int(
                data["items[0][quantity]"]
            )
        return deepcopy(subscription)

    monkeypatch.setattr(
        stripe, "_validate_runtime_configuration", lambda **kwargs: None
    )
    monkeypatch.setattr(stripe, "_request", request)
    return calls


@pytest.fixture
def practice():
    practice = _practice()
    practice.billing_exempt = True
    practice.stripe_customer_id = "cus_core_must_not_be_reused"
    practice.save()
    return practice


def _issue(practice, **options) -> Any:
    options = {
        "operator_ref": "test:F1",
        "reason": "Synthetic",
        "dry_run": False,
    } | options
    return invoice.bill_by_invoice(practice, **options)


def test_invoice_subscription_never_writes_practice_stripe_fields(
    practice, invoice_remote
):
    before = _access(practice)
    assert _issue(practice, dry_run=True) is None
    assert invoice_remote == []
    assert PracticeInvoiceBilling.get(practice_id=practice.id) is None
    assert _access(practice) == before
    _issue(practice)
    assert _access(practice) == before
    assert PracticeAccessAudit.get(
        practice_id=practice.id, action="invoice_billing_started"
    )


def test_invoice_lane_uses_own_customer_and_no_practice_metadata(
    practice, invoice_remote
):
    row = _issue(practice, days_until_due=45)
    assert row.stripe_customer_id != practice.stripe_customer_id
    customer, subscription = invoice_remote
    assert customer[1] == "/customers"
    assert subscription[2]["customer"] == row.stripe_customer_id
    assert subscription[2]["collection_method"] == "send_invoice"
    assert subscription[2]["days_until_due"] == "45"
    assert subscription[2]["items[0][quantity]"] == "1"
    for _, _, data, key in invoice_remote:
        assert key
        assert data["metadata[billing_lane]"] == "invoice_only"
        assert data["metadata[account_reference]"] == row.account_reference
        assert "metadata[practice_id]" not in data
        assert "client_reference_id" not in data
        assert str(practice.id) not in str(data)


@pytest.mark.parametrize("kind", ["exempt", "active", "trialing"])
def test_invoice_events_leave_entitled_unchanged_for_exempt_active_and_trialing(kind):
    practice = _practice()
    practice.billing_exempt = kind == "exempt"
    practice.subscription_status = kind if kind != "exempt" else "invalid"
    practice.trial_ends_at = datetime.now(timezone.utc) + timedelta(days=14)
    practice.save()
    row = _row(practice)
    before = _access(practice)
    entitled = practice.entitled()
    assert entitled
    for index, status in enumerate(["payment_failed", "past_due", "unpaid"]):
        event_type = (
            "invoice.payment_failed" if index == 0 else "customer.subscription.updated"
        )
        with capture_logs() as logs:
            assert stripe.apply_stripe_event(
                _event(row, event_type, status, offset=index)
            )
        assert logs[-1]["event"] == ALERT
        assert _access(practice) == before
        assert practice.entitled() == entitled


def test_invoice_event_routes_to_invoice_table_before_practice_lookup(
    practice, monkeypatch
):
    row = _row(practice)
    monkeypatch.setattr(
        Practice,
        "get",
        Mock(side_effect=AssertionError("Practice lookup must not run")),
    )
    assert stripe.apply_stripe_event(_event(row, "invoice.payment_failed"))
    assert not stripe.apply_stripe_event(
        _event(row, "customer.subscription.updated", "unpaid", offset=-1)
    )
    row.refresh()
    assert row.subscription_status == "active"


@pytest.mark.parametrize(
    "field",
    "stripe_subscription_id stripe_checkout_session_id stripe_checkout_request_json invoice_row reservation non_entitled missing_operator".split(),
)
def test_bill_by_invoice_refuses_bound_marker_or_open_checkout_practice(
    practice, field, invoice_remote
):
    options = {}
    if field == "non_entitled":
        practice.billing_exempt = False
        practice.save()
    elif field == "missing_operator":
        options["operator_ref"] = " "
    elif field in {"invoice_row", "reservation"}:
        row = _row(practice)
        if field == "reservation":
            row.stripe_subscription_id = None
            row.subscription_status = None
            row.save()
    else:
        setattr(practice, field, "synthetic-existing")
        practice.save()
    before = _access(practice)
    error = ValueError if field == "missing_operator" else invoice.InvoiceBillingError
    with pytest.raises(error):
        _issue(practice, **options)
    if field == "missing_operator":
        args = [
            "bill-by-invoice",
            "--practice-id",
            str(practice.id),
            "--reason",
            "Synthetic",
        ]
        env = {"NO_COLOR": "1", "TERM": "dumb"}
        result = CliRunner().invoke(
            cli_app, args + ["--no-dry-run"], env=env, color=False
        )
        assert result.exit_code == 2 and "--operator-ref" in result.output
    assert invoice_remote == []
    assert _access(practice) == before


def test_invoice_seat_change_updates_only_invoice_record(
    practice, invoice_remote, monkeypatch
):
    from app.services.public_entitlements import (
        lock_practice_seats,
        record_active_clinician_seat_quantity,
    )

    from app.models.practice import PracticeMember

    from tests.factories import save_practice_member

    practice.origin_channel = "mcd"
    practice.save()
    doctor = _organization_doctor("invoice-seat@example.com")
    member = save_practice_member(
        PracticeMember(
            practice_id=practice.id,
            doctor_id=doctor.id,
            role="workforce",
            attested_at=datetime.now(timezone.utc),
        )
    )
    before = _access(practice)
    row = _issue(practice)
    assert row.quantity == 2
    assert invoice_remote[1][2]["items[0][quantity]"] == "2"
    assert invoice_remote[1][2]["items[0][price]"] == "price_test_mcd"
    for state, count in [("inactive", 1), ("active", 2)]:
        member.seat_state = state
        save_practice_member(member)
        with get_session() as session:
            locked = lock_practice_seats(session, practice.id)
            assert locked is not None
            assert record_active_clinician_seat_quantity(session, locked) == count
            session.commit()
        assert invoice.sync_invoice_subscription_quantity(practice.id)
        row.refresh()
        assert row.quantity == count
        assert invoice_remote[-1][2]["items[0][quantity]"] == str(count)
        assert _access(practice) == before

    monkeypatch.setattr(
        stripe, "_request", Mock(side_effect=RuntimeError("synthetic outage"))
    )
    with capture_logs() as logs:
        assert not invoice.sync_invoice_subscription_quantity(practice.id)
    assert logs[-1]["event"] == ALERT
    assert _access(practice) == before and practice.entitled()
