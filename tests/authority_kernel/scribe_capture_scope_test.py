"""Invented capture scope; real authority and processing-access decisions.

Run only with --confcutdir=tests/authority_kernel and the explicitly named
disposable PostgreSQL database. The real support view-as guard is restored.
"""

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from types import SimpleNamespace
from uuid import uuid4

import pytest

import app.services.scribe_capture_scope as capture
from app.configuration.authority import AuthorityKernelSettings
from app.configuration.scribe_capture import ScribeCaptureSettings
from app.services import authority, view_as

from activemodel.session_manager import _session_context
from app.models.appointment import Appointment
from app.models.authority import (
    AuthorityActor,
    AuthorityActorExternalIdentity,
    AuthorityPerson,
    AuthorityPersonBinding,
    AuthorityTenant,
    AuthorityTenantMembership,
    MountContextSession,
    MountContextToken,
)
from app.models.baa import BAA_CURRENT_REFERENCE
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.pilot_access_grandfather_grant import PilotAccessGrandfatherGrant
from app.models.practice import Practice, PracticeMember, lock_practice_seats
from app.models.scribe_capture_binding import (
    ScribeCaptureBinding,
    ScribeCaptureProviderLink,
)
from app.models.superadmin_capability import (
    SuperadminCapabilityEnvironment,
    SuperadminCapabilityGrant,
    SuperadminCapabilityKind,
)
from app.models.view_as import ViewAsSession
from sqlalchemy.engine import make_url
from sqlmodel import Session, create_engine, select

REAL_VIEW_AS_GUARD = view_as.mount_context_session_blocked_by_view_as

SETTINGS = AuthorityKernelSettings(
    enabled=True,
    token_ttl_seconds=900,
    token_refresh_after_seconds=300,
    clerk_issuer="https://test.clerk.invalid",
)
EXTRA_MODELS = (
    Doctor,
    Patient,
    Appointment,
    Practice,
    PracticeMember,
    PilotAccessGrandfatherGrant,
    ScribeCaptureProviderLink,
    ScribeCaptureBinding,
)


def make_scope_case(session, *, existing_tenant=None, existing_practice=None):
    now = datetime.now(UTC)
    actor = AuthorityActor(actor_kind="human")
    tenant = existing_tenant or AuthorityTenant()
    person = AuthorityPerson(lifecycle_state="patient")
    doctor = Doctor(clerk_id=f"user_synthetic_{uuid4().hex}", biography=None)
    practice = existing_practice or Practice(
        legal_name="Invented capture practice",
        email_domain="synthetic.invalid",
        join_code=uuid4().hex[:16],
        baa_accepted_at=now,
        baa_version=BAA_CURRENT_REFERENCE,
        billing_exempt=True,
    )
    session.add_all([actor, tenant, person, doctor, practice])
    session.flush()
    patient = Patient(
        doctor_id=doctor.id,
        name="Invented Capture Patient",
        date_of_birth=datetime(1990, 1, 1, tzinfo=UTC),
    )
    session.add(patient)
    session.flush()
    appointment = Appointment(
        doctor_id=doctor.id,
        patient_id=patient.id,
        external_id=None,
        appointment_date=now,
    )
    session.add(appointment)
    session.flush()
    identity = AuthorityActorExternalIdentity(
        actor_id=actor.id,
        issuer=SETTINGS.clerk_issuer,
        subject=doctor.clerk_id,
        verified_at=now,
    )
    membership = AuthorityTenantMembership(
        actor_id=actor.id,
        tenant_id=tenant.id,
        membership_kind="provider",
    )
    person_binding = AuthorityPersonBinding(
        person_id=person.id,
        tenant_id=tenant.id,
        binding_ref=str(appointment.id),
        binding_kind="provider_record",
        verified_at=now,
    )
    practice_member = PracticeMember(
        practice_id=practice.id,
        doctor_id=doctor.id,
        role="owner",
        attested_at=now,
    )
    assert lock_practice_seats(session, practice.id) is not None
    session.add_all([identity, membership, person_binding, practice_member])
    session.flush()
    link = ScribeCaptureProviderLink(
        actor_id=actor.id,
        tenant_id=tenant.id,
        source_identity_id=identity.id,
        doctor_id=doctor.id,
        practice_id=practice.id,
        scribe_tenant_id=uuid4(),
        reviewed_at=now,
        review_ref=f"synthetic_review_{uuid4().hex}",
    )
    session.add(link)
    session.commit()
    token = authority.exchange_context_token(
        clerk_issuer=identity.issuer,
        clerk_subject=identity.subject,
        requested_surface="provider_assistant",
        record_ref=str(appointment.id),
        session=session,
        settings=SETTINGS,
    )
    turn_id = uuid4()
    resolved = authority.resolve_mount_context(
        workload_client_id="11111111-1111-4111-8111-111111111111",
        mount_context_token=token.context_token,
        client_turn_id=turn_id,
        session=session,
        settings=SETTINGS,
    )
    session.commit()
    arguments = {
        "workload_client_id": "11111111-1111-4111-8111-111111111111",
        "mount_context_token": token.context_token,
        "client_turn_id": turn_id,
        "interaction_ref": resolved.interaction_ref,
        "workspace_id": uuid4(),
        "appointment_ref": str(appointment.id),
        "request_nonce": uuid4(),
        "settings": SETTINGS,
        "capture_settings": ScribeCaptureSettings(enabled=True),
    }
    return SimpleNamespace(
        actor=actor,
        tenant=tenant,
        person=person,
        doctor=doctor,
        practice=practice,
        patient=patient,
        appointment=appointment,
        identity=identity,
        membership=membership,
        person_binding=person_binding,
        practice_member=practice_member,
        link=link,
        arguments=arguments,
        mount=session.get(MountContextSession, resolved.context.session_id),
        token=session.exec(
            select(MountContextToken).where(
                MountContextToken.session_id == resolved.context.session_id
            )
        ).one(),
    )


