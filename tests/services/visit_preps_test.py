"""PostgreSQL-backed tests for physician Visit Prep commands."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import pytest

from app.services.visit_prep_question_sets import ensure_default_question_set
from app.services.visit_preps import (
    RecipientInput,
    VisitPrepConflict,
    VisitPrepNotFound,
    VisitPrepValidation,
    access_token_hash,
    create_visit_prep,
    reconcile_visit_prep,
    reschedule_visit_prep,
    revoke_visit_prep,
    rotate_visit_prep_credential,
)
from app.utils.encryption import decrypt_value

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.practice import Practice, PracticeMember
from app.models.visit_prep import (
    VisitPrep,
    VisitPrepCredentialGeneration,
    VisitPrepScheduleGeneration,
)
from sqlalchemy import func
from sqlmodel import select

from tests.factories import save_practice_member


def _withdraw_seat(member: PracticeMember, *, ends_at: datetime | None = None):
    from app.models.practice import lock_practice_seats

    with get_session() as session:
        lock_practice_seats(session, member.practice_id)
        member.seat_state = "inactive"
        member.access_ends_at = ends_at
        session.add(member)
        session.commit()
        session.refresh(member)


@dataclass(frozen=True)
class _Scope:
    doctor: Doctor
    practice: Practice
    owner: PracticeMember
    question_set_version_id: TypeIDType


def _scope(label: str, *, now: datetime) -> _Scope:
    nonce = secrets.token_hex(5)
    doctor = Doctor(
        clerk_id=f"user_visit_prep_commands_{label}_{nonce}",
        email=f"visit-prep-commands-{label}-{nonce}@example.test",
        biography="Synthetic Visit Prep command test account.",
    ).save()
    practice = Practice(
        legal_name=f"Synthetic Visit Prep Practice {label} {nonce}",
        join_code=f"P{secrets.token_hex(4).upper()[:7]}",
        email_domain="example.test",
        negotiated_baa_on_file=True,
        negotiated_baa_effective_date=now - timedelta(days=1),
        negotiated_baa_reference=f"test:visit-prep:{label}:{nonce}",
        billing_exempt=True,
    ).save()
    owner = save_practice_member(
        PracticeMember(
            practice_id=practice.id,
            doctor_id=doctor.id,
            role="owner",
            attested_at=now - timedelta(days=1),
            attestation_name="Synthetic Physician",
        )
    )
    seeded = ensure_default_question_set(
        owner_practice_member_id=owner.id,
        # Question-set publication cannot predate its database-generated
        # creation timestamp. Command clocks below intentionally start later.
        now=datetime.now(UTC) + timedelta(seconds=1),
    )
    return _Scope(doctor, practice, owner, seeded.version.id)


def _recipient(*, email: str = "patient@example.com") -> RecipientInput:
    return RecipientInput(
        first_name="  Jos\N{LATIN SMALL LETTER E WITH ACUTE}  ",
        role="patient",
        email=email,
        phone="+12025550123",
    )


def _create(
    scope: _Scope,
    *,
    now: datetime,
    scheduled_at: datetime,
    idempotency_key: str | None = None,
    recipient: RecipientInput | None = None,
    question_set_version_id: TypeIDType | None = None,
):
    return create_visit_prep(
        owner_practice_member_id=scope.owner.id,
        owner_doctor_id=scope.doctor.id,
        practice_id=scope.practice.id,
        idempotency_key=idempotency_key or str(uuid4()),
        recipient=recipient or _recipient(),
        physician_display_name="  Dr. Synthetic  ",
        scheduled_at=scheduled_at,
        timezone_name="UTC",
        question_set_version_id=(
            question_set_version_id or scope.question_set_version_id
        ),
        now=now,
    )


def _lineage(visit_prep_id):
    with get_session() as session:
        visit_prep = session.exec(
            select(VisitPrep).where(VisitPrep.id == visit_prep_id)
        ).one()
        schedules = list(
            session.exec(
                select(VisitPrepScheduleGeneration)
                .where(VisitPrepScheduleGeneration.visit_prep_id == visit_prep_id)
                .order_by(cast(Any, VisitPrepScheduleGeneration.generation))
            ).all()
        )
        credentials = list(
            session.exec(
                select(VisitPrepCredentialGeneration)
                .where(VisitPrepCredentialGeneration.visit_prep_id == visit_prep_id)
                .order_by(cast(Any, VisitPrepCredentialGeneration.generation))
            ).all()
        )
        return visit_prep, schedules, credentials


def test_create_atomically_persists_encrypted_recipient_and_bounded_lineage() -> None:
    now = datetime.now(UTC) + timedelta(seconds=2)
    scheduled_at = now + timedelta(days=7)
    scope = _scope("create", now=now)

    result = _create(scope, now=now, scheduled_at=scheduled_at)

    assert result.disposition == "created"
    assert result.access_token is not None
    assert result.access_token.startswith("vp1_")
    assert result.visit_prep.schedule_generation == 1
    assert result.visit_prep.credential_generation == 1
    assert result.visit_prep.credential_expires_at == scheduled_at + timedelta(hours=72)
    assert result.visit_prep.has_email_contact is True
    assert result.visit_prep.has_sms_contact is True
    assert result.visit_prep.physician_display_name == "Dr. Synthetic"
    assert result.visit_prep.practice_display_name == scope.practice.legal_name

    visit_prep, schedules, credentials = _lineage(result.visit_prep.id)
    assert len(schedules) == 1
    assert len(credentials) == 1
    assert schedules[0].unused_purge_at == scheduled_at + timedelta(days=30)
    assert schedules[0].unused_hard_purge_at == scheduled_at + timedelta(days=90)
    assert credentials[0].expires_at == scheduled_at + timedelta(hours=72)
    assert credentials[0].token_hash == access_token_hash(result.access_token)
    assert result.access_token not in credentials[0].token_hash
    assert result.access_token not in visit_prep.recipient_snapshot_ciphertext
    assert "patient@example.com" not in visit_prep.recipient_snapshot_ciphertext
    assert json.loads(decrypt_value(visit_prep.recipient_snapshot_ciphertext)) == {
        "email": "patient@example.com",
        "first_name": "Jos\N{LATIN SMALL LETTER E WITH ACUTE}",
        "phone": "+12025550123",
        "role": "patient",
        "version": 1,
    }
    credential_table = cast(Any, VisitPrepCredentialGeneration).__table__
    assert "access_token" not in credential_table.columns

    with get_session() as session:
        assert (
            session.exec(
                select(func.count())
                .select_from(VisitPrep)
                .where(VisitPrep.owner_practice_member_id == scope.owner.id)
            ).one()
            == 1
        )
        assert (
            session.exec(
                select(func.count())
                .select_from(VisitPrepScheduleGeneration)
                .where(
                    VisitPrepScheduleGeneration.visit_prep_id == result.visit_prep.id
                )
            ).one()
            == 1
        )
        assert (
            session.exec(
                select(func.count())
                .select_from(VisitPrepCredentialGeneration)
                .where(
                    VisitPrepCredentialGeneration.visit_prep_id == result.visit_prep.id
                )
            ).one()
            == 1
        )


def test_create_replays_without_plaintext_and_rejects_divergent_payload() -> None:
    now = datetime.now(UTC) + timedelta(seconds=2)
    scheduled_at = now + timedelta(days=8)
    scope = _scope("replay", now=now)
    key = str(uuid4())

    created = _create(
        scope,
        now=now,
        scheduled_at=scheduled_at,
        idempotency_key=key,
        recipient=_recipient(email="PATIENT@EXAMPLE.COM"),
    )
    replayed = _create(
        scope,
        now=now + timedelta(minutes=1),
        scheduled_at=scheduled_at,
        idempotency_key=key,
        recipient=_recipient(),
    )

    assert replayed.disposition == "replayed"
    assert replayed.visit_prep.id == created.visit_prep.id
    assert replayed.access_token is None

    with pytest.raises(VisitPrepConflict, match="different payload"):
        _create(
            scope,
            now=now + timedelta(minutes=2),
            scheduled_at=scheduled_at,
            idempotency_key=key,
            recipient=_recipient(email="different@example.com"),
        )

    _visit_prep, schedules, credentials = _lineage(created.visit_prep.id)
    assert len(schedules) == 1
    assert len(credentials) == 1


def test_create_replay_remains_idempotent_after_expiry_but_new_past_create_fails() -> (
    None
):
    now = datetime.now(UTC) + timedelta(seconds=2)
    scheduled_at = now + timedelta(hours=1)
    scope = _scope("expired-replay", now=now)
    key = str(uuid4())
    created = _create(
        scope,
        now=now,
        scheduled_at=scheduled_at,
        idempotency_key=key,
    )

    replayed = _create(
        scope,
        now=scheduled_at + timedelta(hours=73),
        scheduled_at=scheduled_at,
        idempotency_key=key,
    )

    assert replayed.disposition == "replayed"
    assert replayed.access_token is None
    assert replayed.visit_prep.id == created.visit_prep.id
    assert replayed.visit_prep.credential_active is False

    with pytest.raises(VisitPrepValidation, match="must be in the future"):
        _create(
            scope,
            now=scheduled_at + timedelta(hours=73),
            scheduled_at=scheduled_at,
        )


def test_rotate_then_revoke_preserves_credential_history_and_is_idempotent() -> None:
    now = datetime.now(UTC) + timedelta(seconds=2)
    scheduled_at = now + timedelta(days=10)
    scope = _scope("rotate-revoke", now=now)
    created = _create(scope, now=now, scheduled_at=scheduled_at)

    rotated = rotate_visit_prep_credential(
        owner_practice_member_id=scope.owner.id,
        owner_doctor_id=scope.doctor.id,
        practice_id=scope.practice.id,
        visit_prep_id=created.visit_prep.id,
        expected_credential_generation=1,
        now=now + timedelta(hours=1),
    )

    assert rotated.disposition == "rotated"
    assert rotated.access_token is not None
    assert rotated.access_token != created.access_token
    assert rotated.visit_prep.credential_generation == 2
    assert rotated.visit_prep.credential_expires_at == scheduled_at + timedelta(
        hours=72
    )
    with pytest.raises(VisitPrepConflict, match="stale"):
        rotate_visit_prep_credential(
            owner_practice_member_id=scope.owner.id,
            owner_doctor_id=scope.doctor.id,
            practice_id=scope.practice.id,
            visit_prep_id=created.visit_prep.id,
            expected_credential_generation=1,
            now=now + timedelta(hours=2),
        )

    revoked = revoke_visit_prep(
        owner_practice_member_id=scope.owner.id,
        owner_doctor_id=scope.doctor.id,
        practice_id=scope.practice.id,
        visit_prep_id=created.visit_prep.id,
        expected_credential_generation=2,
        reason="physician_revoked",
        now=now + timedelta(hours=2),
    )
    replayed = revoke_visit_prep(
        owner_practice_member_id=scope.owner.id,
        owner_doctor_id=scope.doctor.id,
        practice_id=scope.practice.id,
        visit_prep_id=created.visit_prep.id,
        expected_credential_generation=2,
        reason="physician_revoked",
        now=now + timedelta(hours=3),
    )

    assert revoked.disposition == "revoked"
    assert revoked.access_token is None
    assert revoked.visit_prep.credential_active is False
    assert replayed.disposition == "replayed"
    assert replayed.access_token is None
    visit_prep, _schedules, credentials = _lineage(created.visit_prep.id)
    assert visit_prep.revocation_reason == "physician_revoked"
    assert [credential.generation for credential in credentials] == [1, 2]
    assert [credential.revocation_reason for credential in credentials] == [
        "rotated",
        "physician_revoked",
    ]

    with pytest.raises(VisitPrepConflict, match="another reason"):
        revoke_visit_prep(
            owner_practice_member_id=scope.owner.id,
            owner_doctor_id=scope.doctor.id,
            practice_id=scope.practice.id,
            visit_prep_id=created.visit_prep.id,
            expected_credential_generation=2,
            reason="appointment_cancelled",
            now=now + timedelta(hours=4),
        )
    with pytest.raises(VisitPrepConflict, match="stale"):
        revoke_visit_prep(
            owner_practice_member_id=scope.owner.id,
            owner_doctor_id=scope.doctor.id,
            practice_id=scope.practice.id,
            visit_prep_id=created.visit_prep.id,
            expected_credential_generation=1,
            reason="physician_revoked",
            now=now + timedelta(hours=4),
        )


def test_reschedule_appends_schedule_and_rotates_credential_atomically() -> None:
    now = datetime.now(UTC) + timedelta(seconds=2)
    original_at = now + timedelta(days=5)
    replacement_at = now + timedelta(days=12)
    scope = _scope("reschedule", now=now)
    created = _create(scope, now=now, scheduled_at=original_at)

    rescheduled = reschedule_visit_prep(
        owner_practice_member_id=scope.owner.id,
        owner_doctor_id=scope.doctor.id,
        practice_id=scope.practice.id,
        visit_prep_id=created.visit_prep.id,
        expected_schedule_generation=1,
        expected_credential_generation=1,
        scheduled_at=replacement_at,
        timezone_name="UTC",
        now=now + timedelta(hours=1),
    )

    assert rescheduled.disposition == "rescheduled"
    assert rescheduled.access_token is not None
    assert rescheduled.visit_prep.schedule_generation == 2
    assert rescheduled.visit_prep.credential_generation == 2
    assert rescheduled.visit_prep.credential_expires_at == replacement_at + timedelta(
        hours=72
    )
    replayed = reschedule_visit_prep(
        owner_practice_member_id=scope.owner.id,
        owner_doctor_id=scope.doctor.id,
        practice_id=scope.practice.id,
        visit_prep_id=created.visit_prep.id,
        expected_schedule_generation=2,
        expected_credential_generation=2,
        scheduled_at=replacement_at,
        timezone_name="UTC",
        now=now + timedelta(hours=2),
    )
    assert replayed.disposition == "replayed"
    assert replayed.access_token is None

    _visit_prep, schedules, credentials = _lineage(created.visit_prep.id)
    assert [schedule.generation for schedule in schedules] == [1, 2]
    assert [schedule.scheduled_at for schedule in schedules] == [
        original_at,
        replacement_at,
    ]
    assert credentials[0].revocation_reason == "rescheduled"
    assert credentials[1].schedule_generation_id == schedules[1].id


def test_reschedule_rejects_an_already_expired_credential() -> None:
    now = datetime.now(UTC) + timedelta(seconds=2)
    original_at = now + timedelta(hours=1)
    scope = _scope("expired-reschedule", now=now)
    created = _create(scope, now=now, scheduled_at=original_at)

    with pytest.raises(VisitPrepConflict, match="already expired"):
        reschedule_visit_prep(
            owner_practice_member_id=scope.owner.id,
            owner_doctor_id=scope.doctor.id,
            practice_id=scope.practice.id,
            visit_prep_id=created.visit_prep.id,
            expected_schedule_generation=1,
            expected_credential_generation=1,
            scheduled_at=now + timedelta(days=10),
            timezone_name="UTC",
            now=original_at + timedelta(hours=73),
        )

    _visit_prep, schedules, credentials = _lineage(created.visit_prep.id)
    assert len(schedules) == 1
    assert len(credentials) == 1
    assert credentials[0].revoked_at is None


def test_reconcile_attaches_owned_appointment_once_without_rotating_token() -> None:
    now = datetime.now(UTC) + timedelta(seconds=2)
    scheduled_at = now + timedelta(days=6)
    scope = _scope("reconcile", now=now)
    patient = Patient(
        doctor_id=scope.doctor.id,
        name="Synthetic Reconciliation Patient",
        date_of_birth=datetime(1980, 1, 1, tzinfo=UTC),
    ).save()
    appointment = Appointment(
        doctor_id=scope.doctor.id,
        patient_id=patient.id,
        created_by_system="visit_prep_test",
        external_id=None,
        appointment_date=scheduled_at,
        appointment_name="Synthetic reconciliation appointment",
        appointment_notes_preview=None,
    ).save()
    created = _create(scope, now=now, scheduled_at=scheduled_at)

    reconciled = reconcile_visit_prep(
        owner_practice_member_id=scope.owner.id,
        owner_doctor_id=scope.doctor.id,
        practice_id=scope.practice.id,
        visit_prep_id=created.visit_prep.id,
        appointment_id=appointment.id,
        now=now + timedelta(minutes=5),
    )
    replayed = reconcile_visit_prep(
        owner_practice_member_id=scope.owner.id,
        owner_doctor_id=scope.doctor.id,
        practice_id=scope.practice.id,
        visit_prep_id=created.visit_prep.id,
        appointment_id=appointment.id,
        now=now + timedelta(minutes=6),
    )

    assert reconciled.disposition == "reconciled"
    assert reconciled.access_token is None
    assert reconciled.visit_prep.patient_id == patient.id
    assert reconciled.visit_prep.appointment_id == appointment.id
    assert reconciled.visit_prep.credential_generation == 1
    assert replayed.disposition == "replayed"
    _visit_prep, schedules, credentials = _lineage(created.visit_prep.id)
    assert len(schedules) == 1
    assert len(credentials) == 1


def test_cross_owner_versions_records_and_commands_are_not_discoverable() -> None:
    now = datetime.now(UTC) + timedelta(seconds=2)
    scheduled_at = now + timedelta(days=9)
    first = _scope("owner-one", now=now)
    second = _scope("owner-two", now=now)

    with pytest.raises(VisitPrepNotFound, match="question-set version"):
        _create(
            first,
            now=now,
            scheduled_at=scheduled_at,
            question_set_version_id=second.question_set_version_id,
        )

    created = _create(first, now=now, scheduled_at=scheduled_at)
    foreign_patient = Patient(
        doctor_id=second.doctor.id,
        name="Synthetic Foreign Patient",
        date_of_birth=datetime(1981, 1, 1, tzinfo=UTC),
    ).save()
    foreign_appointment = Appointment(
        doctor_id=second.doctor.id,
        patient_id=foreign_patient.id,
        created_by_system="visit_prep_test",
        external_id=None,
        appointment_date=scheduled_at,
        appointment_name="Synthetic foreign appointment",
        appointment_notes_preview=None,
    ).save()

    with pytest.raises(VisitPrepNotFound, match="appointment"):
        reconcile_visit_prep(
            owner_practice_member_id=first.owner.id,
            owner_doctor_id=first.doctor.id,
            practice_id=first.practice.id,
            visit_prep_id=created.visit_prep.id,
            appointment_id=foreign_appointment.id,
            now=now + timedelta(minutes=5),
        )
    with pytest.raises(VisitPrepNotFound, match="Visit Prep"):
        rotate_visit_prep_credential(
            owner_practice_member_id=second.owner.id,
            owner_doctor_id=second.doctor.id,
            practice_id=second.practice.id,
            visit_prep_id=created.visit_prep.id,
            expected_credential_generation=1,
            now=now + timedelta(minutes=5),
        )

    visit_prep, schedules, credentials = _lineage(created.visit_prep.id)
    assert visit_prep.patient_id is None
    assert visit_prep.appointment_id is None
    assert len(schedules) == 1
    assert len(credentials) == 1


def test_invalid_timezone_and_tokens_fail_closed_without_creating_a_prep() -> None:
    now = datetime.now(UTC) + timedelta(seconds=2)
    scope = _scope("invalid", now=now)

    for invalid_timezone in ("Not/A_Real_Zone", "Etc/../UTC"):
        with pytest.raises(VisitPrepValidation, match="timezone is invalid"):
            create_visit_prep(
                owner_practice_member_id=scope.owner.id,
                owner_doctor_id=scope.doctor.id,
                practice_id=scope.practice.id,
                idempotency_key=str(uuid4()),
                recipient=_recipient(),
                physician_display_name="Dr. Synthetic",
                scheduled_at=now + timedelta(days=3),
                timezone_name=invalid_timezone,
                question_set_version_id=scope.question_set_version_id,
                now=now,
            )

    with pytest.raises(VisitPrepValidation, match="scheduled time is invalid"):
        create_visit_prep(
            owner_practice_member_id=scope.owner.id,
            owner_doctor_id=scope.doctor.id,
            practice_id=scope.practice.id,
            idempotency_key=str(uuid4()),
            recipient=_recipient(),
            physician_display_name="Dr. Synthetic",
            scheduled_at=datetime.max.replace(tzinfo=UTC),
            timezone_name="UTC",
            question_set_version_id=scope.question_set_version_id,
            now=now,
        )

    with pytest.raises(VisitPrepValidation, match="first name is invalid"):
        _create(
            scope,
            now=now,
            scheduled_at=now + timedelta(days=3),
            recipient=RecipientInput(first_name="\ud800", email="patient@example.com"),
        )

    with pytest.raises(VisitPrepValidation, match="scheduled time is invalid"):
        create_visit_prep(
            owner_practice_member_id=scope.owner.id,
            owner_doctor_id=scope.doctor.id,
            practice_id=scope.practice.id,
            idempotency_key=str(uuid4()),
            recipient=_recipient(),
            physician_display_name="Dr. Synthetic",
            scheduled_at=datetime.fromisoformat("9999-12-31T23:59:59-12:00"),
            timezone_name="Etc/GMT+12",
            question_set_version_id=scope.question_set_version_id,
            now=now,
        )

    with get_session() as session:
        assert (
            session.exec(
                select(func.count())
                .select_from(VisitPrep)
                .where(VisitPrep.owner_practice_member_id == scope.owner.id)
            ).one()
            == 0
        )

    for invalid in (
        "",
        "vp1_",
        "vp1_" + "A" * 42,
        "vp1_" + "A" * 44,
        "vp1_" + "!" * 43,
        "vp1_" + "\N{SNOWMAN}" * 43,
    ):
        with pytest.raises(VisitPrepValidation, match="credential is invalid"):
            access_token_hash(invalid)


@pytest.mark.parametrize(
    "withdrawal", ["seat", "baa", "entitlement", "attestation", "doctor"]
)
def test_creation_rechecks_current_platform_authority(withdrawal):
    now = datetime.now(UTC) + timedelta(seconds=2)
    scope = _scope("authority", now=now)
    if withdrawal == "seat":
        _withdraw_seat(scope.owner)
    elif withdrawal == "baa":
        scope.practice.negotiated_baa_on_file = False
        scope.practice.save()
    elif withdrawal == "entitlement":
        scope.practice.billing_exempt = False
        scope.practice.save()
    elif withdrawal == "attestation":
        scope.owner.attested_at = None
        scope.owner.attestation_name = None
        scope.owner.save()
    else:
        scope.doctor.deleted_at = now
        scope.doctor.save()
    with pytest.raises(VisitPrepNotFound, match="owner is unavailable"):
        _create(scope, now=now, scheduled_at=now + timedelta(days=7))


def test_scheduled_seat_end_remains_allowed_only_until_its_current_end():
    now = datetime.now(UTC) + timedelta(seconds=2)
    scope = _scope("ending-seat", now=now)
    _withdraw_seat(scope.owner, ends_at=now + timedelta(hours=1))
    assert (
        _create(scope, now=now, scheduled_at=now + timedelta(days=7)).disposition
        == "created"
    )
    with pytest.raises(VisitPrepNotFound):
        _create(
            scope, now=now + timedelta(hours=1), scheduled_at=now + timedelta(days=7)
        )


@pytest.mark.parametrize("operation", ["rotate", "revoke", "reschedule", "reconcile"])
def test_existing_preparation_commands_reject_withdrawn_seat(operation):
    now = datetime.now(UTC) + timedelta(seconds=2)
    scope = _scope("withdrawn", now=now)
    created = _create(scope, now=now, scheduled_at=now + timedelta(days=7))
    _withdraw_seat(scope.owner)
    kwargs: dict[str, Any] = dict(
        owner_practice_member_id=scope.owner.id,
        owner_doctor_id=scope.doctor.id,
        practice_id=scope.practice.id,
        visit_prep_id=created.visit_prep.id,
        now=now + timedelta(minutes=1),
    )
    if operation == "rotate":
        action = rotate_visit_prep_credential
        kwargs.update(expected_credential_generation=1)
    elif operation == "revoke":
        action = revoke_visit_prep
        kwargs.update(expected_credential_generation=1, reason="physician_revoked")
    elif operation == "reschedule":
        action = reschedule_visit_prep
        kwargs.update(
            expected_credential_generation=1,
            expected_schedule_generation=1,
            scheduled_at=now + timedelta(days=8),
            timezone_name="UTC",
        )
    else:
        action = reconcile_visit_prep
        patient = Patient(doctor_id=scope.doctor.id, name="Synthetic Patient").save()
        kwargs.update(patient_id=patient.id)
    with pytest.raises(VisitPrepNotFound):
        action(**kwargs)


def test_preparation_rejects_legacy_published_set_without_five_open_ended_topics():
    from app.models.visit_prep_question_set import (
        VisitPrepQuestionSet,
        VisitPrepQuestionSetVersion,
    )

    now = datetime.now(UTC) + timedelta(seconds=2)
    scope = _scope("legacy-published", now=now)
    question_set = VisitPrepQuestionSet(owner_practice_member_id=scope.owner.id).save()
    version = VisitPrepQuestionSetVersion(
        question_set_id=question_set.id,
        owner_practice_member_id=scope.owner.id,
        version_number=1,
        name="Legacy empty published questions",
        published_at=datetime.now(UTC) + timedelta(seconds=1),
    ).save()
    with pytest.raises(
        VisitPrepValidation, match="published question-set version is invalid"
    ):
        _create(
            scope,
            now=now,
            scheduled_at=now + timedelta(days=7),
            question_set_version_id=version.id,
        )
