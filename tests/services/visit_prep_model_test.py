"""Database invariants for the dormant Visit Prep aggregate schema."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
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
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlmodel import SQLModel


@dataclass(frozen=True)
class _Scope:
    doctor: Doctor
    practice: Practice
    owner: PracticeMember
    question_version: VisitPrepQuestionSetVersion


def _scope(label: str, *, published: bool = True) -> _Scope:
    nonce = secrets.token_hex(6)
    doctor = Doctor(
        clerk_id=f"user_visit_prep_aggregate_{label}_{nonce}",
        email=f"visit-prep-aggregate-{label}-{nonce}@example.test",
        biography="Synthetic Visit Prep aggregate test account.",
    ).save()
    practice = Practice(
        legal_name=f"Synthetic Visit Prep Aggregate Practice {nonce}",
        join_code=f"A{secrets.token_hex(4).upper()[:7]}",
        email_domain="example.test",
    ).save()
    owner = PracticeMember(
        practice_id=practice.id,
        doctor_id=doctor.id,
        role="owner",
    ).save()
    question_set = VisitPrepQuestionSet(
        owner_practice_member_id=owner.id,
        seed_key=f"aggregate.{label}"[:64],
    ).save()
    question_version = VisitPrepQuestionSetVersion(
        question_set_id=question_set.id,
        owner_practice_member_id=owner.id,
        version_number=1,
        name=f"Synthetic aggregate questions {label}"[:120],
        published_at=(datetime.now(UTC) + timedelta(minutes=5)) if published else None,
    ).save()
    return _Scope(doctor, practice, owner, question_version)


def _patient(scope: _Scope, label: str) -> Patient:
    return Patient(
        doctor_id=scope.doctor.id,
        name=f"Synthetic Patient {label}",
        date_of_birth=datetime(1980, 1, 1, tzinfo=UTC),
    ).save()


def _appointment(scope: _Scope, patient: Patient, label: str) -> Appointment:
    return Appointment(
        doctor_id=scope.doctor.id,
        patient_id=patient.id,
        created_by_system="visit_prep_test",
        external_id=None,
        appointment_date=datetime.now(UTC) + timedelta(days=14),
        appointment_name=f"Synthetic appointment {label}",
        appointment_notes_preview=None,
    ).save()


def _new_prep(
    scope: _Scope,
    *,
    patient: Patient | None = None,
    appointment: Appointment | None = None,
    created_at: datetime | None = None,
    recipient_plaintext: str | None = None,
) -> VisitPrep:
    created = created_at or datetime.now(UTC) - timedelta(minutes=1)
    linked_at = created + timedelta(seconds=1)
    recipient = recipient_plaintext or json.dumps(
        {
            "first_name": "Synthetic",
            "email": "synthetic-patient@example.test",
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    prep = VisitPrep(
        practice_id=scope.practice.id,
        owner_practice_member_id=scope.owner.id,
        owner_doctor_id=scope.doctor.id,
        question_set_version_id=scope.question_version.id,
        patient_id=patient.id if patient is not None else None,
        appointment_id=appointment.id if appointment is not None else None,
        creation_source="physician_diy",
        creation_idempotency_key=str(uuid4()),
        creation_payload_hmac_sha256="a" * 64,
        recipient_snapshot_ciphertext=encrypt_value(recipient),
        recipient_snapshot_format_version=1,
        has_email_contact=True,
        has_sms_contact=False,
        physician_display_name="Synthetic Physician",
        practice_display_name="Synthetic Practice",
        patient_linked_at=linked_at if patient is not None else None,
        patient_linked_by_ref="actor:synthetic-physician"
        if patient is not None
        else None,
        appointment_linked_at=linked_at if appointment is not None else None,
        appointment_linked_by_ref="actor:synthetic-physician"
        if appointment is not None
        else None,
    )
    prep.created_at = created
    prep.updated_at = created
    return prep


def _schedule(
    prep: VisitPrep,
    *,
    generation: int,
    scheduled_at: datetime,
    purge_hours: int = 720,
    hard_purge_hours: int = 2160,
) -> VisitPrepScheduleGeneration:
    scheduled_utc = scheduled_at.astimezone(UTC)
    return VisitPrepScheduleGeneration(
        visit_prep_id=prep.id,
        owner_practice_member_id=prep.owner_practice_member_id,
        generation=generation,
        scheduled_at=scheduled_utc,
        timezone_name="America/New_York",
        unused_purge_at=scheduled_utc + timedelta(hours=purge_hours),
        unused_hard_purge_at=scheduled_utc + timedelta(hours=hard_purge_hours),
    )


def _credential(
    prep: VisitPrep,
    schedule: VisitPrepScheduleGeneration,
    *,
    generation: int,
    token_character: str,
    issued_at: datetime | None = None,
    expiry_hours: int = 72,
) -> VisitPrepCredentialGeneration:
    issued = issued_at or datetime.now(UTC)
    return VisitPrepCredentialGeneration(
        visit_prep_id=prep.id,
        owner_practice_member_id=prep.owner_practice_member_id,
        schedule_generation_id=schedule.id,
        generation=generation,
        token_hash=token_character * 64,
        issued_at=issued,
        expires_at=schedule.scheduled_at + timedelta(hours=expiry_hours),
    )


def _reject_insert(instance: object, match: str) -> None:
    with get_session() as session:
        with pytest.raises(DBAPIError, match=match):
            with session.begin_nested():
                session.add(instance)
                session.flush()


def _reject_update(table_name: str, row_id, values: dict, match: str) -> None:
    table = SQLModel.metadata.tables[table_name]
    with get_session() as session:
        with pytest.raises(DBAPIError, match=match):
            with session.begin_nested():
                session.execute(
                    update(table).where(table.c.id == row_id).values(**values)
                )


def test_visit_prep_requires_a_published_owned_question_version() -> None:
    scope = _scope("draft", published=False)

    _reject_insert(
        _new_prep(scope),
        "requires a published owned question version",
    )

    scope.question_version.published_at = datetime.now(UTC) + timedelta(minutes=5)
    scope.question_version.save()
    prep = _new_prep(scope).save()

    assert prep.question_set_version_id == scope.question_version.id


def test_tenant_patient_and_appointment_fences_reject_mismatched_scope() -> None:
    owner_scope = _scope("scope-owner")
    other_scope = _scope("scope-other")
    owner_patient = _patient(owner_scope, "owner")
    other_patient = _patient(other_scope, "other")
    second_owner_patient = _patient(owner_scope, "second-owner-patient")
    mismatched_appointment = _appointment(
        owner_scope,
        second_owner_patient,
        "other-patient",
    )

    wrong_practice = _new_prep(owner_scope)
    wrong_practice.practice_id = other_scope.practice.id
    _reject_insert(wrong_practice, "fk_visit_prep_owner_scope")

    _reject_insert(
        _new_prep(owner_scope, patient=other_patient),
        "fk_visit_prep_patient_owner",
    )

    _reject_insert(
        _new_prep(
            owner_scope,
            patient=owner_patient,
            appointment=mismatched_appointment,
        ),
        "fk_visit_prep_appointment_owner_patient",
    )

    valid_appointment = _appointment(owner_scope, owner_patient, "valid")
    valid = _new_prep(
        owner_scope,
        patient=owner_patient,
        appointment=valid_appointment,
    ).save()
    assert valid.patient_id == owner_patient.id
    assert valid.appointment_id == valid_appointment.id


def test_sensitive_snapshot_digest_and_token_hash_are_excluded_from_serialization() -> (
    None
):
    scope = _scope("secret-serialization")
    plaintext = json.dumps(
        {
            "first_name": "SyntheticSecretName",
            "email": "synthetic-secret@example.test",
        }
    )
    prep = _new_prep(scope, recipient_plaintext=plaintext).save()
    scheduled_at = datetime.now(UTC) + timedelta(days=7)
    schedule = _schedule(prep, generation=1, scheduled_at=scheduled_at).save()
    credential = _credential(
        prep,
        schedule,
        generation=1,
        token_character="b",
    ).save()

    prep_dump = prep.model_dump()
    credential_dump = credential.model_dump()
    assert "recipient_snapshot_ciphertext" not in prep_dump
    assert "creation_payload_hmac_sha256" not in prep_dump
    assert "token_hash" not in credential_dump

    with get_session() as session:
        stored = (
            session.execute(
                text(
                    "SELECT recipient_snapshot_ciphertext, "
                    "creation_payload_hmac_sha256 FROM visit_prep WHERE id = :id"
                ),
                {"id": prep.id.uuid},
            )
            .mappings()
            .one()
        )

    assert stored["recipient_snapshot_ciphertext"] == prep.recipient_snapshot_ciphertext
    assert "SyntheticSecretName" not in stored["recipient_snapshot_ciphertext"]
    assert (
        "synthetic-secret@example.test" not in stored["recipient_snapshot_ciphertext"]
    )
    assert stored["creation_payload_hmac_sha256"] == "a" * 64


def test_root_snapshots_and_attached_canonical_links_are_immutable() -> None:
    scope = _scope("root-immutable")
    first_patient = _patient(scope, "first")
    second_patient = _patient(scope, "second")
    prep = _new_prep(scope, patient=first_patient).save()
    assert prep.patient_linked_at is not None

    _reject_update(
        "visit_prep",
        prep.id,
        {"recipient_snapshot_ciphertext": encrypt_value('{"first_name":"Changed"}')},
        "ownership and creation snapshots are immutable",
    )
    _reject_update(
        "visit_prep",
        prep.id,
        {
            "patient_id": second_patient.id,
            "patient_linked_at": prep.patient_linked_at + timedelta(minutes=1),
        },
        "patient linkage is immutable once attached",
    )


def test_revocation_cannot_attach_canonical_links_in_the_same_update() -> None:
    scope = _scope("revoke-with-link")
    patient = _patient(scope, "revoke-with-link")
    appointment = _appointment(scope, patient, "revoke-with-link")
    prep = _new_prep(scope).save()
    assert prep.created_at is not None
    event_at = prep.created_at + timedelta(minutes=1)
    scheduled_at = event_at + timedelta(days=14)
    _schedule(prep, generation=1, scheduled_at=scheduled_at).save()

    _reject_update(
        "visit_prep",
        prep.id,
        {
            "patient_id": patient.id,
            "patient_linked_at": event_at,
            "patient_linked_by_ref": "actor:synthetic-physician",
            "appointment_id": appointment.id,
            "appointment_linked_at": event_at,
            "appointment_linked_by_ref": "actor:synthetic-physician",
            "revoked_at": event_at,
            "revoked_by_ref": "actor:synthetic-physician",
            "revocation_reason": "appointment_cancelled",
        },
        "revoked or quarantined Visit Prep cannot gain canonical links",
    )

    _reject_update(
        "visit_prep",
        prep.id,
        {
            "lifecycle_state": "confirmed",
            "confirmed_at": event_at,
            "confirmed_by_role": "patient",
            "retention_policy_version": "clinical.v1",
            "retain_until": scheduled_at.replace(year=scheduled_at.year + 7),
            "revoked_at": event_at,
            "revoked_by_ref": "actor:synthetic-physician",
            "revocation_reason": "appointment_cancelled",
        },
        "revoked Visit Prep cannot advance",
    )


def test_lifecycle_and_retention_are_monotonic_and_quarantine_is_terminal() -> None:
    scope = _scope("lifecycle")
    prep = _new_prep(scope).save()
    assert prep.created_at is not None
    confirmed_at = prep.created_at + timedelta(minutes=1)
    started_at = confirmed_at + timedelta(minutes=1)
    completed_at = started_at + timedelta(minutes=1)
    scheduled_at = confirmed_at + timedelta(days=14)
    _schedule(prep, generation=1, scheduled_at=scheduled_at).save()
    retain_until = scheduled_at.replace(year=scheduled_at.year + 7)
    table = SQLModel.metadata.tables["visit_prep"]

    _reject_update(
        "visit_prep",
        prep.id,
        {
            "lifecycle_state": "in_progress",
            "confirmed_at": confirmed_at,
            "confirmed_by_role": "patient",
            "started_at": started_at,
            "retention_policy_version": "clinical.v1",
            "retain_until": retain_until,
        },
        "invalid Visit Prep lifecycle transition",
    )

    with get_session() as session:
        session.execute(
            update(table)
            .where(table.c.id == prep.id)
            .values(
                lifecycle_state="confirmed",
                confirmed_at=confirmed_at,
                confirmed_by_role="patient",
                retention_policy_version="clinical.v1",
                retain_until=retain_until,
            )
        )
        session.commit()
        session.execute(
            update(table)
            .where(table.c.id == prep.id)
            .values(lifecycle_state="in_progress", started_at=started_at)
        )
        session.commit()
        session.execute(
            update(table)
            .where(table.c.id == prep.id)
            .values(lifecycle_state="completed", completed_at=completed_at)
        )
        session.commit()

    _reject_update(
        "visit_prep",
        prep.id,
        {"retain_until": retain_until + timedelta(days=1)},
        "retention snapshot is immutable",
    )
    _reject_update(
        "visit_prep",
        prep.id,
        {"lifecycle_state": "in_progress", "completed_at": None},
        "invalid Visit Prep lifecycle transition",
    )

    misdirected_at = completed_at + timedelta(minutes=1)
    _reject_update(
        "visit_prep",
        prep.id,
        {
            "lifecycle_state": "misdirected",
            "revoked_at": misdirected_at,
            "revoked_by_ref": "actor:synthetic-security-owner",
            "revocation_reason": "wrong_recipient",
            "misdirected_at": misdirected_at,
            "misdirected_reason": "wrong_recipient_interaction",
            "misdirected_incident_ref": "incident:synthetic-001",
        },
        "quarantine requires pending review first",
    )
    _reject_update(
        "visit_prep",
        prep.id,
        {
            "lifecycle_state": "misdirected",
            "revoked_at": misdirected_at,
            "revoked_by_ref": "actor:synthetic-security-owner",
            "revocation_reason": "wrong_recipient",
            "misdirected_at": misdirected_at,
            "misdirected_reason": "wrong_recipient_interaction",
            "misdirected_incident_ref": "incident:synthetic-001",
            "misdirected_disposition": "purge_approved",
            "misdirected_disposition_at": misdirected_at,
            "misdirected_disposition_by_ref": "actor:synthetic-security-owner",
            "misdirected_disposition_until": misdirected_at,
        },
        "quarantine requires pending review first",
    )
    with get_session() as session:
        session.execute(
            update(table)
            .where(table.c.id == prep.id)
            .values(
                lifecycle_state="misdirected",
                revoked_at=misdirected_at,
                revoked_by_ref="actor:synthetic-security-owner",
                revocation_reason="wrong_recipient",
                misdirected_at=misdirected_at,
                misdirected_reason="wrong_recipient_interaction",
                misdirected_incident_ref="incident:synthetic-001",
                misdirected_disposition="pending_review",
            )
        )
        session.commit()

    _reject_update(
        "visit_prep",
        prep.id,
        {"completed_at": completed_at + timedelta(minutes=2)},
        "completion is immutable",
    )


def test_schedule_policy_is_exact_sequential_and_immutable() -> None:
    prep = _new_prep(_scope("schedule")).save()
    scheduled_at = datetime.now(UTC) + timedelta(days=14)

    _reject_insert(
        _schedule(
            prep,
            generation=1,
            scheduled_at=scheduled_at,
            purge_hours=719,
        ),
        "unused_purge_at_policy",
    )
    _reject_insert(
        _schedule(prep, generation=2, scheduled_at=scheduled_at),
        "schedule generation is not sequential",
    )

    first = _schedule(prep, generation=1, scheduled_at=scheduled_at).save()
    assert first.unused_purge_at == scheduled_at + timedelta(hours=720)
    assert first.unused_hard_purge_at == scheduled_at + timedelta(hours=2160)

    prep_table = SQLModel.metadata.tables["visit_prep"]
    with get_session() as session:
        with pytest.raises(DBAPIError, match="not yet eligible for deletion"):
            with session.begin_nested():
                session.execute(delete(prep_table).where(prep_table.c.id == prep.id))

    _reject_insert(
        _schedule(
            prep,
            generation=3,
            scheduled_at=scheduled_at + timedelta(days=1),
        ),
        "schedule generation is not sequential",
    )
    second = _schedule(
        prep,
        generation=2,
        scheduled_at=scheduled_at + timedelta(days=1),
    ).save()
    assert second.generation == 2

    _reject_update(
        "visit_prep_schedule_generation",
        first.id,
        {"timezone_name": "UTC"},
        "schedule generations are immutable",
    )
    schedule_table = SQLModel.metadata.tables["visit_prep_schedule_generation"]
    with get_session() as session:
        with pytest.raises(DBAPIError, match="deleted through aggregate cleanup"):
            with session.begin_nested():
                session.execute(
                    delete(schedule_table).where(schedule_table.c.id == first.id)
                )


def test_credentials_require_current_schedule_exact_expiry_and_rotation() -> None:
    prep = _new_prep(_scope("credential")).save()
    first_time = datetime.now(UTC) + timedelta(days=14)
    first_schedule = _schedule(prep, generation=1, scheduled_at=first_time).save()

    _reject_insert(
        _credential(
            prep,
            first_schedule,
            generation=1,
            token_character="c",
            expiry_hours=71,
        ),
        "expiry must be schedule plus 72 hours",
    )
    first = _credential(
        prep,
        first_schedule,
        generation=1,
        token_character="d",
    ).save()

    _reject_insert(
        _credential(
            prep,
            first_schedule,
            generation=2,
            token_character="e",
        ),
        "uq_visit_prep_active_credential",
    )

    second_schedule = _schedule(
        prep,
        generation=2,
        scheduled_at=first_time + timedelta(days=2),
    ).save()
    _reject_insert(
        _credential(
            prep,
            first_schedule,
            generation=2,
            token_character="0",
        ),
        "requires the current schedule",
    )

    credential_table = SQLModel.metadata.tables["visit_prep_credential_generation"]
    revoked_at = datetime.now(UTC) + timedelta(minutes=1)
    with get_session() as session:
        session.execute(
            update(credential_table)
            .where(credential_table.c.id == first.id)
            .values(
                revoked_at=revoked_at,
                revoked_by_ref="actor:synthetic-physician",
                revocation_reason="rescheduled",
            )
        )
        session.commit()

    second = _credential(
        prep,
        second_schedule,
        generation=2,
        token_character="f",
    ).save()
    assert second.expires_at == second_schedule.scheduled_at + timedelta(hours=72)

    _reject_update(
        "visit_prep_credential_generation",
        first.id,
        {
            "revoked_at": None,
            "revoked_by_ref": None,
            "revocation_reason": None,
        },
        "credential revocation is irreversible",
    )
    _reject_update(
        "visit_prep_credential_generation",
        second.id,
        {"token_hash": "0" * 64},
        "credential lineage is immutable",
    )


def test_active_legal_hold_blocks_deletion_then_released_hold_cascades() -> None:
    scope = _scope("legal-hold")
    now = datetime.now(UTC)
    prep = _new_prep(scope, created_at=now - timedelta(days=40)).save()
    schedule = _schedule(
        prep,
        generation=1,
        scheduled_at=now - timedelta(days=31),
    ).save()
    credential = _credential(
        prep,
        schedule,
        generation=1,
        token_character="1",
        issued_at=now - timedelta(days=40),
    ).save()
    hold = VisitPrepLegalHold(
        visit_prep_id=prep.id,
        owner_practice_member_id=prep.owner_practice_member_id,
        generation=1,
        placed_at=now - timedelta(days=20),
        placed_by_ref="actor:synthetic-security-owner",
        placement_reason="synthetic_legal_review",
    ).save()
    prep_table = SQLModel.metadata.tables["visit_prep"]
    hold_table = SQLModel.metadata.tables["visit_prep_legal_hold"]

    with get_session() as session:
        with pytest.raises(DBAPIError, match="active legal hold cannot be deleted"):
            with session.begin_nested():
                session.execute(delete(prep_table).where(prep_table.c.id == prep.id))
        with pytest.raises(DBAPIError, match="deleted through aggregate cleanup"):
            with session.begin_nested():
                session.execute(delete(hold_table).where(hold_table.c.id == hold.id))

        session.execute(
            text(
                "CREATE TEMP TABLE visit_prep_legal_hold "
                "(visit_prep_id uuid, released_at timestamptz)"
            )
        )
        try:
            with pytest.raises(DBAPIError, match="active legal hold cannot be deleted"):
                with session.begin_nested():
                    session.execute(
                        text("DELETE FROM public.visit_prep WHERE id = :id"),
                        {"id": prep.id.uuid},
                    )
        finally:
            session.execute(text("DROP TABLE pg_temp.visit_prep_legal_hold"))

        with pytest.raises(DBAPIError, match="release cannot be future-dated"):
            with session.begin_nested():
                session.execute(
                    update(hold_table)
                    .where(hold_table.c.id == hold.id)
                    .values(
                        released_at=now + timedelta(days=1),
                        released_by_ref="actor:synthetic-security-owner",
                        release_reason="synthetic_review_complete",
                    )
                )

        released_at = now - timedelta(days=19)
        session.execute(
            update(hold_table)
            .where(hold_table.c.id == hold.id)
            .values(
                released_at=released_at,
                released_by_ref="actor:synthetic-security-owner",
                release_reason="synthetic_review_complete",
            )
        )
        session.commit()

    _reject_update(
        "visit_prep_legal_hold",
        hold.id,
        {
            "released_at": None,
            "released_by_ref": None,
            "release_reason": None,
        },
        "legal-hold release is irreversible",
    )

    with get_session() as session:
        session.execute(delete(prep_table).where(prep_table.c.id == prep.id))
        session.commit()
        schedule_table = SQLModel.metadata.tables["visit_prep_schedule_generation"]
        credential_table = SQLModel.metadata.tables["visit_prep_credential_generation"]
        assert (
            session.scalar(
                select(func.count())
                .select_from(prep_table)
                .where(prep_table.c.id == prep.id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(schedule_table)
                .where(schedule_table.c.id == schedule.id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(credential_table)
                .where(credential_table.c.id == credential.id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(hold_table)
                .where(hold_table.c.id == hold.id)
            )
            == 0
        )
