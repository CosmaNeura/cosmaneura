"""Real owner-only metadata reads; no private decryption or sending effect."""

from dataclasses import asdict, replace
from uuid import uuid4

import pytest

from app.services import visit_prep_sms_outbox as outbox
from app.services import visit_prep_sms_status as status
from app.services import visit_prep_sms_worker as worker
from app.services.visit_prep_sms_consent import (
    PatientSMSConsentInput,
    get_patient_sms_consent_view,
    record_patient_sms_consent,
)
from app.services.visit_prep_sms_contract import VisitPrepSMSUnavailable
from app.services.visit_preps import rotate_visit_prep_credential

from activemodel.session_manager import get_engine
from sqlalchemy import event, text
from sqlmodel import Session

from tests.integration.visit_prep_sms_outbox_test import (
    _finish_queue_only_fixtures,  # noqa: F401 -- owned fixture cleanup only
    _ready,
    _row,
)
from tests.integration.visit_prep_sms_worker_test import _worker


def _read(fixture, options, **changes):
    return status.get_sms_status(
        **(
            dict(
                owner_practice_member_id=fixture.owner.id,
                owner_doctor_id=fixture.doctor.id,
                practice_id=fixture.practice.id,
                expected_clerk_subject=fixture.doctor.clerk_id,
                visit_prep_id=fixture.prep_id,
                settings=options["settings"],
            )
            | changes
        )
    )


def _audits(fixture):
    with Session(get_engine()) as session:
        return session.execute(
            text(
                "SELECT count(*) FROM authority_audit_event WHERE event_type='visit_prep.sms_status_read' AND resource_ref=:prep"
            ),
            {"prep": str(fixture.prep_id)},
        ).scalar_one()


def test_missing_steps_are_explicit_not_permission_and_do_not_create_intents(
    monkeypatch,
):
    fixture, options, _ = _ready(monkeypatch)
    result = _read(fixture, options)
    assert [v.step for v in result.messages] == [
        "booking_sms",
        "unopened_48h_sms",
        "unfinished_12h_sms",
    ]
    assert all(
        v.status == "not_requested"
        and all(x is None for k, x in asdict(v).items() if k not in {"step", "status"})
        for v in result.messages
    )
    assert _audits(fixture) == 1
    with Session(get_engine()) as session:
        assert (
            session.execute(
                text(
                    "SELECT count(*) FROM visit_prep_sms_delivery WHERE visit_prep_id=:prep"
                ),
                {"prep": fixture.prep_id.uuid},
            ).scalar_one()
            == 0
        )


@pytest.mark.parametrize(
    "state,code",
    [
        ("queued", None),
        ("submitting", None),
        ("accepted", None),
        ("failed", "transport_unavailable"),
        ("suppressed", "scope_unavailable"),
        ("uncertain", "submission_uncertain"),
    ],
)
def test_actual_status_no_private_columns_no_effect_and_no_channel_gate(
    monkeypatch, state, code
):
    fixture, options, _ = _ready(monkeypatch)
    receipt = outbox.enqueue_sms(**options)
    if state in {"submitting", "accepted", "uncertain"}:
        assert isinstance(
            worker.claim_sms_invitation(**_worker(options)), worker.SMSSubmissionClaim
        )
    if state not in {"queued", "submitting"}:
        with Session(get_engine()) as session:
            session.execute(
                text(
                    "UPDATE visit_prep_sms_delivery SET state=:state,outcome_code=:code,payload_ciphertext=NULL,lease_until=NULL,finalized_at=clock_timestamp() WHERE id=:id"
                ),
                {"state": state, "code": code, "id": _row(receipt).id.uuid},
            )
            session.commit()
    before = _row(receipt).model_dump()
    statements = []

    def observe(conn, cursor, statement, parameters, context, executemany):
        if "visit_prep_sms_delivery" in statement:
            statements.append(statement)

    event.listen(get_engine(), "before_cursor_execute", observe)
    try:
        result = _read(
            fixture,
            options,
            settings=replace(
                options["settings"],
                public_api_enabled=False,
                sms_send_enabled=False,
                sms_consent_capture_enabled=False,
            ),
        )
    finally:
        event.remove(get_engine(), "before_cursor_execute", observe)
    assert statements and all(
        "ciphertext" not in s and "digest" not in s and "consent_event_id" not in s
        for s in statements
    )
    value = result.messages[0]
    assert (
        value.status == state
        and value.outcome_code == code
        and value.context_current is True
    )
    assert value.submission_window_elapsed is (False if state == "submitting" else None)
    assert _row(receipt).model_dump() == before and _audits(fixture) == 1
    assert all(
        secret not in str(asdict(result))
        for secret in (
            fixture.access_token,
            "+12025550123",
            "José",
            str(options["operation_id"]),
        )
    )
    if state == "submitting":
        with Session(get_engine()) as session:
            session.execute(
                text(
                    "UPDATE visit_prep_sms_delivery SET state='uncertain',outcome_code='submission_uncertain',payload_ciphertext=NULL,lease_until=NULL,finalized_at=clock_timestamp() WHERE id=:id"
                ),
                {"id": _row(receipt).id.uuid},
            )
            session.commit()


