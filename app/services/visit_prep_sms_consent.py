"""Optional, dormant SMS choices from a current confirmed invitation holder.

This is a preparation-scoped opt-in, not verified identity, first-contact consent,
sender approval or provider STOP clearance. There is no transport or callback.
Email and SMS histories, purposes and keyed digests are independent.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Iterator, Literal, cast
from uuid import UUID

from typeid import TypeID

from app.configuration.visit_prep import VisitPrepSettings
from app.services import visit_prep_access
from app.services.visit_prep_command_contract import (
    RecipientInput,
    _normalize_recipient,
)
from app.services.visit_prep_contributions import _require_recipient
from app.services.visit_prep_email_consent import _ERRORS, _capture_context, _refs
from app.services.visit_prep_email_contract import VisitPrepEmailInvalid
from app.services.visit_prep_email_evidence import (
    _closed_object,
    _digest,
    _json,
    _time,
    _uuid,
)
from app.services.visit_prep_packages import _database_now
from app.services.visit_prep_patient_continuation import (
    ConfirmedVisitPrepAccess,
    valid_confirmation_name,
)
from app.services.visit_prep_sms_suppression import (
    SMSSuppressionUnavailable,
    configured_sms_sender_digest,
    read_locked_sms_suppression,
)
from app.services.visit_preps import _visit_prep_session
from app.utils.encryption import (
    DecryptionError,
    decrypt_value,
    encrypt_value,
    pseudonymous_digest,
)

from activemodel.types import TypeIDType
from app.models.authority import AuthorityAuditEvent
from app.models.visit_prep import VisitPrep, VisitPrepCredentialGeneration
from app.models.visit_prep_sms_consent import VisitPrepSMSConsentEvent
from sqlmodel import Session, col, select

Role = Literal["patient", "caregiver"]
Kind = Literal["granted", "withdrawn"]
VERSION = "visit_prep_sms_consent_v1"
PURPOSE = "visit_prep_sms_invitation_and_reminders"
_CONTEXT = "visit-prep-sms-consent-evidence-v1"
_FAILURES = _ERRORS + (DecryptionError, RecursionError, SMSSuppressionUnavailable)


@contextmanager
def _session() -> Iterator[Session]:
    with _visit_prep_session() as session:
        try:
            yield session
        except _FAILURES:
            session.rollback()
            raise


def sms_consent_disclosure(*, role: Role, event_kind: Kind) -> str:
    if role not in {"patient", "caregiver"} or event_kind not in {
        "granted",
        "withdrawn",
    }:
        raise VisitPrepEmailInvalid()
    if event_kind == "withdrawn":
        return (
            "Do not allow further Visit Prep invitation or reminder text messages for "
            "this preparation. A text already submitted cannot be recalled. This does "
            "not change your answers or email choice. You can still prepare for your visit."
        )
    text = (
        "Allow your care team to send automated Visit Prep invitations and reminders "
        "to the mobile number already provided for this preparation, only until the "
        "expiry shown. This is optional and not a condition of care or of completing "
        "Visit Prep. Message and data rates may apply. Message frequency depends on "
        "your appointment and whether you have opened or completed your preparation. "
        "This does not permit email, marketing or other disclosures. You can withdraw "
        "permission here; when messaging is connected you can also reply STOP to stop "
        "texts. A text already submitted cannot be recalled. Do not text health "
        "information. Visit Prep is not monitored in real time and is not for emergencies. "
        "I confirm I am the intended recipient and am authorized to receive these texts "
        "at the mobile number provided. This is my attestation, not verified control of that number."
    )
    if role == "caregiver":
        text += " I attest I am authorized to make this communication choice for the patient; my legal authority has not been independently verified."
    return text


def sms_consent_disclosure_fingerprint(*, role: Role, event_kind: Kind) -> str:
    return hashlib.sha256(
        _json(
            {
                "version": VERSION,
                "purpose": PURPOSE,
                "role": role,
                "event_kind": event_kind,
                "text": sms_consent_disclosure(role=role, event_kind=event_kind),
            }
        ).encode()
    ).hexdigest()


def _control() -> None:
    settings = visit_prep_access.VISIT_PREP_SETTINGS
    if (
        type(settings) is not VisitPrepSettings
        or settings.configuration_valid is not True
        or settings.sms_consent_capture_enabled is not True
    ):
        raise VisitPrepEmailInvalid()


def stored_sms_recipient(prep: VisitPrep) -> str:
    if (
        type(prep.recipient_snapshot_format_version) is not int
        or prep.recipient_snapshot_format_version != 1
        or prep.has_sms_contact is not True
    ):
        raise VisitPrepEmailInvalid()
    ciphertext = prep.recipient_snapshot_ciphertext
    if not isinstance(ciphertext, str) or not 1 <= len(ciphertext.encode()) <= 16_384:
        raise VisitPrepEmailInvalid()
    plaintext = decrypt_value(ciphertext)
    if len(plaintext.encode()) > 12_000:
        raise VisitPrepEmailInvalid()
    payload = json.loads(plaintext, object_pairs_hook=_closed_object)
    if (
        not isinstance(payload, dict)
        or set(payload) != {"version", "first_name", "role", "email", "phone"}
        or type(payload["version"]) is not int
        or payload["version"] != 1
    ):
        raise VisitPrepEmailInvalid()
    recipient = _normalize_recipient(
        RecipientInput(
            first_name=payload["first_name"],
            role=payload["role"],
            email=payload["email"],
            phone=payload["phone"],
        )
    )
    if (
        recipient.phone is None
        or recipient.phone != payload["phone"]
        or asdict(recipient)
        != {key: payload[key] for key in ("first_name", "role", "email", "phone")}
        or re.fullmatch(r"\+[1-9][0-9]{7,14}", recipient.phone) is None
    ):
        raise VisitPrepEmailInvalid()
    return recipient.phone


def _recipient(prep: VisitPrep) -> str:
    return pseudonymous_digest(
        stored_sms_recipient(prep), context="visit-prep-sms-recipient-v1"
    )


@dataclass(frozen=True, repr=False)
class PatientSMSConsentInput:
    operation_id: str
    expected_event_sequence: int
    event_kind: Kind
    role: Role
    disclosure_fingerprint: str
    expires_at: datetime | None
    recipient_control_attested: bool
    caregiver_authority_attested: bool


@dataclass(frozen=True, repr=False)
class PatientSMSConsentReceipt:
    event_sequence: int
    current_event_sequence: int
    event_kind: Kind
    expires_at: datetime | None
    recorded_at: datetime
    disposition: Literal["recorded", "replayed"]


@dataclass(frozen=True, repr=False)
class PatientSMSConsentView:
    actor_role: Role
    disclosure_version: str
    grant_disclosure: str
    grant_disclosure_fingerprint: str
    withdrawal_disclosure: str
    withdrawal_disclosure_fingerprint: str
    status: Literal["not_recorded", "grant_recorded", "withdrawn", "expired"]
    event_sequence: int
    recorded_at: datetime | None
    expires_at: datetime | None
    maximum_expires_at: datetime
    can_grant: bool
    checked_at: datetime
    contact_available: bool
    phone_suppression_status: Literal["not_connected", "not_recorded", "stopped"]


def _suppression(session: Session, prep: VisitPrep):
    sender = configured_sms_sender_digest()
    if sender is None or prep.has_sms_contact is not True:
        return "not_connected"
    return (
        "stopped"
        if read_locked_sms_suppression(
            session, sender_digest=sender, recipient_digest=_recipient(prep)
        )
        else "not_recorded"
    )


def _manifest(
    row: VisitPrepSMSConsentEvent,
    prep: VisitPrep,
    credential: VisitPrepCredentialGeneration,
) -> dict:
    role, kind = row.actor_role, row.event_kind
    if role not in {"patient", "caregiver"} or kind not in {"granted", "withdrawn"}:
        raise VisitPrepEmailInvalid()
    return {
        "format_version": 1,
        "purpose": PURPOSE,
        "disclosure_version": VERSION,
        "event_id": str(row.id),
        "visit_prep_id": str(row.visit_prep_id),
        "owner_practice_member_id": str(row.owner_practice_member_id),
        "question_set_version_id": str(row.question_set_version_id),
        "credential_id": str(row.credential_id),
        "event_sequence": row.event_sequence,
        "event_kind": kind,
        "actor_role": role,
        "operation_id": str(row.operation_id),
        "recipient_digest": row.recipient_digest,
        "disclosure_fingerprint": row.disclosure_fingerprint,
        "effective_at": _time(row.effective_at).isoformat(),
        "expires_at": _time(row.expires_at).isoformat() if row.expires_at else None,
        "recipient_control_attested": kind == "granted",
        "caregiver_authority_attested": kind == "granted" and role == "caregiver",
        "actor_binding_digest": pseudonymous_digest(
            _json(
                {
                    "credential": str(credential.id),
                    "verifier": credential.token_hash,
                    "confirmed_at": _time(prep.confirmed_at).isoformat(),
                    "role": role,
                }
            ),
            context="visit-prep-sms-consent-actor-v1",
        ),
    }


def _verify(session: Session, row: VisitPrepSMSConsentEvent, prep: VisitPrep) -> None:
    credential = session.get(
        VisitPrepCredentialGeneration, row.credential_id, populate_existing=True
    )
    if (
        credential is None
        or credential.visit_prep_id != prep.id
        or credential.owner_practice_member_id != prep.owner_practice_member_id
        or row.visit_prep_id != prep.id
        or row.owner_practice_member_id != prep.owner_practice_member_id
        or row.question_set_version_id != prep.question_set_version_id
        or row.actor_role != prep.confirmed_by_role
        or not _uuid(str(row.operation_id))
        or type(row.envelope_format_version) is not int
        or row.envelope_format_version != 1
        or type(row.event_sequence) is not int
        or row.event_sequence < 1
        or not _digest(row.payload_hmac_sha256)
        or not _digest(credential.token_hash)
        or type(credential.token_format_version) is not int
        or credential.token_format_version != 1
        or _time(row.effective_at) < _time(prep.confirmed_at)
        or credential.issued_at > row.effective_at
        or credential.expires_at <= row.effective_at
        or (
            credential.revoked_at is not None
            and credential.revoked_at <= row.effective_at
        )
        or row.recorded_at < row.effective_at
        or row.recorded_at > _database_now(session)
        or (
            row.event_kind == "granted"
            and (row.expires_at is None or row.expires_at <= row.recorded_at)
        )
        or (row.event_kind == "withdrawn" and row.expires_at is not None)
        or row.recipient_digest != _recipient(prep)
        or row.disclosure_fingerprint
        != sms_consent_disclosure_fingerprint(
            role=cast(Role, row.actor_role), event_kind=cast(Kind, row.event_kind)
        )
    ):
        raise VisitPrepEmailInvalid()
    if (
        not isinstance(row.evidence_ciphertext, str)
        or not 1 <= len(row.evidence_ciphertext.encode()) <= 8192
    ):
        raise VisitPrepEmailInvalid()
    plaintext = decrypt_value(row.evidence_ciphertext)
    if len(plaintext.encode()) > 4500:
        raise VisitPrepEmailInvalid()
    payload = json.loads(plaintext, object_pairs_hook=_closed_object)
    if _json(payload) != _json(
        _manifest(row, prep, credential)
    ) or not hmac.compare_digest(
        pseudonymous_digest(_json(payload), context=_CONTEXT), row.payload_hmac_sha256
    ):
        raise VisitPrepEmailInvalid()
    audits = session.exec(
        select(AuthorityAuditEvent)
        .where(
            AuthorityAuditEvent.resource_type == "visit_prep_sms_consent",
            AuthorityAuditEvent.resource_ref == str(row.id),
        )
        .limit(2)
    ).all()
    if len(audits) != 1:
        raise VisitPrepEmailInvalid()
    audit = audits[0]
    if (
        audit.event_source != "platform_authoritative"
        or audit.event_type != f"visit_prep.sms_consent_{row.event_kind}"
        or audit.outcome != row.event_kind
        or audit.client_event_id != row.operation_id
        or audit.request_digest != row.payload_hmac_sha256
        or audit.content_sha256 != row.disclosure_fingerprint
        or audit.route != "visit_prep_patient_sms_consent"
        or audit.intent != "confirmed_bearer_sms_choice"
        or audit.platform_refs != _refs(prep, credential)
        or audit.occurred_at != row.recorded_at
        or not audit.occurred_at <= audit.recorded_at <= _database_now(session)
        or any(
            value is not None
            for value in (
                audit.actor_id,
                audit.person_id,
                audit.tenant_id,
                audit.interaction_id,
                audit.workload_client_id,
            )
        )
    ):
        raise VisitPrepEmailInvalid()


def _latest(session: Session, prep: VisitPrep) -> VisitPrepSMSConsentEvent | None:
    row = session.exec(
        select(VisitPrepSMSConsentEvent)
        .where(VisitPrepSMSConsentEvent.visit_prep_id == prep.id)
        .order_by(col(VisitPrepSMSConsentEvent.event_sequence).desc())
        .limit(1)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if row is not None:
        _verify(session, row, prep)
    return row


def _checked(
    session: Session,
    access_token: str | ConfirmedVisitPrepAccess,
    first_name: str,
    role: Role,
):
    _control()
    authorized = _capture_context(session, access_token=access_token, role=role)
    _require_recipient(authorized, first_name=first_name, role=role)
    return authorized


def get_patient_sms_consent_view(
    *,
    access_token: str | ConfirmedVisitPrepAccess,
    first_name: str,
    role: Role,
    view_as_active: bool = False,
) -> PatientSMSConsentView:
    result = None
    try:
        if (
            view_as_active is not False
            or not valid_confirmation_name(first_name, access_token)
            or role not in {"patient", "caregiver"}
        ):
            raise VisitPrepEmailInvalid()
        with _session() as session:
            authorized = _checked(session, access_token, first_name, role)
            prep = authorized.visit_prep
            contact = prep.has_sms_contact is True
            if contact:
                _recipient(prep)
            row = _latest(session, prep)
            grant = sms_consent_disclosure_fingerprint(role=role, event_kind="granted")
            withdrawal = sms_consent_disclosure_fingerprint(
                role=role, event_kind="withdrawn"
            )
            session.add(
                AuthorityAuditEvent(
                    event_type="visit_prep.sms_consent_read",
                    outcome="allowed",
                    occurred_at=_database_now(session),
                    recorded_at=_database_now(session),
                    resource_type="visit_prep",
                    resource_ref=str(prep.id),
                    content_sha256=grant,
                    route="visit_prep_patient_sms_consent_read",
                    intent="confirmed_bearer_sms_choice_review",
                    platform_refs=_refs(prep, authorized.credential),
                )
            )
            session.flush()
            suppression = _suppression(session, prep)
            checked = _checked(session, access_token, first_name, role)
            maximum = min(checked.credential.expires_at, checked.schedule.scheduled_at)
            status: Literal[
                "not_recorded", "grant_recorded", "withdrawn", "expired"
            ] = (
                "not_recorded"
                if row is None
                else "withdrawn"
                if row.event_kind == "withdrawn"
                else "expired"
                if row.expires_at is None
                or min(row.expires_at, maximum) <= checked.event_at
                else "grant_recorded"
            )
            result = PatientSMSConsentView(
                role,
                VERSION,
                sms_consent_disclosure(role=role, event_kind="granted"),
                grant,
                sms_consent_disclosure(role=role, event_kind="withdrawn"),
                withdrawal,
                status,
                row.event_sequence if row else 0,
                row.recorded_at if row else None,
                row.expires_at if row else None,
                maximum,
                contact
                and suppression != "stopped"
                and prep.completed_at is None
                and maximum > checked.event_at,
                checked.event_at,
                contact,
                suppression,
            )
            if grant != sms_consent_disclosure_fingerprint(
                role=role, event_kind="granted"
            ) or withdrawal != sms_consent_disclosure_fingerprint(
                role=role, event_kind="withdrawn"
            ):
                raise VisitPrepEmailInvalid()
            session.commit()
    except _FAILURES:
        result = None
    if result is None:
        raise VisitPrepEmailInvalid()
    return result


def record_patient_sms_consent(
    *,
    access_token: str | ConfirmedVisitPrepAccess,
    first_name: str,
    decision: PatientSMSConsentInput,
    view_as_active: bool = False,
) -> PatientSMSConsentReceipt:
    result = None
    try:
        if (
            type(decision) is not PatientSMSConsentInput
            or view_as_active is not False
            or not valid_confirmation_name(first_name, access_token)
            or not _uuid(decision.operation_id)
            or type(decision.expected_event_sequence) is not int
            or not 0 <= decision.expected_event_sequence < 2_147_483_647
            or type(decision.recipient_control_attested) is not bool
            or type(decision.caregiver_authority_attested) is not bool
            or (
                decision.recipient_control_attested,
                decision.caregiver_authority_attested,
            )
            != (
                decision.event_kind == "granted",
                decision.event_kind == "granted" and decision.role == "caregiver",
            )
            or decision.disclosure_fingerprint
            != sms_consent_disclosure_fingerprint(
                role=decision.role, event_kind=decision.event_kind
            )
            or (decision.event_kind == "withdrawn" and decision.expires_at is not None)
        ):
            raise VisitPrepEmailInvalid()
        expiry = (
            _time(decision.expires_at) if decision.event_kind == "granted" else None
        )
        with _session() as session:
            authorized = _checked(session, access_token, first_name, decision.role)
            prep = authorized.visit_prep
            recipient = _recipient(prep)
            latest = _latest(session, prep)
            sequence = latest.event_sequence if latest else 0
            prior = session.exec(
                select(VisitPrepSMSConsentEvent)
                .where(
                    VisitPrepSMSConsentEvent.visit_prep_id == prep.id,
                    VisitPrepSMSConsentEvent.operation_id
                    == UUID(decision.operation_id),
                )
                .with_for_update()
            ).one_or_none()
            if prior is not None:
                _verify(session, prior, prep)
                if (
                    prior.event_sequence != decision.expected_event_sequence + 1
                    or prior.event_kind != decision.event_kind
                    or prior.actor_role != decision.role
                    or prior.expires_at != expiry
                    or prior.disclosure_fingerprint != decision.disclosure_fingerprint
                ):
                    raise VisitPrepEmailInvalid()
                row = prior
                disposition: Literal["recorded", "replayed"] = "replayed"
            else:
                if decision.expected_event_sequence != sequence:
                    raise VisitPrepEmailInvalid()
                now = _database_now(session)
                if decision.event_kind == "granted" and (
                    _suppression(session, prep) == "stopped"
                    or prep.completed_at is not None
                    or expiry is None
                    or not now
                    < expiry
                    <= min(
                        authorized.credential.expires_at,
                        authorized.schedule.scheduled_at,
                    )
                ):
                    raise VisitPrepEmailInvalid()
                row = VisitPrepSMSConsentEvent(
                    visit_prep_id=prep.id,
                    owner_practice_member_id=prep.owner_practice_member_id,
                    question_set_version_id=prep.question_set_version_id,
                    credential_id=authorized.credential.id,
                    event_sequence=sequence + 1,
                    event_kind=decision.event_kind,
                    actor_role=decision.role,
                    operation_id=UUID(decision.operation_id),
                    recipient_digest=recipient,
                    disclosure_fingerprint=decision.disclosure_fingerprint,
                    effective_at=now,
                    recorded_at=now,
                    expires_at=expiry,
                    evidence_ciphertext="",
                    payload_hmac_sha256="0" * 64,
                )
                row.id = cast(TypeIDType, TypeID(prefix="visit_prep_sms_consent"))
                plaintext = _json(_manifest(row, prep, authorized.credential))
                if len(plaintext.encode()) > 4500:
                    raise VisitPrepEmailInvalid()
                row.evidence_ciphertext = encrypt_value(plaintext)
                row.payload_hmac_sha256 = pseudonymous_digest(
                    plaintext, context=_CONTEXT
                )
                session.add(row)
                session.flush()
                session.refresh(row)
                session.add(
                    AuthorityAuditEvent(
                        event_type=f"visit_prep.sms_consent_{decision.event_kind}",
                        outcome=decision.event_kind,
                        occurred_at=row.recorded_at,
                        recorded_at=_database_now(session),
                        client_event_id=row.operation_id,
                        resource_type="visit_prep_sms_consent",
                        resource_ref=str(row.id),
                        request_digest=row.payload_hmac_sha256,
                        content_sha256=decision.disclosure_fingerprint,
                        route="visit_prep_patient_sms_consent",
                        intent="confirmed_bearer_sms_choice",
                        platform_refs=_refs(prep, authorized.credential),
                    )
                )
                session.flush()
                _verify(session, row, prep)
                sequence, disposition = row.event_sequence, "recorded"
            if (
                disposition == "recorded"
                and decision.event_kind == "granted"
                and _suppression(session, prep) == "stopped"
            ):
                raise VisitPrepEmailInvalid()
            checked = _checked(session, access_token, first_name, decision.role)
            if decision.disclosure_fingerprint != sms_consent_disclosure_fingerprint(
                role=decision.role, event_kind=decision.event_kind
            ) or (
                disposition == "recorded"
                and decision.event_kind == "granted"
                and (expiry is None or expiry <= checked.event_at)
            ):
                raise VisitPrepEmailInvalid()
            result = PatientSMSConsentReceipt(
                row.event_sequence,
                sequence,
                decision.event_kind,
                row.expires_at,
                row.recorded_at,
                disposition,
            )
            session.commit()
    except _FAILURES:
        result = None
    if result is None:
        raise VisitPrepEmailInvalid()
    return result
