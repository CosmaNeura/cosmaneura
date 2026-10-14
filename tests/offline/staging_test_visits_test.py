"""Execute the real provisioning and route bodies without Core/vendor startup.

Run with --confcutdir=tests/offline. Set TEST_VISIT_SQL_URL to a disposable local
PostgreSQL database to also exercise real row-lock concurrency.
"""

import ast
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import Barrier
from types import ModuleType, SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.testclient import TestClient
from pydantic import BaseModel

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import registry
from sqlmodel import Field, Session, SQLModel, create_engine, select

ROOT = Path(__file__).resolve().parents[2]


class TestModels(SQLModel, registry=registry()):
    pass


class SeedDoctor(TestModels, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    email: str
    last_active_at: datetime | None = None
    deleted_at: datetime | None = None


class SeedPatient(TestModels, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    doctor_id: str = Field(foreign_key="seeddoctor.id")
    name: str
    date_of_birth: datetime


class SeedAppointment(TestModels, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    doctor_id: str = Field(foreign_key="seeddoctor.id")
    patient_id: str = Field(foreign_key="seedpatient.id")
    appointment_date: datetime
    appointment_name: str | None = None
    appointment_notes_preview: str | None = None
    created_by_system: str | None = None
    external_id: str | None = None


class SeedFixture(TestModels, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    doctor_id: str = Field(foreign_key="seeddoctor.id", unique=True)
    patient_id: str = Field(foreign_key="seedpatient.id")
    appointment_id: str = Field(foreign_key="seedappointment.id")
    fixture_version: str = "synthetic_preview_labs_v1"
    synthetic_only: bool = True
    provisioned_by: str


def load_source(path, namespace):
    # Only imports are removed; SQL, guards, transactions and validators are
    # the production source body, not copied test implementations.
    tree = ast.parse((ROOT / path).read_text())
    tree.body = [
        node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    exec(compile(tree, str(path), "exec"), namespace)


@pytest.fixture
def source(tmp_path, monkeypatch):
    url = os.environ.get("TEST_VISIT_SQL_URL", f"sqlite:///{tmp_path}/visits.sqlite")
    engine = create_engine(url)
    TestModels.metadata.create_all(engine)
    doctor_id = str(uuid4())

    @contextmanager
    def sessions():
        with Session(engine, expire_on_commit=False) as session:
            yield session

    with sessions() as session:
        session.add(
            SeedDoctor(
                id=doctor_id,
                email="invented@example.test",
                last_active_at=datetime.now(UTC),
            )
        )
        session.commit()
    module = ModuleType("offline_staging_test_visits")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    ns = module.__dict__
    ns.update(
        re=re,
        dataclass=dataclass,
        datetime=datetime,
        date=date,
        UTC=UTC,
        timedelta=timedelta,
        cast=cast,
        UUID=UUID,
        TypeIDType=str,
        get_session=sessions,
        select=select,
        is_staging=lambda: True,
        IntegrityError=IntegrityError,
        Doctor=SeedDoctor,
        Patient=SeedPatient,
        Appointment=SeedAppointment,
        SyntheticPreviewAppointmentFixture=SeedFixture,
        SYNTHETIC_PREVIEW_FIXTURE_VERSION="synthetic_preview_labs_v1",
    )
    load_source("app/services/staging_synthetic_appointment.py", ns)
    from datetime import time

    ns["time"] = time
    load_source("app/services/staging_test_visits.py", ns)
    args = dict(
        doctor_id=doctor_id,
        doctor_email="invented@example.test",
        request_id=uuid4(),
        visit_date=datetime.now(UTC).date(),
    )
    yield ns, sessions, args, engine
    with sessions() as session:
        for model in (SeedFixture, SeedAppointment, SeedPatient, SeedDoctor):
            for row in session.exec(select(model)).all():
                session.delete(row)
            session.flush()
        session.commit()
    engine.dispose()


def test_empty_pair_retry_and_new_request_preserve_old_visits(source):
    ns, sessions, args, _ = source
    create = ns["ensure_staging_test_visits"]
    first = create(**args)
    again = create(**args)
    assert first.created and not again.created
    ids = [row.id for row in first.appointments]
    assert [row.id for row in again.appointments] == ids
    second = create(**(args | {"request_id": uuid4()}))
    assert not set(ids).intersection(row.id for row in second.appointments)
    with sessions() as session:
        saved = [session.get(SeedAppointment, identifier) for identifier in ids]
        assert all(
            row.external_id is None
            and row.appointment_notes_preview == ns["TEST_VISIT_NOTICE"]
            for row in saved
        )
        assert len(session.exec(select(SeedPatient)).all()) == 1
        assert (
            len(session.exec(select(SeedAppointment)).all()) == 5
        )  # fixed Preview fixture + two pairs
        assert (
            session.get(SeedAppointment, ids[0]).appointment_name
            == first.appointments[0].appointment_name
        )


@pytest.mark.parametrize(
    "problem",
    [
        "production",
        "future",
        "wrong_email",
        "tampered_patient",
        "partial_pair",
        "changed_date",
    ],
)
def test_refusal_does_not_write_or_repair_existing_data(source, problem):
    ns, sessions, args, _ = source
    create = ns["ensure_staging_test_visits"]
    first = create(**args)
    with sessions() as session:
        if problem == "tampered_patient":
            appointment = session.get(SeedAppointment, first.appointments[0].id)
            patient = session.get(SeedPatient, appointment.patient_id)
            patient.name = "Synthetic mismatched fixture"
            session.add(patient)
        if problem == "partial_pair":
            session.delete(session.get(SeedAppointment, first.appointments[1].id))
        session.commit()
        before = [
            (row.id, row.appointment_name)
            for row in session.exec(select(SeedAppointment)).all()
        ]
    if problem == "production":
        ns["is_staging"] = lambda: False
    if problem == "future":
        args["visit_date"] += timedelta(days=1)
    if problem == "wrong_email":
        args["doctor_email"] = "unrelated@example.test"
    if problem == "changed_date":
        args["visit_date"] -= timedelta(days=1)
    with pytest.raises(ns["StagingSyntheticAppointmentError"]):
        create(**args)
    with sessions() as session:
        assert [
            (row.id, row.appointment_name)
            for row in session.exec(select(SeedAppointment)).all()
        ] == before


def test_production_refuses_before_any_database_access(source):
    ns, _, args, _ = source
    ns["is_staging"] = lambda: False
    ns["get_session"] = lambda: pytest.fail("database accessed outside staging")
    with pytest.raises(ns["StagingSyntheticAppointmentError"]):
        ns["ensure_staging_test_visits"](**args)


def test_recovery_preserves_generated_titles_and_original_receipt(source):
    ns, sessions, args, _ = source
    create = ns["ensure_staging_test_visits"]
    first = create(**args)
    with sessions() as session:
        row = session.get(SeedAppointment, first.appointments[0].id)
        row.appointment_name = "Invented difficulty falling asleep"
        session.add(row)
        session.commit()
    recovered = create(**args)
    assert not recovered.created
    assert recovered.appointments == first.appointments
    with sessions() as session:
        assert (
            session.get(SeedAppointment, first.appointments[0].id).appointment_name
            == "Invented difficulty falling asleep"
        )


def test_old_committed_request_recovers_without_creating_or_provisioning(source):
    ns, sessions, args, _ = source
    create = ns["ensure_staging_test_visits"]
    first = create(**args)

    class LaterClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(days=8)

    ns["datetime"] = LaterClock
    ns["ensure_staging_synthetic_appointment"] = lambda **_: pytest.fail(
        "expired request attempted provisioning"
    )
    recovered = create(**args)
    assert not recovered.created and recovered.appointments == first.appointments
    with pytest.raises(ns["StagingSyntheticAppointmentError"]):
        create(**(args | {"request_id": uuid4()}))
    with sessions() as session:
        assert len(session.exec(select(SeedAppointment)).all()) == 3


def test_old_unseen_request_never_provisions_patient_or_visits(source):
    ns, sessions, args, _ = source
    args["visit_date"] -= timedelta(days=8)
    with pytest.raises(ns["StagingSyntheticAppointmentError"]):
        ns["ensure_staging_test_visits"](**args)
    with sessions() as session:
        assert not session.exec(select(SeedPatient)).all()
        assert not session.exec(select(SeedAppointment)).all()


def test_same_retained_uuid_is_scoped_to_each_signed_in_doctor(source):
    ns, sessions, args, _ = source
    create = ns["ensure_staging_test_visits"]
    first = create(**args)
    other = dict(doctor_id=str(uuid4()), doctor_email="other-invented@example.test")
    with sessions() as session:
        session.add(
            SeedDoctor(
                id=other["doctor_id"],
                email=other["doctor_email"],
                last_active_at=datetime.now(UTC),
            )
        )
        session.commit()
    second = create(**(args | other))
    assert second.created
    assert not {row.id for row in first.appointments}.intersection(
        row.id for row in second.appointments
    )
    assert create(**args).appointments == first.appointments
    assert create(**(args | other)).appointments == second.appointments
    with sessions() as session:
        for result, doctor_id in (
            (first, args["doctor_id"]),
            (second, other["doctor_id"]),
        ):
            assert all(
                session.get(SeedAppointment, row.id).doctor_id == doctor_id
                for row in result.appointments
            )


def test_postgres_concurrent_retry_creates_one_pair(source):
    ns, sessions, args, engine = source
    if engine.dialect.name != "postgresql":
        pytest.skip("requires disposable local PostgreSQL")
    # Provision the independently governed patient first; race the pair writes.
    ns["ensure_staging_synthetic_appointment"](
        doctor_id=args["doctor_id"],
        doctor_email=args["doctor_email"],
        operator_ref="offline_concurrency_fixture",
    )
    barrier = Barrier(2)

    def create():
        barrier.wait(timeout=10)
        return ns["ensure_staging_test_visits"](**args)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create(), range(2)))
    assert sorted(result.created for result in results) == [False, True]
    assert [row.id for row in results[0].appointments] == [
        row.id for row in results[1].appointments
    ]
    with sessions() as session:
        assert len(session.exec(select(SeedAppointment)).all()) == 3


def test_route_scope_and_fixed_request_contract(source):
    ns, _, args, _ = source
    ns.update(
        APIRouter=APIRouter,
        HTTPException=HTTPException,
        Request=Request,
        Response=Response,
        BaseModel=BaseModel,
    )
    load_source("app/routes/staging_test_visits.py", ns)
    app = FastAPI()
    state = SimpleNamespace(
        doctor=SimpleNamespace(id=args["doctor_id"], email=args["doctor_email"]),
        view_as=None,
    )

    @app.middleware("http")
    async def known_signed_in_test_account(request, call_next):
        request.state.doctor, request.state.view_as = state.doctor, state.view_as
        return await call_next(request)

    app.include_router(ns["staging_test_visits_api"])
    body = dict(
        request_id=str(args["request_id"]), visit_date=args["visit_date"].isoformat()
    )
    with TestClient(app) as client:
        assert (
            client.post(
                "/staging/test-visits", json=body | {"doctor_id": "unrelated"}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/staging/test-visits",
                json=body | {"patient_name": "Synthetic injected input"},
            ).status_code
            == 422
        )
        state.view_as = object()
        assert client.post("/staging/test-visits", json=body).status_code == 403
        state.view_as = None
        response = client.post("/staging/test-visits", json=body)
        assert (
            response.status_code == 200
            and response.headers["cache-control"] == "no-store"
        )
        assert response.json()["request_id"] == body["request_id"]
        assert len(response.json()["visits"]) == 2
        ns["is_staging"] = lambda: False
        assert client.post("/staging/test-visits", json=body).status_code == 404


def test_route_inherits_existing_authenticated_internal_dependencies():
    text = (ROOT / "app/routes/internal.py").read_text()
    assert "internal_api_app.include_router(staging_test_visits_api)" in text
    assert "Depends(authenticate_clerk_request_middleware)" in text
    assert "Depends(apply_view_as)" in text
    assert "Depends(require_practice_in_force)" in text
