"""Real PostgreSQL checks for the dormant preparation aggregate."""

import json
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.utils.encryption import encrypt_value

from activemodel.session_manager import get_session
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.practice import Practice, PracticeMember
from app.models.visit_prep import (
    VisitPrep,
    VisitPrepCredentialGeneration,
    VisitPrepLegalHold,
    VisitPrepScheduleGeneration,
)
from app.models.visit_prep_question_set import (
    VisitPrepQuestionSet,
    VisitPrepQuestionSetVersion,
)
from sqlalchemy import delete, inspect, select, update
from sqlalchemy.exc import DBAPIError
from sqlmodel import SQLModel

from tests.factories import save_practice_member


def _prep(*, published=True, created=None):
    nonce = secrets.token_hex(6)
    doctor = Doctor(
        clerk_id=f"user_vp_aggregate_{nonce}",
        email=f"vp-aggregate-{nonce}@example.test",
        biography="Synthetic Visit Prep model test account.",
    ).save()
    practice = Practice(
        legal_name=f"Synthetic VP {nonce}",
        join_code=nonce[:8].upper(),
        email_domain="example.test",
    ).save()
    member = save_practice_member(
        PracticeMember(
            practice_id=practice.id,
            doctor_id=doctor.id,
            role="owner",
        )
    )
    question_set = VisitPrepQuestionSet(owner_practice_member_id=member.id).save()
    version = VisitPrepQuestionSetVersion(
        question_set_id=question_set.id,
        owner_practice_member_id=member.id,
        version_number=1,
        name="Synthetic questions",
        published_at=datetime.now(UTC) + timedelta(minutes=1) if published else None,
    ).save()
    result = VisitPrep(
        practice_id=practice.id,
        owner_practice_member_id=member.id,
        owner_doctor_id=doctor.id,
        question_set_version_id=version.id,
        creation_source="physician_diy",
        creation_idempotency_key=str(uuid4()),
        creation_payload_hmac_sha256="a" * 64,
        recipient_snapshot_ciphertext=encrypt_value(
            json.dumps(
                {
                    "first_name": "SyntheticSecret",
                    "email": "synthetic@example.test",
                }
            )
        ),
        has_email_contact=True,
        physician_display_name="Synthetic Physician",
        practice_display_name="Synthetic Practice",
    )
    result.created_at = created or datetime.now(UTC) - timedelta(minutes=1)
    result.updated_at = result.created_at
    return result


def _schedule(prep, when, generation=1):
    return VisitPrepScheduleGeneration(
        visit_prep_id=prep.id,
        owner_practice_member_id=prep.owner_practice_member_id,
        generation=generation,
        scheduled_at=when,
        timezone_name="America/New_York",
        unused_purge_at=when + timedelta(hours=720),
        unused_hard_purge_at=when + timedelta(hours=2160),
    )


def _credential(prep, schedule, generation=1, issued=None):
    return VisitPrepCredentialGeneration(
        visit_prep_id=prep.id,
        owner_practice_member_id=prep.owner_practice_member_id,
        schedule_generation_id=schedule.id,
        generation=generation,
        token_hash=secrets.token_hex(32),
        issued_at=issued or datetime.now(UTC),
        expires_at=schedule.scheduled_at + timedelta(hours=72),
    )


def _insert_rejected(row, message):
    with get_session() as session, pytest.raises(DBAPIError, match=message):
        with session.begin_nested():
            session.add(row)
            session.flush()


def _change(row, **values):
    with get_session() as session:
        session.execute(
            update(type(row)).where(type(row).id == row.id).values(**values)
        )
        session.commit()


def _change_rejected(row, message, **values):
    with get_session() as session, pytest.raises(DBAPIError, match=message):
        with session.begin_nested():
            session.execute(
                update(type(row)).where(type(row).id == row.id).values(**values)
            )


def _delete_rejected(row, message):
    with get_session() as session, pytest.raises(DBAPIError, match=message):
        with session.begin_nested():
            session.execute(delete(type(row)).where(type(row).id == row.id))


def test_new_patient_does_not_require_a_chart_and_sensitive_fields_are_not_serialized():
    prep = _prep().save()
    assert prep.patient_id is None and prep.appointment_id is None
    assert "SyntheticSecret" not in prep.recipient_snapshot_ciphertext
    assert "recipient_snapshot_ciphertext" not in prep.model_dump()
    assert "creation_payload_hmac_sha256" not in prep.model_dump()
    schedule = _schedule(prep, datetime.now(UTC) + timedelta(days=14)).save()
    credential = _credential(prep, schedule).save()
    assert "token_hash" not in credential.model_dump()


