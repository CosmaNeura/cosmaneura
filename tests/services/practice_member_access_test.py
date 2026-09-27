import secrets
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.services.practice_member_access import access_cutoff_after
from app.services.public_entitlements import active_clinician_seat_count

from activemodel.session_manager import get_session
from app.models.doctor import Doctor
from app.models.practice import (
    MemberAccessState,
    Practice,
    PracticeMember,
    member_access_state,
    member_holds_seat_clause,
)
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select

from tests.factories import save_practice_member

EASTERN = ZoneInfo("America/New_York")
UTC = timezone.utc


@pytest.mark.parametrize(
    ("now", "cutoff"),
    [
        (
            datetime(2026, 9, 27, 18, 0, tzinfo=UTC),
            datetime(2026, 9, 28, 4, 0, tzinfo=UTC),
        ),
        (
            datetime(2026, 9, 27, 23, 59, 59, tzinfo=EASTERN),
            datetime(2026, 9, 28, 4, 0, tzinfo=UTC),
        ),
        (
            datetime(2026, 9, 28, 0, 0, tzinfo=EASTERN),
            datetime(2026, 9, 29, 4, 0, tzinfo=UTC),
        ),
        (
            datetime(2026, 7, 1, 2, 0, tzinfo=UTC),
            datetime(2026, 7, 1, 4, 0, tzinfo=UTC),
        ),
        (
            datetime(2027, 3, 13, 22, 0, tzinfo=EASTERN),
            datetime(2027, 3, 14, 5, 0, tzinfo=UTC),
        ),
        (
            datetime(2027, 3, 14, 1, 30, tzinfo=EASTERN),
            datetime(2027, 3, 15, 4, 0, tzinfo=UTC),
        ),
        (
            datetime(2026, 11, 1, 1, 30, fold=0, tzinfo=EASTERN),
            datetime(2026, 11, 2, 5, 0, tzinfo=UTC),
        ),
        (
            datetime(2026, 11, 1, 1, 30, fold=1, tzinfo=EASTERN),
            datetime(2026, 11, 2, 5, 0, tzinfo=UTC),
        ),
        (
            datetime(2026, 10, 31, 20, 0, tzinfo=EASTERN),
            datetime(2026, 11, 1, 4, 0, tzinfo=UTC),
        ),
    ],
    ids=[
        "afternoon-edt",
        "last-second-edt",
        "exact-midnight-edt",
        "utc-date-ahead-of-eastern",
        "evening-before-spring-forward",
        "early-morning-of-spring-forward",
        "fall-back-first-fold",
        "fall-back-second-fold",
        "evening-before-fall-back",
    ],
)
def test_access_cutoff_is_the_next_eastern_midnight(now: datetime, cutoff: datetime):
    result = access_cutoff_after(now)

    assert result == cutoff
    assert result.tzinfo is UTC
    assert result > now
    assert result.astimezone(EASTERN).time().isoformat() == "00:00:00"


def test_fall_back_folds_are_distinct_instants_with_one_cutoff():
    first = datetime(2026, 11, 1, 1, 30, fold=0, tzinfo=EASTERN)
    second = datetime(2026, 11, 1, 1, 30, fold=1, tzinfo=EASTERN)

    assert first.astimezone(UTC) == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    assert second.astimezone(UTC) == datetime(2026, 11, 1, 6, 30, tzinfo=UTC)


def test_access_cutoff_rejects_a_naive_datetime():
    with pytest.raises(ValueError):
        access_cutoff_after(datetime(2026, 9, 27, 18, 0))


def _practice() -> Practice:
    return Practice(
        legal_name="Access End Practice, PLLC",
        join_code=f"E{secrets.token_hex(4).upper()[:7]}",
        email_domain="example.com",
        is_solo=False,
    ).save()


def _member(practice: Practice, **fields) -> PracticeMember:
    nonce = secrets.token_hex(4)
    doctor = Doctor(
        clerk_id=f"user_access_end_{nonce}",
        email=f"access-end-{nonce}@example.com",
        biography="",
    ).save()
    return save_practice_member(
        PracticeMember(
            practice_id=practice.id,
            doctor_id=doctor.id,
            role="workforce",
            **fields,
        )
    )


def _tombstone(at: datetime) -> dict:
    return {
        "access_revoked_at": at,
        "access_revoked_by_ref": "test-suite",
        "access_revocation_reason": "Test offboarding",
        "access_records_retain_until": at + timedelta(days=366 * 7),
    }


def test_sql_and_python_access_states_agree_for_every_state():
    practice = _practice()
    cutoff = datetime(2026, 9, 28, 4, 0, tzinfo=UTC)
    earlier = cutoff - timedelta(days=2)
    members = {
        "active": _member(practice),
        "ending": _member(practice, seat_state="inactive", access_ends_at=cutoff),
        "ended": _member(practice, seat_state="inactive", access_ends_at=earlier),
        "inactive": _member(practice, seat_state="inactive"),
        "tombstone": _member(practice, **_tombstone(earlier)),
        "inactive-tombstone": _member(
            practice,
            seat_state="inactive",
            access_ends_at=cutoff,
            **_tombstone(earlier),
        ),
    }
    instants = [
        earlier - timedelta(days=1),
        cutoff - timedelta(microseconds=1),
        cutoff,
        cutoff + timedelta(days=1),
    ]

    with get_session() as session:
        for at in instants:
            selected = dict(
                session.exec(
                    select(col(PracticeMember.id), member_holds_seat_clause(at)).where(
                        PracticeMember.practice_id == practice.id
                    )
                ).all()
            )
            assert all(isinstance(value, bool) for value in selected.values())
            held = {row_id for row_id, holds in selected.items() if holds}
            for label, member in members.items():
                state = member_access_state(member, at=at)
                assert (member.id in held) == (
                    state in {MemberAccessState.active, MemberAccessState.ending}
                ), (label, at)
            assert active_clinician_seat_count(session, practice.id, at=at) == len(held)

    assert (
        member_access_state(members["ending"], at=cutoff - timedelta(microseconds=1))
        is MemberAccessState.ending
    )
    assert (
        member_access_state(members["ending"], at=cutoff) is MemberAccessState.revoked
    )
    assert member_access_state(members["ended"], at=cutoff) is MemberAccessState.revoked
    assert (
        member_access_state(members["inactive-tombstone"], at=earlier)
        is MemberAccessState.permanently_revoked
    )


def test_a_member_in_their_grace_period_still_holds_a_seat():
    practice = _practice()
    now = datetime.now(UTC)
    cutoff = access_cutoff_after(now)
    _member(practice)
    _member(practice, seat_state="inactive", access_ends_at=cutoff)

    with get_session() as session:
        assert active_clinician_seat_count(session, practice.id, at=now) == 2
        assert active_clinician_seat_count(session, practice.id) == 2
        assert active_clinician_seat_count(session, practice.id, at=cutoff) == 1


def test_database_refuses_an_access_end_on_an_active_seat():
    practice = _practice()

    with pytest.raises(IntegrityError):
        _member(practice, access_ends_at=datetime.now(UTC) + timedelta(hours=1))
