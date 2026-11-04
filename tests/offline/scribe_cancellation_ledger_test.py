"""Actual ledger SQL/route source on synthetic metadata, no Core/cloud bootstrap.

Existing Clerk/Entra crypto and current-provider policy are injected at their
normalized seams; these tests do not claim to qualify those existing adapters.
Run with --confcutdir=tests/offline. The owned PG16 expand is rehearsed separately.
"""

import ast
import importlib.util
import sys
import time
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Literal, cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

import sqlalchemy as sa
from sqlmodel import SQLModel

ROOT = Path(__file__).resolve().parents[2]
ISSUER = "https://synthetic.clerk.invalid"
SUBJECT = "user_synthetic_a"
NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)


def table_counts(f):
    with f.engine.connect() as db:
        return tuple(
            db.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()
            for table in (*f.metadata.tables.values(), f.operation)
        )


def test_fresh_status_is_not_found_and_mutations_stay_denied(fixture):
    f = fixture
    subject = "user_synthetic_never_activated"
    before = table_counts(f)
    requests = [
        f.contract.StatusRequest(action="status", request_nonce=uuid4()),
        f.contract.PreviewRequest(action="preview", request_nonce=uuid4()),
        f.contract.AcceptRequest(
            action="request",
            request_nonce=uuid4(),
            preview_sha256="a" * 64,
            confirmation="CANCEL",
        ),
    ]
    for request in requests:
        error = (
            f.contract.CancellationNotFound
            if request.action == "status"
            else f.contract.CancellationDenied
        )
        with pytest.raises(error):
            f.service.execute_cancellation(
                issuer=ISSUER,
                subject=subject,
                session_id="sess_synthetic",
                request=request,
                request_key=uuid4() if request.action == "request" else None,
                settings=f.settings,
                authority_settings=f.auth,
                deadline=f.budget(),
            )
    assert table_counts(f) == before


def test_fresh_revoked_grant_is_denied_not_setup(fixture):
    f = fixture
    subject = "user_synthetic_revoked_fresh"
    with f.engine.begin() as db:
        db.execute(
            f.tables.grant.insert().values(
                id="synthetic_revoked", clerk_id=subject, revoked_at=NOW
            )
        )
    before = table_counts(f)
    with pytest.raises(f.contract.CancellationDenied):
        f.service.execute_cancellation(
            issuer=ISSUER,
            subject=subject,
            session_id="sess_synthetic",
            request=f.contract.StatusRequest(action="status", request_nonce=uuid4()),
            request_key=None,
            settings=f.settings,
            authority_settings=f.auth,
            deadline=f.budget(),
        )
    assert table_counts(f) == before


@pytest.mark.parametrize(
    "tombstone", ["deleted", "identity", "grant", "member", "link"]
)
def test_existing_status_tombstones_are_denials_and_do_not_write(fixture, tombstone):
    f = fixture
    with f.engine.begin() as db:
        if tombstone == "deleted":
            db.execute(
                f.tables.doctor.update()
                .where(f.tables.doctor.c.clerk_id == SUBJECT)
                .values(deleted_at=NOW)
            )
        if tombstone == "identity":
            db.execute(
                f.tables.identity.update()
                .where(f.tables.identity.c.subject == SUBJECT)
                .values(revoked_at=NOW)
            )
        if tombstone == "grant":
            db.execute(
                f.tables.grant.insert().values(
                    id="synthetic_revoked", clerk_id=SUBJECT, revoked_at=NOW
                )
            )
        if tombstone == "member":
            db.execute(
                f.tables.member.insert().values(
                    id="synthetic_member", doctor_id="doctor_a", seat_state="inactive"
                )
            )
        if tombstone == "link":
            db.execute(
                f.tables.link.update()
                .where(f.tables.link.c.doctor_id == "doctor_a")
                .values(revoked_at=NOW)
            )
    before = table_counts(f)
    with pytest.raises(f.contract.CancellationDenied):
        f.execute(f.contract.StatusRequest(action="status", request_nonce=uuid4()))
    assert table_counts(f) == before


def revoke_entitlement_or_seat(f, tombstone):
    with f.engine.begin() as db:
        if tombstone == "grant":
            db.execute(
                f.tables.grant.insert().values(
                    id="synthetic_revoked", clerk_id=SUBJECT, revoked_at=NOW
                )
            )
        else:
            db.execute(
                f.tables.member.insert().values(
                    id="synthetic_member",
                    doctor_id="doctor_a",
                    seat_state="inactive" if tombstone == "inactive" else "active",
                    access_revoked_at=NOW if tombstone == "revoked" else None,
                )
            )
            if tombstone == "multiple":
                db.execute(
                    f.tables.member.insert().values(
                        id="synthetic_member_two",
                        doctor_id="doctor_a",
                        seat_state="active",
                    )
                )


