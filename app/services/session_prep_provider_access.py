"""Session Prep is included for every active provider account.

Account activation is independent of practice setup, onboarding, billing and
legacy grant snapshots. Explicit account stops remain effective. Record and
note ownership are checked separately on every read or action; availability
never grants access to another account's charts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast

from app.services.scribe_cancellation_access import doctor_cancellation_denial

from activemodel.session_manager import get_session
from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember
from sqlmodel import Session, col, select

SessionPrepProviderAccessDenial = Literal[
    "provider_unavailable",
    "scribe_cancelled_read_only",
    "scribe_export_period_expired",
]


@dataclass(frozen=True)
class SessionPrepProviderAccessDecision:
    allowed: bool
    denial_reason: SessionPrepProviderAccessDenial | None
    practice: Practice | None = None


def _denied(
    reason: SessionPrepProviderAccessDenial,
) -> SessionPrepProviderAccessDecision:
    return SessionPrepProviderAccessDecision(allowed=False, denial_reason=reason)


def resolve_session_prep_provider_access(
    doctor: Doctor,
    *,
    session: Session | None = None,
    at: datetime | None = None,
) -> SessionPrepProviderAccessDecision:
    """Read fresh account state without a Session Prep enrollment requirement.

The optional practice is a compatibility namespace for already-bound records,
not an eligibility requirement. Accounts without one use their own authority
namespace when an authenticated owned-record exchange is requested.
"""

    current_at = at or datetime.now(UTC)
    if session is None:
        with get_session() as managed:
            return resolve_session_prep_provider_access(
                doctor,
                session=cast(Session, managed),
                at=current_at,
            )

    rows = session.exec(
        select(Doctor, PracticeMember, Practice)
        .select_from(Doctor)
        .outerjoin(PracticeMember, col(PracticeMember.doctor_id) == col(Doctor.id))
        .outerjoin(Practice, col(Practice.id) == col(PracticeMember.practice_id))
        .where(Doctor.id == doctor.id, Doctor.clerk_id == doctor.clerk_id)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if rows is None:
        return _denied("provider_unavailable")
    current_doctor, _member, practice = rows
    if current_doctor.deleted_at is not None:
        return _denied("provider_unavailable")
    cancellation = doctor_cancellation_denial(
        current_doctor, session=session, at=current_at
    )
    if cancellation is not None:
        return _denied(cancellation)
    return SessionPrepProviderAccessDecision(
        allowed=True,
        denial_reason=None,
        practice=practice,
    )
