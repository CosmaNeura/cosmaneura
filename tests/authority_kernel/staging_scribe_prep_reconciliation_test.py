"""Explicit reconciliation on disposable PostgreSQL; all identities invented."""

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

import pytest

import app.services.staging_scribe_prep_reconciliation as repair
from app.configuration.session_prep import SessionPrepProviderSettings
from app.services import authority
from app.services.provider_record_authority import validate_provider_record_scope
from app.services.scribe_capture_scope import (
    authorize_capture_binding,
    resolve_capture_binding,
)

from app.models.authority import AuthorityPersonOrigin
from app.models.doctor import Doctor
from app.models.pilot_access_grandfather_grant import PilotAccessGrandfatherGrant
from app.models.practice import PracticeMember
from app.models.practice_access_audit import PracticeAccessAudit
from app.models.provider_record_authority import (
    ProviderPracticeAuthority,
    ProviderRecordAuthority,
)
from app.models.scribe_capture_binding import ScribeCaptureBinding
from sqlmodel import select

from tests.authority_kernel.scribe_capture_scope_test import (
    SETTINGS,
)
from tests.authority_kernel.scribe_capture_scope_test import (
    capture_case as _capture_case,
)
from tests.authority_kernel.scribe_capture_scope_test import (
    capture_engine as _base_engine,
)

capture_case = _capture_case
base_engine = _base_engine


@pytest.fixture
def capture_engine(base_engine):
    return base_engine.execution_options(isolation_level="SERIALIZABLE")


def grant(session, doctor):
    now = datetime.now(UTC)
    doctor.email = f"{uuid4().hex}@synthetic.invalid"
    doctor.onboarding_completed_at = now
    session.add(doctor)
    session.add(
        PilotAccessGrandfatherGrant(
            clerk_id=doctor.clerk_id,
            doctor_id_snapshot=doctor.id,
            email_snapshot=doctor.email,
            email_sha256=hashlib.sha256(doctor.email.encode()).hexdigest(),
            source_ref="synthetic_reconciliation",
            source_sha256="a" * 64,
            grant_plan_sha256="b" * 64,
            granted_by_ref="synthetic_operator",
            grant_reason="Invented test fixture",
            granted_at=now,
        )
    )
    session.flush()


@pytest.fixture
def case(capture_case, monkeypatch):
    session, current = capture_case
    current.person_binding.actor_id = current.actor.id
    session.add(current.person_binding)
    session.flush()
    receipt = resolve_capture_binding(**current.arguments)
    current.capture_binding = session.exec(
        select(ScribeCaptureBinding).where(
            ScribeCaptureBinding.id == receipt.binding_ref
        )
    ).one()
    current.additional = Doctor(
        clerk_id=f"user_additional_{uuid4().hex}", biography=None
    )
    session.add(current.additional)
    session.flush()
    grant(session, current.doctor)
    grant(session, current.additional)
    current.origin = AuthorityPersonOrigin(
        person_id=current.person.id,
        origin="provider_entry",
        source_ref="scribe-staging-20260101-abc12345-provider",
    )
    session.add(current.origin)
    session.flush()
    monkeypatch.setattr(repair, "is_staging", lambda: True)
    monkeypatch.setattr(repair, "AUTHORITY_KERNEL_SETTINGS", SETTINGS)
    cohort = SessionPrepProviderSettings(
        True,
        frozenset(
            {
                current.doctor.clerk_id,
                current.additional.clerk_id,
            }
        ),
    )
    monkeypatch.setattr(repair, "SESSION_PREP_PROVIDER_SETTINGS", cohort)
    import app.configuration.session_prep as configuration

    monkeypatch.setattr(configuration, "SESSION_PREP_PROVIDER_SETTINGS", cohort)
    monkeypatch.setattr(
        authority, "validate_provider_record_scope", validate_provider_record_scope
    )
    current.kwargs = dict(
        additional_doctor_id=current.additional.id,
        capture_binding_id=current.capture_binding.id,
        operator_ref=f"synthetic_reconcile_{uuid4().hex}",
        preserve_solo_enrollment=True,
    )
    return session, current


