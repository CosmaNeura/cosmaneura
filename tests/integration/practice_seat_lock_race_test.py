"""PostgreSQL regression: two joins racing for a practice's last seat."""

import os
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace

import psycopg
import pytest
from fastapi import HTTPException

from app.routes import internal
from app.services.public_entitlements import (
    MAX_ACTIVE_CLINICIAN_SEATS,
    active_clinician_seat_count,
)

from activemodel.session_manager import get_session
from app.models.baa import BAA_CURRENT_REFERENCE
from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember

from tests.factories import save_practice_member


def _doctor(domain: str, label: str) -> Doctor:
    return Doctor(
        clerk_id=f"user_seat_race_{label}_{secrets.token_hex(6)}",
        email=f"seat-race-{label}-{secrets.token_hex(3)}@{domain}",
        biography="",
    ).save()


def _backends_waiting_on_locks(connection: psycopg.Connection) -> int:
    row = connection.execute(
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE datname = current_database() AND wait_event_type = 'Lock'"
    ).fetchone()
    assert row is not None
    return int(row[0])


def test_two_joins_cannot_both_take_the_last_seat(monkeypatch: pytest.MonkeyPatch):
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("requires the PostgreSQL test database URL")
    domain = f"seat-race-{secrets.token_hex(4)}.com"
    practice = Practice(
        legal_name="Seat Race Practice, PLLC",
        join_code=f"S{secrets.token_hex(4).upper()[:7]}",
        email_domain=domain,
        origin_channel="direct",
        is_solo=False,
        baa_accepted_at=datetime.now(timezone.utc),
        baa_version=BAA_CURRENT_REFERENCE,
        stripe_subscription_id=f"sub_seat_race_{secrets.token_hex(8)}",
    ).save()
    for ordinal in range(MAX_ACTIVE_CLINICIAN_SEATS - 1):
        save_practice_member(
            PracticeMember(
                practice_id=practice.id,
                doctor_id=_doctor(domain, f"m{ordinal}").id,
                role="workforce",
            )
        )
    contenders = [_doctor(domain, "a"), _doctor(domain, "b")]
    emails = {doctor.clerk_id: doctor.email for doctor in contenders}
    monkeypatch.setattr(
        internal, "verified_primary_email", lambda clerk_id: emails.get(clerk_id)
    )

    def join(doctor: Doctor):
        return internal.join_practice(
            SimpleNamespace(state=SimpleNamespace(doctor=doctor)),  # type: ignore[arg-type]
            internal.JoinPracticeBody(
                join_code=practice.join_code,
                attestation_name="Racing Clinician",
                checks=["workforce", "not-party", "direction", "phi"],
            ),
        )

    # The executor is entered first so that, if an assertion fails, closing
    # the blocker releases the row lock before the executor joins its threads.
    with (
        ThreadPoolExecutor(max_workers=2) as executor,
        psycopg.connect(database_url) as blocker,
        psycopg.connect(database_url, autocommit=True) as observer,
    ):
        blocker.execute(
            "SELECT id FROM practice WHERE id = %s FOR UPDATE",
            (practice.id.uuid,),
        )
        futures = [executor.submit(join, doctor) for doctor in contenders]
        deadline = time.monotonic() + 10
        while _backends_waiting_on_locks(observer) < 2:
            assert time.monotonic() < deadline, "joins did not queue on the lock"
            assert not any(future.done() for future in futures)
            time.sleep(0.02)
        blocker.rollback()

        outcomes: list[object] = []
        for future in futures:
            try:
                outcomes.append(future.result(timeout=10))
            except HTTPException as exc:
                outcomes.append(exc)

    rejected = [item for item in outcomes if isinstance(item, HTTPException)]
    assert len(rejected) == 1
    assert rejected[0].status_code == 409
    assert rejected[0].detail == "A practice may have at most 20 active clinicians."
    with get_session() as session:
        assert (
            active_clinician_seat_count(session, practice.id)
            == MAX_ACTIVE_CLINICIAN_SEATS
        )
    final = Practice.get(id=practice.id)
    assert final is not None
    assert final.stripe_quantity_sync_required is True
    assert final.stripe_quantity_sync_target == MAX_ACTIVE_CLINICIAN_SEATS
    assert final.stripe_quantity_sync_version == 1
