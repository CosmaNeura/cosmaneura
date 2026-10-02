"""Atomic invitation handoff to the dormant outbox, never a provider call.

The owner command and future creation/booking transaction use the same locked
helper. Qualified consent-source verification is required for new work and is
absent by default. Replaying an old receipt does not refresh evidence, restore
the secret, or enqueue another submission. No route or worker is registered.
"""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, cast
from uuid import UUID

from typeid import TypeID

from app.configuration.visit_prep import VISIT_PREP_SETTINGS, VisitPrepSettings
from app.services.visit_prep_command_contract import access_token_hash
from app.services.visit_prep_email_authority import (
    EmailConsentSourceVerifier,
    LockedEmailAuthority,
    resolve_locked_email_authority,
)
from app.services.visit_prep_email_contract import (
    INVITATION_TEMPLATE_VERSION,
    VisitPrepEmailInvalid,
    VisitPrepInvitationEmail,
    _patient_origin,
)
from app.services.visit_prep_email_evidence import (
    _time,
    _uuid,
    email_policy_fingerprint,
)
from app.services.visit_prep_email_payload import (
    _canonical_ref,
    _closed_object,
    bind_stored_invitation,
    decrypt_invitation_payload,
    encrypt_invitation_payload,
    invitation_authorization_digest,
    stored_email_recipient,
)
from app.services.visit_prep_email_policy import _authorize_owner
from app.services.visit_prep_packages import _database_now
from app.services.visit_preps import (
    VisitPrepError,
    _lock_visit_prep,
    _visit_prep_session,
)
from app.utils.encryption import (
    DecryptionError,
    EncryptionError,
    decrypt_value,
    encrypt_value,
    pseudonymous_digest,
)

from activemodel.types import TypeIDType
from app.models.authority import AuthorityAuditEvent
from app.models.visit_prep import VisitPrep
from app.models.visit_prep_delivery import VisitPrepEmailDelivery
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

_ERRORS = (
    VisitPrepEmailInvalid,
    VisitPrepError,
    EncryptionError,
    DecryptionError,
    SQLAlchemyError,
    ValueError,
    TypeError,
    AttributeError,
    UnicodeError,
    RecursionError,
    OverflowError,
)


@dataclass(frozen=True, repr=False)
class EmailEnqueueReceipt:
    delivery_id: str
    credential_generation: int
    state: str
    requested_at: datetime
    expires_at: datetime
    disposition: Literal["queued", "replayed"]

    def __repr__(self) -> str:
        return "EmailEnqueueReceipt(<redacted>)"


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _context(row: VisitPrepEmailDelivery, prep: VisitPrep) -> dict:
    return {
        "format_version": 1,
        "purpose": "visit_prep_email_enqueue",
        "delivery_id": str(row.id),
        "visit_prep_id": str(prep.id),
        "owner_practice_member_id": str(prep.owner_practice_member_id),
        "owner_doctor_id": str(prep.owner_doctor_id),
        "practice_id": str(prep.practice_id),
        "question_set_version_id": str(prep.question_set_version_id),
        "schedule_generation_id": str(row.schedule_generation_id),
        "credential_generation": row.credential_generation,
        "operation_id": str(row.operation_id),
        "template_version": INVITATION_TEMPLATE_VERSION,
        "expires_at": _time(row.expires_at).isoformat(),
    }


def _request_digest(
    prep: VisitPrep, *, generation: int, token_hash: str, patient_origin: str
) -> str:
    # Retry intent is independent of mutable current authority and ciphertext
    # nonces. It includes the secret's hash, not the bearer or contact address.
    return pseudonymous_digest(
        _json(
            {
                "visit_prep_id": str(prep.id),
                "owner_practice_member_id": str(prep.owner_practice_member_id),
                "question_set_version_id": str(prep.question_set_version_id),
                "credential_generation": generation,
                "credential_token_hash": token_hash,
                "patient_origin": patient_origin,
                "template_version": INVITATION_TEMPLATE_VERSION,
            }
        ),
        context="visit-prep-email-enqueue-request-v1",
    )


def _seal_authorization(
    row: VisitPrepEmailDelivery,
    locked: LockedEmailAuthority,
    *,
    subject: str,
    request_digest: str,
) -> str:
    plaintext = _json(
        {
            "context": _context(row, locked.preparation),
            "request_digest": request_digest,
            "subject": subject,
            "approval": locked.approval.manifest(),
            "consent": locked.consent.manifest(),
        }
    )
    if len(plaintext.encode("utf-8")) > 5_500:
        raise VisitPrepEmailInvalid()
    ciphertext = encrypt_value(plaintext)
    invitation_authorization_digest(ciphertext)
    return ciphertext


