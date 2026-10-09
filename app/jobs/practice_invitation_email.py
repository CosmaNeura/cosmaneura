from datetime import datetime, timezone

from decouple import config

from app import log
from app.celery import celery_app
from app.configuration.emailer import mail
from app.routes.middleware import allowed_hosts
from app.services.practice_invitations import (
    invitation_expired,
    invitation_token,
    token_digest,
)

from app.models.practice import Practice, PracticeMember
from app.models.public_onboarding import PracticeInvitation

# Off until a deployment has a real mail provider; the owner always gets a
# copyable link either way.
PRACTICE_INVITATION_EMAIL_ENABLED = config(
    "PRACTICE_INVITATION_EMAIL_ENABLED", default=False, cast=bool
)


def email_enabled() -> bool:
    return PRACTICE_INVITATION_EMAIL_ENABLED


def _plain(value: str) -> str:
    # Owner-typed text is rendered through Markdown into HTML.
    return value.translate(str.maketrans("", "", "<>[]"))


def perform(invitation_id: str) -> None:
    """Send one pending invitation's link, re-derived here so it never rides the broker."""

    invitation = PracticeInvitation.get(id=invitation_id)
    now = datetime.now(timezone.utc)
    if (
        invitation is None
        or invitation.accepted_at is not None
        or invitation.revoked_at is not None
        or invitation_expired(invitation, now)
    ):
        log.info(
            "practice invitation email skipped",
            event_code="practice_invitation_email_not_pending",
        )
        return
    token = invitation_token(invitation.id)
    if token_digest(token) != invitation.token_digest:
        log.warning(
            "practice invitation email skipped",
            event_code="practice_invitation_email_token_mismatch",
        )
        return
    practice = Practice.get(id=invitation.practice_id)
    assert practice is not None
    inviter = PracticeMember.get(doctor_id=invitation.invited_by_doctor_id)
    owner_name = (
        (inviter.attestation_name if inviter else None)
        or practice.baa_signature_name
        or "The practice owner"
    )
    legal_name = _plain(practice.legal_name)
    mail(
        to=invitation.invitee_email,
        subject=f"You're invited to join {legal_name} on Cosmas",
        template_path="mail/practice_invitation.md",
        context={
            "owner_name": _plain(owner_name),
            "legal_name": legal_name,
            "invite_url": f"{allowed_hosts(True)[0]}/invite#t={token}",
            "expires_on": invitation.expires_at.strftime("%B %-d, %Y"),
        },
    )


perform_celery = celery_app.task(perform)


def queue(invitation_id: str) -> None:
    perform_celery.delay(invitation_id)