def test_plan_writes_nothing_and_unreconciled_exchange_is_denied(case):
    session, f = case
    before = f.capture_binding.model_dump()
    plan = repair.reconcile(session, **f.kwargs)
    assert plan["status"] == "planned"
    assert (
        session.exec(
            select(PracticeMember).where(PracticeMember.doctor_id == f.additional.id)
        ).one_or_none()
        is None
    )
    assert (
        session.exec(
            select(ProviderRecordAuthority).where(
                ProviderRecordAuthority.appointment_id == f.appointment.id
            )
        ).one_or_none()
        is None
    )
    assert f.capture_binding.model_dump() == before
    with pytest.raises(authority.AuthorityRequestDeniedError):
        authority.exchange_context_token(
            clerk_issuer=SETTINGS.clerk_issuer,
            clerk_subject=f.doctor.clerk_id,
            requested_surface="provider_assistant",
            record_ref=str(f.appointment.id),
            session=session,
            settings=SETTINGS,
        )
    # The expected refusal must not leave its tentative new mapping behind.
    session.rollback()


def test_apply_and_idempotent_retry_preserve_scope_and_provenance(case):
    session, f = case
    before = f.capture_binding.model_dump()
    plan = repair.reconcile(session, **f.kwargs)
    result = repair.reconcile(
        session, **f.kwargs, confirm_plan_sha256=plan["plan_sha256"]
    )
    assert result["status"] == "applied"
    member = session.exec(
        select(PracticeMember).where(PracticeMember.doctor_id == f.additional.id)
    ).one()
    assert member.practice_id == f.practice.id and member.role == "workforce"
    assert member.attested_at is None and member.attestation_name is None
    assert f.capture_binding.model_dump() == before
    assert f.origin.source_ref == "scribe-staging-20260101-abc12345-provider"
    retry = repair.reconcile(
        session, **f.kwargs, confirm_plan_sha256=plan["plan_sha256"]
    )
    assert retry["status"] == "already_applied"
    assert session.exec(
        select(PracticeAccessAudit).where(
            PracticeAccessAudit.document_reference == f.kwargs["operator_ref"]
        )
    ).one()
    issued = authority.exchange_context_token(
        clerk_issuer=SETTINGS.clerk_issuer,
        clerk_subject=f.doctor.clerk_id,
        requested_surface="provider_assistant",
        record_ref=str(f.appointment.id),
        session=session,
        settings=SETTINGS,
    )
    context = authority.bootstrap_mount_context(
        mount_context_token=issued.context_token, session=session, settings=SETTINGS
    )
    assert "session-prep:read" in context.capabilities
    assert context.actor_id == str(f.actor.id) and context.tenant_id == str(f.tenant.id)
    turn = uuid4()
    resolved = authority.resolve_mount_context(
        workload_client_id=f.arguments["workload_client_id"],
        mount_context_token=issued.context_token,
        client_turn_id=turn,
        session=session,
        settings=SETTINGS,
    )
    reopened = authorize_capture_binding(
        session,
        binding_ref=str(f.capture_binding.id),
        workspace_id=f.capture_binding.workspace_id,
        workload_client_id=f.arguments["workload_client_id"],
        mount_context_token=issued.context_token,
        interaction_ref=resolved.interaction_ref,
        client_turn_id=turn,
        settings=SETTINGS,
        capture_settings=f.arguments["capture_settings"],
    )
    assert reopened.id == f.capture_binding.id and reopened.model_dump() == before


