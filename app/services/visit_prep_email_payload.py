"""Explicit invitation encryption bound to an exact outbox and recipient.

This codec grants no consent, current-scope authority or permission to send.
The command/worker must resolve those under locks before using it. No ORM
property transparently decrypts recipient contact or the invitation bearer.
"""

from __future__ import annotations

import hmac
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from typeid import TypeID
from typeid.errors import TypeIDException

from app.services.visit_prep_command_contract import (
    RecipientInput,
    VisitPrepValidation,
    _normalize_recipient,
    access_token_hash,
)
from app.services.visit_prep_email_contract import (
    INVITATION_TEMPLATE_VERSION,
    VisitPrepEmailInvalid,
    VisitPrepInvitationEmail,
    email_address,
)
from app.utils.encryption import (
    DecryptionError,
    EncryptionError,
    decrypt_value,
    encrypt_value,
    pseudonymous_digest,
)

from app.models.visit_prep import VisitPrep, VisitPrepCredentialGeneration
from app.models.visit_prep_delivery import VisitPrepEmailDelivery

MAX_INVITATION_PLAINTEXT_BYTES = 2_500
MAX_INVITATION_CIPHERTEXT_BYTES = 4_096
_HASH = re.compile(r"[0-9a-f]{64}\Z")


def _canonical_ref(value: object, prefix: str) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = TypeID.from_string(value)
        return parsed.prefix == prefix and str(parsed) == value
    except (TypeIDException, ValueError):
        return False


@dataclass(frozen=True, repr=False)
class EmailInvitationBinding:
    delivery_id: str
    visit_prep_id: str
    owner_practice_member_id: str
    question_set_version_id: str
    schedule_generation_id: str
    credential_generation: int
    operation_id: str
    expires_at: datetime
    credential_token_hash: str
    recipient_digest: str
    authorization_digest: str

    def __post_init__(self) -> None:
        valid = False
        try:
            operation = UUID(self.operation_id)
            valid = (
                _canonical_ref(self.delivery_id, "visit_prep_email")
                and _canonical_ref(self.visit_prep_id, "visit_prep")
                and _canonical_ref(self.owner_practice_member_id, "practice_member")
                and _canonical_ref(
                    self.question_set_version_id, "visit_prep_question_set_version"
                )
                and _canonical_ref(self.schedule_generation_id, "visit_prep_schedule")
                and type(self.credential_generation) is int
                and 0 < self.credential_generation <= 2_147_483_647
                and str(operation) == self.operation_id
                and operation.version == 4
                and isinstance(self.expires_at, datetime)
                and self.expires_at.utcoffset() is not None
                and self.expires_at.astimezone(UTC).utcoffset() is not None
                and all(
                    isinstance(value, str) and _HASH.fullmatch(value) is not None
                    for value in (
                        self.credential_token_hash,
                        self.recipient_digest,
                        self.authorization_digest,
                    )
                )
            )
        except (TypeError, ValueError, AttributeError, OverflowError):
            pass
        if not valid:
            raise VisitPrepEmailInvalid()

    def __repr__(self) -> str:
        return "EmailInvitationBinding(<redacted>)"

    def manifest(self) -> dict:
        # Revalidate even a frozen object at the encryption boundary.
        self.__post_init__()
        return {
            "format_version": 1,
            "purpose": "visit_prep_email_invitation",
            "delivery_id": self.delivery_id,
            "visit_prep_id": self.visit_prep_id,
            "owner_practice_member_id": self.owner_practice_member_id,
            "question_set_version_id": self.question_set_version_id,
            "schedule_generation_id": self.schedule_generation_id,
            "credential_generation": self.credential_generation,
            "operation_id": self.operation_id,
            "template_version": INVITATION_TEMPLATE_VERSION,
            "expires_at": self.expires_at.astimezone(UTC).isoformat(),
            "credential_token_hash": self.credential_token_hash,
            "recipient_digest": self.recipient_digest,
            "authorization_digest": self.authorization_digest,
        }


def invitation_recipient_digest(recipient: str) -> str:
    email_address(recipient)
    digest = None
    try:
        digest = pseudonymous_digest(recipient, context="visit-prep-email-recipient-v1")
    except EncryptionError:
        pass
    if digest is None:
        raise VisitPrepEmailInvalid()
    return digest