@pytest.fixture
def capture_engine(monkeypatch):
    raw_url = os.environ.get("W8_CORE_CAPTURE_TEST_URL")
    if not raw_url:
        pytest.skip("Explicit disposable W8 PostgreSQL URL required")
    url = make_url(raw_url)
    original_scope = url.database in {
        "w8_core_capture_test",
        "w8_core_notebook_test",
        "w8x_core_prep_test",
    } and (url.host, url.port, url.username) == ("127.0.0.1", 55439, "w1a_synth")
    resolution_scope = (url.database, url.host, url.port, url.username) == (
        "w24_core_note_resolution_test",
        "127.0.0.1",
        55445,
        "w11_synth",
    )
    operational_beta_scope = url.database in {
        "beta_scribe_integrated_test",
        "staging_release_test",
    } and (url.host, url.port, url.username) == ("127.0.0.1", 32768, "beta_local")
    if not (original_scope or resolution_scope or operational_beta_scope):
        pytest.fail("Refusing a database outside the disposable W8 scope")
    # Match Core's configured engine when exercising nested transcript entries.
    from activemodel.session_manager import _serialize_pydantic_model

    engine = create_engine(url, json_serializer=_serialize_pydantic_model)
    monkeypatch.setattr(
        view_as, "mount_context_session_blocked_by_view_as", REAL_VIEW_AS_GUARD
    )
    yield engine
    engine.dispose()


@pytest.fixture
def capture_case(capture_engine):
    with capture_engine.connect() as connection:
        transaction = connection.begin()
        with Session(
            bind=connection,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        ) as session:
            context_token = _session_context.set(session)
            try:
                yield session, make_scope_case(session)
            finally:
                _session_context.reset(context_token)
        transaction.rollback()


def test_resolve_and_reopen_preserve_binding_with_fresh_nonce(capture_case):
    session, case = capture_case
    first = capture.resolve_capture_binding(**case.arguments)
    session.expire_all()
    second = capture.resolve_capture_binding(
        **{**case.arguments, "request_nonce": uuid4()}
    )
    assert first.binding_ref == second.binding_ref
    assert first.actor_ref == str(case.actor.id)
    assert first.appointment_ref == str(case.appointment.id)
    assert first.request_nonce != second.request_nonce
    assert len(_workspace_bindings(session, case)) == 1
    assert "user_synthetic" not in repr(first)
    assert case.arguments["mount_context_token"] not in repr(first)


