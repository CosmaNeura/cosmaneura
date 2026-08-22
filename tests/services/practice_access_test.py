"""Privileged access changes are effective-dated and durably evidenced."""

import secrets
from datetime import datetime, timedelta, timezone
from typing import cast

import pytest

from app.services.practice_access import (
    PracticeAccessChangeError,
    record_negotiated_baa,
    set_billing_exemption,
)

from activemodel.session_manager import get_session
from app.models.practice import Practice
from app.models.practice_access_audit import (
    PracticeAccessAction,
    PracticeAccessAudit,
)
from sqlalchemy import delete, update
from sqlalchemy.exc import DBAPIError
from sqlmodel import SQLModel

NOW = datetime(2026, 8, 20, 20, 0, tzinfo=timezone.utc)


def _practice() -> Practice:
    return Practice(
        legal_name="Access Audit Practice, PLLC",
        join_code=f"A{secrets.token_hex(4).upper()[:7]}",
        email_domain="example.com",
        origin_channel="direct",
    ).save()


def test_future_negotiated_baa_is_recorded_but_does_not_unlock_early():
    practice = _practice()
    effective_at = NOW + timedelta(days=3)
    audit = record_negotiated_baa(
        practice,
        operator_ref="ticket-BAA-204",
        reason="Countersigned agreement scheduled for customer launch",
        document_reference="CN-BAA-CUST-2026-0042",
        effective_at=effective_at,
    )

    practice.refresh()
    assert practice.negotiated_baa_on_file
    assert not practice.baa_in_force(at=NOW)
    assert practice.baa_in_force(at=effective_at)
    assert audit.action == PracticeAccessAction.negotiated_baa_recorded.value
    assert audit.operator_ref == "ticket-BAA-204"
    assert audit.document_reference == "CN-BAA-CUST-2026-0042"


def test_future_replacement_cannot_suspend_negotiated_baa_currently_in_force():
    practice = _practice()
    current = datetime.now(timezone.utc)
    record_negotiated_baa(
        practice,
        operator_ref="ticket-BAA-current",
        reason="Current countersigned agreement",
        document_reference="CN-BAA-CUST-2026-CURRENT",
        effective_at=current - timedelta(days=1),
    )
    with pytest.raises(PracticeAccessChangeError, match="cannot replace"):
        record_negotiated_baa(
            practice,
            operator_ref="ticket-BAA-next",
            reason="Future replacement",
            document_reference="CN-BAA-CUST-2027-NEXT",
            effective_at=current + timedelta(days=30),
        )
    practice.refresh()
    assert practice.negotiated_baa_reference == "CN-BAA-CUST-2026-CURRENT"
    assert practice.baa_in_force(at=current)


def test_billing_exemption_changes_append_operator_evidence():
    practice = _practice()
    expires_at = NOW + timedelta(days=30)
    set_audit = set_billing_exemption(
        practice,
        operator_ref="ticket-PILOT-7",
        reason="Approved early-adopter pilot",
        expires_at=expires_at,
        now=NOW,
    )
    clear_audit = set_billing_exemption(
        practice,
        operator_ref="ticket-PILOT-8",
        reason="Pilot converted to self-serve billing",
        expires_at=None,
        clear=True,
        now=NOW + timedelta(days=1),
    )

    practice.refresh()
    assert not practice.billing_exempt
    assert set_audit.action == PracticeAccessAction.billing_exemption_set.value
    assert set_audit.expires_at == expires_at
    assert clear_audit.action == PracticeAccessAction.billing_exemption_cleared.value
    records = cast(
        list[PracticeAccessAudit],
        list(
            PracticeAccessAudit.where(
                PracticeAccessAudit.practice_id == practice.id
            ).all()
        ),
    )
    assert {record.id for record in records} == {set_audit.id, clear_audit.id}


def test_practice_access_audit_database_rejects_update_and_delete():
    practice = _practice()
    audit = set_billing_exemption(
        practice,
        operator_ref="ticket-IMMUTABLE",
        reason="Append-only trigger test",
        expires_at=NOW + timedelta(days=1),
        now=NOW,
    )
    table = SQLModel.metadata.tables["practice_access_audit"]

    with get_session() as session:
        savepoint = session.begin_nested()
        with pytest.raises(DBAPIError, match="append-only"):
            session.execute(
                update(table)
                .where(table.c.id == audit.id)
                .values(reason="Mutation forbidden")
            )
        savepoint.rollback()

        savepoint = session.begin_nested()
        with pytest.raises(DBAPIError, match="append-only"):
            session.execute(delete(table).where(table.c.id == audit.id))
        savepoint.rollback()


def test_expired_or_unaudited_access_change_is_rejected():
    practice = _practice()
    with pytest.raises(PracticeAccessChangeError, match="future"):
        set_billing_exemption(
            practice,
            operator_ref="ticket-old",
            reason="Expired request",
            expires_at=NOW - timedelta(seconds=1),
            now=NOW,
        )
    with pytest.raises(PracticeAccessChangeError, match="operator_ref"):
        record_negotiated_baa(
            practice,
            operator_ref=" ",
            reason="Missing operator",
            document_reference="CN-BAA-CUST-2026-0043",
            effective_at=NOW,
        )
