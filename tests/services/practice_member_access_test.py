import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from app.services import practice_invitations as invitations
from app.services import practice_member_access as member_access
from app.services.practice_member_access import access_cutoff_after
from app.services.public_entitlements import (
    MAX_ACTIVE_CLINICIAN_SEATS,
    ActiveClinicianSeatLimitReached,
    active_clinician_seat_count,
)

from activemodel.session_manager import get_session
from app.models.baa import BAA_CURRENT_REFERENCE
from app.models.doctor import Doctor
from app.models.practice import (
    MemberAccessState,
    Practice,
    PracticeMember,
    member_access_state,
    member_holds_seat_clause,
    practice_seat_lock_held,
)
from app.models.practice_access_audit import PracticeAccessAudit
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
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


def _member(practice: Practice, **fields: Any) -> PracticeMember:
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
            **{"role": "workforce", **fields},
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


def _now() -> datetime:
    return datetime.now(UTC)


def _owned_practice(**overrides) -> tuple[Practice, PracticeMember]:
    fields = {
        "legal_name": "Revoke Practice, PLLC",
        "join_code": f"V{secrets.token_hex(4).upper()[:7]}",
        "email_domain": "clinic-revoke.example",
        "origin_channel": "direct",
        "is_solo": False,
        "baa_accepted_at": _now(),
        "baa_version": BAA_CURRENT_REFERENCE,
        "billing_exempt": True,
        "stripe_subscription_id": f"sub_revoke_{secrets.token_hex(8)}",
    } | overrides
    practice = Practice(**fields).save()
    owner = _member(
        practice, role="owner", attested_at=_now(), attestation_name="Pat Owner"
    )
    return practice, owner


def _workforce(practice: Practice, **fields) -> PracticeMember:
    return _member(practice, attested_at=_now(), attestation_name="Riley", **fields)


def _fill_seats(practice: Practice, total: int) -> None:
    with get_session() as session:
        occupied = active_clinician_seat_count(session, practice.id)
    for _ in range(total - occupied):
        _workforce(practice)


def _revoke(owner: PracticeMember, member: PracticeMember, *, now=None):
    with get_session() as session:
        return member_access.revoke_member_access(
            session,
            owner_member=owner,
            member_id=member.id,
            clock=(lambda: now) if now else _now,
        )


def _restore(owner: PracticeMember, member: PracticeMember, *, now=None):
    with get_session() as session:
        return member_access.restore_member_access(
            session,
            owner_member=owner,
            member_id=member.id,
            clock=(lambda: now) if now else _now,
        )


def _audits(practice: Practice) -> list[PracticeAccessAudit]:
    with get_session() as session:
        return list(
            session.exec(
                select(PracticeAccessAudit)
                .where(PracticeAccessAudit.practice_id == practice.id)
                .order_by(col(PracticeAccessAudit.created_at))
            ).all()
        )


def _reload(model, row_id):
    with get_session() as session:
        loaded = session.get(model, row_id)
        assert loaded is not None
        return loaded


def _end_grace_period(member: PracticeMember) -> None:
    """Stand in for the clock passing the member's cut-off."""

    member = _reload(PracticeMember, member.id)
    member.access_ends_at = _now() - timedelta(seconds=1)
    save_practice_member(member)


def test_revoke_schedules_the_cutoff_and_records_who_did_it():
    practice, owner = _owned_practice()
    member = _workforce(practice)
    now = datetime(2026, 9, 27, 18, 0, tzinfo=UTC)

    result = _revoke(owner, member, now=now)

    assert result.changed is True
    assert result.member.seat_state == "inactive"
    assert result.member.access_ends_at == datetime(2026, 9, 28, 4, 0, tzinfo=UTC)
    assert result.member.access_revoked_at is None
    after = _reload(Practice, practice.id)
    assert after.stripe_quantity_sync_version == 0
    assert after.stripe_quantity_sync_required is False
    [audit] = _audits(practice)
    assert audit.action == "practice_member_access_revoked"
    assert audit.document_reference == str(member.id)
    assert audit.operator_ref == f"doctor:{owner.doctor_id}"
    assert audit.reason == "Workforce access revoked by the practice owner."
    assert audit.created_at == now
    assert audit.effective_at == datetime(2026, 9, 28, 4, 0, tzinfo=UTC)
    assert audit.expires_at is None


