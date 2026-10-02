"""Real enqueue/worker/custody lifecycle using only an invented transport."""

import pickle
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

import app.services.visit_prep_email_worker as worker
from app.services.visit_prep_email_enqueue import enqueue_email_invitation
from app.services.visit_prep_email_worker import (
    EmailSubmissionClaim,
    claim_email_invitation,
    dispatch_email_claim,
    maintain_email_secrets,
    run_one_email,
)
from app.services.visit_preps import revoke_visit_prep, rotate_visit_prep_credential

from activemodel.session_manager import get_session, global_session
from app.models.authority import AuthorityAuditEvent
from sqlalchemy import text
from sqlmodel import select

from tests.services.visit_prep_email_enqueue_test import _ready, _rows
from tests.services.visit_prep_email_evidence_test import (
    _evidence,
    _store_approval,
    _store_consent,
)


def _queued(label="email-worker", *, expiry=None):
    fixture = _ready(label, expiry=expiry)
    enqueue_email_invitation(**fixture[3])
    return fixture


def _options(fixture):
    return {
        key: fixture[3][key]
        for key in ("patient_origin", "source_verifier", "settings")
    }


def _events(fixture):
    with get_session() as session:
        return list(
            session.exec(
                select(AuthorityAuditEvent).where(
                    AuthorityAuditEvent.client_event_id == fixture[3]["operation_id"]
                )
            ).all()
        )


def test_actual_enqueue_claim_dispatch_and_terminal_secret_cleanup():
    fixture = _queued()
    calls = []

    def transport(invitation):
        calls.append(invitation)
        (row,) = _rows(fixture)
        assert row.state == "submitting" and row.submitted_at is not None
        assert row.lease_until == row.submitted_at + timedelta(seconds=90)
        return "accepted"

    assert run_one_email(**_options(fixture), transport=transport) == "accepted"
    assert len(calls) == 1 and calls[0].access_token == fixture[3]["access_token"]
    assert calls[0].recipient == "patient@example.com"
    (row,) = _rows(fixture)
    assert (
        row.state == "accepted"
        and row.payload_ciphertext is None
        and row.lease_until is None
    )
    assert row.outcome_code is None
    assert run_one_email(**_options(fixture), transport=transport) == "idle"
    assert len(calls) == 1
    events = _events(fixture)
    assert {event.event_type for event in events} == {
        "visit_prep.email_invitation_queued",
        "visit_prep.email_submission_claimed",
        "visit_prep.email_accepted",
    }
    for event in events:
        assert fixture[3]["access_token"] not in event.model_dump_json()
        assert "patient@example.com" not in event.model_dump_json()


@pytest.mark.parametrize(
    "result", [None, True, 1, "delivered", "failed", {}, "accepted\n"]
)
def test_any_unrecognized_transport_result_is_uncertain_and_never_retried(result):
    fixture = _queued()
    calls = []

    def transport(invitation):
        calls.append(invitation)
        return result

    assert run_one_email(**_options(fixture), transport=transport) == "uncertain"
    (row,) = _rows(fixture)
    assert row.outcome_code == "submission_uncertain" and row.payload_ciphertext is None
    assert (
        run_one_email(**_options(fixture), transport=transport) == "idle"
        and len(calls) == 1
    )


@pytest.mark.parametrize("failure", [TimeoutError, ValueError, RuntimeError])
def test_transport_exception_is_uncertain_without_private_error_or_retry(failure):
    fixture = _queued()
    calls = []

    def transport(invitation):
        calls.append(invitation)
        raise failure(invitation.recipient + invitation.access_token)

    assert run_one_email(**_options(fixture), transport=transport) == "uncertain"
    assert (
        run_one_email(**_options(fixture), transport=transport) == "idle"
        and len(calls) == 1
    )
    assert _rows(fixture)[0].payload_ciphertext is None


@pytest.mark.parametrize(
    "change",
    [
        "enabled",
        "public_api_enabled",
        "email_send_enabled",
        "configuration_valid",
        "source",
    ],
)
def test_disabled_worker_does_not_claim_or_invoke_transport(change):
    fixture = _queued()
    options = _options(fixture)
    if change == "source":
        options["source_verifier"] = None
    else:
        options["settings"] = replace(options["settings"], **{change: False})
    calls = []

    def transport(invitation):
        calls.append(invitation)
        return "accepted"

    assert run_one_email(**options, transport=transport) == "disabled"
    assert calls == [] and _rows(fixture)[0].state == "queued"
    assert len(_events(fixture)) == 1


