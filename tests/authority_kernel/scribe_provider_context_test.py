"""Offline Core discovery SQL and the private read boundary; synthetic values."""

import time
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from typeid import TypeID

from app.configuration import scribe_native
from app.configuration.authority import AuthorityKernelSettings
from app.routes import scribe_provider_context as route
from app.routes.dependencies.entra_service import EntraServicePrincipal
from app.routes.embed_context import (
    VerifiedClerkIdentity,
    authenticate_embed_platform_session,
)
from app.services import authority
from app.services import scribe_provider_context as service
from app.services.companion_encounter_contract import (
    ConnectionContent,
    ConnectionSnapshot,
    digest,
)
from app.services.scribe_encounter_contract import CatalogRequest

from app.models.authority import AuthorityActorExternalIdentity
from app.models.doctor import Doctor
from app.models.scribe_capture_binding import ScribeCaptureProviderLink
from sqlalchemy import Column, DateTime, MetaData, String, Table
from sqlmodel import Session, create_engine

ACTOR = "authority_actor_00000000000000000000000001"
TENANT = "authority_tenant_0000000000e008000000000002"
LINK = "scribe_provider_link_00000000000000000000000003"
DOCTOR = "doctor_00000000000000000000000004"
IDENTITY = "actor_identity_00000000000000000000000005"
ISSUER = "https://test.clerk.invalid"
AUTH = AuthorityKernelSettings(True, 900, 300, clerk_issuer=ISSUER)


def connection():
    content = ConnectionContent(
        actor_ref=ACTOR,
        tenant_ref=TENANT,
        provider_link_ref=LINK,
        practice_ref="practice_00000000000000000000000006",
        practice_label="Invented practice",
        review_sha256="a" * 64,
    ).model_dump(mode="json")
    return ConnectionSnapshot(**content, connection_sha256=digest(content))


@pytest.fixture
def discovery_db():
    # Only the actual read columns are needed. No authority policy is simulated
    # here; the existing current-provider resolver remains the permission gate.
    engine = create_engine("sqlite://")
    metadata = MetaData()
    identities = Table(
        str(AuthorityActorExternalIdentity.__tablename__),
        metadata,
        Column("id", String, primary_key=True),
        Column("issuer", String),
        Column("subject", String),
        Column("revoked_at", DateTime),
    )
    doctors = Table(
        str(Doctor.__tablename__),
        metadata,
        Column("id", String, primary_key=True),
        Column("clerk_id", String),
    )
    links = Table(
        str(ScribeCaptureProviderLink.__tablename__),
        metadata,
        Column("id", String, primary_key=True),
        Column("tenant_id", String),
        Column("source_identity_id", String),
        Column("doctor_id", String),
        Column("revoked_at", DateTime),
    )
    metadata.create_all(engine)
    with engine.begin() as db:
        db.execute(
            identities.insert(),
            dict(id=IDENTITY, issuer=ISSUER, subject="user_synthetic"),
        )
        db.execute(doctors.insert(), dict(id=DOCTOR, clerk_id="user_synthetic"))
        db.execute(
            links.insert(),
            dict(
                id=LINK,
                tenant_id=TypeID.from_string(TENANT).uuid.hex,
                source_identity_id=IDENTITY,
                doctor_id=DOCTOR,
            ),
        )
    with Session(engine) as session:
        yield session, links, identities, doctors
    engine.dispose()


def test_discovery_reads_existing_core_mapping(discovery_db):
    session, *_ = discovery_db
    assert (
        service._discovered_tenant(session, issuer=ISSUER, subject="user_synthetic")
        == TENANT
    )


@pytest.mark.parametrize(
    "issuer,subject",
    [("https://other.clerk.invalid", "user_synthetic"), (ISSUER, "user_other")],
)
def test_discovery_cannot_select_another_subject_or_issuer(
    discovery_db, issuer, subject
):
    session, *_ = discovery_db
    with pytest.raises(authority.AuthorityRequestDeniedError):
        service._discovered_tenant(session, issuer=issuer, subject=subject)


