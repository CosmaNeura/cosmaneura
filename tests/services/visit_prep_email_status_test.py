"""Actual private status reads without activating invitations or decryption."""

from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta

import pytest

from app.services.visit_prep_email_contract import VisitPrepEmailInvalid
from app.services.visit_prep_email_status import get_email_status
from app.services.visit_prep_email_worker import (
    EmailSubmissionClaim,
    claim_email_invitation,
)
from app.services.visit_preps import revoke_visit_prep, rotate_visit_prep_credential

from activemodel.session_manager import get_session
from app.models.authority import AuthorityAuditEvent
from sqlalchemy import text
from sqlmodel import Session, select

from tests.services.visit_prep_delivery_model_test import _update
from tests.services.visit_prep_email_enqueue_test import _ready, _rows
from tests.services.visit_prep_email_worker_test import _options, _queued


def _read(fixture, **changes):
    args = {
        key: fixture[3][key]
        for key in (
            "owner_practice_member_id",
            "owner_doctor_id",
            "practice_id",
            "expected_clerk_subject",
            "visit_prep_id",
            "settings",
        )
    }
    return get_email_status(**(args | changes))


def _audits(fixture):
    with get_session() as session:
        return session.exec(
            select(AuthorityAuditEvent).where(
                AuthorityAuditEvent.event_type == "visit_prep.email_status_read",
                AuthorityAuditEvent.resource_ref == str(fixture[1].id),
            )
        ).all()


def test_missing_intent_read_is_private_audited_and_does_not_create_it():
    fixture = _ready()
    result = _read(fixture)
    assert result.status == "not_requested"
    assert all(
        asdict(result)[key] is None
        for key in (
            "outcome_code",
            "credential_generation",
            "context_current",
            "invitation_expired",
            "submission_window_elapsed",
            "requested_at",
            "expires_at",
            "submitted_at",
            "finalized_at",
        )
    )
    assert _rows(fixture) == [] and len(_audits(fixture)) == 1
    assert repr(result) == "EmailStatusView(<redacted>)"
    for secret in (
        fixture[3]["access_token"],
        "patient@example.com",
        fixture[3]["expected_clerk_subject"],
    ):
        assert (
            secret not in str(asdict(result))
            and secret not in _audits(fixture)[0].model_dump_json()
        )


@pytest.mark.parametrize(
    "state,code",
    [
        ("queued", None),
        ("submitting", None),
        ("accepted", None),
        ("failed", "transport_unavailable"),
        ("suppressed", "consent_withdrawn"),
        ("uncertain", "submission_uncertain"),
    ],
)
def test_actual_recorded_status_is_not_delivery_or_send_permission(state, code):
    fixture = _queued()
    if state in {"submitting", "accepted", "uncertain"}:
        assert isinstance(
            claim_email_invitation(**_options(fixture)), EmailSubmissionClaim
        )
    if state not in {"queued", "submitting"}:
        _update(
            _rows(fixture)[0],
            state=state,
            outcome_code=code,
            payload_ciphertext=None,
            lease_until=None,
            finalized_at=datetime.now(UTC),
        )
    before = _rows(fixture)[0]
    result = _read(
        fixture,
        settings=replace(
            fixture[3]["settings"], public_api_enabled=False, email_send_enabled=False
        ),
    )
    assert result.status == state and result.outcome_code == code
    assert result.context_current is True and result.invitation_expired is False
    assert result.submission_window_elapsed is (
        False if state == "submitting" else None
    )
    after = _rows(fixture)[0]
    assert (
        after.state == before.state
        and after.payload_ciphertext == before.payload_ciphertext
    )
    assert len(_audits(fixture)) == 1


def test_rotation_retains_old_status_but_flags_its_obsolete_context():
    fixture = _queued()
    scope, root, _, _ = fixture
    rotate_visit_prep_credential(
        owner_practice_member_id=scope.owner.id,
        owner_doctor_id=scope.doctor.id,
        practice_id=scope.practice.id,
        visit_prep_id=root.id,
        expected_credential_generation=1,
    )
    value = _read(fixture)
    assert value.status == "queued" and value.context_current is False
    assert value.credential_generation == 1 and value.current_credential_generation == 2
    assert _rows(fixture)[0].state == "queued"  # Read does not perform cleanup.


def test_revocation_is_visible_to_current_owner_without_sending_authority():
    fixture = _queued()
    scope, root, _, _ = fixture
    revoke_visit_prep(
        owner_practice_member_id=scope.owner.id,
        owner_doctor_id=scope.doctor.id,
        practice_id=scope.practice.id,
        visit_prep_id=root.id,
        expected_credential_generation=1,
        reason="physician_revoked",
    )
    assert _read(fixture).context_current is False


@pytest.mark.parametrize("claimed", [True, False])
def test_expired_pending_work_reports_time_without_finalizing_it(claimed):
    fixture = _queued(expiry=datetime.now(UTC) + timedelta(seconds=1))
    if claimed:
        assert isinstance(
            claim_email_invitation(**_options(fixture)), EmailSubmissionClaim
        )
    with get_session() as session:
        session.execute(text("SELECT pg_sleep(1.1)"))
    result = _read(fixture)
    assert result.status == ("submitting" if claimed else "queued")
    assert result.invitation_expired is True
    assert result.submission_window_elapsed is (True if claimed else None)
    assert _rows(fixture)[0].payload_ciphertext is not None


@pytest.mark.parametrize(
    "change", ["subject", "view_as", "baa", "cohort", "disabled", "owner"]
)
def test_current_access_denial_is_private_and_audit_free(change):
    fixture = _queued()
    changes = {}
    if change == "subject":
        changes["expected_clerk_subject"] = "wrong-synthetic-subject"
    elif change == "view_as":
        changes["view_as_active"] = True
    elif change == "baa":
        fixture[0].practice.negotiated_baa_on_file = False
        fixture[0].practice.save()
    elif change == "owner":
        changes["visit_prep_id"] = _ready("other-status-owner")[1].id
    else:
        changes["settings"] = replace(
            fixture[3]["settings"],
            **(
                {"enabled": False}
                if change == "disabled"
                else {"enabled_doctor_ids": frozenset()}
            ),
        )
    with pytest.raises(VisitPrepEmailInvalid) as caught:
        _read(fixture, **changes)
    assert caught.value.__cause__ is caught.value.__context__ is None
    assert _audits(fixture) == []


def test_audit_failure_rolls_back_read_without_disclosure(monkeypatch):
    fixture = _queued()
    original = Session.flush

    def fail(session, *args, **kwargs):
        if any(isinstance(row, AuthorityAuditEvent) for row in session.new):
            raise ValueError(fixture[3]["access_token"])
        return original(session, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Session, "flush", fail)
        with pytest.raises(VisitPrepEmailInvalid) as caught:
            _read(fixture)
    assert caught.value.__cause__ is caught.value.__context__ is None
    assert _audits(fixture) == [] and _rows(fixture)[0].state == "queued"
