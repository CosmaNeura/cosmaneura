"""Actual Core mailer/SMTP boundary with invented messages and a fake SDK."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage
from uuid import uuid4

import aiosmtplib
import pytest
from mailers import Mailer
from mailers.transports import InMemoryTransport, NullTransport
from mailers.transports.smtp import SMTPTransport

from app.configuration import emailer
from app.configuration.visit_prep_email import (
    SUBMISSION_TIMEOUT_SECONDS,
    CoreSMTPInvitationTransport,
    VisitPrepEmailSubmissionUncertain,
    VisitPrepEmailUnavailable,
)
from app.services.visit_prep_command_contract import _new_access_token
from app.services.visit_prep_email_contract import (
    VisitPrepEmailInvalid,
    VisitPrepInvitationEmail,
)


def _invitation() -> VisitPrepInvitationEmail:
    return VisitPrepInvitationEmail(
        recipient="recipient@example.invalid",
        patient_origin="https://intake.test.invalid",
        access_token=_new_access_token(),
        operation_id=str(uuid4()),
    )


def _smtp(**changes) -> SMTPTransport:
    return SMTPTransport(
        **{
            "host": "smtp.example.invalid",
            "use_tls": True,
            "validate_certs": True,
            **changes,
        }
    )


def _adapter() -> CoreSMTPInvitationTransport:
    return CoreSMTPInvitationTransport(
        transport=_smtp(), sender_address="care@example.invalid"
    )


def test_worker_thread_sends_one_plain_message_via_actual_mailer(monkeypatch):
    calls = []
    loops = []

    async def fake_smtp(message, **options):
        loops.append(asyncio.get_running_loop())
        calls.append((message, options))
        return {}, "Synthetic provider acceptance"

    monkeypatch.setattr(aiosmtplib, "send", fake_smtp)
    adapter = _adapter()
    invitation = _invitation()
    with ThreadPoolExecutor(max_workers=1) as worker:
        assert worker.submit(adapter, invitation).result(timeout=5) == "accepted"
    assert len(calls) == 1 and loops[0].is_closed()
    message, options = calls[0]
    assert isinstance(message, EmailMessage)
    assert str(message["To"]) == invitation.recipient
    assert str(message["From"]) == "care@example.invalid"
    assert str(message["Subject"]) == invitation.subject
    assert str(message["Message-ID"]) == (
        f"<{invitation.operation_id}@example.invalid>"
    )
    assert invitation.access_token not in str(message.items())
    assert message.get_content() == invitation.body + "\n"
    assert message.get_content_type() == "text/plain"
    assert not message.is_multipart()
    assert not any(message.get(header) for header in ("Cc", "Bcc", "Reply-To"))
    assert options["use_tls"] is True and options["validate_certs"] is True
    assert "http" not in str(message.items())
    assert "recipient@" not in repr(adapter)
    assert "care@" not in repr(adapter)


@pytest.mark.parametrize(
    "transport",
    [
        NullTransport(),
        InMemoryTransport(),
        _smtp(use_tls=False),
        _smtp(validate_certs=False),
    ],
)
def test_no_sink_or_unverified_plaintext_transport_can_claim_acceptance(transport):
    with pytest.raises(VisitPrepEmailUnavailable):
        CoreSMTPInvitationTransport(
            transport=transport, sender_address="care@example.invalid"
        )


def test_uses_existing_smtp_settings_without_mutating_the_general_mailer(monkeypatch):
    smtp = _smtp()
    general = Mailer(smtp, from_address="care@example.invalid")
    monkeypatch.setattr(emailer, "_mailer", general)
    adapter = CoreSMTPInvitationTransport.from_configured_mailer()
    assert adapter._mailer is not general
    assert adapter._mailer.transport is smtp
    assert general.preprocessors == []
    assert adapter._mailer.preprocessors == []


def test_absent_configured_mailer_is_bounded(monkeypatch):
    monkeypatch.setattr(emailer, "_mailer", None)
    with pytest.raises(VisitPrepEmailUnavailable):
        CoreSMTPInvitationTransport.from_configured_mailer()


@pytest.mark.parametrize("failure", [RuntimeError, TimeoutError, ValueError])
def test_any_provider_error_is_uncertain_sanitized_and_never_retried(
    monkeypatch, failure
):
    calls = []
    invitation = _invitation()

    async def fail(message, **_options):
        calls.append(message)
        raise failure(f"{invitation.recipient}: {invitation.access_token}")

    monkeypatch.setattr(aiosmtplib, "send", fail)
    with pytest.raises(VisitPrepEmailSubmissionUncertain) as caught:
        _adapter()(invitation)
    assert len(calls) == 1
    assert str(caught.value) == "Visit Prep email submission is uncertain"
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def test_total_timeout_is_bounded_and_uncertain(monkeypatch):
    timeout_values = []

    async def timeout(awaitable, *, timeout):
        timeout_values.append(timeout)
        awaitable.close()
        raise TimeoutError("Synthetic private timeout detail")

    monkeypatch.setattr(asyncio, "wait_for", timeout)
    with pytest.raises(VisitPrepEmailSubmissionUncertain):
        _adapter()(_invitation())
    assert timeout_values == [SUBMISSION_TIMEOUT_SECONDS]


def test_revalidates_tampered_inputs_before_provider_call(monkeypatch):
    calls = []

    async def send(message, **_options):
        calls.append(message)

    monkeypatch.setattr(aiosmtplib, "send", send)
    invitation = _invitation()
    object.__setattr__(invitation, "recipient", "a@example.invalid,b@example.invalid")
    with pytest.raises(VisitPrepEmailInvalid):
        _adapter()(invitation)
    with pytest.raises(VisitPrepEmailInvalid):
        _adapter()(object())
    assert not calls


def test_unexpected_event_loop_thread_fails_without_a_send_or_unawaited_coroutine(
    monkeypatch,
):
    calls = []

    async def send(message, **_options):
        calls.append(message)

    async def wrong_thread():
        with pytest.raises(VisitPrepEmailSubmissionUncertain):
            _adapter()(_invitation())

    monkeypatch.setattr(aiosmtplib, "send", send)
    asyncio.run(wrong_thread())
    assert not calls
