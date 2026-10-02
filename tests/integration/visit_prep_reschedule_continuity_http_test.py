"""One patient affirmation survives rescheduling; old links alone stay denied."""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from app.services import visit_prep_interviews as interviews
from app.services.visit_prep_patient_continuation import COOKIE_NAME, open_confirmation
from app.services.visit_preps import (
    reschedule_visit_prep,
    revoke_visit_prep,
    rotate_visit_prep_credential,
)

from activemodel.session_manager import get_session
from app.models.visit_prep import VisitPrep, VisitPrepScheduleGeneration
from sqlalchemy import DateTime, literal
from sqlmodel import col, select

from tests.integration.visit_prep_booking_lifecycle_ingress_test import _changed
from tests.integration.visit_prep_completed_booking_reschedule_test import _completed
from tests.integration.visit_prep_contribution_http_test import _headers
from tests.integration.visit_prep_patient_continuation_http_test import (
    CONTINUED,
    PREFIX,
    _client,
    _confirmed,
)
from tests.services.visit_prep_contribution_projection_test import _prepared


def test_actual_signed_booking_change_preserves_cookie_only_patient_access(
    monkeypatch, owned_rate_limit_redis
):
    fixture, _, _, settings, source, booking_client = _completed(monkeypatch)
    patient = _client(monkeypatch, owned_rate_limit_redis)
    confirmed = patient.post(
        PREFIX + "/confirm",
        headers=_headers(fixture) | CONTINUED,
        json={"first_name": "José", "role": "patient"},
    )
    assert confirmed.status_code == 200
    source["datetime"] = (
        datetime.fromisoformat(source["datetime"]) + timedelta(days=1)
    ).isoformat()
    assert _changed(booking_client, settings).status_code == 200
    assert patient.get(PREFIX, headers=_headers(fixture) | CONTINUED).status_code == 200
    read = patient.post(
        PREFIX + "/contribution", headers=CONTINUED, json={"role": "patient"}
    )
    assert read.status_code == 200
    assert read.json()["topics"][-1]["initial_answer"] == "My invented patient story."


def _move(fixture, *, days=10, generation=1):
    with get_session() as session:
        schedule = session.exec(
            select(VisitPrepScheduleGeneration)
            .where(VisitPrepScheduleGeneration.visit_prep_id == fixture.prep_id)
            .order_by(col(VisitPrepScheduleGeneration.generation).desc())
        ).first()
        assert schedule is not None
    return reschedule_visit_prep(
        owner_practice_member_id=fixture.owner.id,
        owner_doctor_id=fixture.doctor.id,
        practice_id=fixture.practice.id,
        visit_prep_id=fixture.prep_id,
        expected_schedule_generation=generation,
        expected_credential_generation=generation,
        scheduled_at=schedule.scheduled_at + timedelta(days=days),
        timezone_name=schedule.timezone_name,
    )


@pytest.mark.parametrize("role", ["patient", "caregiver"])
@pytest.mark.parametrize("complete", [False, True])
def test_original_cookie_follows_multiple_reschedules_then_current_link_and_edits(
    monkeypatch, owned_rate_limit_redis, role, complete
):
    fixture, client = _confirmed(
        monkeypatch, owned_rate_limit_redis, role=role, complete=complete
    )
    original = open_confirmation(client.cookies.get(COOKIE_NAME))
    first = _move(fixture)
    second = _move(fixture, generation=2)
    with get_session() as session:
        prep = session.get(VisitPrep, fixture.prep_id)
        assert prep.confirmed_at == original.confirmed_at
        assert prep.confirmed_by_role == role
    # Only a cryptographic remembered confirmation follows the retired link.
    stranger = _client(monkeypatch, owned_rate_limit_redis)
    assert (
        stranger.get(PREFIX, headers=_headers(fixture) | CONTINUED).status_code == 404
    )
    assert (
        stranger.get(
            PREFIX,
            headers={"Authorization": "Bearer " + first.access_token} | CONTINUED,
        ).status_code
        == 404
    )
    for header in (
        _headers(fixture) | CONTINUED,
        {"Authorization": "Bearer " + second.access_token} | CONTINUED,
        CONTINUED,
    ):
        resumed = client.get(PREFIX, headers=header)
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["continued_role"] == role
        assert resumed.json()["status"] != "confirmation_required"
        renewed = open_confirmation(client.cookies.get(COOKIE_NAME))
        assert renewed.confirmed_at == original.confirmed_at
        assert renewed.token_hash != original.token_hash
        assert renewed.expires_at > original.expires_at
    if not complete:
        saved = client.post(
            PREFIX + "/responses",
            headers=CONTINUED | {"Idempotency-Key": str(uuid4())},
            json={"question_key": "medications", "response_kind": "skipped"},
        )
        assert saved.status_code == 200, saved.text
        return
    read = client.post(PREFIX + "/contribution", headers=CONTINUED, json={"role": role})
    assert read.status_code == 200
    changed = client.post(
        PREFIX + "/contribution/response",
        headers=CONTINUED | {"Idempotency-Key": str(uuid4())},
        json={
            "role": role,
            "basis": read.json()["basis"],
            "question_key": "story",
            "response_kind": "answered",
            "answer": "My invented rescheduled update.",
        },
    )
    assert changed.status_code == 200, changed.text
    final = client.post(
        PREFIX + "/contribution", headers=CONTINUED, json={"role": role}
    )
    assert final.status_code == 200
    assert final.json()["topics"][-1]["initial_answer"] == "My invented health story."
    assert final.json()["topics"][-1]["answer"] == "My invented rescheduled update."


