"""Dormant one-attempt SMS worker. No provider, scheduler or live sender installed.

Persist the irreversible claim before invocation; keep the Core recipient fence
through the effect. Unknown submissions are never returned to the send lane.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import Lock
from time import monotonic
from typing import Any, Literal, cast

from app.configuration.visit_prep import VisitPrepSettings
from app.services.visit_prep_packages import _database_now
from app.services.visit_prep_sms_contract import (
    SMSApprovalVerifier,
    SMSConsentVerifier,
    SMSStep,
    SMSTransport,
    VisitPrepSMSUnavailable,
)
from app.services.visit_prep_sms_outbox import (
    _audit,
    current_sms_timing,
    decrypt_sms_message,
    read_sms_approval,
    resolve_locked_sms_authority,
    verify_sms_evidence,
)
from app.services.visit_prep_sms_suppression import read_locked_sms_suppression
from app.services.visit_preps import _lock_current_lineage

from activemodel.session_manager import get_engine
from activemodel.types import TypeIDType
from app.models.practice import Practice
from app.models.visit_prep import VisitPrep, VisitPrepCredentialGeneration
from app.models.visit_prep_sms_approval import VisitPrepSMSApprovalEvent
from app.models.visit_prep_sms_consent import VisitPrepSMSConsentEvent
from app.models.visit_prep_sms_delivery import VisitPrepSMSDelivery
from app.models.visit_prep_sms_stop import VisitPrepSMSStopEvent
from sqlalchemy import and_, or_
from sqlmodel import Session, col, select

Outcome = Literal[
    "idle",
    "wait",
    "disabled",
    "accepted",
    "suppressed",
    "uncertain",
    "discarded",
    "storage_unavailable",
]


@dataclass(repr=False)
class _Custody:
    lock: Any = field(default_factory=Lock)
    used: bool = False


@dataclass(frozen=True, repr=False)
class SMSSubmissionClaim:
    delivery_id: TypeIDType
    operation_id: str
    submitted_at: datetime
    lease_until: datetime
    cadence_fingerprint: str
    _custody: _Custody = field(default_factory=_Custody, compare=False)

    def consume(self) -> bool:
        with self._custody.lock:
            if self._custody.used:
                return False
            self._custody.used = True
            return True


def _enabled(settings: VisitPrepSettings) -> bool:
    return (
        type(settings) is VisitPrepSettings
        and settings.configuration_valid is True
        and settings.enabled is True
        and settings.public_api_enabled is True
        and settings.sms_send_enabled is True
    )


def _lock_row(session: Session, identifier: TypeIDType) -> VisitPrepSMSDelivery | None:
    hints = session.exec(
        select(col(VisitPrep.practice_id), col(VisitPrep.id))
        .join(
            VisitPrepSMSDelivery,
            col(VisitPrepSMSDelivery.visit_prep_id) == col(VisitPrep.id),
        )
        .where(VisitPrepSMSDelivery.id == identifier)
    ).one_or_none()
    if hints is None:
        return None
    session.exec(
        select(Practice).where(Practice.id == hints[0]).with_for_update()
    ).one()
    session.exec(
        select(VisitPrep)
        .where(VisitPrep.id == hints[1])
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    return session.exec(
        select(VisitPrepSMSDelivery)
        .where(VisitPrepSMSDelivery.id == identifier)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()


def _finish(
    session: Session, row: VisitPrepSMSDelivery, state: str, code: str | None
) -> Outcome:
    now = _database_now(session)
    if row.state == "submitting" and (
        row.lease_until is None or now >= row.lease_until
    ):
        state, code = "uncertain", "submission_uncertain"
    row.state, row.outcome_code = state, code
    row.payload_ciphertext, row.lease_until, row.finalized_at = None, None, now
    session.add(row)
    session.flush()
    _audit(session, row, state)
    session.commit()
    return cast(Outcome, state)


def _eligible(session, row, settings, approval_verifier, consent_verifier):
    verify_sms_evidence(session, row)
    locked = resolve_locked_sms_authority(
        session,
        approval=read_sms_approval(row),
        visit_prep_id=row.visit_prep_id,
        settings=settings,
        approval_verifier=approval_verifier,
        consent_verifier=consent_verifier,
    )
    if (
        locked.credential.id != row.credential_id
        or locked.consent.id != row.consent_event_id
        or locked.checked_at >= row.expires_at
    ):
        raise VisitPrepSMSUnavailable()
    action, reason, _, fingerprint = current_sms_timing(
        session, locked, settings, cast(SMSStep, row.step), exclude=row
    )
    return locked, action, reason, fingerprint


def claim_sms_invitation(
    *,
    settings: VisitPrepSettings,
    approval_verifier: SMSApprovalVerifier | None = None,
    consent_verifier: SMSConsentVerifier | None = None,
) -> SMSSubmissionClaim | Outcome:
    if (
        not _enabled(settings)
        or not callable(approval_verifier)
        or not callable(consent_verifier)
    ):
        return "disabled"
    try:
        with Session(get_engine()) as session:
            identifiers = session.exec(
                select(col(VisitPrepSMSDelivery.id))
                .where(VisitPrepSMSDelivery.state == "queued")
                .order_by(
                    col(VisitPrepSMSDelivery.requested_at), col(VisitPrepSMSDelivery.id)
                )
                .limit(16)
            ).all()
            waiting = False
            for identifier in identifiers:
                session.rollback()
                row = _lock_row(session, identifier)
                if row is None or row.state != "queued":
                    continue
                try:
                    _, action, _, fingerprint = _eligible(
                        session, row, settings, approval_verifier, consent_verifier
                    )
                except Exception:
                    session.rollback()
                    row = _lock_row(session, identifier)
                    if row is None or row.state != "queued":
                        continue
                    return _finish(session, row, "suppressed", "scope_unavailable")
                if action == "wait":
                    waiting = True
                    continue
                if action != "candidate":
                    return _finish(session, row, "suppressed", "obsolete_context")
                row.state = "submitting"
                row.submitted_at = _database_now(session)
                row.lease_until = row.submitted_at + timedelta(seconds=90)
                session.add(row)
                session.flush()
                session.refresh(row)
                _audit(session, row, "submitting")
                assert row.submitted_at is not None and row.lease_until is not None
                claim = SMSSubmissionClaim(
                    row.id,
                    str(row.operation_id),
                    row.submitted_at,
                    row.lease_until,
                    fingerprint,
                )
                session.commit()
                return claim
            return "wait" if waiting else "idle"
    except Exception:
        return "storage_unavailable"


def dispatch_sms_claim(
    claim: SMSSubmissionClaim,
    *,
    settings: VisitPrepSettings,
    transport: SMSTransport | None = None,
    approval_verifier: SMSApprovalVerifier | None = None,
    consent_verifier: SMSConsentVerifier | None = None,
) -> Outcome:
    if type(claim) is not SMSSubmissionClaim or not claim.consume():
        return "discarded"
    try:
        with Session(get_engine()) as session:
            row = _lock_row(session, claim.delivery_id)
            if (
                row is None
                or row.state != "submitting"
                or str(row.operation_id) != claim.operation_id
                or row.submitted_at != claim.submitted_at
                or row.lease_until != claim.lease_until
            ):
                return "discarded"
            if row.lease_until is None or row.lease_until <= _database_now(session):
                return _finish(session, row, "uncertain", "submission_uncertain")
            try:
                if not _enabled(settings) or not callable(transport):
                    raise VisitPrepSMSUnavailable()
                locked, action, _, fingerprint = _eligible(
                    session, row, settings, approval_verifier, consent_verifier
                )
                message = decrypt_sms_message(session, row, locked)
                # Recheck after decryption and all source/policy reads. The transaction
                # keeps owner/root, consent and sender-recipient locks through invocation.
                _, final_action, _, final_fingerprint = _eligible(
                    session, row, settings, approval_verifier, consent_verifier
                )
                now = _database_now(session)
                if (
                    action != "candidate"
                    or final_action != "candidate"
                    or fingerprint != claim.cadence_fingerprint
                    or fingerprint != final_fingerprint
                    or now >= row.expires_at
                    or row.lease_until is None
                    or now + timedelta(seconds=30) >= row.lease_until
                ):
                    raise VisitPrepSMSUnavailable()
            except Exception:
                session.rollback()
                row = _lock_row(session, claim.delivery_id)
                if row is None or row.state != "submitting":
                    return "discarded"
                return _finish(session, row, "suppressed", "scope_unavailable")
            transaction, nested = (
                session.get_transaction(),
                session.get_nested_transaction(),
            )
            state, code = "uncertain", "submission_uncertain"
            started = monotonic()
            try:
                assert transport is not None
                result = transport(message)
                if (
                    result == "accepted"
                    and monotonic() - started < 30
                    and session.get_transaction() is transaction
                    and session.get_nested_transaction() is nested
                    and transaction is not None
                    and transaction.is_active
                ):
                    state, code = "accepted", None
            except Exception:
                pass  # No provider exception, phone, body or bearer reaches logs/results.
            if (
                session.get_transaction() is not transaction
                or transaction is None
                or not transaction.is_active
            ):
                session.rollback()
                row = _lock_row(session, claim.delivery_id)
                if row is None or row.state != "submitting":
                    return "storage_unavailable"
                state, code = "uncertain", "submission_uncertain"
            return _finish(session, row, state, code)
    except Exception:
        # The committed submitting fence remains. Maintenance records uncertainty;
        # neither this worker nor an operator may blindly requeue it.
        return "storage_unavailable"


def run_one_sms(**options) -> Outcome:
    if not callable(options.get("transport")):
        return "disabled"
    claim = claim_sms_invitation(
        **{key: value for key, value in options.items() if key != "transport"}
    )
    return (
        dispatch_sms_claim(claim, **options)
        if isinstance(claim, SMSSubmissionClaim)
        else claim
    )


def maintain_sms_secrets(*, limit: int = 25) -> dict[str, int | str]:
    """Clear expired/obsolete queued secrets and expired claims even with sends off."""
    if type(limit) is not int or not 1 <= limit <= 100:
        return {"status": "storage_unavailable", "cleared": 0}
    cleared = 0
    try:
        with Session(get_engine()) as session:
            now = _database_now(session)
            latest_credential = (
                select(col(VisitPrepCredentialGeneration.id))
                .where(
                    VisitPrepCredentialGeneration.visit_prep_id
                    == VisitPrepSMSDelivery.visit_prep_id
                )
                .order_by(col(VisitPrepCredentialGeneration.generation).desc())
                .limit(1)
                .correlate(VisitPrepSMSDelivery)
                .scalar_subquery()
            )
            latest_consent = (
                select(col(VisitPrepSMSConsentEvent.id))
                .where(
                    VisitPrepSMSConsentEvent.visit_prep_id
                    == VisitPrepSMSDelivery.visit_prep_id
                )
                .order_by(col(VisitPrepSMSConsentEvent.event_sequence).desc())
                .limit(1)
                .correlate(VisitPrepSMSDelivery)
                .scalar_subquery()
            )
            latest_approval = (
                select(col(VisitPrepSMSApprovalEvent.event_kind))
                .where(
                    VisitPrepSMSApprovalEvent.owner_practice_member_id
                    == VisitPrepSMSDelivery.owner_practice_member_id
                )
                .order_by(col(VisitPrepSMSApprovalEvent.event_sequence).desc())
                .limit(1)
                .correlate(VisitPrepSMSDelivery)
                .scalar_subquery()
            )
            stopped = (
                select(col(VisitPrepSMSStopEvent.id))
                .where(
                    VisitPrepSMSStopEvent.sender_digest
                    == VisitPrepSMSDelivery.sender_digest,
                    VisitPrepSMSStopEvent.recipient_digest
                    == VisitPrepSMSDelivery.recipient_digest,
                )
                .correlate(VisitPrepSMSDelivery)
                .exists()
            )
            identifiers = session.exec(
                select(col(VisitPrepSMSDelivery.id))
                .join(
                    VisitPrep,
                    col(VisitPrep.id) == col(VisitPrepSMSDelivery.visit_prep_id),
                )
                .where(
                    or_(
                        and_(
                            col(VisitPrepSMSDelivery.state) == "submitting",
                            col(VisitPrepSMSDelivery.lease_until) <= now,
                        ),
                        and_(
                            col(VisitPrepSMSDelivery.state) == "queued",
                            or_(
                                col(VisitPrepSMSDelivery.expires_at) <= now,
                                col(VisitPrep.revoked_at).is_not(None),
                                col(VisitPrep.misdirected_at).is_not(None),
                                col(VisitPrep.completed_at).is_not(None),
                                col(VisitPrepSMSDelivery.credential_id)
                                != latest_credential,
                                col(VisitPrepSMSDelivery.consent_event_id)
                                != latest_consent,
                                latest_approval == "withdrawn",
                                stopped,
                            ),
                        ),
                    )
                )
                .order_by(col(VisitPrepSMSDelivery.requested_at))
                .limit(limit)
            ).all()
            for identifier in identifiers:
                session.rollback()
                row = _lock_row(session, identifier)
                if row is None or row.state not in {"queued", "submitting"}:
                    continue
                now = _database_now(session)
                if row.state == "submitting":
                    if row.lease_until is None or row.lease_until <= now:
                        _finish(session, row, "uncertain", "submission_uncertain")
                        cleared += 1
                elif row.expires_at <= now:
                    _finish(session, row, "suppressed", "expired")
                    cleared += 1
                else:
                    # Current source qualification is not needed to destroy a secret.
                    prep = session.get(VisitPrep, row.visit_prep_id)
                    if prep is None:
                        raise VisitPrepSMSUnavailable()
                    _, credential = _lock_current_lineage(session, visit_prep=prep)
                    consent = session.exec(
                        select(VisitPrepSMSConsentEvent)
                        .where(VisitPrepSMSConsentEvent.visit_prep_id == prep.id)
                        .order_by(col(VisitPrepSMSConsentEvent.event_sequence).desc())
                        .limit(1)
                    ).one_or_none()
                    stopped = read_locked_sms_suppression(
                        session,
                        sender_digest=row.sender_digest,
                        recipient_digest=row.recipient_digest,
                    )
                    approval = session.exec(
                        select(col(VisitPrepSMSApprovalEvent.event_kind))
                        .where(
                            VisitPrepSMSApprovalEvent.owner_practice_member_id
                            == row.owner_practice_member_id
                        )
                        .order_by(col(VisitPrepSMSApprovalEvent.event_sequence).desc())
                        .limit(1)
                    ).one_or_none()
                    if (
                        credential.id != row.credential_id
                        or credential.revoked_at is not None
                        or prep.revoked_at is not None
                        or prep.misdirected_at is not None
                        or prep.completed_at is not None
                        or consent is None
                        or consent.id != row.consent_event_id
                        or consent.event_kind != "granted"
                        or consent.expires_at is None
                        or consent.expires_at <= now
                        or approval == "withdrawn"
                        or stopped
                    ):
                        _finish(session, row, "suppressed", "obsolete_context")
                        cleared += 1
        return {"status": "available", "cleared": cleared}
    except Exception:
        return {"status": "storage_unavailable", "cleared": cleared}