def test_draft_question_version_and_cross_tenant_links_are_rejected():
    _insert_rejected(
        _prep(published=False), "requires a published owned question version"
    )
    prep, other = _prep(), _prep()
    prep.practice_id = other.practice_id
    _insert_rejected(prep, "fk_visit_prep_owner_scope")
    prep = _prep()
    patient = Patient(doctor_id=other.owner_doctor_id, name="Synthetic Patient").save()
    prep.patient_id = patient.id
    prep.patient_linked_at = datetime.now(UTC)
    prep.patient_linked_by_ref = "actor:synthetic"
    _insert_rejected(prep, "fk_visit_prep_patient_owner")
    prep = _prep()
    first = Patient(doctor_id=prep.owner_doctor_id, name="Synthetic First").save()
    second = Patient(doctor_id=prep.owner_doctor_id, name="Synthetic Second").save()
    appointment = Appointment(
        doctor_id=prep.owner_doctor_id,
        patient_id=second.id,
        created_by_system="visit_prep_test",
        external_id=None,
        appointment_date=datetime.now(UTC) + timedelta(days=14),
        appointment_name="Synthetic appointment",
        appointment_notes_preview=None,
    ).save()
    prep.patient_id, prep.appointment_id = first.id, appointment.id
    prep.patient_linked_at = prep.appointment_linked_at = datetime.now(UTC)
    prep.patient_linked_by_ref = prep.appointment_linked_by_ref = "actor:synthetic"
    _insert_rejected(prep, "fk_visit_prep_appointment_owner_patient")
    prep.patient_id = second.id
    assert prep.save().appointment_id == appointment.id


def test_creation_snapshot_and_schedule_are_immutable_and_generations_are_sequential():
    prep = _prep().save()
    when = datetime.now(UTC) + timedelta(days=14)
    _insert_rejected(_schedule(prep, when, 2), "schedule generation is not sequential")
    schedule = _schedule(prep, when).save()
    _schedule(prep, when + timedelta(days=1), 2).save()
    _change_rejected(
        prep, "creation snapshots are immutable", creation_source="booking"
    )
    _change_rejected(
        schedule, "schedule generations are immutable", timezone_name="UTC"
    )
    _delete_rejected(schedule, "deleted through aggregate cleanup")
    _delete_rejected(prep, "not yet eligible for deletion")


def test_credential_rotation_requires_current_schedule_exact_expiry_and_revocation():
    prep = _prep().save()
    when = datetime.now(UTC) + timedelta(days=14)
    first_schedule = _schedule(prep, when).save()
    first = _credential(prep, first_schedule).save()
    second_schedule = _schedule(prep, when + timedelta(days=1), 2).save()
    _insert_rejected(
        _credential(prep, first_schedule, 2), "requires the current schedule"
    )
    bad_expiry = _credential(prep, second_schedule, 2)
    bad_expiry.expires_at += timedelta(seconds=1)
    _insert_rejected(bad_expiry, "expiry must be schedule plus 72 hours")
    _insert_rejected(
        _credential(prep, second_schedule, 2), "uq_visit_prep_active_credential"
    )
    _change(
        first,
        revoked_at=datetime.now(UTC),
        revoked_by_ref="actor:synthetic",
        revocation_reason="rescheduled",
    )
    second = _credential(prep, second_schedule, 2).save()
    _change_rejected(
        first,
        "revocation is irreversible",
        revoked_at=None,
        revoked_by_ref=None,
        revocation_reason=None,
    )
    _change_rejected(second, "credential lineage is immutable", token_hash="0" * 64)


def test_confirmation_pins_exact_utc_calendar_retention_including_leap_year():
    prep = _prep(created=datetime(2028, 1, 1, tzinfo=UTC)).save()
    when = datetime(2028, 2, 29, 12, tzinfo=UTC)
    _schedule(prep, when).save()
    retention = datetime(2035, 2, 28, 12, tzinfo=UTC)
    confirmation = dict(
        lifecycle_state="confirmed",
        confirmed_at=datetime(2028, 1, 2, tzinfo=UTC),
        confirmed_by_role="patient",
        retention_policy_version="clinical.v1",
        retain_until=retention,
    )
    _change_rejected(
        prep,
        "seven calendar years",
        **(confirmation | {"retain_until": retention + timedelta(days=1)}),
    )
    _change(prep, **confirmation)
    _schedule(prep, when + timedelta(days=1), 2).save()
    _change_rejected(
        prep,
        "retention snapshot is immutable",
        retain_until=retention + timedelta(days=1),
    )
    _change_rejected(
        prep,
        "invalid Visit Prep lifecycle transition",
        lifecycle_state="invited",
        confirmed_at=None,
        confirmed_by_role=None,
    )
    _delete_rejected(prep, "clinical Visit Prep is still retained")


