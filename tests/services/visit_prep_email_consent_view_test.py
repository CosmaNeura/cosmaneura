"""Actual synthetic PostgreSQL choice reads and required-confirmation capture."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

import app.services.visit_prep_email_consent_view as views
from app.services.visit_prep_email_consent import record_confirmed_patient_email_consent
from app.services.visit_prep_email_consent_view import get_patient_email_consent_view
from app.services.visit_prep_interviews import complete_interview
from app.services.visit_preps import rotate_visit_prep_credential

from activemodel.session_manager import get_session
from app.models.authority import AuthorityAuditEvent
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from tests.services.visit_prep_email_consent_test import (
    _audits,
    _capture,
    _decision,
    _deny,
    _rows,
)
from tests.services.visit_prep_email_consent_test import (
    _ready as _confirmed,
)
from tests.services.visit_prep_interviews_test import _respond


def _ready(monkeypatch, label, **kwargs):
    fixture = _confirmed(monkeypatch, label, **kwargs)
    from app.services import visit_prep_access

    monkeypatch.setattr(
        visit_prep_access,
        "VISIT_PREP_SETTINGS",
        replace(
            visit_prep_access.VISIT_PREP_SETTINGS, email_consent_capture_enabled=True
        ),
    )
    return fixture


def _view(fixture, **changes):
    args: dict = (
        dict(access_token=fixture.access_token, first_name="José", role="patient")
        | changes
    )
    return get_patient_email_consent_view(**args)


def _public_capture(fixture, decision, **changes):
    args: dict = (
        dict(access_token=fixture.access_token, first_name="José", decision=decision)
        | changes
    )
    return record_confirmed_patient_email_consent(**args)


def _read_audits(fixture):
    with get_session() as session:
        return session.exec(
            select(AuthorityAuditEvent).where(
                AuthorityAuditEvent.event_type == "visit_prep.email_consent_read",
                AuthorityAuditEvent.resource_ref == str(fixture.prep_id),
            )
        ).all()


@pytest.mark.parametrize("enabled", [False, 1, None])
def test_public_choice_capture_has_an_independent_fail_closed_control(
    monkeypatch, enabled
):
    from app.services import visit_prep_access

    fixture = _ready(monkeypatch, "choice-independent-control")
    monkeypatch.setattr(
        visit_prep_access,
        "VISIT_PREP_SETTINGS",
        replace(
            visit_prep_access.VISIT_PREP_SETTINGS, email_consent_capture_enabled=enabled
        ),
    )
    _deny(lambda: _view(fixture))
    _deny(lambda: _public_capture(fixture, _decision(fixture)))
    assert not _rows(fixture) and not _read_audits(fixture)


@pytest.mark.parametrize("role", ["patient", "caregiver"])
def test_choice_read_is_audited_optional_and_distinct_from_send_permission(
    monkeypatch, role
):
    fixture = _ready(monkeypatch, "choice-view", role=role)
    view = _view(fixture, role=role)
    assert view.status == "not_recorded" and view.event_sequence == 0
    assert view.recorded_at is view.expires_at is None and view.can_grant
    assert view.maximum_expires_at == fixture.scheduled_at
    assert view.actor_role == role
    assert "optional" in view.grant_disclosure and "not SMS" in view.grant_disclosure
    assert ("on their behalf" in view.grant_disclosure) is (role == "caregiver")
    assert len(view.grant_disclosure_fingerprint) == 64
    assert len(view.withdrawal_disclosure_fingerprint) == 64
    assert view.grant_disclosure_fingerprint != view.withdrawal_disclosure_fingerprint
    assert repr(view) == "PatientEmailConsentView(<redacted>)"
    assert not _rows(fixture) and not _audits(fixture)
    audit = _read_audits(fixture)[0]
    assert audit.actor_id is audit.person_id is audit.tenant_id is None
    assert audit.content_sha256 == view.grant_disclosure_fingerprint
    assert audit.occurred_at <= audit.recorded_at <= view.checked_at
    receipt = _public_capture(fixture, _decision(fixture, role=role))
    current = _view(fixture, role=role)
    assert current.status == "grant_recorded" and current.event_sequence == 1
    assert current.recorded_at == receipt.recorded_at
    assert current.expires_at == fixture.scheduled_at
    _public_capture(
        fixture, _decision(fixture, kind="withdrawn", sequence=1, role=role)
    )
    withdrawn = _view(fixture, role=role)
    assert withdrawn.status == "withdrawn" and withdrawn.event_sequence == 2
    assert withdrawn.expires_at is None and withdrawn.can_grant
    assert len(_read_audits(fixture)) == 3


@pytest.mark.parametrize("confirmation", ["Wrong synthetic name", "", None, 12])
def test_fresh_name_confirmation_required_for_reads_and_public_mutations(
    monkeypatch, confirmation
):
    fixture = _ready(monkeypatch, "choice-confirmation")
    _deny(lambda: _view(fixture, first_name=confirmation))
    _deny(lambda: _public_capture(fixture, _decision(fixture), first_name=confirmation))
    assert not _rows(fixture) and not _read_audits(fixture)


@pytest.mark.parametrize("role", ["caregiver", "trusted_source", None])
def test_wrong_confirmation_role_never_discloses_or_captures(monkeypatch, role):
    fixture = _ready(monkeypatch, "choice-role")
    _deny(lambda: _view(fixture, role=role))
    _deny(lambda: _public_capture(fixture, replace(_decision(fixture), role=role)))
    assert not _rows(fixture) and not _read_audits(fixture)


@pytest.mark.parametrize("view_as", [True, None, 0])
def test_support_view_denies_both_public_domain_entry_points(monkeypatch, view_as):
    fixture = _ready(monkeypatch, "choice-support")
    _deny(lambda: _view(fixture, view_as_active=view_as))
    _deny(lambda: _public_capture(fixture, _decision(fixture), view_as_active=view_as))
    assert not _rows(fixture) and not _read_audits(fixture)


def test_completion_removes_grant_option_without_preventing_read_or_withdrawal(
    monkeypatch,
):
    fixture = _ready(monkeypatch, "choice-complete")
    _public_capture(fixture, _decision(fixture))
    for topic in ("reason", "medications", "adherence", "story"):
        _respond(fixture, topic)
    complete_interview(access_token=fixture.access_token)
    current = _view(fixture)
    assert current.status == "grant_recorded" and not current.can_grant
    _deny(lambda: _public_capture(fixture, _decision(fixture, sequence=1)))
    _public_capture(fixture, _decision(fixture, kind="withdrawn", sequence=1))
    assert _view(fixture).status == "withdrawn"


def test_actual_choice_expiry_changes_display_without_creating_another_event(
    monkeypatch,
):
    fixture = _ready(monkeypatch, "choice-expiry")
    _capture(
        fixture, _decision(fixture, expires_at=datetime.now(UTC) + timedelta(seconds=1))
    )
    with get_session() as session:
        session.execute(text("SELECT pg_sleep(1.1)"))
    current = _view(fixture)
    assert current.status == "expired" and current.event_sequence == 1
    assert current.expires_at is not None and current.expires_at <= current.checked_at
    assert len(_rows(fixture)) == len(_audits(fixture)) == 1


def test_current_rotation_preserves_attribution_but_old_link_cannot_read(monkeypatch):
    fixture = _ready(monkeypatch, "choice-rotation")
    _public_capture(fixture, _decision(fixture))
    rotated = rotate_visit_prep_credential(
        owner_practice_member_id=fixture.owner.id,
        owner_doctor_id=fixture.doctor.id,
        practice_id=fixture.practice.id,
        visit_prep_id=fixture.prep_id,
        expected_credential_generation=1,
    )
    _deny(lambda: _view(fixture))
    current = _view(fixture, access_token=rotated.access_token)
    assert current.status == "grant_recorded" and current.event_sequence == 1
    assert _rows(fixture)[0].operation_id is not None


@pytest.mark.parametrize("failure", ["audit", "lost_ack", "future_evidence"])
def test_failed_read_does_not_return_private_state_and_recovery_is_read_only(
    monkeypatch, failure
):
    fixture = _ready(monkeypatch, "choice-read-failure")
    _public_capture(fixture, _decision(fixture))
    original_flush, original_commit = Session.flush, Session.commit
    original_context = views._capture_context

    def fail_audit(session, *args, **kwargs):
        if any(isinstance(row, AuthorityAuditEvent) for row in session.new):
            raise SQLAlchemyError("synthetic private audit failure")
        return original_flush(session, *args, **kwargs)

    def lost_ack(session):
        original_commit(session)
        raise SQLAlchemyError("synthetic private commit failure")

    with monkeypatch.context() as patch:
        if failure == "future_evidence":
            patch.setattr(
                views,
                "_capture_context",
                lambda session, **kwargs: replace(
                    original_context(session, **kwargs),
                    event_at=datetime.now(UTC) - timedelta(days=1),
                ),
            )
        else:
            patch.setattr(
                Session,
                "flush" if failure == "audit" else "commit",
                fail_audit if failure == "audit" else lost_ack,
            )
        _deny(lambda: _view(fixture))
    assert len(_read_audits(fixture)) == (1 if failure == "lost_ack" else 0)
    assert _view(fixture).status == "grant_recorded"
    assert len(_rows(fixture)) == len(_audits(fixture)) == 1