@pytest.mark.parametrize("stage", ["before_claim", "after_claim"])
@pytest.mark.parametrize(
    "change",
    [
        "consent",
        "approval",
        "baa",
        "revocation",
        "rotation",
        "cohort",
        "source",
        "origin",
    ],
)
def test_changed_authority_or_context_before_effect_prevents_submission(stage, change):
    fixture = _queued()
    options = _options(fixture)
    claim = claim_email_invitation(**options) if stage == "after_claim" else None
    scope, root, _, _ = fixture
    if change in {"consent", "approval"}:
        _, approval, consent = _evidence(root, sequence=2, kind="withdrawn")
        _store_consent(consent) if change == "consent" else _store_approval(approval)
    elif change == "baa":
        scope.practice.negotiated_baa_on_file = False
        scope.practice.save()
    elif change == "revocation":
        revoke_visit_prep(
            owner_practice_member_id=scope.owner.id,
            owner_doctor_id=scope.doctor.id,
            practice_id=scope.practice.id,
            visit_prep_id=root.id,
            expected_credential_generation=1,
            reason="physician_revoked",
        )
    elif change == "rotation":
        rotate_visit_prep_credential(
            owner_practice_member_id=scope.owner.id,
            owner_doctor_id=scope.doctor.id,
            practice_id=scope.practice.id,
            visit_prep_id=root.id,
            expected_credential_generation=1,
        )
    elif change == "cohort":
        options["settings"] = replace(
            options["settings"], enabled_doctor_ids=frozenset()
        )
    elif change == "source":
        options["source_verifier"] = lambda *_a, **_k: False
    else:
        options["patient_origin"] = "https://changed.example.invalid"
    calls = []

    def transport(invitation):
        calls.append(invitation)
        return "accepted"

    if stage == "after_claim":
        assert isinstance(claim, EmailSubmissionClaim)
        result = dispatch_email_claim(claim, **options, transport=transport)
    else:
        result = run_one_email(**options, transport=transport)
    assert result == "suppressed" and calls == []
    assert _rows(fixture)[0].payload_ciphertext is None


def test_missing_transport_is_failed_before_invocation_not_delivered():
    fixture = _queued()
    assert run_one_email(**_options(fixture)) == "failed"
    (row,) = _rows(fixture)
    assert (
        row.outcome_code == "transport_unavailable" and row.payload_ciphertext is None
    )


def test_claim_is_value_free_nonserializable_and_only_consumed_once():
    fixture = _queued()
    claim = claim_email_invitation(**_options(fixture))
    assert isinstance(claim, EmailSubmissionClaim)
    assert repr(claim) == "EmailSubmissionClaim(<redacted>)"
    with pytest.raises(TypeError):
        pickle.dumps(claim)
    calls = []

    def transport(invitation):
        calls.append(invitation)
        raise TimeoutError()

    assert (
        dispatch_email_claim(claim, **_options(fixture), transport=transport)
        == "uncertain"
    )
    assert (
        dispatch_email_claim(claim, **_options(fixture), transport=transport)
        == "discarded"
    )
    assert len(calls) == 1


def test_worker_rejects_an_ambient_transaction_without_committing_it():
    fixture = _queued()
    with global_session() as session:
        session.execute(text("SELECT 1"))
        transaction = session.get_transaction()
        assert (
            run_one_email(**_options(fixture), transport=lambda _: "accepted")
            == "storage_unavailable"
        )
        assert (
            session.get_transaction() is transaction
            and transaction is not None
            and transaction.is_active
        )
    assert _rows(fixture)[0].state == "queued"


@pytest.mark.parametrize("change", ["revocation", "rotation", "consent", "approval"])
def test_secret_cleanup_runs_with_gates_off_and_keeps_durable_evidence(change):
    fixture = _queued()
    scope, root, _, _ = fixture
    original = _rows(fixture)[0].authorization_ciphertext
    if change in {"consent", "approval"}:
        _, approval, consent = _evidence(root, sequence=2, kind="withdrawn")
        if change == "consent":
            _store_consent(consent)
        else:
            _store_approval(approval)
    elif change == "rotation":
        rotate_visit_prep_credential(
            owner_practice_member_id=scope.owner.id,
            owner_doctor_id=scope.doctor.id,
            practice_id=scope.practice.id,
            visit_prep_id=root.id,
            expected_credential_generation=1,
        )
    else:
        revoke_visit_prep(
            owner_practice_member_id=scope.owner.id,
            owner_doctor_id=scope.doctor.id,
            practice_id=scope.practice.id,
            visit_prep_id=root.id,
            expected_credential_generation=1,
            reason="physician_revoked",
        )
    result = maintain_email_secrets()
    assert result == {"suppressed": 1, "uncertain": 0, "status": "ok"}
    (row,) = _rows(fixture)
    assert row.payload_ciphertext is None and row.authorization_ciphertext == original
    assert row.outcome_code == (
        "consent_withdrawn"
        if change == "consent"
        else "scope_unavailable"
        if change == "approval"
        else "obsolete_context"
    )
    assert maintain_email_secrets() == {"suppressed": 0, "uncertain": 0, "status": "ok"}


