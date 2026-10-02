"""Dormant Core SMS queue, exact receipts, and separately qualified authority.

No browser routes, booking wiring, default verifiers or real SMS transport.
Reuse Core's owner/lifecycle/timing boundaries, never email consent or approval.
"""

import hmac
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Literal, cast
from uuid import UUID

from typeid import TypeID

from app.configuration.visit_prep import VisitPrepSettings
from app.configuration.visit_prep_cadence import current_standard_cadence
from app.services.visit_prep_command_contract import access_token_hash
from app.services.visit_prep_current_timing import (
    plan_locked_current_delivery,
)
from app.services.visit_prep_delivery_links import delivery_token_deadline
from app.services.visit_prep_delivery_timing import (
    next_outside_quiet_hours,
    plan_delivery_timing,
)
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
from app.services.visit_prep_sms_consent import _latest, stored_sms_recipient
from app.services.visit_prep_sms_contract import (
    POLICY_FINGERPRINT,
    STEPS,
    SMSApprovalVerifier,
    SMSConsentVerifier,
    SMSStandingApproval,
    SMSStep,
    VisitPrepSMSUnavailable,
    VisitPrepText,
)
from app.services.visit_prep_sms_suppression import (
    configured_sms_sender_digest,
    read_locked_sms_suppression,
    sms_recipient_digest,
)
from app.services.visit_preps import (
    _appointment_matches_schedule,
    _lock_current_lineage,
    _lock_visit_prep,
    _owned_appointment,
    _owned_patient,
)
from app.utils.encryption import decrypt_value, encrypt_value, pseudonymous_digest

from activemodel.session_manager import get_engine
from activemodel.types import TypeIDType
from app.models.authority import AuthorityAuditEvent
from app.models.visit_prep import VisitPrep, VisitPrepCredentialGeneration
from app.models.visit_prep_sms_consent import VisitPrepSMSConsentEvent
from app.models.visit_prep_sms_delivery import VisitPrepSMSDelivery
from sqlalchemy import or_
from sqlalchemy import select as columns_select
from sqlmodel import Session, col, select


@dataclass(frozen=True, repr=False)
class LockedSMSAuthority:
    prep: VisitPrep
    credential: VisitPrepCredentialGeneration
    consent: VisitPrepSMSConsentEvent
    approval: SMSStandingApproval
    checked_at: datetime


@dataclass(frozen=True, repr=False)
class SMSQueueResult:
    disposition: Literal["queued", "replayed", "wait", "omit"]
    reason: str
    delivery_id: str | None = None
    next_check_at: datetime | None = None


def _scope(approval: SMSStandingApproval, settings: VisitPrepSettings) -> dict:
    if type(approval) is not SMSStandingApproval or not all(
        _canonical_ref(value, prefix)
        for value, prefix in (
            (approval.owner_practice_member_id, "practice_member"),
            (approval.owner_doctor_id, "doctor"),
            (approval.practice_id, "practice"),
        )
    ):
        raise VisitPrepSMSUnavailable()
    return {
        "owner_practice_member_id": TypeID.from_string(
            approval.owner_practice_member_id
        ),
        "owner_doctor_id": TypeID.from_string(approval.owner_doctor_id),
        "practice_id": TypeID.from_string(approval.practice_id),
        "expected_clerk_subject": approval.subject,
        "settings": settings,
    }


