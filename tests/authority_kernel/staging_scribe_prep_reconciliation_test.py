"""Explicit reconciliation on disposable PostgreSQL; all identities invented."""

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from uuid import uuid4

import pytest

import app.services.staging_scribe_prep_reconciliation as repair
from app.configuration.session_prep import SessionPrepProviderSettings
from app.services import authority
from app.services.organization_access import lock_roster_enrollment_email
from app.services.pilot_access_lock import lock_pilot_access_lineage
from app.services.provider_record_authority import validate_provider_record_scope
from app.services.public_entitlements import ActiveClinicianSeatLimitReached
from app.services.scribe_capture_scope import (
    authorize_capture_binding,
    resolve_capture_binding,
)

from app.models.appointment import Appointment
from app.models.authority import (
    AuthorityPerson,
    AuthorityPersonBinding,
    AuthorityPersonOrigin,
)
from app.models.doctor import Doctor
from app.models.organization_access import ProviderOrganization
from app.models.patient import Patient
from app.models.pilot_access_grandfather_grant import PilotAccessGrandfatherGrant
from app.models.practice import PracticeMember, lock_practice_seats
from app.models.practice_access_audit import PracticeAccessAudit
from app.models.provider_record_authority import (
    ProviderPracticeAuthority,
    ProviderRecordAuthority,
)
from app.models.scribe_capture_binding import ScribeCaptureBinding
from sqlalchemy import text
from sqlmodel import Session, select

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
    return base_engine.execution_options(isolation_level="READ COMMITTED")


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


def install_exact_authority_pair(session, f, *, conflicting_doctor=False):
    mapping = ProviderPracticeAuthority(
        practice_id=f.practice.id, tenant_id=f.tenant.id
    )
    session.add(mapping)
    session.flush()
    record = ProviderRecordAuthority(
        binding_id=f.person_binding.id,
        practice_authority_id=mapping.id,
        doctor_id=f.additional.id if conflicting_doctor else f.doctor.id,
        appointment_id=f.appointment.id,
        patient_id=f.patient.id,
        context_snapshot_ref=f"context_prep_{uuid4().hex}",
    )
    session.add(record)
    session.flush()
    return mapping, record


def test_existing_exact_pair_is_preserved_while_membership_is_added(case):
    session, f = case
    mapping, record = install_exact_authority_pair(session, f)
    before = (mapping.model_dump(), record.model_dump(), f.capture_binding.model_dump())
    plan = repair.reconcile(session, **f.kwargs)
    assert plan["plan"]["existing_authority"]["record_mapping"] == str(record.id)
    assert (
        session.exec(
            select(PracticeMember).where(PracticeMember.doctor_id == f.additional.id)
        ).one_or_none()
        is None
    )
    assert (
        repair.reconcile(session, **f.kwargs, confirm_plan_sha256=plan["plan_sha256"])[
            "status"
        ]
        == "applied"
    )
    assert (
        mapping.model_dump(),
        record.model_dump(),
        f.capture_binding.model_dump(),
    ) == before
    member = session.exec(
        select(PracticeMember).where(PracticeMember.doctor_id == f.additional.id)
    ).one()
    assert member.practice_id == f.practice.id and member.role == "workforce"
    assert member.attested_at is None and member.attestation_name is None
    assert (
        validate_provider_record_scope(
            session=session,
            binding=f.person_binding,
            actor_id=f.actor.id,
            clerk_subject=f.doctor.clerk_id,
        )
        is not None
    )
    assert (
        repair.reconcile(session, **f.kwargs, confirm_plan_sha256=plan["plan_sha256"])[
            "status"
        ]
        == "already_applied"
    )


@pytest.mark.parametrize("drift", ["new_pair", "context_snapshot"])
def test_existing_mapping_state_is_bound_into_plan(case, drift):
    session, f = case
    record = None
    if drift == "context_snapshot":
        _, record = install_exact_authority_pair(session, f)
    plan = repair.reconcile(session, **f.kwargs)
    if drift == "new_pair":
        install_exact_authority_pair(session, f)
    else:
        plan["plan"]["existing_authority"]["context_snapshot_sha256"] = "0" * 64
        plan["plan_sha256"] = repair._digest(plan["plan"])
    with pytest.raises(repair.ReconciliationRefused):
        repair.reconcile(session, **f.kwargs, confirm_plan_sha256=plan["plan_sha256"])
    assert (
        session.exec(
            select(PracticeMember).where(PracticeMember.doctor_id == f.additional.id)
        ).one_or_none()
        is None
    )


