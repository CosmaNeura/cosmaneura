"""PostgreSQL regressions: invitation acceptances racing on the seat lock."""

import os
import secrets
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace

import psycopg
import pytest
from fastapi import HTTPException

from app.routes import internal
from app.services import practice_invitations
from app.services.public_entitlements import (
    MAX_ACTIVE_CLINICIAN_SEATS,
    active_clinician_seat_count,
)

from activemodel.session_manager import get_session
from app.models.baa import BAA_CURRENT_REFERENCE
from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember
from app.models.public_onboarding import PracticeInvitation

from tests.factories import save_practice_member

CHECKS = ["workforce", "not-party", "direction", "phi"]


def _doctor(label: str) -> Doctor:
    return Doctor(
        clerk_id=f"user_invite_race_{label}_{secrets.token_hex(6)}",
        email=f"invite-race-{label}-{secrets.token_hex(3)}@gmail.com",
        biography="",
    ).save()


def _database_url() -> str:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("requires the PostgreSQL test database URL")
    return database_url


def _practice(members: int) -> tuple[Practice, PracticeMember]:
    practice = Practice(
        legal_name="Invitation Race Practice, PLLC",
        join_code=f"R{secrets.token_hex(4).upper()[:7]}",
        email_domain=f"invite-race-{secrets.token_hex(4)}.com",
        origin_channel="direct",
        is_solo=False,
        baa_accepted_at=datetime.now(timezone.utc),
        baa_version=BAA_CURRENT_REFERENCE,
        billing_exempt=True,
        stripe_subscription_id=f"sub_invite_race_{secrets.token_hex(8)}",
    ).save()
    owner = save_practice_member(
        PracticeMember(
            practice_id=practice.id, doctor_id=_doctor("owner").id, role="owner"
        )
    )
    for ordinal in range(members - 1):
        save_practice_member(
            PracticeMember(
                practice_id=practice.id,
                doctor_id=_doctor(f"m{ordinal}").id,
                role="workforce",
            )
        )
    return practice, owner


def _invite(owner: PracticeMember, email: str) -> str:
    with get_session() as session:
        return practice_invitations.issue_invitation(
            session,
            owner_member=owner,
            email=email,
            idempotency_key=secrets.token_hex(8),
            now=datetime.now(timezone.utc),
        ).token


def _request(doctor: Doctor) -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(
            doctor=doctor,
            auth_state=SimpleNamespace(payload={"sub": doctor.clerk_id}),
        )
    )


def _accept(doctor: Doctor, token: str):
    return internal.accept_practice_invitation(
        _request(doctor),  # type: ignore[arg-type]
        internal.AcceptPracticeInvitationBody(
            token=token, attestation_name="Racing Clinician", checks=CHECKS
        ),
    )


def _backends_waiting_on_locks(connection: psycopg.Connection) -> int:
    row = connection.execute(
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE datname = current_database() AND wait_event_type = 'Lock'"
    ).fetchone()
    assert row is not None
    return int(row[0])


def _race(practice: Practice, calls: list[Callable[[], object]]) -> list[object]:
    """Queue every call behind a held practice row lock, then release it.

    Each call is queued before the next is submitted. PostgreSQL grants a
    contended row lock to its waiters in arrival order, so the calls take the
    practice lock in list order.
    """

    database_url = _database_url()
    # The executor is entered first so that, if an assertion fails, closing
    # the blocker releases the row lock before the executor joins its threads.
    with (
        ThreadPoolExecutor(max_workers=len(calls)) as executor,
        psycopg.connect(database_url) as blocker,
        psycopg.connect(database_url, autocommit=True) as observer,
    ):
        blocker.execute(
            "SELECT id FROM practice WHERE id = %s FOR UPDATE",
            (practice.id.uuid,),
        )
        futures = []
        for queued, call in enumerate(calls, start=1):
            futures.append(executor.submit(call))
            deadline = time.monotonic() + 10
            while _backends_waiting_on_locks(observer) < queued:
                assert time.monotonic() < deadline, "calls did not queue on the lock"
                assert not any(future.done() for future in futures)
                time.sleep(0.02)
        blocker.rollback()

        outcomes: list[object] = []
        for future in futures:
            try:
                outcomes.append(future.result(timeout=10))
            except HTTPException as exc:
                outcomes.append(exc)
    return outcomes


