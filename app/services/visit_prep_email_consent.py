"""Dormant explicit email choices from a confirmed invitation holder.

Confirmation/contact data never create consent. This command separately
captures a displayed disclosure, deadline and explicit recipient/caregiver
attestations. Core records an independent audit of the capture interaction.
Bearer attribution and self-attestation are not verified legal identity or
caregiver authority. No route, qualified source verifier or sender is mounted.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from typeid import TypeID

from app.configuration.visit_prep import VisitPrepSettings
from app.services import visit_prep_access
from app.services.visit_prep_email_contract import VisitPrepEmailInvalid
from app.services.visit_prep_email_evidence import (
    EmailConsentEvidence,
    _digest,
    _json,
    _time,
    _uuid,
    read_email_consent,
    seal_email_evidence,
)
from app.services.visit_prep_email_payload import (
    _canonical_ref,
    invitation_recipient_digest,
    stored_email_recipient,
)
from app.services.visit_prep_interviews import (
    VisitPrepInterviewError,
    _AuthorizedInterview,
    _lock_authorized_interview,
)
from app.services.visit_prep_packages import _database_now
from app.services.visit_preps import (
    VisitPrepError,
    _lock_owner_scope,
    _visit_prep_session,
)
from app.utils.encryption import EncryptionError, pseudonymous_digest

from activemodel.types import TypeIDType
from app.models.authority import AuthorityAuditEvent
from app.models.visit_prep import VisitPrep, VisitPrepCredentialGeneration
from app.models.visit_prep_email_authority import VisitPrepEmailConsentEvent
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

CONSENT_DISCLOSURE_VERSION = "visit_prep_email_consent_v1"
_ERRORS = (
    VisitPrepEmailInvalid,
    VisitPrepInterviewError,
    VisitPrepError,
    EncryptionError,
    SQLAlchemyError,
    ValueError,
    TypeError,
    AttributeError,
    UnicodeError,
    OverflowError,
)


def email_consent_disclosure(
    *,
    role: Literal["patient", "caregiver"],
    event_kind: Literal["granted", "withdrawn"],
) -> str:
    """Fixed draft wording; counsel/customer qualification precedes activation."""
    if role not in {"patient", "caregiver"} or event_kind not in {
        "granted",
        "withdrawn",
    }:
        raise VisitPrepEmailInvalid()
    if event_kind == "withdrawn":
        return (
            "Withdraw permission for further Visit Prep invitation emails for this "
            "preparation. An email already submitted cannot be recalled. This "
            "choice does not change your answers or prevent preparing for your visit."
        )
    text = (
        "Allow your care team to email a private Visit Prep link to the email address "
        "already provided for this preparation, only until the expiry shown. "
        "This choice is optional and does not prevent answering the preparation. "
        "It permits invitation emails only, not SMS, marketing, treatment, or "
        "other disclosures. You can withdraw this permission before another "
        "invitation is submitted; an email already submitted cannot be recalled. "
        "Do not reply to invitation emails with health information. Visit Prep "
        "is not monitored in real time and is not for emergencies. "
        "I confirm I am the intended recipient and am authorized to receive these "
        "invitations at the email address provided."
    )
    if role == "caregiver":
        text += (
            " I am acting for the patient and attest that I am authorized to make "
            "this communication choice on their behalf. This is my attestation, "
            "not independent verification of my identity or legal authority."
        )
    return text


def email_consent_disclosure_fingerprint(
    *,
    role: Literal["patient", "caregiver"],
    event_kind: Literal["granted", "withdrawn"],
) -> str:
    return hashlib.sha256(
        _json(
            {
                "version": CONSENT_DISCLOSURE_VERSION,
                "role": role,
                "event_kind": event_kind,
                "purpose": "visit_prep_email_invitation",
                "text": email_consent_disclosure(role=role, event_kind=event_kind),
            }
        ).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, repr=False)
class PatientEmailConsentInput:
    operation_id: str
    expected_event_sequence: int
    event_kind: Literal["granted", "withdrawn"]
    role: Literal["patient", "caregiver"]
    disclosure_fingerprint: str
    expires_at: datetime | None
    recipient_control_attested: bool
    caregiver_authority_attested: bool

    def __repr__(self) -> str:
        return "PatientEmailConsentInput(<redacted>)"


@dataclass(frozen=True, repr=False)
class PatientEmailConsentReceipt:
    event_sequence: int
    current_event_sequence: int
    event_kind: Literal["granted", "withdrawn"]
    expires_at: datetime | None
    recorded_at: datetime
    disposition: Literal["recorded", "replayed"]

    def __repr__(self) -> str:
        return "PatientEmailConsentReceipt(<redacted>)"


@dataclass(frozen=True, repr=False)
class PatientEmailConsentSource:
    """Authenticated capture record, not a verified identity or send capability."""

    evidence: EmailConsentEvidence
    disclosure_fingerprint: str
    recipient_control_attested: bool
    caregiver_authority_attested: bool
    identity_assurance: Literal["invitation_bearer"] = "invitation_bearer"

    def __repr__(self) -> str:
        return "PatientEmailConsentSource(<redacted>)"


def _attestations(role: str, event_kind: str) -> tuple[bool, bool]:
    return event_kind == "granted", role == "caregiver" and event_kind == "granted"


def _actor_digest(
    prep: VisitPrep, credential: VisitPrepCredentialGeneration, role: str
) -> str:
    return pseudonymous_digest(
        _json(
            {
                "preparation": str(prep.id),
                "credential": str(credential.id),
                "verifier": credential.token_hash,
                "confirmed_at": _time(prep.confirmed_at).isoformat(),
                "role": role,
            }
        ),
        context="visit-prep-email-consent-actor-v1",
    )


def _source_digest(
    evidence: EmailConsentEvidence,
    *,
    credential: VisitPrepCredentialGeneration,
    disclosure_fingerprint: str,
) -> str:
    payload = evidence.manifest()
    payload.pop("source_payload_digest")
    recipient, caregiver = _attestations(evidence.actor_role, evidence.event_kind)
    return pseudonymous_digest(
        _json(
            payload
            | {
                "capture_method": "confirmed_invitation_bearer",
                "credential_id": str(credential.id),
                "credential_token_hash": credential.token_hash,
                "disclosure_fingerprint": disclosure_fingerprint,
                "recipient_control_attested": recipient,
                "caregiver_authority_attested": caregiver,
            }
        ),
        context="visit-prep-email-consent-source-v1",
    )


def _refs(
    prep: VisitPrep, credential: VisitPrepCredentialGeneration
) -> list[dict[str, str]]:
    return [
        {"kind": "visit_prep", "ref": str(prep.id)},
        {"kind": "practice_member", "ref": str(prep.owner_practice_member_id)},
        {"kind": "doctor", "ref": str(prep.owner_doctor_id)},
        {"kind": "practice", "ref": str(prep.practice_id)},
        {
            "kind": "visit_prep_question_set_version",
            "ref": str(prep.question_set_version_id),
        },
        {"kind": "visit_prep_credential", "ref": str(credential.id)},
    ]


def read_patient_email_consent_source(
    session: Session, evidence: EmailConsentEvidence
) -> PatientEmailConsentSource:
    """Verify exact source audit and bound capture; never resolve send authority.

    A qualified verifier must additionally decide whether bearer attribution
    and these attestations satisfy the applicable identity/caregiver policy.
    This historical source reader deliberately does not install that decision
    in the current-email resolver or claim the credential is currently usable.
    """
    result = None
    try:
        if type(evidence) is not EmailConsentEvidence:
            raise VisitPrepEmailInvalid()
        evidence.manifest()
        if evidence.actor_role not in {"patient", "caregiver"}:
            raise VisitPrepEmailInvalid()
        row = session.get(
            VisitPrepEmailConsentEvent, TypeID.from_string(evidence.consent_event_id)
        )
        if row is None or read_email_consent(row) != evidence:
            raise VisitPrepEmailInvalid()
        audits = session.exec(
            select(AuthorityAuditEvent)
            .where(
                AuthorityAuditEvent.client_event_id == UUID(evidence.source_event_id),
                AuthorityAuditEvent.resource_type == "visit_prep_email_consent",
                AuthorityAuditEvent.resource_ref == evidence.consent_event_id,
            )
            .limit(2)
        ).all()
        if len(audits) != 1:
            raise VisitPrepEmailInvalid()
        audit = audits[0]
        prep = session.get(VisitPrep, TypeID.from_string(evidence.visit_prep_id))
        if (
            prep is None
            or prep.confirmed_by_role != evidence.actor_role
            or prep.owner_practice_member_id != row.owner_practice_member_id
            or prep.question_set_version_id != row.question_set_version_id
            or _time(prep.confirmed_at) > evidence.effective_at
            or not isinstance(audit.platform_refs, list)
            or len(audit.platform_refs) != 6
        ):
            raise VisitPrepEmailInvalid()
        credential_ref = audit.platform_refs[-1]
        if (
            not isinstance(credential_ref, dict)
            or set(credential_ref) != {"kind", "ref"}
            or credential_ref["kind"] != "visit_prep_credential"
            or not _canonical_ref(credential_ref["ref"], "visit_prep_credential")
        ):
            raise VisitPrepEmailInvalid()
        credential = session.get(
            VisitPrepCredentialGeneration, TypeID.from_string(credential_ref["ref"])
        )
        role = cast(Literal["patient", "caregiver"], evidence.actor_role)
        kind = cast(Literal["granted", "withdrawn"], evidence.event_kind)
        fingerprint = email_consent_disclosure_fingerprint(role=role, event_kind=kind)
        if (
            credential is None
            or credential.visit_prep_id != prep.id
            or credential.owner_practice_member_id != prep.owner_practice_member_id
            or type(credential.token_format_version) is not int
            or credential.token_format_version != 1
            or not _digest(credential.token_hash)
            or credential.issued_at > evidence.effective_at
            or credential.expires_at <= evidence.effective_at
            or (
                credential.revoked_at is not None
                and credential.revoked_at <= evidence.effective_at
            )
            or audit.platform_refs != _refs(prep, credential)
            or audit.event_source != "platform_authoritative"
            or audit.event_type != f"visit_prep.email_consent_{kind}"
            or audit.outcome != kind
            or audit.route != "visit_prep_patient_email_consent"
            or audit.intent != "confirmed_bearer_email_choice"
            or audit.workload_client_id is not None
            or audit.actor_id is not None
            or audit.tenant_id is not None
            or audit.person_id is not None
            or audit.interaction_id is not None
            or _time(audit.occurred_at) != _time(row.recorded_at)
            or _time(audit.recorded_at) < _time(audit.occurred_at)
            or audit.content_sha256 != fingerprint
            or audit.request_digest != evidence.source_payload_digest
            or not hmac.compare_digest(
                _actor_digest(prep, credential, role), evidence.actor_binding_digest
            )
            or not hmac.compare_digest(
                invitation_recipient_digest(stored_email_recipient(prep)),
                evidence.recipient_digest,
            )
            or not hmac.compare_digest(
                _source_digest(
                    evidence, credential=credential, disclosure_fingerprint=fingerprint
                ),
                evidence.source_payload_digest,
            )
        ):
            raise VisitPrepEmailInvalid()
        recipient, caregiver = _attestations(role, kind)
        result = PatientEmailConsentSource(evidence, fingerprint, recipient, caregiver)
    except _ERRORS:
        pass
    if result is None:
        raise VisitPrepEmailInvalid()
    return result


def _capture_context(
    session: Session, *, access_token: str, role: str
) -> _AuthorizedInterview:
    settings = visit_prep_access.VISIT_PREP_SETTINGS
    if (
        type(settings) is not VisitPrepSettings
        or settings.configuration_valid is not True
        or settings.enabled is not True
        or settings.public_api_enabled is not True
    ):
        raise VisitPrepEmailInvalid()
    authorized = _lock_authorized_interview(session, access_token=access_token)
    prep, credential = authorized.visit_prep, authorized.credential
    now = _database_now(session)
    _lock_owner_scope(
        session,
        owner_practice_member_id=prep.owner_practice_member_id,
        owner_doctor_id=prep.owner_doctor_id,
        practice_id=prep.practice_id,
        now=now,
    )
    if (
        prep.confirmed_by_role != role
        or prep.confirmed_at is None
        or prep.confirmed_at > now
        or prep.misdirected_at is not None
        or credential.expires_at <= now
    ):
        raise VisitPrepEmailInvalid()
    return replace(authorized, event_at=now)


def _record(
    session: Session, *, access_token: str, decision: PatientEmailConsentInput
) -> PatientEmailConsentReceipt:
    authorized = _capture_context(
        session, access_token=access_token, role=decision.role
    )
    prep, credential = authorized.visit_prep, authorized.credential
    recipient_digest = invitation_recipient_digest(stored_email_recipient(prep))
    latest_row = session.exec(
        select(VisitPrepEmailConsentEvent)
        .where(VisitPrepEmailConsentEvent.visit_prep_id == prep.id)
        .order_by(cast(Any, VisitPrepEmailConsentEvent.event_sequence).desc())
        .limit(1)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    latest = read_email_consent(latest_row) if latest_row is not None else None
    sequence = latest.event_sequence if latest is not None else 0
    prior = session.exec(
        select(VisitPrepEmailConsentEvent)
        .where(
            VisitPrepEmailConsentEvent.visit_prep_id == prep.id,
            VisitPrepEmailConsentEvent.operation_id == UUID(decision.operation_id),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    disposition: Literal["recorded", "replayed"] = "replayed"
    if prior is not None:
        evidence = read_email_consent(prior)
        source = read_patient_email_consent_source(session, evidence)
        if (
            evidence.event_sequence != decision.expected_event_sequence + 1
            or evidence.event_kind != decision.event_kind
            or evidence.actor_role != decision.role
            or evidence.expires_at != decision.expires_at
            or evidence.recipient_digest != recipient_digest
            or source.disclosure_fingerprint != decision.disclosure_fingerprint
            or source.recipient_control_attested != decision.recipient_control_attested
            or source.caregiver_authority_attested
            != decision.caregiver_authority_attested
        ):
            raise VisitPrepEmailInvalid()
        row = prior
    else:
        if decision.expected_event_sequence != sequence:
            raise VisitPrepEmailInvalid()
        authorized = _capture_context(
            session, access_token=access_token, role=decision.role
        )
        now = authorized.event_at
        if decision.event_kind == "granted" and (
            prep.completed_at is not None
            or decision.expires_at is None
            or not now
            < decision.expires_at
            <= min(credential.expires_at, authorized.schedule.scheduled_at)
        ):
            raise VisitPrepEmailInvalid()
        evidence = EmailConsentEvidence(
            consent_event_id=str(TypeID(prefix="visit_prep_email_consent")),
            visit_prep_id=str(prep.id),
            owner_practice_member_id=str(prep.owner_practice_member_id),
            question_set_version_id=str(prep.question_set_version_id),
            event_sequence=sequence + 1,
            event_kind=decision.event_kind,
            actor_role=decision.role,
            operation_id=decision.operation_id,
            source_event_id=str(uuid4()),
            source_kind=f"{decision.role}_confirmation",
            actor_binding_digest=_actor_digest(prep, credential, decision.role),
            source_payload_digest="0" * 64,
            recipient_digest=recipient_digest,
            effective_at=now,
            expires_at=decision.expires_at,
        )
        evidence = replace(
            evidence,
            source_payload_digest=_source_digest(
                evidence,
                credential=credential,
                disclosure_fingerprint=decision.disclosure_fingerprint,
            ),
        )
        sealed = seal_email_evidence(evidence)
        row = VisitPrepEmailConsentEvent(
            visit_prep_id=prep.id,
            owner_practice_member_id=prep.owner_practice_member_id,
            question_set_version_id=prep.question_set_version_id,
            event_sequence=evidence.event_sequence,
            event_kind=decision.event_kind,
            actor_role=decision.role,
            recipient_digest=recipient_digest,
            operation_id=UUID(decision.operation_id),
            source_event_id=UUID(evidence.source_event_id),
            payload_hmac_sha256=sealed.payload_hmac_sha256,
            evidence_ciphertext=sealed.ciphertext,
            recorded_at=now,
            effective_at=now,
            expires_at=decision.expires_at,
        )
        row.id = cast(TypeIDType, TypeID.from_string(evidence.consent_event_id))
        session.add(row)
        session.flush()
        session.refresh(row)
        session.add(
            AuthorityAuditEvent(
                event_type=f"visit_prep.email_consent_{decision.event_kind}",
                outcome=decision.event_kind,
                occurred_at=row.recorded_at,
                recorded_at=_database_now(session),
                client_event_id=UUID(evidence.source_event_id),
                resource_type="visit_prep_email_consent",
                resource_ref=str(row.id),
                request_digest=evidence.source_payload_digest,
                content_sha256=decision.disclosure_fingerprint,
                route="visit_prep_patient_email_consent",
                intent="confirmed_bearer_email_choice",
                platform_refs=_refs(prep, credential),
            )
        )
        session.flush()
        read_patient_email_consent_source(session, evidence)
        sequence = row.event_sequence
        disposition = "recorded"
    checked = _capture_context(session, access_token=access_token, role=decision.role)
    if not hmac.compare_digest(
        decision.disclosure_fingerprint,
        email_consent_disclosure_fingerprint(
            role=decision.role, event_kind=decision.event_kind
        ),
    ):
        raise VisitPrepEmailInvalid()
    if (
        disposition == "recorded"
        and decision.event_kind == "granted"
        and (row.expires_at is None or row.expires_at <= checked.event_at)
    ):
        raise VisitPrepEmailInvalid()
    receipt = PatientEmailConsentReceipt(
        row.event_sequence,
        sequence,
        decision.event_kind,
        row.expires_at,
        row.recorded_at,
        disposition,
    )
    session.commit()
    return receipt


def record_patient_email_consent(
    *,
    access_token: str,
    decision: PatientEmailConsentInput,
    view_as_active: bool = False,
) -> PatientEmailConsentReceipt:
    """Capture an explicit choice; never infer it from opening or confirming.

    Only current confirmed bearer access is used. Withdrawals remain possible
    after completion while that bearer remains valid; revoked/rotated links
    are never revived. A separately governed recovery channel is still needed
    after access expires. Capture remains dormant until retention and disclosure
    qualification; no default source verifier is installed by this command.
    """
    result = None
    try:
        if (
            type(decision) is not PatientEmailConsentInput
            or view_as_active is not False
            or not _uuid(decision.operation_id)
            or type(decision.expected_event_sequence) is not int
            or not 0 <= decision.expected_event_sequence < 2_147_483_647
            or decision.event_kind not in {"granted", "withdrawn"}
            or decision.role not in {"patient", "caregiver"}
            or not _digest(decision.disclosure_fingerprint)
            or type(decision.recipient_control_attested) is not bool
            or type(decision.caregiver_authority_attested) is not bool
            or (
                decision.recipient_control_attested,
                decision.caregiver_authority_attested,
            )
            != _attestations(decision.role, decision.event_kind)
            or not hmac.compare_digest(
                decision.disclosure_fingerprint,
                email_consent_disclosure_fingerprint(
                    role=decision.role, event_kind=decision.event_kind
                ),
            )
        ):
            raise VisitPrepEmailInvalid()
        if decision.event_kind == "granted":
            decision = replace(decision, expires_at=_time(decision.expires_at))
        elif decision.expires_at is not None:
            raise VisitPrepEmailInvalid()
        with _visit_prep_session() as session:
            try:
                result = _record(session, access_token=access_token, decision=decision)
            except _ERRORS:
                session.rollback()
                raise
    except _ERRORS:
        pass
    if result is None:
        raise VisitPrepEmailInvalid()
    return result