def test_revoke_and_restore_judge_the_member_at_an_instant_read_under_the_lock():
    practice, owner = _owned_practice()
    member = _workforce(practice)
    reads: list[datetime] = []

    def change(action):
        with get_session() as session:

            def clock() -> datetime:
                assert practice_seat_lock_held(session, practice.id)
                reads.append(_now())
                return reads[-1]

            return action(session, owner_member=owner, member_id=member.id, clock=clock)

    revoked = change(member_access.revoke_member_access)
    restored = change(member_access.restore_member_access)

    assert revoked.decided_at == reads[0]
    assert revoked.member.access_ends_at == access_cutoff_after(reads[0])
    assert restored.decided_at == reads[1]
    assert [audit.created_at for audit in _audits(practice)] == reads


def test_a_repeated_revoke_keeps_the_first_cutoff_and_one_audit_row():
    practice, owner = _owned_practice()
    member = _workforce(practice)
    first = _revoke(owner, member)

    again = _revoke(owner, member, now=_now() + timedelta(days=1))

    assert again.changed is False
    assert again.member.access_ends_at == first.member.access_ends_at
    assert len(_audits(practice)) == 1


def test_only_the_owner_can_revoke_or_restore():
    practice, _owner = _owned_practice()
    colleague = _workforce(practice)
    member = _workforce(practice)

    with pytest.raises(member_access.MemberNotFound):
        _revoke(colleague, member)
    with pytest.raises(member_access.MemberNotFound):
        _restore(colleague, member)
    assert _reload(PracticeMember, member.id).seat_state == "active"
    assert _audits(practice) == []


def test_the_owner_cannot_be_revoked():
    practice, owner = _owned_practice()

    with pytest.raises(member_access.MemberAccessConflict) as refused:
        _revoke(owner, owner)

    assert str(refused.value) == "The practice owner's access can't be revoked."
    assert _reload(PracticeMember, owner.id).seat_state == "active"


def test_a_member_of_another_practice_is_not_found():
    _, owner = _owned_practice()
    other, _ = _owned_practice()
    stranger = _workforce(other)

    with pytest.raises(member_access.MemberNotFound):
        _revoke(owner, stranger)
    with pytest.raises(member_access.MemberNotFound):
        _restore(owner, stranger)
    assert _reload(PracticeMember, stranger.id).seat_state == "active"


def test_organization_managed_practices_are_refused():
    practice, owner = _owned_practice(
        negotiated_baa_required=True, baa_accepted_at=None, baa_version=None
    )
    member = _workforce(practice)

    with pytest.raises(member_access.MemberAccessConflict):
        _revoke(owner, member)
    assert _reload(PracticeMember, member.id).seat_state == "active"


def test_a_permanently_revoked_or_closed_account_cannot_be_revoked_or_restored():
    practice, owner = _owned_practice()
    now = _now()
    tombstoned = _workforce(practice, **_tombstone(now))
    closed = _workforce(practice, seat_state="inactive")
    closed_doctor = _reload(Doctor, closed.doctor_id)
    closed_doctor.deleted_at = now
    closed_doctor.save()

    with pytest.raises(member_access.MemberAccessConflict):
        _revoke(owner, tombstoned)
    for member in (tombstoned, closed):
        with pytest.raises(member_access.MemberAccessConflict) as refused:
            _restore(owner, member)
        assert str(refused.value) == "This clinician's access can't be restored."
    assert _reload(PracticeMember, closed.id).seat_state == "inactive"
    assert _audits(practice) == []


def test_restoring_an_active_member_is_a_no_op():
    practice, owner = _owned_practice()
    member = _workforce(practice)

    result = _restore(owner, member)

    assert result.changed is False
    assert _audits(practice) == []


def test_restore_before_the_cutoff_cancels_without_a_cap_check_or_quantity():
    practice, owner = _owned_practice()
    member = _workforce(practice)
    _fill_seats(practice, MAX_ACTIVE_CLINICIAN_SEATS)
    _revoke(owner, member)

    result = _restore(owner, member)

    assert result.changed is True
    assert result.member.seat_state == "active"
    assert result.member.access_ends_at is None
    after = _reload(Practice, practice.id)
    assert after.stripe_quantity_sync_version == 0
    with get_session() as session:
        assert (
            active_clinician_seat_count(session, practice.id)
            == MAX_ACTIVE_CLINICIAN_SEATS
        )
    revoked, restored = _audits(practice)
    assert revoked.action == "practice_member_access_revoked"
    assert restored.action == "practice_member_access_restored"
    assert restored.reason == "Workforce access restored by the practice owner."
    assert restored.effective_at == restored.created_at
    assert restored.operator_ref == f"doctor:{owner.doctor_id}"
    assert restored.document_reference == str(member.id)


