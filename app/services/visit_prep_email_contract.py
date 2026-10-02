"""Minimum-necessary invitation email; no answers or arbitrary message fields.

Adapted and changed from CAB's single-recipient/message-boundary behavior at
01ad97536c4f. Apache-2.0 license retained in docs/licenses/cab-apache-2.0.txt.
Unlike CAB's insurance policy filter, this template has a closed input set.
The bearer is intentionally present in the recipient's invitation, never its
subject or message ID. Only an authorized outbox may supply these inputs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from uuid import UUID

from app.services.visit_prep_command_contract import (
    VisitPrepValidation,
    access_token_hash,
)

INVITATION_TEMPLATE_VERSION = "visit_prep_invitation_v1"
INVITATION_SUBJECT = "An invitation to prepare for your visit"
_EMAIL = re.compile(
    r"(?=.{3,254}\Z)(?![^@]*\.\.)"
    r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]{1,64}@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z"
)
_HOST = re.compile(
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?\Z"
)


class VisitPrepEmailInvalid(ValueError):
    def __init__(self) -> None:
        super().__init__("Visit Prep email is invalid")


def email_address(value: object) -> str:
    """One ASCII mailbox, not a display name, list, or injectable header."""
    if not isinstance(value, str) or _EMAIL.fullmatch(value) is None:
        raise VisitPrepEmailInvalid()
    return value


def _patient_origin(value: object) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 261
        or not value.isascii()
        or any(character.isspace() or ord(character) < 32 for character in value)
        or "\x7f" in value
    ):
        raise VisitPrepEmailInvalid()
    origin = None
    try:
        origin = urlsplit(value)
    except ValueError:
        pass
    # This comes from trusted Core configuration, never a caller-supplied URL.
    # No query/fragment, credentials, port, path, IP literal, or redirect wrapper.
    if (
        origin is None
        or origin.scheme != "https"
        or not origin.netloc
        or _HOST.fullmatch(origin.netloc) is None
        or origin.path
        or origin.query
        or origin.fragment
        or value != f"https://{origin.netloc}"
    ):
        raise VisitPrepEmailInvalid()
    return value


@dataclass(frozen=True, repr=False)
class VisitPrepInvitationEmail:
    recipient: str = field(repr=False)
    patient_origin: str = field(repr=False)
    access_token: str = field(repr=False)
    operation_id: str = field(repr=False)

    def __post_init__(self) -> None:
        email_address(self.recipient)
        _patient_origin(self.patient_origin)
        invalid = False
        try:
            parsed = UUID(self.operation_id)
            invalid = str(parsed) != self.operation_id or parsed.version != 4
            access_token_hash(self.access_token)
        except (TypeError, ValueError, AttributeError, VisitPrepValidation):
            invalid = True
        if invalid:
            # Raise outside the handler, without retaining sensitive exceptions.
            raise VisitPrepEmailInvalid() from None

    def __repr__(self) -> str:
        return "VisitPrepInvitationEmail(<redacted>)"

    @property
    def subject(self) -> str:
        return INVITATION_SUBJECT

    @property
    def body(self) -> str:
        return (
            "Your care team invites you to prepare for your upcoming visit.\n\n"
            "Share what matters to you through five topics, including a space "
            "to tell your story in your own words. You can skip a topic, return "
            "later, and review or correct your answers.\n\n"
            "Open your private preparation link:\n"
            f"{self.patient_origin}/visit-prep#access_token={self.access_token}\n\n"
            "This link is intended only for you or your caregiver. Do not "
            "forward it. If this invitation is not for you, use the "
            "wrong-recipient option without entering any health information.\n\n"
            "Do not reply to this email with health information. Visit Prep "
            "is not monitored in real time and is not for emergencies. "
            "Contact your care team directly for urgent concerns."
        )
