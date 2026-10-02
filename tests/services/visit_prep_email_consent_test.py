"""Explicit invented patient/caregiver choices; no qualified identity or sends."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest

import app.services.visit_prep_email_consent as consent
from app.services.visit_prep_email_authority import resolve_locked_email_authority
from app.services.visit_prep_email_consent import (
    PatientEmailConsentInput,
    email_consent_disclosure,
    email_consent_disclosure_fingerprint,
    read_patient_email_consent_source,
    record_patient_email_consent,
)
from app.services.visit_prep_email_contract import VisitPrepEmailInvalid
from app.services.visit_prep_email_evidence import read_email_consent
from app.services.visit_prep_interviews import (
    complete_interview,
    confirm_interview,
    report_wrong_recipient,
)
from app.services.visit_preps import rotate_visit_prep_credential

from activemodel.session_manager import get_session
from app.models.authority import AuthorityAuditEvent
from app.models.visit_prep_email_authority import VisitPrepEmailConsentEvent
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from tests.services.visit_prep_email_evidence_test import _evidence, _store_consent
from tests.services.visit_prep_interviews_test import _fixture, _respond


def _ready(monkeypatch, label="consent-capture", *, role="patient", confirmed=True):
    fixture = _fixture(monkeypatch, label, recipient_role=role)
    if confirmed:
        confirm_interview(
            access_token=fixture.access_token, first_name="José", role=role
        )
    return fixture


def _decision(fixture, *, role="patient", kind="granted", sequence=0, **changes):
    return PatientEmailConsentInput(
        **(
            {
                "operation_id": str(uuid4()),
                "expected_event_sequence": sequence,
                "event_kind": kind,
                "role": role,
                "disclosure_fingerprint": email_consent_disclosure_fingerprint(
                    role=role, event_kind=kind
                ),
                "expires_at": fixture.scheduled_at if kind == "granted" else None,
                "recipient_control_attested": kind == "granted",
                "caregiver_authority_attested": kind == "granted"
                and role == "caregiver",
            }
            | changes
        )
    )


def _capture(fixture, decision, **changes):
    return record_patient_email_consent(
        access_token=fixture.access_token, decision=decision, **changes
    )


def _rows(fixture):
    with get_session() as session:
        return session.exec(
            select(VisitPrepEmailConsentEvent)
            .where(VisitPrepEmailConsentEvent.visit_prep_id == fixture.prep_id)
            .order_by(VisitPrepEmailConsentEvent.event_sequence)
        ).all()


def _audits(fixture):
    refs = [str(row.id) for row in _rows(fixture)]
    with get_session() as session:
        return session.exec(
            select(AuthorityAuditEvent).where(
                AuthorityAuditEvent.resource_type == "visit_prep_email_consent",
                AuthorityAuditEvent.resource_ref.in_(refs),
            )
        ).all()


def _deny(operation):
    with pytest.raises(VisitPrepEmailInvalid) as caught:
        operation()
    assert str(caught.value) == "Visit Prep email is invalid"
    assert caught.value.__cause__ is None and caught.value.__context__ is None


@pytest.mark.parametrize("role", ["patient", "caregiver"])
def test_confirmation_does_not_grant_consent_but_explicit_choice_has_attributed_source(
    monkeypatch, role
):
    fixture = _ready(monkeypatch, role=role)
    assert not _rows(fixture)
    decision = _decision(fixture, role=role)
    receipt = _capture(fixture, decision)
    assert receipt.event_sequence == receipt.current_event_sequence == 1
    assert receipt.event_kind == "granted" and receipt.disposition == "recorded"
    assert receipt.expires_at == fixture.scheduled_at
    row = _rows(fixture)[0]
    evidence = read_email_consent(row)
    assert evidence.actor_role == role
    assert evidence.source_kind == f"{role}_confirmation"
    assert evidence.operation_id == decision.operation_id
    with get_session() as session:
        source = read_patient_email_consent_source(session, evidence)
    assert source.identity_assurance == "invitation_bearer"
    assert source.recipient_control_attested is True
    assert source.caregiver_authority_attested is (role == "caregiver")
    assert source.disclosure_fingerprint == decision.disclosure_fingerprint
    audit = _audits(fixture)[0]
    assert str(audit.client_event_id) == evidence.source_event_id
    assert audit.request_digest == evidence.source_payload_digest
    assert audit.content_sha256 == decision.disclosure_fingerprint
    assert audit.actor_id is None and audit.person_id is None
    assert audit.tenant_id is None and audit.interaction_id is None
    assert audit.occurred_at == row.recorded_at == receipt.recorded_at
    assert audit.recorded_at >= audit.occurred_at
    assert fixture.access_token not in row.evidence_ciphertext
    assert "patient@example.com" not in row.evidence_ciphertext
    assert "recipient" not in receipt.__dataclass_fields__
    assert repr(decision) == "PatientEmailConsentInput(<redacted>)"
    assert repr(receipt) == "PatientEmailConsentReceipt(<redacted>)"
    assert repr(source) == "PatientEmailConsentSource(<redacted>)"
    assert consent.visit_prep_access.VISIT_PREP_SETTINGS.email_send_enabled is False


def test_unconfirmed_contact_cannot_capture_and_declining_does_not_block_answers(
    monkeypatch,
):
    fixture = _ready(monkeypatch, confirmed=False)
    _deny(lambda: _capture(fixture, _decision(fixture)))
    confirm_interview(
        access_token=fixture.access_token, first_name="José", role="patient"
    )
    receipt = _capture(fixture, _decision(fixture, kind="withdrawn"))
    assert receipt.event_kind == "withdrawn"
    result = _respond(fixture, "reason", answer="Synthetic reason for a visit.")
    assert result.disposition == "accepted"
    assert _rows(fixture)[0].event_kind == "withdrawn"


def test_old_grant_replay_and_stale_fresh_key_never_restore_later_withdrawal(
    monkeypatch,
):
    fixture = _ready(monkeypatch)
    original = _decision(fixture)
    first = _capture(fixture, original)
    cipher = _rows(fixture)[0].evidence_ciphertext
    withdrawal = _decision(fixture, kind="withdrawn", sequence=1)
    _capture(fixture, withdrawal)
    replayed = _capture(fixture, original)
    assert replayed.event_sequence == 1 and replayed.current_event_sequence == 2
    assert (
        replayed.disposition == "replayed" and replayed.recorded_at == first.recorded_at
    )
    assert _rows(fixture)[0].evidence_ciphertext == cipher
    assert _rows(fixture)[-1].event_kind == "withdrawn"
    assert _capture(fixture, withdrawal).disposition == "replayed"
    _deny(lambda: _capture(fixture, replace(original, operation_id=str(uuid4()))))
    assert len(_rows(fixture)) == len(_audits(fixture)) == 2
    assert _capture(fixture, _decision(fixture, sequence=2)).event_sequence == 3


@pytest.mark.parametrize("change", ["deadline", "kind", "sequence", "role"])
def test_exact_operation_replay_rejects_changed_intent(monkeypatch, change):
    fixture = _ready(monkeypatch)
    original = _decision(fixture)
    _capture(fixture, original)
    replacements = {
        "deadline": _decision(
            fixture, expires_at=fixture.scheduled_at - timedelta(days=1)
        ),
        "kind": _decision(fixture, kind="withdrawn"),
        "sequence": _decision(fixture, sequence=1),
        "role": _decision(fixture, role="caregiver"),
    }
    changed = replace(replacements[change], operation_id=original.operation_id)
    _deny(lambda: _capture(fixture, changed))
    assert len(_rows(fixture)) == len(_audits(fixture)) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"operation_id": "00000000-0000-0000-0000-000000000000"},
        {"operation_id": "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"},
        {"operation_id": uuid4()},
        {"expected_event_sequence": True},
        {"expected_event_sequence": -1},
        {"expected_event_sequence": 2_147_483_647},
        {"event_kind": "approved"},
        {"role": "trusted_source"},
        {"disclosure_fingerprint": "A" * 64},
        {"disclosure_fingerprint": "a" * 64},
        {"expires_at": None},
        {"expires_at": datetime(2026, 10, 1)},
        {"expires_at": datetime.now(UTC) - timedelta(seconds=1)},
        {"recipient_control_attested": False},
        {"recipient_control_attested": 1},
        {"caregiver_authority_attested": True},
        {"caregiver_authority_attested": None},
    ],
)
def test_strict_choice_disclosure_deadline_and_attestations_are_required(
    monkeypatch, changes
):
    fixture = _ready(monkeypatch)
    _deny(lambda: _capture(fixture, replace(_decision(fixture), **changes)))
    assert not _rows(fixture)


def test_caregiver_requires_separate_authority_attestation_and_matching_confirmed_role(
    monkeypatch,
):
    fixture = _ready(monkeypatch, role="caregiver")
    _deny(lambda: _capture(fixture, _decision(fixture)))
    _deny(
        lambda: _capture(
            fixture,
            _decision(fixture, role="caregiver", caregiver_authority_attested=False),
        )
    )
    assert not _rows(fixture)
    assert (
        _capture(fixture, _decision(fixture, role="caregiver")).disposition
        == "recorded"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"enabled": False},
        {"enabled": 1},
        {"public_api_enabled": False},
        {"configuration_valid": False},
        {"enabled_doctor_ids": frozenset()},
    ],
)
def test_disabled_default_and_current_cohort_deny_capture(monkeypatch, changes):
    fixture = _ready(monkeypatch)
    settings = consent.visit_prep_access.VISIT_PREP_SETTINGS
    monkeypatch.setattr(
        consent.visit_prep_access, "VISIT_PREP_SETTINGS", replace(settings, **changes)
    )
    _deny(lambda: _capture(fixture, _decision(fixture)))
    assert not _rows(fixture)


@pytest.mark.parametrize("view", [True, None, 0])
def test_support_view_cannot_record_patient_choice(monkeypatch, view):
    fixture = _ready(monkeypatch)
    _deny(lambda: _capture(fixture, _decision(fixture), view_as_active=view))


def test_grant_cannot_extend_beyond_the_shown_visit_and_current_link(monkeypatch):
    fixture = _ready(monkeypatch)
    _deny(
        lambda: _capture(
            fixture,
            _decision(fixture, expires_at=fixture.scheduled_at + timedelta(seconds=1)),
        )
    )


def test_completion_allows_withdrawal_but_never_new_grant(monkeypatch):
    fixture = _ready(monkeypatch)
    _capture(fixture, _decision(fixture))
    for topic in ("reason", "medications", "adherence", "story"):
        _respond(fixture, topic)
    complete_interview(access_token=fixture.access_token)
    _deny(lambda: _capture(fixture, _decision(fixture, sequence=1)))
    assert (
        _capture(fixture, _decision(fixture, kind="withdrawn", sequence=1)).event_kind
        == "withdrawn"
    )


@pytest.mark.parametrize("change", ["baa", "doctor", "wrong_recipient", "rotation"])
def test_current_authority_denial_never_revives_access_for_replay(monkeypatch, change):
    fixture = _ready(monkeypatch)
    decision = _decision(fixture)
    _capture(fixture, decision)
    if change == "baa":
        fixture.practice.negotiated_baa_on_file = False
        fixture.practice.save()
    elif change == "doctor":
        fixture.doctor.deleted_at = datetime.now(UTC)
        fixture.doctor.save()
    elif change == "wrong_recipient":
        report_wrong_recipient(access_token=fixture.access_token)
    else:
        rotate_visit_prep_credential(
            owner_practice_member_id=fixture.owner.id,
            owner_doctor_id=fixture.doctor.id,
            practice_id=fixture.practice.id,
            visit_prep_id=fixture.prep_id,
            expected_credential_generation=1,
        )
    _deny(lambda: _capture(fixture, decision))
    assert len(_rows(fixture)) == 1


def test_encrypted_role_label_without_independent_capture_audit_is_not_source_proof(
    monkeypatch,
):
    fixture = _ready(monkeypatch)
    with get_session() as session:
        from app.models.visit_prep import VisitPrep

        root = session.get(VisitPrep, fixture.prep_id)
    _, _, evidence = _evidence(root)
    _store_consent(evidence)
    with get_session() as session:
        _deny(lambda: read_patient_email_consent_source(session, evidence))


def test_authentic_source_reader_rejects_changed_evidence_and_ambiguous_audits(
    monkeypatch,
):
    fixture = _ready(monkeypatch)
    decision = _decision(fixture)
    _capture(fixture, decision)
    row = _rows(fixture)[0]
    evidence = read_email_consent(row)
    with get_session() as session:
        _deny(
            lambda: read_patient_email_consent_source(
                session, replace(evidence, source_payload_digest="a" * 64)
            )
        )
        audit = _audits(fixture)[0]
        duplicate = AuthorityAuditEvent(
            **{key: value for key, value in audit.model_dump().items() if key != "id"}
        )
        session.add(duplicate)
        session.commit()
        _deny(lambda: read_patient_email_consent_source(session, evidence))


def test_source_reader_does_not_install_a_send_verifier_even_with_physician_approval(
    monkeypatch,
):
    from app.services.visit_prep_email_evidence import email_policy_fingerprint
    from app.services.visit_prep_email_policy import record_email_policy_decision

    fixture = _ready(monkeypatch)
    _capture(fixture, _decision(fixture))
    record_email_policy_decision(
        owner_practice_member_id=fixture.owner.id,
        owner_doctor_id=fixture.doctor.id,
        practice_id=fixture.practice.id,
        expected_clerk_subject=fixture.doctor.clerk_id,
        operation_id=str(uuid4()),
        expected_event_sequence=0,
        event_kind="approved",
        policy_fingerprint=email_policy_fingerprint(),
        expires_at=fixture.scheduled_at,
        settings=consent.visit_prep_access.VISIT_PREP_SETTINGS,
    )
    settings = replace(
        consent.visit_prep_access.VISIT_PREP_SETTINGS, email_send_enabled=True
    )
    with get_session() as session:
        _deny(
            lambda: resolve_locked_email_authority(
                session,
                owner_practice_member_id=fixture.owner.id,
                owner_doctor_id=fixture.doctor.id,
                practice_id=fixture.practice.id,
                expected_clerk_subject=fixture.doctor.clerk_id,
                visit_prep_id=fixture.prep_id,
                settings=settings,
            )
        )


@pytest.mark.parametrize("stage", ["audit", "lost_ack"])
def test_atomic_audit_and_lost_ack_recovery_keep_one_actual_choice(monkeypatch, stage):
    fixture = _ready(monkeypatch)
    decision = _decision(fixture)
    original_flush, original_commit = Session.flush, Session.commit

    def fail_audit(session, *args, **kwargs):
        if any(isinstance(row, AuthorityAuditEvent) for row in session.new):
            raise SQLAlchemyError("synthetic private capture diagnostic")
        return original_flush(session, *args, **kwargs)

    def lost_ack(session):
        original_commit(session)
        raise SQLAlchemyError("synthetic private commit diagnostic")

    with monkeypatch.context() as patch:
        patch.setattr(
            Session,
            "flush" if stage == "audit" else "commit",
            fail_audit if stage == "audit" else lost_ack,
        )
        _deny(lambda: _capture(fixture, decision))
    assert (
        len(_rows(fixture)) == len(_audits(fixture)) == (0 if stage == "audit" else 1)
    )
    receipt = _capture(fixture, decision)
    assert receipt.disposition == ("recorded" if stage == "audit" else "replayed")
    assert len(_rows(fixture)) == len(_audits(fixture)) == 1


def test_actual_elapsed_expiry_before_commit_rolls_back_consent_and_audit(monkeypatch):
    fixture = _ready(monkeypatch)
    decision = _decision(fixture, expires_at=datetime.now(UTC) + timedelta(seconds=1))
    original = consent._capture_context
    calls = 0

    def delayed(session, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            session.execute(text("SELECT pg_sleep(1.1)"))
        return original(session, **kwargs)

    monkeypatch.setattr(consent, "_capture_context", delayed)
    _deny(lambda: _capture(fixture, decision))
    assert calls == 3 and not _rows(fixture) and not _audits(fixture)


def test_equivalent_expiry_in_another_timezone_replays_the_original_choice(monkeypatch):
    fixture = _ready(monkeypatch)
    decision = _decision(fixture)
    first = _capture(fixture, decision)
    replay = _capture(
        fixture,
        replace(
            decision,
            expires_at=decision.expires_at.astimezone(timezone(timedelta(hours=-5))),
        ),
    )
    assert replay == replace(first, disposition="replayed")
    assert len(_rows(fixture)) == len(_audits(fixture)) == 1


def test_expired_historical_receipt_is_not_extended_or_restored(monkeypatch):
    fixture = _ready(monkeypatch)
    decision = _decision(fixture, expires_at=datetime.now(UTC) + timedelta(seconds=1))
    first = _capture(fixture, decision)
    with get_session() as session:
        session.execute(text("SELECT pg_sleep(1.1)"))
    replay = _capture(fixture, decision)
    assert replay == replace(first, disposition="replayed")
    assert replay.expires_at < datetime.now(UTC)
    assert len(_rows(fixture)) == len(_audits(fixture)) == 1


def test_rotation_preserves_original_source_but_old_link_cannot_replay(monkeypatch):
    fixture = _ready(monkeypatch)
    decision = _decision(fixture)
    first = _capture(fixture, decision)
    evidence = read_email_consent(_rows(fixture)[0])
    rotated = rotate_visit_prep_credential(
        owner_practice_member_id=fixture.owner.id,
        owner_doctor_id=fixture.doctor.id,
        practice_id=fixture.practice.id,
        visit_prep_id=fixture.prep_id,
        expected_credential_generation=1,
    )
    with get_session() as session:
        source = read_patient_email_consent_source(session, evidence)
    assert source.identity_assurance == "invitation_bearer"
    _deny(lambda: _capture(fixture, decision))
    replay = _capture(replace(fixture, access_token=rotated.access_token), decision)
    assert replay == replace(first, disposition="replayed")
    assert len(_rows(fixture)) == len(_audits(fixture)) == 1


@pytest.mark.parametrize("stage", ["new_choice", "replay"])
def test_disclosure_change_before_commit_denies_without_adding_evidence(
    monkeypatch, stage
):
    fixture = _ready(monkeypatch)
    decision = _decision(fixture)
    if stage == "replay":
        _capture(fixture, decision)
    original = consent._capture_context
    calls = 0

    def change_at_final_check(session, **kwargs):
        nonlocal calls
        calls += 1
        authorized = original(session, **kwargs)
        if calls == (3 if stage == "new_choice" else 2):
            monkeypatch.setattr(consent, "CONSENT_DISCLOSURE_VERSION", "changed_draft")
        return authorized

    monkeypatch.setattr(consent, "_capture_context", change_at_final_check)
    _deny(lambda: _capture(fixture, decision))
    assert len(_rows(fixture)) == len(_audits(fixture)) == (stage == "replay")


def test_disclosure_is_optional_email_only_and_not_identity_verification():
    text = email_consent_disclosure(role="caregiver", event_kind="granted")
    assert "optional" in text and "not SMS" in text
    assert "not independent verification" in text
    assert email_consent_disclosure_fingerprint(
        role="patient", event_kind="granted"
    ) != email_consent_disclosure_fingerprint(role="caregiver", event_kind="granted")
