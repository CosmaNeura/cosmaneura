"""Durable physician SMS choices; independent sender and patient gates remain."""

import hmac
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, cast
from uuid import UUID

from typeid import TypeID

from app.configuration.visit_prep import VisitPrepSettings
from app.services.visit_prep_email_evidence import (
    _closed_object,
    _digest,
    _json,
    _time,
    _uuid,
)
from app.services.visit_prep_email_payload import _canonical_ref
from app.services.visit_prep_email_policy import _authorize_owner
from app.services.visit_prep_packages import _database_now
from app.services.visit_prep_sms_contract import (
    POLICY_FINGERPRINT,
    SMSStandingApproval,
    VisitPrepSMSUnavailable,
)
from app.services.visit_prep_sms_suppression import configured_sms_sender_digest
from app.utils.encryption import decrypt_value, encrypt_value, pseudonymous_digest

from activemodel.session_manager import get_engine
from activemodel.types import TypeIDType
from app.models.authority import AuthorityAuditEvent
from app.models.visit_prep_sms_approval import VisitPrepSMSApprovalEvent
from sqlmodel import Session, col, select


@dataclass(frozen=True, repr=False)
class SMSPolicyReceipt:
    event_id: str
    event_sequence: int
    current_event_sequence: int
    event_kind: Literal["approved", "withdrawn"]
    policy_fingerprint: str
    recorded_at: datetime
    expires_at: datetime | None
    disposition: Literal["recorded", "replayed"]


class SMSSenderSourceVerifier(Protocol):
    def __call__(
        self, session: Session, *, sender_digest: str, checked_at: datetime
    ) -> bool:
        """Verify the exact current configured sender/account and qualified source.

        Trusted Core adapter only, no accepting default. Must compare the sender
        digest against authenticated durable provider/account/number evidence,
        current service approval and source locks. No I/O, commit or lock release.
        A physician policy, configuration string or sealed label is insufficient.
        """
        ...


def _subject(subject: str) -> str:
    return pseudonymous_digest(subject, context="visit-prep-sms-policy-subject-v1")


def _manifest(row: VisitPrepSMSApprovalEvent) -> dict:
    return {
        "format_version": 1,
        "purpose": "visit_prep_sms_physician_policy",
        "event_id": str(row.id),
        "owner_practice_member_id": str(row.owner_practice_member_id),
        "owner_doctor_id": str(row.owner_doctor_id),
        "practice_id": str(row.practice_id),
        "event_sequence": row.event_sequence,
        "event_kind": row.event_kind,
        "operation_id": str(row.operation_id),
        "template_version": row.template_version,
        "sender_digest": row.sender_digest,
        "subject_digest": row.subject_digest,
        "policy_fingerprint": row.policy_fingerprint,
        "recorded_at": _time(row.recorded_at).isoformat(),
        "expires_at": _time(row.expires_at).isoformat()
        if row.expires_at is not None
        else None,
    }


def _refs(row: VisitPrepSMSApprovalEvent) -> list[dict[str, str]]:
    return [
        {"kind": "doctor", "ref": str(row.owner_doctor_id)},
        {"kind": "practice", "ref": str(row.practice_id)},
        {"kind": "practice_member", "ref": str(row.owner_practice_member_id)},
    ]


