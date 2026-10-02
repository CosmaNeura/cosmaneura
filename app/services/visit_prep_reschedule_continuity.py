"""A sealed confirmation may follow only unchanged-recipient reschedule lineage.

Called under the interview's canonical owner/root/current-lineage locks. Raw
bearers, contact values and network signals never enter this continuation lane.
"""

import hmac
from datetime import datetime

from app.services.visit_prep_interview_contract import VisitPrepInterviewUnavailable
from app.services.visit_prep_patient_continuation import ConfirmedVisitPrepAccess

from app.models.visit_prep import (
    VisitPrep,
    VisitPrepCredentialGeneration,
    VisitPrepScheduleGeneration,
)
from app.models.visit_prep_delivery_link import VisitPrepDeliveryLink
from sqlmodel import Session, col, select

MAX_CONTINUITY_GENERATIONS = 128


def validate_reschedule_continuity(
    session: Session,
    *,
    prep: VisitPrep,
    current: VisitPrepCredentialGeneration,
    access: ConfirmedVisitPrepAccess,
    at: datetime,
    own_wrong_recipient_replay: bool = False,
) -> datetime | None:
    delivery_deadline = None

    def unavailable():
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")

    proof = session.exec(
        select(VisitPrepCredentialGeneration)
        .where(
            VisitPrepCredentialGeneration.token_hash == access.token_hash,
            VisitPrepCredentialGeneration.visit_prep_id == prep.id,
            VisitPrepCredentialGeneration.owner_practice_member_id
            == prep.owner_practice_member_id,
        )
        .execution_options(populate_existing=True)
    ).one_or_none()
    if (
        proof is None
        or proof.token_format_version != 1
        or proof.issued_at > at
        or proof.expires_at != access.expires_at
        or access.expires_at <= at
        or access.confirmed_at > at
        or prep.confirmed_at != access.confirmed_at
        or prep.confirmed_by_role != access.actor_role
        or (
            prep.lifecycle_state not in {"confirmed", "in_progress", "completed"}
            and not own_wrong_recipient_replay
        )
        or prep.retain_until is None
        or prep.retain_until <= at
    ):
        unavailable()
    assert proof is not None
    anchors = [proof]
    if access.presented_token_hash is not None:
        presented = session.exec(
            select(VisitPrepCredentialGeneration)
            .where(
                VisitPrepCredentialGeneration.token_hash == access.presented_token_hash,
                VisitPrepCredentialGeneration.visit_prep_id == prep.id,
                VisitPrepCredentialGeneration.owner_practice_member_id
                == prep.owner_practice_member_id,
            )
            .execution_options(populate_existing=True)
        ).one_or_none()
        if presented is None:
            from app.services.visit_prep_delivery_links import (
                DeliveryLinkUnavailable,
                verify_delivery_link,
            )

            alias = session.exec(
                select(VisitPrepDeliveryLink).where(
                    VisitPrepDeliveryLink.token_hash == access.presented_token_hash
                )
            ).one_or_none()
            try:
                if alias is None:
                    raise DeliveryLinkUnavailable()
                verify_delivery_link(session, alias)
                if (
                    alias.visit_prep_id != prep.id
                    or alias.owner_practice_member_id != prep.owner_practice_member_id
                    or alias.question_set_version_id != prep.question_set_version_id
                    or alias.credential_id != current.id
                    or not alias.issued_at <= at < alias.expires_at
                ):
                    raise DeliveryLinkUnavailable()
                delivery_deadline = alias.expires_at
                presented = current
            except DeliveryLinkUnavailable:
                unavailable()
        if presented is None:
            unavailable()
        assert presented is not None
        anchors.append(presented)
    first = min(anchor.generation for anchor in anchors)
    expected = current.generation - first + 1
    if not 1 <= expected <= MAX_CONTINUITY_GENERATIONS:
        unavailable()
    lineage = session.exec(
        select(VisitPrepCredentialGeneration, VisitPrepScheduleGeneration)
        .join(
            VisitPrepScheduleGeneration,
            col(VisitPrepScheduleGeneration.id)
            == VisitPrepCredentialGeneration.schedule_generation_id,
        )
        .where(
            VisitPrepCredentialGeneration.visit_prep_id == prep.id,
            VisitPrepCredentialGeneration.owner_practice_member_id
            == prep.owner_practice_member_id,
            VisitPrepCredentialGeneration.generation >= first,
            VisitPrepCredentialGeneration.generation <= current.generation,
            VisitPrepScheduleGeneration.visit_prep_id == prep.id,
            VisitPrepScheduleGeneration.owner_practice_member_id
            == prep.owner_practice_member_id,
        )
        .order_by(col(VisitPrepCredentialGeneration.generation))
        .limit(MAX_CONTINUITY_GENERATIONS + 1)
        .execution_options(populate_existing=True)
    ).all()
    if len(lineage) != expected:
        unavailable()
    for index, (credential, schedule) in enumerate(lineage):
        if (
            credential.generation != first + index
            or credential.token_format_version != 1
            or credential.issued_at > at
        ):
            unavailable()
        if index == len(lineage) - 1:
            if credential.id != current.id:
                unavailable()
            continue  # Current revocation and expiry are checked by the caller.
        successor, next_schedule = lineage[index + 1]
        if (
            credential.revoked_at is None
            or credential.revocation_reason != "rescheduled"
            or credential.revoked_at < access.confirmed_at
            or credential.revoked_at > successor.issued_at
            or successor.issued_at < credential.issued_at
            or next_schedule.generation != schedule.generation + 1
            or hmac.compare_digest(credential.token_hash, successor.token_hash)
        ):
            unavailable()
    return delivery_deadline
