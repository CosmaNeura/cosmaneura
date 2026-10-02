"""Current physician SMS policy display; never sender qualification or a send."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.configuration.visit_prep_cadence import (
    VisitPrepCadenceSettings,
    current_standard_cadence,
)
from app.services.visit_prep_email_policy import _authorize_owner
from app.services.visit_prep_packages import _database_now
from app.services.visit_prep_sms_contract import (
    POLICY_FINGERPRINT,
    POLICY_TEXT,
    SMSStep,
    VisitPrepSMSUnavailable,
    VisitPrepText,
)
from app.services.visit_prep_sms_policy import _latest, _subject, _valid_scope
from app.services.visit_prep_sms_suppression import configured_sms_sender_digest

from activemodel.session_manager import get_engine
from app.models.authority import AuthorityAuditEvent
from sqlmodel import Session

SMSPolicyStatus = Literal[
    "not_approved",
    "approved",
    "withdrawn",
    "expired",
    "policy_changed",
    "sender_changed",
    "identity_changed",
]


@dataclass(frozen=True, repr=False)
class SMSPolicyView:
    policy_fingerprint: str
    policy_text: str
    sender_digest: str | None
    templates: dict[SMSStep, str]
    status: SMSPolicyStatus
    event_sequence: int
    decision_policy_fingerprint: str | None
    decision_sender_digest: str | None
    recorded_at: datetime | None
    expires_at: datetime | None
    checked_at: datetime
    standard_cadence: VisitPrepCadenceSettings | None


def _templates() -> dict[SMSStep, str]:
    # Render the actual fixed templates using public invented placeholders only.
    # Do not read a patient, contact or credential to show the policy.
    origin, token = "https://policy.example.invalid", "vp1_" + "A" * 43
    return {
        step: VisitPrepText(
            recipient="+12025550123",
            sender_digest="a" * 64,
            patient_origin=origin,
            access_token=token,
            operation_id="00000000-0000-4000-8000-000000000001",
            step=step,
        ).body.replace(
            f"{origin}/visit-prep#access_token={token}", "{private_preparation_link}"
        )
        for step in ("booking_sms", "unopened_48h_sms", "unfinished_12h_sms")
    }


def get_sms_policy_view(*, view_as_active: bool = False, **scope) -> SMSPolicyView:
    result = None
    try:
        _valid_scope(scope, view_as_active)
        with Session(get_engine()) as session:
            owner, practice, _ = _authorize_owner(session, **scope)
            row = _latest(session, owner.id)
            sender, cadence = configured_sms_sender_digest(), current_standard_cadence()
            if row is not None and (
                row.practice_id != practice.id or row.owner_doctor_id != owner.doctor_id
            ):
                raise VisitPrepSMSUnavailable()
            session.add(
                AuthorityAuditEvent(
                    event_type="visit_prep.sms_policy_read",
                    outcome="allowed",
                    occurred_at=_database_now(session),
                    resource_type="visit_prep_sms_policy",
                    resource_ref=str(owner.id),
                    content_sha256=POLICY_FINGERPRINT,
                    route="visit_prep_sms_policy_read",
                    intent="physician_sms_policy_review",
                    platform_refs=[
                        {"kind": "doctor", "ref": str(owner.doctor_id)},
                        {"kind": "practice", "ref": str(practice.id)},
                        {"kind": "practice_member", "ref": str(owner.id)},
                    ],
                )
            )
            session.flush()
            _, _, now = _authorize_owner(session, **scope)
            if (
                sender != configured_sms_sender_digest()
                or cadence != current_standard_cadence()
            ):
                raise VisitPrepSMSUnavailable()
            status: SMSPolicyStatus = "not_approved"
            if row is not None:
                if row.event_kind == "withdrawn":
                    status = "withdrawn"
                elif row.subject_digest != _subject(scope["expected_clerk_subject"]):
                    status = "identity_changed"
                elif row.policy_fingerprint != POLICY_FINGERPRINT:
                    status = "policy_changed"
                elif row.sender_digest != sender:
                    status = "sender_changed"
                elif row.expires_at is None or row.expires_at <= now:
                    status = "expired"
                else:
                    status = "approved"
            result = SMSPolicyView(
                POLICY_FINGERPRINT,
                POLICY_TEXT,
                sender,
                _templates(),
                status,
                row.event_sequence if row else 0,
                row.policy_fingerprint if row else None,
                row.sender_digest if row else None,
                row.recorded_at if row else None,
                row.expires_at if row else None,
                now,
                cadence,
            )
            session.commit()
    except Exception:
        result = None
    if result is None:
        raise VisitPrepSMSUnavailable() from None
    return result