@pytest.mark.parametrize(
    "change",
    [
        "production",
        "cohort",
        "disabled",
        "inactive_member",
        "link_revoked",
        "identity_revoked",
        "binding_revoked",
        "scope_digest",
        "foreign_origin",
        "foreign_member",
        "wrong_hash",
        "changed_email",
        "subscription",
        "solo_without_confirmation",
        "unknown_origin",
    ],
)
def test_refusals_leave_membership_and_mapping_unwritten(case, monkeypatch, change):
    session, f = case
    plan = repair.reconcile(session, **f.kwargs)
    if change == "production":
        monkeypatch.setattr(repair, "is_staging", lambda: False)
    elif change == "cohort":
        monkeypatch.setattr(
            repair,
            "SESSION_PREP_PROVIDER_SETTINGS",
            SessionPrepProviderSettings(False, frozenset()),
        )
    elif change == "disabled":
        f.additional.deleted_at = datetime.now(UTC)
    elif change == "inactive_member":
        f.practice_member.seat_state = "inactive"
    elif change == "link_revoked":
        f.link.revoked_at = datetime.now(UTC)
    elif change == "identity_revoked":
        f.identity.revoked_at = datetime.now(UTC)
    elif change == "binding_revoked":
        f.capture_binding.revoked_at = datetime.now(UTC)
    elif change == "scope_digest":
        f.capture_binding.scope_sha256 = "0" * 64
    elif change == "foreign_origin":
        session.add(
            AuthorityPersonOrigin(
                person_id=f.person.id,
                origin="provider_entry",
                source_ref="patient_01j00000000000000000000000",
            )
        )
    elif change == "foreign_member":
        f.membership.membership_kind = "staff"
    elif change == "wrong_hash":
        plan["plan_sha256"] = "0" * 64
    elif change == "changed_email":
        f.additional.email = "other@synthetic.invalid"
    elif change == "subscription":
        f.practice.stripe_subscription_id = "sub_synthetic"
    elif change == "solo_without_confirmation":
        f.kwargs["preserve_solo_enrollment"] = False
    elif change == "unknown_origin":
        session.add(
            AuthorityPersonOrigin(
                person_id=f.person.id,
                origin="provider_entry",
                source_ref="unknown_import",
            )
        )
    session.flush()
    with pytest.raises(repair.ReconciliationRefused):
        repair.reconcile(session, **f.kwargs, confirm_plan_sha256=plan["plan_sha256"])
    assert (
        session.exec(
            select(PracticeMember).where(PracticeMember.doctor_id == f.additional.id)
        ).one_or_none()
        is None
    )


def test_failed_postcondition_rolls_back_all_new_rows(case, monkeypatch):
    session, f = case
    plan = repair.reconcile(session, **f.kwargs)
    monkeypatch.setattr(repair, "validate_provider_record_scope", lambda **_: None)
    with pytest.raises(repair.ReconciliationRefused), session.begin_nested():
        repair.reconcile(session, **f.kwargs, confirm_plan_sha256=plan["plan_sha256"])
    assert (
        session.exec(
            select(PracticeMember).where(PracticeMember.doctor_id == f.additional.id)
        ).one_or_none()
        is None
    )

    assert (
        session.exec(
            select(ProviderRecordAuthority).where(
                ProviderRecordAuthority.appointment_id == f.appointment.id
            )
        ).one_or_none()
        is None
    )
    assert (
        session.exec(
            select(PracticeAccessAudit).where(
                PracticeAccessAudit.document_reference == f.kwargs["operator_ref"]
            )
        ).one_or_none()
        is None
    )
    assert (
        session.exec(
            select(ProviderPracticeAuthority).where(
                ProviderPracticeAuthority.practice_id == f.practice.id
            )
        ).one_or_none()
        is None
    )


@pytest.mark.parametrize(
    "change", ["environment", "app", "host", "database", "owner_confirmation"]
)
def test_cli_rejects_wrong_target_before_opening_a_session(monkeypatch, capsys, change):
    import sys

    from scripts.staging_reconcile_scribe_prep import main

    monkeypatch.setenv("PYTHON_ENV", "staging")
    monkeypatch.setenv("CONTAINER_APP_NAME", "cosmaneura-staging-app")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://synthetic:synthetic@cosmaneura-staging-postgres.postgres.database.azure.com/staging",
    )
    arguments = [
        "operator",
        "--additional-doctor-id",
        "doctor_01j00000000000000000000000",
        "--capture-binding-id",
        "scribe_capture_binding_01j00000000000000000000000",
        "--operator-ref",
        "synthetic_operator_reference",
        "--confirm-owner-directed-pilot-membership",
    ]
    if change == "environment":
        monkeypatch.setenv("PYTHON_ENV", "production")
    elif change == "app":
        monkeypatch.setenv("CONTAINER_APP_NAME", "other-app")
    elif change == "host":
        monkeypatch.setenv(
            "DATABASE_URL", "postgresql://synthetic:synthetic@other.invalid/staging"
        )
    elif change == "database":
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql://synthetic:synthetic@cosmaneura-staging-postgres.postgres.database.azure.com/other",
        )
    else:
        arguments.pop()
    monkeypatch.setattr(sys, "argv", arguments)
    import activemodel.session_manager as sessions

    monkeypatch.setattr(
        sessions,
        "global_session",
        lambda: pytest.fail("target refusal opened a database session"),
    )
    assert main() == 1
    assert capsys.readouterr().out.strip() == '{"status": "refused_or_unconfirmed"}'
