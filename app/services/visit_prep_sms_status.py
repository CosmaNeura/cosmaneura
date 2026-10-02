"""Owner-only SMS intent snapshots; no private payload or sending authority."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, cast

from app.configuration.visit_prep import VisitPrepSettings
from app.services.visit_prep_email_policy import _ERRORS, _authorize_owner
from app.services.visit_prep_packages import _database_now
from app.services.visit_prep_sms_contract import SMSStep, VisitPrepSMSUnavailable
from app.services.visit_preps import _lock_current_lineage, _lock_visit_prep

from activemodel.session_manager import get_engine
from activemodel.types import TypeIDType
from app.models.authority import AuthorityAuditEvent
from sqlalchemy import text
from sqlmodel import Session

SMSStatus = Literal[
    "not_requested",
    "queued",
    "submitting",
    "accepted",
    "failed",
    "suppressed",
    "uncertain",
]
SMSOutcomeCode = Literal[
    "transport_unavailable",
    "scope_unavailable",
    "obsolete_context",
    "expired",
    "submission_uncertain",
]
_STEPS: tuple[SMSStep, ...] = ("booking_sms", "unopened_48h_sms", "unfinished_12h_sms")
_CODES = {
    "queued": {None},
    "submitting": {None},
    "accepted": {None},
    "failed": {"transport_unavailable"},
    "suppressed": {"scope_unavailable", "obsolete_context", "expired"},
    "uncertain": {"submission_uncertain"},
}


@dataclass(frozen=True, repr=False)
class SMSMessageStatus:
    step: SMSStep
    status: SMSStatus
    outcome_code: SMSOutcomeCode | None = None
    credential_generation: int | None = None
    context_current: bool | None = None
    invitation_expired: bool | None = None
    submission_window_elapsed: bool | None = None
    requested_at: datetime | None = None
    expires_at: datetime | None = None
    submitted_at: datetime | None = None
    finalized_at: datetime | None = None


@dataclass(frozen=True, repr=False)
class SMSStatusView:
    preparation_id: str
    current_credential_generation: int
    messages: tuple[SMSMessageStatus, ...]
    checked_at: datetime


def _time(value: object) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def get_sms_status(
    *,
    owner_practice_member_id: TypeIDType,
    owner_doctor_id: TypeIDType,
    practice_id: TypeIDType,
    expected_clerk_subject: str,
    visit_prep_id: TypeIDType,
    settings: VisitPrepSettings,
    view_as_active: bool = False,
) -> SMSStatusView:
    """Latest intent per step, including old credentials; never dispatch or clean up.

    Channel/public flags do not hide retained operational history from its current
    authorized owner. Root locking serializes with queue/worker changes. Only
    metadata columns are selected: not even encrypted payloads are loaded.
    Absence of an intent says nothing about permission, STOP or provider readiness.
    """
    result = None
    try:
        if (
            type(settings) is not VisitPrepSettings
            or settings.configuration_valid is not True
            or settings.enabled is not True
            or view_as_active is not False
            or type(expected_clerk_subject) is not str
            or not 1 <= len(expected_clerk_subject) <= 255
        ):
            raise VisitPrepSMSUnavailable()
        # Own transaction: never commit a caller's pending work or disclose an
        # uncommitted ambient snapshot. These are read/audit locks, not send locks.
        with Session(get_engine()) as session:
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
                    raise VisitPrepSMSUnavailable()
                schedule, credential = _lock_current_lineage(session, visit_prep=prep)
                rows = (
                    session.execute(
                        text("""
                    SELECT DISTINCT ON (sms.step)
                        sms.step, sms.state, sms.outcome_code, sms.requested_at,
                        sms.expires_at, sms.submitted_at, sms.lease_until, sms.finalized_at,
                        sms.owner_practice_member_id AS sms_owner,
                        sms.question_set_version_id AS sms_version,
                        c.generation, c.visit_prep_id AS credential_prep,
                        c.owner_practice_member_id AS credential_owner,
                        c.schedule_generation_id, s.visit_prep_id AS schedule_prep,
                        s.owner_practice_member_id AS schedule_owner
                    FROM visit_prep_sms_delivery sms
                    LEFT JOIN visit_prep_credential_generation c ON c.id = sms.credential_id
                    LEFT JOIN visit_prep_schedule_generation s ON s.id = c.schedule_generation_id
                    WHERE sms.visit_prep_id = :prep
                    ORDER BY sms.step, c.generation DESC NULLS FIRST, sms.requested_at DESC, sms.id DESC
                """),
                        {"prep": prep.id.uuid},
                    )
                    .mappings()
                    .all()
                )
                if len(rows) > 3 or len({r["step"] for r in rows}) != len(rows):
                    raise VisitPrepSMSUnavailable()
                session.add(
                    AuthorityAuditEvent(
                        event_type="visit_prep.sms_status_read",
                        outcome="allowed",
                        occurred_at=_database_now(session),
                        resource_type="visit_prep",
                        resource_ref=str(prep.id),
                        route="visit_prep_sms_status",
                        intent="provider_sms_status_review",
                        platform_refs=[
                            {"kind": "doctor", "ref": str(owner.doctor_id)},
                            {"kind": "practice", "ref": str(practice.id)},
                            {"kind": "practice_member", "ref": str(owner.id)},
                        ],
                    )
                )
                session.flush()
                _, _, now = _authorize_owner(session, **scope)
                messages = {}
                for row in rows:
                    state, code = row["state"], row["outcome_code"]
                    requested, expires = row["requested_at"], row["expires_at"]
                    submitted, lease, finalized = (
                        row["submitted_at"],
                        row["lease_until"],
                        row["finalized_at"],
                    )
                    if (
                        row["step"] not in _STEPS
                        or state not in _CODES
                        or code not in _CODES[state]
                        or row["sms_owner"] != owner.id.uuid
                        or row["sms_version"] != prep.question_set_version_id.uuid
                        or row["credential_prep"] != prep.id.uuid
                        or row["credential_owner"] != owner.id.uuid
                        or row["schedule_prep"] != prep.id.uuid
                        or row["schedule_owner"] != owner.id.uuid
                        or type(row["generation"]) is not int
                        or not 1 <= row["generation"] <= credential.generation
                        or not all(_time(t) for t in (requested, expires))
                        or not requested <= now
                        or not requested < expires <= requested + timedelta(hours=24)
                        or (
                            submitted is not None
                            and (
                                not _time(submitted)
                                or not requested <= submitted <= now
                            )
                        )
                        or (
                            finalized is not None
                            and (
                                not _time(finalized)
                                or not (submitted or requested) <= finalized <= now
                            )
                        )
                    ):
                        raise VisitPrepSMSUnavailable()
                    if state == "queued":
                        valid = (
                            submitted is None and lease is None and finalized is None
                        )
                    elif state == "submitting":
                        valid = (
                            submitted is not None
                            and _time(lease)
                            and lease == submitted + timedelta(seconds=90)
                            and finalized is None
                        )
                    else:
                        valid = (
                            lease is None
                            and finalized is not None
                            and (
                                state not in {"accepted", "uncertain"}
                                or submitted is not None
                            )
                        )
                    if not valid:
                        raise VisitPrepSMSUnavailable()
                    messages[row["step"]] = SMSMessageStatus(
                        step=cast(SMSStep, row["step"]),
                        status=cast(SMSStatus, state),
                        outcome_code=cast(SMSOutcomeCode | None, code),
                        credential_generation=row["generation"],
                        context_current=(
                            row["generation"] == credential.generation
                            and row["schedule_generation_id"] == schedule.id.uuid
                            and prep.revoked_at is None
                            and prep.misdirected_at is None
                            and prep.completed_at is None
                            and credential.revoked_at is None
                        ),
                        invitation_expired=now >= expires,
                        submission_window_elapsed=now >= min(expires, lease)
                        if state == "submitting"
                        else None,
                        requested_at=requested,
                        expires_at=expires,
                        submitted_at=submitted,
                        finalized_at=finalized,
                    )
                result = SMSStatusView(
                    str(prep.id),
                    credential.generation,
                    tuple(
                        messages.get(step, SMSMessageStatus(step, "not_requested"))
                        for step in _STEPS
                    ),
                    now,
                )
                session.commit()
            except (*_ERRORS, VisitPrepSMSUnavailable):
                session.rollback()
                result = None
    except (*_ERRORS, VisitPrepSMSUnavailable):
        result = None
    if result is None:
        raise VisitPrepSMSUnavailable() from None
    return result
