import hashlib
import secrets
from datetime import datetime, timezone

import pytest

from activemodel.session_manager import get_session
from app.models.doctor import Doctor
from app.models.superadmin_capability import (
    SuperadminCapabilityAudit,
    SuperadminCapabilityAuditAction,
    SuperadminCapabilityEnvironment,
    SuperadminCapabilityGrant,
    SuperadminCapabilityKind,
)
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlmodel import SQLModel


def _grant() -> SuperadminCapabilityGrant:
    nonce = secrets.token_hex(6)
    subject = f"user_capability_model_{nonce}"
    email = f"capability-model-{nonce}@example.com"
    doctor = Doctor(clerk_id=subject, email=email, biography="").save()
    return SuperadminCapabilityGrant(
        environment=SuperadminCapabilityEnvironment.staging,
        capability_kind=SuperadminCapabilityKind.view_as,
        clerk_issuer="https://staging.example.clerk.accounts.dev",
        clerk_subject=subject,
        doctor_id=doctor.id,
        issuer_sha256=hashlib.sha256(
            b"https://staging.example.clerk.accounts.dev"
        ).hexdigest(),
        subject_sha256=hashlib.sha256(subject.encode()).hexdigest(),
        verified_primary_email_sha256=hashlib.sha256(email.encode()).hexdigest(),
        source_ref="controlled:model-test",
        source_sha256="a" * 64,
        grant_plan_sha256="b" * 64,
        granted_by_ref="ticket:model-test",
        grant_reason_code="reviewed_model_test",
        granted_at=datetime.now(timezone.utc),
    ).save()


def test_model_metadata_matches_migration_guards():
    grant = SQLModel.metadata.tables["superadmin_capability_grant"]
    audit = SQLModel.metadata.tables["superadmin_capability_audit"]
    assert {
        "superadmin_capability_grant_ck_superadmin_capability_grant_environment_check",
        "superadmin_capability_grant_ck_superadmin_capability_grant_kind_check",
        "superadmin_capability_grant_ck_superadmin_capability_grant_issuer_check",
        "superadmin_capability_grant_ck_superadmin_capability_grant_subject_check",
        "superadmin_capability_grant_ck_superadmin_capability_grant_digests_check",
        "superadmin_capability_grant_ck_superadmin_capability_grant_revocation_complete_check",
    }.issubset({constraint.name for constraint in grant.constraints})
    assert {
        "uq_superadmin_capability_active_identity_kind",
        "uq_superadmin_capability_active_doctor_kind",
        "superadmin_capability_grant_retain_until_idx",
    }.issubset({index.name for index in grant.indexes})
    assert {
        "superadmin_capability_audit_ck_superadmin_capability_audit_action_check",
        "superadmin_capability_audit_ck_superadmin_capability_audit_environment_check",
        "superadmin_capability_audit_ck_superadmin_capability_audit_kind_check",
        "superadmin_capability_audit_ck_superadmin_capability_audit_digests_check",
        "uq_superadmin_capability_audit_grant_action",
    }.issubset({constraint.name for constraint in audit.constraints})


def test_database_blocks_identity_mutation_and_audit_update_or_delete():
    grant = _grant()
    audit = SuperadminCapabilityAudit(
        grant_id=grant.id,
        action=SuperadminCapabilityAuditAction.granted,
        environment=SuperadminCapabilityEnvironment.staging,
        capability_kind=SuperadminCapabilityKind.view_as,
        identity_binding_sha256="c" * 64,
        evidence_sha256="b" * 64,
        operator_ref="ticket:model-test",
        reason_code="reviewed_model_test",
        occurred_at=datetime.now(timezone.utc),
        recorded_at=datetime.now(timezone.utc),
    ).save()

    with get_session() as session:
        session.begin_nested()
        with pytest.raises(DBAPIError, match="may only transition once to revoked"):
            session.execute(
                text(
                    "UPDATE superadmin_capability_grant "
                    "SET clerk_subject = :replacement WHERE id = :grant_id"
                ),
                params={
                    "replacement": f"user_replacement_{secrets.token_hex(6)}",
                    "grant_id": grant.id.uuid,
                },
            )
        session.rollback()

    with get_session() as session:
        persisted = session.get(SuperadminCapabilityAudit, audit.id)
        assert persisted is not None
        session.begin_nested()
        session.delete(persisted)
        with pytest.raises((DBAPIError, RuntimeError), match="append-only"):
            session.flush()
        session.rollback()