def test_renewed_cookie_reopens_original_link_after_original_expiry(
    monkeypatch, owned_rate_limit_redis
):
    fixture, client = _confirmed(monkeypatch, owned_rate_limit_redis)
    original = open_confirmation(client.cookies.get(COOKIE_NAME))
    _move(fixture)
    assert client.get(PREFIX, headers=CONTINUED).status_code == 200
    future = original.expires_at + timedelta(hours=1)
    monkeypatch.setattr(
        interviews.func,
        "clock_timestamp",
        lambda: literal(future, type_=DateTime(timezone=True)),
    )
    resumed = client.get(PREFIX, headers=_headers(fixture) | CONTINUED)
    assert resumed.status_code == 200 and resumed.json()["continued_role"] == "patient"
    assert (
        client.post(
            PREFIX + "/contribution", headers=CONTINUED, json={"role": "patient"}
        ).status_code
        == 200
    )


@pytest.mark.parametrize("fault", ["rotation", "cancelled", "recipient", "quarantine"])
def test_remembered_reschedule_never_crosses_a_revocation_boundary(
    monkeypatch, owned_rate_limit_redis, fault
):
    fixture, client = _confirmed(
        monkeypatch, owned_rate_limit_redis, complete=fault != "rotation"
    )
    _move(fixture)
    assert client.get(PREFIX, headers=CONTINUED).status_code == 200
    if fault == "rotation":
        rotate_visit_prep_credential(
            owner_practice_member_id=fixture.owner.id,
            owner_doctor_id=fixture.doctor.id,
            practice_id=fixture.practice.id,
            visit_prep_id=fixture.prep_id,
            expected_credential_generation=2,
        )
    elif fault == "quarantine":
        assert (
            client.post(PREFIX + "/wrong-recipient", headers=CONTINUED).status_code
            == 200
        )
        assert (
            client.post(PREFIX + "/wrong-recipient", headers=CONTINUED).status_code
            == 200
        )
    else:
        revoke_visit_prep(
            owner_practice_member_id=fixture.owner.id,
            owner_doctor_id=fixture.doctor.id,
            practice_id=fixture.practice.id,
            visit_prep_id=fixture.prep_id,
            expected_credential_generation=2,
            reason="appointment_cancelled"
            if fault == "cancelled"
            else "physician_revoked",
        )
    assert client.get(PREFIX, headers=CONTINUED).status_code == 404
    denied = client.post(
        PREFIX + "/contribution", headers=CONTINUED, json={"role": "patient"}
    )
    assert denied.status_code == 404 and "invented health story" not in denied.text


def test_old_cookie_and_other_preparation_link_cannot_select_saved_content(
    monkeypatch, owned_rate_limit_redis
):
    fixture, client = _confirmed(monkeypatch, owned_rate_limit_redis)
    _move(fixture)
    other = _prepared(monkeypatch, "rescheduled-other")
    resumed = client.get(PREFIX, headers=_headers(other) | CONTINUED)
    assert (
        resumed.status_code == 200
        and resumed.json()["status"] == "confirmation_required"
    )
    assert (
        client.post(
            PREFIX + "/contribution",
            headers=_headers(other) | CONTINUED,
            json={"role": "patient"},
        ).status_code
        == 404
    )


def test_unrenewed_expired_proof_cannot_recover_from_old_link_alone(
    monkeypatch, owned_rate_limit_redis
):
    fixture, client = _confirmed(monkeypatch, owned_rate_limit_redis)
    proof = open_confirmation(client.cookies.get(COOKIE_NAME))
    _move(fixture)
    future = proof.expires_at + timedelta(hours=1)
    monkeypatch.setattr(
        interviews.func,
        "clock_timestamp",
        lambda: literal(future, type_=DateTime(timezone=True)),
    )
    assert client.get(PREFIX, headers=_headers(fixture) | CONTINUED).status_code == 404
    assert client.get(PREFIX, headers=CONTINUED).status_code == 404


def test_valid_proof_cannot_skip_explicit_rotation_before_reschedule(
    monkeypatch, owned_rate_limit_redis
):
    fixture, client = _confirmed(monkeypatch, owned_rate_limit_redis, complete=False)
    rotate_visit_prep_credential(
        owner_practice_member_id=fixture.owner.id,
        owner_doctor_id=fixture.doctor.id,
        practice_id=fixture.practice.id,
        visit_prep_id=fixture.prep_id,
        expected_credential_generation=1,
    )
    with get_session() as session:
        schedule = session.exec(
            select(VisitPrepScheduleGeneration).where(
                VisitPrepScheduleGeneration.visit_prep_id == fixture.prep_id
            )
        ).one()
    moved = reschedule_visit_prep(
        owner_practice_member_id=fixture.owner.id,
        owner_doctor_id=fixture.doctor.id,
        practice_id=fixture.practice.id,
        visit_prep_id=fixture.prep_id,
        expected_schedule_generation=1,
        expected_credential_generation=2,
        scheduled_at=schedule.scheduled_at + timedelta(days=1),
        timezone_name=schedule.timezone_name,
    )
    assert client.get(PREFIX, headers=CONTINUED).status_code == 404
    assert (
        client.get(
            PREFIX,
            headers={"Authorization": "Bearer " + moved.access_token} | CONTINUED,
        ).status_code
        == 404
    )