@pytest.mark.parametrize(
    ("target", "field", "value"),
    [
        ("actor", "status", "revoked"),
        ("actor", "actor_kind", "workload"),
        ("tenant", "status", "suspended"),
        ("membership", "status", "revoked"),
        ("membership", "membership_kind", "staff"),
        ("identity", "revoked_at", "now"),
        ("identity", "issuer", "https://foreign.invalid"),
        ("identity", "subject", "user_synthetic_foreign"),
        ("person", "status", "revoked"),
        ("person_binding", "status", "revoked"),
        ("person_binding", "revoked_at", "now"),
        ("person_binding", "verified_at", None),
        ("person_binding", "binding_ref", "record_foreign_000001"),
        ("doctor", "deleted_at", "now"),
        ("link", "revoked_at", "now"),
        ("practice_member", "access_revoked_at", "now"),
        ("practice_member", "seat_state", "inactive"),
        ("practice_member", "attested_at", None),
        ("practice", "baa_accepted_at", None),
        ("practice", "billing_exempt", False),
        ("mount", "status", "revoked"),
        ("token", "revoked_at", "now"),
        ("token", "expires_at", "expired"),
        ("mount", "absolute_expires_at", "expired"),
    ],
)
def test_current_authority_is_checked_again_on_reopen(
    capture_case, target, field, value
):
    session, case = capture_case
    capture.resolve_capture_binding(**case.arguments)
    if value == "now":
        value = datetime.now(UTC)
    elif value == "expired":
        value = datetime.now(UTC) - timedelta(seconds=1)
    row = getattr(case, target)
    setattr(row, field, value)
    if target == "practice_member" and field == "access_revoked_at":
        row.access_revoked_by_ref = "synthetic_reviewer"
        row.access_revocation_reason = "synthetic_revocation"
        row.access_records_retain_until = value + timedelta(days=366 * 7)
    session.add(row)
    session.commit()
    with pytest.raises(authority.AuthorityKernelError):
        capture.resolve_capture_binding(**case.arguments)
    assert len(_workspace_bindings(session, case)) == 1


@pytest.mark.parametrize("target", ["appointment", "patient", "link"])
def test_changed_clinical_ownership_is_denied(capture_case, target):
    session, case = capture_case
    capture.resolve_capture_binding(**case.arguments)
    foreign = Doctor(clerk_id="user_synthetic_foreign", biography=None)
    session.add(foreign)
    session.flush()
    row = getattr(case, target)
    row.doctor_id = foreign.id
    session.add(row)
    session.commit()
    with pytest.raises(authority.AuthorityKernelError):
        capture.resolve_capture_binding(**case.arguments)


def test_session_prep_provider_context_does_not_supply_capture_link(capture_case):
    session, case = capture_case
    session.delete(case.link)
    session.commit()
    with pytest.raises(capture.CaptureScopeDenied):
        capture.resolve_capture_binding(**case.arguments)
    assert not _workspace_bindings(session, case)


@pytest.mark.parametrize(
    "change", ["scribe_tenant", "workspace", "workload", "turn", "nonce_only"]
)
def test_binding_and_request_lineage_cannot_be_reassigned(capture_case, change):
    session, case = capture_case
    first = capture.resolve_capture_binding(**case.arguments)
    args = dict(case.arguments)
    if change == "scribe_tenant":
        case.link.scribe_tenant_id = uuid4()
        session.add(case.link)
        session.commit()
    elif change == "workspace":
        args["workspace_id"] = uuid4()
    elif change == "workload":
        args["workload_client_id"] = "22222222-2222-4222-8222-222222222222"
    elif change == "turn":
        args["client_turn_id"] = uuid4()
    else:
        args["request_nonce"] = uuid4()
        assert capture.resolve_capture_binding(**args).binding_ref == first.binding_ref
        return
    with pytest.raises(authority.AuthorityKernelError):
        capture.resolve_capture_binding(**args)


@pytest.mark.parametrize("same_practice", [False, True])
def test_another_provider_cannot_adopt_existing_workspace(capture_case, same_practice):
    session, case = capture_case
    capture.resolve_capture_binding(**case.arguments)
    other = make_scope_case(
        session,
        existing_tenant=case.tenant if same_practice else None,
        existing_practice=case.practice if same_practice else None,
    )
    with pytest.raises(capture.CaptureScopeDenied):
        capture.resolve_capture_binding(
            **{
                **other.arguments,
                "workspace_id": case.arguments["workspace_id"],
            }
        )
    assert len(_workspace_bindings(session, case)) == 1


def _workspace_bindings(session, case):
    return session.exec(
        select(ScribeCaptureBinding).where(
            ScribeCaptureBinding.workspace_id == case.arguments["workspace_id"]
        )
    ).all()