def resolve_locked_sms_authority(
    session: Session,
    *,
    approval: SMSStandingApproval,
    visit_prep_id: TypeIDType,
    settings: VisitPrepSettings,
    approval_verifier: SMSApprovalVerifier | None,
    consent_verifier: SMSConsentVerifier | None,
) -> LockedSMSAuthority:
    if (
        type(settings) is not VisitPrepSettings
        or settings.configuration_valid is not True
        or settings.enabled is not True
        or settings.public_api_enabled is not True
        or settings.sms_send_enabled is not True
        or type(approval) is not SMSStandingApproval
        or not callable(approval_verifier)
        or not callable(consent_verifier)
        or not _digest(approval.sender_digest)
        or not _digest(approval.source_event_digest)
        or not _digest(approval.source_evidence_hmac)
        or approval.policy_fingerprint != POLICY_FINGERPRINT
        or configured_sms_sender_digest() != approval.sender_digest
    ):
        raise VisitPrepSMSUnavailable()
    scope = _scope(approval, settings)
    owner, practice, _ = _authorize_owner(session, **scope)
    prep = _lock_visit_prep(
        session, owner_practice_member_id=owner.id, visit_prep_id=visit_prep_id
    )
    if prep.owner_doctor_id != owner.doctor_id or prep.practice_id != practice.id:
        raise VisitPrepSMSUnavailable()
    schedule, credential = _lock_current_lineage(session, visit_prep=prep)
    if prep.appointment_id is not None:
        appointment = _owned_appointment(
            session, owner_doctor_id=owner.doctor_id, appointment_id=prep.appointment_id
        )
        session.refresh(appointment)
        if (
            appointment.patient_id != prep.patient_id
            or not _appointment_matches_schedule(
                session,
                appointment_id=appointment.id,
                scheduled_at=schedule.scheduled_at,
            )
        ):
            raise VisitPrepSMSUnavailable()
    if prep.patient_id is not None:
        _owned_patient(
            session, owner_doctor_id=owner.doctor_id, patient_id=prep.patient_id
        )
    consent = _latest(session, prep)
    if consent is None or consent.event_kind != "granted" or consent.expires_at is None:
        raise VisitPrepSMSUnavailable()
    recipient = sms_recipient_digest(stored_sms_recipient(prep))
    if read_locked_sms_suppression(
        session, sender_digest=approval.sender_digest, recipient_digest=recipient
    ):
        raise VisitPrepSMSUnavailable()
    transaction, nested = session.get_transaction(), session.get_nested_transaction()
    now = _database_now(session)
    if (
        approval_verifier(session, approval, checked_at=now) is not True
        or consent_verifier(session, consent_event_id=str(consent.id), checked_at=now)
        is not True
        or session.get_transaction() is not transaction
        or session.get_nested_transaction() is not nested
        or transaction is None
        or not transaction.is_active
    ):
        raise VisitPrepSMSUnavailable()
    _, _, now = _authorize_owner(session, **scope)
    if (
        not _time(approval.effective_at) <= now < _time(approval.expires_at)
        or consent.expires_at <= now
        or credential.expires_at <= now
        or credential.revoked_at is not None
        or credential.issued_at > now
        or credential.token_format_version != 1
        or schedule.scheduled_at <= now
        or prep.revoked_at is not None
        or prep.misdirected_at is not None
        or prep.completed_at is not None
        or prep.retain_until is None
        or prep.retain_until <= now
    ):
        raise VisitPrepSMSUnavailable()
    return LockedSMSAuthority(prep, credential, consent, approval, now)


def _approval_payload(approval: SMSStandingApproval) -> dict:
    return asdict(approval) | {
        "effective_at": _time(approval.effective_at).isoformat(),
        "expires_at": _time(approval.expires_at).isoformat(),
    }


def read_sms_approval(row: VisitPrepSMSDelivery) -> SMSStandingApproval:
    payload = _read_evidence(row)["approval"]
    return SMSStandingApproval(
        **(
            payload
            | {
                "effective_at": datetime.fromisoformat(payload["effective_at"]),
                "expires_at": datetime.fromisoformat(payload["expires_at"]),
            }
        )
    )


def _context(row: VisitPrepSMSDelivery) -> dict:
    return {
        "format_version": 1,
        "purpose": "visit_prep_sms_outbox",
        "delivery_id": str(row.id),
        "visit_prep_id": str(row.visit_prep_id),
        "owner_practice_member_id": str(row.owner_practice_member_id),
        "question_set_version_id": str(row.question_set_version_id),
        "credential_id": str(row.credential_id),
        "consent_event_id": str(row.consent_event_id),
        "operation_id": str(row.operation_id),
        "step": row.step,
        "template_version": row.template_version,
        "sender_digest": row.sender_digest,
        "recipient_digest": row.recipient_digest,
        "request_hmac": row.request_hmac,
        "payload_hmac": row.payload_hmac,
        "expires_at": _time(row.expires_at).isoformat(),
    }


def _read_evidence(row: VisitPrepSMSDelivery) -> dict:
    if (
        not isinstance(row.evidence_ciphertext, str)
        or not 1 <= len(row.evidence_ciphertext.encode()) <= 8192
    ):
        raise VisitPrepSMSUnavailable()
    plaintext = decrypt_value(row.evidence_ciphertext)
    if len(plaintext.encode()) > 5500:
        raise VisitPrepSMSUnavailable()
    payload = json.loads(plaintext, object_pairs_hook=_closed_object)
    if (
        not isinstance(payload, dict)
        or set(payload) != {"context", "approval", "consent_hmac", "patient_origin"}
        or _json(payload["context"]) != _json(_context(row))
        or not hmac.compare_digest(
            row.evidence_hmac,
            pseudonymous_digest(
                _json(payload), context="visit-prep-sms-outbox-evidence-v1"
            ),
        )
    ):
        raise VisitPrepSMSUnavailable()
    return payload


