"""PostgreSQL-backed tests for the public Visit Prep interview domain."""

from __future__ import annotations

import json
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from uuid import uuid4

import pytest

import app.services.visit_prep_interviews as interviews
from app.configuration.visit_prep import VisitPrepSettings
from app.services.visit_prep_interviews import (
    ResponseInput,
    VisitPrepInterviewConflict,
    VisitPrepInterviewUnavailable,
    VisitPrepInterviewValidation,
)
from app.services.visit_prep_question_sets import ensure_default_question_set
from app.services.visit_preps import (
    RecipientInput,
    create_visit_prep,
    reschedule_visit_prep,
    revoke_visit_prep,
    rotate_visit_prep_credential,
)
from app.utils.encryption import DecryptionError, decrypt_value

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember
from app.models.visit_prep import VisitPrep
from app.models.visit_prep_question_set import VisitPrepQuestion
from app.models.visit_prep_response import VisitPrepQuestionResponse
from sqlalchemy import text, update
from sqlalchemy.exc import DBAPIError
from sqlmodel import Session, SQLModel, select

from tests.factories import save_practice_member


@dataclass(frozen=True)
class _Fixture:
    doctor: Doctor
    practice: Practice
    owner: PracticeMember
    prep_id: TypeIDType
    access_token: str
    scheduled_at: datetime


def _enable(monkeypatch: pytest.MonkeyPatch, doctor: Doctor) -> None:
    monkeypatch.setattr(
        interviews.visit_prep_access,
        "VISIT_PREP_SETTINGS",
        VisitPrepSettings(
            enabled=True,
            public_api_enabled=True,
            enabled_doctor_ids=frozenset({str(doctor.id)}),
        ),
    )