def test_expired_invitation_is_erased_without_ever_claiming_submission():
    fixture = _queued(expiry=datetime.now(UTC) + timedelta(seconds=1))
    with get_session() as session:
        session.execute(text("SELECT pg_sleep(1.1)"))
    assert maintain_email_secrets()["suppressed"] == 1
    (row,) = _rows(fixture)
    assert row.state == "suppressed" and row.submitted_at is None
    assert row.outcome_code == "expired" and row.payload_ciphertext is None


def test_expired_submission_after_crash_is_uncertain_never_reclaimed():
    fixture = _queued(expiry=datetime.now(UTC) + timedelta(seconds=1))
    claim = claim_email_invitation(**_options(fixture))
    assert isinstance(claim, EmailSubmissionClaim)
    with get_session() as session:
        session.execute(text("SELECT pg_sleep(1.1)"))
    assert run_one_email(**_options(fixture), transport=lambda _: "accepted") == "idle"
    assert maintain_email_secrets()["uncertain"] == 1
    (row,) = _rows(fixture)
    assert row.state == "uncertain" and row.payload_ciphertext is None
    assert (
        dispatch_email_claim(claim, **_options(fixture), transport=lambda _: "accepted")
        == "discarded"
    )


def test_active_older_rows_do_not_starve_expired_secret_maintenance():
    active = _queued("active-first")
    expired = _queued("expired-second", expiry=datetime.now(UTC) + timedelta(seconds=1))
    with get_session() as session:
        session.execute(text("SELECT pg_sleep(1.1)"))
    assert maintain_email_secrets(limit=1)["suppressed"] == 1
    assert (
        _rows(active)[0].state == "queued"
        and _rows(expired)[0].payload_ciphertext is None
    )


@pytest.mark.parametrize("limit", [0, True, 101, "25"])
def test_maintenance_limit_rejects_coercion_without_work(limit):
    assert maintain_email_secrets(limit=limit) == {
        "suppressed": 0,
        "uncertain": 0,
        "status": "invalid",
    }


@pytest.mark.parametrize("stage", ["claim", "dispatch"])
@pytest.mark.parametrize("action", ["commit", "rollback", "exception", "nonboolean"])
def test_unqualified_or_transaction_releasing_source_cannot_send(stage, action):
    fixture = _queued()
    options = _options(fixture)
    claim = claim_email_invitation(**options) if stage == "dispatch" else None
    calls = []

    def source(session, evidence, *, checked_at):
        if action == "commit":
            session.commit()
        elif action == "rollback":
            session.rollback()
        elif action == "exception":
            raise RuntimeError(fixture[3]["access_token"])
        else:
            return 1
        return True

    def transport(invitation):
        calls.append(invitation)
        return "accepted"

    options["source_verifier"] = source
    if stage == "dispatch":
        assert isinstance(claim, EmailSubmissionClaim)
        outcome = dispatch_email_claim(claim, **options, transport=transport)
    else:
        outcome = run_one_email(**options, transport=transport)
    assert outcome == "suppressed" and calls == []
    assert _rows(fixture)[0].payload_ciphertext is None


@pytest.mark.parametrize("stage", ["claim", "dispatch", "maintenance"])
def test_audit_failure_is_atomic_and_never_resends_after_effect(monkeypatch, stage):
    fixture = _queued()
    options = _options(fixture)
    claim = claim_email_invitation(**options) if stage == "dispatch" else None
    if stage == "maintenance":
        _, _, consent = _evidence(fixture[1], sequence=2, kind="withdrawn")
        _store_consent(consent)
    before = _rows(fixture)[0].state
    events = len(_events(fixture))
    calls = []

    def fail_audit(*_args, **_kwargs):
        raise RuntimeError(fixture[3]["access_token"])

    def transport(invitation):
        calls.append(invitation)
        return "accepted"

    monkeypatch.setattr(worker, "_audit", fail_audit)
    if stage == "dispatch":
        assert isinstance(claim, EmailSubmissionClaim)
        assert (
            dispatch_email_claim(claim, **options, transport=transport)
            == "storage_unavailable"
        )
        assert (
            dispatch_email_claim(claim, **options, transport=transport) == "discarded"
        )
    elif stage == "maintenance":
        assert maintain_email_secrets()["status"] == "storage_unavailable"
    else:
        assert run_one_email(**options, transport=transport) == "storage_unavailable"
    assert len(calls) == (1 if stage == "dispatch" else 0)
    assert _rows(fixture)[0].state == before
    assert _rows(fixture)[0].payload_ciphertext is not None
    assert len(_events(fixture)) == events


def test_final_authority_failure_rolls_back_claim_and_its_audit(monkeypatch):
    fixture = _queued()
    calls = []

    def fail_authority(*_args, **_kwargs):
        raise RuntimeError(fixture[3]["access_token"])

    def transport(invitation):
        calls.append(invitation)
        return "accepted"

    monkeypatch.setattr(worker, "_authorize_owner", fail_authority)
    assert (
        run_one_email(**_options(fixture), transport=transport) == "storage_unavailable"
    )
    assert calls == [] and _rows(fixture)[0].state == "queued"
    assert len(_events(fixture)) == 1
