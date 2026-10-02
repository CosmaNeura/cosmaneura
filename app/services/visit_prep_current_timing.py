"""Current locked Core cadence evidence, never messaging authorization.

Email and SMS have separate durable histories. An empty email history never
implies that no SMS was sent. Shared-sender spacing also requires the SMS worker's
cross-preparation channel fence before dispatch.
Keep the caller's transaction open through any separately authorized handoff.
No contact, bearer, answer, consent decryption, provider call or commit occurs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, cast

from app.configuration.visit_prep import VISIT_PREP_SETTINGS, VisitPrepSettings
from app.services.visit_prep_delivery_timing import (
    TimingDecision,
    TimingSnapshot,
    TimingTouch,
    plan_delivery_timing,
)
from app.services.visit_prep_email_contract import (
    INVITATION_TEMPLATE_VERSION,
    VisitPrepEmailInvalid,
)
from app.services.visit_prep_email_payload import _canonical_ref
from app.services.visit_prep_email_policy import _authorize_owner
from app.services.visit_prep_open_evidence import read_invitation_open_evidence
from app.services.visit_prep_packages import _database_now
from app.services.visit_preps import (
    _appointment_matches_schedule,
    _lock_current_lineage,
    _lock_visit_prep,
    _owned_appointment,
    _owned_patient,
)

from activemodel.types import TypeIDType
from app.models.visit_prep import (
    VisitPrep,
    VisitPrepCredentialGeneration,
    VisitPrepScheduleGeneration,
)
from app.models.visit_prep_delivery import VisitPrepEmailDelivery
from app.models.visit_prep_sms_delivery import VisitPrepSMSDelivery
from sqlalchemy import and_
from sqlalchemy import select as columns_select
from sqlmodel import Session, col


@dataclass(frozen=True, repr=False)
class CurrentTimingPlan:
    preparation_id: str
    snapshot: TimingSnapshot
    email_history: tuple[TimingTouch, ...]
    decisions: tuple[TimingDecision, ...]
    checked_at: datetime
    sms_history_available: bool = True
    sms_history: tuple[TimingTouch, ...] = ()

    def __repr__(self) -> str:
        return "CurrentTimingPlan(<redacted>)"


def _email_history(session: Session, prep: VisitPrep) -> tuple[TimingTouch, ...]:
    # Root serialization prevents a legitimate new intent appearing between
    # this read and handoff. Lock existing outbox rows, including old pending
    # submissions; never select/decrypt their private envelopes. The FK alone
    # does not prove an email's schedule is the credential's actual schedule.
    rows = session.execute(
        columns_select(
            col(VisitPrepEmailDelivery.owner_practice_member_id),
            col(VisitPrepEmailDelivery.question_set_version_id),
            col(VisitPrepEmailDelivery.schedule_generation_id),
            col(VisitPrepEmailDelivery.credential_generation),
            col(VisitPrepEmailDelivery.template_version),
            col(VisitPrepEmailDelivery.state),
            col(VisitPrepEmailDelivery.requested_at),
            col(VisitPrepEmailDelivery.submitted_at),
            col(VisitPrepCredentialGeneration.owner_practice_member_id),
            col(VisitPrepCredentialGeneration.schedule_generation_id),
            col(VisitPrepScheduleGeneration.generation),
            col(VisitPrepScheduleGeneration.owner_practice_member_id),
            col(VisitPrepScheduleGeneration.visit_prep_id),
        )
        .select_from(VisitPrepEmailDelivery)
        .outerjoin(
            VisitPrepCredentialGeneration,
            and_(
                col(VisitPrepCredentialGeneration.visit_prep_id)
                == col(VisitPrepEmailDelivery.visit_prep_id),
                col(VisitPrepCredentialGeneration.generation)
                == col(VisitPrepEmailDelivery.credential_generation),
            ),
        )
        .outerjoin(
            VisitPrepScheduleGeneration,
            col(VisitPrepScheduleGeneration.id)
            == col(VisitPrepEmailDelivery.schedule_generation_id),
        )
        .where(col(VisitPrepEmailDelivery.visit_prep_id) == prep.id)
        .order_by(col(VisitPrepEmailDelivery.credential_generation))
        .limit(129)
        .with_for_update(of=VisitPrepEmailDelivery)
    ).all()
    if len(rows) > 128:
        raise VisitPrepEmailInvalid()
    history = []
    for row in rows:
        (
            owner,
            question_version,
            schedule_id,
            credential_generation,
            template,
            state,
            requested_at,
            submitted_at,
            credential_owner,
            credential_schedule_id,
            schedule_generation,
            schedule_owner,
            schedule_prep_id,
        ) = row
        if (
            owner != prep.owner_practice_member_id
            or credential_owner != owner
            or schedule_owner != owner
            or schedule_prep_id != prep.id
            or schedule_generation is None
            or question_version != prep.question_set_version_id
            or schedule_id != credential_schedule_id
            or template != INVITATION_TEMPLATE_VERSION
        ):
            raise VisitPrepEmailInvalid()
        history.append(
            TimingTouch(
                step="booking_email",
                schedule_generation=schedule_generation,
                credential_generation=credential_generation,
                state=cast(
                    Literal[
                        "queued",
                        "submitting",
                        "accepted",
                        "failed",
                        "suppressed",
                        "uncertain",
                    ],
                    state,
                ),
                requested_at=requested_at,
                submitted_at=submitted_at,
            )
        )
    return tuple(history)


def plan_locked_current_delivery(
    session: Session,
    *,
    owner_practice_member_id: TypeIDType,
    owner_doctor_id: TypeIDType,
    practice_id: TypeIDType,
    expected_clerk_subject: str,
    visit_prep_id: TypeIDType,
    quiet_hours: tuple[int, int],
    minimum_channel_spacing: timedelta,
    view_as_active: bool = False,
    settings: VisitPrepSettings = VISIT_PREP_SETTINGS,
) -> CurrentTimingPlan:
    """Non-authorizing plan from current rows under the caller's transaction.

    Scope is normalized trusted Core input, not a browser assertion or service
    grant. Denial requires caller rollback. No source adapter, booking job or
    public route is installed. Explicit policy inputs introduce no live defaults.
    SMS history is loaded separately; timing still grants no sending authority.
    """
    if (
        type(settings) is not VisitPrepSettings
        or settings.configuration_valid is not True
        or settings.enabled is not True
        or view_as_active is not False
        or not isinstance(expected_clerk_subject, str)
        or not 1 <= len(expected_clerk_subject) <= 255
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
            raise VisitPrepEmailInvalid()
    if prep.patient_id is not None:
        patient = _owned_patient(
            session, owner_doctor_id=owner.doctor_id, patient_id=prep.patient_id
        )
        session.refresh(patient)
        if patient.doctor_id != owner.doctor_id:
            raise VisitPrepEmailInvalid()
    if (
        type(credential.token_format_version) is not int
        or credential.token_format_version != 1
    ):
        raise VisitPrepEmailInvalid()
    history = _email_history(session, prep)
    sms_history = _sms_history(session, prep)
    # All waits precede the final authoritative clock/access recheck. An owner
    # lock freezes mutation, not expiry. Do not use a caller-supplied clock.
    _, _, now = _authorize_owner(session, **scope)
    opened = read_invitation_open_evidence(
        session, prep=prep, at=_database_now(session)
    )
    _, _, now = _authorize_owner(session, **scope)
    snapshot = TimingSnapshot(
        booked_at=credential.issued_at,
        scheduled_at=schedule.scheduled_at,
        credential_expires_at=credential.expires_at,
        timezone_name=schedule.timezone_name,
        schedule_generation=schedule.generation,
        credential_generation=credential.generation,
        lifecycle_state=cast(
            Literal["invited", "confirmed", "in_progress", "completed", "misdirected"],
            prep.lifecycle_state,
        ),
        first_party_open_state=opened.state,
        opened_at=opened.opened_at,
        completed_at=prep.completed_at,
        revoked=prep.revoked_at is not None or credential.revoked_at is not None,
        appointment_cancelled=credential.revocation_reason == "appointment_cancelled",
    )
    decisions = plan_delivery_timing(
        snapshot,
        now=now,
        quiet_hours=quiet_hours,
        minimum_channel_spacing=minimum_channel_spacing,
        history=history + sms_history,
    )
    return CurrentTimingPlan(
        str(prep.id), snapshot, history, decisions, now, True, sms_history
    )


def _sms_history(session: Session, prep: VisitPrep) -> tuple[TimingTouch, ...]:
    rows = session.execute(
        columns_select(
            col(VisitPrepSMSDelivery.owner_practice_member_id),
            col(VisitPrepSMSDelivery.question_set_version_id),
            col(VisitPrepSMSDelivery.step),
            col(VisitPrepSMSDelivery.template_version),
            col(VisitPrepSMSDelivery.state),
            col(VisitPrepSMSDelivery.requested_at),
            col(VisitPrepSMSDelivery.submitted_at),
            col(VisitPrepCredentialGeneration.generation),
            col(VisitPrepCredentialGeneration.visit_prep_id),
            col(VisitPrepCredentialGeneration.owner_practice_member_id),
            col(VisitPrepScheduleGeneration.generation),
            col(VisitPrepScheduleGeneration.visit_prep_id),
            col(VisitPrepScheduleGeneration.owner_practice_member_id),
        )
        .select_from(VisitPrepSMSDelivery)
        .outerjoin(
            VisitPrepCredentialGeneration,
            col(VisitPrepCredentialGeneration.id)
            == col(VisitPrepSMSDelivery.credential_id),
        )
        .outerjoin(
            VisitPrepScheduleGeneration,
            col(VisitPrepScheduleGeneration.id)
            == col(VisitPrepCredentialGeneration.schedule_generation_id),
        )
        .where(col(VisitPrepSMSDelivery.visit_prep_id) == prep.id)
        .order_by(col(VisitPrepSMSDelivery.requested_at))
        .limit(129)
        .with_for_update(of=VisitPrepSMSDelivery)
    ).all()
    if len(rows) > 128:
        raise VisitPrepEmailInvalid()
    history = []
    for (
        owner,
        version,
        step,
        template,
        state,
        requested,
        submitted,
        generation,
        credential_prep,
        credential_owner,
        schedule_generation,
        schedule_prep,
        schedule_owner,
    ) in rows:
        if (
            owner != prep.owner_practice_member_id
            or version != prep.question_set_version_id
            or credential_prep != prep.id
            or schedule_prep != prep.id
            or credential_owner != owner
            or schedule_owner != owner
            or generation is None
            or schedule_generation is None
            or template != "visit_prep_sms_v1"
            or step not in {"booking_sms", "unopened_48h_sms", "unfinished_12h_sms"}
        ):
            raise VisitPrepEmailInvalid()
        history.append(
            TimingTouch(
                step, schedule_generation, generation, state, requested, submitted
            )
        )
    return tuple(history)
