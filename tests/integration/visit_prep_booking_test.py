"""Signed synthetic Acuity notification through the real creation transaction."""

import base64
import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.configuration import visit_prep, visit_prep_acuity
from app.configuration.visit_prep import VisitPrepSettings
from app.configuration.visit_prep_acuity import (
    AcuityBookingSettings,
    AcuityCalendarBinding,
    load_acuity_booking_settings,
)
from app.routes.visit_prep_acuity import PATH, router
from app.services import (
    visit_prep_access,
)
from app.services import (
    visit_prep_acuity as acuity,
)
from app.services import (
    visit_prep_booking as booking_service,
)
from app.services.visit_prep_packages import list_visit_preps
from app.utils.encryption import decrypt_value

from activemodel.session_manager import get_session, global_session
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.visit_prep import VisitPrep
from app.models.visit_prep_booking import VisitPrepBookingReceipt
from app.models.visit_prep_delivery import VisitPrepEmailDelivery
from sqlalchemy import text
from sqlmodel import select

from tests.services.visit_preps_test import _scope

BODY = b"action=scheduled&id=54321&calendarID=27238&appointmentTypeID=12"


def _ready(monkeypatch):
    scope = _scope("booking-handoff", now=datetime.now(UTC))
    binding = AcuityCalendarBinding(
        27238,
        str(scope.practice.id),
        str(scope.owner.id),
        str(scope.doctor.id),
        "America/New_York",
        "Dr. Synthetic",
    )
    settings = AcuityBookingSettings(
        True, uuid4(), "123456", "invented-acuity-key-not-live", (binding,)
    )
    monkeypatch.setattr(visit_prep_acuity, "SETTINGS", settings)
    admission = VisitPrepSettings(
        enabled=True, enabled_doctor_ids=frozenset({str(scope.doctor.id)})
    )
    monkeypatch.setattr(visit_prep, "VISIT_PREP_SETTINGS", admission)
    monkeypatch.setattr(visit_prep_access, "VISIT_PREP_SETTINGS", admission)
    response = {
        "id": 54321,
        "calendarID": 27238,
        "firstName": "José",
        "email": "patient@example.com",
        "phone": "+12025550123",
        "datetime": (datetime.now(UTC) + timedelta(days=14))
        .astimezone(ZoneInfo("America/New_York"))
        .isoformat(),
        "timezone": "America/New_York",
        "canceled": False,
        "forms": [{"value": "invented clinical field must not be copied"}],
        "notes": "invented clinical note",
    }
    calls = []
    client_factory = httpx.Client

    def remote(request):
        calls.append(request)
        assert (
            str(request.url) == "https://acuityscheduling.com/api/v1/appointments/54321"
        )
        assert request.method == "GET"
        return httpx.Response(200, json=response)

    monkeypatch.setattr(
        acuity.httpx,
        "Client",
        lambda **options: client_factory(
            **options, transport=httpx.MockTransport(remote)
        ),
    )
    app = FastAPI()
    app.include_router(router)
    return scope, binding, settings, response, calls, TestClient(app)


def _post(client, settings, *, body=BODY, signature=None):
    signature = (
        signature
        or base64.b64encode(
            hmac.new(settings.api_key.encode(), body, hashlib.sha256).digest()
        ).decode()
    )
    return client.post(
        PATH,
        content=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Acuity-Signature": signature,
        },
    )


def _rows(scope):
    with get_session() as session:
        preps = session.exec(
            select(VisitPrep).where(
                VisitPrep.owner_practice_member_id == scope.owner.id
            )
        ).all()
        receipts = session.exec(
            select(VisitPrepBookingReceipt).where(
                VisitPrepBookingReceipt.owner_practice_member_id == scope.owner.id
            )
        ).all()
        return preps, receipts


def test_signed_booking_creates_one_chart_free_preparation_and_exact_replay(
    monkeypatch,
):
    scope, _, settings, _, calls, client = _ready(monkeypatch)
    with client:
        first, replay = _post(client, settings), _post(client, settings)
    assert first.status_code == replay.status_code == 200
    assert (
        first.json()["disposition"] == "created"
        and replay.json()["disposition"] == "replayed"
    )
    assert first.json()["preparation_id"] == replay.json()["preparation_id"]
    assert first.json()["delivery"] == "not_requested" and len(calls) == 2
    preps, receipts = _rows(scope)
    assert len(preps) == len(receipts) == 1
    prep, receipt = preps[0], receipts[0]
    assert prep.patient_id is prep.appointment_id is None
    assert prep.creation_source == "appointment_booking"
    assert receipt.question_set_version_id == scope.question_set_version_id
    assert "clinical" not in decrypt_value(prep.recipient_snapshot_ciphertext)
    assert "patient@example.com" not in str(receipt.model_dump())
    assert "vp1_" not in first.text + replay.text + str(receipt.model_dump())
    assert first.headers["cache-control"] == "no-store"
    with get_session() as session:
        assert not session.exec(
            select(Patient).where(Patient.doctor_id == scope.doctor.id)
        ).all()
        assert not session.exec(
            select(VisitPrepEmailDelivery).where(
                VisitPrepEmailDelivery.visit_prep_id == prep.id
            )
        ).all()
    listed = list_visit_preps(
        owner_practice_member_id=scope.owner.id,
        owner_doctor_id=scope.doctor.id,
        practice_id=scope.practice.id,
        expected_clerk_subject=scope.doctor.clerk_id,
    )
    assert [p.id for p in listed.preparations] == [prep.id]


