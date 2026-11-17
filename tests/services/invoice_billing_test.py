"""Synthetic invoice-lane boundaries: commerce cannot rewrite clinical access."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from structlog.testing import capture_logs
from typer.testing import CliRunner

from app.cli import app as cli_app
from app.services import invoice_billing, stripe_billing

from activemodel.session_manager import get_session
from app.models.practice import Practice
from app.models.practice_access_audit import PracticeAccessAudit
from app.models.practice_invoice_billing import PracticeInvoiceBilling

from tests.services.stripe_billing_test import (
    NOW,
    _practice,
)
from tests.services.stripe_billing_test import configured_stripe as configured_stripe


def _access(practice):
    practice.refresh()
    return {
        name: getattr(practice, name)
        for name in Practice.model_fields
        if name.startswith("stripe_")
        or name
        in {
            "subscription_status",
            "billing_exempt",
            "billing_exempt_until",
            "trial_ends_at",
        }
    }


def _row(practice):
    return PracticeInvoiceBilling(
        practice_id=practice.id,
        stripe_customer_id="cus_invoice_synthetic",
        stripe_subscription_id="sub_invoice_synthetic",
        price_tier="direct",
        quantity=1,
        subscription_status="active",
        operator_ref="test:F1",
        reason="Synthetic emailed invoice",
    ).save()


def _event(row, event_type, status=None, *, offset=0):
    obj = {
        "id": row.stripe_subscription_id
        if event_type.startswith("customer.")
        else "in_invoice_synthetic",
        "customer": row.stripe_customer_id,
        "subscription": row.stripe_subscription_id,
        "status": status,
        "livemode": False,
    }
    return {
        "id": f"evt_invoice_{event_type}_{status}_{offset}",
        "type": event_type,
        "created": int(NOW.timestamp()) + offset,
        "livemode": False,
        "api_version": "2025-09-30.clover",
        "data": {"object": obj},
    }


@pytest.fixture
def invoice_remote(monkeypatch):
    calls = []
    subscription = {}

    def request(method, path, data=None, *, idempotency_key=None):
        calls.append((method, path, deepcopy(data), idempotency_key))
        if path == "/customers":
            return {"id": "cus_new_invoice_synthetic", "livemode": False}
        if path == "/subscriptions":
            subscription.update(
                id="sub_new_invoice_synthetic",
                customer=data["customer"],
                status="active",
                livemode=False,
                collection_method=data["collection_method"],
                metadata={
                    "billing_lane": data["metadata[billing_lane]"],
                    "account_reference": data["metadata[account_reference]"],
                },
                items={
                    "data": [
                        {
                            "id": "si_invoice_synthetic",
                            "price": {"id": data["items[0][price]"]},
                            "quantity": int(data["items[0][quantity]"]),
                        }
                    ]
                },
            )
        elif method == "POST":
            subscription["items"]["data"][0]["quantity"] = int(
                data["items[0][quantity]"]
            )
        return deepcopy(subscription)

    monkeypatch.setattr(
        stripe_billing, "_validate_runtime_configuration", lambda **kwargs: None
    )
    monkeypatch.setattr(stripe_billing, "_request", request)
    return calls


def _entitled_practice():
    practice = _practice()
    practice.billing_exempt = True
    practice.stripe_customer_id = "cus_core_must_not_be_reused"
    practice.save()
    return practice


def test_invoice_subscription_never_writes_practice_stripe_fields(invoice_remote):
    practice = _entitled_practice()
    before = _access(practice)
    invoice_billing.bill_by_invoice(
        practice, operator_ref="test:F1", reason="Synthetic", dry_run=False
    )
    assert _access(practice) == before
    assert PracticeAccessAudit.get(
        practice_id=practice.id, action="invoice_billing_started"
    )


def test_invoice_lane_uses_own_customer_and_no_practice_metadata(invoice_remote):
    practice = _entitled_practice()
    row = invoice_billing.bill_by_invoice(
        practice,
        operator_ref="test:F1",
        reason="Synthetic",
        days_until_due=45,
        dry_run=False,
    )
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
    for index, (event_type, status) in enumerate(
        [
            ("invoice.payment_failed", "open"),
            ("customer.subscription.updated", "past_due"),
            ("customer.subscription.updated", "unpaid"),
        ]
    ):
        with capture_logs() as logs:
            assert stripe_billing.apply_stripe_event(
                _event(row, event_type, status, offset=index)
            )
        assert (
            logs
            and logs[-1]["event"] == "invoice-only billing needs operator attention"
        )
        assert _access(practice) == before
        assert practice.entitled() == entitled


def test_invoice_event_routes_to_invoice_table_before_practice_lookup(monkeypatch):
    practice = _entitled_practice()
    row = _row(practice)
    monkeypatch.setattr(
        Practice,
        "get",
        Mock(side_effect=AssertionError("Practice lookup must not run")),
    )
    assert stripe_billing.apply_stripe_event(_event(row, "invoice.payment_failed"))
    assert not stripe_billing.apply_stripe_event(
        _event(row, "customer.subscription.updated", "unpaid", offset=-1)
    )
    row.refresh()
    assert row.subscription_status == "active"


def test_invoice_seat_change_updates_only_invoice_record(invoice_remote):
    practice = _entitled_practice()
    before = _access(practice)
    row = invoice_billing.bill_by_invoice(
        practice, operator_ref="test:F1", reason="Synthetic", dry_run=False
    )
    with get_session() as session:
        assert invoice_billing.mark_invoice_quantity(session, practice.id, 2)
        session.commit()
    assert invoice_billing.sync_invoice_subscription_quantity(practice.id)
    row.refresh()
    assert row.quantity == 2
    assert not row.quantity_sync_required
    assert invoice_remote[-1][2]["items[0][quantity]"] == "2"
    assert _access(practice) == before


@pytest.mark.parametrize(
    "field",
    [
        "stripe_subscription_id",
        "stripe_checkout_session_id",
        "stripe_checkout_request_json",
        "invoice_row",
    ],
)
def test_bill_by_invoice_refuses_bound_marker_or_open_checkout_practice(
    field, invoice_remote
):
    practice = _entitled_practice()
    if field == "invoice_row":
        _row(practice)
    else:
        setattr(practice, field, "synthetic-existing")
        practice.save()
    before = _access(practice)
    with pytest.raises(invoice_billing.InvoiceBillingError):
        invoice_billing.bill_by_invoice(
            practice, operator_ref="test:F1", reason="Synthetic", dry_run=False
        )
    assert invoice_remote == []
    assert _access(practice) == before


def test_bill_by_invoice_dry_run_changes_nothing(invoice_remote):
    practice = _entitled_practice()
    before = _access(practice)
    assert (
        invoice_billing.bill_by_invoice(
            practice, operator_ref="test:F1", reason="Synthetic"
        )
        is None
    )
    assert invoice_remote == []
    assert PracticeInvoiceBilling.get(practice_id=practice.id) is None
    assert _access(practice) == before


def test_bill_by_invoice_refuses_non_entitled_practice(invoice_remote):
    with pytest.raises(invoice_billing.InvoiceBillingError, match="entitled"):
        invoice_billing.bill_by_invoice(
            _practice(), operator_ref="test:F1", reason="Synthetic", dry_run=False
        )
    assert invoice_remote == []


def test_bill_by_invoice_refuses_missing_operator_ref(invoice_remote):
    practice = _entitled_practice()
    with pytest.raises(ValueError, match="operator_ref"):
        invoice_billing.bill_by_invoice(
            practice, operator_ref=" ", reason="Synthetic", dry_run=False
        )
    result = CliRunner().invoke(
        cli_app,
        [
            "bill-by-invoice",
            "--practice-id",
            str(practice.id),
            "--reason",
            "Synthetic",
            "--no-dry-run",
        ],
    )
    assert result.exit_code != 0
    assert "operator-ref" in result.output
    assert invoice_remote == []


def test_invoice_failed_seat_sync_alerts_without_blocking(invoice_remote, monkeypatch):
    practice = _entitled_practice()
    invoice_billing.bill_by_invoice(
        practice, operator_ref="test:F1", reason="Synthetic", dry_run=False
    )
    before = _access(practice)
    monkeypatch.setattr(
        stripe_billing, "_request", Mock(side_effect=RuntimeError("synthetic outage"))
    )
    with capture_logs() as logs:
        assert not invoice_billing.sync_invoice_subscription_quantity(practice.id)
    assert logs[-1]["event"] == "invoice-only billing needs operator attention"
    assert _access(practice) == before and practice.entitled()


def test_invoice_creation_and_seat_mutations_use_active_clinician_count(invoice_remote):
    from app.services.public_entitlements import (
        lock_practice_seats,
        record_active_clinician_seat_quantity,
    )

    from app.models.doctor import Doctor
    from app.models.practice import PracticeMember

    from tests.factories import save_practice_member

    practice = _entitled_practice()
    practice.origin_channel = "mcd"
    practice.save()
    doctor = Doctor(
        clerk_id="user_invoice_extra_synthetic",
        email="invoice-seat@example.com",
        biography="",
    ).save()
    member = save_practice_member(
        PracticeMember(
            practice_id=practice.id,
            doctor_id=doctor.id,
            role="workforce",
            attested_at=datetime.now(timezone.utc),
        )
    )
    before = _access(practice)
    row = invoice_billing.bill_by_invoice(
        practice, operator_ref="test:F1", reason="Synthetic", dry_run=False
    )
    assert row.quantity == 2
    assert invoice_remote[1][2]["items[0][quantity]"] == "2"
    assert invoice_remote[1][2]["items[0][price]"] == "price_test_mcd"
    # Remove and then add a clinician through the same ledger hook used by Core.
    for state, count in [("inactive", 1), ("active", 2)]:
        member.seat_state = state
        save_practice_member(member)
        with get_session() as session:
            locked = lock_practice_seats(session, practice.id)
            assert locked is not None
            assert record_active_clinician_seat_quantity(session, locked) == count
            session.commit()
        assert invoice_billing.sync_invoice_subscription_quantity(practice.id)
        row.refresh()
        assert row.quantity == count
        assert invoice_remote[-1][2]["items[0][quantity]"] == str(count)
        assert _access(practice) == before


def test_invoice_ambiguous_creation_keeps_reservation_and_prevents_duplicates(
    invoice_remote, monkeypatch
):
    practice = _entitled_practice()
    before = _access(practice)
    monkeypatch.setattr(
        stripe_billing,
        "_request",
        Mock(side_effect=RuntimeError("synthetic lost response")),
    )
    with pytest.raises(RuntimeError):
        invoice_billing.bill_by_invoice(
            practice, operator_ref="test:F1", reason="Synthetic", dry_run=False
        )
    assert PracticeInvoiceBilling.get(practice_id=practice.id) is not None
    with pytest.raises(invoice_billing.InvoiceBillingError, match="unresolved"):
        invoice_billing.bill_by_invoice(
            practice, operator_ref="test:F1", reason="Synthetic", dry_run=False
        )
    assert _access(practice) == before