@pytest.mark.parametrize(
    "changed", ["link_revoked", "identity_revoked", "doctor_changed", "ambiguous"]
)
def test_missing_changed_and_ambiguous_mapping_denies(discovery_db, changed):
    session, links, identities, doctors = discovery_db
    if changed == "link_revoked":
        session.execute(links.update().values(revoked_at=datetime(2026, 1, 1)))
    elif changed == "identity_revoked":
        session.execute(identities.update().values(revoked_at=datetime(2026, 1, 1)))
    elif changed == "doctor_changed":
        session.execute(doctors.update().values(clerk_id="user_other"))
    else:
        session.execute(
            links.insert().values(
                id="scribe_provider_link_00000000000000000000000007",
                tenant_id=TypeID.from_string(TENANT).uuid.hex,
                source_identity_id=IDENTITY,
                doctor_id=DOCTOR,
            )
        )
    with pytest.raises(authority.AuthorityRequestDeniedError):
        service._discovered_tenant(session, issuer=ISSUER, subject="user_synthetic")


def test_current_provider_resolver_is_revalidated_and_never_committed(monkeypatch):
    session = SimpleNamespace(
        no_autoflush=nullcontext(), new=set(), dirty=set(), deleted=set()
    )
    calls = []
    session.expire_all = lambda: calls.append("expire")
    session.rollback = lambda: calls.append("rollback")

    @contextmanager
    def managed():
        yield session

    monkeypatch.setattr(service, "global_session", managed)
    monkeypatch.setattr(service, "transaction_budget", lambda *_: nullcontext())
    monkeypatch.setattr(service, "_discovered_tenant", lambda *_args, **_kwargs: TENANT)

    def current(_session, args):
        assert args["tenant_ref"] == TENANT
        assert args["session_id"] == "sess_synthetic"
        calls.append("current")
        return connection(), DOCTOR, SimpleNamespace(id=LINK)

    monkeypatch.setattr(service, "_current_provider", current)
    assert (
        service._connection(
            issuer=ISSUER,
            subject="user_synthetic",
            session_id="sess_synthetic",
            authority_settings=AUTH,
            deadline=time.monotonic() + 10,
        )
        == connection()
    )
    assert calls == ["current", "expire", "current", "rollback"]
    calls.clear()
    session.dirty.add("changed")
    with pytest.raises(service.RecordUnavailable):
        service._connection(
            issuer=ISSUER,
            subject="user_synthetic",
            session_id="sess_synthetic",
            authority_settings=AUTH,
            deadline=time.monotonic() + 10,
        )
    assert calls == ["current", "rollback"]


def test_catalog_derives_authority_and_rechecks_connection(monkeypatch):
    reads = iter([connection(), connection()])
    monkeypatch.setattr(service, "_connection", lambda **_kwargs: next(reads))
    nonce = uuid4()
    calls = []

    def catalog(**kwargs):
        calls.append(kwargs)
        assert isinstance(kwargs["request"], CatalogRequest)
        assert kwargs["request"].expected_actor_ref == ACTOR
        assert kwargs["request"].expected_tenant_ref == TENANT
        return dict(
            version="1",
            action="list",
            request_nonce=str(nonce),
            account=dict(actor_ref=ACTOR, tenant_ref=TENANT),
            encounters=[],
        )

    monkeypatch.setattr(service.scribe_encounters, "encounter_workflow", catalog)
    result = service.read_provider_context(
        issuer=ISSUER,
        subject="user_synthetic",
        session_id="sess_synthetic",
        request=service.ProviderContextRequest(action="catalog", request_nonce=nonce),
        authority_settings=AUTH,
        deadline=time.monotonic() + 10,
    )
    assert result.catalog and len(calls) == 1
    changed = connection().model_copy(
        update={"provider_link_ref": "scribe_provider_link_00000000000000000000000007"}
    )
    reads = iter([connection(), changed])
    with pytest.raises(authority.AuthorityRequestDeniedError):
        service.read_provider_context(
            issuer=ISSUER,
            subject="user_synthetic",
            session_id="sess_synthetic",
            request=service.ProviderContextRequest(
                action="catalog", request_nonce=nonce
            ),
            authority_settings=AUTH,
            deadline=time.monotonic() + 10,
        )