@pytest.mark.parametrize("tombstone", ["grant", "inactive", "revoked", "multiple"])
def test_own_cancellation_status_and_retry_survive_entitlement_loss(fixture, tombstone):
    f = fixture
    request, key, receipt = accept(f)
    revoke_entitlement_or_seat(f, tombstone)
    before = table_counts(f)
    status = f.execute(f.contract.StatusRequest(action="status", request_nonce=uuid4()))
    assert status.operation_id == receipt.operation_id and status.phase == receipt.phase
    assert f.execute(request, key) == receipt
    with pytest.raises(f.contract.CancellationDenied):
        preview(f)
    assert table_counts(f) == before


@pytest.mark.parametrize("tombstone", ["grant", "inactive", "revoked", "multiple"])
def test_entitlement_loss_without_receipt_stays_closed(fixture, tombstone):
    f = fixture
    revoke_entitlement_or_seat(f, tombstone)
    before = table_counts(f)
    for request in [
        f.contract.StatusRequest(action="status", request_nonce=uuid4()),
        f.contract.PreviewRequest(action="preview", request_nonce=uuid4()),
    ]:
        with pytest.raises(f.contract.CancellationDenied):
            f.execute(request)
    assert table_counts(f) == before


@pytest.mark.parametrize("tombstone", ["deleted", "identity"])
def test_existing_receipt_does_not_bypass_identity_tombstones(fixture, tombstone):
    f = fixture
    request, key, _receipt = accept(f)
    with f.engine.begin() as db:
        table = f.tables.doctor if tombstone == "deleted" else f.tables.identity
        field = table.c.clerk_id if tombstone == "deleted" else table.c.subject
        values = {"deleted_at": NOW} if tombstone == "deleted" else {"revoked_at": NOW}
        db.execute(table.update().where(field == SUBJECT).values(**values))
    before = table_counts(f)
    for current, request_key in [
        (f.contract.StatusRequest(action="status", request_nonce=uuid4()), None),
        (request, key),
    ]:
        with pytest.raises(f.contract.CancellationDenied):
            f.execute(current, request_key)
    assert table_counts(f) == before


def package(monkeypatch, name, **values):
    module = ModuleType(name)
    module.__path__ = []
    module.__dict__.update(values)
    monkeypatch.setitem(sys.modules, name, module)
    return module