def test_restore_after_the_cutoff_at_the_cap_is_refused():
    practice, owner = _owned_practice()
    member = _workforce(practice)
    _revoke(owner, member)
    _end_grace_period(member)
    _fill_seats(practice, MAX_ACTIVE_CLINICIAN_SEATS)

    with pytest.raises(ActiveClinicianSeatLimitReached):
        _restore(owner, member)

    assert _reload(PracticeMember, member.id).seat_state == "inactive"
    assert _reload(Practice, practice.id).stripe_quantity_sync_version == 0
    assert len(_audits(practice)) == 1


def test_restore_after_the_cutoff_takes_a_seat_and_records_the_quantity():
    practice, owner = _owned_practice()
    member = _workforce(practice)
    _workforce(practice)
    _revoke(owner, member)
    _end_grace_period(member)
    now = _now()

    result = _restore(owner, member, now=now)

    assert result.member.seat_state == "active"
    assert result.member.access_ends_at is None
    after = _reload(Practice, practice.id)
    assert after.stripe_quantity_sync_required is True
    assert after.stripe_quantity_sync_target == 3
    assert after.stripe_quantity_sync_version == 1
    restored = _audits(practice)[-1]
    assert restored.action == "practice_member_access_restored"
    assert restored.effective_at == now


def test_a_failed_commit_leaves_neither_the_change_nor_its_audit_row():
    practice, owner = _owned_practice()
    member = _workforce(practice)

    with get_session() as session:

        def fail_commit() -> None:
            session.flush()
            raise SQLAlchemyError("simulated commit failure")

        session.commit = fail_commit  # type: ignore[method-assign]
        with pytest.raises(SQLAlchemyError):
            member_access.revoke_member_access(
                session, owner_member=owner, member_id=member.id
            )
        session.rollback()

    reloaded = _reload(PracticeMember, member.id)
    assert reloaded.seat_state == "active"
    assert reloaded.access_ends_at is None
    assert _audits(practice) == []


def test_a_failed_audit_write_rolls_back_the_revocation(monkeypatch):
    practice, owner = _owned_practice()
    member = _workforce(practice)
    unrecorded = PracticeAccessAudit(
        practice_id=practice.id,
        action="practice_member_access_revoked",
        operator_ref="x" * 300,
        reason="Unrecordable",
    )
    monkeypatch.setattr(member_access, "_audit", lambda *_args, **_kwargs: unrecorded)

    with get_session() as session:
        with pytest.raises(SQLAlchemyError):
            member_access.revoke_member_access(
                session, owner_member=owner, member_id=member.id
            )
        session.rollback()

    reloaded = _reload(PracticeMember, member.id)
    assert reloaded.seat_state == "active"
    assert reloaded.access_ends_at is None


def test_a_seat_ending_tonight_admits_an_invitation_but_not_its_acceptance():
    practice, owner = _owned_practice()
    leaving = _workforce(practice)
    _fill_seats(practice, MAX_ACTIVE_CLINICIAN_SEATS)
    _revoke(owner, leaving)
    invitee = Doctor(
        clerk_id=f"user_revoke_invitee_{secrets.token_hex(4)}",
        email=f"invitee-{secrets.token_hex(4)}@gmail.com",
        biography="",
    ).save()
    email = (invitee.email or "").lower()

    with get_session() as session:
        issued = invitations.issue_invitation(
            session,
            owner_member=owner,
            email=email,
            idempotency_key=secrets.token_hex(8),
        )

    def accept():
        with get_session() as session:
            return invitations.accept_invitation(
                session,
                token=issued.token,
                doctor=invitee,
                verified_email=email,
                attestation_name="Replacement Clinician",
                reverify_email=lambda _doctor: email,
            )

    with pytest.raises(ActiveClinicianSeatLimitReached):
        accept()
    _end_grace_period(leaving)
    _, member = accept()

    assert member.seat_state == "active"
    after = _reload(Practice, practice.id)
    assert after.stripe_quantity_sync_target == MAX_ACTIVE_CLINICIAN_SEATS


def test_inviting_a_revoked_member_points_the_owner_at_restore():
    practice, owner = _owned_practice()
    member = _workforce(practice)
    _revoke(owner, member)
    doctor = _reload(Doctor, member.doctor_id)

    with (
        get_session() as session,
        pytest.raises(invitations.InvitationConflict) as refused,
    ):
        invitations.issue_invitation(
            session,
            owner_member=owner,
            email=doctor.email or "",
            idempotency_key=secrets.token_hex(8),
        )

    assert str(refused.value) == (
        "This clinician is already a member of the practice. "
        "Restore them from the roster instead."
    )