def _check_replay(
    row: VisitPrepEmailDelivery,
    prep: VisitPrep,
    *,
    subject: str,
    request_digest: str,
    operation_id: str,
    generation: int,
) -> None:
    if (
        row.operation_id != UUID(operation_id)
        or row.credential_generation != generation
        or type(row.envelope_format_version) is not int
        or row.envelope_format_version != 1
        or row.template_version != INVITATION_TEMPLATE_VERSION
        or row.owner_practice_member_id != prep.owner_practice_member_id
        or row.visit_prep_id != prep.id
        or row.question_set_version_id != prep.question_set_version_id
    ):
        raise VisitPrepEmailInvalid()
    invitation_authorization_digest(row.authorization_ciphertext)
    plaintext = decrypt_value(row.authorization_ciphertext)
    if len(plaintext.encode("utf-8")) > 5_500:
        raise VisitPrepEmailInvalid()
    payload = json.loads(plaintext, object_pairs_hook=_closed_object)
    if (
        not isinstance(payload, dict)
        or set(payload)
        != {"context", "request_digest", "subject", "approval", "consent"}
        or _json(payload["context"]) != _json(_context(row, prep))
        or payload["subject"] != subject
        or not isinstance(payload["request_digest"], str)
        or not hmac.compare_digest(payload["request_digest"], request_digest)
    ):
        raise VisitPrepEmailInvalid()


