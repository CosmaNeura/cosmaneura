"""Real synthetic Core source/authority/lease boundaries; never calls a model."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from typeid import TypeID

import app.services.visit_prep_synthesis_builds as builds
from app.services import visit_prep_access
from app.services.visit_prep_interviews import report_wrong_recipient
from app.services.visit_prep_synthesis_builds import (
    VisitPrepBuildDiscarded,
    claim_visit_prep_synthesis,
    fail_visit_prep_synthesis_claim,
    load_claimed_synthesis_sources,
    request_visit_prep_synthesis,
)
from app.services.visit_prep_synthesis_publication import VisitPrepSynthesisStale
from app.services.visit_preps import VisitPrepNotFound, rotate_visit_prep_credential
from app.utils.encryption import EncryptionError, decrypt_value, encrypt_value

from activemodel.session_manager import get_session
from app.models.authority import AuthorityAuditEvent
from app.models.doctor import Doctor
from app.models.visit_prep_synthesis_build import VisitPrepSynthesisBuild
from sqlalchemy import delete, update
from sqlalchemy.exc import DBAPIError
from sqlmodel import SQLModel, select

from tests.services.visit_prep_contribution_commands_test import _correct, _review
from tests.services.visit_prep_contribution_projection_test import _prepared
from tests.services.visit_prep_packages_test import _scope
from tests.services.visit_prep_synthesis_publication_test import _publish, _selected
from tests.services.visit_preps_test import _withdraw_seat


def _request(fixture, source_version=None):
    return request_visit_prep_synthesis(
        **_scope(fixture),
        visit_prep_id=fixture.prep_id,
        expected_source_version=source_version or _selected(fixture).source_version,
    )


def _build(fixture):
    with get_session() as session:
        return session.exec(
            select(VisitPrepSynthesisBuild).where(
                VisitPrepSynthesisBuild.visit_prep_id == fixture.prep_id
            )
        ).one()


def _change_build(fixture, **values):
    with get_session() as session:
        row = session.get(VisitPrepSynthesisBuild, _build(fixture).id)
        for key, value in values.items():
            setattr(row, key, value)
        session.add(row)
        session.commit()


def _expire(fixture):
    _change_build(fixture, lease_until=datetime.now(UTC) - timedelta(seconds=1))


def _audits(fixture):
    with get_session() as session:
        return session.exec(
            select(AuthorityAuditEvent).where(
                AuthorityAuditEvent.resource_ref == str(fixture.prep_id),
                AuthorityAuditEvent.event_type.in_(
                    [
                        "visit_prep.synthesis_requested",
                        "visit_prep.synthesis_sources_selected",
                    ]
                ),
            )
        ).all()


@pytest.mark.parametrize("role,complete", [("patient", True), ("caregiver", False)])
def test_durable_queue_claim_current_sources_and_requester_privacy(
    monkeypatch, role, complete
):
    fixture = _prepared(monkeypatch, "build-current", role=role, complete=complete)
    selected = _selected(fixture)
    receipt = _request(fixture, selected.source_version)
    row = _build(fixture)
    assert receipt.state == "pending" and receipt.epoch == 1
    assert row.owner_practice_member_id == fixture.owner.id
    assert fixture.doctor.clerk_id not in row.requester_ciphertext
    assert fixture.access_token not in row.requester_ciphertext
    assert "invented" not in row.requester_ciphertext
    assert "requester_ciphertext" not in row.model_dump()
    assert fixture.doctor.clerk_id not in repr(row)
    decoded = json.loads(decrypt_value(row.requester_ciphertext))
    assert decoded["subject"] == fixture.doctor.clerk_id
    assert decoded["visit_prep_id"] == str(fixture.prep_id)
    before = datetime.now(UTC)
    claim = claim_visit_prep_synthesis()
    assert claim.build_id == receipt.build_id and claim.attempt == 1
    assert before < claim.lease_until <= datetime.now(UTC) + builds.LEASE
    assert claim_visit_prep_synthesis() is None
    loaded = load_claimed_synthesis_sources(claim)
    assert loaded == selected and loaded.actor_role == role
    assert loaded.current_answers_complete == complete
    assert _selected(fixture).package_id == str(fixture.prep_id)
    assert [event.event_type for event in _audits(fixture)] == [
        "visit_prep.synthesis_requested",
        "visit_prep.synthesis_sources_selected",
    ]


def test_same_source_retries_keep_epoch_requester_lease_attempts_and_backoff(
    monkeypatch,
):
    fixture = _prepared(monkeypatch, "build-replay")
    first = _request(fixture)
    row = _build(fixture)
    assert _request(fixture) == first
    assert _build(fixture).requester_ciphertext == row.requester_ciphertext
    claim = claim_visit_prep_synthesis()
    running = _build(fixture)
    assert _request(fixture).epoch == first.epoch
    assert _build(fixture).lease_until == running.lease_until
    assert _build(fixture).attempts == 1
    assert fail_visit_prep_synthesis_claim(claim, code="generation_failed")
    pending = _build(fixture)
    assert _request(fixture).epoch == first.epoch
    assert _build(fixture).retry_at == pending.retry_at
    assert _build(fixture).attempts == 1
    assert claim_visit_prep_synthesis() is None


def test_existing_current_artifact_needs_no_model_job_and_ready_retries_are_stable(
    monkeypatch,
):
    fixture = _prepared(monkeypatch, "build-ready")
    _publish(fixture)
    first = _request(fixture)
    assert first.state == "ready"
    assert _request(fixture) == first
    assert claim_visit_prep_synthesis() is None


@pytest.mark.parametrize("change", ["correction", "review", "rotation"])
def test_source_changes_before_execution_requeue_without_returning_old_answers(
    monkeypatch, change
):
    fixture = _prepared(monkeypatch, "build-superseded", complete=change != "rotation")
    receipt = _request(fixture)
    claim = claim_visit_prep_synthesis()
    if change == "correction":
        _correct(fixture)
    elif change == "review":
        _review(fixture)
    else:
        rotate_visit_prep_credential(
            **_scope(fixture, package=False),
            visit_prep_id=fixture.prep_id,
            expected_credential_generation=1,
        )
    with pytest.raises(VisitPrepBuildDiscarded):
        load_claimed_synthesis_sources(claim)
    row = _build(fixture)
    assert row.epoch == receipt.epoch + 1 and row.attempts == 0
    assert row.state == "pending" and row.lease_until is None
    assert row.source_version == _selected(fixture).source_version
    assert not fail_visit_prep_synthesis_claim(claim, code="generation_failed")
    next_claim = claim_visit_prep_synthesis()
    assert next_claim.attempt == 1 and next_claim.epoch == row.epoch
    assert load_claimed_synthesis_sources(next_claim) == _selected(fixture)


def test_new_owner_request_supersedes_running_job_and_stale_display_is_rejected(
    monkeypatch,
):
    fixture = _prepared(monkeypatch, "build-request-change")
    selected = _selected(fixture)
    first = _request(fixture)
    claim = claim_visit_prep_synthesis()
    _correct(fixture)
    with pytest.raises(VisitPrepSynthesisStale):
        _request(fixture, selected.source_version)
    assert _build(fixture).epoch == first.epoch
    next_request = _request(fixture)
    assert next_request.epoch == first.epoch + 1
    with pytest.raises(VisitPrepBuildDiscarded):
        load_claimed_synthesis_sources(claim)
    assert _build(fixture).state == "pending"


def test_expired_lease_reclaims_without_old_worker_releasing_or_reading(monkeypatch):
    fixture = _prepared(monkeypatch, "build-expired")
    _request(fixture)
    first = claim_visit_prep_synthesis()
    _expire(fixture)
    second = claim_visit_prep_synthesis()
    assert second.attempt == 2 and second.epoch == first.epoch
    assert second.lease_until != first.lease_until
    with pytest.raises(VisitPrepBuildDiscarded):
        load_claimed_synthesis_sources(first)
    assert not fail_visit_prep_synthesis_claim(first, code="generation_failed")
    assert _build(fixture).attempts == 2 and _build(fixture).state == "running"
    assert load_claimed_synthesis_sources(second) == _selected(fixture)


@pytest.mark.parametrize("mode", ["crashes", "failures"])
def test_three_attempt_bound_and_explicit_cooldown_recovery(monkeypatch, mode):
    fixture = _prepared(monkeypatch, "build-attempts")
    first = _request(fixture)
    for attempt in range(1, 4):
        claim = claim_visit_prep_synthesis()
        assert claim.attempt == attempt
        if mode == "crashes":
            _expire(fixture)
        else:
            assert fail_visit_prep_synthesis_claim(claim, code="generation_failed")
            if attempt < 3:
                _change_build(
                    fixture, retry_at=datetime.now(UTC) - timedelta(seconds=1)
                )
    assert claim_visit_prep_synthesis() is None
    failed = _build(fixture)
    assert failed.attempts == 3 and failed.state == "failed"
    assert failed.failure_code == (
        "worker_attempts_exhausted" if mode == "crashes" else "generation_failed"
    )
    assert _request(fixture).epoch == first.epoch
    _change_build(fixture, retry_at=datetime.now(UTC) - timedelta(seconds=1))
    recovered = _request(fixture)
    assert recovered.epoch == first.epoch + 1 and recovered.state == "pending"
    assert claim_visit_prep_synthesis().attempt == 1


@pytest.mark.parametrize(
    "denial", ["gate", "baa", "seat", "identity", "quarantine", "ciphertext"]
)
def test_current_denial_terminates_claim_without_source_audit(monkeypatch, denial):
    fixture = _prepared(monkeypatch, "build-denial")
    _request(fixture)
    claim = claim_visit_prep_synthesis()
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
            doctor.clerk_id = "user_synthetic_new_identity"
            session.add(doctor)
            session.commit()
    elif denial == "quarantine":
        report_wrong_recipient(access_token=fixture.access_token)
    else:
        _change_build(fixture, requester_ciphertext="corrupt_synthetic_envelope")
    with pytest.raises(VisitPrepBuildDiscarded) as error:
        load_claimed_synthesis_sources(claim)
    assert error.value.__cause__ is None
    assert _build(fixture).state == "failed"
    assert _build(fixture).failure_code == "scope_unavailable"
    assert len(_audits(fixture)) == 1


@pytest.mark.parametrize("field", ["visit_prep_id", "version", "subject", "extra"])
def test_encrypted_requester_schema_and_context_are_closed(monkeypatch, field):
    fixture = _prepared(monkeypatch, "build-envelope")
    _request(fixture)
    claim = claim_visit_prep_synthesis()
    payload = json.loads(decrypt_value(_build(fixture).requester_ciphertext))
    payload[field] = str(TypeID("visit_prep")) if field == "visit_prep_id" else True
    _change_build(fixture, requester_ciphertext=encrypt_value(json.dumps(payload)))
    with pytest.raises(VisitPrepBuildDiscarded):
        load_claimed_synthesis_sources(claim)
    assert _build(fixture).state == "failed"


def test_encryption_and_audit_failure_roll_back_queue_without_partial_work(monkeypatch):
    fixture = _prepared(monkeypatch, "build-rollback")
    source = _selected(fixture).source_version

    def fail(*_args, **_kwargs):
        raise EncryptionError()

    with monkeypatch.context() as patch:
        patch.setattr(builds, "encrypt_value", fail)
        with pytest.raises(EncryptionError):
            _request(fixture, source)
    with monkeypatch.context() as patch:
        patch.setattr(builds, "_audit", fail)
        with pytest.raises(EncryptionError):
            _request(fixture, source)
    with get_session() as session:
        assert not session.exec(
            select(VisitPrepSynthesisBuild).where(
                VisitPrepSynthesisBuild.visit_prep_id == fixture.prep_id
            )
        ).all()
    assert not _audits(fixture)


@pytest.mark.parametrize(
    "values",
    [
        {"epoch": 0},
        {"attempts": 4},
        {"attempts": 3, "state": "pending"},
        {"state": "running", "lease_until": None},
        {"state": "unknown"},
        {"requester_format_version": 2},
        {"source_version": "bad"},
        {"failure_code": "patient_text"},
        {"state": "failed", "failure_code": None},
    ],
)
def test_real_database_rejects_invalid_operational_states(monkeypatch, values):
    fixture = _prepared(monkeypatch, "build-constraints")
    _request(fixture)
    table = SQLModel.metadata.tables["visit_prep_synthesis_build"]
    with get_session() as session:
        with pytest.raises(DBAPIError):
            with session.begin_nested():
                session.execute(
                    update(table)
                    .where(table.c.id == _build(fixture).id)
                    .values(**values)
                )


def test_final_database_clock_expiry_prevents_source_disclosure_and_audit(monkeypatch):
    fixture = _prepared(monkeypatch, "build-final-expiry")
    _request(fixture)
    claim = claim_visit_prep_synthesis()
    original = builds._finish

    def expire_before_finish(*args, **kwargs):
        monkeypatch.setattr(builds, "_database_now", lambda _session: claim.lease_until)
        return original(*args, **kwargs)

    monkeypatch.setattr(builds, "_finish", expire_before_finish)
    with pytest.raises(VisitPrepBuildDiscarded):
        load_claimed_synthesis_sources(claim)
    assert len(_audits(fixture)) == 1
    assert _build(fixture).state == "running"


def test_wrong_owner_request_cannot_queue_another_providers_preparation(monkeypatch):
    fixture = _prepared(monkeypatch, "build-wrong-owner")
    source = _selected(fixture).source_version
    scope = _scope(fixture)
    scope["owner_practice_member_id"] = TypeID("practice_member")
    with pytest.raises(VisitPrepNotFound):
        request_visit_prep_synthesis(
            **scope, visit_prep_id=fixture.prep_id, expected_source_version=source
        )


def test_all_skipped_request_keeps_empty_current_sources_without_inventing_answers(
    monkeypatch,
):
    from app.services.visit_prep_interviews import complete_interview, confirm_interview

    from tests.services.visit_prep_interviews_test import _fixture, _respond

    fixture = _fixture(monkeypatch, "build-all-skipped")
    confirm_interview(
        access_token=fixture.access_token, first_name="José", role="patient"
    )
    for key in ("reason", "medications", "adherence", "story"):
        _respond(fixture, key)
    complete_interview(access_token=fixture.access_token)
    _request(fixture)
    selected = load_claimed_synthesis_sources(claim_visit_prep_synthesis())
    assert not selected.sources
    assert selected.current_answers_complete
    assert selected.skipped_question_keys == (
        "reason",
        "medications",
        "adherence",
        "story",
    )


def test_retained_owner_build_read_is_independent_of_expired_public_link(monkeypatch):
    import app.services.visit_prep_packages as packages

    from tests.services.visit_prep_packages_test import _read

    fixture = _prepared(monkeypatch, "build-retained")
    future = _read(fixture).preparation.credential_expires_at + timedelta(hours=1)
    # Advance the database-owned application clock, not immutable credentials.
    monkeypatch.setattr(packages, "_database_now", lambda _session: future)
    monkeypatch.setattr(builds, "_database_now", lambda _session: future)
    _request(fixture)
    assert load_claimed_synthesis_sources(claim_visit_prep_synthesis()) == _selected(
        fixture
    )


def test_generation_profile_change_does_not_claim_or_disclose_old_profile_job(
    monkeypatch,
):
    fixture = _prepared(monkeypatch, "build-profile")
    first = _request(fixture)
    claim = claim_visit_prep_synthesis()
    monkeypatch.setattr(builds, "MODEL_PROFILE", "synthetic_next_profile")
    with pytest.raises(VisitPrepBuildDiscarded):
        load_claimed_synthesis_sources(claim)
    assert claim_visit_prep_synthesis() is None
    second = _request(fixture)
    assert second.build_id != first.build_id
    assert claim_visit_prep_synthesis().build_id == second.build_id


def test_parent_legal_hold_and_retention_apply_to_build_cleanup():
    from app.models.visit_prep import VisitPrepLegalHold

    from tests.services.visit_prep_model_test import (
        _change,
        _credential,
        _prep,
        _schedule,
    )

    now = datetime.now(UTC)
    prep = _prep(created=now.replace(year=2010)).save()
    schedule = _schedule(prep, now.replace(year=2010) + timedelta(days=1)).save()
    _credential(prep, schedule, issued=now.replace(year=2010)).save()
    row = VisitPrepSynthesisBuild(
        visit_prep_id=prep.id,
        owner_practice_member_id=prep.owner_practice_member_id,
        question_set_version_id=prep.question_set_version_id,
        generator_version=builds.GENERATOR_VERSION,
        prompt_version=builds.PROMPT_VERSION,
        model_profile=builds.MODEL_PROFILE,
        source_version="a" * 64,
        requester_ciphertext=encrypt_value('{"synthetic":"historical queue fixture"}'),
        requested_at=now,
        retry_at=now,
    ).save()
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
        assert session.get(VisitPrepSynthesisBuild, row.id) is not None
    _change(
        hold,
        released_at=now - timedelta(minutes=1),
        released_by_ref="actor:synthetic",
        release_reason="synthetic_release",
    )
    with get_session() as session:
        session.execute(delete(table).where(table.c.id == prep.id))
        session.flush()
        assert session.get(VisitPrepSynthesisBuild, row.id) is None