@pytest.mark.parametrize(
    "extra",
    [
        dict(action="create"),
        dict(expected_tenant_ref=TENANT),
        dict(expected_actor_ref=ACTOR),
    ],
)
def test_closed_context_cannot_supply_authority_or_mutation(extra):
    with pytest.raises(ValidationError):
        service.ProviderContextRequest.model_validate(
            dict(action="connection", request_nonce=str(uuid4())) | extra
        )


def client(monkeypatch, *, enabled=True):
    app = FastAPI()
    route.install(app)
    app.dependency_overrides[route.authenticate_native_workload] = (
        lambda: EntraServicePrincipal("11111111-1111-4111-8111-111111111111")
    )
    app.dependency_overrides[authenticate_embed_platform_session] = (
        lambda: VerifiedClerkIdentity(ISSUER, "user_synthetic", "sess_synthetic")
    )
    app.dependency_overrides[route.context_enabled] = lambda: enabled
    app.dependency_overrides[route.get_authority_kernel_settings] = lambda: replace(
        AUTH, enabled=False
    )
    monkeypatch.setattr(
        scribe_native,
        "SCRIBE_NATIVE_WORKLOAD_AUTH_SETTINGS",
        SimpleNamespace(enabled=True),
    )
    calls = []

    def read(**kwargs):
        calls.append(kwargs)
        assert kwargs["authority_settings"].enabled
        assert kwargs["session_id"] == "sess_synthetic"
        return service.ProviderContextResponse(
            action=kwargs["request"].action,
            request_nonce=kwargs["request"].request_nonce,
            checked_at=datetime(2026, 1, 1, tzinfo=UTC),
            connection=connection(),
        )

    monkeypatch.setattr(service, "read_provider_context", read)
    return TestClient(app), calls, app


def headers():
    return {
        "Authorization": "Bearer synthetic-human",
        "X-Cosmaneura-Workload-Authorization": "Bearer synthetic-workload",
        "X-Cosmaneura-Record-Version": "1",
    }


def test_private_route_off_does_not_read_or_enable_global_authority(monkeypatch):
    original_authority = scribe_native.AUTHORITY_KERNEL_SETTINGS
    http, calls, _ = client(monkeypatch, enabled=False)
    assert (
        http.post(
            route.PATH,
            json=dict(action="connection", request_nonce=str(uuid4())),
            headers=headers(),
        ).status_code
        == 503
    )
    assert calls == []
    assert scribe_native.AUTHORITY_KERNEL_SETTINGS is original_authority


def test_private_route_returns_bounded_no_store_connection(monkeypatch):
    http, calls, _ = client(monkeypatch)
    response = http.post(
        route.PATH,
        json=dict(action="connection", request_nonce=str(uuid4())),
        headers=headers(),
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-cosmaneura-record-version"] == "1"
    assert response.json()["connection"]["actor_ref"] == ACTOR
    assert len(calls) == 1


def test_missing_workload_token_cannot_reach_context(monkeypatch):
    http, calls, app = client(monkeypatch)
    del app.dependency_overrides[route.authenticate_native_workload]
    assert (
        http.post(
            route.PATH,
            json=dict(action="connection", request_nonce=str(uuid4())),
            headers={"Authorization": "Bearer synthetic-human"},
        ).status_code
        == 401
    )
    assert calls == []


@pytest.mark.parametrize(
    "body",
    [
        '{"action":"connection","action":"catalog"}',
        '{"action":"create"}',
        '{"action":"connection","expected_actor_ref":"x"}',
        "x" * 1025,
    ],
)
def test_invalid_body_cannot_reach_core(monkeypatch, body):
    http, calls, _ = client(monkeypatch)
    assert (
        http.post(
            route.PATH,
            content=body,
            headers=headers() | {"Content-Type": "application/json"},
        ).status_code
        == 422
    )
    assert calls == []
