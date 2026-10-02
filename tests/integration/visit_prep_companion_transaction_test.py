"""Owned real PostgreSQL; immutable package parity and no disclosure on failure."""

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event
from time import monotonic
from types import SimpleNamespace
from uuid import uuid4

import pytest
from typeid import TypeID

from app.configuration import authority, service_auth
from app.configuration.clerk import VisitPrepPhysicianPrincipal
from app.configuration.service_auth import (
    EntraAgentAuthSettings,
    VerifiedCredentialWindow,
    VisitPrepWorkloadPrincipal,
)
from app.routes.visit_prep_synthesis import _synthesis_response
from app.services import authority as authority_service
from app.services import companion_encounters
from app.services import visit_prep_companion as receiver
from app.services.companion_provider_provisioning import provision_companion_provider
from app.services.companion_records import (
    RecordBudget,
    RecordDeadline,
    RecordUnavailable,
)
from app.services.visit_prep_synthesis_publication import get_visit_prep_synthesis
from app.services.visit_preps import VisitPrepNotFound

from activemodel.session_manager import get_session, global_session
from app.models.authority import AuthorityAuditEvent, AuthorityTenantMembership
from app.models.scribe_capture_binding import ScribeCaptureProviderLink
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlmodel import select

from tests.integration.visit_prep_contribution_lock_test import _observe_waiting_lock
from tests.services.visit_prep_contribution_commands_test import _correct
from tests.services.visit_prep_contribution_projection_test import _prepared
from tests.services.visit_prep_packages_test import _scope
from tests.services.visit_prep_synthesis_artifact_test import _artifact


def _case(monkeypatch, *, complete=True, caregiver=False, fixture=None):
    fixture = fixture or _prepared(
        monkeypatch,
        "companion" + uuid4().hex[:6],
        complete=complete,
        role="caregiver" if caregiver else "patient",
    )
    settings = replace(
        authority.AUTHORITY_KERNEL_SETTINGS,
        enabled=True,
        clerk_issuer="https://companion.clerk.invalid",
    )
    policy = EntraAgentAuthSettings(
        enabled=True,
        tenant_id=str(uuid4()),
        audience=str(uuid4()),
        sovereign_client_ids=frozenset({str(uuid4())}),
    )
    monkeypatch.setattr(authority, "AUTHORITY_KERNEL_SETTINGS", settings)
    monkeypatch.setattr(service_auth, "COMPANION_VISIT_PREP_AUTH_SETTINGS", policy)
    with global_session() as session:
        doctor = session.get(type(fixture.doctor), fixture.doctor.id)
        doctor.last_active_at = datetime.now(UTC)
        doctor.clerk_id = "user_vp" + uuid4().hex
        fixture.doctor.clerk_id = doctor.clerk_id
        session.add(doctor)
        session.commit()
        arguments = dict(
            doctor_id=fixture.doctor.id,
            practice_id=fixture.practice.id,
            tenant_id=TypeID(prefix="authority_tenant"),
            scribe_tenant_id=uuid4(),
            verified_primary_email=fixture.doctor.email,
            verified_clerk_subject=fixture.doctor.clerk_id,
            email_confirmation=fixture.doctor.email,
            review_ref="review_" + uuid4().hex,
            operator_ref="operator_" + uuid4().hex,
            settings=settings,
            create_missing_authority=True,
        )
        plan = provision_companion_provider(session, **arguments)
        provision_companion_provider(
            session, **arguments, apply=True, confirm_plan_sha256=plan.plan_sha256
        )
        connection, _ = companion_encounters._provider(
            session,
            issuer=settings.clerk_issuer,
            subject=fixture.doctor.clerk_id,
            session_id="sess_invented",
            tenant_ref=str(arguments["tenant_id"]),
            settings=settings,
        )
        session.rollback()
    now = int(datetime.now(UTC).timestamp())
    window = VerifiedCredentialWindow(now - 10, now - 10, now + 300)
    physician = VisitPrepPhysicianPrincipal(
        settings.clerk_issuer, fixture.doctor.clerk_id, "sess_invented", window
    )
    workload = VisitPrepWorkloadPrincipal(next(iter(policy.allowed_client_ids)), window)
    request = receiver.VisitPrepCompanionRequest(
        contract_version="1",
        action="visit-prep.open",
        purpose="provider_previsit_review",
        preparation_ref=str(fixture.prep_id),
        expected_actor_ref=connection.actor_ref,
        expected_tenant_ref=connection.tenant_ref,
        connection_sha256=connection.connection_sha256,
        workspace_id=uuid4(),
        request_nonce=uuid4(),
    )
    return SimpleNamespace(
        fixture=fixture,
        settings=settings,
        policy=policy,
        physician=physician,
        workload=workload,
        request=request,
    )


