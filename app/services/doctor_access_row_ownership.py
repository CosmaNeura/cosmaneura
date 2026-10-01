"""Private evidence of actual provider row locks, never an access decision."""

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, cast

from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember
from sqlalchemy import event, inspect
from sqlalchemy.orm import InstanceState, SessionTransaction, object_session
from sqlmodel import Session, select

_ISSUER = object()


@dataclass(frozen=True, eq=False)
class DoctorAccessRowOwnership:
    """Usable only in the exact lock-taking transaction and factory scope."""

    _session: Session = field(repr=False)
    _transaction: SessionTransaction = field(repr=False)
    _doctor_id: Any = field(repr=False)
    _practice_id: Any = field(repr=False)
    _subject: str = field(repr=False)
    _issuer: object = field(repr=False)
    _rows: tuple[Doctor, Practice, PracticeMember] = field(repr=False)
    _active: bool = field(default=True, repr=False)

    def _invalidate(self, *_args) -> None:
        object.__setattr__(self, "_active", False)

    def matches_subject(self, session: Session, subject: str | None) -> bool:
        current = session.get_nested_transaction() or session.get_transaction()
        # Inspect only loaded state: expiration/detachment selects the original
        # query path rather than causing implicit SQL during proof validation.
        doctor_state, practice_state, member_state = (
            cast(InstanceState, inspect(row)).dict for row in self._rows
        )
        return (
            self._issuer is _ISSUER
            and self._active
            and session is self._session
            and current is self._transaction
            and self._transaction.is_active
            and subject == self._subject
            and all(object_session(row) is session for row in self._rows)
            and doctor_state.get("id") == self._doctor_id
            and doctor_state.get("clerk_id") == self._subject
            and practice_state.get("id") == self._practice_id
            and member_state.get("doctor_id") == self._doctor_id
            and member_state.get("practice_id") == self._practice_id
        )

    def matches(self, session: Session, doctor: Doctor, *, practice_id: Any) -> bool:
        state = cast(InstanceState, inspect(doctor)).dict
        return (
            self.matches_subject(session, state.get("clerk_id"))
            and object_session(doctor) is session
            and state.get("id") == self._doctor_id
            and practice_id == self._practice_id
        )


@contextmanager
def lock_doctor_access_rows(
    session: Session, *, practice_id: Any, doctor_id: Any, subject: str
):
    """Keep the existing Practice→Doctor order and post-wait owner recheck.

    The yielded None preserves the caller's existing denial. A proof is minted
    only after both actual FOR UPDATE statements and the current recheck.
    Transaction/savepoint changes invalidate it even if control later returns
    to its original parent transaction. No lock is added or released here.
    """
    practice = session.exec(
        select(Practice)
        .where(Practice.id == practice_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    doctor = session.exec(
        select(Doctor)
        .where(Doctor.id == doctor_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    member = session.exec(
        select(PracticeMember)
        .where(PracticeMember.doctor_id == doctor_id)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if (
        practice is None
        or doctor is None
        or doctor.clerk_id != subject
        or doctor.deleted_at is not None
        or member is None
        or member.practice_id != practice_id
    ):
        yield None
        return
    transaction = session.get_nested_transaction() or session.get_transaction()
    assert transaction is not None
    proof = DoctorAccessRowOwnership(
        session,
        transaction,
        doctor.id,
        practice.id,
        subject,
        _ISSUER,
        (doctor, practice, member),
    )
    for name in ("after_transaction_create", "after_transaction_end"):
        event.listen(session, name, proof._invalidate)
    try:
        yield practice, doctor, member, proof
    finally:
        proof._invalidate()
        for name in ("after_transaction_create", "after_transaction_end"):
            event.remove(session, name, proof._invalidate)
