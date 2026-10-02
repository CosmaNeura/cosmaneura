"""Audited owner-only email status; no contact, bearer or sending authority."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast

from app.configuration.visit_prep import VisitPrepSettings
from app.services.visit_prep_email_contract import VisitPrepEmailInvalid
from app.services.visit_prep_email_policy import _ERRORS, _authorize_owner
from app.services.visit_prep_packages import _database_now
from app.services.visit_preps import (
    _lock_current_lineage,
    _lock_visit_prep,
    _visit_prep_session,
)

from activemodel.types import TypeIDType
from app.models.authority import AuthorityAuditEvent
from app.models.visit_prep_delivery import VisitPrepEmailDelivery
from sqlmodel import col, select

EmailStatus = Literal[
    "not_requested",
    "queued",
    "submitting",
    "accepted",
    "failed",
    "suppressed",
    "uncertain",
]
EmailOutcomeCode = Literal[
    "transport_unavailable",
    "message_invalid",
    "scope_unavailable",
    "obsolete_context",
    "consent_withdrawn",
    "expired",
    "submission_uncertain",
]


@dataclass(frozen=True, repr=False)
class EmailStatusView:
    preparation_id: str
    status: EmailStatus
    outcome_code: EmailOutcomeCode | None
    current_credential_generation: int
    credential_generation: int | None
    context_current: bool | None
    invitation_expired: bool | None
    submission_window_elapsed: bool | None
    requested_at: datetime | None
    expires_at: datetime | None
    submitted_at: datetime | None
    finalized_at: datetime | None
    checked_at: datetime

    def __repr__(self) -> str:
        return "EmailStatusView(<redacted>)"


def get_email_status(
    *,
    owner_practice_member_id: TypeIDType,
    owner_doctor_id: TypeIDType,
    practice_id: TypeIDType,
    expected_clerk_subject: str,
    visit_prep_id: TypeIDType,
    settings: VisitPrepSettings,
    view_as_active: bool = False,
) -> EmailStatusView:
    """Latest intent across credentials, not a current permission or delivery read.

    Public/channel controls and invitation expiry do not deny the authenticated
    owner's retained operational record. Current owner access remains required.
    This command never decrypts a mailbox, bearer, answer or consent evidence,
    enqueues work, invokes maintenance, or calls a provider.
    """
    result = None
    try:
        if (
            type(settings) is not VisitPrepSettings
            or settings.configuration_valid is not True
            or settings.enabled is not True
            or view_as_active is not False
            or not isinstance(expected_clerk_subject, str)
            or not 1 <= len(expected_clerk_subject) <= 255
        ):
            raise VisitPrepEmailInvalid()
        with _visit_prep_session() as session:
            try:
                scope = {
                    "owner_practice_member_id": owner_practice_member_id,
                    "owner_doctor_id": owner_doctor_id,
                    "practice_id": practice_id,
                    "expected_clerk_subject": expected_clerk_subject,
                    "settings": settings,
                }
                owner, practice, _ = _authorize_owner(session, **scope)
                prep = _lock_visit_prep(
                    session,
                    owner_practice_member_id=owner.id,
                    visit_prep_id=visit_prep_id,
                )
                if (
                    prep.owner_doctor_id != owner.doctor_id
                    or prep.practice_id != practice.id
                ):
                    raise VisitPrepEmailInvalid()
                schedule, credential = _lock_current_lineage(session, visit_prep=prep)
                row = session.exec(
                    select(VisitPrepEmailDelivery)
                    .where(VisitPrepEmailDelivery.visit_prep_id == prep.id)
                    .order_by(col(VisitPrepEmailDelivery.credential_generation).desc())
                    .limit(1)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                ).one_or_none()
                if row is not None and (
                    row.owner_practice_member_id != owner.id
                    or row.question_set_version_id != prep.question_set_version_id
                    or row.state
                    not in {
                        "queued",
                        "submitting",
                        "accepted",
                        "failed",
                        "suppressed",
                        "uncertain",
                    }
                    or row.outcome_code
                    not in {
                        None,
                        "transport_unavailable",
                        "message_invalid",
                        "scope_unavailable",
                        "obsolete_context",
                        "consent_withdrawn",
                        "expired",
                        "submission_uncertain",
                    }
                ):
                    raise VisitPrepEmailInvalid()
                session.add(
                    AuthorityAuditEvent(
                        event_type="visit_prep.email_status_read",
                        outcome="allowed",
                        occurred_at=_database_now(session),
                        resource_type="visit_prep",
                        resource_ref=str(prep.id),
                        route="visit_prep_email_status",
                        intent="provider_invitation_status_review",
                        platform_refs=[
                            {"kind": "doctor", "ref": str(owner.doctor_id)},
                            {"kind": "practice", "ref": str(practice.id)},
                            {"kind": "practice_member", "ref": str(owner.id)},
                        ],
                    )
                )
                session.flush()
                _, _, now = _authorize_owner(session, **scope)
                result = EmailStatusView(
                    preparation_id=str(prep.id),
                    status=cast(EmailStatus, row.state) if row else "not_requested",
                    outcome_code=cast(EmailOutcomeCode | None, row.outcome_code)
                    if row
                    else None,
                    current_credential_generation=credential.generation,
                    credential_generation=row.credential_generation if row else None,
                    context_current=(
                        row.credential_generation == credential.generation
                        and row.schedule_generation_id == schedule.id
                        and prep.revoked_at is None
                        and prep.misdirected_at is None
                        and prep.completed_at is None
                        and credential.revoked_at is None
                    )
                    if row
                    else None,
                    invitation_expired=now >= row.expires_at if row else None,
                    submission_window_elapsed=(
                        now >= min(row.expires_at, row.lease_until)
                    )
                    if row and row.state == "submitting" and row.lease_until
                    else None,
                    requested_at=row.requested_at if row else None,
                    expires_at=row.expires_at if row else None,
                    submitted_at=row.submitted_at if row else None,
                    finalized_at=row.finalized_at if row else None,
                    checked_at=now,
                )
                session.commit()
            except _ERRORS:
                session.rollback()
                result = None
    except _ERRORS:
        result = None
    if result is None:
        raise VisitPrepEmailInvalid() from None
    return result
