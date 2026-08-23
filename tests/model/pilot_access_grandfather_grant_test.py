import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import pytest

from activemodel.session_manager import get_session
from app.models.pilot_access_grandfather_grant import PilotAccessGrandfatherGrant
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlmodel import SQLModel


def _grant(*, granted_at: datetime | None = None) -> PilotAccessGrandfatherGrant:
    email = f"model-{secrets.token_hex(5)}@example.com"
    return PilotAccessGrandfatherGrant(
        clerk_id=f"user_model_{secrets.token_hex(8)}",
        email_snapshot=email,
        email_sha256=hashlib.sha256(email.encode()).hexdigest(),
        source_ref="clerk-export:model-guard",
        source_sha256="c" * 64,
        grant_plan_sha256="a" * 64,
        granted_by_ref="change:model-guard",
        grant_reason="Exercise retained grant constraints",
        granted_at=granted_at or datetime.now(timezone.utc),
    ).save()


def test_model_metadata_matches_grandfather_migration_guards():
    table = SQLModel.metadata.tables["pilot_access_grandfather_grant"]
    constraint_names = {constraint.name for constraint in table.constraints}
    index_names = {index.name for index in table.indexes}

    assert {
        "pilot_access_grandfather_grant_ck_pilot_access_grant_clerk_subject_check",
        "pilot_access_grandfather_grant_ck_pilot_access_grant_email_normalized_check",
        "pilot_access_grandfather_grant_ck_pilot_access_grant_email_sha256_check",
        "pilot_access_grandfather_grant_ck_pilot_access_grant_plan_sha256_check",
        "pilot_access_grandfather_grant_ck_pilot_access_grant_source_sha256_check",
        "pilot_access_grandfather_grant_ck_pilot_access_grant_revocation_complete_check",
    }.issubset(constraint_names)
    assert {
        "uq_pilot_access_grant_active_clerk",
        "uq_pilot_access_grant_active_doctor",
        "uq_pilot_access_grant_active_email_sha256",
        "pilot_access_grant_retain_until_idx",
    }.issubset(index_names)


def test_database_rejects_incomplete_or_short_retention_revocation():
    grant = _grant()
    with get_session() as session:
        persisted = session.get(PilotAccessGrandfatherGrant, grant.id)
        assert persisted is not None
        session.begin_nested()
        persisted.revoked_at = datetime.now(timezone.utc)
        persisted.revoked_by_ref = "change:incomplete-revoke"
        persisted.revocation_reason = "Incomplete retained evidence"
        persisted.revocation_plan_sha256 = "b" * 64
        persisted.retain_until = persisted.revoked_at + timedelta(days=365)
        session.add(persisted)
        with pytest.raises(
            DBAPIError,
            match="violates check constraint",
        ):
            session.flush()
        session.rollback()


def test_database_blocks_grant_evidence_update_and_early_delete():
    grant = _grant()
    with get_session() as session:
        session.begin_nested()
        with pytest.raises(DBAPIError, match="may only transition to revoked"):
            session.execute(
                text(
                    "UPDATE pilot_access_grandfather_grant "
                    "SET source_ref = 'changed' WHERE id = :grant_id"
                ),
                params={"grant_id": grant.id.uuid},
            )
        session.rollback()

    with get_session() as session:
        persisted = session.get(PilotAccessGrandfatherGrant, grant.id)
        assert persisted is not None
        savepoint = session.begin_nested()
        session.delete(persisted)
        with pytest.raises(DBAPIError, match="still retained"):
            session.flush()
        savepoint.rollback()