@pytest.fixture
def live_emails(monkeypatch: pytest.MonkeyPatch) -> None:
    def current_email(clerk_id: str) -> str | None:
        doctor = Doctor.get(clerk_id=clerk_id)
        return (doctor.email or "").strip().lower() if doctor else None

    monkeypatch.setattr(internal, "verified_primary_email", current_email)


def test_two_invitees_cannot_both_take_the_last_seat(live_emails):
    _database_url()
    practice, owner = _practice(MAX_ACTIVE_CLINICIAN_SEATS - 1)
    contenders = [_doctor("a"), _doctor("b")]
    tokens = [_invite(owner, doctor.email or "") for doctor in contenders]

    outcomes = _race(
        practice,
        [
            lambda doctor=doctor, token=token: _accept(doctor, token)
            for doctor, token in zip(contenders, tokens, strict=True)
        ],
    )

    rejected = [item for item in outcomes if isinstance(item, HTTPException)]
    assert len(rejected) == 1
    assert rejected[0].status_code == 409
    assert rejected[0].detail == (
        "This practice can't add more clinicians right now. "
        "Ask the practice owner to contact Cosmaneura."
    )
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


@pytest.mark.parametrize("first", ["accept", "revoke"])
def test_accept_and_revoke_leave_exactly_one_terminal_state(live_emails, first: str):
    _database_url()
    practice, owner = _practice(2)
    invitee = _doctor("contested")
    token = _invite(owner, invitee.email or "")
    invitation = PracticeInvitation.get(
        token_digest=practice_invitations.token_digest(token)
    )
    assert invitation is not None
    owner_doctor = Doctor.get(id=owner.doctor_id)
    assert owner_doctor is not None
    calls: dict[str, Callable[[], object]] = {
        "accept": lambda: _accept(invitee, token),
        "revoke": lambda: internal.revoke_practice_invitation(
            _request(owner_doctor),  # type: ignore[arg-type]
            invitation.id,
        ),
    }
    order = [first, "revoke" if first == "accept" else "accept"]

    outcomes = dict(zip(order, _race(practice, [calls[name] for name in order])))

    final = PracticeInvitation.get(id=invitation.id)
    assert final is not None
    member = PracticeMember.get(doctor_id=invitee.id)
    accepted, revoked = outcomes["accept"], outcomes["revoke"]
    if first == "accept":
        assert isinstance(accepted, internal.PracticeStatus)
        assert final.accepted_by_doctor_id == invitee.id
        assert final.revoked_at is None
        assert member is not None and member.practice_id == practice.id
        assert isinstance(revoked, HTTPException)
        assert (revoked.status_code, revoked.detail) == (
            409,
            "An accepted invitation cannot be revoked.",
        )
    else:
        assert revoked is None
        assert final.revoked_at is not None and final.accepted_at is None
        assert member is None
        assert isinstance(accepted, HTTPException)
        assert (accepted.status_code, accepted.detail) == (
            409,
            "This invitation is no longer active. Ask the practice owner for a new one.",
        )


def test_a_double_submitted_accept_seats_the_doctor_once(live_emails):
    _database_url()
    practice, owner = _practice(2)
    invitee = _doctor("double")
    token = _invite(owner, invitee.email or "")

    outcomes = _race(practice, [lambda: _accept(invitee, token)] * 2)

    assert not any(isinstance(item, HTTPException) for item in outcomes)
    with get_session() as session:
        assert active_clinician_seat_count(session, practice.id) == 3
    final = Practice.get(id=practice.id)
    assert final is not None
    assert final.stripe_quantity_sync_version == 1
