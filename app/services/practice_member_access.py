"""Owner revocation and restoration of practice members' access.

A revocation is reversible and takes effect at the next midnight on US Eastern
clock time; until then the member keeps access and holds their seat. Every
function here takes the practice seat lock before any other row, the same
order as invitation acceptance, and writes its audit row in the same
transaction as the change it records.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.services.public_entitlements import (
    lock_practice_for_active_seat_mutation,
    record_active_clinician_seat_quantity,
    require_active_clinician_seat_capacity,
)

from app.models.doctor import Doctor
from app.models.practice import (
    ClinicianSeatState,
    MemberAccessState,
    Practice,
    PracticeMember,
    PracticeRole,
    member_access_state,
)
from app.models.practice_access_audit import PracticeAccessAction, PracticeAccessAudit
from sqlmodel import Session, select

ACCESS_CUTOFF_ZONE = ZoneInfo("America/New_York")

OWNER_REVOCATION_REASON = "Workforce access revoked by the practice owner."
OWNER_RESTORATION_REASON = "Workforce access restored by the practice owner."


def access_cutoff_after(now: datetime) -> datetime:
    """The next US Eastern midnight strictly after ``now``, in UTC."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("access cut-off requires a timezone-aware datetime")
    eastern_date = now.astimezone(ACCESS_CUTOFF_ZONE).date()
    return datetime.combine(
        eastern_date + timedelta(days=1), time(0), ACCESS_CUTOFF_ZONE
    ).astimezone(timezone.utc)


class PracticeMemberAccessError(Exception):
    """A refused revoke or restore; the message is safe to show the caller."""


class MemberNotFound(PracticeMemberAccessError):
    def __init__(self) -> None:
        super().__init__("Clinician not found.")


class MemberAccessConflict(PracticeMemberAccessError):
    pass


@dataclass(frozen=True)
class MemberAccessChange:
    practice: Practice
    member: PracticeMember
    doctor: Doctor
    changed: bool
    "False when the member was already in the requested state."
    decided_at: datetime
    "The instant, read under the seat lock, that the member's state was judged at."


@dataclass(frozen=True)
class _LockedMember:
    practice: Practice
    owner: PracticeMember
    member: PracticeMember
    doctor: Doctor


def _lock_member(
    session: Session, owner_member: PracticeMember, member_id: Any
) -> _LockedMember:
    practice = lock_practice_for_active_seat_mutation(session, owner_member.practice_id)
    owner = session.exec(
        select(PracticeMember)
        .where(PracticeMember.id == owner_member.id)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if (
        owner is None
        or owner.practice_id != practice.id
        or owner.role != PracticeRole.owner.value
    ):
        raise MemberNotFound
    candidate = session.exec(
        select(PracticeMember).where(PracticeMember.id == member_id)
    ).one_or_none()
    if candidate is None or candidate.practice_id != practice.id:
        raise MemberNotFound
    doctor = session.exec(
        select(Doctor)
        .where(Doctor.id == candidate.doctor_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    member = session.exec(
        select(PracticeMember)
        .where(PracticeMember.id == member_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if (
        member is None
        or member.practice_id != practice.id
        or member.doctor_id != doctor.id
    ):
        raise MemberNotFound
    if practice.organization_id is not None or practice.negotiated_baa_required:
        raise MemberAccessConflict(
            "This practice uses organization-managed access. "
            "Contact Cosmaneura support to change a clinician's access."
        )
    return _LockedMember(practice=practice, owner=owner, member=member, doctor=doctor)


def _audit(
    locked: _LockedMember,
    *,
    action: PracticeAccessAction,
    reason: str,
    now: datetime,
    effective_at: datetime,
) -> PracticeAccessAudit:
    audit = PracticeAccessAudit(
        practice_id=locked.practice.id,
        action=action.value,
        operator_ref=f"doctor:{locked.owner.doctor_id}",
        reason=reason,
        effective_at=effective_at,
        expires_at=None,
        document_reference=str(locked.member.id),
    )
    audit.created_at = now
    return audit


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _finish(
    session: Session, locked: _LockedMember, *, changed: bool, decided_at: datetime
) -> MemberAccessChange:
    session.commit()
    session.refresh(locked.practice)
    session.refresh(locked.member)
    session.refresh(locked.doctor)
    return MemberAccessChange(
        practice=locked.practice,
        member=locked.member,
        doctor=locked.doctor,
        changed=changed,
        decided_at=decided_at,
    )


def revoke_member_access(
    session: Session,
    *,
    owner_member: PracticeMember,
    member_id: Any,
    clock: Callable[[], datetime] = _utc_now,
) -> MemberAccessChange:
    """Schedule a member's access to end at tonight's Eastern midnight.

    The seat stays occupied until then, so the billed quantity is unchanged
    now; the periodic quantity sweep records the lower count after the
    cut-off.
    """

    locked = _lock_member(session, owner_member, member_id)
    # Read only once the lock is held: a request queued behind another seat
    # change may be released after tonight's cut-off.
    now = clock()
    member = locked.member
    if member.role == PracticeRole.owner.value:
        raise MemberAccessConflict("The practice owner's access can't be revoked.")
    if member.access_revoked_at is not None:
        raise MemberAccessConflict("This clinician's access has already ended.")
    if member.seat_state != ClinicianSeatState.active.value:
        return _finish(session, locked, changed=False, decided_at=now)

    cutoff = access_cutoff_after(now)
    member.seat_state = ClinicianSeatState.inactive.value
    member.access_ends_at = cutoff
    session.add(member)
    session.add(
        _audit(
            locked,
            action=PracticeAccessAction.practice_member_access_revoked,
            reason=OWNER_REVOCATION_REASON,
            now=now,
            effective_at=cutoff,
        )
    )
    return _finish(session, locked, changed=True, decided_at=now)


def restore_member_access(
    session: Session,
    *,
    owner_member: PracticeMember,
    member_id: Any,
    clock: Callable[[], datetime] = _utc_now,
) -> MemberAccessChange:
    """Undo an owner revocation.

    Before the cut-off this only cancels it: the seat never freed. After the
    cut-off the member takes a seat again, so the cap applies and the new
    quantity is recorded for Stripe in this transaction.
    """

    locked = _lock_member(session, owner_member, member_id)
    # Read only once the lock is held: a restore queued behind another seat
    # change may be released after the cut-off, when it needs a seat again.
    now = clock()
    member = locked.member
    if member.role == PracticeRole.owner.value:
        raise MemberAccessConflict("The practice owner's access can't be restored.")
    if member.access_revoked_at is not None or locked.doctor.deleted_at is not None:
        raise MemberAccessConflict("This clinician's access can't be restored.")
    state = member_access_state(member, at=now)
    if state is MemberAccessState.active:
        return _finish(session, locked, changed=False, decided_at=now)

    if state is MemberAccessState.revoked:
        require_active_clinician_seat_capacity(session, locked.practice, at=now)
    member.seat_state = ClinicianSeatState.active.value
    member.access_ends_at = None
    session.add(member)
    if state is MemberAccessState.revoked:
        record_active_clinician_seat_quantity(session, locked.practice)
    session.add(
        _audit(
            locked,
            action=PracticeAccessAction.practice_member_access_restored,
            reason=OWNER_RESTORATION_REASON,
            now=now,
            effective_at=now,
        )
    )
    return _finish(session, locked, changed=True, decided_at=now)
