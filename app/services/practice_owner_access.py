"""Revalidate an acting owner after acquiring the practice mutation lock."""

from collections.abc import Iterable
from typing import Any

from app.services.doctor_access import resolve_doctor_access

from app.models.doctor import Doctor
from app.models.practice import (
    Practice,
    PracticeMember,
    PracticeRole,
    practice_seat_lock_held,
)
from sqlmodel import Session, col, select


class PracticeOwnerUnavailable(Exception):
    """The request's owner no longer has authority to change this practice."""


def lock_authorized_practice_owner(
    session: Session,
    practice: Practice,
    owner_member: PracticeMember,
    *,
    additional_doctor_ids: Iterable[Any] = (),
) -> PracticeMember:
    """Lock Doctors before members and reject stale pre-request authority.

    A route gate can pass before an account closure or offboarding transaction
    acquires the practice lock. Refresh both the actor and membership here;
    their retained owner role alone does not authorize a later mutation.
    Member mutations include the target Doctor so every Doctor lock precedes
    any membership lock, in the same order as support offboarding.
    """

    if not practice_seat_lock_held(session, practice.id):
        raise RuntimeError("owner authorization requires the practice seat lock")
    owner_doctor_id = owner_member.doctor_id
    doctors = session.exec(
        select(Doctor)
        .where(col(Doctor.id).in_({owner_doctor_id, *additional_doctor_ids}))
        .order_by(col(Doctor.id))
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    doctor = next((row for row in doctors if row.id == owner_doctor_id), None)
    owner = session.exec(
        select(PracticeMember)
        .where(PracticeMember.id == owner_member.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if (
        doctor is None
        or owner is None
        or owner.practice_id != practice.id
        or owner.doctor_id != owner_doctor_id
        or owner.role != PracticeRole.owner.value
        or not resolve_doctor_access(doctor).allowed
    ):
        raise PracticeOwnerUnavailable
    return owner
