"""Concurrent late-Checkout follow-up inserts must collapse to one audit row."""

import os
import secrets
import threading
from datetime import datetime, timezone

import pytest

from activemodel.session_manager import get_session
from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember, PracticeRole
from app.models.practice_access_audit import PracticeAccessAudit
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, create_engine, select

from tests.factories import save_practice_member

NOW = datetime(2026, 11, 19, 18, tzinfo=timezone.utc)


def _engine():
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("requires the PostgreSQL test database URL")
    url = make_url(database_url).set(drivername="postgresql+psycopg")
    return create_engine(url, pool_size=2, max_overflow=0)


def test_concurrent_follow_up_inserts_collapse_to_one_row():
    practice = Practice(
        legal_name="Follow-up Race Practice, PLLC",
        join_code=f"F{secrets.token_hex(4).upper()[:7]}",
        email_domain="synthetic.invalid",
        origin_channel="direct",
    ).save()
    owner = Doctor(
        clerk_id=f"user_followUpRace{secrets.token_hex(6)}",
        email=f"follow-up-race-{secrets.token_hex(4)}@synthetic.invalid",
        biography="",
    ).save()
    save_practice_member(
        PracticeMember(
            practice_id=practice.id,
            doctor_id=owner.id,
            role=PracticeRole.owner.value,
            attested_at=NOW,
        )
    )
    document_reference = f"stripe-subscription:sub_race_{secrets.token_hex(4)}"
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()
    engine = _engine()

    def insert_once() -> None:
        with Session(engine) as session:
            barrier.wait(timeout=5)
            session.add(
                PracticeAccessAudit(
                    practice_id=practice.id,
                    action="stripe_checkout_follow_up_required",
                    operator_ref="system:stripe-billing",
                    reason="Stripe subscription arrived after owner self-delete",
                    effective_at=NOW,
                    document_reference=document_reference,
                )
            )
            try:
                session.commit()
                with lock:
                    outcomes.append("committed")
            except IntegrityError:
                session.rollback()
                with lock:
                    outcomes.append("IntegrityError")

    try:
        threads = [threading.Thread(target=insert_once) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
    finally:
        engine.dispose()

    assert sorted(outcomes) == ["IntegrityError", "committed"]

    with get_session() as session:
        rows = session.exec(
            select(PracticeAccessAudit).where(
                PracticeAccessAudit.practice_id == practice.id,
                PracticeAccessAudit.action == "stripe_checkout_follow_up_required",
                PracticeAccessAudit.document_reference == document_reference,
            )
        ).all()
    assert len(rows) == 1
