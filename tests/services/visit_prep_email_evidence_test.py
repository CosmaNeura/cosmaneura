"""Closed synthetic evidence round trips; no real consent-source adapter."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from typeid import TypeID

import app.services.visit_prep_email_evidence as codec
from app.services.visit_prep_email_contract import VisitPrepEmailInvalid
from app.services.visit_prep_email_evidence import (
    EmailApprovalEvidence,
    EmailConsentEvidence,
    email_policy_fingerprint,
    read_email_approval,
    read_email_consent,
    seal_email_evidence,
)
from app.services.visit_prep_email_payload import invitation_recipient_digest
from app.utils.encryption import (
    DecryptionError,
    EncryptionError,
    decrypt_value,
    encrypt_value,
    pseudonymous_digest,
)

from activemodel.session_manager import get_session
from app.models.visit_prep_email_authority import (
    VisitPrepEmailApprovalEvent,
    VisitPrepEmailConsentEvent,
)

from tests.services.visit_prep_model_test import _prep


def _evidence(root=None, *, sequence=1, kind=None, expires=None, effective=None):
    root = root or _prep().save()
    now = datetime.now(UTC)
    approval = EmailApprovalEvidence(
        approval_event_id=str(TypeID(prefix="visit_prep_email_approval")),
        owner_practice_member_id=str(root.owner_practice_member_id),
        practice_id=str(root.practice_id),
        owner_doctor_id=str(root.owner_doctor_id),
        event_sequence=sequence,
        event_kind="withdrawn" if kind == "withdrawn" else "approved",
        operation_id=str(uuid4()),
        policy_fingerprint=email_policy_fingerprint(),
        actor_doctor_id=str(root.owner_doctor_id),
        occurred_at=now,
        expires_at=None if kind == "withdrawn" else expires or now + timedelta(days=1),
    )
    consent = EmailConsentEvidence(
        consent_event_id=str(TypeID(prefix="visit_prep_email_consent")),
        visit_prep_id=str(root.id),
        owner_practice_member_id=str(root.owner_practice_member_id),
        question_set_version_id=str(root.question_set_version_id),
        event_sequence=sequence,
        event_kind="withdrawn" if kind == "withdrawn" else "granted",
        actor_role="patient",
        operation_id=str(uuid4()),
        source_event_id=str(uuid4()),
        source_kind="patient_confirmation",
        actor_binding_digest=pseudonymous_digest(
            "invented actor", context="test-actor"
        ),
        source_payload_digest=pseudonymous_digest(
            "invented source", context="test-source"
        ),
        recipient_digest=invitation_recipient_digest("patient@example.com"),
        effective_at=effective or now,
        expires_at=None if kind == "withdrawn" else expires or now + timedelta(days=1),
    )
    return root, approval, consent


def _store_approval(evidence, *, persist=True):
    sealed = seal_email_evidence(evidence)
    row = VisitPrepEmailApprovalEvent(
        id=TypeID.from_string(evidence.approval_event_id),
        owner_practice_member_id=TypeID.from_string(evidence.owner_practice_member_id),
        practice_id=TypeID.from_string(evidence.practice_id),
        owner_doctor_id=TypeID.from_string(evidence.owner_doctor_id),
        event_sequence=evidence.event_sequence,
        event_kind=evidence.event_kind,
        operation_id=evidence.operation_id,
        policy_fingerprint=evidence.policy_fingerprint,
        payload_hmac_sha256=sealed.payload_hmac_sha256,
        evidence_ciphertext=sealed.ciphertext,
        recorded_at=datetime.now(UTC),
        expires_at=evidence.expires_at,
    )
    return row.save() if persist else row


def _store_consent(evidence, *, persist=True):
    sealed = seal_email_evidence(evidence)
    row = VisitPrepEmailConsentEvent(
        id=TypeID.from_string(evidence.consent_event_id),
        visit_prep_id=TypeID.from_string(evidence.visit_prep_id),
        owner_practice_member_id=TypeID.from_string(evidence.owner_practice_member_id),
        question_set_version_id=TypeID.from_string(evidence.question_set_version_id),
        event_sequence=evidence.event_sequence,
        event_kind=evidence.event_kind,
        actor_role=evidence.actor_role,
        operation_id=evidence.operation_id,
        source_event_id=evidence.source_event_id,
        recipient_digest=evidence.recipient_digest,
        payload_hmac_sha256=sealed.payload_hmac_sha256,
        evidence_ciphertext=sealed.ciphertext,
        recorded_at=datetime.now(UTC),
        effective_at=evidence.effective_at,
        expires_at=evidence.expires_at,
    )
    return row.save() if persist else row


def _private(operation):
    with pytest.raises(VisitPrepEmailInvalid) as caught:
        operation()
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert str(caught.value) == "Visit Prep email is invalid"


@pytest.mark.parametrize("kind", [None, "withdrawn"])
def test_real_stored_evidence_is_explicit_closed_private_and_distinct(kind):
    root, approval, consent = _evidence(kind=kind)
    approval_row, consent_row = _store_approval(approval), _store_consent(consent)
    with get_session() as session:
        for row, evidence, reader in (
            (approval_row, approval, read_email_approval),
            (consent_row, consent, read_email_consent),
        ):
            stored = session.get(type(row), row.id)
            assert reader(stored) == evidence
            assert stored.created_at == stored.recorded_at
            assert evidence.manifest() == json.loads(
                decrypt_value(stored.evidence_ciphertext)
            )
            for private in (
                str(row.id),
                evidence.operation_id,
                evidence.owner_practice_member_id,
            ):
                assert private not in repr(evidence)
                assert private not in stored.evidence_ciphertext
            assert "evidence_ciphertext" not in stored.model_dump()
        assert root.patient_id is None and root.appointment_id is None
    assert repr(seal_email_evidence(consent)) == "SealedEmailEvidence(<redacted>)"


@pytest.mark.parametrize("stream", ["approval", "consent"])
@pytest.mark.parametrize(
    "field",
    [
        "id",
        "owner_practice_member_id",
        "event_sequence",
        "operation_id",
        "event_kind",
        "expires_at",
    ],
)
def test_authentic_ciphertext_cannot_move_to_other_row_metadata(stream, field):
    root, approval, consent = _evidence()
    row = _store_approval(approval) if stream == "approval" else _store_consent(consent)
    reader = read_email_approval if stream == "approval" else read_email_consent
    changes = {
        "id": TypeID(prefix="visit_prep_email_" + stream),
        "owner_practice_member_id": TypeID(prefix="practice_member"),
        "event_sequence": 2,
        "operation_id": uuid4(),
        "event_kind": "withdrawn",
        "expires_at": datetime.now(UTC) + timedelta(days=2),
    }
    _private(lambda: reader(row.model_copy(update={field: changes[field]})))


@pytest.mark.parametrize(
    "changes",
    [
        {"actor_doctor_id": str(TypeID(prefix="doctor"))},
        {"event_sequence": True},
        {"event_sequence": 0},
        {"event_sequence": 2_147_483_648},
        {"policy_fingerprint": "A" * 64},
        {"operation_id": "00000000-0000-0000-0000-000000000000"},
        {"occurred_at": datetime(2026, 10, 1)},
        {"occurred_at": datetime.min.replace(tzinfo=timezone(timedelta(hours=1)))},
        {"expires_at": None},
        {"event_kind": "granted"},
    ],
)
def test_approval_manifest_rejects_coercion_malformed_actor_and_times(changes):
    _, evidence, _ = _evidence()
    _private(lambda: seal_email_evidence(replace(evidence, **changes)))


@pytest.mark.parametrize(
    "changes",
    [
        {"actor_role": "physician"},
        {"source_kind": "staff_checkbox"},
        {"actor_role": "caregiver"},
        {"actor_role": "trusted_source"},
        {"source_event_id": "not a uuid"},
        {"event_sequence": True},
        {"actor_binding_digest": ""},
        {"source_payload_digest": "A" * 64},
        {"recipient_digest": "patient@example.com"},
        {"effective_at": datetime(2026, 10, 1)},
        {"expires_at": None},
    ],
)
def test_consent_manifest_rejects_unverified_label_shapes_and_private_input(changes):
    _, _, evidence = _evidence()
    _private(lambda: seal_email_evidence(replace(evidence, **changes)))


@pytest.mark.parametrize("stream", ["approval", "consent"])
@pytest.mark.parametrize(
    "change",
    [
        "extra",
        "bool",
        "purpose",
        "source",
        "digest",
        "duplicate",
        "large",
        "version",
        "cipher",
        "time",
    ],
)
def test_encrypted_wrong_schema_and_corruption_are_rejected_privately(stream, change):
    _, approval, consent = _evidence()
    row = _store_approval(approval) if stream == "approval" else _store_consent(consent)
    reader = read_email_approval if stream == "approval" else read_email_consent
    payload = json.loads(decrypt_value(row.evidence_ciphertext))
    updates = {}
    if change == "extra":
        payload["story"] = "invented private story"
    elif change == "bool":
        payload["format_version"] = True
    elif change == "purpose":
        payload["purpose"] = "pastoral"
    elif change == "source":
        payload["source_kind"] = "staff_checkbox"
    elif change == "digest":
        updates["payload_hmac_sha256"] = "a" * 64
    elif change == "version":
        updates["envelope_format_version"] = True
    elif change == "cipher":
        updates["evidence_ciphertext"] = "\ud800"
    elif change == "time":
        updates["recorded_at"] = datetime(2000, 1, 1, tzinfo=UTC)
    context = f"visit-prep-email-{stream}-evidence-v1"
    plaintext = codec._json(payload)
    if change == "duplicate":
        plaintext = '{"format_version":1,"format_version":1}'
    if change == "large":
        plaintext = "x" * 4501
    if change not in {"digest", "version", "cipher", "time"}:
        updates |= {
            "evidence_ciphertext": encrypt_value(plaintext),
            "payload_hmac_sha256": pseudonymous_digest(plaintext, context=context),
        }
    _private(lambda: reader(row.model_copy(update=updates)))


def test_crypto_failure_never_carries_private_cause_or_context(monkeypatch):
    _, approval, consent = _evidence()
    row = _store_consent(consent)
    for name, exception in (
        ("decrypt_value", DecryptionError),
        ("encrypt_value", EncryptionError),
        ("pseudonymous_digest", EncryptionError),
    ):
        with monkeypatch.context() as patch:

            def fail(*_args, **_kwargs):
                raise exception() from RuntimeError("invented private source")

            patch.setattr(codec, name, fail)
            if name == "decrypt_value":
                _private(lambda: read_email_consent(row))
            else:
                _private(lambda: seal_email_evidence(approval))


def test_equivalent_timezones_and_frozen_object_revalidation():
    _, approval, consent = _evidence()
    for evidence in (approval, consent):
        alternate = replace(
            evidence,
            expires_at=evidence.expires_at.astimezone(timezone(timedelta(hours=-5))),
        )
        assert alternate.manifest() == evidence.manifest()
        object.__setattr__(evidence, "event_sequence", True)
        _private(lambda: seal_email_evidence(evidence))


def test_policy_tracks_actual_template_content_not_an_authored_fingerprint(monkeypatch):
    before = email_policy_fingerprint()
    monkeypatch.setattr(
        codec.VisitPrepInvitationEmail,
        "body",
        property(lambda _: "Changed fixed guidance"),
    )
    assert email_policy_fingerprint() != before
