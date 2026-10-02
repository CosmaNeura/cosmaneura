"""Synthetic encrypted invitation binding; no send or authorization grant."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from typeid import TypeID

import app.services.visit_prep_email_payload as codec
from app.services.visit_prep_command_contract import (
    _new_access_token,
    access_token_hash,
)
from app.services.visit_prep_email_contract import (
    VisitPrepEmailInvalid,
    VisitPrepInvitationEmail,
)
from app.services.visit_prep_email_payload import (
    EmailInvitationBinding,
    bind_stored_invitation,
    decrypt_invitation_payload,
    encrypt_invitation_payload,
    invitation_authorization_digest,
    invitation_recipient_digest,
)
from app.utils.encryption import (
    DecryptionError,
    EncryptionError,
    decrypt_value,
    encrypt_value,
)

from activemodel.session_manager import get_session
from app.models.visit_prep import VisitPrep, VisitPrepCredentialGeneration
from app.models.visit_prep_delivery import VisitPrepEmailDelivery

from tests.services.visit_prep_model_test import _credential, _prep, _schedule


def _fixture():
    invitation = VisitPrepInvitationEmail(
        recipient="synthetic@example.invalid",
        patient_origin="https://intake.test.invalid",
        access_token=_new_access_token(),
        operation_id=str(uuid4()),
    )
    binding = EmailInvitationBinding(
        delivery_id=str(TypeID(prefix="visit_prep_email")),
        visit_prep_id=str(TypeID(prefix="visit_prep")),
        owner_practice_member_id=str(TypeID(prefix="practice_member")),
        question_set_version_id=str(TypeID(prefix="visit_prep_question_set_version")),
        schedule_generation_id=str(TypeID(prefix="visit_prep_schedule")),
        credential_generation=1,
        operation_id=invitation.operation_id,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        credential_token_hash=access_token_hash(invitation.access_token),
        recipient_digest=invitation_recipient_digest(invitation.recipient),
        authorization_digest=invitation_authorization_digest(
            encrypt_value("Synthetic authorization evidence; not real consent")
        ),
    )
    return invitation, binding


def _decode(ciphertext, binding):
    return decrypt_invitation_payload(
        ciphertext, binding, patient_origin="https://intake.test.invalid"
    )


def _private_error(operation):
    with pytest.raises(VisitPrepEmailInvalid) as caught:
        operation()
    assert str(caught.value) == "Visit Prep email is invalid"
    assert caught.value.__context__ is None and caught.value.__cause__ is None


def test_roundtrip_encrypts_only_closed_inputs_with_no_sensitive_repr():
    invitation, binding = _fixture()
    ciphertext = encrypt_invitation_payload(invitation, binding)
    assert _decode(ciphertext, binding) == invitation
    assert 1 <= len(ciphertext.encode()) <= 4_096
    for sensitive in (
        invitation.access_token,
        invitation.recipient,
        binding.delivery_id,
        binding.credential_token_hash,
        binding.recipient_digest,
        binding.authorization_digest,
    ):
        assert sensitive not in repr(binding) and sensitive not in ciphertext
    payload = json.loads(decrypt_value(ciphertext))
    assert set(payload) == {"manifest", "invitation"}
    assert set(payload["invitation"]) == {
        "recipient",
        "patient_origin",
        "access_token",
        "operation_id",
    }
    assert payload["manifest"]["purpose"] == "visit_prep_email_invitation"
    assert payload["manifest"]["template_version"] == "visit_prep_invitation_v1"


@pytest.mark.parametrize(
    "field",
    [
        "delivery_id",
        "visit_prep_id",
        "owner_practice_member_id",
        "question_set_version_id",
        "schedule_generation_id",
        "credential_generation",
        "operation_id",
        "expires_at",
        "credential_token_hash",
        "recipient_digest",
        "authorization_digest",
    ],
)
def test_ciphertext_cannot_move_to_a_different_valid_lineage_or_authorization(field):
    invitation, binding = _fixture()
    _, other = _fixture()
    changed = replace(binding, **{field: getattr(other, field)})
    if field == "credential_generation":
        changed = replace(binding, credential_generation=2)
    elif field == "recipient_digest":
        changed = replace(
            binding,
            recipient_digest=invitation_recipient_digest("other@example.invalid"),
        )
    _private_error(
        lambda: _decode(encrypt_invitation_payload(invitation, binding), changed)
    )


@pytest.mark.parametrize("field", ["recipient", "access_token", "operation_id"])
def test_valid_but_different_message_inputs_cannot_bind_to_the_intent(field):
    invitation, binding = _fixture()
    changes = {
        "recipient": "other@example.invalid",
        "access_token": _new_access_token(),
        "operation_id": str(uuid4()),
    }
    changed = replace(invitation, **{field: changes[field]})
    _private_error(lambda: encrypt_invitation_payload(changed, binding))
    payload = {
        "manifest": binding.manifest(),
        "invitation": {
            "recipient": changed.recipient,
            "patient_origin": changed.patient_origin,
            "access_token": changed.access_token,
            "operation_id": changed.operation_id,
        },
    }
    _private_error(lambda: _decode(encrypt_value(json.dumps(payload)), binding))


def test_current_trusted_origin_change_denies_an_otherwise_valid_invitation():
    invitation, binding = _fixture()
    ciphertext = encrypt_invitation_payload(invitation, binding)
    _private_error(
        lambda: decrypt_invitation_payload(
            ciphertext, binding, patient_origin="https://new-origin.test.invalid"
        )
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"delivery_id": "patient@example.invalid"},
        {"visit_prep_id": str(TypeID(prefix="patient"))},
        {"owner_practice_member_id": None},
        {"credential_generation": True},
        {"credential_generation": 0},
        {"credential_generation": 2_147_483_648},
        {"operation_id": "00000000-0000-0000-0000-000000000000"},
        {"expires_at": datetime(2026, 10, 1)},
        {"expires_at": None},
        {"expires_at": datetime.min.replace(tzinfo=timezone(timedelta(hours=1)))},
        {"credential_token_hash": "a" * 63},
        {"recipient_digest": "A" * 64},
        {"authorization_digest": "private content"},
    ],
)
def test_binding_shape_does_not_coerce_input_or_leak_it(changes):
    _, binding = _fixture()
    _private_error(lambda: replace(binding, **changes))


def test_frozen_binding_and_invitation_are_rechecked_at_the_boundary():
    invitation, binding = _fixture()
    ciphertext = encrypt_invitation_payload(invitation, binding)
    object.__setattr__(binding, "credential_generation", True)
    _private_error(lambda: _decode(ciphertext, binding))
    _private_error(lambda: encrypt_invitation_payload(invitation, binding))
    invitation, binding = _fixture()
    object.__setattr__(invitation, "recipient", "a@example.invalid,b@example.invalid")
    _private_error(lambda: encrypt_invitation_payload(invitation, binding))


def test_expiry_manifest_uses_the_same_instant_across_aware_timezones():
    invitation, binding = _fixture()
    alternate = replace(
        binding,
        expires_at=binding.expires_at.astimezone(timezone(timedelta(hours=-5))),
    )
    assert alternate.manifest() == binding.manifest()
    assert (
        _decode(encrypt_invitation_payload(invitation, binding), alternate)
        == invitation
    )


@pytest.mark.parametrize(
    "change", ["extra", "bool-version", "wrong-purpose", "extra-message"]
)
def test_closed_encrypted_schema_rejects_unknown_or_ambiguous_content(change):
    invitation, binding = _fixture()
    payload = json.loads(decrypt_value(encrypt_invitation_payload(invitation, binding)))
    if change == "extra":
        payload["answer"] = "Synthetic private story"
    elif change == "bool-version":
        payload["manifest"]["format_version"] = True
    elif change == "wrong-purpose":
        payload["manifest"]["purpose"] = "visit_prep_synthesis"
    else:
        payload["invitation"]["bcc"] = "other@example.invalid"
    _private_error(lambda: _decode(encrypt_value(json.dumps(payload)), binding))


@pytest.mark.parametrize(
    "plaintext",
    [
        "[]",
        "not json",
        '{"manifest":{},"manifest":{},"invitation":{}}',
        "{" + '"answer":"' + "x" * 2_500 + '"}',
        "[" * 1_500,
    ],
)
def test_malformed_duplicate_oversized_or_nested_envelopes_are_private(plaintext):
    _, binding = _fixture()
    _private_error(lambda: _decode(encrypt_value(plaintext), binding))


@pytest.mark.parametrize(
    "ciphertext", [None, "", "x" * 4_097, "not ciphertext", "\ud800"]
)
def test_ciphertext_bounds_and_unicode_fail_closed(ciphertext):
    _, binding = _fixture()
    _private_error(lambda: _decode(ciphertext, binding))


@pytest.mark.parametrize(
    "name,failure",
    [
        ("encrypt_value", EncryptionError),
        ("decrypt_value", DecryptionError),
        ("pseudonymous_digest", EncryptionError),
    ],
)
def test_crypto_failure_does_not_retain_private_exception_chain(
    monkeypatch, name, failure
):
    invitation, binding = _fixture()
    ciphertext = encrypt_invitation_payload(invitation, binding)

    def fail(*_args, **_kwargs):
        raise failure() from RuntimeError(invitation.access_token)

    monkeypatch.setattr(codec, name, fail)
    if name == "decrypt_value":
        _private_error(lambda: _decode(ciphertext, binding))
    else:
        _private_error(lambda: encrypt_invitation_payload(invitation, binding))


def test_authorization_fingerprint_is_separate_and_not_a_permission_grant():
    invitation, binding = _fixture()
    assert invitation_recipient_digest(invitation.recipient) == binding.recipient_digest
    assert (
        invitation_authorization_digest(invitation.recipient)
        != binding.recipient_digest
    )
    assert (
        invitation_recipient_digest("other@example.invalid") != binding.recipient_digest
    )
    for invalid in (None, "", "x" * 8_193, "\ud800", "\u2603" * 4_000):
        _private_error(lambda: invitation_authorization_digest(invalid))


def _stored_fixture():
    root = _prep()
    root.recipient_snapshot_ciphertext = encrypt_value(
        json.dumps(
            {
                "version": 1,
                "first_name": "Synthetic",
                "role": "caregiver",
                "email": "synthetic@example.com",
                "phone": None,
            }
        )
    )
    root.save()
    now = datetime.now(UTC)
    schedule = _schedule(root, now + timedelta(days=14)).save()
    token = _new_access_token()
    credential = _credential(root, schedule)
    credential.token_hash = access_token_hash(token)
    credential.save()
    row = VisitPrepEmailDelivery(
        # IDs normally materialize at INSERT. The transactional enqueue command
        # must allocate this ID before sealing an envelope bound to that row.
        id=TypeID(prefix="visit_prep_email"),
        visit_prep_id=root.id,
        owner_practice_member_id=root.owner_practice_member_id,
        question_set_version_id=root.question_set_version_id,
        schedule_generation_id=schedule.id,
        credential_generation=credential.generation,
        authorization_ciphertext=encrypt_value("Synthetic evidence; no consent grant"),
        requested_at=datetime.now(UTC),
        expires_at=now + timedelta(hours=1),
    )
    binding = bind_stored_invitation(
        row, root, credential, checked_at=datetime.now(UTC)
    )
    invitation = VisitPrepInvitationEmail(
        recipient="synthetic@example.com",
        patient_origin="https://intake.test.invalid",
        access_token=token,
        operation_id=str(row.operation_id),
    )
    row.payload_ciphertext = encrypt_invitation_payload(invitation, binding)
    row.save()
    return row, root, credential, invitation


def test_real_stored_rows_bind_ciphertext_to_contact_and_credential_without_a_chart():
    row, root, credential, invitation = _stored_fixture()
    with get_session() as session:
        stored = session.get(VisitPrepEmailDelivery, row.id)
        parent = session.get(VisitPrep, root.id)
        current = session.get(VisitPrepCredentialGeneration, credential.id)
        binding = bind_stored_invitation(
            stored, parent, current, checked_at=datetime.now(UTC)
        )
        assert _decode(stored.payload_ciphertext, binding) == invitation
        assert parent.patient_id is None and parent.appointment_id is None
        assert stored.authorization_ciphertext and stored.state == "queued"


def test_stored_contact_crypto_failure_has_no_private_exception_chain(monkeypatch):
    row, root, credential, invitation = _stored_fixture()

    def fail(*_args, **_kwargs):
        raise DecryptionError() from RuntimeError(invitation.recipient)

    monkeypatch.setattr(codec, "decrypt_value", fail)
    _private_error(
        lambda: bind_stored_invitation(
            row, root, credential, checked_at=datetime.now(UTC)
        )
    )


@pytest.mark.parametrize(
    "target,changes",
    [
        ("row", {"envelope_format_version": True}),
        ("row", {"template_version": "other"}),
        ("row", {"state": "accepted"}),
        ("row", {"expires_at": datetime(2000, 1, 1, tzinfo=UTC)}),
        ("row", {"expires_at": datetime.now(UTC) + timedelta(hours=25)}),
        ("row", {"requested_at": datetime(2100, 1, 1, tzinfo=UTC)}),
        ("row", {"visit_prep_id": TypeID(prefix="visit_prep")}),
        ("row", {"owner_practice_member_id": TypeID(prefix="practice_member")}),
        (
            "row",
            {
                "question_set_version_id": TypeID(
                    prefix="visit_prep_question_set_version"
                )
            },
        ),
        ("row", {"schedule_generation_id": TypeID(prefix="visit_prep_schedule")}),
        ("row", {"credential_generation": 2}),
        ("row", {"credential_generation": True}),
        ("row", {"state": "submitting", "lease_until": None}),
        (
            "row",
            {
                "state": "submitting",
                "submitted_at": datetime(2000, 1, 1, tzinfo=UTC),
                "lease_until": datetime(2000, 1, 2, tzinfo=UTC),
            },
        ),
        ("root", {"revoked_at": datetime(2000, 1, 1, tzinfo=UTC)}),
        ("root", {"misdirected_at": datetime(2000, 1, 1, tzinfo=UTC)}),
        ("root", {"completed_at": datetime(2000, 1, 1, tzinfo=UTC)}),
        ("root", {"has_email_contact": False}),
        ("root", {"recipient_snapshot_format_version": True}),
        ("credential", {"revoked_at": datetime(2000, 1, 1, tzinfo=UTC)}),
        ("credential", {"token_format_version": True}),
        ("credential", {"token_hash": "private value"}),
        ("credential", {"issued_at": datetime(2100, 1, 1, tzinfo=UTC)}),
        ("credential", {"expires_at": datetime(2000, 1, 1, tzinfo=UTC)}),
    ],
)
def test_stored_boundary_rejects_inactive_expired_or_cross_bound_inputs(
    target, changes
):
    row, root, credential, _ = _stored_fixture()
    items = {"row": row, "root": root, "credential": credential}
    # Copy the row for boundary corruption tests; never bypass SQL immutability.
    items[target] = items[target].model_copy(update=changes)
    _private_error(
        lambda: bind_stored_invitation(
            items["row"],
            items["root"],
            items["credential"],
            checked_at=datetime.now(UTC),
        )
    )


@pytest.mark.parametrize(
    "plaintext",
    [
        "not json",
        "[]",
        '{"version":1,"version":1}',
        "x" * 12_001,
        json.dumps(
            {
                "version": True,
                "first_name": "Synthetic",
                "role": "patient",
                "email": "synthetic@example.invalid",
                "phone": None,
            }
        ),
        json.dumps(
            {
                "version": 1,
                "first_name": "Synthetic",
                "role": "patient",
                "email": "OTHER@EXAMPLE.COM",
                "phone": None,
            }
        ),
        json.dumps(
            {
                "version": 1,
                "first_name": "Synthetic",
                "role": "patient",
                "email": None,
                "phone": "+12025550123",
            }
        ),
    ],
    ids=[
        "invalid-json",
        "not-object",
        "duplicate-version",
        "too-long",
        "boolean-version",
        "noncanonical-mailbox",
        "phone-only",
    ],
)
def test_stored_contact_envelope_is_closed_bounded_and_canonical(plaintext):
    row, root, credential, _ = _stored_fixture()
    corrupted = root.model_copy(
        update={"recipient_snapshot_ciphertext": encrypt_value(plaintext)}
    )
    _private_error(
        lambda: bind_stored_invitation(
            row, corrupted, credential, checked_at=datetime.now(UTC)
        )
    )


def test_replaced_contact_or_authorization_cannot_decode_an_old_stored_invitation():
    row, root, credential, _ = _stored_fixture()
    contact = root.model_copy(
        update={
            "recipient_snapshot_ciphertext": encrypt_value(
                json.dumps(
                    {
                        "version": 1,
                        "first_name": "Synthetic",
                        "role": "patient",
                        "email": "other@example.com",
                        "phone": None,
                    }
                )
            )
        }
    )
    changed = bind_stored_invitation(
        row, contact, credential, checked_at=datetime.now(UTC)
    )
    _private_error(lambda: _decode(row.payload_ciphertext, changed))
    evidence = row.model_copy(
        update={"authorization_ciphertext": encrypt_value("Other synthetic evidence")}
    )
    changed = bind_stored_invitation(
        evidence, root, credential, checked_at=datetime.now(UTC)
    )
    _private_error(lambda: _decode(row.payload_ciphertext, changed))


def test_valid_submitting_row_decodes_only_inside_its_existing_lease():
    row, root, credential, invitation = _stored_fixture()
    now = datetime.now(UTC)
    claimed = row.model_copy(
        update={
            "state": "submitting",
            "submitted_at": now,
            "lease_until": now + timedelta(seconds=90),
        }
    )
    bound = bind_stored_invitation(claimed, root, credential, checked_at=now)
    assert _decode(claimed.payload_ciphertext, bound) == invitation
    _private_error(
        lambda: bind_stored_invitation(
            claimed, root, credential, checked_at=claimed.lease_until
        )
    )
