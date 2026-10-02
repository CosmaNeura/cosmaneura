"""Dormant, single-recipient SMTP invitation transport using Core's mailer.

Adapted and changed from CAB 01ad97536c4f's bounded submission and sanitized uncertainty pattern,
not its ACS dependency, credentials, billing policy, or database. A message ID
is provenance only: SMTP offers no exactly-once or idempotent-send guarantee.
Nothing imports this adapter into a route or constructs it during startup.
Apache-2.0 license retained in docs/licenses/cab-apache-2.0.txt.
"""

from __future__ import annotations

import asyncio
from email.message import EmailMessage
from email.policy import SMTP
from typing import Literal

from mailers import Mailer
from mailers.transports.smtp import SMTPTransport

from app.services.visit_prep_email_contract import (
    VisitPrepEmailInvalid,
    VisitPrepInvitationEmail,
    email_address,
)

SUBMISSION_TIMEOUT_SECONDS = 30


class VisitPrepEmailUnavailable(ValueError):
    def __init__(self) -> None:
        super().__init__("Visit Prep email transport is unavailable")


class VisitPrepEmailSubmissionUncertain(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Visit Prep email submission is uncertain")


class CoreSMTPInvitationTransport:
    def __init__(self, *, transport: SMTPTransport, sender_address: str) -> None:
        # Console/file/null/multi transports must never masquerade as acceptance.
        # The existing mailer factory may disable certificate checking by default;
        # do not silently inherit that for patient invitations or modify it here.
        if (
            type(transport) is not SMTPTransport
            or transport._use_tls is not True
            or transport._validate_certs is not True
        ):
            raise VisitPrepEmailUnavailable()
        self._sender = email_address(sender_address)
        self._mailer = Mailer(transport, from_address=self._sender)

    @classmethod
    def from_configured_mailer(cls) -> CoreSMTPInvitationTransport:
        from app.configuration import emailer

        configured = emailer._mailer
        if not isinstance(configured, Mailer) or not configured.from_address:
            raise VisitPrepEmailUnavailable()
        return cls(
            transport=configured.transport,  # type: ignore[arg-type]
            sender_address=configured.from_address,
        )

    def __repr__(self) -> str:
        return "CoreSMTPInvitationTransport(<redacted>)"

    async def _submit(self, message: EmailMessage) -> None:
        await asyncio.wait_for(
            self._mailer.send(message), timeout=SUBMISSION_TIMEOUT_SECONDS
        )

    def __call__(self, invitation: VisitPrepInvitationEmail) -> Literal["accepted"]:
        if type(invitation) is not VisitPrepInvitationEmail:
            raise VisitPrepEmailInvalid()
        # Reconstruct the closed inputs at the final transport boundary. There
        # is no arbitrary subject/body/header, attachment, CC, or BCC interface.
        checked = VisitPrepInvitationEmail(
            recipient=invitation.recipient,
            patient_origin=invitation.patient_origin,
            access_token=invitation.access_token,
            operation_id=invitation.operation_id,
        )
        message = EmailMessage(policy=SMTP)
        message["From"] = self._sender
        message["To"] = checked.recipient
        message["Subject"] = checked.subject
        message["Message-ID"] = f"<{checked.operation_id}@{self._sender.split('@')[1]}>"
        message.set_content(checked.body, subtype="plain", charset="utf-8")
        failed = False
        try:
            # Celery's worker thread has no event loop. Exactly one attempt; a
            # timeout, refusal, malformed result or lost response is not delivery.
            submission = self._submit(message)
            try:
                result = asyncio.run(submission)
            finally:
                # If called from an unexpected event-loop thread, asyncio.run
                # rejects before awaiting it. Close that unused coroutine too.
                submission.close()
            failed = result is not None
        except Exception:
            failed = True
        if failed:
            # Mailers/SMTP errors may retain recipients and payload in their
            # exception chains. Raise outside the handler, retaining neither.
            raise VisitPrepEmailSubmissionUncertain() from None
        return "accepted"
