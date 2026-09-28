"""PostgreSQL regressions: member revoke and restore racing on the seat lock."""

import os
import secrets
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
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
from app.models.practice_access_audit import PracticeAccessAudit
from sqlmodel import col, select

from tests.factories import save_practice_member

CHECKS = ["workforce", "not-party", "direction", "phi"]
OWNER_CAP_DETAIL = "To add more clinicians, contact us at info@cosmaneura.com."
INVITEE_CAP_DETAIL = (
    "This practice can't add more clinicians right now. "
    "Ask the practice owner to contact Cosmaneura."
)


def _database_url() -> str:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("requires the PostgreSQL test database URL")
    return database_url


def _doctor(label: str) -> Doctor:
    return Doctor(
        clerk_id=f"user_member_race_{label}_{secrets.token_hex(6)}",
        email=f"member-race-{label}-{secrets.token_hex(3)}@gmail.com",
        biography="",
    ).save()


def _member(practice: Practice, label: str, **fields) -> PracticeMember:
    return save_practice_member(
        PracticeMember(
            practice_id=practice.id,
            doctor_id=_doctor(label).id,
            attested_at=datetime.now(timezone.utc),
            attestation_name=f"Racing {label}",
            **{"role": "workforce", **fields},
        )
    )


def _practice(active_seats: int) -> tuple[Practice, PracticeMember, Doctor]:
    practice = Practice(
        legal_name="Member Race Practice, PLLC",
        join_code=f"M{secrets.token_hex(4).upper()[:7]}",
        email_domain=f"member-race-{secrets.token_hex(4)}.com",
        origin_channel="direct",
        is_solo=False,
        baa_accepted_at=datetime.now(timezone.utc),
        baa_version=BAA_CURRENT_REFERENCE,
        billing_exempt=True,
        stripe_subscription_id=f"sub_member_race_{secrets.token_hex(8)}",
    ).save()
    owner = _member(practice, "owner", role="owner")
    for ordinal in range(active_seats - 1):
        _member(practice, f"m{ordinal}")
    owner_doctor = Doctor.get(id=owner.doctor_id)
    assert owner_doctor is not None
    return practice, owner, owner_doctor


def _request(doctor: Doctor) -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(
            doctor=doctor,
            auth_state=SimpleNamespace(payload={"sub": doctor.clerk_id}),
        )
    )


def _invite(owner: PracticeMember, email: str) -> str:
    with get_session() as session:
        return practice_invitations.issue_invitation(
            session,
            owner_member=owner,
            email=email,
            idempotency_key=secrets.token_hex(8),
            now=datetime.now(timezone.utc),
        ).token


def _accept(doctor: Doctor, token: str):
    return internal.accept_practice_invitation(
        _request(doctor),  # type: ignore[arg-type]
        internal.AcceptPracticeInvitationBody(
            token=token, attestation_name="Racing Invitee", checks=CHECKS
        ),
    )


def _revoke(owner_doctor: Doctor, member: PracticeMember):
    return internal.revoke_practice_member(
        _request(owner_doctor),  # type: ignore[arg-type]
        member.id,
    )


def _restore(owner_doctor: Doctor, member: PracticeMember):
    return internal.restore_practice_member(
        _request(owner_doctor),  # type: ignore[arg-type]
        member.id,
    )


def _audits(practice: Practice, member: PracticeMember) -> list[str]:
    with get_session() as session:
        return list(
            session.exec(
                select(PracticeAccessAudit.action)
                .where(
                    PracticeAccessAudit.practice_id == practice.id,
                    PracticeAccessAudit.document_reference == str(member.id),
                )
                .order_by(col(PracticeAccessAudit.created_at))
            ).all()
        )


def _backends_waiting_on_locks(connection: psycopg.Connection) -> int:
    row = connection.execute(
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE datname = current_database() AND wait_event_type = 'Lock'"
    ).fetchone()
    assert row is not None
    return int(row[0])