def _serialize(case, view, checked_at):
    return json.dumps(
        {
            "contract_version": "1",
            "preparation_ref": case.request.preparation_ref,
            "connection_sha256": case.request.connection_sha256,
            "workspace_id": str(case.request.workspace_id),
            "request_nonce": str(case.request.request_nonce),
            "checked_at": checked_at.isoformat(),
            "package": _synthesis_response(view).model_dump(mode="json"),
        },
        ensure_ascii=False,
    ).encode("utf-8")


def _read(case, *, serialize=None, **overrides):
    return receiver.read_visit_prep_companion(
        workload=case.workload,
        physician=case.physician,
        request=case.request,
        deadline=RecordBudget(monotonic() + 15),
        serialize=serialize or (lambda view, at: _serialize(case, view, at)),
        **overrides,
    )


def _audits(case):
    with get_session() as session:
        return session.exec(
            select(AuthorityAuditEvent).where(
                AuthorityAuditEvent.resource_ref == case.request.preparation_ref,
                AuthorityAuditEvent.event_type.in_(
                    ["visit_prep.synthesis_read", "visit_prep.companion_open"]
                ),
            )
        ).all()


@pytest.mark.parametrize(
    "ready,caregiver", [(False, False), (True, False), (False, True)]
)
def test_chart_free_package_matches_dashboard_and_commits_two_content_free_audits(
    monkeypatch, ready, caregiver
):
    case = _case(monkeypatch, complete=not caregiver, caregiver=caregiver)
    if ready:
        _artifact(case.fixture).save()
    dashboard = _synthesis_response(
        get_visit_prep_synthesis(
            **_scope(case.fixture), visit_prep_id=case.fixture.prep_id
        )
    ).model_dump(mode="json")
    before = len(_audits(case))
    body = json.loads(_read(case))
    assert body["package"] == dashboard
    assert body["package"]["sources"]["preparation"]["patient_id"] is None
    assert body["package"]["sources"]["preparation"]["appointment_id"] is None
    assert body["package"]["status"] == ("ready" if ready else "missing")
    assert len(_audits(case)) == before + 2
    action = [a for a in _audits(case) if a.event_type == "visit_prep.companion_open"][
        -1
    ]
    assert action.client_event_id == case.request.request_nonce
    assert action.workload_client_id == case.workload.client_id
    assert action.interaction_id is None
    assert "invented health story" not in repr(action)
    assert case.physician.session_id not in repr(action.platform_refs)


def test_retry_reauthorizes_current_correction_instead_of_replaying_retained_content(
    monkeypatch,
):
    case = _case(monkeypatch)
    _artifact(case.fixture).save()
    first = json.loads(_read(case))["package"]
    _correct(case.fixture, answer="A corrected invented story.")
    second = json.loads(_read(case))["package"]
    assert first["status"] == "ready" and second["status"] == "stale"
    assert second["brief"] is None and second["artifact_id"] is None
    assert first["sources"]["source_version"] != second["sources"]["source_version"]
    assert (
        len([a for a in _audits(case) if a.event_type == "visit_prep.companion_open"])
        == 2
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("preparation_ref", str(TypeID(prefix="visit_prep"))),
        ("expected_actor_ref", str(TypeID(prefix="authority_actor"))),
        ("expected_tenant_ref", str(TypeID(prefix="authority_tenant"))),
        ("connection_sha256", "f" * 64),
    ],
)
def test_wrong_selection_denies_without_content_or_committed_audit(
    monkeypatch, field, value
):
    case = _case(monkeypatch)
    case.request = case.request.model_copy(update={field: value})
    called = []
    with pytest.raises((receiver.VisitPrepCompanionDenied, VisitPrepNotFound)):
        _read(case, serialize=lambda *_: called.append(True))
    assert called == [] and _audits(case) == []


@pytest.mark.parametrize(
    "failure",
    [
        "serialize",
        "overflow",
        "expired_workload",
        "expired_physician",
        "disabled",
        "deadline",
    ],
)
def test_failure_after_private_serialization_discards_content_and_audits(
    monkeypatch, failure
):
    case = _case(monkeypatch)
    captured = []

    def serialize(view, at):
        captured.append(view.package.source_version)
        if failure == "serialize":
            raise ValueError("invented private serializer sentinel")
        if failure == "overflow":
            return b"x" * (receiver.MAX_RESPONSE_BYTES + 1)
        if failure.startswith("expired_"):
            principal = (
                case.workload if failure == "expired_workload" else case.physician
            )
            object.__setattr__(principal.window, "expires_at", 1)
        if failure == "disabled":
            monkeypatch.setattr(
                service_auth,
                "COMPANION_VISIT_PREP_AUTH_SETTINGS",
                replace(case.policy, enabled=False),
            )
        if failure == "deadline":
            monkeypatch.setattr(
                receiver,
                "check_deadline",
                lambda *_: (_ for _ in ()).throw(RecordDeadline()),
            )
        return _serialize(case, view, at)

    with pytest.raises(
        (ValueError, RecordUnavailable, receiver.VisitPrepCompanionDenied)
    ):
        _read(case, serialize=serialize)
    assert len(captured) == 1 and _audits(case) == []


