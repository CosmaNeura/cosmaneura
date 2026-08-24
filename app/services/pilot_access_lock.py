"""Transaction-wide serialization for pilot continuity lineage mutations."""

from __future__ import annotations

import hashlib

from sqlalchemy import func
from sqlmodel import Session, select

_PILOT_ACCESS_LINEAGE_LOCK_KEY = int.from_bytes(
    hashlib.sha256(b"pilot-access:continuity-lineage:v1").digest()[:8],
    byteorder="big",
    signed=True,
)


def lock_pilot_access_lineage(session: Session) -> None:
    """Serialize every mutation of the audited pilot continuity lineage.

    Callers must acquire this transaction-scoped advisory lock before any
    subject, practice, or row lock. PostgreSQL releases it automatically on
    commit or rollback.
    """

    session.exec(
        select(func.pg_advisory_xact_lock(_PILOT_ACCESS_LINEAGE_LOCK_KEY))
    ).one()
