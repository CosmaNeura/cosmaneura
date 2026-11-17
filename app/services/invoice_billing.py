"""Operator-only emailed invoices, isolated from every access projection."""

from datetime import datetime, timezone
from typing import Any

from app import log
from app.services import stripe_billing
from app.services.practice_access import _required
from app.services.public_entitlements import active_clinician_seat_count

from activemodel.session_manager import get_session
from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember
from app.models.practice_access_audit import PracticeAccessAudit
from app.models.practice_invoice_billing import PracticeInvoiceBilling
from sqlmodel import Session, col, select

TERMINAL = {"canceled", "incomplete_expired"}
ATTENTION = {"past_due", "unpaid", "incomplete", "payment_failed"}


class InvoiceBillingError(ValueError):
    pass


def _alert(row: PracticeInvoiceBilling, reason: str) -> None:
    log.error(
        "invoice-only billing needs operator attention",
        account_reference=row.account_reference,
        reason=reason,
    )


def invoice_billing_open(row: PracticeInvoiceBilling | None) -> bool:
    # A reservation with an ambiguous provider result also blocks double billing.
    return row is not None and row.subscription_status not in TERMINAL


def bill_by_invoice(
    practice: Practice,
    *,
    operator_ref: str,
    reason: str,
    days_until_due: int = 30,
    dry_run: bool = True,
) -> PracticeInvoiceBilling | None:
    operator = _required(operator_ref, field="operator_ref", limit=255)
    explanation = _required(reason, field="reason", limit=512)
    if not 1 <= days_until_due <= 365:
        raise InvoiceBillingError("days_until_due must be between 1 and 365")
    with get_session() as session:
        locked = session.exec(
            select(Practice)
            .where(Practice.id == practice.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one()
        row = session.get(PracticeInvoiceBilling, locked.id)
        if not locked.entitled():
            raise InvoiceBillingError("practice must already be entitled")
        if (
            locked.stripe_subscription_id
            or locked.stripe_checkout_session_id
            or locked.stripe_checkout_request_json
        ):
            raise InvoiceBillingError(
                "bound subscription or open Checkout must be resolved"
            )
        if invoice_billing_open(row):
            raise InvoiceBillingError(
                "an invoice subscription or unresolved invoice operation already exists"
            )
        owner = session.exec(
            select(Doctor)
            .join(PracticeMember, col(PracticeMember.doctor_id) == col(Doctor.id))
            .where(
                PracticeMember.practice_id == locked.id, PracticeMember.role == "owner"
            )
        ).one()
        quantity = active_clinician_seat_count(session, locked.id)
        if not 1 <= quantity <= 20:
            raise InvoiceBillingError("active clinician count is outside seat bounds")
        if dry_run:
            return None
        stripe_billing._validate_runtime_configuration(require_active_price=True)
        if row is None:
            row = PracticeInvoiceBilling(
                practice_id=locked.id,
                price_tier=locked.origin_channel,
                quantity=quantity,
                operator_ref=operator,
                reason=explanation,
                days_until_due=days_until_due,
            )
        else:
            row.operation_version += 1
            row.stripe_customer_id = None
            row.stripe_subscription_id = None
            row.subscription_status = None
            row.price_tier = locked.origin_channel
            row.quantity = quantity
            row.days_until_due = days_until_due
            row.operator_ref = operator
            row.reason = explanation
        session.add(row)
        # Persist the operation before any POST: ambiguous failures cannot create
        # a second invoice lane. Operators recover using the recorded key.
        session.commit()
        return _complete_invoice_creation(
            session, row, owner.email, stripe_billing.price_id_for_practice(locked)
        )


def _complete_invoice_creation(
    session: Session, row: PracticeInvoiceBilling, email: str, price_id: str
) -> PracticeInvoiceBilling:
    key = f"invoice-only-{row.account_reference}-{row.operation_version}"
    metadata = {
        "metadata[billing_lane]": "invoice_only",
        "metadata[account_reference]": row.account_reference,
    }
    try:
        customer = stripe_billing._request(
            "POST",
            "/customers",
            {"email": email, **metadata},
            idempotency_key=f"{key}-customer",
        )
        stripe_billing._validate_livemode(
            customer.get("livemode"), source="invoice customer"
        )
        row.stripe_customer_id = stripe_billing._stripe_reference_id(
            customer.get("id"), prefix="cus_", source="invoice customer"
        )
        session.add(row)
        session.commit()
        subscription = stripe_billing._request(
            "POST",
            "/subscriptions",
            {
                "customer": row.stripe_customer_id,
                "items[0][price]": price_id,
                "items[0][quantity]": str(row.quantity),
                "collection_method": "send_invoice",
                "days_until_due": str(row.days_until_due),
                **metadata,
            },
            idempotency_key=f"{key}-subscription",
        )
        item = _validate_invoice_subscription(subscription, row)
        price = item["price"]
        returned_price = price.get("id") if isinstance(price, dict) else price
        if returned_price != price_id or item["quantity"] != row.quantity:
            raise InvoiceBillingError("invoice subscription price or quantity mismatch")
        row.stripe_subscription_id = stripe_billing._stripe_reference_id(
            subscription.get("id"), prefix="sub_", source="invoice subscription"
        )
        row.subscription_status = subscription["status"]
        session.add(row)
        session.add(
            PracticeAccessAudit(
                practice_id=row.practice_id,
                action="invoice_billing_started",
                operator_ref=row.operator_ref,
                reason=row.reason,
                effective_at=datetime.now(timezone.utc),
                document_reference=key,
            )
        )
        session.commit()
        if row.subscription_status in ATTENTION:
            _alert(row, row.subscription_status)
        return row
    except Exception:
        _alert(row, "invoice creation failed; reconcile the reserved operation")
        raise


def _validate_invoice_subscription(
    subscription: dict[str, Any], row: PracticeInvoiceBilling
) -> dict[str, Any]:
    stripe_billing._validate_livemode(
        subscription.get("livemode"), source="invoice subscription"
    )
    if (
        row.stripe_subscription_id
        and subscription.get("id") != row.stripe_subscription_id
    ):
        raise InvoiceBillingError("invoice subscription id mismatch")
    metadata = subscription.get("metadata") or {}
    if (
        subscription.get("customer") != row.stripe_customer_id
        or metadata.get("billing_lane") != "invoice_only"
        or metadata.get("account_reference") != row.account_reference
        or "practice_id" in metadata
        or subscription.get("collection_method") != "send_invoice"
    ):
        raise InvoiceBillingError("invoice subscription identity or lane mismatch")
    return stripe_billing._subscription_item(subscription)


def apply_invoice_event(
    subscription_id: str, event_type: str, created_at: datetime, obj: dict[str, Any]
) -> bool | None:
    """None means no match; matched stale events return without a Practice read."""
    with get_session() as session:
        row = session.exec(
            select(PracticeInvoiceBilling)
            .where(PracticeInvoiceBilling.stripe_subscription_id == subscription_id)
            .with_for_update()
        ).one_or_none()
        if row is None:
            return None
        if (
            row.last_event_created_at is not None
            and created_at < row.last_event_created_at
        ):
            return False
        if obj.get("customer") != row.stripe_customer_id:
            _alert(row, "invoice event customer mismatch")
            return False
        status = obj.get("status")
        if event_type.startswith("invoice."):
            row.last_invoice_status = (
                "payment_failed"
                if event_type == "invoice.payment_failed"
                else status or event_type.removeprefix("invoice.")
            )
            attention = row.last_invoice_status
        else:
            row.subscription_status = status
            attention = status
        row.last_event_created_at = created_at
        session.add(row)
        session.commit()
        if attention in ATTENTION:
            _alert(row, str(attention))
        return True


def mark_invoice_quantity(session: Session, practice_id: Any, quantity: int) -> bool:
    row = session.get(PracticeInvoiceBilling, practice_id)
    if not invoice_billing_open(row):
        return False
    assert row is not None
    row.quantity = quantity
    row.quantity_sync_version += 1
    row.quantity_sync_required = True
    session.add(row)
    return True


def sync_invoice_subscription_quantity(practice_id: Any) -> bool:
    with get_session() as session:
        row = session.exec(
            select(PracticeInvoiceBilling)
            .where(PracticeInvoiceBilling.practice_id == practice_id)
            .with_for_update()
        ).one()
        if not row.stripe_subscription_id or row.subscription_status in TERMINAL:
            return False
        try:
            stripe_billing._validate_runtime_configuration()
            subscription = stripe_billing._request(
                "GET", f"/subscriptions/{row.stripe_subscription_id}"
            )
            item = _validate_invoice_subscription(subscription, row)
            result = stripe_billing._request(
                "POST",
                f"/subscriptions/{row.stripe_subscription_id}",
                {
                    "items[0][id]": item["id"],
                    "items[0][quantity]": str(row.quantity),
                    "proration_behavior": "none",
                },
                idempotency_key=f"invoice-seats-{row.account_reference}-{row.operation_version}-{row.quantity_sync_version}",
            )
            updated_item = _validate_invoice_subscription(result, row)
            if updated_item["quantity"] != row.quantity:
                raise InvoiceBillingError("invoice quantity update was not applied")
            row.quantity_sync_required = False
            session.add(row)
            session.commit()
            return True
        except Exception:
            _alert(row, "invoice seat sync failed")
            return False


def reconcile_invoice_quantities() -> tuple[int, int]:
    with get_session() as session:
        ids = session.exec(
            select(col(PracticeInvoiceBilling.practice_id)).where(
                PracticeInvoiceBilling.quantity_sync_required
            )
        ).all()
    succeeded = sum(
        sync_invoice_subscription_quantity(practice_id) for practice_id in ids
    )
    return succeeded, len(ids) - succeeded
