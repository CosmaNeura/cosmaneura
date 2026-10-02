"""Closed, encrypted email evidence; integrity is not source verification.

Core must resolve the latest streams and verify the underlying consent source
under locks. A physician's approval is never a recipient's consent. These
codecs are not routes, consent capture adapters, or permission to send.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.services.visit_prep_email_contract import (
    INVITATION_TEMPLATE_VERSION,
    VisitPrepEmailInvalid,
    VisitPrepInvitationEmail,
)
from app.services.visit_prep_email_payload import _canonical_ref
from app.utils.encryption import (
    DecryptionError,
    EncryptionError,
    decrypt_value,
    encrypt_value,
    pseudonymous_digest,
)

from app.models.visit_prep_email_authority import (
    VisitPrepEmailApprovalEvent,
    VisitPrepEmailConsentEvent,
)

EMAIL_INVITATION_PURPOSE = "visit_prep_email_invitation"
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_MAX_PLAINTEXT = 4_500
_MAX_CIPHERTEXT = 8_192
_INVALID = (
    VisitPrepEmailInvalid,
    EncryptionError,
    DecryptionError,
    ValueError,
    TypeError,
    AttributeError,
    UnicodeError,
    RecursionError,
    OverflowError,
)


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _time(value: object) -> datetime:
    if type(value) is not datetime or value.utcoffset() is None:
        raise VisitPrepEmailInvalid()
    return value.astimezone(UTC)


def _uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = UUID(value)
    return str(parsed) == value and parsed.version == 4


def _digest(value: object) -> bool:
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


def _text(payload: dict, field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str):
        raise VisitPrepEmailInvalid()
    return value


def email_policy_fingerprint() -> str:
    """Fingerprint the actual fixed template and existing outbox limits.

    Public placeholder values are not recipients or credentials. A template or
    policy change invalidates old standing approval at runtime; no cached hash
    or caller-supplied policy can silently authorize changed message content.
    """
    invitation = VisitPrepInvitationEmail(
        recipient="policy@example.invalid",
        patient_origin="https://policy.example.invalid",
        access_token="vp1_" + "A" * 43,
        operation_id="00000000-0000-4000-8000-000000000001",
    )
    link = (
        f"{invitation.patient_origin}/visit-prep#access_token={invitation.access_token}"
    )
    policy = {
        "version": 1,
        "purpose": EMAIL_INVITATION_PURPOSE,
        "template_version": INVITATION_TEMPLATE_VERSION,
        "subject": invitation.subject,
        "body": invitation.body.replace(link, "{private_preparation_link}"),
        "maximum_secret_hours": 24,
        "one_submission_per_credential": True,
    }
    return hashlib.sha256(_json(policy).encode("utf-8")).hexdigest()


@dataclass(frozen=True, repr=False)
class EmailApprovalEvidence:
    approval_event_id: str
    owner_practice_member_id: str
    practice_id: str
    owner_doctor_id: str
    event_sequence: int
    event_kind: str
    operation_id: str
    policy_fingerprint: str
    actor_doctor_id: str
    occurred_at: datetime
    expires_at: datetime | None

    def manifest(self) -> dict:
        valid = False
        occurred = None
        try:
            valid = (
                _canonical_ref(self.approval_event_id, "visit_prep_email_approval")
                and _canonical_ref(self.owner_practice_member_id, "practice_member")
                and _canonical_ref(self.practice_id, "practice")
                and _canonical_ref(self.owner_doctor_id, "doctor")
                and self.actor_doctor_id == self.owner_doctor_id
                and type(self.event_sequence) is int
                and 0 < self.event_sequence <= 2_147_483_647
                and self.event_kind in {"approved", "withdrawn"}
                and _uuid(self.operation_id)
                and _digest(self.policy_fingerprint)
                and (
                    _time(self.expires_at) > _time(self.occurred_at)
                    if self.event_kind == "approved"
                    else self.expires_at is None
                )
            )
            occurred = _time(self.occurred_at)
        except _INVALID:
            valid = False
        if not valid or occurred is None:
            raise VisitPrepEmailInvalid()
        result = asdict(self)
        result["occurred_at"] = occurred.isoformat()
        result["expires_at"] = (
            _time(self.expires_at).isoformat() if self.expires_at is not None else None
        )
        return result | {
            "format_version": 1,
            "purpose": EMAIL_INVITATION_PURPOSE,
            "template_version": INVITATION_TEMPLATE_VERSION,
            "source_kind": "physician_policy_confirmation",
        }

    def __repr__(self) -> str:
        return "EmailApprovalEvidence(<redacted>)"


@dataclass(frozen=True, repr=False)
class EmailConsentEvidence:
    consent_event_id: str
    visit_prep_id: str
    owner_practice_member_id: str
    question_set_version_id: str
    event_sequence: int
    event_kind: str
    actor_role: str
    operation_id: str
    source_event_id: str
    source_kind: str
    actor_binding_digest: str
    source_payload_digest: str
    recipient_digest: str
    effective_at: datetime
    expires_at: datetime | None

    def manifest(self) -> dict:
        valid = False
        effective = None
        try:
            valid = (
                _canonical_ref(self.consent_event_id, "visit_prep_email_consent")
                and _canonical_ref(self.visit_prep_id, "visit_prep")
                and _canonical_ref(self.owner_practice_member_id, "practice_member")
                and _canonical_ref(
                    self.question_set_version_id, "visit_prep_question_set_version"
                )
                and type(self.event_sequence) is int
                and 0 < self.event_sequence <= 2_147_483_647
                and self.event_kind in {"granted", "withdrawn"}
                and (self.actor_role, self.source_kind)
                in {
                    ("patient", "patient_confirmation"),
                    ("caregiver", "caregiver_confirmation"),
                    ("trusted_source", "verified_booking"),
                }
                and _uuid(self.operation_id)
                and _uuid(self.source_event_id)
                and all(
                    _digest(value)
                    for value in (
                        self.actor_binding_digest,
                        self.source_payload_digest,
                        self.recipient_digest,
                    )
                )
                and (
                    _time(self.expires_at) > _time(self.effective_at)
                    if self.event_kind == "granted"
                    else self.expires_at is None
                )
            )
            effective = _time(self.effective_at)
        except _INVALID:
            valid = False
        if not valid or effective is None:
            raise VisitPrepEmailInvalid()
        result = asdict(self)
        result["effective_at"] = effective.isoformat()
        result["expires_at"] = (
            _time(self.expires_at).isoformat() if self.expires_at is not None else None
        )
        return result | {"format_version": 1, "purpose": EMAIL_INVITATION_PURPOSE}

    def __repr__(self) -> str:
        return "EmailConsentEvidence(<redacted>)"


@dataclass(frozen=True, repr=False)
class SealedEmailEvidence:
    ciphertext: str
    payload_hmac_sha256: str

    def __repr__(self) -> str:
        return "SealedEmailEvidence(<redacted>)"


def seal_email_evidence(
    evidence: EmailApprovalEvidence | EmailConsentEvidence,
) -> SealedEmailEvidence:
    sealed = None
    try:
        if type(evidence) not in {EmailApprovalEvidence, EmailConsentEvidence}:
            raise VisitPrepEmailInvalid()
        plaintext = _json(evidence.manifest())
        if len(plaintext.encode("utf-8")) > _MAX_PLAINTEXT:
            raise VisitPrepEmailInvalid()
        context = (
            "visit-prep-email-approval-evidence-v1"
            if type(evidence) is EmailApprovalEvidence
            else "visit-prep-email-consent-evidence-v1"
        )
        digest = pseudonymous_digest(plaintext, context=context)
        ciphertext = encrypt_value(plaintext)
        if not 1 <= len(ciphertext.encode("utf-8")) <= _MAX_CIPHERTEXT:
            raise VisitPrepEmailInvalid()
        sealed = SealedEmailEvidence(ciphertext, digest)
    except _INVALID:
        pass
    if sealed is None:
        raise VisitPrepEmailInvalid()
    return sealed


def _closed_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise VisitPrepEmailInvalid()
        result[key] = value
    return result


def _read(row: VisitPrepEmailApprovalEvent | VisitPrepEmailConsentEvent) -> dict:
    if type(row.envelope_format_version) is not int or row.envelope_format_version != 1:
        raise VisitPrepEmailInvalid()
    if (
        not isinstance(row.evidence_ciphertext, str)
        or not 1 <= len(row.evidence_ciphertext.encode("utf-8")) <= _MAX_CIPHERTEXT
        or not _digest(row.payload_hmac_sha256)
    ):
        raise VisitPrepEmailInvalid()
    plaintext = decrypt_value(row.evidence_ciphertext)
    if len(plaintext.encode("utf-8")) > _MAX_PLAINTEXT:
        raise VisitPrepEmailInvalid()
    payload = json.loads(plaintext, object_pairs_hook=_closed_object)
    if not isinstance(payload, dict):
        raise VisitPrepEmailInvalid()
    context = (
        "visit-prep-email-approval-evidence-v1"
        if type(row) is VisitPrepEmailApprovalEvent
        else "visit-prep-email-consent-evidence-v1"
    )
    if not hmac.compare_digest(
        pseudonymous_digest(_json(payload), context=context), row.payload_hmac_sha256
    ):
        raise VisitPrepEmailInvalid()
    if _time(row.recorded_at) != _time(row.created_at):
        raise VisitPrepEmailInvalid()
    if row.expires_at is not None and _time(row.expires_at) <= _time(row.recorded_at):
        raise VisitPrepEmailInvalid()
    return payload


def read_email_approval(row: VisitPrepEmailApprovalEvent) -> EmailApprovalEvidence:
    evidence = None
    try:
        if type(row) is not VisitPrepEmailApprovalEvent:
            raise VisitPrepEmailInvalid()
        payload = _read(row)
        if row.template_version != INVITATION_TEMPLATE_VERSION:
            raise VisitPrepEmailInvalid()
        checked = EmailApprovalEvidence(
            approval_event_id=str(row.id),
            owner_practice_member_id=str(row.owner_practice_member_id),
            practice_id=str(row.practice_id),
            owner_doctor_id=str(row.owner_doctor_id),
            event_sequence=row.event_sequence,
            event_kind=row.event_kind,
            operation_id=str(row.operation_id),
            policy_fingerprint=row.policy_fingerprint,
            actor_doctor_id=_text(payload, "actor_doctor_id"),
            occurred_at=datetime.fromisoformat(_text(payload, "occurred_at")),
            expires_at=row.expires_at,
        )
        if _json(checked.manifest()) != _json(payload) or _time(
            checked.occurred_at
        ) > _time(row.recorded_at):
            raise VisitPrepEmailInvalid()
        evidence = checked
    except _INVALID:
        pass
    if evidence is None:
        raise VisitPrepEmailInvalid()
    return evidence


def read_email_consent(row: VisitPrepEmailConsentEvent) -> EmailConsentEvidence:
    evidence = None
    try:
        if type(row) is not VisitPrepEmailConsentEvent:
            raise VisitPrepEmailInvalid()
        payload = _read(row)
        if row.purpose != EMAIL_INVITATION_PURPOSE:
            raise VisitPrepEmailInvalid()
        checked = EmailConsentEvidence(
            consent_event_id=str(row.id),
            visit_prep_id=str(row.visit_prep_id),
            owner_practice_member_id=str(row.owner_practice_member_id),
            question_set_version_id=str(row.question_set_version_id),
            event_sequence=row.event_sequence,
            event_kind=row.event_kind,
            actor_role=row.actor_role,
            operation_id=str(row.operation_id),
            source_event_id=str(row.source_event_id),
            source_kind=_text(payload, "source_kind"),
            actor_binding_digest=_text(payload, "actor_binding_digest"),
            source_payload_digest=_text(payload, "source_payload_digest"),
            recipient_digest=row.recipient_digest,
            effective_at=row.effective_at,
            expires_at=row.expires_at,
        )
        if _json(checked.manifest()) != _json(payload) or _time(
            checked.effective_at
        ) > _time(row.recorded_at):
            raise VisitPrepEmailInvalid()
        evidence = checked
    except _INVALID:
        pass
    if evidence is None:
        raise VisitPrepEmailInvalid()
    return evidence
