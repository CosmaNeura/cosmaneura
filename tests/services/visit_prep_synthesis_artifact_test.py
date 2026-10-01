"""Owned synthetic PostgreSQL storage and explicit encrypted artifact boundaries."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from typeid import TypeID

import app.services.visit_prep_synthesis_payload as codec
from app.services.visit_prep_interviews import report_wrong_recipient
from app.services.visit_prep_synthesis_contract import (
    GENERATOR_VERSION,
    PROMPT_VERSION,
    VisitPrepSynthesisInvalid,
    select_patient_sources,
)
from app.services.visit_prep_synthesis_generator import MODEL_PROFILE
from app.services.visit_prep_synthesis_payload import (
    decrypt_synthesis_payload,
    encrypt_synthesis_payload,
)
from app.services.visit_preps import rotate_visit_prep_credential
from app.utils.encryption import (
    DecryptionError,
    EncryptionError,
    decrypt_value,
    encrypt_value,
)

from activemodel.session_manager import get_session
from app.models.visit_prep import (
    VisitPrep,
    VisitPrepCredentialGeneration,
    VisitPrepLegalHold,
    VisitPrepScheduleGeneration,
)
from app.models.visit_prep_synthesis import VisitPrepSynthesisArtifact
from sqlalchemy import delete, text, update
from sqlalchemy.exc import DBAPIError
from sqlmodel import SQLModel, select

from tests.services.visit_prep_contribution_commands_test import _correct, _review
from tests.services.visit_prep_contribution_projection_test import _prepared
from tests.services.visit_prep_model_test import _change, _credential, _prep, _schedule
from tests.services.visit_prep_packages_test import _read
from tests.services.visit_prep_synthesis_test import _brief


def _artifact(fixture, *, version=1, profile=MODEL_PROFILE):
    package = _read(fixture)
    selection = select_patient_sources(package)
    with get_session() as session:
        credential = session.exec(
            select(VisitPrepCredentialGeneration)
            .where(VisitPrepCredentialGeneration.visit_prep_id == fixture.prep_id)
            .order_by(VisitPrepCredentialGeneration.generation.desc())
        ).first()
        assert credential is not None
    return VisitPrepSynthesisArtifact(
        visit_prep_id=fixture.prep_id,
        owner_practice_member_id=fixture.owner.id,
        question_set_version_id=package.preparation.question_set_version_id,
        schedule_generation_id=credential.schedule_generation_id,
        credential_generation=credential.generation,
        version=version,
        source_version=selection.source_version,
        basis_response_count=selection.basis.original_response_count,
        basis_event_sequence=selection.basis.event_sequence,
        actor_role=selection.actor_role,
        original_interview_completed=selection.original_interview_completed,
        current_answers_complete=selection.current_answers_complete,
        current_answers_reviewed=selection.current_answers_reviewed,
        generator_version=GENERATOR_VERSION,
        prompt_version=PROMPT_VERSION,
        model_profile=profile,
        payload_ciphertext=encrypt_synthesis_payload(_brief(selection), selection),
        payload_format_version=1,
    )


def _reject(row, match):
    with get_session() as session:
        with pytest.raises(DBAPIError, match=match):
            with session.begin_nested():
                session.add(row)
                session.flush()


def test_unlinked_artifact_is_encrypted_explicitly_bound_and_server_timed(monkeypatch):
    fixture = _prepared(monkeypatch, "artifact")
    selection = select_patient_sources(_read(fixture))
    row = _artifact(fixture)
    row.created_at = row.recorded_at = datetime.now(UTC) + timedelta(days=10)
    before = datetime.now(UTC)
    saved = row.save()
    assert before <= saved.recorded_at <= datetime.now(UTC)
    assert saved.created_at == saved.updated_at == saved.recorded_at
    assert saved.id.prefix == "visit_prep_brief"
    assert "invented" not in saved.payload_ciphertext
    assert "payload_ciphertext" not in saved.model_dump()
    assert "invented" not in repr(saved)
    assert decrypt_synthesis_payload(saved.payload_ciphertext, selection) == _brief(
        selection
    )
    with get_session() as session:
        prep = session.get(VisitPrep, fixture.prep_id)
        assert (
            prep is not None and prep.patient_id is None and prep.appointment_id is None
        )


def test_artifacts_cannot_be_mutated_or_deleted_individually(monkeypatch):
    fixture = _prepared(monkeypatch, "artifact-immutable")
    row = _artifact(fixture).save()
    row_id = row.id
    table = SQLModel.metadata.tables["visit_prep_synthesis_artifact"]
    for statement, match in (
        (update(table).where(table.c.id == row.id).values(version=9), "immutable"),
        (delete(table).where(table.c.id == row.id), "aggregate cleanup"),
        (text("TRUNCATE public.visit_prep_synthesis_artifact"), "cannot"),
        (text("TRUNCATE public.visit_prep CASCADE"), "cannot"),
    ):
        with get_session() as session:
            with pytest.raises(DBAPIError, match=match):
                with session.begin_nested():
                    session.execute(statement)
    row.model_profile = "synthetic_changed"
    with get_session() as session:
        with pytest.raises(RuntimeError, match="immutable"):
            with session.begin_nested():
                session.add(row)
                session.flush()
    with get_session() as session:
        with pytest.raises(RuntimeError, match="aggregate cleanup"):
            with session.begin_nested():
                session.delete(session.get(VisitPrepSynthesisArtifact, row_id))
                session.flush()


def test_source_profile_and_sequential_versions_deduplicate_without_overwrite(
    monkeypatch,
):
    fixture = _prepared(monkeypatch, "artifact-dedup")
    first = _artifact(fixture).save()
    _reject(_artifact(fixture), "version is not next")
    _reject(_artifact(fixture, version=2), "uq_visit_prep_brief_source_profile")
    _reject(
        _artifact(fixture, version=3, profile="synthetic_alternative"),
        "version is not next",
    )
    second = _artifact(fixture, version=2, profile="synthetic_alternative").save()
    assert second.source_version == first.source_version and second.id != first.id
    with get_session() as session:
        assert (
            session.get(VisitPrepSynthesisArtifact, first.id).payload_ciphertext
            == first.payload_ciphertext
        )


def test_changed_patient_basis_requires_new_artifact_and_invalidates_old_codec(
    monkeypatch,
):
    fixture = _prepared(monkeypatch, "artifact-revision")
    first = _artifact(fixture).save()
    stale = _artifact(fixture, version=2)
    _correct(fixture, answer="A revised synthetic narrative.")
    _reject(stale, "basis is not current")
    selection = select_patient_sources(_read(fixture))
    with pytest.raises(VisitPrepSynthesisInvalid):
        decrypt_synthesis_payload(first.payload_ciphertext, selection)
    second = _artifact(fixture, version=2).save()
    assert second.source_version != first.source_version
    assert decrypt_synthesis_payload(second.payload_ciphertext, selection) == _brief(
        selection
    )
    with get_session() as session:
        assert session.get(VisitPrepSynthesisArtifact, first.id) is not None


def test_current_review_is_bound_but_never_claims_clinician_verification(monkeypatch):
    fixture = _prepared(monkeypatch, "artifact-review")
    fake = _artifact(fixture)
    fake.current_answers_reviewed = True
    _reject(fake, "review evidence is not current")
    _review(fixture)
    saved = _artifact(fixture).save()
    assert saved.current_answers_reviewed and saved.current_answers_complete
    _correct(fixture, question="adherence", answer="some")
    partial = _artifact(fixture, version=2).save()
    assert partial.original_interview_completed
    assert not partial.current_answers_complete and not partial.current_answers_reviewed


def test_rotation_and_quarantine_deny_stale_publication_and_preserve_prior_artifacts(
    monkeypatch,
):
    fixture = _prepared(monkeypatch, "artifact-rotate", complete=False)
    first = _artifact(fixture).save()
    stale = _artifact(fixture, version=2)
    rotate_visit_prep_credential(
        owner_practice_member_id=fixture.owner.id,
        owner_doctor_id=fixture.doctor.id,
        practice_id=fixture.practice.id,
        visit_prep_id=fixture.prep_id,
        expected_credential_generation=1,
    )
    _reject(stale, "context is not current")
    _artifact(fixture, version=2).save()
    other = _prepared(monkeypatch, "artifact-quarantine")
    pending = _artifact(other)
    report_wrong_recipient(access_token=other.access_token)
    _reject(pending, "inactive Visit Prep")
    with get_session() as session:
        assert session.get(VisitPrepSynthesisArtifact, first.id) is not None


@pytest.mark.parametrize(
    "field",
    ["owner_practice_member_id", "question_set_version_id", "schedule_generation_id"],
)
def test_wrong_root_or_schedule_lineage_is_denied(monkeypatch, field):
    first = _prepared(monkeypatch, "artifact-lineage-first")
    row = _artifact(first)
    second = _prepared(monkeypatch, "artifact-lineage-second")
    wrong = _artifact(second)
    setattr(row, field, getattr(wrong, field))
    _reject(row, "lineage does not exist|context is not current")


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("source_version", "private words", "source_version_shape"),
        ("model_profile", "private words", "model_profile_shape"),
        ("generator_version", "invalid-version", "generation_versions_shape"),
        ("prompt_version", "", "generation_versions_shape"),
        ("payload_ciphertext", "", "payload_shape"),
        ("payload_ciphertext", "x" * 65537, "payload_shape"),
        ("payload_format_version", 2, "payload_shape"),
        ("actor_role", "caregiver", "attribution or completion changed"),
        ("original_interview_completed", False, "attribution or completion changed"),
        ("basis_response_count", 3, "basis is not current"),
    ],
)
def test_storage_rejects_invalid_shapes(monkeypatch, field, value, match):
    fixture = _prepared(monkeypatch, "artifact-invalid")
    row = _artifact(fixture)
    setattr(row, field, value)
    _reject(row, match)


def test_ciphertext_cannot_be_reused_for_another_preparation_or_claimed_review(
    monkeypatch,
):
    fixture = _prepared(monkeypatch, "artifact-codec")
    selection = select_patient_sources(_read(fixture))
    ciphertext = encrypt_synthesis_payload(_brief(selection), selection)
    for changed in (
        replace(selection, package_id=str(TypeID("visit_prep"))),
        replace(selection, current_answers_reviewed=True),
        replace(selection, source_version="b" * 64),
        replace(
            selection,
            sources=(
                replace(selection.sources[0], answer="Other invented words."),
                *selection.sources[1:],
            ),
        ),
    ):
        with pytest.raises(VisitPrepSynthesisInvalid) as error:
            decrypt_synthesis_payload(ciphertext, changed)
        assert (
            str(error.value) == "Visit Prep synthesis is invalid"
            and error.value.__cause__ is None
        )


@pytest.mark.parametrize(
    "change", ["manifest", "quote", "unknown", "shape", "json", "oversize"]
)
def test_corrupt_or_unbounded_envelopes_fail_without_content_disclosure(
    monkeypatch, change
):
    fixture = _prepared(monkeypatch, "artifact-corrupt")
    selection = select_patient_sources(_read(fixture))
    encrypted = encrypt_synthesis_payload(_brief(selection), selection)
    payload = json.loads(decrypt_value(encrypted))
    if change == "manifest":
        payload["manifest"]["current_answers_complete"] = 1
    elif change == "quote":
        payload["brief"]["headline"]["evidence"][0]["quote"] = "Invented absent text"
    elif change == "unknown":
        payload["private_unsupported"] = "Synthetic private detail"
    elif change == "shape":
        payload = []
    plaintext = "not-json" if change == "json" else json.dumps(payload)
    if change == "oversize":
        plaintext = "x" * 47001
    with pytest.raises(VisitPrepSynthesisInvalid) as error:
        decrypt_synthesis_payload(encrypt_value(plaintext), selection)
    assert (
        str(error.value) == "Visit Prep synthesis is invalid"
        and error.value.__cause__ is None
    )


def test_encryption_and_retired_key_failures_never_expose_payload(monkeypatch):
    fixture = _prepared(monkeypatch, "artifact-keys")
    selection = select_patient_sources(_read(fixture))
    ciphertext = encrypt_synthesis_payload(_brief(selection), selection)

    def encrypt_failure(_value):
        raise EncryptionError()

    monkeypatch.setattr(codec, "encrypt_value", encrypt_failure)
    with pytest.raises(VisitPrepSynthesisInvalid):
        encrypt_synthesis_payload(_brief(selection), selection)

    def decrypt_failure(_value):
        raise DecryptionError("unknown_key")

    monkeypatch.setattr(codec, "decrypt_value", decrypt_failure)
    with pytest.raises(VisitPrepSynthesisInvalid) as error:
        decrypt_synthesis_payload(ciphertext, selection)
    assert error.value.__cause__ is None and "unknown_key" not in str(error.value)


def test_legal_hold_and_parent_retention_control_aggregate_only_cleanup():
    now = datetime.now(UTC)
    created, scheduled = (
        now.replace(year=2010),
        now.replace(year=2010) + timedelta(days=1),
    )
    prep = _prep(created=created).save()
    schedule = _schedule(prep, scheduled).save()
    credential = _credential(prep, schedule, issued=created).save()
    _change(
        prep,
        lifecycle_state="confirmed",
        confirmed_at=created + timedelta(hours=1),
        confirmed_by_role="patient",
        retention_policy_version="clinical.v1",
        retain_until=scheduled.replace(year=2017),
    )
    row = VisitPrepSynthesisArtifact(
        visit_prep_id=prep.id,
        owner_practice_member_id=prep.owner_practice_member_id,
        question_set_version_id=prep.question_set_version_id,
        schedule_generation_id=schedule.id,
        credential_generation=credential.generation,
        version=1,
        source_version="a" * 64,
        basis_response_count=0,
        basis_event_sequence=0,
        actor_role="patient",
        original_interview_completed=False,
        current_answers_complete=False,
        current_answers_reviewed=False,
        generator_version=GENERATOR_VERSION,
        prompt_version=PROMPT_VERSION,
        model_profile=MODEL_PROFILE,
        payload_ciphertext=encrypt_value(
            '{"synthetic":"historical empty storage fixture"}'
        ),
        payload_format_version=1,
    ).save()
    # Storage permits retained-owner synthesis after public credential expiry.
    assert credential.expires_at < now
    hold = VisitPrepLegalHold(
        visit_prep_id=prep.id,
        owner_practice_member_id=prep.owner_practice_member_id,
        generation=1,
        placed_at=now - timedelta(minutes=2),
        placed_by_ref="actor:synthetic",
        placement_reason="synthetic_hold",
    ).save()
    table = SQLModel.metadata.tables["visit_prep"]
    with get_session() as session:
        with pytest.raises(DBAPIError, match="active legal hold"):
            with session.begin_nested():
                session.execute(delete(table).where(table.c.id == prep.id))
    _change(
        hold,
        released_at=now - timedelta(minutes=1),
        released_by_ref="actor:synthetic",
        release_reason="synthetic_release",
    )
    with get_session() as session:
        session.execute(delete(table).where(table.c.id == prep.id))
        session.flush()
        assert session.get(VisitPrepSynthesisArtifact, row.id) is None
        assert session.get(VisitPrepScheduleGeneration, schedule.id) is None
