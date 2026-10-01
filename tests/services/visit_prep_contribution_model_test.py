"""Real PostgreSQL invariants for dormant attributable contribution history."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

import pytest

from app.services import visit_prep_interviews as interviews
from app.services.visit_preps import rotate_visit_prep_credential
from app.utils.encryption import decrypt_value, encrypt_value

from activemodel.session_manager import get_session
from app.models.visit_prep import VisitPrep
from app.models.visit_prep_contribution import VisitPrepContributionEvent
from app.models.visit_prep_response import VisitPrepQuestionResponse
from sqlalchemy import delete, text, update
from sqlalchemy.exc import DBAPIError
from sqlmodel import SQLModel, select

from tests.services.visit_prep_interviews_test import _fixture, _respond


def _contribution_fixture(
    monkeypatch,
    label="contribution",
    *,
    role: Literal["patient", "caregiver"] = "patient",
):
    fixture = _fixture(monkeypatch, label, recipient_role=role)
    interviews.confirm_interview(
        access_token=fixture.access_token,
        first_name="Jos\N{LATIN SMALL LETTER E WITH ACUTE}",
        role=role,
    )
    _respond(fixture, "reason", answer="My original invented visit reason.")
    return fixture


def _event(fixture, *, basis=0, responses=1, kind="correction", role="patient"):
    with get_session() as session:
        prep = session.get(VisitPrep, fixture.prep_id)
        assert prep is not None
        response = session.exec(
            select(VisitPrepQuestionResponse).where(
                VisitPrepQuestionResponse.visit_prep_id == fixture.prep_id,
                VisitPrepQuestionResponse.submission_sequence == 1,
            )
        ).one()
    envelope = json.dumps(
        {"kind": "story", "value": "My corrected invented reason.", "version": 1}
    )
    return VisitPrepContributionEvent(
        visit_prep_id=fixture.prep_id,
        owner_practice_member_id=fixture.owner.id,
        question_set_version_id=prep.question_set_version_id,
        credential_generation=1,
        actor_role=role,
        event_kind=kind,
        event_sequence=basis + 1,
        basis_event_sequence=basis,
        basis_response_count=responses,
        response_id=response.id if kind == "correction" else None,
        response_kind="answered" if kind == "correction" else None,
        answer_ciphertext=encrypt_value(envelope) if kind == "correction" else None,
        answer_format_version=1 if kind == "correction" else None,
        idempotency_key=str(uuid4()),
        payload_hmac_sha256="a" * 64,
    )


def _reject(instance, match):
    with get_session() as session:
        with pytest.raises(DBAPIError, match=match):
            with session.begin_nested():
                session.add(instance)
                session.flush()


def test_original_words_remain_encrypted_and_immutable_after_correction(monkeypatch):
    fixture = _contribution_fixture(monkeypatch)
    with get_session() as session:
        original = session.exec(
            select(VisitPrepQuestionResponse).where(
                VisitPrepQuestionResponse.visit_prep_id == fixture.prep_id
            )
        ).one()
        original_ciphertext = original.answer_ciphertext
        original_hmac = original.payload_hmac_sha256
    correction = _event(fixture).save()
    assert correction.answer_ciphertext is not None
    assert "corrected invented" not in correction.answer_ciphertext
    assert json.loads(decrypt_value(correction.answer_ciphertext))["value"] == (
        "My corrected invented reason."
    )
    assert "answer_ciphertext" not in correction.model_dump()
    assert "payload_hmac_sha256" not in correction.model_dump()
    assert "corrected invented" not in repr(correction)
    with get_session() as session:
        retained = session.get(VisitPrepQuestionResponse, original.id)
        assert retained is not None
        assert retained.answer_ciphertext == original_ciphertext
        assert retained.payload_hmac_sha256 == original_hmac
    table = SQLModel.metadata.tables["visit_prep_contribution_event"]
    for statement, message in (
        (
            update(table)
            .where(table.c.id == correction.id)
            .values(actor_role="caregiver"),
            "immutable",
        ),
        (delete(table).where(table.c.id == correction.id), "aggregate cleanup"),
    ):
        with get_session() as session:
            with pytest.raises(DBAPIError, match=message):
                with session.begin_nested():
                    session.execute(statement)
    correction.actor_role = "caregiver"
    with get_session() as session:
        with pytest.raises(RuntimeError, match="append-only"):
            with session.begin_nested():
                session.add(correction)
                session.flush()


def test_review_binds_exact_response_count_and_prior_event_sequence(monkeypatch):
    fixture = _contribution_fixture(monkeypatch, "review-basis")
    correction = _event(fixture).save()
    review = _event(fixture, basis=1, kind="review").save()
    assert review.basis_event_sequence == correction.event_sequence
    assert review.response_id is None and review.answer_ciphertext is None
    _reject(_event(fixture, basis=1, kind="review"), "basis is not current")
    _respond(fixture, "medications", answer="An invented medication report.")
    _reject(_event(fixture, basis=2, kind="review"), "basis is not current")
    next_review = _event(fixture, basis=2, responses=2, kind="review").save()
    assert next_review.basis_response_count == 2
    # A new correction advances the history without rewriting the old review.
    later = _event(fixture, basis=3, responses=2).save()
    assert later.event_sequence != next_review.event_sequence
    with get_session() as session:
        assert session.get(VisitPrepContributionEvent, review.id) is not None


def test_caregiver_attribution_must_match_original_confirmation(monkeypatch):
    fixture = _contribution_fixture(monkeypatch, "caregiver", role="caregiver")
    _reject(_event(fixture), "role does not match confirmation")
    event = _event(fixture, role="caregiver").save()
    assert event.actor_role == "caregiver"


def test_correction_cannot_target_another_preparation_or_owner(monkeypatch):
    first = _contribution_fixture(monkeypatch, "lineage-first")
    second = _contribution_fixture(monkeypatch, "lineage-second")
    wrong_response = _event(first)
    wrong_response.response_id = _event(second).response_id
    _reject(wrong_response, "correction response lineage does not exist")
    wrong_owner = _event(first)
    wrong_owner.owner_practice_member_id = second.owner.id
    _reject(wrong_owner, "root lineage does not exist")
    wrong_version = _event(first)
    wrong_version.question_set_version_id = _event(second).question_set_version_id
    _reject(wrong_version, "root lineage does not exist")


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"response_kind": None}, "event_shape"),
        ({"answer_format_version": None}, "event_shape"),
        ({"response_kind": "skipped"}, "event_shape"),
        ({"answer_ciphertext": ""}, "event_shape"),
        ({"event_kind": "review"}, "event_shape"),
        ({"event_kind": "agent_review"}, "event_kind_allowed"),
        ({"event_sequence": 0}, "basis is not current"),
        ({"basis_response_count": 0}, "basis is not current"),
        ({"idempotency_key": "private text"}, "idempotency_key_shape"),
        ({"payload_hmac_sha256": "unkeyed"}, "payload_hmac_shape"),
    ],
)
def test_malformed_shapes_are_rejected_without_weakening_original_storage(
    monkeypatch, changes, match
):
    fixture = _contribution_fixture(monkeypatch, "shape")
    event = _event(fixture)
    for key, value in changes.items():
        setattr(event, key, value)
    _reject(event, match)


def test_skipped_correction_has_no_clinical_envelope(monkeypatch):
    fixture = _contribution_fixture(monkeypatch, "correct-to-skip")
    event = _event(fixture)
    event.response_kind = "skipped"
    event.answer_ciphertext = event.answer_format_version = None
    saved = event.save()
    assert saved.answer_ciphertext is None and saved.response_kind == "skipped"


def test_rotation_invalidates_old_authority_but_keeps_history(monkeypatch):
    fixture = _contribution_fixture(monkeypatch, "rotation")
    original = _event(fixture).save()
    rotate_visit_prep_credential(
        owner_practice_member_id=fixture.owner.id,
        owner_doctor_id=fixture.doctor.id,
        practice_id=fixture.practice.id,
        visit_prep_id=fixture.prep_id,
        expected_credential_generation=1,
    )
    _reject(_event(fixture, basis=1), "current active credential")
    next_event = _event(fixture, basis=1)
    next_event.credential_generation = 2
    next_event.save()
    with get_session() as session:
        assert session.get(VisitPrepContributionEvent, original.id) is not None


def test_wrong_recipient_quarantine_closes_corrections_and_review(monkeypatch):
    fixture = _contribution_fixture(monkeypatch, "quarantine")
    original = _event(fixture).save()
    interviews.report_wrong_recipient(access_token=fixture.access_token)
    _reject(_event(fixture, basis=1), "inactive Visit Prep")
    _reject(_event(fixture, basis=1, kind="review"), "inactive Visit Prep")
    with get_session() as session:
        assert session.get(VisitPrepContributionEvent, original.id) is not None


def test_completed_interview_can_record_later_attributable_correction(monkeypatch):
    fixture = _contribution_fixture(monkeypatch, "after-completion")
    for key in ("medications", "adherence", "story"):
        _respond(fixture, key)
    interviews.complete_interview(access_token=fixture.access_token)
    event = _event(fixture, responses=4).save()
    assert event.event_sequence == 1
    with get_session() as session:
        prep = session.get(VisitPrep, fixture.prep_id)
        assert prep is not None and prep.completed_at is not None
        assert prep.lifecycle_state == "completed"


def test_event_time_is_server_owned_and_duplicate_keys_are_prep_scoped(monkeypatch):
    fixture = _contribution_fixture(monkeypatch, "clock-and-key")
    event = _event(fixture)
    event.created_at = event.recorded_at = datetime.now(UTC) + timedelta(days=10)
    before = datetime.now(UTC)
    saved = event.save()
    assert saved.recorded_at is not None
    assert before <= saved.recorded_at <= datetime.now(UTC)
    assert saved.created_at == saved.updated_at == saved.recorded_at
    duplicate = _event(fixture, basis=1)
    duplicate.idempotency_key = saved.idempotency_key
    _reject(duplicate, "uq_visit_prep_contribution_key")


def test_truncate_and_premature_cleanup_cannot_bypass_retention(monkeypatch):
    fixture = _contribution_fixture(monkeypatch, "retention")
    event = _event(fixture).save()
    root_table = SQLModel.metadata.tables["visit_prep"]
    for statement in (
        text("TRUNCATE public.visit_prep_contribution_event"),
        text("TRUNCATE public.visit_prep CASCADE"),
        delete(root_table).where(root_table.c.id == fixture.prep_id),
    ):
        with get_session() as session:
            with pytest.raises(DBAPIError):
                with session.begin_nested():
                    session.execute(statement)
    with get_session() as session:
        assert session.get(VisitPrepContributionEvent, event.id) is not None
