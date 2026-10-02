"""Dormant current email authorization within the caller's transaction.

Owner/practice and root locks serialize approval and consent withdrawals with
enqueue/dispatch. A returned snapshot is not transferable permission: keep
the transaction open and recheck immediately before the effect. No worker,
route or verified consent-source adapter is connected by this module.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, cast

from app.configuration.visit_prep import VISIT_PREP_SETTINGS, VisitPrepSettings
from app.services import visit_prep_access
from app.services.visit_prep_email_contract import VisitPrepEmailInvalid
from app.services.visit_prep_email_evidence import (
    EmailApprovalEvidence,
    EmailConsentEvidence,
    email_policy_fingerprint,
    read_email_approval,
    read_email_consent,
)
from app.services.visit_prep_email_payload import (
    invitation_recipient_digest,
    stored_email_recipient,
)
from app.services.visit_prep_packages import _database_now
from app.services.visit_preps import (
    VisitPrepError,
    _appointment_matches_schedule,
    _lock_current_lineage,
    _lock_owner_scope,
    _lock_visit_prep,
    _owned_appointment,
    _owned_patient,
)

from activemodel.types import TypeIDType
from app.models.doctor import Doctor
from app.models.visit_prep import VisitPrep, VisitPrepCredentialGeneration
from app.models.visit_prep_email_authority import (
    VisitPrepEmailApprovalEvent,
    VisitPrepEmailConsentEvent,
)
from sqlmodel import Session, select


class EmailConsentSourceVerifier(Protocol):
    def __call__(
        self, session: Session, evidence: EmailConsentEvidence, *, checked_at: datetime
    ) -> bool:
        """Verify authentic source, actor/caregiver authority and exact intent.

        Must lock current source authority, compare its event, recipient,
        purpose, role, actor/source digests and effective/expiry times, and deny
        withdrawn/unavailable sources. Never trust an evidence label alone.
        This is a trusted Core adapter, not a browser-selected callback. It
        must not commit, release these locks, or contact an external service.
        """
        ...


@dataclass(frozen=True, repr=False)
class LockedEmailAuthority:
    preparation: VisitPrep
    credential: VisitPrepCredentialGeneration
    approval: EmailApprovalEvidence
    consent: EmailConsentEvidence
    checked_at: datetime

    def __repr__(self) -> str:
        return "LockedEmailAuthority(<redacted>)"


def _verify_source(
    verifier: EmailConsentSourceVerifier,
    session: Session,
    evidence: EmailConsentEvidence,
    *,
    checked_at: datetime,
) -> bool:
    # Source diagnostics can include private evidence. An unavailable verifier
    # denies permission without transporting its exception or chained secrets.
    try:
        return verifier(session, evidence, checked_at=checked_at) is True
    except Exception:
        return False


def resolve_locked_email_authority(
    session: Session,
    *,
    owner_practice_member_id: TypeIDType,
    owner_doctor_id: TypeIDType,
    practice_id: TypeIDType,
    expected_clerk_subject: str,
    visit_prep_id: TypeIDType,
    source_verifier: EmailConsentSourceVerifier | None = None,
    view_as_active: bool = False,
    settings: VisitPrepSettings = VISIT_PREP_SETTINGS,
) -> LockedEmailAuthority:
    """Resolve both latest streams; no fallback to an older unexpired grant.

    No consent-source adapter is enabled by default. The caller must supply a
    qualified Core verifier and keep this same transaction open through the
    intended action. Source verification is not supplied by the physician's
    approval, a booking connection, contact data, or encrypted bytes.
    """
    result = None
    try:
        if (
            type(settings) is not VisitPrepSettings
            or settings.configuration_valid is not True
            or settings.enabled is not True
            or settings.public_api_enabled is not True
            or settings.email_send_enabled is not True
            or view_as_active is not False
            or source_verifier is None
            or not callable(source_verifier)
        ):
            raise VisitPrepEmailInvalid()
        owner, practice = _lock_owner_scope(
            session,
            owner_practice_member_id=owner_practice_member_id,
            owner_doctor_id=owner_doctor_id,
            practice_id=practice_id,
            now=_database_now(session),
        )
        doctor = session.exec(select(Doctor).where(Doctor.id == owner.doctor_id)).one()
        if (
            not isinstance(expected_clerk_subject, str)
            or not expected_clerk_subject
            or doctor.clerk_id != expected_clerk_subject
        ):
            raise VisitPrepEmailInvalid()
        prep = _lock_visit_prep(
            session, owner_practice_member_id=owner.id, visit_prep_id=visit_prep_id
        )
        if prep.owner_doctor_id != owner.doctor_id or prep.practice_id != practice.id:
            raise VisitPrepEmailInvalid()
        schedule, credential = _lock_current_lineage(session, visit_prep=prep)
        if prep.appointment_id is not None:
            appointment = _owned_appointment(
                session,
                owner_doctor_id=owner.doctor_id,
                appointment_id=prep.appointment_id,
            )
            if (
                appointment.patient_id != prep.patient_id
                or not _appointment_matches_schedule(
                    session,
                    appointment_id=appointment.id,
                    scheduled_at=schedule.scheduled_at,
                )
            ):
                raise VisitPrepEmailInvalid()
        if prep.patient_id is not None:
            _owned_patient(
                session, owner_doctor_id=owner.doctor_id, patient_id=prep.patient_id
            )
        approval_row = session.exec(
            select(VisitPrepEmailApprovalEvent)
            .where(VisitPrepEmailApprovalEvent.owner_practice_member_id == owner.id)
            .order_by(cast(Any, VisitPrepEmailApprovalEvent.event_sequence).desc())
            .limit(1)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
        consent_row = session.exec(
            select(VisitPrepEmailConsentEvent)
            .where(VisitPrepEmailConsentEvent.visit_prep_id == prep.id)
            .order_by(cast(Any, VisitPrepEmailConsentEvent.event_sequence).desc())
            .limit(1)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
        if approval_row is None or consent_row is None:
            raise VisitPrepEmailInvalid()
        approval = read_email_approval(approval_row)
        consent = read_email_consent(consent_row)
        recipient_digest = invitation_recipient_digest(stored_email_recipient(prep))
        transaction = session.get_transaction()
        nested_transaction = session.get_nested_transaction()
        if (
            transaction is None
            or transaction.is_active is not True
            or not _verify_source(
                source_verifier, session, consent, checked_at=_database_now(session)
            )
            or session.get_transaction() is not transaction
            or session.get_nested_transaction() is not nested_transaction
            or transaction.is_active is not True
        ):
            raise VisitPrepEmailInvalid()
        # Time-dependent authority is checked after all lock waits and source
        # verification. Holding row locks alone does not freeze expiry time.
        now = _database_now(session)
        _lock_owner_scope(
            session,
            owner_practice_member_id=owner.id,
            owner_doctor_id=doctor.id,
            practice_id=practice.id,
            now=now,
        )
        decision = visit_prep_access.resolve_visit_prep_access(
            doctor, settings=settings, at=now
        )
        if (
            decision.can_send_email is not True
            or decision.practice_member_id != owner.id
            or decision.practice_id != practice.id
            or decision.doctor_id != doctor.id
            or prep.revoked_at is not None
            or prep.misdirected_at is not None
            or prep.completed_at is not None
            or credential.revoked_at is not None
            or type(credential.token_format_version) is not int
            or credential.token_format_version != 1
            or credential.issued_at > now
            or credential.expires_at <= now
            or schedule.scheduled_at <= now
            or approval.event_kind != "approved"
            or approval.expires_at is None
            or approval.expires_at <= now
            or approval.practice_id != str(practice.id)
            or approval.owner_doctor_id != str(doctor.id)
            or not hmac.compare_digest(
                approval.policy_fingerprint, email_policy_fingerprint()
            )
            or consent.event_kind != "granted"
            or consent.expires_at is None
            or consent.expires_at <= now
            or consent.owner_practice_member_id != str(owner.id)
            or consent.question_set_version_id != str(prep.question_set_version_id)
            or not hmac.compare_digest(consent.recipient_digest, recipient_digest)
        ):
            raise VisitPrepEmailInvalid()
        result = LockedEmailAuthority(prep, credential, approval, consent, now)
    except (VisitPrepEmailInvalid, VisitPrepError):
        pass
    if result is None:
        raise VisitPrepEmailInvalid()
    return result