def test_disabled_service_never_touches_authority(capture_case, monkeypatch):
    _, case = capture_case
    monkeypatch.setattr(
        authority, "authorize_interaction", lambda **_: pytest.fail("authority reached")
    )
    with pytest.raises(authority.AuthorityKernelDisabledError):
        capture.resolve_capture_binding(
            **{
                **case.arguments,
                "capture_settings": ScribeCaptureSettings(),
            }
        )


def test_active_support_view_as_blocks_preexisting_capture_binding(capture_case):
    session, case = capture_case
    capture.resolve_capture_binding(**case.arguments)
    now = datetime.now(UTC)
    target = Doctor(clerk_id="user_synthetic_support_target", biography=None)
    session.add(target)
    session.flush()

    def digest(value):
        return hashlib.sha256(value.encode()).hexdigest()

    grant = SuperadminCapabilityGrant(
        environment=SuperadminCapabilityEnvironment.staging,
        capability_kind=SuperadminCapabilityKind.view_as,
        clerk_issuer=case.identity.issuer,
        clerk_subject=case.identity.subject,
        doctor_id=case.doctor.id,
        issuer_sha256=digest(case.identity.issuer),
        subject_sha256=digest(case.identity.subject),
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
        actor_doctor_id=case.doctor.id,
        target_doctor_id=target.id,
        actor_issuer_sha256=digest(case.identity.issuer),
        actor_subject_sha256=digest(case.identity.subject),
        clerk_session_sha256="d" * 64,
        browser_nonce_sha256="e" * 64,
        started_at=now,
        absolute_expires_at=now + timedelta(minutes=10),
    )
    session.add(view)
    session.commit()
    for _ in range(2):
        with pytest.raises(authority.AuthorityRequestDeniedError):
            capture.resolve_capture_binding(**case.arguments)
    session.refresh(view)
    assert view.ended_at is None


def _committed_case(engine):
    with Session(engine, expire_on_commit=False) as session:
        token = _session_context.set(session)
        try:
            return make_scope_case(session)
        finally:
            _session_context.reset(token)


def _thread_resolve(engine, arguments, barrier=None):
    with Session(engine, expire_on_commit=False) as session:
        token = _session_context.set(session)
        try:
            if barrier is not None:
                barrier.wait(timeout=10)
            return capture.resolve_capture_binding(**arguments)
        finally:
            _session_context.reset(token)


def test_simultaneous_resolve_creates_one_binding(capture_engine):
    case = _committed_case(capture_engine)
    barrier = Barrier(4)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(
                _thread_resolve,
                capture_engine,
                {
                    **case.arguments,
                    "request_nonce": uuid4(),
                },
                barrier,
            )
            for _ in range(4)
        ]
        snapshots = [future.result(timeout=15) for future in futures]
    assert len({snapshot.binding_ref for snapshot in snapshots}) == 1
    assert len({snapshot.request_nonce for snapshot in snapshots}) == 4
    with Session(capture_engine) as session:
        assert (
            len(
                session.exec(
                    select(ScribeCaptureBinding).where(
                        ScribeCaptureBinding.workspace_id
                        == case.arguments["workspace_id"]
                    )
                ).all()
            )
            == 1
        )


def test_revocation_committed_while_waiting_for_identity_lock_is_denied(
    capture_engine, monkeypatch
):
    case = _committed_case(capture_engine)
    reached_identity = Event()
    original_locked = capture._locked

    def observe_lock(session, model, identifier):
        if model is AuthorityActorExternalIdentity:
            reached_identity.set()
        return original_locked(session, model, identifier)

    monkeypatch.setattr(capture, "_locked", observe_lock)
    with Session(capture_engine) as revoker:
        identity = revoker.exec(
            select(AuthorityActorExternalIdentity)
            .where(AuthorityActorExternalIdentity.id == case.identity.id)
            .with_for_update()
        ).one()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(_thread_resolve, capture_engine, case.arguments)
            assert reached_identity.wait(timeout=10)
            identity.revoked_at = datetime.now(UTC)
            revoker.add(identity)
            revoker.commit()
            with pytest.raises(authority.AuthorityKernelError):
                pending.result(timeout=10)
    with Session(capture_engine) as session:
        assert not session.exec(
            select(ScribeCaptureBinding).where(
                ScribeCaptureBinding.workspace_id == case.arguments["workspace_id"]
            )
        ).all()
