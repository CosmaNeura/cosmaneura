"""Actual owner authority, source revalidation and encrypted brief publication."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

import app.services.visit_prep_synthesis_publication as publication
from app.services import visit_prep_access
from app.services.visit_prep_interviews import (
    complete_interview,
    confirm_interview,
    report_wrong_recipient,
)
from app.services.visit_prep_synthesis_contract import (
    VisitPrepSynthesisInvalid,
    select_patient_sources,
)
from app.services.visit_prep_synthesis_payload import decrypt_synthesis_payload
from app.services.visit_prep_synthesis_publication import (
    VisitPrepSynthesisStale,
    get_visit_prep_synthesis,
    publish_visit_prep_synthesis,
    read_visit_prep_synthesis_in_session,
)
from app.services.visit_preps import (
    VisitPrepNotFound,
    reschedule_visit_prep,
    rotate_visit_prep_credential,
)
from app.utils.encryption import EncryptionError

from activemodel.session_manager import get_session
from app.models.authority import AuthorityAuditEvent
from app.models.doctor import Doctor
from app.models.visit_prep_synthesis import VisitPrepSynthesisArtifact
from sqlmodel import col, select

from tests.services.visit_prep_contribution_commands_test import _correct, _review
from tests.services.visit_prep_contribution_projection_test import _prepared
from tests.services.visit_prep_interviews_test import _fixture, _respond
from tests.services.visit_prep_packages_test import _read, _scope
from tests.services.visit_prep_synthesis_artifact_test import _artifact
from tests.services.visit_prep_synthesis_test import _brief
from tests.services.visit_preps_test import _withdraw_seat


def _selected(fixture):
    return select_patient_sources(_read(fixture))


def _publish(fixture, selection=None, brief=None):
    selection = selection or _selected(fixture)
    return publish_visit_prep_synthesis(
        **_scope(fixture),
        visit_prep_id=fixture.prep_id,
        expected_selection=selection,
        prepared=brief or _brief(selection),
    )


def _view(fixture):
    return get_visit_prep_synthesis(**_scope(fixture), visit_prep_id=fixture.prep_id)


def _staged_view(fixture):
    with get_session() as session:
        return read_visit_prep_synthesis_in_session(
            session, **_scope(fixture), visit_prep_id=fixture.prep_id
        )


def _rows(fixture):
    with get_session() as session:
        return session.exec(
            select(VisitPrepSynthesisArtifact)
            .where(VisitPrepSynthesisArtifact.visit_prep_id == fixture.prep_id)
            .order_by(col(VisitPrepSynthesisArtifact.version))
        ).all()


def _audits(fixture):
    with get_session() as session:
        return session.exec(
            select(AuthorityAuditEvent).where(
                AuthorityAuditEvent.resource_ref == str(fixture.prep_id),
                col(AuthorityAuditEvent.event_type).in_(
                    ["visit_prep.synthesis_published", "visit_prep.synthesis_read"]
                ),
            )
        ).all()


@pytest.mark.parametrize("role,complete", [("patient", True), ("caregiver", False)])
def test_publish_and_read_bind_current_sources_without_a_chart(
    monkeypatch, role, complete
):
    fixture = _prepared(monkeypatch, "publication", role=role, complete=complete)
    selection = _selected(fixture)
    missing = _view(fixture)
    assert missing.status == "missing" and missing.prepared is None
    receipt = _publish(fixture, selection)
    assert receipt.disposition == "published" and receipt.version == 1
    view = _view(fixture)
    assert view.status == "ready" and view.artifact_id == receipt.artifact_id
    assert view.version == 1 and view.recorded_at is not None
    assert view.recorded_at.tzinfo is not None
    assert view.prepared == _brief(selection)
    assert view.package.source_version == selection.source_version
    assert view.package.preparation.patient_id is None
    assert view.package.preparation.appointment_id is None
    assert view.package.contribution.actor_role == role
    assert view.package.contribution.current_answers_complete == complete
    assert not view.package.contribution.current_answers_reviewed
    row = _rows(fixture)[0]
    assert "invented" not in row.payload_ciphertext
    assert decrypt_synthesis_payload(row.payload_ciphertext, selection) == view.prepared
    assert "invented" not in repr(view) and "invented" not in repr(receipt)
    audits = _audits(fixture)
    assert len(audits) == 3
    assert all(event.request_digest == selection.source_version for event in audits)
    assert all(fixture.access_token not in repr(event) for event in audits)


def test_retry_reuses_first_valid_artifact_and_never_overwrites_model_output(
    monkeypatch,
):
    fixture = _prepared(monkeypatch, "publication-replay")
    selection = _selected(fixture)
    first = _publish(fixture, selection)
    alternate = _brief(selection).model_copy(update={"visit_priorities": []})
    second = _publish(fixture, selection, alternate)
    assert second.disposition == "replayed" and second.artifact_id == first.artifact_id
    assert len(_rows(fixture)) == 1
    assert _view(fixture).prepared == _brief(selection)


@pytest.mark.parametrize(
    "change", ["correction", "review", "rotation", "reschedule", "completion"]
)
def test_later_source_change_rejects_old_output_and_never_discloses_it_as_current(
    monkeypatch, change
):
    fixture = _prepared(
        monkeypatch,
        "publication-stale",
        complete=change not in {"completion", "rotation", "reschedule"},
    )
    original = _selected(fixture)
    first = _publish(fixture, original)
    ciphertext = _rows(fixture)[0].payload_ciphertext
    if change == "correction":
        _correct(fixture)
    elif change == "review":
        _review(fixture)
    elif change == "rotation":
        rotate_visit_prep_credential(
            owner_practice_member_id=fixture.owner.id,
            owner_doctor_id=fixture.doctor.id,
            practice_id=fixture.practice.id,
            visit_prep_id=fixture.prep_id,
            expected_credential_generation=1,
        )
    elif change == "reschedule":
        reschedule_visit_prep(
            owner_practice_member_id=fixture.owner.id,
            owner_doctor_id=fixture.doctor.id,
            practice_id=fixture.practice.id,
            visit_prep_id=fixture.prep_id,
            expected_credential_generation=1,
            expected_schedule_generation=1,
            scheduled_at=fixture.scheduled_at + timedelta(days=1),
            timezone_name="UTC",
        )
    else:
        _respond(fixture, "medications")
        _respond(fixture, "adherence", answer="yes")
        _respond(fixture, "story", answer="Synthetic later completed story.")
        complete_interview(access_token=fixture.access_token)
    with pytest.raises(VisitPrepSynthesisStale):
        _publish(fixture, original)
    before_decrypt = publication.decrypt_synthesis_payload

    def stale_must_not_decrypt(*_args, **_kwargs):
        raise AssertionError("stale artifact was decrypted")

    monkeypatch.setattr(
        publication, "decrypt_synthesis_payload", stale_must_not_decrypt
    )
    stale = _view(fixture)
    assert stale.status == "stale" and stale.prepared is None
    assert (
        stale.artifact_id is None
        and stale.version is None
        and stale.recorded_at is None
    )
    monkeypatch.setattr(publication, "decrypt_synthesis_payload", before_decrypt)
    second = _publish(fixture)
    assert second.version == first.version + 1
    assert _view(fixture).status == "ready"
    rows = _rows(fixture)
    assert rows[0].payload_ciphertext == ciphertext and len(rows) == 2


@pytest.mark.parametrize("change", ["wording", "role", "package", "source_version"])
def test_caller_cannot_launder_changed_selection_even_with_unchanged_basis(
    monkeypatch, change
):
    fixture = _prepared(monkeypatch, "publication-forged")
    selected = _selected(fixture)
    if change == "wording":
        forged = replace(
            selected,
            sources=(
                replace(selected.sources[0], answer="Forged synthetic wording."),
                *selected.sources[1:],
            ),
        )
    elif change == "role":
        forged = replace(selected, actor_role="caregiver")
    elif change == "package":
        from typeid import TypeID

        forged = replace(selected, package_id=str(TypeID("visit_prep")))
    else:
        forged = replace(selected, source_version="0" * 64)
    with pytest.raises(VisitPrepSynthesisStale):
        _publish(fixture, forged)
    assert not _rows(fixture) and not _audits(fixture)


@pytest.mark.parametrize("operation", ["publish", "read", "staged_read"])
@pytest.mark.parametrize("denial", ["gate", "baa", "seat", "identity", "quarantine"])
def test_current_denial_prevents_artifact_write_or_decryption(
    monkeypatch, operation, denial
):
    fixture = _prepared(monkeypatch, "publication-denial")
    selected = _selected(fixture)
    _publish(fixture, selected)
    initial_audits = len(_audits(fixture))
    if denial == "gate":
        monkeypatch.setattr(
            visit_prep_access,
            "VISIT_PREP_SETTINGS",
            replace(visit_prep_access.VISIT_PREP_SETTINGS, enabled=False),
        )
    elif denial == "baa":
        fixture.practice.negotiated_baa_on_file = False
        fixture.practice.save()
    elif denial == "seat":
        _withdraw_seat(fixture.owner)
    elif denial == "identity":
        with get_session() as session:
            doctor = session.get(Doctor, fixture.doctor.id)
            assert doctor is not None
            doctor.clerk_id = "user_synthetic_rebound_identity"
            session.add(doctor)
            session.commit()
    else:
        report_wrong_recipient(access_token=fixture.access_token)
    monkeypatch.setattr(
        publication,
        "decrypt_synthesis_payload",
        lambda *_args: pytest.fail("unauthorized artifact decryption"),
    )
    with pytest.raises(VisitPrepNotFound):
        if operation == "publish":
            _publish(fixture, selected)
        elif operation == "staged_read":
            _staged_view(fixture)
        else:
            _view(fixture)
    assert len(_rows(fixture)) == 1 and len(_audits(fixture)) == initial_audits


def test_another_owner_cannot_publish_or_read_even_an_existing_valid_artifact(
    monkeypatch,
):
    first = _prepared(monkeypatch, "publication-owner-first")
    selected = _selected(first)
    _publish(first, selected)
    second = _prepared(monkeypatch, "publication-owner-second")
    with pytest.raises(VisitPrepNotFound):
        get_visit_prep_synthesis(
            owner_practice_member_id=second.owner.id,
            owner_doctor_id=second.doctor.id,
            practice_id=second.practice.id,
            expected_clerk_subject=second.doctor.clerk_id,
            visit_prep_id=first.prep_id,
        )
    with pytest.raises(VisitPrepNotFound):
        publish_visit_prep_synthesis(
            owner_practice_member_id=second.owner.id,
            owner_doctor_id=second.doctor.id,
            practice_id=second.practice.id,
            expected_clerk_subject=second.doctor.clerk_id,
            visit_prep_id=first.prep_id,
            expected_selection=selected,
            prepared=_brief(selected),
        )


def test_all_skipped_input_publishes_empty_brief_not_invented_information(monkeypatch):
    fixture = _fixture(monkeypatch, "publication-empty")
    confirm_interview(
        access_token=fixture.access_token, first_name="José", role="patient"
    )
    for key in ("reason", "medications", "adherence", "story"):
        _respond(fixture, key)
    complete_interview(access_token=fixture.access_token)
    selected = _selected(fixture)
    assert not selected.sources
    _publish(fixture, selected)
    view = _view(fixture)
    assert view.status == "ready" and view.prepared is not None
    assert not view.prepared.items()
    assert view.package.contribution.current_answers_complete
    assert not view.package.contribution.current_answers_reviewed


def test_other_generation_profile_is_stale_and_its_payload_is_never_decrypted(
    monkeypatch,
):
    fixture = _prepared(monkeypatch, "publication-profile")
    _artifact(fixture, profile="synthetic_old_model_profile").save()
    monkeypatch.setattr(
        publication,
        "decrypt_synthesis_payload",
        lambda *_args: pytest.fail("old profile decryption"),
    )
    assert _view(fixture).status == "stale"


@pytest.mark.parametrize("field", ["generator_version", "prompt_version"])
def test_other_generator_or_prompt_version_is_not_a_current_brief(monkeypatch, field):
    fixture = _prepared(monkeypatch, "publication-other-version")
    row = _artifact(fixture)
    setattr(row, field, "synthetic_previous_v1")
    row.save()
    monkeypatch.setattr(
        publication,
        "decrypt_synthesis_payload",
        lambda *_args: pytest.fail("previous generation version decryption"),
    )
    assert _view(fixture).status == "stale"


def test_false_current_metadata_is_rejected_even_when_storage_accepts_it(monkeypatch):
    fixture = _prepared(monkeypatch, "publication-metadata")
    selected = _selected(fixture)
    row = _artifact(fixture)
    row.current_answers_complete = False
    row.save()
    for operation in (lambda: _view(fixture), lambda: _publish(fixture, selected)):
        with pytest.raises(VisitPrepSynthesisInvalid):
            operation()
    assert not _audits(fixture) and len(_rows(fixture)) == 1


def test_retained_owner_publish_and_read_ignore_public_gate_and_invitation_expiry(
    monkeypatch,
):
    fixture = _prepared(monkeypatch, "publication-retained")
    selected = _selected(fixture)
    import app.services.visit_prep_packages as packages

    package = _read(fixture)
    future = package.preparation.credential_expires_at + timedelta(hours=1)
    monkeypatch.setattr(packages, "_database_now", lambda _session: future)
    monkeypatch.setattr(
        visit_prep_access,
        "VISIT_PREP_SETTINGS",
        replace(visit_prep_access.VISIT_PREP_SETTINGS, public_api_enabled=False),
    )
    _publish(fixture, selected)
    view = _view(fixture)
    assert view.status == "ready" and not view.package.preparation.credential_active
    assert view.package.source_version == selected.source_version


def test_corrupt_current_artifact_fails_closed_on_read_and_retry(monkeypatch):
    fixture = _prepared(monkeypatch, "publication-corrupt")
    selected = _selected(fixture)
    row = _artifact(fixture)
    row.payload_ciphertext = "synthetic_invalid_encrypted_envelope"
    row.save()
    for operation in (lambda: _view(fixture), lambda: _publish(fixture, selected)):
        with pytest.raises(
            VisitPrepSynthesisInvalid, match="^Visit Prep synthesis is invalid$"
        ):
            operation()
    assert not _audits(fixture) and len(_rows(fixture)) == 1


def test_invalid_generated_brief_is_not_saved_or_audited(monkeypatch):
    fixture = _prepared(monkeypatch, "publication-invalid")
    selected = _selected(fixture)
    invalid = _brief(selected).model_copy(update={"summary": []})
    with pytest.raises(VisitPrepSynthesisInvalid):
        _publish(fixture, selected, invalid)
    assert not _rows(fixture) and not _audits(fixture)


@pytest.mark.parametrize("operation", ["publish", "read"])
def test_no_result_before_audit_commit_and_publication_rolls_back_with_it(
    monkeypatch, operation
):
    fixture = _prepared(monkeypatch, "publication-audit-failure")
    selected = _selected(fixture)
    if operation == "read":
        _publish(fixture, selected)
    count = len(_audits(fixture))

    def fail_commit(session):
        session.rollback()
        raise RuntimeError("synthetic audit commit failure")

    monkeypatch.setattr(publication, "_commit", fail_commit)
    with pytest.raises(RuntimeError, match="synthetic audit commit failure"):
        _publish(fixture, selected) if operation == "publish" else _view(fixture)
    assert len(_rows(fixture)) == (1 if operation == "read" else 0)
    assert len(_audits(fixture)) == count


def test_encryption_failure_exposes_no_payload_and_saves_nothing(monkeypatch):
    fixture = _prepared(monkeypatch, "publication-encryption")
    selected = _selected(fixture)
    import app.services.visit_prep_synthesis_payload as codec

    def fail(_value):
        raise EncryptionError()

    monkeypatch.setattr(codec, "encrypt_value", fail)
    with pytest.raises(VisitPrepSynthesisInvalid) as error:
        _publish(fixture, selected)
    assert "payload" not in str(error.value) and error.value.__cause__ is None
    assert not _rows(fixture) and not _audits(fixture)


@pytest.mark.parametrize("operation", ["publish", "read"])
def test_final_database_time_recheck_denies_expired_entitlement(monkeypatch, operation):
    fixture = _prepared(monkeypatch, "publication-time")
    selected = _selected(fixture)
    if operation == "read":
        _publish(fixture, selected)
    prior_audits = len(_audits(fixture))
    original = publication._database_now
    # Loading sources has succeeded; publication's final clock must still
    # observe expired entitlement rather than its earlier snapshot.
    future = datetime.now(UTC) + timedelta(days=1)
    fixture.practice.billing_exempt_until = future - timedelta(seconds=1)
    fixture.practice.save()
    monkeypatch.setattr(publication, "_database_now", lambda _session: future)
    with pytest.raises(VisitPrepNotFound):
        _publish(fixture, selected) if operation == "publish" else _view(fixture)
    monkeypatch.setattr(publication, "_database_now", original)
    assert len(_rows(fixture)) == (1 if operation == "read" else 0)
    assert len(_audits(fixture)) == prior_audits
