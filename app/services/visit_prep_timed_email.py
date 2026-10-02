"""Dormant timing-to-outbox connection, not a scheduler or send capability.

Current Core evidence and the existing qualified consent/authority handoff share
one transaction. Never replace an uncertain intent with a new operation key.
Explicit quiet/spacing policy is trusted Core input; no runtime default is added.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from app.configuration.visit_prep import VISIT_PREP_SETTINGS, VisitPrepSettings
from app.services.visit_prep_command_contract import access_token_hash
from app.services.visit_prep_current_timing import plan_locked_current_delivery
from app.services.visit_prep_delivery_timing import plan_delivery_timing
from app.services.visit_prep_email_authority import EmailConsentSourceVerifier
from app.services.visit_prep_email_contract import (
    VisitPrepEmailInvalid,
    _patient_origin,
)
from app.services.visit_prep_email_enqueue import (
    _ERRORS,
    EmailEnqueueReceipt,
    enqueue_locked_email_invitation,
)
from app.services.visit_prep_email_evidence import _uuid
from app.services.visit_prep_packages import _database_now
from app.services.visit_preps import _visit_prep_session

from activemodel.types import TypeIDType
from sqlmodel import Session


@dataclass(frozen=True, repr=False)
class TimedEmailResult:
    disposition: Literal["queued", "replayed", "wait", "omit"]
    reason: str
    next_check_at: datetime | None
    receipt: EmailEnqueueReceipt | None = None

    def __repr__(self) -> str:
        return "TimedEmailResult(<redacted>)"


def enqueue_locked_timed_email(
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
    quiet_hours: tuple[int, int],
    minimum_channel_spacing: timedelta,
    source_verifier: EmailConsentSourceVerifier | None = None,
    view_as_active: bool = False,
    settings: VisitPrepSettings = VISIT_PREP_SETTINGS,
) -> TimedEmailResult:
    """Evaluate latest timing and record due work without committing or sending.

    An existing same-generation intent uses the original exact replay checks,
    even if quiet hours or invitation state changed. A new intent requires the
    existing verified consent source and channel controls; a wait/omit is only
    timing advice. Callers roll back any failure and retain the same operation
    and input after an uncertain commit. No route or recurring job is connected.
    """
    if (
        type(expected_credential_generation) is not int
        or not 0 < expected_credential_generation <= 2_147_483_647
        or not _uuid(operation_id)
    ):
        raise VisitPrepEmailInvalid()
    access_token_hash(access_token)
    _patient_origin(patient_origin)
    scope = {
        "owner_practice_member_id": owner_practice_member_id,
        "owner_doctor_id": owner_doctor_id,
        "practice_id": practice_id,
        "expected_clerk_subject": expected_clerk_subject,
        "visit_prep_id": visit_prep_id,
        "settings": settings,
        "view_as_active": view_as_active,
    }
    plan = plan_locked_current_delivery(
        session,
        **scope,
        quiet_hours=quiet_hours,
        minimum_channel_spacing=minimum_channel_spacing,
    )
    existing = any(
        touch.credential_generation == expected_credential_generation
        for touch in plan.email_history
    )
    if (
        not existing
        and plan.snapshot.credential_generation != expected_credential_generation
    ):
        raise VisitPrepEmailInvalid()
    decision = next(item for item in plan.decisions if item.step == "booking_email")
    if not existing and decision.action != "candidate":
        return TimedEmailResult(
            decision.action, decision.reason, decision.next_check_at
        )
    receipt = enqueue_locked_email_invitation(
        session,
        **scope,
        expected_credential_generation=expected_credential_generation,
        access_token=access_token,
        operation_id=operation_id,
        patient_origin=patient_origin,
        source_verifier=source_verifier,
    )
    if not existing:
        # Encryption, audit and source checks can cross a quiet/spacing or
        # appointment boundary. Re-evaluate the original locked history at
        # the final database clock, not the newly inserted self-blocking row.
        final = plan_delivery_timing(
            plan.snapshot,
            now=_database_now(session),
            quiet_hours=quiet_hours,
            minimum_channel_spacing=minimum_channel_spacing,
            history=plan.email_history,
        )[0]
        if final.step != "booking_email" or final.action != "candidate":
            raise VisitPrepEmailInvalid()
    return TimedEmailResult(
        receipt.disposition, "already_planned" if existing else "due", None, receipt
    )


def enqueue_timed_email_invitation(**arguments) -> TimedEmailResult:
    """Commit one timing/handoff transaction; fixed private failure on uncertainty."""
    result = None
    try:
        with _visit_prep_session() as session:
            try:
                result = enqueue_locked_timed_email(session, **arguments)
                session.commit()
            except _ERRORS:
                session.rollback()
                raise
    except _ERRORS:
        result = None
    if result is None:
        raise VisitPrepEmailInvalid() from None
    return result