@pytest.mark.parametrize("conflict", ["partial_pair", "different_doctor"])
def test_partial_or_conflicting_existing_authority_is_refused(case, conflict):
    session, f = case
    if conflict == "partial_pair":
        session.add(
            ProviderPracticeAuthority(practice_id=f.practice.id, tenant_id=f.tenant.id)
        )
    else:
        install_exact_authority_pair(session, f, conflicting_doctor=True)
    session.flush()
    with pytest.raises(repair.ReconciliationRefused):
        repair.reconcile(session, **f.kwargs)


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
    additional_patient = Patient(
        doctor_id=f.additional.id,
        name="Invented additional patient",
        date_of_birth=datetime(1990, 1, 1, tzinfo=UTC),
    )
    session.add(additional_patient)
    session.flush()
    additional_visit = Appointment(
        doctor_id=f.additional.id,
        patient_id=additional_patient.id,
        external_id=None,
        appointment_date=datetime.now(UTC),
    )
    session.add(additional_visit)
    session.flush()
    additional_token = authority.exchange_context_token(
        clerk_issuer=SETTINGS.clerk_issuer,
        clerk_subject=f.additional.clerk_id,
        requested_surface="provider_assistant",
        record_ref=str(additional_visit.id),
        session=session,
        settings=SETTINGS,
    )
    additional_context = authority.bootstrap_mount_context(
        mount_context_token=additional_token.context_token,
        session=session,
        settings=SETTINGS,
    )
    assert additional_context.tenant_id == str(f.tenant.id)
    assert additional_context.actor_id != str(f.actor.id)
    assert "session-prep:read" in additional_context.capabilities
    with pytest.raises(authority.AuthorityRequestDeniedError):
        authority.exchange_context_token(
            clerk_issuer=SETTINGS.clerk_issuer,
            clerk_subject=f.additional.clerk_id,
            requested_surface="provider_assistant",
            record_ref=str(f.appointment.id),
            session=session,
            settings=SETTINGS,
        )


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
        "nonprovider_tenant_membership",
        "wrong_hash",
        "changed_email",
        "subscription",
        "solo_without_confirmation",
        "unknown_origin",
        "reserved_practice",
        "managed_domain",
        "another_binding_for_same_person",
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
        assert lock_practice_seats(session, f.practice.id) is not None
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
    elif change == "nonprovider_tenant_membership":
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
    elif change == "reserved_practice":
        f.practice.negotiated_baa_required = True
        f.practice.baa_version = None
        f.practice.baa_accepted_at = None
    elif change == "managed_domain":
        domain = f.additional.email.rsplit("@", 1)[1]
        session.add(
            ProviderOrganization(
                legal_name="Invented managed organization",
                slug=uuid4().hex,
                email_domain=domain,
                access_code_salt="a" * 32,
                access_code_digest=uuid4().hex * 2,
                access_code_created_at=datetime.now(UTC),
            )
        )
    elif change == "another_binding_for_same_person":
        session.add(
            AuthorityPersonBinding(
                tenant_id=f.tenant.id,
                person_id=f.person.id,
                actor_id=f.actor.id,
                binding_ref=f"synthetic_other_{uuid4().hex}",
                binding_kind="provider_record",
                verified_at=datetime.now(UTC),
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


def test_seat_limit_refuses_addition(case):
    session, f = case
    assert lock_practice_seats(session, f.practice.id) is not None
    for _ in range(19):
        doctor = Doctor(clerk_id=f"user_seat_{uuid4().hex}", biography=None)
        session.add(doctor)
        session.flush()
        session.add(
            PracticeMember(
                doctor_id=doctor.id, practice_id=f.practice.id, role="workforce"
            )
        )
    session.flush()
    with pytest.raises(ActiveClinicianSeatLimitReached):
        repair.reconcile(session, **f.kwargs)


def test_other_legacy_binding_is_reported_and_never_adopted(case):
    session, f = case
    person = AuthorityPerson(lifecycle_state="patient")
    session.add(person)
    session.flush()
    other = AuthorityPersonBinding(
        tenant_id=f.tenant.id,
        person_id=person.id,
        actor_id=None,
        binding_ref=f"synthetic_old_{uuid4().hex}",
        binding_kind="provider_record",
        verified_at=datetime.now(UTC),
    )
    session.add(other)
    session.flush()
    before = other.model_dump()
    plan = repair.reconcile(session, **f.kwargs)
    assert plan["plan"]["other_tenant_binding_count"] == 1
    assert plan["plan"]["tenant_capture_count"] == 1
    repair.reconcile(session, **f.kwargs, confirm_plan_sha256=plan["plan_sha256"])
    assert other.model_dump() == before
    assert (
        session.exec(
            select(ProviderRecordAuthority).where(
                ProviderRecordAuthority.binding_id == other.id
            )
        ).one_or_none()
        is None
    )


def test_second_capture_under_a_different_person_is_refused(case):
    session, f = case
    person = AuthorityPerson(lifecycle_state="patient")
    appointment = Appointment(
        doctor_id=f.doctor.id,
        patient_id=f.patient.id,
        external_id=None,
        appointment_date=datetime.now(UTC),
    )
    session.add(person)
    session.add(appointment)
    session.flush()
    other_binding = AuthorityPersonBinding(
        tenant_id=f.tenant.id,
        person_id=person.id,
        actor_id=f.actor.id,
        binding_ref=str(appointment.id),
        binding_kind="provider_record",
        verified_at=datetime.now(UTC),
    )
    session.add(other_binding)
    session.flush()
    assert repair.reconcile(session, **f.kwargs)["status"] == "planned"
    values = f.capture_binding.model_dump(exclude={"id", "created_at", "updated_at"})
    values.update(
        workspace_id=uuid4(),
        person_binding_id=other_binding.id,
        appointment_id=appointment.id,
    )
    session.add(ScribeCaptureBinding(**values))
    session.flush()
    with pytest.raises(repair.ReconciliationRefused):
        repair.reconcile(session, **f.kwargs)


@pytest.mark.parametrize("lock_kind", ["lineage", "email"])
def test_waited_advisory_lock_observes_committed_changes(base_engine, lock_kind):
    """Reproduce the stale-snapshot race with two real PostgreSQL sessions.

    Only a uniquely named disposable fixture table is created and removed.
    This checks lock/snapshot semantics, not a simulated roster or seat writer.
    """
    table = f"reconciliation_snapshot_{uuid4().hex}"
    email = f"{uuid4().hex}@synthetic.invalid"

    def acquire(session):
        if lock_kind == "lineage":
            lock_pilot_access_lineage(session)
        else:
            lock_roster_enrollment_email(session, email)

    started = Event()
    backend = []

    def waiting_reader():
        with Session(base_engine) as session:
            session.connection(execution_options={"isolation_level": "READ COMMITTED"})
            backend.append(session.execute(text("SELECT pg_backend_pid()")).scalar())
            started.set()
            acquire(session)
            return session.execute(text(f"SELECT value FROM {table}")).scalar()

    with base_engine.begin() as connection:
        connection.execute(text(f"CREATE TABLE {table} (value integer NOT NULL)"))
        connection.execute(text(f"INSERT INTO {table} VALUES (0)"))
    try:
        with (
            ThreadPoolExecutor(max_workers=1) as executor,
            Session(base_engine) as writer,
        ):
            acquire(writer)
            future = executor.submit(waiting_reader)
            assert started.wait(5)
            deadline = time.monotonic() + 5
            waiting = False
            while time.monotonic() < deadline:
                waiting = writer.execute(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid=:pid AND locktype='advisory' AND NOT granted)"
                    ),
                    {"pid": backend[0]},
                ).scalar()
                if waiting:
                    break
                time.sleep(0.01)
            assert waiting, "Reader did not reach the advisory lock"
            writer.execute(text(f"UPDATE {table} SET value=1"))
            writer.commit()
            assert future.result(timeout=5) == 1
    finally:
        with base_engine.begin() as connection:
            connection.execute(text(f"DROP TABLE {table}"))


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
    assert json.loads(capsys.readouterr().out) == {
        "status": "refused_or_unconfirmed",
        "phase": "target_validation",
    }
