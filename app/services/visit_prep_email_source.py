"""Verify an actual Core email capture, never a contact or a booking label."""

from datetime import datetime

from app.configuration import visit_prep_email_source as configuration
from app.services.visit_prep_email_consent import read_patient_email_consent_source
from app.services.visit_prep_email_evidence import EmailConsentEvidence

from sqlmodel import Session


def verify_patient_email_source(
    session: Session, evidence: EmailConsentEvidence, *, checked_at: datetime
) -> bool:
    # The authority resolver holds the owner/root/latest-consent locks and
    # independently checks withdrawal, expiry, recipient and current access.
    if configuration.PATIENT_EMAIL_SOURCE_QUALIFIED is not True:
        return False
    source = read_patient_email_consent_source(session, evidence)
    return (
        evidence.event_kind == "granted"
        and evidence.effective_at <= checked_at
        and evidence.expires_at is not None
        and checked_at < evidence.expires_at
        and source.recipient_control_attested is True
        and source.caregiver_authority_attested is (evidence.actor_role == "caregiver")
        and source.identity_assurance == "invitation_bearer"
        and configuration.PATIENT_EMAIL_SOURCE_QUALIFIED is True
    )