@pytest.mark.parametrize("acknowledged", [False, True])
def test_commit_failure_or_lost_acknowledgment_never_returns_private_content(
    monkeypatch, acknowledged
):
    case = _case(monkeypatch)
    original = receiver.global_session
    captured = []

    @contextmanager
    def commit_uncertain():
        with original() as session:
            commit = session.commit

            def unavailable():
                if acknowledged:
                    commit()
                raise RuntimeError("invented commit acknowledgment loss")

            monkeypatch.setattr(session, "commit", unavailable)
            yield session

    monkeypatch.setattr(receiver, "global_session", commit_uncertain)
    with pytest.raises(RuntimeError, match="acknowledgment loss"):
        _read(
            case,
            serialize=lambda view, at: (
                captured.append(view),
                _serialize(case, view, at),
            )[1],
        )
    assert len(captured) == 1
    assert len(_audits(case)) == (2 if acknowledged else 0)


@pytest.mark.parametrize("row", ["link", "membership"])
def test_current_reviewed_connection_withdrawal_denies_before_patient_projection(
    monkeypatch, row
):
    case = _case(monkeypatch)
    with get_session() as session:
        if row == "link":
            value = session.exec(
                select(ScribeCaptureProviderLink).where(
                    ScribeCaptureProviderLink.doctor_id == case.fixture.doctor.id
                )
            ).one()
            value.revoked_at = datetime.now(UTC)
        else:
            value = session.exec(
                select(AuthorityTenantMembership).where(
                    AuthorityTenantMembership.actor_id
                    == case.request.expected_actor_ref
                )
            ).one()
            value.status = "revoked"
        session.add(value)
        session.commit()
    with pytest.raises(authority_service.AuthorityRequestDeniedError):
        _read(case)
    assert _audits(case) == []


def test_ambient_transaction_is_refused_without_commit_flush_or_rollback(monkeypatch):
    case = _case(monkeypatch)
    with global_session() as session:
        session.execute(text("SELECT 1"))
        transaction = session.get_transaction()
        with pytest.raises(RuntimeError, match="global session already set"):
            _read(case)
        assert session.get_transaction() is transaction and transaction.is_active


@pytest.mark.parametrize("change", ["baa", "correction", "token_expiry"])
def test_actual_practice_lock_wait_rechecks_committed_authority_and_sources(
    monkeypatch, change
):
    case = _case(monkeypatch)
    original = receiver.global_session
    started = Event()

    @contextmanager
    def tagged():
        with original() as session:
            session.execute(text("SET LOCAL application_name = 'vp_companion_wait'"))
            started.set()
            yield session

    monkeypatch.setattr(receiver, "global_session", tagged)
    with global_session() as lock:
        lock.exec(
            select(type(case.fixture.practice))
            .where(type(case.fixture.practice).id == case.fixture.practice.id)
            .with_for_update()
        ).one()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_read, case)
            try:
                assert started.wait(timeout=5)
                _observe_waiting_lock(lock, "vp_companion_wait")
                if change == "baa":
                    practice = lock.get(
                        type(case.fixture.practice), case.fixture.practice.id
                    )
                    practice.negotiated_baa_on_file = False
                    lock.add(practice)
                elif change == "correction":
                    _correct(
                        case.fixture,
                        answer="Invented correction during a genuine lock wait.",
                    )
                else:
                    expiry = lock.execute(
                        text(
                            "SELECT floor(extract(epoch FROM clock_timestamp()))::bigint + 1"
                        )
                    ).scalar_one()
                    object.__setattr__(case.physician.window, "expires_at", expiry)
                    lock.execute(text("SELECT pg_sleep(1.05)"))
            finally:
                lock.commit()
            if change == "correction":
                body = json.loads(future.result(timeout=5))
                assert "during a genuine lock wait" in json.dumps(body)
            else:
                with pytest.raises(
                    (VisitPrepNotFound, receiver.VisitPrepCompanionDenied)
                ):
                    future.result(timeout=5)
    assert len(_audits(case)) == (2 if change == "correction" else 0)


