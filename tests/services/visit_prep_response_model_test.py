"""PostgreSQL invariants for dormant Visit Prep answer storage."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import pytest

from app.services.visit_prep_question_sets import ensure_default_question_set
from app.services.visit_preps import RecipientInput, create_visit_prep
from app.utils.encryption import decrypt_value, encrypt_value

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember
from app.models.visit_prep import VisitPrep
from app.models.visit_prep_response import VisitPrepQuestionResponse
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlmodel import SQLModel

from tests.factories import save_practice_member


@dataclass(frozen=True)
class _Scope:
    doctor: Doctor
    practice: Practice
    owner: PracticeMember
    visit_prep_id: TypeIDType
    question_set_version_id: TypeIDType
    question_ids: tuple[TypeIDType, ...]
    credential_generation: int
    scheduled_at: datetime


def _scope(
    label: str,
    *,
    now: datetime | None = None,
    scheduled_at: datetime | None = None,
) -> _Scope:
    effective_at = now or datetime.now(UTC) - timedelta(seconds=5)
    effective_schedule = scheduled_at or effective_at + timedelta(days=7)
    nonce = secrets.token_hex(5)
    doctor = Doctor(
        clerk_id=f"user_visit_prep_response_{label}_{nonce}",
        email=f"visit-prep-response-{label}-{nonce}@example.test",
        biography="Synthetic Visit Prep response test account.",
    ).save()
    practice = Practice(
        legal_name=f"Synthetic Visit Prep Response {label} {nonce}",
        join_code=f"R{secrets.token_hex(4).upper()[:7]}",
        email_domain="example.test",
        negotiated_baa_on_file=True,
        negotiated_baa_effective_date=effective_at - timedelta(days=1),
        negotiated_baa_reference=f"test:visit-prep-response:{label}:{nonce}",
        billing_exempt=True,
    ).save()
    owner = save_practice_member(
        PracticeMember(
            practice_id=practice.id,
            doctor_id=doctor.id,
            role="owner",
            attested_at=effective_at - timedelta(days=1),
            attestation_name="Synthetic Physician",
        )
    )
    seeded = ensure_default_question_set(
        owner_practice_member_id=owner.id,
        now=datetime.now(UTC) + timedelta(seconds=1),
    )
    created = create_visit_prep(
        owner_practice_member_id=owner.id,
        owner_doctor_id=doctor.id,
        practice_id=practice.id,
        idempotency_key=str(uuid4()),
        recipient=RecipientInput(
            first_name="Synthetic",
            role="patient",
            email="synthetic-response@example.com",
        ),
        physician_display_name="Dr. Synthetic",
        scheduled_at=effective_schedule,
        timezone_name="UTC",
        question_set_version_id=seeded.version.id,
        now=effective_at,
    )
    return _Scope(
        doctor=doctor,
        practice=practice,
        owner=owner,
        visit_prep_id=created.visit_prep.id,
        question_set_version_id=seeded.version.id,
        question_ids=tuple(question.id for question in seeded.version.questions),
        credential_generation=created.visit_prep.credential_generation,
        scheduled_at=effective_schedule,
    )


def _start(scope: _Scope, *, at: datetime | None = None) -> None:
    started_at = at or datetime.now(UTC) - timedelta(seconds=1)
    confirmed_at = started_at - timedelta(seconds=1)
    with get_session() as session:
        visit_prep = session.execute(
            select(VisitPrep)
            .where(cast(Any, VisitPrep.id) == scope.visit_prep_id)
            .with_for_update()
        ).scalar_one()
        visit_prep.lifecycle_state = "confirmed"
        visit_prep.confirmed_at = confirmed_at
        visit_prep.confirmed_by_role = "patient"
        visit_prep.retention_policy_version = "clinical.v1"
        visit_prep.retain_until = scope.scheduled_at.replace(
            year=scope.scheduled_at.year + 7
        )
        session.add(visit_prep)
        session.flush()
        visit_prep.lifecycle_state = "in_progress"
        visit_prep.started_at = started_at
        session.add(visit_prep)
        session.commit()


def _response(
    scope: _Scope,
    *,
    question_index: int,
    sequence: int,
    kind: str = "answered",
    idempotency_key: str | None = None,
    credential_generation: int | None = None,
) -> VisitPrepQuestionResponse:
    answer = json.dumps(
        {"kind": "story", "value": "Synthetic clinical answer"},
        separators=(",", ":"),
        sort_keys=True,
    )
    return VisitPrepQuestionResponse(
        visit_prep_id=scope.visit_prep_id,
        owner_practice_member_id=scope.owner.id,
        question_set_version_id=scope.question_set_version_id,
        question_id=scope.question_ids[question_index],
        credential_generation=(credential_generation or scope.credential_generation),
        submission_sequence=sequence,
        response_kind=kind,
        answer_ciphertext=encrypt_value(answer) if kind == "answered" else None,
        answer_format_version=1 if kind == "answered" else None,
        idempotency_key=idempotency_key or str(uuid4()),
        payload_hmac_sha256="b" * 64,
    )


def _reject_insert(instance: VisitPrepQuestionResponse, match: str) -> None:
    with get_session() as session:
        with pytest.raises(DBAPIError, match=match):
            with session.begin_nested():
                session.add(instance)
                session.flush()


def test_answer_and_skip_are_private_explicit_and_append_only() -> None:
    scope = _scope("private")
    _start(scope)
    answer = _response(scope, question_index=0, sequence=1).save()
    skipped = _response(
        scope,
        question_index=1,
        sequence=2,
        kind="skipped",
    ).save()

    assert answer.recorded_at is not None
    assert answer.created_at == answer.recorded_at
    assert skipped.answer_ciphertext is None
    assert skipped.answer_format_version is None
    assert "answer_ciphertext" not in answer.model_dump()
    assert "payload_hmac_sha256" not in answer.model_dump()

    with get_session() as session:
        stored = (
            session.execute(
                text(
                    "SELECT answer_ciphertext, payload_hmac_sha256 "
                    "FROM visit_prep_question_response WHERE id = :id"
                ),
                {"id": answer.id.uuid},
            )
            .mappings()
            .one()
        )
    assert "Synthetic clinical answer" not in stored["answer_ciphertext"]
    assert json.loads(decrypt_value(stored["answer_ciphertext"])) == {
        "kind": "story",
        "value": "Synthetic clinical answer",
    }
    assert stored["payload_hmac_sha256"] == "b" * 64

    table = SQLModel.metadata.tables["visit_prep_question_response"]
    with get_session() as session:
        with pytest.raises(DBAPIError, match="responses are immutable"):
            with session.begin_nested():
                session.execute(
                    update(table)
                    .where(table.c.id == answer.id)
                    .values(response_kind="skipped")
                )
        with pytest.raises(DBAPIError, match="aggregate cleanup"):
            with session.begin_nested():
                session.execute(delete(table).where(table.c.id == answer.id))


def test_shape_lineage_and_active_credential_are_database_fenced() -> None:
    scope = _scope("fences")
    other = _scope("other-lineage")
    _start(scope)

    malformed_skip = _response(
        scope,
        question_index=0,
        sequence=1,
        kind="skipped",
    )
    malformed_skip.answer_ciphertext = encrypt_value("hidden")
    malformed_skip.answer_format_version = 1
    _reject_insert(malformed_skip, "answer_shape")

    missing_answer_format = _response(scope, question_index=0, sequence=1)
    missing_answer_format.answer_format_version = None
    _reject_insert(missing_answer_format, "answer_shape")

    wrong_question = _response(scope, question_index=0, sequence=1)
    wrong_question.question_id = other.question_ids[0]
    _reject_insert(wrong_question, "fk_visit_prep_response_question_lineage")

    wrong_version = _response(scope, question_index=0, sequence=1)
    wrong_version.question_set_version_id = other.question_set_version_id
    _reject_insert(wrong_version, "response root lineage does not exist")

    stale_credential = _response(
        scope,
        question_index=0,
        sequence=1,
        credential_generation=scope.credential_generation + 1,
    )
    _reject_insert(stale_credential, "requires the current active credential")


def test_response_order_question_and_idempotency_are_prep_scoped() -> None:
    scope = _scope("ordering")
    _start(scope)
    key = str(uuid4())
    _response(
        scope,
        question_index=0,
        sequence=1,
        idempotency_key=key,
    ).save()

    _reject_insert(
        _response(scope, question_index=1, sequence=1),
        "response sequence is not current",
    )
    _reject_insert(
        _response(scope, question_index=0, sequence=2),
        "uq_visit_prep_response_question",
    )
    _reject_insert(
        _response(
            scope,
            question_index=1,
            sequence=2,
            idempotency_key=key,
        ),
        "uq_visit_prep_response_idempotency",
    )

    accepted = _response(scope, question_index=1, sequence=2).save()
    assert accepted.submission_sequence == 2


def test_inactive_and_expired_preps_cannot_accept_responses() -> None:
    invited = _scope("invited")
    _reject_insert(
        _response(invited, question_index=0, sequence=1),
        "inactive Visit Prep",
    )

    old = datetime.now(UTC) - timedelta(days=11)
    expired = _scope("expired", now=old)
    _start(expired, at=old + timedelta(seconds=2))
    _reject_insert(
        _response(expired, question_index=0, sequence=1),
        "current active credential",
    )


def test_response_deletes_only_with_approved_aggregate_cleanup() -> None:
    scope = _scope("cleanup", now=datetime.now(UTC) - timedelta(seconds=20))
    _start(scope, at=datetime.now(UTC) - timedelta(seconds=10))
    response = _response(scope, question_index=0, sequence=1).save()
    event_at = datetime.now(UTC) - timedelta(seconds=5)

    with get_session() as session:
        visit_prep = session.execute(
            select(VisitPrep)
            .where(cast(Any, VisitPrep.id) == scope.visit_prep_id)
            .with_for_update()
        ).scalar_one()
        visit_prep.lifecycle_state = "misdirected"
        visit_prep.revoked_at = event_at
        visit_prep.revoked_by_ref = "actor:synthetic-security-owner"
        visit_prep.revocation_reason = "wrong_recipient"
        visit_prep.misdirected_at = event_at
        visit_prep.misdirected_reason = "wrong_recipient"
        visit_prep.misdirected_incident_ref = "incident:synthetic"
        visit_prep.misdirected_disposition = "pending_review"
        session.add(visit_prep)
        session.commit()

    with get_session() as session:
        visit_prep = session.execute(
            select(VisitPrep)
            .where(cast(Any, VisitPrep.id) == scope.visit_prep_id)
            .with_for_update()
        ).scalar_one()
        disposition_at = event_at
        visit_prep.misdirected_disposition = "purge_approved"
        visit_prep.misdirected_disposition_at = disposition_at
        visit_prep.misdirected_disposition_by_ref = "actor:synthetic-security-owner"
        visit_prep.misdirected_disposition_until = disposition_at
        session.add(visit_prep)
        session.commit()

    with get_session() as session:
        visit_prep = session.execute(
            select(VisitPrep).where(cast(Any, VisitPrep.id) == scope.visit_prep_id)
        ).scalar_one()
        session.delete(visit_prep)
        session.commit()

    with get_session() as session:
        assert (
            session.execute(
                select(VisitPrepQuestionResponse).where(
                    cast(Any, VisitPrepQuestionResponse.id) == response.id
                )
            ).scalar_one_or_none()
            is None
        )