def test_current_changed_or_reassigned_booking_never_creates_a_second_preparation(
    monkeypatch,
):
    scope, _, settings, response, _, client = _ready(monkeypatch)
    with client:
        assert _post(client, settings).status_code == 200
        response["email"] = "other@example.com"
        assert _post(client, settings).status_code == 500
        response["calendarID"] = 99
        assert _post(client, settings).status_code == 500
    preps, receipts = _rows(scope)
    assert len(preps) == len(receipts) == 1


@pytest.mark.parametrize(
    "failure", ["signature", "duplicate_field", "disabled", "canceled"]
)
def test_invalid_or_unavailable_source_has_no_creation_effect(monkeypatch, failure):
    scope, _, settings, response, calls, client = _ready(monkeypatch)
    body, signature = BODY, None
    if failure == "signature":
        signature = "A" * 44
    elif failure == "duplicate_field":
        body += b"&id=54321"
    elif failure == "disabled":
        monkeypatch.setattr(visit_prep_acuity, "SETTINGS", AcuityBookingSettings())
    else:
        response["canceled"] = True
    with client:
        result = _post(client, settings, body=body, signature=signature)
    assert result.status_code != 200 and _rows(scope) == ([], [])
    assert "José" not in result.text and "patient@example.com" not in result.text
    assert len(calls) == (1 if failure == "canceled" else 0)


def test_late_source_disable_rolls_back_receipt_preparation_and_credential(monkeypatch):
    scope, _, settings, _, _, client = _ready(monkeypatch)
    create = booking_service.create_locked_visit_prep

    def late(*args, **kwargs):
        result = create(*args, **kwargs)
        monkeypatch.setattr(visit_prep_acuity, "SETTINGS", AcuityBookingSettings())
        return result

    monkeypatch.setattr(booking_service, "create_locked_visit_prep", late)
    with client:
        assert _post(client, settings).status_code == 500
    assert _rows(scope) == ([], [])


def test_booking_evidence_is_immutable_even_through_direct_sql(monkeypatch):
    scope, _, settings, _, _, client = _ready(monkeypatch)
    with client:
        assert _post(client, settings).status_code == 200
    _, receipts = _rows(scope)
    for statement in (
        "UPDATE visit_prep_booking_receipt SET snapshot_hmac = :digest WHERE id = :id",
        "DELETE FROM visit_prep_booking_receipt WHERE id = :id",
    ):
        with get_session() as session:
            with pytest.raises(Exception, match="immutable"):
                session.execute(
                    text(statement), {"digest": "a" * 64, "id": receipts[0].id.uuid}
                )
            session.rollback()


def test_source_config_defaults_off_and_rejects_ambiguous_calendar_mapping(monkeypatch):
    _, binding, _, _, _, _ = _ready(monkeypatch)
    assert not load_acuity_booking_settings({}).enabled
    values = {
        "VISIT_PREP_ACUITY_ENABLED": "true",
        "VISIT_PREP_ACUITY_CONNECTION_ID": str(uuid4()),
        "VISIT_PREP_ACUITY_ACCOUNT_ID": "123456",
        "VISIT_PREP_ACUITY_API_KEY": "invented-key-not-live",
        "VISIT_PREP_ACUITY_CALENDAR_BINDINGS": json.dumps([asdict(binding)]),
    }
    assert load_acuity_booking_settings(values).enabled
    values["VISIT_PREP_ACUITY_CALENDAR_BINDINGS"] = json.dumps(
        [asdict(binding), asdict(binding)]
    )
    assert not load_acuity_booking_settings(values).enabled


def test_source_reference_is_scoped_to_physical_connection_not_just_numeric_id(
    monkeypatch,
):
    scope, _, settings, _, _, client = _ready(monkeypatch)
    with client:
        assert _post(client, settings).status_code == 200
        other = replace(settings, connection_id=uuid4())
        monkeypatch.setattr(visit_prep_acuity, "SETTINGS", other)
        assert _post(client, other).status_code == 200
    assert len(_rows(scope)[0]) == len(_rows(scope)[1]) == 2


def test_concurrent_signed_notifications_have_one_creation_and_one_replay(monkeypatch):
    scope, _, settings, _, _, client = _ready(monkeypatch)
    start = Barrier(2)

    def submit():
        start.wait(timeout=5)
        return _post(client, settings)

    with client, ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(), range(2)))
    assert [r.status_code for r in results] == [200, 200]
    assert {r.json()["disposition"] for r in results} == {"created", "replayed"}
    assert len({r.json()["preparation_id"] for r in results}) == 1
    assert len(_rows(scope)[0]) == len(_rows(scope)[1]) == 1


def test_webhook_commits_its_own_transaction_not_pending_request_work(monkeypatch):
    scope, _, settings, _, _, _ = _ready(monkeypatch)
    pending = Doctor(clerk_id=f"synthetic-uncommitted-{uuid4()}")

    async def request_work():
        with global_session() as session:
            session.add(pending)
            yield
            assert pending in session.new

    app = FastAPI(dependencies=[Depends(request_work)])
    app.include_router(router)
    with TestClient(app) as client:
        assert _post(client, settings).status_code == 200
    with get_session() as session:
        assert (
            session.exec(
                select(Doctor).where(Doctor.clerk_id == pending.clerk_id)
            ).one_or_none()
            is None
        )
    assert len(_rows(scope)[0]) == len(_rows(scope)[1]) == 1
