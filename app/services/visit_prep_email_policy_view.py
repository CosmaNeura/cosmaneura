"""Current owner policy display; not recipient consent or permission to send."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, cast

from app.configuration.visit_prep import VisitPrepSettings
from app.services.visit_prep_email_contract import (
    INVITATION_TEMPLATE_VERSION,
    VisitPrepEmailInvalid,
    VisitPrepInvitationEmail,
)
from app.services.visit_prep_email_evidence import (
    email_policy_fingerprint,
    read_email_approval,
)
from app.services.visit_prep_email_policy import _ERRORS, _authorize_owner
from app.services.visit_prep_packages import _database_now
from app.services.visit_preps import _visit_prep_session

from activemodel.types import TypeIDType
from app.models.authority import AuthorityAuditEvent
from app.models.visit_prep_email_authority import VisitPrepEmailApprovalEvent
from sqlmodel import Session, select

PolicyStatus = Literal[
    "not_approved", "approved", "withdrawn", "expired", "policy_changed"
]


@dataclass(frozen=True, repr=False)
class EmailPolicyView:
    policy_fingerprint: str
    template_version: str
    subject: str
    template_text: str
    status: PolicyStatus
    event_sequence: int
    decision_policy_fingerprint: str | None
    recorded_at: datetime | None
    expires_at: datetime | None
    checked_at: datetime

    def __repr__(self) -> str:
        return "EmailPolicyView(<redacted>)"


def _template() -> VisitPrepInvitationEmail:
    # Public placeholders only. Never select a preparation or issue a credential
    # to display the fixed policy. Replace the full placeholder link on the wire.
    return VisitPrepInvitationEmail(
        recipient="policy@example.invalid",
        patient_origin="https://policy.example.invalid",
        access_token="vp1_" + "A" * 43,
        operation_id="00000000-0000-4000-8000-000000000001",
    )


def _read(session: Session, scope: dict) -> EmailPolicyView:
    owner, practice, _ = _authorize_owner(session, **scope)
    row = session.exec(
        select(VisitPrepEmailApprovalEvent)
        .where(VisitPrepEmailApprovalEvent.owner_practice_member_id == owner.id)
        .order_by(cast(Any, VisitPrepEmailApprovalEvent.event_sequence).desc())
        .limit(1)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    evidence = read_email_approval(row) if row is not None else None
    fingerprint = email_policy_fingerprint()
    if evidence is not None and (
        evidence.owner_practice_member_id != str(owner.id)
        or evidence.owner_doctor_id != str(owner.doctor_id)
        or evidence.practice_id != str(practice.id)
    ):
        raise VisitPrepEmailInvalid()
    invitation = _template()
    text = invitation.body.replace(
        f"{invitation.patient_origin}/visit-prep#access_token={invitation.access_token}",
        "{private_preparation_link}",
    )
    session.add(
        AuthorityAuditEvent(
            event_type="visit_prep.email_policy_read",
            outcome="allowed",
            occurred_at=_database_now(session),
            recorded_at=_database_now(session),
            resource_type="visit_prep_email_policy",
            resource_ref=str(owner.id),
            content_sha256=fingerprint,
            route="visit_prep_email_policy_read",
            intent="physician_invitation_policy_review",
            platform_refs=[
                {"kind": "doctor", "ref": str(owner.doctor_id)},
                {"kind": "practice", "ref": str(practice.id)},
                {"kind": "practice_member", "ref": str(owner.id)},
            ],
        )
    )
    session.flush()
    _, _, now = _authorize_owner(session, **scope)
    if fingerprint != email_policy_fingerprint():
        raise VisitPrepEmailInvalid()
    if evidence is not None and evidence.occurred_at > now:
        raise VisitPrepEmailInvalid()
    status: PolicyStatus = "not_approved"
    if evidence is not None:
        if evidence.event_kind == "withdrawn":
            status = "withdrawn"
        elif evidence.policy_fingerprint != fingerprint:
            status = "policy_changed"
        elif evidence.expires_at is None or evidence.expires_at <= now:
            status = "expired"
        else:
            status = "approved"
    result = EmailPolicyView(
        fingerprint,
        INVITATION_TEMPLATE_VERSION,
        invitation.subject,
        text,
        status,
        evidence.event_sequence if evidence is not None else 0,
        evidence.policy_fingerprint if evidence is not None else None,
        row.recorded_at if row is not None else None,
        evidence.expires_at if evidence is not None else None,
        now,
    )
    session.commit()
    return result


def get_email_policy_view(
    *,
    owner_practice_member_id: TypeIDType,
    owner_doctor_id: TypeIDType,
    practice_id: TypeIDType,
    expected_clerk_subject: str,
    settings: VisitPrepSettings,
    view_as_active: bool = False,
) -> EmailPolicyView:
    """Read current policy/decision under authority locks, never send readiness."""
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
                result = _read(
                    session,
                    {
                        "owner_practice_member_id": owner_practice_member_id,
                        "owner_doctor_id": owner_doctor_id,
                        "practice_id": practice_id,
                        "expected_clerk_subject": expected_clerk_subject,
                        "settings": settings,
                    },
                )
            except _ERRORS:
                session.rollback()
                raise
    except _ERRORS:
        pass
    if result is None:
        raise VisitPrepEmailInvalid()
    return result
