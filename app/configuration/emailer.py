"""
Simple email interface:

* Create plain text emails from markdown
* Use a consistent template for HTML and insert md => html converted emails
* Inline CSS
* Single entrypoint to allow for mutation or metrics in the future

"""

import asyncio
import re
from email.utils import getaddresses

import aiosmtplib
import markdown2
from aiosmtplib.errors import (
    SMTPAuthenticationError,
    SMTPConnectError,
    SMTPConnectTimeoutError,
    SMTPDataError,
    SMTPHeloError,
    SMTPRecipientsRefused,
    SMTPSenderRefused,
)
from decouple import config
from mailers import Email, Mailer
from mailers.preprocessors.cssliner import css_inliner
from mailers.preprocessors.remove_html_comments import remove_html_comments
from mailers.transports.smtp import SMTPTransport

from ..templates import render_template
from .clerk_otp_delivery import valid_recipient

_mailer: Mailer | None = None


class SmtpNotAccepted(Exception):
    """Transport proves the single-recipient message was not accepted."""


class SmtpOutcomeUnknown(Exception):
    """A timeout/disconnection may occur after SMTP acceptance."""


async def send_clerk_verification_code(recipient: str, code: str) -> None:
    """Use existing SMTP credentials with mandatory TLS and fixed content only."""
    if (
        _mailer is None
        or not isinstance(_mailer.transport, SMTPTransport)
        or not isinstance(_mailer.from_address, str)
        or any(ord(c) < 32 for c in _mailer.from_address)
        or not valid_recipient(recipient)
        or re.fullmatch(r"[0-9]{6}", code) is None
    ):
        raise SmtpNotAccepted()
    senders = getaddresses([_mailer.from_address])
    if (
        len(senders) != 1
        or not valid_recipient(senders[0][1])
        or senders[0][1].rpartition("@")[2].lower() != "cosmaneura.com"
    ):
        raise SmtpNotAccepted()
    sender = senders[0][1]
    transport = _mailer.transport
    text = f"Your Scribe Beta verification code is: {code}\n\nEnter it in the sign-in window. If you did not request this code, ignore this email."
    message = Email(
        to=recipient,
        from_address=sender,
        subject="Your Scribe Beta verification code",
        text=text,
        html=f"<p>Your Scribe Beta verification code:</p><p><strong>{code}</strong></p><p>Enter it in the sign-in window. If you did not request this code, ignore this email.</p>",
    )
    try:
        # Mailer.send wraps transport failures and supports console/file sinks.
        # This dedicated seam uses only its configured SMTP transport, never a
        # sink/preprocessor, and never permits plaintext or unverified TLS.
        result = await asyncio.wait_for(
            aiosmtplib.send(
                message.build(),
                sender=sender,
                recipients=[recipient],
                hostname=transport._host,
                port=transport._port,
                username=transport._user,
                password=transport._password,
                use_tls=transport._use_tls,
                start_tls=not transport._use_tls,
                validate_certs=True,
                client_key=transport._key_file,
                client_cert=transport._cert_file,
                cert_bundle=transport._cert_bundle,
                timeout=8,
            ),
            timeout=8,
        )
    except (
        SMTPConnectError,
        SMTPConnectTimeoutError,
        SMTPAuthenticationError,
        SMTPRecipientsRefused,
        SMTPDataError,
        SMTPHeloError,
        SMTPSenderRefused,
    ):
        raise SmtpNotAccepted() from None
    except (Exception, asyncio.CancelledError):
        # No exception text/SMTP reply is propagated to logging or telemetry.
        raise SmtpOutcomeUnknown() from None
    # A normal return is insufficient if any recipient was refused. A malformed
    # result is also never treated as SMTP acceptance.
    if (
        not isinstance(result, tuple)
        or len(result) != 2
        or not isinstance(result[0], dict)
        or not isinstance(result[1], str)
    ):
        raise SmtpOutcomeUnknown()
    if result[0]:
        if set(result[0]) == {recipient}:
            raise SmtpNotAccepted()
        raise SmtpOutcomeUnknown()


def configure_mailer():
    SMTP_URL = config("SMTP_URL", cast=str)
    EMAIL_FROM_ADDRESS = config("EMAIL_FROM_ADDRESS", cast=str)

    global _mailer

    _mailer = Mailer(
        SMTP_URL,
        from_address=EMAIL_FROM_ADDRESS,
        preprocessors=[
            css_inliner,
            remove_html_comments,
        ],
    )


def render_email(
    template_path: str, context: dict, layout_path: str = "mail/layout.html"
) -> tuple[str, str]:
    """
    Renders an email template into HTML and plaintext versions
    Returns tuple of (html_content, plaintext_content)
    """

    # First render the markdown template with variables
    markdown_content = render_template(template_path, context)

    # Use the raw markdown as plaintext version
    plaintext_content = markdown_content

    # Convert markdown to HTML for rich email clients
    html_content = markdown2.markdown(markdown_content)

    # Now render the entire html layout, with the markdown => html content inserted
    html_content = render_template(layout_path, context | {"content": html_content})

    return html_content, plaintext_content


def mail(
    to: str,
    subject: str,
    template_path: str,
    context: dict = {},
    from_address: str | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
):
    assert _mailer

    if "subject" not in context:
        context["subject"] = subject

    html_content, plain_content = render_email(template_path, context)

    message = Email(
        to=to,
        from_address=from_address,
        subject=subject,
        cc=cc,
        bcc=bcc,
        text=plain_content,
        html=html_content,
    )

    # A Celery worker thread has no current event loop to borrow.
    # TODO https://github.com/alex-oleshkevich/mailers/issues/14
    return asyncio.run(_mailer.send(message))