def invitation_authorization_digest(ciphertext: str) -> str:
    # Cross-bind the separate durable evidence envelope without exposing it or
    # mistaking a fingerprint for validated/current consent or standing approval.
    if not isinstance(ciphertext, str) or not 1 <= len(ciphertext) <= 8_192:
        raise VisitPrepEmailInvalid()
    digest = None
    try:
        if len(ciphertext.encode("utf-8")) > 8_192:
            raise VisitPrepEmailInvalid()
        digest = pseudonymous_digest(
            ciphertext, context="visit-prep-email-authorization-v1"
        )
    except (EncryptionError, UnicodeError, VisitPrepEmailInvalid):
        pass
    if digest is None:
        raise VisitPrepEmailInvalid()
    return digest


def stored_email_recipient(preparation: VisitPrep) -> str:
    """Explicitly decode the closed contact snapshot; this grants no consent."""
    email = None
    try:
        if (
            type(preparation.recipient_snapshot_format_version) is not int
            or preparation.recipient_snapshot_format_version != 1
            or preparation.has_email_contact is not True
        ):
            raise VisitPrepEmailInvalid()
        ciphertext = preparation.recipient_snapshot_ciphertext
        if not 1 <= len(ciphertext.encode("utf-8")) <= 16_384:
            raise VisitPrepEmailInvalid()
        plaintext = decrypt_value(ciphertext)
        if len(plaintext.encode("utf-8")) > 12_000:
            raise VisitPrepEmailInvalid()
        recipient = json.loads(plaintext, object_pairs_hook=_closed_object)
        if (
            not isinstance(recipient, dict)
            or set(recipient) != {"version", "first_name", "role", "email", "phone"}
            or type(recipient["version"]) is not int
            or recipient["version"] != 1
        ):
            raise VisitPrepEmailInvalid()
        normalized = _normalize_recipient(
            RecipientInput(
                first_name=recipient["first_name"],
                role=recipient["role"],
                email=recipient["email"],
                phone=recipient["phone"],
            )
        )
        if normalized.email != recipient["email"] or normalized.email is None:
            raise VisitPrepEmailInvalid()
        email = email_address(normalized.email)
    except (
        DecryptionError,
        VisitPrepValidation,
        VisitPrepEmailInvalid,
        ValueError,
        TypeError,
        AttributeError,
        UnicodeError,
        RecursionError,
    ):
        pass
    if email is None:
        raise VisitPrepEmailInvalid()
    return email


def bind_stored_invitation(
    delivery: VisitPrepEmailDelivery,
    preparation: VisitPrep,
    credential: VisitPrepCredentialGeneration,
    *,
    checked_at: datetime,
) -> EmailInvitationBinding:
    """Derive integrity binding from explicitly locked, current domain rows.

    The caller supplies database time and the latest credential under the root
    lock. This codec cannot establish that a supplied credential is the latest,
    or resolve current physician/consent/cohort/channel authority. It never
    consults a chart, makes a provider call or changes any of these rows.
    """
    binding = None
    try:
        if (
            type(checked_at) is not datetime
            or checked_at.utcoffset() is None
            or type(delivery.envelope_format_version) is not int
            or delivery.envelope_format_version != 1
            or delivery.template_version != INVITATION_TEMPLATE_VERSION
            or delivery.state not in {"queued", "submitting"}
            or type(preparation.recipient_snapshot_format_version) is not int
            or preparation.recipient_snapshot_format_version != 1
            or preparation.has_email_contact is not True
            or preparation.revoked_at is not None
            or preparation.misdirected_at is not None
            or preparation.completed_at is not None
            or credential.revoked_at is not None
            or type(credential.token_format_version) is not int
            or credential.token_format_version != 1
            or delivery.visit_prep_id != preparation.id
            or credential.visit_prep_id != preparation.id
            or delivery.owner_practice_member_id != preparation.owner_practice_member_id
            or credential.owner_practice_member_id
            != preparation.owner_practice_member_id
            or delivery.question_set_version_id != preparation.question_set_version_id
            or delivery.schedule_generation_id != credential.schedule_generation_id
            or type(delivery.credential_generation) is not int
            or type(credential.generation) is not int
            or delivery.credential_generation != credential.generation
            or credential.issued_at > checked_at
            or delivery.requested_at > checked_at
            or not checked_at < delivery.expires_at <= credential.expires_at
            or delivery.expires_at > delivery.requested_at + timedelta(hours=24)
            or (
                delivery.state == "submitting"
                and (
                    delivery.submitted_at is None
                    or delivery.lease_until is None
                    or not delivery.submitted_at <= checked_at < delivery.lease_until
                )
            )
        ):
            raise VisitPrepEmailInvalid()
        recipient_email = stored_email_recipient(preparation)
        binding = EmailInvitationBinding(
            delivery_id=str(delivery.id),
            visit_prep_id=str(preparation.id),
            owner_practice_member_id=str(preparation.owner_practice_member_id),
            question_set_version_id=str(preparation.question_set_version_id),
            schedule_generation_id=str(credential.schedule_generation_id),
            credential_generation=credential.generation,
            operation_id=str(delivery.operation_id),
            expires_at=delivery.expires_at,
            credential_token_hash=credential.token_hash,
            recipient_digest=invitation_recipient_digest(recipient_email),
            authorization_digest=invitation_authorization_digest(
                delivery.authorization_ciphertext
            ),
        )
    except (
        DecryptionError,
        ValueError,
        TypeError,
        UnicodeError,
        RecursionError,
        AttributeError,
        OverflowError,
        VisitPrepValidation,
    ):
        pass
    if binding is None:
        raise VisitPrepEmailInvalid()
    return binding


