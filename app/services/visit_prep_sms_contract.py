"""Closed minimal SMS templates and separately verified standing approval.

Provider-specific approval/source adapters are not installed. Email authority,
staff statements, a booking connection or a keyed source label are not SMS grants.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from app.services.visit_prep_command_contract import access_token_hash
from app.services.visit_prep_email_contract import _patient_origin
from app.services.visit_prep_email_evidence import _digest
from app.services.visit_prep_sms_suppression import sms_recipient_digest

from sqlmodel import Session

SMSStep = Literal["booking_sms", "unopened_48h_sms", "unfinished_12h_sms"]
STEPS = frozenset({"booking_sms", "unopened_48h_sms", "unfinished_12h_sms"})
POLICY_TEXT = (
    "Allow automated Visit Prep booking invitations and reminders at 48 hours for "
    "unopened preparations and 12 hours for unfinished preparations, only with current "
    "independently qualified recipient SMS permission and no sender-wide STOP. Apply "
    "the central practice quiet hours and at least four-hour same-channel spacing. "
    "No marketing, patient answers or clinical facts in texts. Source/sender approval, "
    "cancellation, rescheduling, completion and uncertainty controls remain required."
)
_OPENINGS = {
    "booking_sms": "Your care team invites you to prepare for your visit.",
    "unopened_48h_sms": "Reminder: your private visit preparation is ready.",
    "unfinished_12h_sms": "Reminder: you can finish preparing for your visit.",
}
_ENDING = "Do not forward this link or text health information. Reply STOP to stop texts. Not monitored for emergencies."
POLICY_FINGERPRINT = hashlib.sha256(
    json.dumps(
        {
            "policy": POLICY_TEXT,
            "version": "visit_prep_sms_v1",
            "openings": _OPENINGS,
            "private_link": "{patient_origin}/visit-prep#access_token={access_token}",
            "ending": _ENDING,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
).hexdigest()


class VisitPrepSMSUnavailable(ValueError):
    def __init__(self):
        super().__init__("Visit Prep SMS is unavailable.")


@dataclass(frozen=True, repr=False)
class SMSStandingApproval:
    """Normalized verified source, never patient/browser supplied authorization."""

    owner_practice_member_id: str
    owner_doctor_id: str
    practice_id: str
    subject: str
    sender_digest: str
    policy_fingerprint: str
    source_event_digest: str
    source_evidence_hmac: str
    effective_at: datetime
    expires_at: datetime


class SMSApprovalVerifier(Protocol):
    def __call__(
        self, session: Session, approval: SMSStandingApproval, *, checked_at: datetime
    ) -> bool:
        """Verify current durable physician approval and qualified source/sender.

        Must compare all fields against authenticated Core source evidence under
        current source/approval locks. No commit, rollback, external I/O or release.
        Missing source qualification fails closed. A sealed claim alone grants nothing.
        """
        ...


class SMSConsentVerifier(Protocol):
    def __call__(
        self, session: Session, *, consent_event_id: str, checked_at: datetime
    ) -> bool:
        """Qualify exact SMS choice source/control/caregiver authority, without I/O.

        Self-attestation captured on this branch is NOT independently qualified.
        No installed default verifier accepts it; live qualification remains a gate.
        """
        ...


@dataclass(frozen=True, repr=False)
class VisitPrepText:
    recipient: str
    sender_digest: str
    patient_origin: str
    access_token: str
    operation_id: str
    step: SMSStep

    def __post_init__(self):
        try:
            sms_recipient_digest(self.recipient)
            _patient_origin(self.patient_origin)
            access_token_hash(self.access_token)
            parsed = UUID(self.operation_id)
            if (
                not _digest(self.sender_digest)
                or parsed.version != 4
                or str(parsed) != self.operation_id
                or self.step not in STEPS
            ):
                raise ValueError
        except Exception:
            raise VisitPrepSMSUnavailable() from None

    @property
    def body(self):
        opening = _OPENINGS[self.step]
        return (
            f"{opening} {self.patient_origin}/visit-prep#access_token={self.access_token} "
            f"{_ENDING}"
        )


class SMSTransport(Protocol):
    def __call__(self, message: VisitPrepText, /) -> Literal["accepted"]:
        """Trusted Core adapter, one bounded 30-second attempt, no retry/logging.

        Unknown result or exception after invocation is uncertain, not a safe retry.
        No real provider implementation is connected yet. Accepted is not delivered.
        """
        ...