def test_revoked_preparation_cannot_advance_or_be_reactivated():
    prep = _prep().save()
    when = datetime.now(UTC) + timedelta(days=14)
    _schedule(prep, when).save()
    _change(
        prep,
        revoked_at=datetime.now(UTC),
        revoked_by_ref="actor:synthetic",
        revocation_reason="appointment_cancelled",
    )
    _change_rejected(
        prep,
        "revoked Visit Prep cannot advance",
        lifecycle_state="confirmed",
        confirmed_at=datetime.now(UTC),
        confirmed_by_role="patient",
        retention_policy_version="clinical.v1",
        retain_until=when.replace(year=when.year + 7),
    )
    _change_rejected(
        prep,
        "revocation is irreversible",
        revoked_at=None,
        revoked_by_ref=None,
        revocation_reason=None,
    )
    _insert_rejected(
        _schedule(prep, when + timedelta(days=1), 2),
        "inactive Visit Prep cannot be rescheduled",
    )


def test_active_legal_hold_blocks_cleanup_and_release_is_irreversible():
    now = datetime.now(UTC)
    prep = _prep(created=now - timedelta(days=40)).save()
    schedule = _schedule(prep, now - timedelta(days=31)).save()
    credential = _credential(prep, schedule, issued=now - timedelta(days=40)).save()
    hold = VisitPrepLegalHold(
        visit_prep_id=prep.id,
        owner_practice_member_id=prep.owner_practice_member_id,
        generation=1,
        placed_at=now - timedelta(days=20),
        placed_by_ref="actor:synthetic",
        placement_reason="synthetic_legal_review",
    ).save()
    _delete_rejected(prep, "active legal hold cannot be deleted")
    _delete_rejected(hold, "deleted through aggregate cleanup")
    _change_rejected(
        hold,
        "release cannot be future-dated",
        released_at=now + timedelta(days=1),
        released_by_ref="actor:synthetic",
        release_reason="review_complete",
    )
    _change(
        hold,
        released_at=now - timedelta(days=19),
        released_by_ref="actor:synthetic",
        release_reason="review_complete",
    )
    _change_rejected(
        hold,
        "release is irreversible",
        released_at=None,
        released_by_ref=None,
        release_reason=None,
    )
    with get_session() as session:
        prep_table = SQLModel.metadata.tables["visit_prep"]
        session.execute(delete(prep_table).where(prep_table.c.id == prep.id))
        session.commit()
        for row in (prep, schedule, credential, hold):
            table = SQLModel.metadata.tables[str(type(row).__tablename__)]
            assert (
                session.scalar(select(table.c.id).where(table.c.id == row.id)) is None
            )


def test_wrong_recipient_requires_review_and_is_terminal():
    prep = _prep().save()
    now = datetime.now(UTC)
    when = now + timedelta(days=14)
    _schedule(prep, when).save()
    _change(
        prep,
        lifecycle_state="confirmed",
        confirmed_at=now,
        confirmed_by_role="caregiver",
        retention_policy_version="clinical.v1",
        retain_until=when.replace(year=when.year + 7),
    )
    quarantine = dict(
        lifecycle_state="misdirected",
        misdirected_at=now + timedelta(seconds=1),
        misdirected_reason="wrong_recipient_interaction",
        misdirected_incident_ref="incident:synthetic-001",
        revoked_at=now + timedelta(seconds=1),
        revoked_by_ref="actor:synthetic",
        revocation_reason="wrong_recipient",
    )
    _change_rejected(prep, "quarantine requires pending review first", **quarantine)
    _change(prep, **quarantine, misdirected_disposition="pending_review")
    _delete_rejected(prep, "quarantined Visit Prep is not eligible for deletion")
    _change_rejected(
        prep, "quarantine evidence is immutable", misdirected_reason="other"
    )
    _change_rejected(
        prep,
        "invalid Visit Prep lifecycle transition",
        lifecycle_state="confirmed",
        misdirected_at=None,
    )


def test_database_columns_match_the_real_aggregate_model():
    with get_session() as session:
        database = inspect(session.connection())
        for name in (
            "visit_prep",
            "visit_prep_schedule_generation",
            "visit_prep_credential_generation",
            "visit_prep_legal_hold",
        ):
            assert {column["name"] for column in database.get_columns(name)} == set(
                SQLModel.metadata.tables[name].columns.keys()
            )