def _verify(session: Session, row: VisitPrepSMSApprovalEvent) -> None:
    if (
        not all(
            _canonical_ref(str(value), prefix)
            for value, prefix in (
                (row.id, "visit_prep_sms_approval"),
                (row.owner_practice_member_id, "practice_member"),
                (row.owner_doctor_id, "doctor"),
                (row.practice_id, "practice"),
            )
        )
        or type(row.event_sequence) is not int
        or not 1 <= row.event_sequence <= 2_147_483_647
        or row.event_kind not in {"approved", "withdrawn"}
        or row.template_version != "visit_prep_sms_v1"
        or not _uuid(str(row.operation_id))
        or type(row.envelope_format_version) is not int
        or row.envelope_format_version != 1
        or not all(
            _digest(v)
            for v in (
                row.sender_digest,
                row.subject_digest,
                row.policy_fingerprint,
                row.payload_hmac,
            )
        )
        or _time(row.recorded_at) > _database_now(session)
        or (
            row.expires_at is None or _time(row.expires_at) <= _time(row.recorded_at)
            if row.event_kind == "approved"
            else row.expires_at is not None
        )
        or type(row.evidence_ciphertext) is not str
        or not 1 <= len(row.evidence_ciphertext.encode()) <= 4096
    ):
        raise VisitPrepSMSUnavailable()
    plaintext = decrypt_value(row.evidence_ciphertext)
    if len(plaintext.encode()) > 2500:
        raise VisitPrepSMSUnavailable()
    payload = json.loads(plaintext, object_pairs_hook=_closed_object)
    if _json(payload) != _json(_manifest(row)) or not hmac.compare_digest(
        row.payload_hmac,
        pseudonymous_digest(
            _json(payload), context="visit-prep-sms-policy-evidence-v1"
        ),
    ):
        raise VisitPrepSMSUnavailable()
    audits = session.exec(
        select(AuthorityAuditEvent)
        .where(
            AuthorityAuditEvent.resource_type == "visit_prep_sms_approval",
            AuthorityAuditEvent.resource_ref == str(row.id),
        )
        .limit(2)
    ).all()
    if len(audits) != 1:
        raise VisitPrepSMSUnavailable()
    audit = audits[0]
    if (
        audit.event_source != "platform_authoritative"
        or audit.event_type != f"visit_prep.sms_policy_{row.event_kind}"
        or audit.outcome != "allowed"
        or audit.client_event_id != row.operation_id
        or audit.request_digest != row.payload_hmac
        or audit.route != "visit_prep_sms_policy"
        or audit.intent != "physician_sms_policy_confirmation"
        or audit.platform_refs != _refs(row)
        or audit.occurred_at != row.recorded_at
        or not row.recorded_at <= audit.recorded_at <= _database_now(session)
        or any(
            v is not None
            for v in (
                audit.actor_id,
                audit.person_id,
                audit.tenant_id,
                audit.interaction_id,
                audit.workload_client_id,
                audit.content_sha256,
            )
        )
    ):
        raise VisitPrepSMSUnavailable()


