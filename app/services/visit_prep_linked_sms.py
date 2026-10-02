"""Atomically issue a bounded link and enqueue one currently permitted SMS.

No browser/source selector, default consent verifier, transport or installed
booking job. A booking contact alone cannot enter this trusted composition.
"""

from app.services import visit_prep_sms_outbox as sms
from app.services.visit_prep_delivery_links import (
    DeliveryLinkIntent,
    issue_locked_delivery_link,
    verify_delivery_link,
)
from app.services.visit_prep_email_evidence import _uuid
from app.services.visit_prep_sms_contract import STEPS, VisitPrepSMSUnavailable

from activemodel.session_manager import get_engine
from app.models.visit_prep_delivery_link import VisitPrepDeliveryLink
from app.models.visit_prep_sms_delivery import VisitPrepSMSDelivery
from sqlmodel import Session, select


def enqueue_linked_sms(
    *,
    intent: DeliveryLinkIntent,
    step,
    patient_origin,
    approval,
    settings,
    approval_verifier=None,
    consent_verifier=None,
) -> sms.SMSQueueResult:
    """Return only a committed queue receipt, never a bearer or recipient."""
    try:
        if (
            type(intent) is not DeliveryLinkIntent
            or not _uuid(intent.operation_id)
            or step not in STEPS
        ):
            raise VisitPrepSMSUnavailable()
        with Session(get_engine()) as session:

            def authority():
                locked = sms.resolve_locked_sms_authority(
                    session,
                    approval=approval,
                    visit_prep_id=intent.visit_prep_id,
                    settings=settings,
                    approval_verifier=approval_verifier,
                    consent_verifier=consent_verifier,
                )
                if locked.credential.id != intent.credential_id:
                    raise VisitPrepSMSUnavailable()
                return locked

            locked = authority()
            prior = session.exec(
                select(VisitPrepSMSDelivery)
                .where(
                    VisitPrepSMSDelivery.visit_prep_id == intent.visit_prep_id,
                    VisitPrepSMSDelivery.credential_id == intent.credential_id,
                    VisitPrepSMSDelivery.step == step,
                )
                .with_for_update()
            ).one_or_none()
            if prior is not None:
                sms.verify_sms_evidence(session, prior)
                alias = session.exec(
                    select(VisitPrepDeliveryLink).where(
                        VisitPrepDeliveryLink.visit_prep_id == intent.visit_prep_id,
                        VisitPrepDeliveryLink.operation_id == prior.operation_id,
                    )
                ).one_or_none()
                if alias is None:
                    raise VisitPrepSMSUnavailable()
                verify_delivery_link(session, alias)
                if (
                    str(prior.operation_id) != intent.operation_id
                    or alias.credential_id != intent.credential_id
                    or alias.expires_at != intent.expires_at
                    or prior.request_hmac
                    != sms._request(
                        intent.visit_prep_id,
                        intent.credential_id,
                        step,
                        patient_origin,
                        alias.token_hash,
                        approval.sender_digest,
                    )
                ):
                    raise VisitPrepSMSUnavailable()
                result = sms.SMSQueueResult(
                    "replayed", "already_planned", str(prior.id)
                )
            else:
                action, reason, next_check, _ = sms.current_sms_timing(
                    session, locked, settings, step
                )
                if action != "candidate":
                    return sms.SMSQueueResult(action, reason, next_check_at=next_check)

                transaction_session, expected_intent = session, intent

                def qualified_source(session, intent, *, checked_at):
                    current = authority()
                    return (
                        session is transaction_session
                        and intent == expected_intent
                        and checked_at <= current.checked_at
                        and current.consent.expires_at is not None
                        and intent.expires_at
                        <= min(current.consent.expires_at, approval.expires_at)
                    )

                link = issue_locked_delivery_link(
                    session,
                    intent=intent,
                    settings=settings,
                    source_verifier=qualified_source,
                    owner_practice_member_id=locked.prep.owner_practice_member_id,
                    owner_doctor_id=locked.prep.owner_doctor_id,
                    practice_id=locked.prep.practice_id,
                    expected_clerk_subject=approval.subject,
                )
                if link.disposition != "issued" or link.access_token is None:
                    raise VisitPrepSMSUnavailable()
                result = sms.enqueue_locked_sms(
                    session,
                    visit_prep_id=intent.visit_prep_id,
                    credential_id=intent.credential_id,
                    step=step,
                    access_token=link.access_token,
                    operation_id=intent.operation_id,
                    patient_origin=patient_origin,
                    approval=approval,
                    settings=settings,
                    approval_verifier=approval_verifier,
                    consent_verifier=consent_verifier,
                )
                if result.disposition != "queued":
                    # A late state/timing change cannot commit an orphaned alias.
                    raise VisitPrepSMSUnavailable()
            authority()
            session.commit()
            return result
    except Exception:
        raise VisitPrepSMSUnavailable() from None
