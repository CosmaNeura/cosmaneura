"""Actual SMS queue and metadata history; synthetic qualification, no provider."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.configuration import visit_prep_sms
from app.configuration.visit_prep_cadence import VisitPrepCadenceSettings
from app.configuration.visit_prep_sms import VisitPrepSMSRouting
from app.services import visit_prep_access
from app.services import visit_prep_sms_outbox as outbox
from app.services.visit_prep_interviews import confirm_interview
from app.services.visit_prep_sms_consent import (
    PatientSMSConsentInput,
    get_patient_sms_consent_view,
    record_patient_sms_consent,
)
from app.services.visit_prep_sms_contract import (
    POLICY_FINGERPRINT,
    SMSStandingApproval,
    VisitPrepSMSUnavailable,
)
from app.services.visit_prep_sms_suppression import (
    sms_sender_digest,
)
from app.utils.encryption import decrypt_value, pseudonymous_digest

from activemodel.session_manager import get_engine
from app.models.visit_prep import VisitPrepCredentialGeneration
from app.models.visit_prep_sms_delivery import VisitPrepSMSDelivery
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlmodel import Session, select

from tests.services.visit_prep_interviews_test import _fixture


@pytest.fixture(autouse=True)
def _finish_queue_only_fixtures():
    # Keep this module's deliberately undrained queues from becoming unrelated
    # global-worker candidates later. Preserve all retained rows/evidence and
    # use normal guarded terminal transitions, never truncate/delete.
    with Session(get_engine()) as session:
        before = set(session.exec(select(VisitPrepSMSDelivery.id)).all())
    yield
    with Session(get_engine()) as session:
        rows = session.exec(
            select(VisitPrepSMSDelivery).where(VisitPrepSMSDelivery.state == "queued")
        ).all()
        for row in rows:
            if row.id not in before:
                session.execute(
                    text(
                        "UPDATE visit_prep_sms_delivery SET state='suppressed',outcome_code='scope_unavailable',payload_ciphertext=NULL,finalized_at=clock_timestamp() WHERE id=:id"
                    ),
                    {"id": row.id.uuid},
                )
        session.commit()


def _ready(monkeypatch, *, routing=None, phone="+12025550123", scheduled_at=None):
    fixture = _fixture(
        monkeypatch, "sms-dispatch", phone=phone, scheduled_at=scheduled_at
    )
    settings = replace(
        visit_prep_access.VISIT_PREP_SETTINGS,
        sms_consent_capture_enabled=True,
        sms_send_enabled=True,
    )
    monkeypatch.setattr(visit_prep_access, "VISIT_PREP_SETTINGS", settings)
    routing = routing or VisitPrepSMSRouting("synthetic", str(uuid4()), "+12025550100")
    monkeypatch.setattr(visit_prep_sms, "ROUTING", routing)
    hour = datetime.now(UTC).hour
    monkeypatch.setattr(
        outbox,
        "current_standard_cadence",
        lambda: VisitPrepCadenceSettings(
            "synthetic-no-quiet-now", (hour + 1) % 24, (hour + 2) % 24, 4
        ),
    )
    confirm_interview(
        access_token=fixture.access_token, first_name="José", role="patient"
    )
    view = get_patient_sms_consent_view(
        access_token=fixture.access_token, first_name="José", role="patient"
    )
    record_patient_sms_consent(
        access_token=fixture.access_token,
        first_name="José",
        decision=PatientSMSConsentInput(
            str(uuid4()),
            0,
            "granted",
            "patient",
            view.grant_disclosure_fingerprint,
            view.maximum_expires_at,
            True,
            False,
        ),
    )
    now = datetime.now(UTC)
    approval = SMSStandingApproval(
        str(fixture.owner.id),
        str(fixture.doctor.id),
        str(fixture.practice.id),
        fixture.doctor.clerk_id,
        sms_sender_digest(routing),
        POLICY_FINGERPRINT,
        pseudonymous_digest(str(uuid4()), context="synthetic-source"),
        pseudonymous_digest("synthetic-qualified-source", context="synthetic-source"),
        now - timedelta(minutes=1),
        now + timedelta(hours=23),
    )
    with Session(get_engine()) as session:
        credential = session.exec(
            select(VisitPrepCredentialGeneration).where(
                VisitPrepCredentialGeneration.visit_prep_id == fixture.prep_id
            )
        ).one()
    with Session(get_engine()) as session:
        from app.models.visit_prep_sms_consent import VisitPrepSMSConsentEvent

        consent_id = str(
            session.exec(
                select(VisitPrepSMSConsentEvent).where(
                    VisitPrepSMSConsentEvent.visit_prep_id == fixture.prep_id
                )
            )
            .one()
            .id
        )
    options = dict(
        visit_prep_id=fixture.prep_id,
        credential_id=credential.id,
        step="booking_sms",
        access_token=fixture.access_token,
        operation_id=str(uuid4()),
        patient_origin="https://synthetic.example.test",
        approval=approval,
        settings=settings,
        # Deliberately synthetic source/control qualification, NOT a live approval adapter.
        approval_verifier=lambda s, a, **kw: a == approval,
        consent_verifier=lambda s, **kw: kw["consent_event_id"] == consent_id,
    )
    return fixture, options, routing


def _row(result):
    with Session(get_engine()) as session:
        return session.exec(
            select(VisitPrepSMSDelivery).where(
                VisitPrepSMSDelivery.id == outbox.TypeID.from_string(result.delivery_id)
            )
        ).one()


def test_missing_qualification_quiet_hours_and_email_flag_do_not_grant_sms(monkeypatch):
    _, options, _ = _ready(monkeypatch)
    for changes in (
        {"approval_verifier": None},
        {"consent_verifier": None},
        {
            "settings": replace(
                options["settings"], sms_send_enabled=False, email_send_enabled=True
            )
        },
    ):
        with pytest.raises(VisitPrepSMSUnavailable):
            outbox.enqueue_sms(**(options | changes))
    hour = datetime.now(UTC).hour
    monkeypatch.setattr(
        outbox,
        "current_standard_cadence",
        lambda: VisitPrepCadenceSettings(
            "synthetic-quiet-now", hour, (hour + 1) % 24, 4
        ),
    )
    wait = outbox.enqueue_sms(**options)
    assert wait.disposition == "wait" and wait.delivery_id is None


def test_late_qualification_rejection_rolls_back_outbox_and_audit(monkeypatch):
    _, options, _ = _ready(monkeypatch)
    calls = []

    def verifier(*args, **kwargs):
        calls.append(1)
        return len(calls) == 1

    with pytest.raises(VisitPrepSMSUnavailable):
        outbox.enqueue_sms(**(options | {"approval_verifier": verifier}))
    with Session(get_engine()) as session:
        assert (
            session.exec(
                select(VisitPrepSMSDelivery).where(
                    VisitPrepSMSDelivery.operation_id == options["operation_id"]
                )
            ).all()
            == []
        )
        assert (
            session.execute(
                text(
                    "SELECT count(*) FROM authority_audit_event WHERE client_event_id=:op"
                ),
                {"op": options["operation_id"]},
            ).scalar_one()
            == 0
        )


def test_queue_replay_private_evidence_and_actual_metadata_history(monkeypatch):
    fixture, options, _ = _ready(monkeypatch)
    receipt = outbox.enqueue_sms(**options)
    assert receipt.disposition == "queued"
    assert outbox.enqueue_sms(**options).delivery_id == receipt.delivery_id
    with pytest.raises(VisitPrepSMSUnavailable):
        outbox.enqueue_sms(**(options | {"operation_id": str(uuid4())}))
    row = _row(receipt)
    retained = decrypt_value(row.evidence_ciphertext)
    assert all(
        value not in retained
        for value in (
            fixture.access_token,
            "+12025550123",
            "José",
            "patient@example.com",
        )
    )
    assert (
        "payload_ciphertext" not in row.model_dump()
        and "evidence_ciphertext" not in row.model_dump()
    )
    with Session(get_engine()) as session:
        outbox.verify_sms_evidence(session, row)
        plan = outbox.plan_locked_current_delivery(
            session,
            **outbox._scope(options["approval"], options["settings"]),
            visit_prep_id=fixture.prep_id,
            quiet_hours=outbox.current_standard_cadence().quiet_hours,
            minimum_channel_spacing=timedelta(hours=4),
        )
        assert len(plan.sms_history) == 1 and plan.sms_history[0].state == "queued"
    for statement in (
        "UPDATE visit_prep_sms_delivery SET step='unfinished_12h_sms' WHERE operation_id=:op",
        "DELETE FROM visit_prep_sms_delivery WHERE operation_id=:op",
        "TRUNCATE visit_prep_sms_delivery",
    ):
        with Session(get_engine()) as session, pytest.raises(DBAPIError):
            session.execute(text(statement), {"op": options["operation_id"]})
            session.commit()
