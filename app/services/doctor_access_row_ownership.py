"""Private evidence of actual provider row locks, never an access decision."""

from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, cast

from activemodel.session_manager import _session_context
from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember
from sqlalchemy import event, inspect
from sqlalchemy.orm import InstanceState, SessionTransaction, object_session
from sqlmodel import Session, select

_ISSUER = object()
_CAPTURE_ROWS: ContextVar["DoctorAccessRowOwnership | None"] = ContextVar(
    "capture_doctor_access_rows", default=None
)
_LOOKUP_ROWS: ContextVar[tuple[Session, ExitStack] | None] = ContextVar(
    "lookup_doctor_access_row_scope", default=None
)


@contextmanager
def lookup_doctor_access_row_scope(session: Session):
    """Retain actual lock evidence only inside one synchronous encounter lookup.

    This scope changes neither the ActiveModel session nor the transaction.
    Each provider validator still takes its original locks and reads current
    access. Evidence expires on transaction/savepoint transitions and is reset
    before the caller's managed session closes, including on exceptions.
    """
    with ExitStack() as stack:
        token = _LOOKUP_ROWS.set((session, stack))
        try:
            yield
        finally:
            _LOOKUP_ROWS.reset(token)


@contextmanager
def provider_doctor_access_rows(
    session: Session, *, practice_id: Any, doctor_id: Any, subject: str
):
    """Take the original locks; make their evidence available to a local lookup."""
    scope = _LOOKUP_ROWS.get()
    if (
        scope is None
        or scope[0] is not session
        or _session_context.get() is not session
    ):
        with lock_doctor_access_rows(
            session, practice_id=practice_id, doctor_id=doctor_id, subject=subject
        ) as locked:
            yield locked
        return
    # The scope retains this exact factory, not an earlier decision or result.
    # Keeping the rows loaded avoids implicit re-reads; all eligibility queries
    # still execute at their original stages under the same row locks.
    locked = scope[1].enter_context(
        lock_doctor_access_rows(
            session, practice_id=practice_id, doctor_id=doctor_id, subject=subject
        )
    )
    token = _CAPTURE_ROWS.set(None if locked is None else locked[3])
    scope[1].callback(_CAPTURE_ROWS.reset, token)
    yield locked


_RECORD_ROWS: ContextVar[tuple[Session, ExitStack] | None] = ContextVar(
    "provider_record_access_row_scope", default=None
)


@contextmanager
def provider_record_access_row_scope(session: Session):
    """Retain only actual provider lock evidence in a synchronous exchange."""
    with ExitStack() as stack:
        token = _RECORD_ROWS.set((session, stack))
        try:
            yield
        finally:
            _RECORD_ROWS.reset(token)


@contextmanager
def _locked_record_owner_rows(session: Session, *, practice_id: Any, subject: str):
    # Exact original provider-record-owner locks and query order. Other callers
    # still execute only these two original statements; scoped exchanges read
    # the current member once after both locks, before publishing row evidence.
    practice = session.exec(
        select(Practice).where(Practice.id == practice_id).with_for_update()
    ).one()
    doctor = session.exec(
        select(Doctor)
        .where(Doctor.clerk_id == subject)
        .with_for_update(of=Doctor)
        .execution_options(populate_existing=True)
    ).one_or_none()
    scope = _RECORD_ROWS.get()
    if (
        doctor is None
        or scope is None
        or scope[0] is not session
        or _session_context.get() is not session
    ):
        yield doctor, None
        return
    member = session.exec(
        select(PracticeMember)
        .where(PracticeMember.doctor_id == doctor.id)
        .execution_options(populate_existing=True)
    ).one_or_none()
    transaction = session.get_nested_transaction() or session.get_transaction()
    if (
        transaction is None
        or doctor.deleted_at is not None
        or member is None
        or member.practice_id != practice_id
    ):
        yield doctor, None
        return
    proof = DoctorAccessRowOwnership(
        session,
        transaction,
        doctor.id,
        practice_id,
        subject,
        _ISSUER,
        (doctor, practice, member),
    )
    for name in ("after_transaction_create", "after_transaction_end"):
        event.listen(session, name, proof._invalidate)
    try:
        yield doctor, proof
    finally:
        proof._invalidate()
        for name in ("after_transaction_create", "after_transaction_end"):
            event.remove(session, name, proof._invalidate)


@contextmanager
def provider_record_owner_rows(session: Session, *, practice_id: Any, subject: str):
    """Own the exact original locks; keep evidence only in the matching scope."""
    scope = _RECORD_ROWS.get()
    if (
        scope is None
        or scope[0] is not session
        or _session_context.get() is not session
    ):
        with _locked_record_owner_rows(
            session, practice_id=practice_id, subject=subject
        ) as locked:
            yield locked[0]
        return
    locked = scope[1].enter_context(
        _locked_record_owner_rows(session, practice_id=practice_id, subject=subject)
    )
    token = _CAPTURE_ROWS.set(locked[1])
    scope[1].callback(_CAPTURE_ROWS.reset, token)
    yield locked[0]


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


def current_capture_doctor_rows(
    session: Session, doctor: Doctor
) -> DoctorAccessRowOwnership | None:
    """Current locked rows only; never an earlier access decision."""
    proof = _CAPTURE_ROWS.get()
    if proof is not None and proof.matches(
        session, doctor, practice_id=proof._practice_id
    ):
        return proof
    return None


def capture_owns_subject_rows(session: Session, subject: str) -> bool:
    """Current subject evidence only, before a fresh record-owner query."""
    proof = _CAPTURE_ROWS.get()
    return (
        proof is not None
        and _session_context.get() is session
        and proof.matches_subject(session, subject)
    )


def lookup_owns_provider_rows(
    session: Session, doctor: Doctor, ownership: DoctorAccessRowOwnership
) -> bool:
    """Only a matching synchronous lookup may combine current provider reads."""
    scope = _LOOKUP_ROWS.get()
    return (
        scope is not None
        and scope[0] is session
        and _session_context.get() is session
        and ownership.matches(session, doctor, practice_id=ownership._practice_id)
    )


def lookup_owns_subject_rows(session: Session, doctor_id: Any, subject: str) -> bool:
    """Exact current lookup owner, for combining reads without new authority."""
    scope, proof = _LOOKUP_ROWS.get(), _CAPTURE_ROWS.get()
    return (
        scope is not None
        and scope[0] is session
        and _session_context.get() is session
        and proof is not None
        and proof._doctor_id == doctor_id
        and proof.matches_subject(session, subject)
    )


@contextmanager
def capture_doctor_access_rows(
    session: Session, *, practice_id: Any, doctor_id: Any, subject: str
):
    # A detached/nontransactional adapter cannot mint row ownership. Keep its
    # original query path instead of interpreting its row objects as locks.
    if (
        not isinstance(session, Session)
        or (session.get_nested_transaction() or session.get_transaction()) is None
    ):
        yield None
        return
    previous = _session_context.get()
    if previous is not None and previous is not session:
        yield None
        return
    with lock_doctor_access_rows(
        session, practice_id=practice_id, doctor_id=doctor_id, subject=subject
    ) as locked:
        proof = None if locked is None else locked[3]
        token = _CAPTURE_ROWS.set(proof)
        session_token = _session_context.set(session)
        try:
            yield locked
        finally:
            _session_context.reset(session_token)
            _CAPTURE_ROWS.reset(token)


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