def enqueue_locked_email_invitation(
    session: Session,
    *,
    owner_practice_member_id: TypeIDType,
    owner_doctor_id: TypeIDType,
    practice_id: TypeIDType,
    expected_clerk_subject: str,
    visit_prep_id: TypeIDType,
    expected_credential_generation: int,
    access_token: str,
    operation_id: str,
    patient_origin: str,
    source_verifier: EmailConsentSourceVerifier | None = None,
    view_as_active: bool = False,
    settings: VisitPrepSettings = VISIT_PREP_SETTINGS,
) -> EmailEnqueueReceipt:
    """Enqueue and audit within the caller's transaction; never commit it.

    Scope/subject and HTTPS origin are trusted Core inputs, not browser-selected
    identities or URLs. The existing bearer must match the current credential.
    A creation/booking caller can use this before its own aggregate commit.
    On failure the caller must roll back. Only the outer owner command commits.
    """
    if (
        type(settings) is not VisitPrepSettings
        or settings.configuration_valid is not True
        or settings.enabled is not True
        or view_as_active is not False
        or not isinstance(expected_clerk_subject, str)
        or not 1 <= len(expected_clerk_subject) <= 255
        or not _uuid(operation_id)
        or type(expected_credential_generation) is not int
        or not 0 < expected_credential_generation <= 2_147_483_647
        or not all(
            _canonical_ref(str(value), prefix)
            for value, prefix in (
                (owner_practice_member_id, "practice_member"),
                (owner_doctor_id, "doctor"),
                (practice_id, "practice"),
                (visit_prep_id, "visit_prep"),
            )
        )
    ):
        raise VisitPrepEmailInvalid()
    patient_origin = _patient_origin(patient_origin)
    token_hash = access_token_hash(access_token)
    scope = {
        "owner_practice_member_id": owner_practice_member_id,
        "owner_doctor_id": owner_doctor_id,
        "practice_id": practice_id,
        "expected_clerk_subject": expected_clerk_subject,
        "settings": settings,
    }
    owner, practice, _ = _authorize_owner(session, **scope)
    prep = _lock_visit_prep(
        session, owner_practice_member_id=owner.id, visit_prep_id=visit_prep_id
    )
    if prep.owner_doctor_id != owner.doctor_id or prep.practice_id != practice.id:
        raise VisitPrepEmailInvalid()
    request_digest = _request_digest(
        prep,
        generation=expected_credential_generation,
        token_hash=token_hash,
        patient_origin=patient_origin,
    )
    prior = session.exec(
        select(VisitPrepEmailDelivery)
        .where(
            VisitPrepEmailDelivery.visit_prep_id == prep.id,
            VisitPrepEmailDelivery.credential_generation
            == expected_credential_generation,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if prior is not None:
        _check_replay(
            prior,
            prep,
            subject=expected_clerk_subject,
            request_digest=request_digest,
            operation_id=operation_id,
            generation=expected_credential_generation,
        )
        _authorize_owner(session, **scope)
        return EmailEnqueueReceipt(
            str(prior.id),
            prior.credential_generation,
            prior.state,
            prior.requested_at,
            prior.expires_at,
            "replayed",
        )
    authority_scope = {
        **scope,
        "visit_prep_id": prep.id,
        "source_verifier": source_verifier,
        "view_as_active": view_as_active,
    }
    locked = resolve_locked_email_authority(session, **authority_scope)
    if (
        locked.credential.generation != expected_credential_generation
        or not hmac.compare_digest(locked.credential.token_hash, token_hash)
        or locked.approval.expires_at is None
        or locked.consent.expires_at is None
    ):
        raise VisitPrepEmailInvalid()
    row = VisitPrepEmailDelivery(
        visit_prep_id=prep.id,
        owner_practice_member_id=owner.id,
        question_set_version_id=prep.question_set_version_id,
        schedule_generation_id=locked.credential.schedule_generation_id,
        credential_generation=locked.credential.generation,
        operation_id=UUID(operation_id),
        requested_at=locked.checked_at,
        expires_at=min(
            locked.checked_at + timedelta(hours=24),
            locked.credential.expires_at,
            locked.approval.expires_at,
            locked.consent.expires_at,
        ),
        authorization_ciphertext="",
    )
    # Allocate the TypeID before sealing: ORM defaults otherwise run at INSERT.
    row.id = cast(TypeIDType, TypeID(prefix="visit_prep_email"))
    row.authorization_ciphertext = _seal_authorization(
        row, locked, subject=expected_clerk_subject, request_digest=request_digest
    )
    binding = bind_stored_invitation(
        row, prep, locked.credential, checked_at=locked.checked_at
    )
    row.payload_ciphertext = encrypt_invitation_payload(
        VisitPrepInvitationEmail(
            recipient=stored_email_recipient(prep),
            patient_origin=patient_origin,
            access_token=access_token,
            operation_id=operation_id,
        ),
        binding,
    )
    # Recheck after encryption, before inserting anything. Never invoke even a
    # trusted source callback after writes: a misbehaving callback could commit
    # away the new intent before its transaction-loss denial is detected.
    current = resolve_locked_email_authority(session, **authority_scope)
    if (
        current.approval != locked.approval
        or current.consent != locked.consent
        or current.credential.generation != row.credential_generation
        or current.checked_at >= row.expires_at
    ):
        raise VisitPrepEmailInvalid()
    session.add(row)
    session.flush()
    session.refresh(row)
    _check_replay(
        row,
        prep,
        subject=expected_clerk_subject,
        request_digest=request_digest,
        operation_id=operation_id,
        generation=expected_credential_generation,
    )
    decrypt_invitation_payload(
        row.payload_ciphertext,
        bind_stored_invitation(
            row, prep, locked.credential, checked_at=_database_now(session)
        ),
        patient_origin=patient_origin,
    )
    session.add(
        AuthorityAuditEvent(
            event_type="visit_prep.email_invitation_queued",
            outcome="allowed",
            occurred_at=row.requested_at,
            client_event_id=UUID(operation_id),
            resource_type="visit_prep_email",
            resource_ref=str(row.id),
            request_digest=request_digest,
            route="visit_prep_email_enqueue",
            intent="invitation_email_handoff",
            platform_refs=[
                {"kind": "doctor", "ref": str(owner.doctor_id)},
                {"kind": "practice", "ref": str(practice.id)},
                {"kind": "practice_member", "ref": str(owner.id)},
                {"kind": "visit_prep", "ref": str(prep.id)},
            ],
        )
    )
    session.flush()
    _, _, checked_at = _authorize_owner(session, **scope)
    if checked_at >= row.expires_at or not hmac.compare_digest(
        locked.approval.policy_fingerprint, email_policy_fingerprint()
    ):
        raise VisitPrepEmailInvalid()
    return EmailEnqueueReceipt(
        str(row.id),
        row.credential_generation,
        row.state,
        row.requested_at,
        row.expires_at,
        "queued",
    )


def enqueue_email_invitation(
    *,
    owner_practice_member_id: TypeIDType,
    owner_doctor_id: TypeIDType,
    practice_id: TypeIDType,
    expected_clerk_subject: str,
    visit_prep_id: TypeIDType,
    expected_credential_generation: int,
    access_token: str,
    operation_id: str,
    patient_origin: str,
    source_verifier: EmailConsentSourceVerifier | None = None,
    view_as_active: bool = False,
    settings: VisitPrepSettings = VISIT_PREP_SETTINGS,
) -> EmailEnqueueReceipt:
    """Commit one owner-authorized handoff and audit, or its exact old receipt.

    Unknown commit outcomes must recover using the same intent and operation,
    never a new key. Source verification is required for new rows and missing
    by default. A historical receipt remains historical even after withdrawal.
    """
    result = None
    try:
        with _visit_prep_session() as session:
            try:
                result = enqueue_locked_email_invitation(
                    session,
                    owner_practice_member_id=owner_practice_member_id,
                    owner_doctor_id=owner_doctor_id,
                    practice_id=practice_id,
                    expected_clerk_subject=expected_clerk_subject,
                    visit_prep_id=visit_prep_id,
                    expected_credential_generation=expected_credential_generation,
                    access_token=access_token,
                    operation_id=operation_id,
                    patient_origin=patient_origin,
                    source_verifier=source_verifier,
                    view_as_active=view_as_active,
                    settings=settings,
                )
                session.commit()
            except _ERRORS:
                session.rollback()
                raise
    except _ERRORS:
        result = None
    if result is None:
        raise VisitPrepEmailInvalid()
    return result
