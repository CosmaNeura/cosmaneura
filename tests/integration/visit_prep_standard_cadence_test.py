"""Central standard on actual owned PostgreSQL; transport/consent are invented."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

import app.configuration.visit_prep_cadence as configuration
import app.services.visit_prep_timed_email as timed
from app.configuration.visit_prep_cadence import DEFAULT_VISIT_PREP_CADENCE
from app.services.visit_prep_email_contract import VisitPrepEmailInvalid
from app.services.visit_prep_email_enqueue import enqueue_email_invitation
from app.services.visit_prep_email_worker import (
    EmailSubmissionClaim,
    claim_standard_email_invitation,
    dispatch_email_claim,
    dispatch_standard_email_claim,
    run_one_standard_email,
)
from app.services.visit_prep_timed_email import enqueue_standard_email_invitation
from app.services.visit_preps import rotate_visit_prep_credential

from activemodel.session_manager import global_session

from tests.integration.visit_prep_email_cadence_test import _rescheduled
from tests.services.visit_prep_email_enqueue_test import _ready, _rows
from tests.services.visit_prep_email_worker_test import _events


def _fixture(monkeypatch, *, quiet=False):
    # Exercise the REAL approved policy; choose an actual current timezone
    # whose local hour is inside/outside quiet hours, never fake the DB clock.
    hour = datetime.now(UTC).hour
    offset = ((22 if quiet else 12) - hour + 12) % 24 - 12
    timezone_name = "UTC" if offset == 0 else f"Etc/GMT{-offset:+d}"
    fixture = _rescheduled(
        _ready("central-standard"),
        at=datetime.now(UTC) + timedelta(days=14),
        timezone_name=timezone_name,
    )
    monkeypatch.setattr(
        configuration, "VISIT_PREP_CADENCE_SETTINGS", DEFAULT_VISIT_PREP_CADENCE
    )
    return fixture


def _standard_options(fixture):
    return {
        key: fixture[3][key]
        for key in ("patient_origin", "source_verifier", "settings")
    }


def test_actual_standard_enqueue_and_single_send_share_approved_policy(monkeypatch):
    fixture = _fixture(monkeypatch)
    queued = enqueue_standard_email_invitation(**fixture[3])
    assert queued.disposition == "queued" and queued.receipt is not None
    calls = []
    assert (
        run_one_standard_email(
            **_standard_options(fixture),
            transport=lambda message: calls.append(message) or "accepted",
        )
        == "accepted"
    )
    assert len(calls) == 1 and calls[0].access_token == fixture[3]["access_token"]
    row = _rows(fixture)[0]
    assert row.state == "accepted" and row.payload_ciphertext is None
    assert len(_events(fixture)) == 3
    assert run_one_standard_email(**_standard_options(fixture)) == "idle"


def test_actual_quiet_schedule_does_not_enqueue_or_send_then_can_reengage(monkeypatch):
    fixture = _fixture(monkeypatch, quiet=True)
    waiting = enqueue_standard_email_invitation(**fixture[3])
    assert waiting.disposition == "wait" and waiting.next_check_at is not None
    assert _rows(fixture) == []
    # Central policy update is TEST ONLY, no provider argument changes it.
    monkeypatch.setattr(
        configuration,
        "VISIT_PREP_CADENCE_SETTINGS",
        replace(
            DEFAULT_VISIT_PREP_CADENCE,
            version="synthetic-quiet-update",
            quiet_start_hour=4,
            quiet_end_hour=5,
        ),
    )
    queued = enqueue_standard_email_invitation(**fixture[3])
    assert queued.disposition == "queued"


def test_standard_worker_preserves_quiet_queued_intent_without_claim(monkeypatch):
    fixture = _fixture(monkeypatch, quiet=True)
    enqueue_email_invitation(**fixture[3])
    before = _rows(fixture)[0]
    calls = []
    assert (
        run_one_standard_email(
            **_standard_options(fixture),
            transport=lambda message: calls.append(message) or "accepted",
        )
        == "wait"
    )
    after = _rows(fixture)[0]
    assert calls == [] and after.state == "queued" and after.submitted_at is None
    assert after.payload_ciphertext == before.payload_ciphertext
    assert after.operation_id == before.operation_id and len(_events(fixture)) == 1


def test_standard_four_hour_spacing_survives_rotation_and_real_prior_send(monkeypatch):
    fixture = _fixture(monkeypatch)
    enqueue_standard_email_invitation(**fixture[3])
    assert (
        run_one_standard_email(
            **_standard_options(fixture), transport=lambda message: "accepted"
        )
        == "accepted"
    )
    first = _rows(fixture)[0]
    changed = rotate_visit_prep_credential(
        **{
            key: fixture[3][key]
            for key in (
                "owner_practice_member_id",
                "owner_doctor_id",
                "practice_id",
                "visit_prep_id",
                "expected_credential_generation",
            )
        }
    )
    fixture[3].update(
        access_token=changed.access_token,
        expected_credential_generation=changed.visit_prep.credential_generation,
        operation_id=str(uuid4()),
    )
    waiting = enqueue_standard_email_invitation(**fixture[3])
    assert waiting.disposition == "wait" and waiting.next_check_at is not None
    assert first.submitted_at is not None
    assert waiting.next_check_at >= first.submitted_at + timedelta(hours=4)
    assert len(_rows(fixture)) == 1


@pytest.mark.parametrize("change", ["version", "rules", "missing"])
@pytest.mark.parametrize("entry", ["standard", "lower_domain"])
def test_changed_central_policy_consumes_claim_without_transport_even_via_domain(
    monkeypatch, change, entry
):
    fixture = _fixture(monkeypatch)
    enqueue_standard_email_invitation(**fixture[3])
    claim = claim_standard_email_invitation(**_standard_options(fixture))
    assert isinstance(claim, EmailSubmissionClaim)
    before = _rows(fixture)[0]
    updated = (
        None
        if change == "missing"
        else replace(
            DEFAULT_VISIT_PREP_CADENCE,
            **(
                {"version": "synthetic-next"}
                if change == "version"
                else {"minimum_channel_spacing_hours": 5}
            ),
        )
    )
    monkeypatch.setattr(configuration, "VISIT_PREP_CADENCE_SETTINGS", updated)
    calls = []
    arguments = _standard_options(fixture) | {
        "transport": lambda message: calls.append(message) or "accepted"
    }
    if entry == "lower_domain":
        arguments |= {
            "quiet_hours": (21, 8),
            "minimum_channel_spacing": timedelta(hours=4),
        }
        outcome = dispatch_email_claim(claim, **arguments)
    else:
        outcome = dispatch_standard_email_claim(claim, **arguments)
    assert outcome == "suppressed" and calls == []
    after = _rows(fixture)[0]
    assert after.operation_id == before.operation_id
    assert after.state == "suppressed" and after.payload_ciphertext is None
    assert (
        dispatch_standard_email_claim(
            claim, **arguments if entry == "standard" else _standard_options(fixture)
        )
        == "discarded"
    )


def test_policy_changes_during_actual_source_verification_prevent_provider_call(
    monkeypatch,
):
    fixture = _fixture(monkeypatch)
    enqueue_standard_email_invitation(**fixture[3])
    claim = claim_standard_email_invitation(**_standard_options(fixture))
    assert isinstance(claim, EmailSubmissionClaim)
    original = fixture[3]["source_verifier"]

    def changed(session, evidence, *, checked_at):
        result = original(session, evidence, checked_at=checked_at)
        monkeypatch.setattr(
            configuration,
            "VISIT_PREP_CADENCE_SETTINGS",
            replace(DEFAULT_VISIT_PREP_CADENCE, version="synthetic-during-source"),
        )
        return result

    calls = []
    assert (
        dispatch_standard_email_claim(
            claim,
            **(_standard_options(fixture) | {"source_verifier": changed}),
            transport=lambda message: calls.append(message) or "accepted",
        )
        == "suppressed"
    )
    assert calls == [] and _rows(fixture)[0].payload_ciphertext is None


def test_central_change_during_enqueue_rolls_back_actual_new_intent(monkeypatch):
    fixture = _fixture(monkeypatch)
    original = timed.enqueue_locked_timed_email

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        monkeypatch.setattr(
            configuration,
            "VISIT_PREP_CADENCE_SETTINGS",
            replace(DEFAULT_VISIT_PREP_CADENCE, version="synthetic-during-enqueue"),
        )
        return result

    monkeypatch.setattr(timed, "enqueue_locked_timed_email", changed)
    with pytest.raises(VisitPrepEmailInvalid):
        enqueue_standard_email_invitation(**fixture[3])
    assert _rows(fixture) == [] and _events(fixture) == []


def test_missing_standard_and_disabled_release_cannot_claim_or_send(monkeypatch):
    fixture = _fixture(monkeypatch)
    enqueue_standard_email_invitation(**fixture[3])
    options = _standard_options(fixture)
    calls = []
    assert (
        run_one_standard_email(
            **(
                options
                | {"settings": replace(options["settings"], email_send_enabled=False)}
            ),
            transport=lambda message: calls.append(message) or "accepted",
        )
        == "disabled"
    )
    monkeypatch.setattr(configuration, "VISIT_PREP_CADENCE_SETTINGS", None)
    assert run_one_standard_email(**options) == "disabled"
    assert calls == [] and _rows(fixture)[0].state == "queued"


def test_standard_worker_still_refuses_ambient_transaction(monkeypatch):
    fixture = _fixture(monkeypatch)
    enqueue_standard_email_invitation(**fixture[3])
    with global_session() as session:
        transaction = session.get_transaction()
        assert (
            claim_standard_email_invitation(**_standard_options(fixture))
            == "storage_unavailable"
        )
        assert session.get_transaction() is transaction
    assert _rows(fixture)[0].state == "queued"