def _fixture(
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    *,
    scheduled_at: datetime | None = None,
    recipient_role: Literal["patient", "caregiver"] = "patient",
) -> _Fixture:
    nonce = secrets.token_hex(5)
    now = datetime.now(UTC)
    doctor = Doctor(
        clerk_id=f"user_visit_prep_interview_{label}_{nonce}",
        email=f"visit-prep-interview-{label}-{nonce}@example.test",
        biography="Synthetic Visit Prep interview test account.",
    ).save()
    practice = Practice(
        legal_name=f"Synthetic Interview Practice {label} {nonce}",
        join_code=f"I{secrets.token_hex(4).upper()[:7]}",
        email_domain="example.test",
        negotiated_baa_on_file=True,
        negotiated_baa_effective_date=now - timedelta(days=1),
        negotiated_baa_reference=f"test:visit-prep-interview:{label}:{nonce}",
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
    seeded = ensure_default_question_set(owner_practice_member_id=owner.id)
    scheduled_at = scheduled_at or now + timedelta(days=20)
    created = create_visit_prep(
        owner_practice_member_id=owner.id,
        owner_doctor_id=doctor.id,
        practice_id=practice.id,
        idempotency_key=str(uuid4()),
        recipient=RecipientInput(
            first_name="Jos\N{LATIN SMALL LETTER E WITH ACUTE}",
            role=recipient_role,
            email="patient@example.com",
        ),
        physician_display_name="Dr. Synthetic",
        scheduled_at=scheduled_at,
        timezone_name="UTC",
        question_set_version_id=seeded.version.id,
    )
    assert created.access_token is not None
    _enable(monkeypatch, doctor)
    return _Fixture(
        doctor=doctor,
        practice=practice,
        owner=owner,
        prep_id=created.visit_prep.id,
        access_token=created.access_token,
        scheduled_at=scheduled_at,
    )


def _confirm(fixture: _Fixture):
    return interviews.confirm_interview(
        access_token=fixture.access_token,
        first_name="  JOSE\N{COMBINING ACUTE ACCENT}  ",
        role="patient",
    )


def _direct_confirmation_values(
    *,
    retain_until: datetime,
) -> dict[str, object]:
    return {
        "lifecycle_state": "confirmed",
        "confirmed_at": datetime.now(UTC),
        "confirmed_by_role": "patient",
        "retention_policy_version": "clinical.v1",
        "retain_until": retain_until,
    }


def _respond(
    fixture: _Fixture,
    question_key: str,
    *,
    answer: str | None = None,
    key: str | None = None,
):
    return interviews.submit_response(
        access_token=fixture.access_token,
        idempotency_key=key or str(uuid4()),
        response=ResponseInput(
            question_key=question_key,
            response_kind="skipped" if answer is None else "answered",
            answer=answer,
        ),
    )


def test_public_api_gate_precedes_token_hash_and_database_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        interviews.visit_prep_access,
        "VISIT_PREP_SETTINGS",
        VisitPrepSettings(enabled=True, public_api_enabled=False),
    )
    token_hash_calls: list[str] = []
    monkeypatch.setattr(
        interviews,
        "_canonical_token_hash",
        lambda value: token_hash_calls.append(value) or "unused",
    )

    with pytest.raises(VisitPrepInterviewUnavailable):
        interviews.get_interview(
            access_token="vp1_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        )

    assert token_hash_calls == []


def test_confirmation_snapshots_clinical_retention_and_hides_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(monkeypatch, "confirmation")

    invited = interviews.get_interview(access_token=fixture.access_token)
    assert invited.status == "confirmation_required"
    assert invited.physician_display_name is None
    assert invited.practice_display_name is None
    assert invited.scheduled_at is None
    assert invited.current_question is None

    confirmed = _confirm(fixture)
    assert confirmed.disposition == "confirmed"
    assert confirmed.interview.status == "in_progress"
    assert confirmed.interview.current_question is not None
    assert confirmed.interview.current_question.key == "reason"
    assert confirmed.interview.physician_display_name == "Dr. Synthetic"
    assert confirmed.interview.practice_display_name == fixture.practice.legal_name

    with get_session() as session:
        prep = session.exec(
            select(VisitPrep).where(VisitPrep.id == fixture.prep_id)
        ).one()
        assert prep.lifecycle_state == "confirmed"
        assert prep.confirmed_by_role == "patient"
        assert prep.retention_policy_version == "clinical.v1"
        assert prep.retain_until == fixture.scheduled_at.replace(
            year=fixture.scheduled_at.year + 7
        )

    replay = _confirm(fixture)
    assert replay.interview.status == "in_progress"
    with get_session() as session:
        prep = session.exec(
            select(VisitPrep).where(VisitPrep.id == fixture.prep_id)
        ).one()
        original_confirmed_at = prep.confirmed_at
        original_retain_until = prep.retain_until
    _confirm(fixture)
    with get_session() as session:
        prep = session.exec(
            select(VisitPrep).where(VisitPrep.id == fixture.prep_id)
        ).one()
        assert prep.confirmed_at == original_confirmed_at
        assert prep.retain_until == original_retain_until


def test_retention_trigger_uses_utc_calendar_boundary_in_non_utc_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduled_at = datetime(2027, 11, 7, 5, 30, tzinfo=UTC)
    fixture = _fixture(
        monkeypatch,
        "retention-timezone",
        scheduled_at=scheduled_at,
    )
    expected = scheduled_at.replace(year=scheduled_at.year + 7)
    prep_table = SQLModel.metadata.tables["visit_prep"]

    with get_session() as session:
        session.execute(text("SELECT set_config('TimeZone', 'America/New_York', true)"))
        session.execute(
            update(prep_table)
            .where(prep_table.c.id == fixture.prep_id)
            .values(**_direct_confirmation_values(retain_until=expected))
        )
        stored = session.execute(
            select(prep_table.c.retain_until).where(prep_table.c.id == fixture.prep_id)
        ).scalar_one()

    assert stored == expected


def test_confirmation_computes_retention_from_utc_in_non_utc_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # This instant is 01:30 EDT. Seven local calendar years later it is 01:30
    # EST, one hour later in UTC, so local-time arithmetic would violate the
    # database's immutable UTC retention rule.
    scheduled_at = datetime(2027, 11, 7, 5, 30, tzinfo=UTC)
    fixture = _fixture(
        monkeypatch,
        "confirmation-retention-timezone",
        scheduled_at=scheduled_at,
    )
    original_get_session = interviews.get_session

    @contextmanager
    def non_utc_service_session() -> Iterator[Session]:
        with original_get_session() as session:
            session.execute(
                text("SELECT set_config('TimeZone', 'America/New_York', true)")
            )
            yield session

    monkeypatch.setattr(interviews, "get_session", non_utc_service_session)

    confirmed = _confirm(fixture)

    assert confirmed.disposition == "confirmed"
    with get_session() as session:
        prep = session.exec(
            select(VisitPrep).where(VisitPrep.id == fixture.prep_id)
        ).one()
    assert prep.retain_until == datetime(2034, 11, 7, 5, 30, tzinfo=UTC)


def test_retention_trigger_rejects_one_microsecond_short(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(monkeypatch, "retention-short")
    expected = fixture.scheduled_at.replace(year=fixture.scheduled_at.year + 7)
    prep_table = SQLModel.metadata.tables["visit_prep"]

    with get_session() as session:
        with pytest.raises(DBAPIError, match="seven calendar years"):
            with session.begin_nested():
                session.execute(
                    update(prep_table)
                    .where(prep_table.c.id == fixture.prep_id)
                    .values(
                        **_direct_confirmation_values(
                            retain_until=expected - timedelta(microseconds=1),
                        )
                    )
                )


def test_retention_trigger_rejects_one_microsecond_long(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(monkeypatch, "retention-long")
    expected = fixture.scheduled_at.replace(year=fixture.scheduled_at.year + 7)
    prep_table = SQLModel.metadata.tables["visit_prep"]

    with get_session() as session:
        with pytest.raises(DBAPIError, match="seven calendar years"):
            with session.begin_nested():
                session.execute(
                    update(prep_table)
                    .where(prep_table.c.id == fixture.prep_id)
                    .values(
                        **_direct_confirmation_values(
                            retain_until=expected + timedelta(microseconds=1),
                        )
                    )
                )


def test_retention_trigger_accepts_february_29_calendar_anniversary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduled_at = datetime(2032, 2, 29, 15, 0, tzinfo=UTC)
    fixture = _fixture(
        monkeypatch,
        "retention-leap-day",
        scheduled_at=scheduled_at,
    )
    expected = datetime(2039, 2, 28, 15, 0, tzinfo=UTC)
    prep_table = SQLModel.metadata.tables["visit_prep"]

    with get_session() as session:
        session.execute(
            update(prep_table)
            .where(prep_table.c.id == fixture.prep_id)
            .values(**_direct_confirmation_values(retain_until=expected))
        )
        stored = session.execute(
            select(prep_table.c.retain_until).where(prep_table.c.id == fixture.prep_id)
        ).scalar_one()

    assert stored == expected


def test_reschedule_after_confirmation_preserves_retention_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(monkeypatch, "retention-reschedule")
    _confirm(fixture)
    original_retention = fixture.scheduled_at.replace(
        year=fixture.scheduled_at.year + 7
    )
    rescheduled_at = fixture.scheduled_at + timedelta(days=45)

    rescheduled = reschedule_visit_prep(
        owner_practice_member_id=fixture.owner.id,
        owner_doctor_id=fixture.doctor.id,
        practice_id=fixture.practice.id,
        visit_prep_id=fixture.prep_id,
        expected_schedule_generation=1,
        expected_credential_generation=1,
        scheduled_at=rescheduled_at,
        timezone_name="UTC",
    )

    assert rescheduled.access_token is not None
    resumed = interviews.get_interview(access_token=rescheduled.access_token)
    assert resumed.scheduled_at == rescheduled_at
    with get_session() as session:
        prep = session.exec(
            select(VisitPrep).where(VisitPrep.id == fixture.prep_id)
        ).one()
        assert prep.retain_until == original_retention


def test_reschedule_before_confirmation_uses_current_schedule_for_retention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(monkeypatch, "retention-reschedule-before")
    rescheduled_at = fixture.scheduled_at + timedelta(days=45)
    rescheduled = reschedule_visit_prep(
        owner_practice_member_id=fixture.owner.id,
        owner_doctor_id=fixture.doctor.id,
        practice_id=fixture.practice.id,
        visit_prep_id=fixture.prep_id,
        expected_schedule_generation=1,
        expected_credential_generation=1,
        scheduled_at=rescheduled_at,
        timezone_name="UTC",
    )
    assert rescheduled.access_token is not None

    interviews.confirm_interview(
        access_token=rescheduled.access_token,
        first_name="Jos\N{LATIN SMALL LETTER E WITH ACUTE}",
        role="patient",
    )

    with get_session() as session:
        prep = session.exec(
            select(VisitPrep).where(VisitPrep.id == fixture.prep_id)
        ).one()
        assert prep.retain_until == rescheduled_at.replace(year=rescheduled_at.year + 7)


@pytest.mark.parametrize(
    ("first_name", "role"),
    [
        ("Jose", "patient"),
        ("Jos\N{LATIN SMALL LETTER E WITH ACUTE}", "caregiver"),
        ("Jos-e\N{LATIN SMALL LETTER E WITH ACUTE}", "patient"),
    ],
)
def test_confirmation_mismatch_is_generic_and_does_not_start_retention(
    monkeypatch: pytest.MonkeyPatch,
    first_name: str,
    role: str,
) -> None:
    fixture = _fixture(monkeypatch, f"mismatch-{secrets.token_hex(2)}")

    with pytest.raises(VisitPrepInterviewUnavailable):
        interviews.confirm_interview(
            access_token=fixture.access_token,
            first_name=first_name,
            role=cast(Any, role),
        )

    with get_session() as session:
        prep = session.exec(
            select(VisitPrep).where(VisitPrep.id == fixture.prep_id)
        ).one()
        assert prep.lifecycle_state == "invited"
        assert prep.confirmed_at is None
        assert prep.retention_policy_version is None
        assert prep.retain_until is None


def test_hidden_conditional_flow_encrypts_answers_and_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(monkeypatch, "hidden")
    _confirm(fixture)

    first_key = str(uuid4())
    first = _respond(
        fixture,
        "reason",
        answer="  Headache\r\nfor two days  ",
        key=first_key,
    )
    assert first.disposition == "accepted"
    assert first.interview.current_question is not None
    assert first.interview.current_question.key == "medications"

    replay = _respond(
        fixture,
        "reason",
        answer="Headache\nfor two days",
        key=first_key,
    )
    assert replay.disposition == "replayed"
    assert replay.interview.current_question is not None
    assert replay.interview.current_question.key == "medications"

    with pytest.raises(VisitPrepInterviewConflict):
        _respond(fixture, "reason", answer="Different", key=first_key)

    _respond(fixture, "medications")
    adherence = _respond(fixture, "adherence", answer="yes")
    assert adherence.interview.current_question is not None
    assert adherence.interview.current_question.key == "story"
    ready = _respond(fixture, "story")
    assert ready.interview.status == "ready_to_complete"
    assert ready.interview.submitted_questions == 4

    completed = interviews.complete_interview(access_token=fixture.access_token)
    assert completed.interview.status == "completed"
    repeated = interviews.complete_interview(access_token=fixture.access_token)
    assert repeated.interview.status == "completed"

    # Exact response replay remains successful after completion.
    final_replay = _respond(
        fixture,
        "reason",
        answer="Headache\nfor two days",
        key=first_key,
    )
    assert final_replay.disposition == "replayed"
    assert final_replay.interview.status == "completed"

    with get_session() as session:
        rows = list(
            session.exec(
                select(VisitPrepQuestionResponse)
                .where(VisitPrepQuestionResponse.visit_prep_id == fixture.prep_id)
                .order_by(cast(Any, VisitPrepQuestionResponse.submission_sequence))
            ).all()
        )
        assert len(rows) == 4
        assert [row.submission_sequence for row in rows] == [1, 2, 3, 4]
        assert all(row.credential_generation == 1 for row in rows)
        assert rows[0].answer_ciphertext is not None
        assert "Headache" not in rows[0].answer_ciphertext
        assert json.loads(decrypt_value(rows[0].answer_ciphertext)) == {
            "kind": "story",
            "value": "Headache\nfor two days",
            "version": 1,
        }
        assert rows[1].response_kind == "skipped"
        assert rows[1].answer_ciphertext is None
        assert rows[1].answer_format_version is None


def test_response_hmac_is_scoped_to_one_visit_prep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _fixture(monkeypatch, "hmac-first")
    second = _fixture(monkeypatch, "hmac-second")
    monkeypatch.setattr(
        interviews.visit_prep_access,
        "VISIT_PREP_SETTINGS",
        VisitPrepSettings(
            enabled=True,
            public_api_enabled=True,
            enabled_doctor_ids=frozenset({str(first.doctor.id), str(second.doctor.id)}),
        ),
    )
    _confirm(first)
    _confirm(second)
    shared_key = str(uuid4())
    _respond(first, "reason", answer="Same answer", key=shared_key)
    _respond(second, "reason", answer="Same answer", key=shared_key)

    with get_session() as session:
        rows = list(
            session.exec(
                select(VisitPrepQuestionResponse)
                .where(
                    VisitPrepQuestionResponse.visit_prep_id.in_(
                        [first.prep_id, second.prep_id]
                    )
                )
                .order_by(cast(Any, VisitPrepQuestionResponse.visit_prep_id))
            ).all()
        )

    assert len(rows) == 2
    assert rows[0].payload_hmac_sha256 != rows[1].payload_hmac_sha256


def test_conditional_follow_up_is_required_for_nonadherence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(monkeypatch, "conditional")
    _confirm(fixture)
    _respond(fixture, "reason", answer="Follow-up")
    _respond(fixture, "medications", answer="Medicine A")
    adherence = _respond(fixture, "adherence", answer="no")
    assert adherence.interview.current_question is not None
    assert adherence.interview.current_question.key == "adherence_barrier"

    barrier = _respond(fixture, "adherence_barrier", answer="Side effects")
    assert barrier.interview.current_question is not None
    assert barrier.interview.current_question.key == "story"
    ready = _respond(fixture, "story", answer="Everything else")
    assert ready.interview.status == "ready_to_complete"
    assert ready.interview.submitted_questions == 5


def test_caregiver_can_complete_an_all_skipped_interview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(
        monkeypatch,
        "caregiver-skips",
        recipient_role="caregiver",
    )
    confirmed = interviews.confirm_interview(
        access_token=fixture.access_token,
        first_name="Jos\N{LATIN SMALL LETTER E WITH ACUTE}",
        role="caregiver",
    )
    assert confirmed.interview.status == "in_progress"

    for question_key in ("reason", "medications", "adherence"):
        _respond(fixture, question_key)
    result = _respond(fixture, "story")

    assert result.interview.status == "ready_to_complete"
    assert result.interview.submitted_questions == 4
    completed = interviews.complete_interview(access_token=fixture.access_token)
    assert completed.interview.status == "completed"
    assert completed.interview.submitted_questions == 4


def test_response_validation_and_forward_only_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(monkeypatch, "validation")
    _confirm(fixture)

    with pytest.raises(VisitPrepInterviewConflict):
        _respond(fixture, "medications", answer="Too soon")
    with pytest.raises(VisitPrepInterviewValidation):
        _respond(fixture, "reason", answer="bad\x00answer")
    with pytest.raises(VisitPrepInterviewValidation):
        _respond(fixture, "reason", answer="x" * 2_001)
    _respond(fixture, "reason", answer="Reason")
    _respond(fixture, "medications", answer="None")
    with pytest.raises(VisitPrepInterviewValidation):
        _respond(fixture, "adherence", answer="YES")
    with pytest.raises(VisitPrepInterviewValidation):
        _respond(fixture, "adherence", answer=" yes ")


def test_rotation_resumes_progress_but_old_token_immediately_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(monkeypatch, "rotation")
    _confirm(fixture)
    first_idempotency_key = str(uuid4())
    first_response = ResponseInput(
        question_key="reason",
        response_kind="answered",
        answer="Reason",
    )
    interviews.submit_response(
        access_token=fixture.access_token,
        idempotency_key=first_idempotency_key,
        response=first_response,
    )

    rotated = rotate_visit_prep_credential(
        owner_practice_member_id=fixture.owner.id,
        owner_doctor_id=fixture.doctor.id,
        practice_id=fixture.practice.id,
        visit_prep_id=fixture.prep_id,
        expected_credential_generation=1,
    )
    assert rotated.access_token is not None
    with pytest.raises(VisitPrepInterviewUnavailable):
        interviews.get_interview(access_token=fixture.access_token)

    resumed = interviews.get_interview(access_token=rotated.access_token)
    assert resumed.status == "in_progress"
    assert resumed.current_question is not None
    assert resumed.current_question.key == "medications"
    replayed = interviews.submit_response(
        access_token=rotated.access_token,
        idempotency_key=first_idempotency_key,
        response=first_response,
    )
    assert replayed.disposition == "replayed"
    interviews.submit_response(
        access_token=rotated.access_token,
        idempotency_key=str(uuid4()),
        response=ResponseInput(
            question_key="medications",
            response_kind="skipped",
        ),
    )
    with get_session() as session:
        rows = list(
            session.exec(
                select(VisitPrepQuestionResponse)
                .where(VisitPrepQuestionResponse.visit_prep_id == fixture.prep_id)
                .order_by(cast(Any, VisitPrepQuestionResponse.submission_sequence))
            ).all()
        )
        assert [row.credential_generation for row in rows] == [1, 2]


def test_revocation_immediately_blocks_patient_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(monkeypatch, "revocation")
    _confirm(fixture)
    revoked = revoke_visit_prep(
        owner_practice_member_id=fixture.owner.id,
        owner_doctor_id=fixture.doctor.id,
        practice_id=fixture.practice.id,
        visit_prep_id=fixture.prep_id,
        expected_credential_generation=1,
        reason="physician_revoked",
    )
    assert revoked.disposition == "revoked"

    with pytest.raises(VisitPrepInterviewUnavailable):
        _respond(fixture, "reason", answer="Must not be stored")
    with get_session() as session:
        responses = session.exec(
            select(VisitPrepQuestionResponse).where(
                VisitPrepQuestionResponse.visit_prep_id == fixture.prep_id
            )
        ).all()
    assert responses == []


def test_every_request_rechecks_the_exact_rollout_cohort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(monkeypatch, "cohort")
    _confirm(fixture)
    monkeypatch.setattr(
        interviews.visit_prep_access,
        "VISIT_PREP_SETTINGS",
        VisitPrepSettings(enabled=True, public_api_enabled=True),
    )

    with pytest.raises(VisitPrepInterviewUnavailable):
        interviews.get_interview(access_token=fixture.access_token)


@pytest.mark.parametrize(
    "gate",
    ["membership", "baa", "entitlement", "inactive", "ended", "attestation", "doctor"],
)
def test_every_response_rechecks_practice_safety_gates_before_writing(
    monkeypatch: pytest.MonkeyPatch,
    gate: str,
) -> None:
    fixture = _fixture(monkeypatch, f"gate-{gate}")
    _confirm(fixture)
    now = datetime.now(UTC)

    with get_session() as session:
        if gate in {"inactive", "ended"}:
            from app.models.practice import lock_practice_seats

            lock_practice_seats(session, fixture.practice.id)
            table = SQLModel.metadata.tables["practice_member"]
            session.execute(
                update(table)
                .where(table.c.id == fixture.owner.id)
                .values(
                    seat_state="inactive",
                    access_ends_at=now - timedelta(seconds=1)
                    if gate == "ended"
                    else None,
                )
            )
        elif gate == "attestation":
            table = SQLModel.metadata.tables["practice_member"]
            session.execute(
                update(table)
                .where(table.c.id == fixture.owner.id)
                .values(attested_at=None, attestation_name=None)
            )
        elif gate == "doctor":
            table = SQLModel.metadata.tables["doctor"]
            session.execute(
                update(table)
                .where(table.c.id == fixture.doctor.id)
                .values(deleted_at=now)
            )
        elif gate == "membership":
            table = SQLModel.metadata.tables["practice_member"]
            session.execute(
                update(table)
                .where(table.c.id == fixture.owner.id)
                .values(
                    access_revoked_at=now,
                    access_revoked_by_ref="test:visit-prep-offboarding",
                    access_revocation_reason="Synthetic offboarding",
                    access_records_retain_until=now + timedelta(days=365 * 8),
                )
            )
        elif gate == "baa":
            table = SQLModel.metadata.tables["practice"]
            session.execute(
                update(table)
                .where(table.c.id == fixture.practice.id)
                .values(negotiated_baa_on_file=False)
            )
        else:
            table = SQLModel.metadata.tables["practice"]
            session.execute(
                update(table)
                .where(table.c.id == fixture.practice.id)
                .values(billing_exempt=False)
            )
        session.commit()

    with pytest.raises(VisitPrepInterviewUnavailable):
        _respond(fixture, "reason", answer="Must not be stored")
    with get_session() as session:
        responses = session.exec(
            select(VisitPrepQuestionResponse).where(
                VisitPrepQuestionResponse.visit_prep_id == fixture.prep_id
            )
        ).all()
    assert responses == []


def test_unknown_or_noncanonical_token_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixture(monkeypatch, "unknown")

    unknown = "vp1_" + "A" * 43
    with pytest.raises(VisitPrepInterviewUnavailable):
        interviews.get_interview(access_token=unknown)
    with pytest.raises(VisitPrepInterviewUnavailable):
        interviews.get_interview(access_token="not-a-token")


def test_completion_requires_every_currently_applicable_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(monkeypatch, "incomplete")
    _confirm(fixture)
    with pytest.raises(VisitPrepInterviewConflict):
        interviews.complete_interview(access_token=fixture.access_token)
    _respond(fixture, "reason")
    with pytest.raises(VisitPrepInterviewConflict):
        interviews.complete_interview(access_token=fixture.access_token)


def test_completed_state_with_missing_responses_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(monkeypatch, "corrupt-completed")
    _confirm(fixture)
    prep_table = SQLModel.metadata.tables["visit_prep"]
    started_at = datetime.now(UTC)

    with get_session() as session:
        session.execute(
            update(prep_table)
            .where(prep_table.c.id == fixture.prep_id)
            .values(
                lifecycle_state="in_progress",
                started_at=started_at,
                updated_at=started_at,
            )
        )
        session.commit()
        session.execute(
            update(prep_table)
            .where(prep_table.c.id == fixture.prep_id)
            .values(
                lifecycle_state="completed",
                completed_at=started_at,
                updated_at=started_at,
            )
        )
        session.commit()

    with pytest.raises(VisitPrepInterviewUnavailable):
        interviews.get_interview(access_token=fixture.access_token)


def test_out_of_order_persisted_response_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(monkeypatch, "corrupt-order")
    _confirm(fixture)
    _respond(fixture, "reason", answer="Reason")
    original_responses = interviews._responses

    def out_of_order(
        session: Any,
        *,
        visit_prep: VisitPrep,
    ) -> list[VisitPrepQuestionResponse]:
        responses = original_responses(session, visit_prep=visit_prep)
        final_question = session.exec(
            select(VisitPrepQuestion).where(
                VisitPrepQuestion.question_set_version_id
                == visit_prep.question_set_version_id,
                VisitPrepQuestion.question_key == "story",
            )
        ).one()
        return [responses[0].model_copy(update={"question_id": final_question.id})]

    monkeypatch.setattr(interviews, "_responses", out_of_order)

    with pytest.raises(VisitPrepInterviewUnavailable):
        interviews.get_interview(access_token=fixture.access_token)


def test_persisted_response_hmac_mismatch_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(monkeypatch, "corrupt-hmac")
    _confirm(fixture)
    _respond(fixture, "reason", answer="Reason")
    original_responses = interviews._responses

    def corrupted_hmac(
        session: Any,
        *,
        visit_prep: VisitPrep,
    ) -> list[VisitPrepQuestionResponse]:
        responses = original_responses(session, visit_prep=visit_prep)
        return [responses[0].model_copy(update={"payload_hmac_sha256": "0" * 64})]

    monkeypatch.setattr(interviews, "_responses", corrupted_hmac)

    with pytest.raises(VisitPrepInterviewUnavailable):
        interviews.get_interview(access_token=fixture.access_token)


def test_corrupt_condition_answer_fails_closed_without_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(monkeypatch, "corrupt")
    _confirm(fixture)
    _respond(fixture, "reason", answer="Reason")
    _respond(fixture, "medications", answer="Medicine")
    _respond(fixture, "adherence", answer="no")

    def fail_decrypt(_ciphertext: str) -> str:
        raise DecryptionError

    monkeypatch.setattr(interviews, "decrypt_value", fail_decrypt)

    with pytest.raises(VisitPrepInterviewUnavailable):
        interviews.get_interview(access_token=fixture.access_token)


def test_corrupt_recipient_snapshot_fails_closed_before_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(monkeypatch, "corrupt-recipient")

    def fail_decrypt(_ciphertext: str) -> str:
        raise DecryptionError

    monkeypatch.setattr(interviews, "decrypt_value", fail_decrypt)

    with pytest.raises(VisitPrepInterviewUnavailable):
        _confirm(fixture)
    with get_session() as session:
        prep = session.exec(
            select(VisitPrep).where(VisitPrep.id == fixture.prep_id)
        ).one()
        assert prep.lifecycle_state == "invited"
        assert prep.confirmed_at is None
        assert prep.retain_until is None


def test_patient_service_never_manufactures_a_chart_and_final_topic_is_open_ended(
    monkeypatch,
):
    fixture = _fixture(monkeypatch, "no-chart-story")
    _confirm(fixture)
    for key in ("reason", "medications", "adherence"):
        _respond(fixture, key)
    resumed = interviews.get_interview(access_token=fixture.access_token)
    assert resumed.current_question is not None
    assert resumed.current_question.key == "story"
    assert resumed.current_question.kind == "story"
    assert resumed.current_question.choices == ()
    with get_session() as session:
        prep = session.exec(
            select(VisitPrep).where(VisitPrep.id == fixture.prep_id)
        ).one()
        assert prep.patient_id is None and prep.appointment_id is None


def test_nonstring_role_and_response_kind_are_bounded_validation_errors(monkeypatch):
    fixture = _fixture(monkeypatch, "typed-input")
    with pytest.raises(VisitPrepInterviewValidation):
        interviews.confirm_interview(
            access_token=fixture.access_token, first_name="José", role=cast(Any, [])
        )
    with pytest.raises(VisitPrepInterviewValidation):
        interviews.submit_response(
            access_token=fixture.access_token,
            idempotency_key=str(uuid4()),
            response=ResponseInput("reason", cast(Any, [])),
        )


@pytest.mark.parametrize("version", [True, 1.0])
def test_corrupt_envelope_version_never_masquerades_as_integer_v1(monkeypatch, version):
    fixture = _fixture(monkeypatch, "envelope-version")
    monkeypatch.setattr(
        interviews,
        "decrypt_value",
        lambda _: json.dumps(
            {"first_name": "José", "role": "patient", "version": version}
        ),
    )
    with pytest.raises(VisitPrepInterviewUnavailable):
        _confirm(fixture)
