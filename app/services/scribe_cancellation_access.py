"""Read the durable account fence, without changing identities or membership.

The ledger-ready switch is an additive-migration deployment guard, not the
intake switch. Closing intake therefore never restores canceled processing.
Existing practice/BAA/record authority must still pass for an allowed read.
"""

from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.engine import Connection

AccessPurpose = Literal["process", "read"]
CancellationAccessDenial = Literal[
    "scribe_cancelled_read_only", "scribe_export_period_expired"
]


def cancellation_denial(
    connection: Connection,
    *,
    doctor_ref: str,
    purpose: AccessPurpose,
    now: datetime,
) -> CancellationAccessDenial | None:
    """Exact Doctor scope only; no tenant, practice, or colleague authority."""
    from app.models.scribe_cancellation import scribe_cancellation_operation

    if purpose not in {"process", "read"} or now.tzinfo is None:
        raise ValueError("invalid_cancellation_access")
    row = connection.execute(
        select(scribe_cancellation_operation.c.export_until).where(
            scribe_cancellation_operation.c.doctor_ref == doctor_ref
        )
    ).one_or_none()
    if row is None:
        return None
    until = row[0]
    until = until.replace(tzinfo=UTC) if until.tzinfo is None else until.astimezone(UTC)
    if purpose == "process":
        return "scribe_cancelled_read_only"
    if now.astimezone(UTC) >= until:
        return "scribe_export_period_expired"
    return None


def doctor_cancellation_denial(
    doctor,
    *,
    purpose: AccessPurpose = "process",
    at: datetime | None = None,
    session=None,
) -> CancellationAccessDenial | None:
    # Lazy configuration permits schema-first, default-off image rollout.
    from app.configuration.scribe_cancellation import SCRIBE_CANCELLATION_SETTINGS

    if purpose not in {"process", "read"}:
        raise ValueError("invalid_cancellation_access")
    if not SCRIBE_CANCELLATION_SETTINGS.ledger_ready:
        return None
    if session is None:
        from activemodel.session_manager import get_session

        with get_session() as managed:
            return doctor_cancellation_denial(
                doctor, purpose=purpose, at=at, session=managed
            )
    return cancellation_denial(
        session.connection(),
        doctor_ref=str(doctor.id),
        purpose=purpose,
        now=at or datetime.now(UTC),
    )