def _race(
    practice: Practice,
    calls: list[Callable[[], object]],
    *,
    release_after: datetime | None = None,
) -> list[object]:
    """Queue the calls, in order, behind a held practice row lock, then release it.

    Each call is submitted only once the previous one is waiting, so the lock
    queue, and with it the commit order, follows the list order. With
    ``release_after``, every call must have queued before that instant and the
    lock is held until it has passed.
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
                assert time.monotonic() < deadline, "call did not queue on the lock"
                assert not any(future.done() for future in futures)
                time.sleep(0.02)
        if release_after is not None:
            assert datetime.now(timezone.utc) < release_after, "queued too late"
            while datetime.now(timezone.utc) <= release_after:
                time.sleep(0.05)
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


def _last_seat_contest(access_ends_in: timedelta = timedelta(minutes=-1)):
    practice, owner, owner_doctor = _practice(MAX_ACTIVE_CLINICIAN_SEATS - 1)
    returning = _member(
        practice,
        "returning",
        seat_state="inactive",
        access_ends_at=datetime.now(timezone.utc) + access_ends_in,
    )
    invitee = _doctor("invitee")
    token = _invite(owner, invitee.email or "")
    return practice, owner_doctor, returning, invitee, token


def _assert_first_of_restore_or_accept_won(
    practice: Practice,
    returning: PracticeMember,
    invitee: Doctor,
    restored: object,
    accepted: object,
    *,
    restore_first: bool,
) -> None:
    restore_won = not isinstance(restored, HTTPException)
    accept_won = not isinstance(accepted, HTTPException)
    assert restore_won != accept_won
    assert restore_won is restore_first
    loser = accepted if restore_won else restored
    assert isinstance(loser, HTTPException)
    assert loser.status_code == 409
    assert loser.detail == (INVITEE_CAP_DETAIL if restore_won else OWNER_CAP_DETAIL)

    final_returning = PracticeMember.get(id=returning.id)
    assert final_returning is not None
    assert (final_returning.seat_state == "active") is restore_won
    assert (PracticeMember.get(doctor_id=invitee.id) is not None) is accept_won
    expected_audits = ["practice_member_access_restored"] if restore_won else []
    assert _audits(practice, returning) == expected_audits

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


ORDERS = pytest.mark.parametrize("restore_first", [True, False])


def _in_order(restore_first: bool, restore, other) -> list[Callable[[], object]]:
    return [restore, other] if restore_first else [other, restore]


def _outcomes(restore_first: bool, outcomes: list[object]) -> tuple[object, object]:
    first, second = outcomes
    return (first, second) if restore_first else (second, first)


@ORDERS
def test_restore_and_acceptance_cannot_both_take_the_last_seat(
    live_emails, restore_first
):
    _database_url()
    practice, owner_doctor, returning, invitee, token = _last_seat_contest()

    restored, accepted = _outcomes(
        restore_first,
        _race(
            practice,
            _in_order(
                restore_first,
                lambda: _restore(owner_doctor, returning),
                lambda: _accept(invitee, token),
            ),
        ),
    )

    _assert_first_of_restore_or_accept_won(
        practice, returning, invitee, restored, accepted, restore_first=restore_first
    )


@ORDERS
def test_a_restore_queued_across_the_cutoff_competes_for_the_last_seat(
    live_emails, restore_first
):
    _database_url()
    practice, owner_doctor, returning, invitee, token = _last_seat_contest(
        access_ends_in=timedelta(seconds=2)
    )
    assert returning.access_ends_at is not None

    # Both calls start while the returning member still holds a seat and run
    # only after their cut-off: whichever runs first takes the freed seat.
    restored, accepted = _outcomes(
        restore_first,
        _race(
            practice,
            _in_order(
                restore_first,
                lambda: _restore(owner_doctor, returning),
                lambda: _accept(invitee, token),
            ),
            release_after=returning.access_ends_at,
        ),
    )

    _assert_first_of_restore_or_accept_won(
        practice, returning, invitee, restored, accepted, restore_first=restore_first
    )


@ORDERS
def test_a_concurrent_revoke_frees_no_seat_before_its_cutoff(
    live_emails, restore_first
):
    _database_url()
    practice, owner_doctor, returning, invitee, token = _last_seat_contest()
    leaving = PracticeMember.one(
        practice_id=practice.id, attestation_name="Racing m0", seat_state="active"
    )

    *contest, revoked = _race(
        practice,
        [
            *_in_order(
                restore_first,
                lambda: _restore(owner_doctor, returning),
                lambda: _accept(invitee, token),
            ),
            lambda: _revoke(owner_doctor, leaving),
        ],
    )
    restored, accepted = _outcomes(restore_first, contest)

    assert not isinstance(revoked, HTTPException)
    assert revoked.seat_active is True  # type: ignore[attr-defined]
    assert revoked.access_ends_at is not None  # type: ignore[attr-defined]
    assert _audits(practice, leaving) == ["practice_member_access_revoked"]
    _assert_first_of_restore_or_accept_won(
        practice, returning, invitee, restored, accepted, restore_first=restore_first
    )


@ORDERS
def test_revoke_and_restore_in_the_grace_period_serialize(live_emails, restore_first):
    _database_url()
    practice, _owner, owner_doctor = _practice(3)
    member = PracticeMember.one(practice_id=practice.id, attestation_name="Racing m0")
    _revoke(owner_doctor, member)

    restored, revoked = _outcomes(
        restore_first,
        _race(
            practice,
            _in_order(
                restore_first,
                lambda: _restore(owner_doctor, member),
                lambda: _revoke(owner_doctor, member),
            ),
        ),
    )

    assert not isinstance(revoked, HTTPException)
    assert not isinstance(restored, HTTPException)
    final = PracticeMember.get(id=member.id)
    assert final is not None
    audits = _audits(practice, member)
    if restore_first:
        # Restore cancelled the revocation; the revoke then scheduled a new one.
        assert final.seat_state == "inactive"
        assert final.access_ends_at is not None
        assert audits == [
            "practice_member_access_revoked",
            "practice_member_access_restored",
            "practice_member_access_revoked",
        ]
    else:
        # The repeated revoke changed nothing; restore then cancelled it.
        assert final.seat_state == "active"
        assert final.access_ends_at is None
        assert audits == [
            "practice_member_access_revoked",
            "practice_member_access_restored",
        ]
    with get_session() as session:
        assert active_clinician_seat_count(session, practice.id) == 3
    final_practice = Practice.get(id=practice.id)
    assert final_practice is not None
    assert final_practice.stripe_quantity_sync_version == 0
