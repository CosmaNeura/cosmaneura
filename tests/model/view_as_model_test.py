import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import pytest

from activemodel.session_manager import get_session
from app.models.doctor import Doctor
from app.models.superadmin_capability import (
    SuperadminCapabilityEnvironment,
    SuperadminCapabilityGrant,
    SuperadminCapabilityKind,
)
from app.models.view_as import ViewAsAuditEvent, ViewAsSession
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlmodel import SQLModel


def _records() -> tuple[ViewAsSession, ViewAsAuditEvent]:
    nonce = secrets.token_hex(6)
    actor = Doctor(
        clerk_id=f"user_view_as_model_actor_{nonce}",
        email=f"view-as-model-actor-{nonce}@example.com",
        biography="",
    ).save()
    target = Doctor(
        clerk_id=f"user_view_as_model_target_{nonce}",
        email=f"view-as-model-target-{nonce}@example.com",
        biography="",
    ).save()
    issuer = "https://staging.example.clerk.accounts.dev"
    now = datetime.now(timezone.utc)
    grant = SuperadminCapabilityGrant(
        environment=SuperadminCapabilityEnvironment.staging,
        capability_kind=SuperadminCapabilityKind.view_as,
        clerk_issuer=issuer,
        clerk_subject=actor.clerk_id,
        doctor_id=actor.id,
        issuer_sha256=hashlib.sha256(issuer.encode()).hexdigest(),
        subject_sha256=hashlib.sha256(actor.clerk_id.encode()).hexdigest(),
        verified_primary_email_sha256=hashlib.sha256(
            (actor.email or "").encode()
        ).hexdigest(),
        source_ref="controlled:view-as-model-test",
        source_sha256="a" * 64,
        grant_plan_sha256="b" * 64,
        granted_by_ref="ticket:view-as-model-test",
        grant_reason_code="reviewed_model_test",
        granted_at=now,
    ).save()
    view_as_session = ViewAsSession(
        environment="staging",
        capability_grant_id=grant.id,
        actor_doctor_id=actor.id,
        target_doctor_id=target.id,
        actor_issuer_sha256="c" * 64,
        actor_subject_sha256="d" * 64,
        clerk_session_sha256="e" * 64,
        browser_nonce_sha256="f" * 64,
        started_at=now,
        absolute_expires_at=now + timedelta(minutes=15),
    ).save()
    audit = ViewAsAuditEvent(
        environment="staging",
        event_type="began",
        outcome="accepted",
        reason_code="support_review",
        occurred_at=now,
        event_digest="1" * 64,
        session_id=view_as_session.id,
        capability_grant_id=grant.id,
        actor_doctor_id=actor.id,
        target_doctor_id=target.id,
    ).save()
    return view_as_session, audit


def test_model_metadata_matches_view_as_migration_guards():
    view_as_session = SQLModel.metadata.tables["view_as_session"]
    audit = SQLModel.metadata.tables["view_as_audit_event"]

    assert {
        "view_as_session_ck_view_as_session_environment_check",
        "view_as_session_ck_view_as_session_distinct_doctors_check",
        "view_as_session_ck_view_as_session_actor_issuer_sha256_check",
        "view_as_session_ck_view_as_session_actor_subject_sha256_check",
        "view_as_session_ck_view_as_session_clerk_session_sha256_check",
        "view_as_session_ck_view_as_session_browser_nonce_sha256_check",
        "view_as_session_ck_view_as_session_expiry_after_start_check",
        "view_as_session_ck_view_as_session_end_complete_check",
        "view_as_session_ck_view_as_session_end_reason_check",
    }.issubset({constraint.name for constraint in view_as_session.constraints})
    assert {
        "uq_view_as_session_active_actor",
        "view_as_session_expiry_idx",
    }.issubset({index.name for index in view_as_session.indexes})
    assert {
        "view_as_audit_event_ck_view_as_audit_environment_check",
        "view_as_audit_event_ck_view_as_audit_event_type_check",
        "view_as_audit_event_ck_view_as_audit_outcome_check",
        "view_as_audit_event_ck_view_as_audit_event_digest_check",
    }.issubset({constraint.name for constraint in audit.constraints})


def test_database_allows_only_session_end_and_rejects_audit_mutation():
    view_as_session, audit = _records()

    with get_session() as session:
        session.begin_nested()
        with pytest.raises(DBAPIError, match="may only transition to ended"):
            session.execute(
                text(
                    "UPDATE view_as_session SET target_doctor_id = actor_doctor_id "
                    "WHERE id = :session_id"
                ),
                params={"session_id": view_as_session.id.uuid},
            )
        session.rollback()

    with get_session() as session:
        persisted = session.get(ViewAsAuditEvent, audit.id)
        assert persisted is not None
        session.begin_nested()
        session.delete(persisted)
        with pytest.raises(DBAPIError, match="append-only"):
            session.flush()
        session.rollback()