def _request(
    prep_id: TypeIDType,
    credential_id: TypeIDType,
    step: str,
    origin: str,
    token_hash: str,
    sender: str,
):
    return pseudonymous_digest(
        _json(
            {
                "prep": str(prep_id),
                "credential": str(credential_id),
                "step": step,
                "origin": origin,
                "token_hash": token_hash,
                "sender": sender,
            }
        ),
        context="visit-prep-sms-outbox-request-v1",
    )


def _audit(session: Session, row: VisitPrepSMSDelivery, outcome: str):
    session.add(
        AuthorityAuditEvent(
            event_type=f"visit_prep.sms_{outcome}",
            outcome=outcome,
            occurred_at=_database_now(session),
            recorded_at=_database_now(session),
            client_event_id=row.operation_id,
            resource_type="visit_prep_sms",
            resource_ref=str(row.id),
            request_digest=row.evidence_hmac,
            route="visit_prep_sms_outbox",
            intent=row.step,
            platform_refs=[
                {"kind": "visit_prep", "ref": str(row.visit_prep_id)},
                {"kind": "practice_member", "ref": str(row.owner_practice_member_id)},
            ],
        )
    )
    session.flush()


def verify_sms_evidence(session: Session, row: VisitPrepSMSDelivery) -> None:
    _read_evidence(row)
    audits = session.exec(
        select(AuthorityAuditEvent)
        .where(
            AuthorityAuditEvent.resource_type == "visit_prep_sms",
            AuthorityAuditEvent.resource_ref == str(row.id),
            AuthorityAuditEvent.event_type == "visit_prep.sms_queued",
        )
        .limit(2)
    ).all()
    if len(audits) != 1:
        raise VisitPrepSMSUnavailable()
    audit = audits[0]
    if (
        audit.event_source != "platform_authoritative"
        or audit.outcome != "queued"
        or audit.client_event_id != row.operation_id
        or audit.request_digest != row.evidence_hmac
        or audit.route != "visit_prep_sms_outbox"
        or audit.intent != row.step
        or audit.platform_refs
        != [
            {"kind": "visit_prep", "ref": str(row.visit_prep_id)},
            {"kind": "practice_member", "ref": str(row.owner_practice_member_id)},
        ]
        or not row.requested_at
        <= audit.occurred_at
        <= audit.recorded_at
        <= _database_now(session)
        or any(
            value is not None
            for value in (
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


def current_sms_timing(
    session: Session,
    locked: LockedSMSAuthority,
    settings: VisitPrepSettings,
    step: SMSStep,
    *,
    exclude: VisitPrepSMSDelivery | None = None,
):
    policy = current_standard_cadence()
    if policy is None or policy.minimum_channel_spacing < timedelta(hours=4):
        raise VisitPrepSMSUnavailable()
    plan = plan_locked_current_delivery(
        session,
        **_scope(locked.approval, settings),
        visit_prep_id=locked.prep.id,
        quiet_hours=policy.quiet_hours,
        minimum_channel_spacing=policy.minimum_channel_spacing,
    )
    local = tuple(
        t
        for t in plan.sms_history
        if not (
            exclude is not None
            and t.step == exclude.step
            and t.credential_generation == locked.credential.generation
            and t.requested_at == exclude.requested_at
        )
    )
    decisions = plan_delivery_timing(
        plan.snapshot,
        now=_database_now(session),
        quiet_hours=policy.quiet_hours,
        minimum_channel_spacing=policy.minimum_channel_spacing,
        history=plan.email_history + local,
    )
    decision = next(d for d in decisions if d.step == step)
    # The sender/recipient lock is held. Include recent and all uncertain/pending
    # other preparations without exposing another patient's content or identity.
    now = _database_now(session)
    rows = session.execute(
        columns_select(
            col(VisitPrepSMSDelivery.state), col(VisitPrepSMSDelivery.submitted_at)
        )
        .where(
            col(VisitPrepSMSDelivery.sender_digest) == locked.approval.sender_digest,
            col(VisitPrepSMSDelivery.recipient_digest)
            == locked.consent.recipient_digest,
            col(VisitPrepSMSDelivery.visit_prep_id) != locked.prep.id,
            or_(
                col(VisitPrepSMSDelivery.state).in_(
                    ["queued", "submitting", "uncertain"]
                ),
                col(VisitPrepSMSDelivery.submitted_at) >= now - timedelta(hours=48),
            ),
        )
        .limit(129)
    ).all()
    if len(rows) > 128:
        raise VisitPrepSMSUnavailable()
    if any(r.submitted_at is not None and _time(r.submitted_at) > now for r in rows):
        raise VisitPrepSMSUnavailable()
    if decision.action == "candidate":
        if any(r.state in {"submitting", "uncertain"} for r in rows):
            return "wait", "channel_pending", None, policy.fingerprint
        submitted = [
            r.submitted_at + policy.minimum_channel_spacing
            for r in rows
            if r.submitted_at is not None
        ]
        permitted = next_outside_quiet_hours(
            max([now, *submitted]),
            as_of=now,
            timezone_name=plan.snapshot.timezone_name,
            quiet_hours=policy.quiet_hours,
        )
        if permitted > now:
            if permitted >= min(
                plan.snapshot.scheduled_at,
                _time(locked.consent.expires_at),
                locked.approval.expires_at,
                locked.credential.expires_at,
            ):
                return "omit", "no_permitted_window", None, policy.fingerprint
            return "wait", "not_due", permitted, policy.fingerprint
    return decision.action, decision.reason, decision.next_check_at, policy.fingerprint


def enqueue_locked_sms(
    session: Session,
    *,
    visit_prep_id: TypeIDType,
    credential_id: TypeIDType,
    step: SMSStep,
    access_token: str,
    operation_id: str,
    patient_origin: str,
    approval: SMSStandingApproval,
    settings: VisitPrepSettings,
    approval_verifier: SMSApprovalVerifier | None = None,
    consent_verifier: SMSConsentVerifier | None = None,
) -> SMSQueueResult:
    if (
        step not in STEPS
        or not _uuid(operation_id)
        or type(approval) is not SMSStandingApproval
    ):
        raise VisitPrepSMSUnavailable()
    token_hash = access_token_hash(access_token)
    owner, _, _ = _authorize_owner(session, **_scope(approval, settings))
    prep = _lock_visit_prep(
        session, owner_practice_member_id=owner.id, visit_prep_id=visit_prep_id
    )
    request = _request(
        prep.id, credential_id, step, patient_origin, token_hash, approval.sender_digest
    )
    prior = session.exec(
        select(VisitPrepSMSDelivery)
        .where(
            VisitPrepSMSDelivery.visit_prep_id == prep.id,
            VisitPrepSMSDelivery.credential_id == credential_id,
            VisitPrepSMSDelivery.step == step,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if prior is not None:
        verify_sms_evidence(session, prior)
        if prior.request_hmac != request or str(prior.operation_id) != operation_id:
            raise VisitPrepSMSUnavailable()
        return SMSQueueResult("replayed", "already_planned", str(prior.id))
    locked = resolve_locked_sms_authority(
        session,
        approval=approval,
        visit_prep_id=prep.id,
        settings=settings,
        approval_verifier=approval_verifier,
        consent_verifier=consent_verifier,
    )
    if locked.credential.id != credential_id:
        raise VisitPrepSMSUnavailable()
    token_deadline = delivery_token_deadline(
        session, prep=prep, credential=locked.credential, token_hash=token_hash
    )
    message = VisitPrepText(
        stored_sms_recipient(prep),
        approval.sender_digest,
        patient_origin,
        access_token,
        operation_id,
        step,
    )
    action, reason, next_check, fingerprint = current_sms_timing(
        session, locked, settings, step
    )
    if action != "candidate":
        return SMSQueueResult(action, reason, next_check_at=next_check)
    expires = min(
        token_deadline,
        locked.credential.expires_at,
        _time(locked.consent.expires_at),
        approval.expires_at,
        locked.checked_at + timedelta(hours=24),
        _lock_current_lineage(session, visit_prep=prep)[0].scheduled_at,
    )
    row = VisitPrepSMSDelivery(
        visit_prep_id=prep.id,
        owner_practice_member_id=prep.owner_practice_member_id,
        question_set_version_id=prep.question_set_version_id,
        credential_id=credential_id,
        consent_event_id=locked.consent.id,
        operation_id=UUID(operation_id),
        step=step,
        sender_digest=approval.sender_digest,
        recipient_digest=locked.consent.recipient_digest,
        request_hmac=request,
        payload_hmac="0" * 64,
        evidence_hmac="0" * 64,
        evidence_ciphertext="",
        payload_ciphertext="",
        requested_at=_database_now(session),
        expires_at=expires,
    )
    row.id = cast(TypeIDType, TypeID(prefix="visit_prep_sms"))
    payload = _json(
        {
            "format_version": 1,
            "context": _context(row),
            "recipient": message.recipient,
            "patient_origin": message.patient_origin,
            "access_token": message.access_token,
        }
    )
    # Context excludes its own payload hash for sealing; verification rebuilds
    # the exact zero-hash context before checking this purpose-bound plaintext.
    row.payload_hmac = pseudonymous_digest(
        payload, context="visit-prep-sms-outbox-payload-v1"
    )
    row.payload_ciphertext = encrypt_value(payload)
    evidence = _json(
        {
            "context": _context(row),
            "approval": _approval_payload(approval),
            "consent_hmac": locked.consent.payload_hmac_sha256,
            "patient_origin": message.patient_origin,
        }
    )
    row.evidence_hmac = pseudonymous_digest(
        evidence, context="visit-prep-sms-outbox-evidence-v1"
    )
    row.evidence_ciphertext = encrypt_value(evidence)
    session.add(row)
    session.flush()
    session.refresh(row)
    _audit(session, row, "queued")
    verify_sms_evidence(session, row)
    final = resolve_locked_sms_authority(
        session,
        approval=approval,
        visit_prep_id=prep.id,
        settings=settings,
        approval_verifier=approval_verifier,
        consent_verifier=consent_verifier,
    )
    if final.consent.id != row.consent_event_id or final.checked_at >= row.expires_at:
        raise VisitPrepSMSUnavailable()
    action, _, _, latest_fingerprint = current_sms_timing(
        session, final, settings, step, exclude=row
    )
    if action != "candidate" or fingerprint != latest_fingerprint:
        raise VisitPrepSMSUnavailable()
    return SMSQueueResult("queued", "due", str(row.id))


def enqueue_sms(**arguments) -> SMSQueueResult:
    try:
        with Session(get_engine()) as session:
            result = enqueue_locked_sms(session, **arguments)
            session.commit()
            return result
    except Exception:
        raise VisitPrepSMSUnavailable() from None


def decrypt_sms_message(
    session: Session, row: VisitPrepSMSDelivery, locked: LockedSMSAuthority
) -> VisitPrepText:
    evidence = _read_evidence(row)
    if (
        row.payload_ciphertext is None
        or len(row.payload_ciphertext.encode()) > 4096
        or row.credential_id != locked.credential.id
        or row.consent_event_id != locked.consent.id
        or evidence["approval"] != _approval_payload(locked.approval)
        or evidence["consent_hmac"] != locked.consent.payload_hmac_sha256
    ):
        raise VisitPrepSMSUnavailable()
    plaintext = decrypt_value(row.payload_ciphertext)
    if len(plaintext.encode()) > 2500:
        raise VisitPrepSMSUnavailable()
    payload = json.loads(plaintext, object_pairs_hook=_closed_object)
    if (
        not isinstance(payload, dict)
        or set(payload)
        != {"format_version", "context", "recipient", "patient_origin", "access_token"}
        or type(payload["format_version"]) is not int
        or payload["format_version"] != 1
        or payload["context"] != (_context(row) | {"payload_hmac": "0" * 64})
        or not hmac.compare_digest(
            row.payload_hmac,
            pseudonymous_digest(
                _json(payload), context="visit-prep-sms-outbox-payload-v1"
            ),
        )
        or payload["recipient"] != stored_sms_recipient(locked.prep)
        or sms_recipient_digest(payload["recipient"]) != row.recipient_digest
        or row.sender_digest != locked.approval.sender_digest
        or payload["patient_origin"] != evidence["patient_origin"]
        or row.request_hmac
        != _request(
            row.visit_prep_id,
            row.credential_id,
            row.step,
            payload["patient_origin"],
            access_token_hash(payload["access_token"]),
            row.sender_digest,
        )
    ):
        raise VisitPrepSMSUnavailable()
    deadline = delivery_token_deadline(
        session,
        prep=locked.prep,
        credential=locked.credential,
        token_hash=access_token_hash(payload["access_token"]),
    )
    if row.expires_at > deadline:
        raise VisitPrepSMSUnavailable()
    return VisitPrepText(
        payload["recipient"],
        row.sender_digest,
        payload["patient_origin"],
        payload["access_token"],
        str(row.operation_id),
        cast(SMSStep, row.step),
    )
