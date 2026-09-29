import secrets
from datetime import datetime, timedelta, timezone

import pytest

import app.jobs.practice_invitation_email as job
from app.configuration.emailer import render_email
from app.services import practice_invitations

from activemodel.session_manager import get_session
from app.models.baa import BAA_CURRENT_REFERENCE
from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember
from app.models.public_onboarding import PracticeInvitation

from tests.factories import save_practice_member


def _issue(*, now: datetime | None = None) -> practice_invitations.IssuedInvitation:
    practice = Practice(
        legal_name="Harbor <Family> [Medicine]",
        join_code=f"E{secrets.token_hex(4).upper()[:7]}",
        email_domain="harbor-email.example",
        origin_channel="direct",
        is_solo=True,
        baa_accepted_at=datetime.now(timezone.utc),
        baa_version=BAA_CURRENT_REFERENCE,
        billing_exempt=True,
    ).save()
    owner_doctor = Doctor(
        clerk_id=f"user_email_owner_{secrets.token_hex(4)}",
        email=f"owner-{secrets.token_hex(3)}@harbor-email.example",
        biography="",
    ).save()
    owner = save_practice_member(
        PracticeMember(
            practice_id=practice.id,
            doctor_id=owner_doctor.id,
            role="owner",
            attestation_name="Dana Owner",
            attested_at=datetime.now(timezone.utc),
        )
    )
    with get_session() as session:
        return practice_invitations.issue_invitation(
            session,
            owner_member=owner,
            email="riley@gmail.com",
            idempotency_key=secrets.token_hex(8),
            now=now or datetime.now(timezone.utc),
        )


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []
    monkeypatch.setattr(job, "mail", lambda **kwargs: calls.append(kwargs))
    return calls


def test_email_carries_the_link_and_names_the_practice(sent: list[dict]):
    issued = _issue()

    job.perform(str(issued.invitation.id))

    assert len(sent) == 1
    call = sent[0]
    assert call["to"] == "riley@gmail.com"
    assert call["template_path"] == "mail/practice_invitation.md"
    assert call["context"]["invite_url"].endswith(f"/invite#t={issued.token}")
    html, text = render_email(
        call["template_path"], call["context"] | {"subject": call["subject"]}
    )
    for body in (html, text):
        assert "Harbor Family Medicine" in body
        assert "Dana Owner" in body
        assert issued.token in body
        assert "workforce attestation" in body
        assert "verif" not in body.lower()
    assert "<Family>" not in html


def test_email_is_skipped_unless_the_invitation_is_pending(sent: list[dict]):
    expired = _issue(now=datetime.now(timezone.utc) - timedelta(days=8))
    revoked = _issue()
    with get_session() as session:
        row = session.get(PracticeInvitation, revoked.invitation.id)
        assert row
        row.revoked_at = datetime.now(timezone.utc)
        row.revoked_by_doctor_id = row.invited_by_doctor_id
        row.revocation_reason = "Revoked by the practice owner."
        session.add(row)
        session.commit()

    job.perform(str(expired.invitation.id))
    job.perform(str(revoked.invitation.id))

    assert sent == []


def test_queue_puts_only_the_invitation_id_on_the_broker(
    monkeypatch: pytest.MonkeyPatch,
):
    issued = _issue()
    queued: list[tuple] = []
    monkeypatch.setattr(
        job.perform_celery,
        "delay",
        lambda *args, **kwargs: queued.append((args, kwargs)),
    )

    job.queue(str(issued.invitation.id))

    assert queued == [((str(issued.invitation.id),), {})]
    assert issued.token not in repr(queued)