def _latest(session: Session, owner: TypeIDType) -> VisitPrepSMSApprovalEvent | None:
    row = session.exec(
        select(VisitPrepSMSApprovalEvent)
        .where(
            VisitPrepSMSApprovalEvent.owner_practice_member_id == owner,
        )
        .order_by(col(VisitPrepSMSApprovalEvent.event_sequence).desc())
        .limit(1)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if row is not None:
        _verify(session, row)
    return row


def _valid_scope(scope: dict, view_as_active: bool = False) -> None:
    settings, subject = scope["settings"], scope["expected_clerk_subject"]
    if (
        type(settings) is not VisitPrepSettings
        or settings.enabled is not True
        or settings.configuration_valid is not True
        or view_as_active is not False
        or type(subject) is not str
        or not 1 <= len(subject) <= 255
        or not all(
            _canonical_ref(str(scope[key]), prefix)
            for key, prefix in (
                ("owner_practice_member_id", "practice_member"),
                ("owner_doctor_id", "doctor"),
                ("practice_id", "practice"),
            )
        )
    ):
        raise VisitPrepSMSUnavailable()


def record_sms_policy_decision(
    *,
    owner_practice_member_id: TypeIDType,
    owner_doctor_id: TypeIDType,
    practice_id: TypeIDType,
    expected_clerk_subject: str,
    settings: VisitPrepSettings,
    operation_id: str,
    expected_event_sequence: int,
    event_kind: Literal["approved", "withdrawn"],
    sender_digest: str,
    policy_fingerprint: str,
    expires_at: datetime | None,
    view_as_active: bool = False,
) -> SMSPolicyReceipt:
    """Explicit policy choice only; own transaction and exact replay, no sending.

    A future authenticated command must derive owner/subject from Core, and the
    sender must come from reviewed current routing, not a browser assertion.
    Expiry is explicit; no invented lifetime. Latest withdrawal is terminal until
    a fresh attributable approval; replay never changes the current decision.
    """
    result = None
    try:
        scope = {
            "owner_practice_member_id": owner_practice_member_id,
            "owner_doctor_id": owner_doctor_id,
            "practice_id": practice_id,
            "expected_clerk_subject": expected_clerk_subject,
            "settings": settings,
        }
        _valid_scope(scope, view_as_active)
        if (
            not _uuid(operation_id)
            or type(expected_event_sequence) is not int
            or not 0 <= expected_event_sequence < 2_147_483_647
            or event_kind not in {"approved", "withdrawn"}
            or not _digest(sender_digest)
            or not _digest(policy_fingerprint)
        ):
            raise VisitPrepSMSUnavailable()
        if event_kind == "approved":
            expires_at = _time(expires_at)
        elif expires_at is not None:
            raise VisitPrepSMSUnavailable()
        with Session(get_engine()) as session:
            owner, practice, _ = _authorize_owner(session, **scope)
            latest = _latest(session, owner.id)
            sequence = latest.event_sequence if latest else 0
            prior = session.exec(
                select(VisitPrepSMSApprovalEvent)
                .where(
                    VisitPrepSMSApprovalEvent.owner_practice_member_id == owner.id,
                    VisitPrepSMSApprovalEvent.operation_id == UUID(operation_id),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            ).one_or_none()
            disposition: Literal["recorded", "replayed"] = "replayed"
            if prior is not None:
                _verify(session, prior)
                if (
                    prior.event_sequence != expected_event_sequence + 1
                    or prior.event_kind != event_kind
                    or prior.sender_digest != sender_digest
                    or prior.policy_fingerprint != policy_fingerprint
                    or prior.expires_at != expires_at
                    or prior.subject_digest != _subject(expected_clerk_subject)
                    or prior.practice_id != practice.id
                    or prior.owner_doctor_id != owner.doctor_id
                ):
                    raise VisitPrepSMSUnavailable()
                row = prior
            else:
                if (
                    expected_event_sequence != sequence
                    or (
                        event_kind == "approved"
                        and (
                            policy_fingerprint != POLICY_FINGERPRINT
                            or sender_digest != configured_sms_sender_digest()
                        )
                    )
                    or (
                        event_kind == "withdrawn"
                        and (
                            latest is None
                            or sender_digest != latest.sender_digest
                            or policy_fingerprint != latest.policy_fingerprint
                        )
                    )
                ):
                    raise VisitPrepSMSUnavailable()
                _, _, now = _authorize_owner(session, **scope)
                row = VisitPrepSMSApprovalEvent(
                    owner_practice_member_id=owner.id,
                    owner_doctor_id=owner.doctor_id,
                    practice_id=practice.id,
                    event_sequence=sequence + 1,
                    event_kind=event_kind,
                    sender_digest=sender_digest,
                    subject_digest=_subject(expected_clerk_subject),
                    policy_fingerprint=policy_fingerprint,
                    operation_id=UUID(operation_id),
                    recorded_at=now,
                    expires_at=expires_at,
                    payload_hmac="0" * 64,
                    evidence_ciphertext="pending",
                )
                row.id = cast(TypeIDType, TypeID("visit_prep_sms_approval"))
                plaintext = _json(_manifest(row))
                row.payload_hmac = pseudonymous_digest(
                    plaintext, context="visit-prep-sms-policy-evidence-v1"
                )
                row.evidence_ciphertext = encrypt_value(plaintext)
                session.add(row)
                session.flush()
                session.refresh(row)
                session.add(
                    AuthorityAuditEvent(
                        event_type=f"visit_prep.sms_policy_{event_kind}",
                        outcome="allowed",
                        occurred_at=row.recorded_at,
                        client_event_id=row.operation_id,
                        request_digest=row.payload_hmac,
                        resource_type="visit_prep_sms_approval",
                        resource_ref=str(row.id),
                        route="visit_prep_sms_policy",
                        intent="physician_sms_policy_confirmation",
                        platform_refs=_refs(row),
                    )
                )
                session.flush()
                _verify(session, row)
                sequence, disposition = row.event_sequence, "recorded"
            _, _, now = _authorize_owner(session, **scope)
            if (
                disposition == "recorded"
                and event_kind == "approved"
                and (
                    row.expires_at is None
                    or row.expires_at <= now
                    or row.policy_fingerprint != POLICY_FINGERPRINT
                    or row.sender_digest != configured_sms_sender_digest()
                )
            ):
                raise VisitPrepSMSUnavailable()
            result = SMSPolicyReceipt(
                str(row.id),
                row.event_sequence,
                sequence,
                cast(Literal["approved", "withdrawn"], row.event_kind),
                row.policy_fingerprint,
                row.recorded_at,
                row.expires_at,
                disposition,
            )
            session.commit()
    except Exception:
        result = None
    if result is None:
        raise VisitPrepSMSUnavailable() from None
    return result


def _approval(row: VisitPrepSMSApprovalEvent, subject: str) -> SMSStandingApproval:
    if (
        row.event_kind != "approved"
        or row.expires_at is None
        or row.subject_digest != _subject(subject)
    ):
        raise VisitPrepSMSUnavailable()
    return SMSStandingApproval(
        str(row.owner_practice_member_id),
        str(row.owner_doctor_id),
        str(row.practice_id),
        subject,
        row.sender_digest,
        row.policy_fingerprint,
        pseudonymous_digest(str(row.id), context="visit-prep-sms-approval-source-v1"),
        row.payload_hmac,
        row.recorded_at,
        row.expires_at,
    )


def _read_locked_sms_policy_approval(session: Session, **scope) -> SMSStandingApproval:
    _valid_scope(scope)
    owner, practice, _ = _authorize_owner(session, **scope)
    row = _latest(session, owner.id)
    if (
        row is None
        or row.practice_id != practice.id
        or row.owner_doctor_id != owner.doctor_id
    ):
        raise VisitPrepSMSUnavailable()
    approval = _approval(row, scope["expected_clerk_subject"])
    _, _, now = _authorize_owner(session, **scope)
    if (
        approval.policy_fingerprint != POLICY_FINGERPRINT
        or approval.sender_digest != configured_sms_sender_digest()
        or not approval.effective_at <= now < approval.expires_at
    ):
        raise VisitPrepSMSUnavailable()
    return approval


def read_locked_sms_policy_approval(session: Session, **scope) -> SMSStandingApproval:
    """Normalize the current durable physician choice, not a transferable grant."""
    result = None
    try:
        result = _read_locked_sms_policy_approval(session, **scope)
    except Exception:
        result = None
    if result is None:
        raise VisitPrepSMSUnavailable() from None
    return result


@dataclass(frozen=True, repr=False)
class CoreSMSApprovalVerifier:
    settings: VisitPrepSettings
    sender_source_verifier: SMSSenderSourceVerifier | None = None

    def __call__(
        self, session: Session, approval: SMSStandingApproval, *, checked_at: datetime
    ) -> bool:
        """Compare latest physician choice AND separately qualified sender source.

        This is deliberately not installed by default. It is usable only by a
        trusted worker composition that supplies the qualified sender adapter.
        Patient consent/control verification remains another independent gate.
        """
        try:
            if type(approval) is not SMSStandingApproval or not callable(
                self.sender_source_verifier
            ):
                return False
            scope = {
                "owner_practice_member_id": TypeID.from_string(
                    approval.owner_practice_member_id
                ),
                "owner_doctor_id": TypeID.from_string(approval.owner_doctor_id),
                "practice_id": TypeID.from_string(approval.practice_id),
                "expected_clerk_subject": approval.subject,
                "settings": self.settings,
            }
            transaction, nested = (
                session.get_transaction(),
                session.get_nested_transaction(),
            )
            if (
                transaction is None
                or not transaction.is_active
                or _time(checked_at) > _database_now(session)
            ):
                return False
            if read_locked_sms_policy_approval(session, **scope) != approval:
                return False
            if (
                self.sender_source_verifier(
                    session,
                    sender_digest=approval.sender_digest,
                    checked_at=_database_now(session),
                )
                is not True
            ):
                return False
            if (
                session.get_transaction() is not transaction
                or session.get_nested_transaction() is not nested
                or not transaction.is_active
            ):
                return False
            return read_locked_sms_policy_approval(session, **scope) == approval
        except Exception:
            return False