def _checked_invitation(
    invitation: VisitPrepInvitationEmail, binding: EmailInvitationBinding
) -> VisitPrepInvitationEmail:
    if type(invitation) is not VisitPrepInvitationEmail:
        raise VisitPrepEmailInvalid()
    checked = VisitPrepInvitationEmail(
        recipient=invitation.recipient,
        patient_origin=invitation.patient_origin,
        access_token=invitation.access_token,
        operation_id=invitation.operation_id,
    )
    if (
        checked.operation_id != binding.operation_id
        or not hmac.compare_digest(
            access_token_hash(checked.access_token), binding.credential_token_hash
        )
        or not hmac.compare_digest(
            invitation_recipient_digest(checked.recipient), binding.recipient_digest
        )
    ):
        raise VisitPrepEmailInvalid()
    return checked


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _closed_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise VisitPrepEmailInvalid()
        result[key] = value
    return result


def encrypt_invitation_payload(
    invitation: VisitPrepInvitationEmail, binding: EmailInvitationBinding
) -> str:
    if type(binding) is not EmailInvitationBinding:
        raise VisitPrepEmailInvalid()
    manifest = binding.manifest()
    checked = _checked_invitation(invitation, binding)
    plaintext = _json(
        {
            "manifest": manifest,
            "invitation": {
                "recipient": checked.recipient,
                "patient_origin": checked.patient_origin,
                "access_token": checked.access_token,
                "operation_id": checked.operation_id,
            },
        }
    )
    if len(plaintext.encode("utf-8")) > MAX_INVITATION_PLAINTEXT_BYTES:
        raise VisitPrepEmailInvalid()
    ciphertext = None
    try:
        ciphertext = encrypt_value(plaintext)
    except EncryptionError:
        pass
    if (
        not isinstance(ciphertext, str)
        or not 1 <= len(ciphertext.encode("utf-8")) <= MAX_INVITATION_CIPHERTEXT_BYTES
    ):
        raise VisitPrepEmailInvalid()
    return ciphertext


def decrypt_invitation_payload(
    ciphertext: str,
    binding: EmailInvitationBinding,
    *,
    patient_origin: str,
) -> VisitPrepInvitationEmail:
    if type(binding) is not EmailInvitationBinding:
        raise VisitPrepEmailInvalid()
    expected_manifest = binding.manifest()
    invitation = None
    try:
        if (
            not isinstance(ciphertext, str)
            or not 1
            <= len(ciphertext.encode("utf-8"))
            <= MAX_INVITATION_CIPHERTEXT_BYTES
        ):
            raise VisitPrepEmailInvalid()
        plaintext = decrypt_value(ciphertext)
        if len(plaintext.encode("utf-8")) > MAX_INVITATION_PLAINTEXT_BYTES:
            raise VisitPrepEmailInvalid()
        payload = json.loads(plaintext, object_pairs_hook=_closed_object)
        if (
            not isinstance(payload, dict)
            or set(payload) != {"manifest", "invitation"}
            or _json(payload["manifest"]) != _json(expected_manifest)
            or not isinstance(payload["invitation"], dict)
            or set(payload["invitation"])
            != {"recipient", "patient_origin", "access_token", "operation_id"}
        ):
            raise VisitPrepEmailInvalid()
        checked = _checked_invitation(
            VisitPrepInvitationEmail(**payload["invitation"]), binding
        )
        # Reject even a valid old origin if trusted configuration has changed.
        if checked.patient_origin != patient_origin:
            raise VisitPrepEmailInvalid()
        invitation = checked
    except (DecryptionError, ValueError, TypeError, UnicodeError, RecursionError):
        pass
    if invitation is None:
        # Parsed input or provider errors can hold secrets. Do not retain their
        # exception chain in the bounded error returned to the command/worker.
        raise VisitPrepEmailInvalid()
    return invitation