def source(monkeypatch, name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fixture(tmp_path, monkeypatch, request):
    for name in (
        "app",
        "app.services",
        "app.models",
        "app.configuration",
        "app.routes",
        "app.routes.dependencies",
    ):
        package(monkeypatch, name)
    source(
        monkeypatch,
        "app.services.companion_record_contract",
        "app/services/companion_record_contract.py",
    )
    contract = source(
        monkeypatch,
        "app.services.scribe_cancellation_contract",
        "app/services/scribe_cancellation_contract.py",
    )
    # Actual table source on private metadata: never alter a preloaded app table.
    with monkeypatch.context() as table_scope:
        table_scope.setattr(SQLModel, "metadata", sa.MetaData())
        model = source(
            monkeypatch,
            "app.models.scribe_cancellation",
            "app/models/scribe_cancellation.py",
        )
    service = source(
        monkeypatch,
        "app.services.scribe_cancellation",
        "app/services/scribe_cancellation.py",
    )
    metadata = sa.MetaData()
    operation = model.scribe_cancellation_operation
    doctor = sa.Table(
        "synthetic_doctor",
        metadata,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("clerk_id", sa.String),
        sa.Column("deleted_at", sa.DateTime),
    )
    actor = sa.Table(
        "synthetic_actor",
        metadata,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("actor_kind", sa.String),
        sa.Column("status", sa.String),
    )
    identity = sa.Table(
        "synthetic_identity",
        metadata,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("actor_id", sa.String),
        sa.Column("issuer", sa.String),
        sa.Column("subject", sa.String),
        sa.Column("revoked_at", sa.DateTime),
        sa.Column("verified_at", sa.DateTime, default=NOW),
    )

    grant = sa.Table(
        "synthetic_grant",
        metadata,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("clerk_id", sa.String),
        sa.Column("revoked_at", sa.DateTime),
    )
    member = sa.Table(
        "synthetic_member",
        metadata,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("doctor_id", sa.String),
        sa.Column("seat_state", sa.String),
        sa.Column("access_revoked_at", sa.DateTime),
    )

    def scope_columns():
        return [
            sa.Column(name, sa.String)
            for name in (
                "actor_id",
                "doctor_id",
                "source_identity_id",
                "tenant_id",
                "practice_id",
                "scribe_tenant_id",
            )
        ]

    link = sa.Table(
        "synthetic_link",
        metadata,
        sa.Column("id", sa.String, primary_key=True),
        *scope_columns(),
        sa.Column("revoked_at", sa.DateTime),
    )
    binding = sa.Table(
        "synthetic_binding",
        metadata,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("provider_link_id", sa.String),
        *scope_columns(),
    )
    admission = sa.Table(
        "synthetic_admission",
        metadata,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("binding_id", sa.String),
        sa.Column("scribe_tenant_id", sa.String),
        sa.Column("session_id", sa.String),
    )
    url = f"sqlite:///{tmp_path / 'ledger.sqlite'}"
    engine = sa.create_engine(url)
    operation.create(engine)
    metadata.create_all(engine)
    with engine.begin() as db:
        for suffix in ("a", "b"):
            db.execute(
                doctor.insert().values(
                    id=f"doctor_{suffix}", clerk_id=f"user_synthetic_{suffix}"
                )
            )
            db.execute(
                actor.insert().values(
                    id=f"actor_{suffix}", actor_kind="human", status="active"
                )
            )
            db.execute(
                identity.insert().values(
                    id=f"identity_{suffix}",
                    actor_id=f"actor_{suffix}",
                    issuer=ISSUER,
                    subject=f"user_synthetic_{suffix}",
                )
            )
            scope = {
                "actor_id": f"actor_{suffix}",
                "doctor_id": f"doctor_{suffix}",
                "source_identity_id": f"identity_{suffix}",
                "tenant_id": "shared_tenant",
                "practice_id": "shared_practice",
                "scribe_tenant_id": "shared_scribe_tenant",
            }
            db.execute(link.insert().values(id=f"link_{suffix}", **scope))
            db.execute(
                binding.insert().values(
                    id=f"binding_{suffix}", provider_link_id=f"link_{suffix}", **scope
                )
            )
            db.execute(
                admission.insert().values(
                    id=f"admission_{suffix}",
                    binding_id=f"binding_{suffix}",
                    scribe_tenant_id="shared_scribe_tenant",
                    session_id=f"session_{suffix}",
                )
            )
    tables = SimpleNamespace(
        doctor=doctor,
        actor=actor,
        identity=identity,
        link=link,
        binding=binding,
        admission=admission,
        grant=grant,
        member=member,
    )

    @contextmanager
    def managed():
        with engine.connect() as db:
            yield SimpleNamespace(
                connection=lambda: db, commit=db.commit, rollback=db.rollback
            )

    package(monkeypatch, "activemodel.session_manager", global_session=managed)
    package(monkeypatch, "app.models.doctor", Doctor=SimpleNamespace(__table__=doctor))
    package(
        monkeypatch,
        "app.models.pilot_access_grandfather_grant",
        PilotAccessGrandfatherGrant=SimpleNamespace(__table__=grant),
    )
    package(
        monkeypatch,
        "app.models.practice",
        PracticeMember=SimpleNamespace(__table__=member),
    )
    package(
        monkeypatch,
        "app.models.authority",
        AuthorityActor=SimpleNamespace(__table__=actor),
        AuthorityActorExternalIdentity=SimpleNamespace(__table__=identity),
    )
    package(
        monkeypatch,
        "app.models.scribe_capture_binding",
        ScribeCaptureProviderLink=SimpleNamespace(__table__=link),
        ScribeCaptureBinding=SimpleNamespace(__table__=binding),
    )
    package(
        monkeypatch,
        "app.models.scribe_capture_admission",
        ScribeCaptureAdmission=SimpleNamespace(__table__=admission),
    )

    class AuthorityError(Exception):
        pass

    authority = package(
        monkeypatch,
        "app.services.authority",
        AuthorityKernelError=AuthorityError,
        _ensure_enabled=lambda settings: None,
    )

    @dataclass(frozen=True)
    class Budget:
        expires_at: float

    class Deadline(Exception):
        pass

    def check_deadline(budget):
        if budget.expires_at <= time.monotonic():
            raise Deadline

    records = package(
        monkeypatch,
        "app.services.companion_records",
        RecordBudget=Budget,
        RecordDeadline=Deadline,
        RecordUnavailable=contract.CancellationUnavailable,
        check_deadline=check_deadline,
        transaction_budget=lambda *args: nullcontext(),
    )
    policy = contract.CancellationPolicy(
        grace_days=60,
        phi_scope="core-and-scribe-clinical-records",
        approval_sha256="a" * 64,
    )
    settings = contract.CancellationSettings(True, policy, ledger_ready=True)
    auth = SimpleNamespace(clerk_issuer=ISSUER)

    def budget():
        return Budget(time.monotonic() + 10)

    def execute(request, key=None, **kwargs):
        return service.execute_cancellation(
            issuer=ISSUER,
            subject=SUBJECT,
            session_id="sess_synthetic",
            request=request,
            request_key=key,
            settings=settings,
            authority_settings=auth,
            deadline=budget(),
            **kwargs,
        )

    yield SimpleNamespace(
        contract=contract,
        service=service,
        model=model,
        operation=operation,
        engine=engine,
        url=url,
        metadata=metadata,
        tables=tables,
        policy=policy,
        settings=settings,
        execute=execute,
        budget=budget,
        records=records,
        authority=authority,
        auth=auth,
    )
    engine.dispose()


def preview(f):
    return f.execute(f.contract.PreviewRequest(action="preview", request_nonce=uuid4()))


def accept(f):
    before = preview(f)
    request = f.contract.AcceptRequest(
        action="request",
        request_nonce=uuid4(),
        preview_sha256=before.preview_sha256,
        confirmation="CANCEL",
    )
    key = uuid4()
    return request, key, f.execute(request, key)


def test_server_policy_is_explicit_and_no_client_authority_fields(fixture):
    f = fixture
    for settings in (
        f.contract.CancellationSettings(),
        f.contract.CancellationSettings(True),
    ):
        with pytest.raises(f.contract.CancellationUnavailable):
            settings.require_policy()
    for grace in (0, 29, 91, True, "60"):
        with pytest.raises(ValidationError):
            f.contract.CancellationPolicy(
                grace_days=grace,
                phi_scope="core-and-scribe-clinical-records",
                approval_sha256="a" * 64,
            )
    for key in ("doctor_ref", "subject", "tenant_id", "phi_scope", "grace_days"):
        with pytest.raises(ValidationError):
            f.contract.REQUEST_ADAPTER.validate_python(
                {"action": "preview", "request_nonce": str(uuid4()), key: "other"}
            )


@pytest.mark.parametrize(
    "issuer,subject", [("https://other.invalid", SUBJECT), (ISSUER, "user_missing")]
)
def test_self_sql_denies_wrong_issuer_and_subject(fixture, issuer, subject):
    with (
        fixture.engine.connect() as db,
        pytest.raises(fixture.contract.CancellationDenied),
    ):
        fixture.service.resolve_self(
            db,
            issuer=issuer,
            subject=subject,
            trusted_issuer=ISSUER,
            tables=fixture.tables,
        )


def test_inventory_excludes_other_creator_in_shared_tenant(fixture):
    f = fixture
    with f.engine.connect() as db:
        own = f.service.resolve_self(
            db, issuer=ISSUER, subject=SUBJECT, trusted_issuer=ISSUER, tables=f.tables
        )
        assert f.service.owned_inventory(db, identity=own, tables=f.tables) == [
            ["link_a", "binding_a", "admission_a", "shared_scribe_tenant", "session_a"]
        ]
    assert preview(f).resource_manifest_status == "unresolved"


@pytest.mark.parametrize(
    "table,column",
    [
        ("binding", "doctor_id"),
        ("binding", "actor_id"),
        ("binding", "source_identity_id"),
        ("binding", "scribe_tenant_id"),
        ("admission", "scribe_tenant_id"),
    ],
)
def test_mismatched_original_ownership_holds_acceptance(fixture, table, column):
    f = fixture
    target = getattr(f.tables, table)
    with f.engine.begin() as db:
        db.execute(
            target.update()
            .where(target.c.id == f"{table}_a")
            .values(**{column: "foreign"})
        )
    with pytest.raises(f.contract.CancellationDenied):
        preview(f)


def test_changed_server_preview_cannot_create_operation(fixture):
    f = fixture
    before = preview(f)
    with f.engine.begin() as db:
        db.execute(
            f.tables.admission.update()
            .where(f.tables.admission.c.id == "admission_a")
            .values(session_id="new_session")
        )
    request = f.contract.AcceptRequest(
        action="request",
        request_nonce=uuid4(),
        preview_sha256=before.preview_sha256,
        confirmation="CANCEL",
    )
    with pytest.raises(f.contract.CancellationConflict):
        f.execute(request, uuid4())
    with f.engine.connect() as db:
        assert (
            db.execute(sa.select(sa.func.count()).select_from(f.operation)).scalar()
            == 0
        )


def test_request_receipt_survives_connection_restart_and_scribe_revocation(fixture):
    f = fixture
    request, key, first = accept(f)
    assert first.phase == "read-only-export"
    assert (
        first.shared_login_preserved
        and first.access_fence_confirmed
        and not first.erasure_confirmed
    )
    assert first.export_until - first.received_at == timedelta(days=60)
    f.engine.dispose()  # Lost HTTP response/process connection; durable file remains.
    with f.engine.begin() as db:
        db.execute(
            f.tables.link.update()
            .where(f.tables.link.c.id == "link_a")
            .values(revoked_at=NOW)
        )
    again = f.execute(request, key)
    status = f.execute(
        f.contract.StatusRequest(
            action="status", request_nonce=uuid4(), operation_id=first.operation_id
        )
    )
    assert again.operation_id == status.operation_id == first.operation_id
    assert again.received_at == first.received_at
    with f.engine.connect() as db:
        assert (
            db.execute(
                sa.select(f.tables.doctor.c.deleted_at).where(
                    f.tables.doctor.c.id == "doctor_a"
                )
            ).scalar()
            is None
        )
        assert (
            db.execute(
                sa.select(f.tables.identity.c.revoked_at).where(
                    f.tables.identity.c.id == "identity_a"
                )
            ).scalar()
            is None
        )


def test_changed_request_key_and_preview_conflict_without_duplicate(fixture):
    f = fixture
    request, key, _first = accept(f)
    with pytest.raises(f.contract.CancellationConflict):
        f.execute(request, uuid4())
    changed = request.model_copy(update={"preview_sha256": "b" * 64})
    with pytest.raises(f.contract.CancellationConflict):
        f.execute(changed, key)
    with f.engine.connect() as db:
        assert (
            db.execute(sa.select(sa.func.count()).select_from(f.operation)).scalar()
            == 1
        )


def test_other_user_cannot_observe_operation_and_doctor_rebind_cannot_recover(fixture):
    f = fixture
    _, _, receipt = accept(f)
    status = f.contract.StatusRequest(
        action="status", request_nonce=uuid4(), operation_id=receipt.operation_id
    )
    with f.engine.connect() as db:
        other = f.service.resolve_self(
            db,
            issuer=ISSUER,
            subject="user_synthetic_b",
            trusted_issuer=ISSUER,
            tables=f.tables,
        )
        with pytest.raises(f.contract.CancellationNotFound):
            f.service.existing_receipt(db, other, status)
    with f.engine.begin() as db:
        db.execute(
            f.tables.doctor.update()
            .where(f.tables.doctor.c.id == "doctor_a")
            .values(clerk_id="user_retired")
        )
    with pytest.raises(f.contract.CancellationDenied):
        f.execute(status)


def test_recovery_preserves_original_policy_even_if_config_changes(fixture):
    f = fixture
    request, key, receipt = accept(f)
    changed = f.contract.CancellationSettings(
        True, f.policy.model_copy(update={"grace_days": 90}), ledger_ready=True
    )
    result = f.service.execute_cancellation(
        issuer=ISSUER,
        subject=SUBJECT,
        session_id="new_session",
        request=request,
        request_key=key,
        settings=changed,
        authority_settings=f.auth,
        deadline=f.budget(),
    )
    assert result.grace_days == 60 and result.export_until == receipt.export_until


def test_ninety_day_fence_is_self_only_and_intake_shutdown_cannot_lift_it(
    fixture, monkeypatch
):
    f = fixture
    access = source(
        monkeypatch,
        "app.services.scribe_cancellation_access",
        "app/services/scribe_cancellation_access.py",
    )
    with f.engine.begin() as db:
        own = f.service.resolve_self(
            db, issuer=ISSUER, subject=SUBJECT, trusted_issuer=ISSUER, tables=f.tables
        )
        inventory = f.service.owned_inventory(db, identity=own, tables=f.tables)
        policy = f.policy.model_copy(
            update={"grace_days": 90, "phi_scope": "core-and-scribe-clinical-records"}
        )
        request = f.contract.AcceptRequest(
            action="request",
            request_nonce=uuid4(),
            preview_sha256=f.service.preview_digest(own, inventory, policy),
            confirmation="CANCEL",
        )
        receipt = f.service.persist_request(
            db,
            identity=own,
            inventory=inventory,
            policy=policy,
            request=request,
            request_key=uuid4(),
            now=NOW,
        )
    package(
        monkeypatch,
        "app.configuration.scribe_cancellation",
        SCRIBE_CANCELLATION_SETTINGS=f.contract.CancellationSettings(
            enabled=False, ledger_ready=True
        ),
    )
    with f.engine.connect() as db:
        session = SimpleNamespace(connection=lambda: db)
        doctor = SimpleNamespace(id="doctor_a")
        assert (
            access.doctor_cancellation_denial(doctor, at=NOW, session=session)
            == "scribe_cancelled_read_only"
        )
        assert (
            access.doctor_cancellation_denial(
                doctor,
                purpose="read",
                at=NOW + timedelta(days=89, hours=23),
                session=session,
            )
            is None
        )
        assert (
            access.doctor_cancellation_denial(
                doctor, purpose="read", at=NOW + timedelta(days=90), session=session
            )
            == "scribe_export_period_expired"
        )
        assert (
            access.doctor_cancellation_denial(
                SimpleNamespace(id="doctor_b"), at=NOW, session=session
            )
            is None
        )
        assert receipt.export_until == NOW + timedelta(days=90)
        assert (
            f.service._receipt(
                db.execute(sa.select(f.operation)).mappings().one(),
                request,
                now=NOW + timedelta(days=90),
            ).phase
            == "read-only-expired"
        )
        assert db.execute(sa.select(f.tables.doctor.c.deleted_at)).scalars().all() == [
            None,
            None,
        ]
        assert db.execute(
            sa.select(f.tables.identity.c.revoked_at)
        ).scalars().all() == [None, None]


def test_existing_account_without_scribe_actor_or_record_can_cancel(fixture):
    f = fixture
    with f.engine.begin() as db:
        db.execute(
            f.tables.identity.delete().where(f.tables.identity.c.subject == SUBJECT)
        )
    _, _, receipt = accept(f)
    assert (
        receipt.shared_login_preserved
        and receipt.access_fence_confirmed
        and not receipt.erasure_confirmed
    )
    with f.engine.connect() as db:
        assert db.execute(sa.select(f.operation.c.inventory)).scalar_one() == []


def test_own_status_discovery_survives_intake_shutdown(fixture):
    f = fixture
    _, _, first = accept(f)
    status = f.contract.StatusRequest(action="status", request_nonce=uuid4())
    receipt = f.service.execute_cancellation(
        issuer=ISSUER,
        subject=SUBJECT,
        session_id="sess_new",
        request=status,
        request_key=None,
        settings=f.contract.CancellationSettings(ledger_ready=True),
        authority_settings=f.auth,
        deadline=f.budget(),
    )
    assert (
        receipt.operation_id == first.operation_id
        and receipt.export_until == first.export_until
    )


def test_actual_account_resolver_and_internal_gate_preserve_read_export_only(
    fixture, monkeypatch
):
    """Exercise the actual shared resolver and HTTP gate, not a copied predicate."""
    from enum import Enum

    from starlette.requests import Request

    f = fixture
    accept(f)
    source(
        monkeypatch,
        "app.services.scribe_cancellation_access",
        "app/services/scribe_cancellation_access.py",
    )
    package(
        monkeypatch,
        "app.configuration.scribe_cancellation",
        SCRIBE_CANCELLATION_SETTINGS=f.contract.CancellationSettings(
            enabled=False, ledger_ready=True
        ),
    )
    package(
        monkeypatch,
        "app.services.doctor_access_row_ownership",
        DoctorAccessRowOwnership=type("Ownership", (), {}),
        current_capture_doctor_rows=lambda *_: None,
    )
    package(
        monkeypatch,
        "app.services.organization_access",
        member_authorization_basis=lambda *_: "organization_roster",
    )
    package(
        monkeypatch,
        "app.services.pilot_access_grandfather",
        **{
            name: type("Rows", (), {})
            if name == "CurrentProviderAccessRows"
            else lambda *_, **__: None
            for name in (
                "CurrentProviderAccessRows",
                "active_pilot_access_grant",
                "owned_provider_access_rows",
                "owned_provider_tombstone_rows",
                "revoked_pilot_access_grant",
            )
        },
    )

    class State(Enum):
        revoked = "revoked"
        active = "active"

    member = SimpleNamespace(practice_id="shared_practice")
    practice = SimpleNamespace(id="shared_practice")
    package(
        monkeypatch,
        "app.models.practice",
        MemberAccessState=State,
        Practice=SimpleNamespace(get=lambda **_: practice),
        PracticeMember=SimpleNamespace(get=lambda **_: member),
        member_access_state=lambda *_, **__: State.active,
    )

    @contextmanager
    def managed():
        with f.engine.connect() as db:
            yield SimpleNamespace(connection=lambda: db)

    package(monkeypatch, "activemodel.session_manager", get_session=managed)
    resolver = source(
        monkeypatch, "app.services.doctor_access", "app/services/doctor_access.py"
    )
    monkeypatch.setattr(resolver, "doctor_tombstone", lambda *_, **__: None)
    decisions = []

    def regular_decision(*_, **kwargs):
        decisions.append(kwargs["require_entitlement"])
        return resolver.DoctorAccessDecision(
            True, "organization_roster", None, True, True
        )

    monkeypatch.setattr(resolver, "_decide", regular_decision)
    doctor_a = SimpleNamespace(id="doctor_a", deleted_at=None)
    doctor_b = SimpleNamespace(id="doctor_b", deleted_at=None)
    assert (
        resolver.resolve_doctor_access(doctor_a).denial_reason
        == "scribe_cancelled_read_only"
    )
    assert resolver.resolve_doctor_access(doctor_b).allowed and decisions[-1]
    assert (
        resolver.resolve_doctor_access(doctor_a, purpose="read").allowed
        and not decisions[-1]
    )
    with f.engine.connect() as db:
        until = (
            db.execute(sa.select(f.operation.c.export_until))
            .scalar_one()
            .replace(tzinfo=UTC)
        )
    assert (
        resolver.resolve_doctor_access(doctor_a, purpose="read", at=until).denial_reason
        == "scribe_export_period_expired"
    )

    package(
        monkeypatch,
        "app.configuration.session_prep",
        SESSION_PREP_PROVIDER_SETTINGS=SimpleNamespace(enabled=False),
    )
    package(
        monkeypatch,
        "app.services.session_prep_provider_access",
        resolve_session_prep_provider_access=lambda *_args, **_kwargs: SimpleNamespace(
            allowed=False
        ),
    )
    source(
        monkeypatch,
        "app.routes.dependencies.session_prep_account",
        "app/routes/dependencies/session_prep_account.py",
    )
    gate = source(
        monkeypatch,
        "app.routes.dependencies.practice_gate",
        "app/routes/dependencies/practice_gate.py",
    )

    def request(method, route, doctor):
        return Request(
            {
                "type": "http",
                "method": method,
                "scheme": "https",
                "path": "/internal/v1/notes",
                "headers": [],
                "server": ("synthetic.invalid", 443),
                "route": SimpleNamespace(name=route),
                "state": {"doctor": doctor},
            }
        )

    assert (
        gate.require_practice_in_force(request("GET", "note_detail", doctor_a)) is None
    )
    for method, route in (("POST", "note_detail"), ("GET", "new_processing_route")):
        with pytest.raises(HTTPException) as error:
            gate.require_practice_in_force(request(method, route, doctor_a))
        assert error.value.status_code == 403 and "read-only" in error.value.detail
    assert (
        gate.require_practice_in_force(
            request("POST", "new_processing_route", doctor_b)
        )
        is None
    )


def route_client(f, monkeypatch):
    package(monkeypatch, "app.configuration.authority", AuthorityKernelSettings=object)
    package(
        monkeypatch,
        "app.configuration.scribe_cancellation",
        SCRIBE_CANCELLATION_SETTINGS=f.settings,
    )
    package(
        monkeypatch,
        "app.configuration.scribe_native",
        native_authority_settings=lambda settings: settings,
    )

    async def workload():
        return SimpleNamespace(client_id="synthetic_owned_workload")

    async def identity():
        return SimpleNamespace(
            issuer=ISSUER, subject=SUBJECT, clerk_session_id="sess_synthetic"
        )

    class Boundary:
        def __init__(self, app, path):
            self.app, self.path = app, path

        async def __call__(self, scope, receive, send):
            scope.setdefault("state", {})["companion_record_deadline"] = f.budget()
            await self.app(scope, receive, send)

    def closed_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    package(
        monkeypatch,
        "app.routes.companion_records",
        RecordBoundary=Boundary,
        _closed_pairs=closed_pairs,
        problem=lambda status: __import__(
            "starlette.responses", fromlist=["Response"]
        ).Response(status_code=status),
    )
    package(
        monkeypatch,
        "app.routes.dependencies.entra_service",
        EntraServicePrincipal=object,
    )
    package(
        monkeypatch,
        "app.routes.dependencies.view_as",
        view_as_browser_session_present=lambda request: (
            request.headers.get("x-synthetic-view-as") == "1"
        ),
    )
    package(
        monkeypatch,
        "app.routes.embed_context",
        VerifiedClerkIdentity=object,
        authenticate_embed_platform_session=identity,
        get_authority_kernel_settings=lambda: f.auth,
    )
    package(
        monkeypatch,
        "app.routes.scribe_native_capture",
        authenticate_native_workload=workload,
    )
    package(
        monkeypatch,
        "app.services.superadmin_capability",
        SuperadminCapabilityError=type("CapabilityError", (Exception,), {}),
    )
    package(
        monkeypatch,
        "app.services.view_as",
        ViewAsError=type("ViewAsError", (Exception,), {}),
    )
    route = source(
        monkeypatch,
        "app.routes.scribe_cancellation",
        "app/routes/scribe_cancellation.py",
    )
    app = FastAPI()
    route.install(app)
    return TestClient(app), route, workload, identity


def test_actual_route_receives_and_recovers_private_receipt(fixture, monkeypatch):
    f = fixture
    client, route, *_ = route_client(f, monkeypatch)
    headers = {"X-Cosmaneura-Record-Version": "1"}
    before = client.post(
        route.PATH,
        headers=headers,
        json={"action": "preview", "request_nonce": str(uuid4())},
    )
    assert before.status_code == 200
    payload = {
        "action": "request",
        "request_nonce": str(uuid4()),
        "confirmation": "CANCEL",
        "preview_sha256": before.json()["preview_sha256"],
    }
    headers["Idempotency-Key"] = str(uuid4())
    first = client.post(route.PATH, headers=headers, json=payload)
    assert first.status_code == 200 and first.headers["cache-control"] == "no-store"
    assert client.post(route.PATH, headers=headers, json=payload).json() == first.json()
    status = client.post(
        route.PATH,
        headers={"X-Cosmaneura-Record-Version": "1"},
        json={
            "action": "status",
            "request_nonce": str(uuid4()),
            "operation_id": first.json()["operation_id"],
        },
    )
    assert status.status_code == 200 and status.json()["phase"] == "read-only-export"


@pytest.mark.parametrize(
    "case",
    [
        "disabled",
        "no_policy",
        "workload",
        "identity",
        "view_as",
        "extra_scope",
        "duplicate_json",
        "invalid_key",
        "no_key",
        "cookie",
        "oversized",
        "query",
    ],
)
def test_route_admission_failures_never_write(fixture, monkeypatch, case):
    f = fixture
    client, route, workload, identity = route_client(f, monkeypatch)
    payload = {
        "action": "request",
        "request_nonce": str(uuid4()),
        "preview_sha256": "a" * 64,
        "confirmation": "CANCEL",
    }
    headers = {"X-Cosmaneura-Record-Version": "1", "Idempotency-Key": str(uuid4())}
    expected = 422
    if case in ("disabled", "no_policy"):
        cast(FastAPI, client.app).dependency_overrides[route.cancellation_settings] = (
            lambda: f.contract.CancellationSettings(case == "no_policy")
        )
        expected = 503
    if case in ("workload", "identity"):

        async def deny():
            raise HTTPException(401)

        cast(FastAPI, client.app).dependency_overrides[
            workload if case == "workload" else identity
        ] = deny
        expected = 401
    if case == "view_as":
        headers["X-Synthetic-View-As"] = "1"
        expected = 403
    if case == "extra_scope":
        payload["doctor_ref"] = "doctor_b"
    if case == "invalid_key":
        headers["Idempotency-Key"] = str(UUID(int=0))
    if case == "no_key":
        headers.pop("Idempotency-Key")
    if case == "cookie":
        headers["Cookie"] = "synthetic=1"
    if case == "oversized":
        payload["padding"] = "x" * 3000
    target = route.PATH + ("?subject=user_synthetic_b" if case == "query" else "")
    if case == "duplicate_json":
        result = client.post(
            target,
            headers=headers | {"Content-Type": "application/json"},
            content='{"action":"request","action":"preview"}',
        )
    else:
        result = client.post(target, headers=headers, json=payload)
    assert result.status_code == expected
    with f.engine.connect() as db:
        assert (
            db.execute(sa.select(sa.func.count()).select_from(f.operation)).scalar()
            == 0
        )


@pytest.mark.parametrize(
    "action,purpose",
    [
        ("read", "read"),
        ("templates", "read"),
        ("template_preference", "read"),
        ("save_status", "read"),
        ("prepare", "process"),
        ("save", "process"),
        ("resolve", "process"),
    ],
)
def test_actual_native_note_dispatch_preserves_export_reads(action, purpose):
    # Compile the actual pure dispatcher seam, avoiding app/cloud bootstrap.
    tree = ast.parse((ROOT / "app/services/scribe_native_capture.py").read_text())
    function = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "_native_access_purpose"
    )
    env = {"Literal": Literal, "NativeCaptureRequest": SimpleNamespace}
    exec(  # noqa: S102 -- only the reviewed local pure-function AST, no input data
        compile(
            ast.Module(body=[function], type_ignores=[]),
            "native-purpose-source",
            "exec",
        ),
        env,
    )
    decide = env["_native_access_purpose"]
    assert (
        decide(
            SimpleNamespace(
                action="workflow_notes", note_request=SimpleNamespace(action=action)
            )
        )
        == purpose
    )
    assert (
        decide(SimpleNamespace(action="workflow_admission", note_request=None))
        == "process"
    )
    assert any(
        isinstance(n, ast.keyword)
        and n.arg == "access_purpose"
        and isinstance(n.value, ast.Call)
        and isinstance(n.value.func, ast.Name)
        and n.value.func.id == "_native_access_purpose"
        for n in ast.walk(tree)
    )