def test_authority_row_lock_contention_does_not_use_an_earlier_connection(monkeypatch):
    case = _case(monkeypatch)
    with global_session() as lock:
        lock.exec(
            select(AuthorityTenantMembership)
            .where(
                AuthorityTenantMembership.actor_id == case.request.expected_actor_ref
            )
            .with_for_update()
        ).one()
        with ThreadPoolExecutor(max_workers=1) as executor:
            with pytest.raises(OperationalError):
                executor.submit(_read, case).result(timeout=5)
        lock.rollback()
    assert _audits(case) == []


def test_existing_support_view_is_denied_without_cleanup_commit(monkeypatch):
    import hashlib

    from app.models.doctor import Doctor
    from app.models.superadmin_capability import (
        SuperadminCapabilityEnvironment,
        SuperadminCapabilityGrant,
        SuperadminCapabilityKind,
    )
    from app.models.view_as import ViewAsSession

    case = _case(monkeypatch)
    now = datetime.now(UTC)

    def digest(value):
        return hashlib.sha256(value.encode()).hexdigest()

    with get_session() as session:
        target = Doctor(
            clerk_id="user_inventedSupportTarget" + uuid4().hex, biography=None
        )
        session.add(target)
        grant = SuperadminCapabilityGrant(
            environment=SuperadminCapabilityEnvironment.staging,
            capability_kind=SuperadminCapabilityKind.view_as,
            clerk_issuer=case.physician.issuer,
            clerk_subject=case.physician.subject,
            doctor_id=case.fixture.doctor.id,
            issuer_sha256=digest(case.physician.issuer),
            subject_sha256=digest(case.physician.subject),
            verified_primary_email_sha256="a" * 64,
            source_ref="synthetic:reviewed-capability",
            source_sha256="b" * 64,
            grant_plan_sha256="c" * 64,
            granted_by_ref="synthetic:reviewer",
            grant_reason_code="synthetic_fixture",
            granted_at=now,
        )
        session.add(grant)
        session.flush()
        view = ViewAsSession(
            environment="staging",
            capability_grant_id=grant.id,
            actor_doctor_id=case.fixture.doctor.id,
            target_doctor_id=target.id,
            actor_issuer_sha256=digest(case.physician.issuer),
            actor_subject_sha256=digest(case.physician.subject),
            clerk_session_sha256="d" * 64,
            browser_nonce_sha256="e" * 64,
            started_at=now,
            absolute_expires_at=now + timedelta(minutes=10),
        )
        session.add(view)
        session.commit()
        view_id = view.id
    for _ in range(2):
        with pytest.raises(authority_service.AuthorityRequestDeniedError):
            _read(case)
    with get_session() as session:
        assert session.get(ViewAsSession, view_id).ended_at is None
    assert _audits(case) == []


@pytest.mark.parametrize("failure", ["audit_failure", "expires_during_audit"])
def test_real_audit_write_failure_or_wait_cannot_release_private_package(
    monkeypatch, failure
):
    from sqlalchemy.exc import DBAPIError

    case = _case(monkeypatch)
    with get_session() as session:
        session.execute(
            text("""
            CREATE FUNCTION vp_companion_audit_guard() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              IF NEW.event_type = 'visit_prep.companion_open' THEN
                IF TG_ARGV[0] = 'fail' THEN RAISE EXCEPTION 'invented audit failure'; END IF;
                PERFORM pg_sleep(1.05);
              END IF;
              RETURN NEW;
            END $$
        """)
        )
        mode = "fail" if failure == "audit_failure" else "wait"
        session.execute(
            text(
                "CREATE TRIGGER vp_companion_audit_guard BEFORE INSERT ON authority_audit_event FOR EACH ROW EXECUTE FUNCTION vp_companion_audit_guard('"
                + mode
                + "')"
            )
        )
        if failure == "expires_during_audit":
            expiry = session.execute(
                text("SELECT floor(extract(epoch FROM clock_timestamp()))::bigint + 1")
            ).scalar_one()
            object.__setattr__(case.physician.window, "expires_at", expiry)
        session.commit()
    called = []
    try:
        with pytest.raises(
            DBAPIError
            if failure == "audit_failure"
            else receiver.VisitPrepCompanionDenied
        ):
            _read(case, serialize=lambda *_: called.append(True))
        assert called == [] and _audits(case) == []
    finally:
        with get_session() as session:
            session.execute(
                text("DROP TRIGGER vp_companion_audit_guard ON authority_audit_event")
            )
            session.execute(text("DROP FUNCTION vp_companion_audit_guard()"))
            session.commit()
