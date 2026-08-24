import hashlib
import secrets
from datetime import UTC, datetime

import pytest

from activemodel.session_manager import get_session
from app.models.pilot_access_continuity_amendment import (
    PilotAccessContinuityAmendment,
    PilotAccessContinuityAmendmentIdentity,
)
from app.models.pilot_access_grandfather_grant import PilotAccessGrandfatherGrant
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlmodel import SQLModel


def _evidence():
    email = f"amendment-model-{secrets.token_hex(5)}@example.com"
    subject = f"user_amendment_model_{secrets.token_hex(6)}"
    now = datetime.now(UTC)
    grant = PilotAccessGrandfatherGrant(
        clerk_id=subject,
        email_snapshot=email,
        email_sha256=hashlib.sha256(email.encode()).hexdigest(),
        source_ref="change:model-amendment",
        source_sha256="a" * 64,
        grant_plan_sha256="b" * 64,
        granted_by_ref="change:model-operator",
        grant_reason="Test retained amendment evidence",
        granted_at=now,
    ).save()
    batch = PilotAccessContinuityAmendment(
        environment="production",
        clerk_issuer="https://model-test.clerk.invalid",
        source_ref="change:model-amendment",
        manifest_sha256="a" * 64,
        plan_sha256="b" * 64,
        applied_by_ref="change:model-operator",
        reason="Test retained amendment evidence",
        account_count=1,
        applied_at=now,
    ).save()
    identity = PilotAccessContinuityAmendmentIdentity(
        amendment_id=batch.id,
        grant_id=grant.id,
        clerk_issuer=batch.clerk_issuer,
        clerk_subject=subject,
        email_sha256=grant.email_sha256,
        safe_prelogin=True,
        continuity_basis="reviewed_operational_user_omission",
    ).save()
    return batch, identity


def test_model_metadata_matches_amendment_migration_guards():
    batch = SQLModel.metadata.tables["pilot_access_continuity_amendment"]
    identity = SQLModel.metadata.tables["pilot_access_continuity_amendment_identity"]
    assert {
        "pilot_access_continuity_amendment_ck_pilot_access_amendment_environment_check",
        "pilot_access_continuity_amendment_ck_pilot_access_amendment_clerk_issuer_check",
        "pilot_access_continuity_amendment_ck_pilot_access_amendment_manifest_sha256_check",
        "pilot_access_continuity_amendment_ck_pilot_access_amendment_plan_sha256_check",
        "pilot_access_continuity_amendment_ck_pilot_access_amendment_account_count_check",
    }.issubset({constraint.name for constraint in batch.constraints})
    assert {
        "uq_pilot_access_amendment_identity_issuer_subject",
        "uq_pilot_access_amendment_identity_email_sha256",
        "uq_pilot_access_amendment_identity_doctor",
    }.issubset({index.name for index in identity.indexes})


def test_database_keeps_amendment_and_identity_evidence_append_only():
    batch, identity = _evidence()
    with get_session() as session:
        savepoint = session.begin_nested()
        with pytest.raises(DBAPIError, match="append-only"):
            session.execute(
                text(
                    "UPDATE pilot_access_continuity_amendment "
                    "SET reason = 'changed' WHERE id = :row_id"
                ),
                params={"row_id": batch.id.uuid},
            )
        savepoint.rollback()

    with get_session() as session:
        savepoint = session.begin_nested()
        with pytest.raises(DBAPIError, match="append-only"):
            session.execute(
                text(
                    "DELETE FROM pilot_access_continuity_amendment_identity "
                    "WHERE id = :row_id"
                ),
                params={"row_id": identity.id.uuid},
            )
        savepoint.rollback()