def test_rotation_retains_old_step_without_cleanup_and_new_context_is_explicit(
    monkeypatch,
):
    fixture, options, _ = _ready(monkeypatch)
    receipt = outbox.enqueue_sms(**options)
    rotate_visit_prep_credential(
        owner_practice_member_id=fixture.owner.id,
        owner_doctor_id=fixture.doctor.id,
        practice_id=fixture.practice.id,
        visit_prep_id=fixture.prep_id,
        expected_credential_generation=1,
    )
    value = _read(fixture, options)
    assert value.current_credential_generation == 2
    assert (
        value.messages[0].credential_generation == 1
        and value.messages[0].context_current is False
    )
    assert (
        _row(receipt).state == "queued" and _row(receipt).payload_ciphertext is not None
    )


def test_latest_step_selects_new_generation_without_rewriting_old_intent(monkeypatch):
    fixture, options, _ = _ready(monkeypatch)
    old = outbox.enqueue_sms(**options)
    renewed = rotate_visit_prep_credential(
        owner_practice_member_id=fixture.owner.id,
        owner_doctor_id=fixture.doctor.id,
        practice_id=fixture.practice.id,
        visit_prep_id=fixture.prep_id,
        expected_credential_generation=1,
    )
    view = get_patient_sms_consent_view(
        access_token=renewed.access_token, first_name="José", role="patient"
    )
    record_patient_sms_consent(
        access_token=renewed.access_token,
        first_name="José",
        decision=PatientSMSConsentInput(
            str(uuid4()),
            1,
            "granted",
            "patient",
            view.grant_disclosure_fingerprint,
            view.maximum_expires_at,
            True,
            False,
        ),
    )
    with Session(get_engine()) as session:
        from app.models.visit_prep import VisitPrepCredentialGeneration
        from app.models.visit_prep_sms_consent import VisitPrepSMSConsentEvent
        from sqlmodel import select

        credential = session.exec(
            select(VisitPrepCredentialGeneration).where(
                VisitPrepCredentialGeneration.visit_prep_id == fixture.prep_id,
                VisitPrepCredentialGeneration.generation == 2,
            )
        ).one()
        consent_id = str(
            session.exec(
                select(VisitPrepSMSConsentEvent).where(
                    VisitPrepSMSConsentEvent.visit_prep_id == fixture.prep_id,
                    VisitPrepSMSConsentEvent.event_sequence == 2,
                )
            )
            .one()
            .id
        )
    fresh = outbox.enqueue_sms(
        **(
            options
            | {
                "credential_id": credential.id,
                "access_token": renewed.access_token,
                "operation_id": str(uuid4()),
                "consent_verifier": lambda s, **kw: kw["consent_event_id"]
                == consent_id,
            }
        )
    )
    value = _read(fixture, options).messages[0]
    assert (
        fresh.disposition == "queued"
        and value.credential_generation == 2
        and value.context_current is True
    )
    assert _row(old).state == "queued" and _row(fresh).state == "queued"


@pytest.mark.parametrize("change", ["subject", "support", "baa", "disabled", "owner"])
def test_current_owner_denial_has_no_audit_or_private_error(monkeypatch, change):
    fixture, options, _ = _ready(monkeypatch)
    changes = {}
    if change == "subject":
        changes["expected_clerk_subject"] = "wrong-subject"
    elif change == "support":
        changes["view_as_active"] = True
    elif change == "disabled":
        changes["settings"] = replace(options["settings"], enabled=False)
    elif change == "owner":
        changes["visit_prep_id"] = _ready(monkeypatch)[0].prep_id
    else:
        fixture.practice.negotiated_baa_on_file = False
        fixture.practice.save()
    with pytest.raises(
        VisitPrepSMSUnavailable, match="^Visit Prep SMS is unavailable.$"
    ):
        _read(fixture, options, **changes)
    assert _audits(fixture) == 0


def test_late_authority_denial_rolls_back_read_audit(monkeypatch):
    fixture, options, _ = _ready(monkeypatch)
    authorize = status._authorize_owner
    calls = []

    def reject_late(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise VisitPrepSMSUnavailable()
        return authorize(*args, **kwargs)

    monkeypatch.setattr(status, "_authorize_owner", reject_late)
    with pytest.raises(VisitPrepSMSUnavailable):
        _read(fixture, options)
    assert len(calls) == 2 and _audits(fixture) == 0
