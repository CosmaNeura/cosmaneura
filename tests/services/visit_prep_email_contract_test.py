"""Synthetic closed-template boundary, not external email acceptance."""

from uuid import uuid4

import pytest

from app.services.visit_prep_command_contract import _new_access_token
from app.services.visit_prep_email_contract import (
    INVITATION_SUBJECT,
    VisitPrepEmailInvalid,
    VisitPrepInvitationEmail,
)


def invitation(**changes) -> VisitPrepInvitationEmail:
    return VisitPrepInvitationEmail(
        **{
            "recipient": "recipient@example.invalid",
            "patient_origin": "https://intake.test.invalid",
            "access_token": _new_access_token(),
            "operation_id": str(uuid4()),
            **changes,
        }
    )


def test_template_has_no_patient_answer_contact_or_clinical_context_fields():
    message = invitation()
    assert message.subject == INVITATION_SUBJECT
    assert message.access_token not in message.subject
    assert message.recipient not in message.body
    assert message.operation_id not in message.body
    assert message.body.count(message.access_token) == 1
    assert (
        f"https://intake.test.invalid/visit-prep#access_token={message.access_token}"
        in message.body
    )
    for requirement in (
        "five topics",
        "tell your story",
        "skip a topic",
        "review or correct",
        "wrong-recipient",
        "Do not reply",
        "not monitored in real time",
        "not for emergencies",
    ):
        assert requirement in message.body
    for unknown in ("answer", "body", "subject", "diagnosis", "cc", "bcc"):
        with pytest.raises(TypeError):
            invitation(**{unknown: "Synthetic private content"})
    for sensitive in (
        message.recipient,
        message.patient_origin,
        message.access_token,
        message.operation_id,
    ):
        assert sensitive not in repr(message)


@pytest.mark.parametrize(
    "recipient",
    [
        "",
        "a,b@example.invalid",
        "a@example.invalid,b@example.invalid",
        "Name <recipient@example.invalid>",
        "a\r\nBcc: b@example.invalid",
        "a..b@example.invalid",
        "a@b@example.invalid",
        "a@localhost",
        "a@-example.invalid",
        "\u2603@example.invalid",
        "a" * 65 + "@example.invalid",
        None,
        ["recipient@example.invalid"],
    ],
)
def test_recipients_are_one_bounded_header_safe_mailbox(recipient):
    with pytest.raises(VisitPrepEmailInvalid) as caught:
        invitation(recipient=recipient)
    assert str(caught.value) == "Visit Prep email is invalid"
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    "origin",
    [
        "http://intake.test.invalid",
        "https://intake.test.invalid/",
        "https://intake.test.invalid/redirect",
        "https://intake.test.invalid?access_token=secret",
        "https://intake.test.invalid#secret",
        "https://user:password@intake.test.invalid",
        "https://intake.test.invalid:443",
        "https://intake.test.invalid\n",
        "https://intake.test.invalid\x00",
        "https://127.0.0.1",
        "https://[::1]",
        "https://[",
        "https://localhost",
        "https://INTAKE.test.invalid",
        "https://intake.\u2603.invalid",
        "https://" + "a" * 256 + ".invalid",
        None,
    ],
)
def test_origin_cannot_be_an_insecure_or_injectable_redirect(origin):
    with pytest.raises(VisitPrepEmailInvalid) as caught:
        invitation(patient_origin=origin)
    assert str(caught.value) == "Visit Prep email is invalid"
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    "changes",
    [
        {"operation_id": "patient@example.invalid"},
        {"operation_id": "00000000-0000-0000-0000-000000000000"},
        {"operation_id": str(uuid4()).upper()},
        {"operation_id": None},
        {"access_token": "vp1_" + "A" * 42 + "B"},
        {"access_token": "vp1_" + "A" * 42},
        {"access_token": "secret"},
        {"access_token": None},
    ],
)
def test_only_random_operation_uuid_and_canonical_invitation_credential(changes):
    with pytest.raises(VisitPrepEmailInvalid) as caught:
        invitation(**changes)
    assert str(caught.value) == "Visit Prep email is invalid"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
