"""Owner-issued, single-use invitations into a practice's workforce.

An invitation is free and reserves nothing: only acceptance creates a
membership, and with it a billed seat. Every function here takes the
practice seat lock before any other row, the same order as a join-code join.
"""

import base64
import hashlib
import hmac
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from typeid import TypeID

from app.routes.middleware import SESSION_SECRET_KEY
from app.services.managed_organization_access import (
    managed_organization_discovery_domain,
)
from app.services.organization_access import (
    OrganizationAccessError,
    doctor_has_managed_roster_assignment,
    lock_roster_enrollment_email,
    normalize_email,
)
from app.services.public_entitlements import (
    lock_practice_for_active_seat_mutation,
    record_active_clinician_seat_quantity,
    require_active_clinician_seat_capacity,
)

from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember, PracticeRole
from app.models.public_onboarding import PracticeInvitation
from sqlalchemy import func
from sqlmodel import Session, col, select

ACCOUNT_IN_ANOTHER_PRACTICE = (
    "This account already belongs to another practice. "
    "Sign up with a different email to join a new one."
)

INVITATION_TTL = timedelta(days=7)
OWNER_REVOCATION_REASON = "Revoked by the practice owner."
REPLACEMENT_REVOCATION_REASON = "Replaced by a new invitation."


class PracticeInvitationError(Exception):
    """A refused invitation action; the message is safe to show the caller."""


class InvitationNotFound(PracticeInvitationError):
    def __init__(self) -> None:
        super().__init__("Invitation not found.")


class InvitationConflict(PracticeInvitationError):
    pass


class InvalidInvitationEmail(PracticeInvitationError):
    def __init__(self) -> None:
        super().__init__("Enter a valid email address.")


class InvitationWrongAccount(PracticeInvitationError):
    def __init__(self) -> None:
        super().__init__(
            "This invitation was sent to a different email address. "
            "Sign in with that address."
        )


class InvitationInactive(PracticeInvitationError):
    def __init__(self) -> None:
        super().__init__(
            "This invitation is no longer active. Ask the practice owner for a new one."
        )


class AccountInAnotherPractice(PracticeInvitationError):
    def __init__(self) -> None:
        super().__init__(ACCOUNT_IN_ANOTHER_PRACTICE)


def _managed_practice_conflict() -> InvitationConflict:
    return InvitationConflict(
        "This practice uses organization-managed enrollment. Use that enrollment path."
    )


@dataclass(frozen=True)
class IssuedInvitation:
    invitation: PracticeInvitation
    token: str
    "Bearer material; returned to the owner and never persisted."
    created: bool
    "False when an existing invitation was replayed or resent unchanged."


InspectionState = Literal[
    "ready",
    "expired",
    "revoked",
    "used",
    "wrong_account",
    "member_elsewhere",
    "already_member",
    "practice_not_ready",
]


@dataclass(frozen=True)
class InvitationInspection:
    state: InspectionState
    legal_name: str | None
    "Disclosed only to the account the invitation was addressed to."
    baa_accepted: bool


def invitation_token(invitation_id: Any) -> str:
    # Derived rather than stored so a replay or resend can return the same
    # link; rotating the secret orphans outstanding links, and resend then
    # replaces them.
    digest = hmac.new(
        str(SESSION_SECRET_KEY).encode("utf-8"),
        f"practice-invitation-token-v1:{invitation_id}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _email_digest(email: str) -> str:
    return hashlib.sha256(
        f"practice-invitation-email-v1:{email}".encode("utf-8")
    ).hexdigest()


def _idempotency_digest(idempotency_key: str) -> str:
    return hashlib.sha256(
        f"practice-invitation-idempotency-v1:{idempotency_key}".encode("utf-8")
    ).hexdigest()


def invitation_expired(invitation: PracticeInvitation, now: datetime) -> bool:
    return invitation.expires_at <= now


def _is_pending(invitation: PracticeInvitation) -> bool:
    return invitation.accepted_at is None and invitation.revoked_at is None


def _token_for(invitation: PracticeInvitation) -> str | None:
    token = invitation_token(invitation.id)
    if not hmac.compare_digest(token_digest(token), invitation.token_digest):
        return None
    return token


def _lock_owner_practice(
    session: Session, owner_member: PracticeMember, *, issuing: bool = True
) -> Practice:
    practice = lock_practice_for_active_seat_mutation(session, owner_member.practice_id)
    owner = session.exec(
        select(PracticeMember).where(PracticeMember.id == owner_member.id)
    ).one_or_none()
    if (
        owner is None
        or owner.practice_id != practice.id
        or owner.role != PracticeRole.owner.value
    ):
        raise InvitationNotFound
    if issuing and (
        practice.organization_id is not None or practice.negotiated_baa_required
    ):
        raise _managed_practice_conflict()
    return practice


def _lock_practice_invitation(
    session: Session, practice: Practice, invitation_id: Any
) -> PracticeInvitation:
    invitation = session.exec(
        select(PracticeInvitation)
        .where(PracticeInvitation.id == invitation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if invitation is None or invitation.practice_id != practice.id:
        raise InvitationNotFound
    return invitation


def _issue_locked(
    session: Session,
    *,
    practice: Practice,
    owner_member: PracticeMember,
    email: str,
    idempotency_key: str,
    now: datetime,
) -> IssuedInvitation:
    if managed_organization_discovery_domain(email) is not None:
        raise InvitationConflict(
            "This email address uses organization-managed access and cannot be invited."
        )
    owner_doctor = session.get(Doctor, owner_member.doctor_id)
    if owner_doctor is not None and hmac.compare_digest(
        (owner_doctor.email or "").strip().lower(), email
    ):
        raise InvitationConflict("You are already a member of this practice.")
    # Membership elsewhere is deliberately not checked: it would disclose an
    # invitee's employer to this owner. Acceptance refuses it instead.
    existing_member = session.exec(
        select(col(PracticeMember.id))
        .join(Doctor, col(Doctor.id) == col(PracticeMember.doctor_id))
        .where(
            PracticeMember.practice_id == practice.id,
            func.lower(func.btrim(col(Doctor.email))) == email,
        )
    ).first()
    if existing_member is not None:
        raise InvitationConflict("This clinician is already a member of the practice.")

    email_digest = _email_digest(email)
    key_digest = _idempotency_digest(idempotency_key)
    replayed = session.exec(
        select(PracticeInvitation).where(
            PracticeInvitation.practice_id == practice.id,
            PracticeInvitation.idempotency_key_digest == key_digest,
        )
    ).one_or_none()
    if replayed is not None:
        if not hmac.compare_digest(replayed.invitee_email_digest, email_digest):
            raise InvitationConflict(
                "This request was already used for a different invitation."
            )
        token = _token_for(replayed)
        if not _is_pending(replayed) or token is None:
            raise InvitationInactive
        return IssuedInvitation(invitation=replayed, token=token, created=False)

    pending = session.exec(
        select(col(PracticeInvitation.id)).where(
            PracticeInvitation.practice_id == practice.id,
            PracticeInvitation.invitee_email_digest == email_digest,
            col(PracticeInvitation.accepted_at).is_(None),
            col(PracticeInvitation.revoked_at).is_(None),
            PracticeInvitation.expires_at > now,
        )
    ).first()
    if pending is not None:
        raise InvitationConflict(
            "An invitation to this address is already pending. Resend it instead."
        )
    # Advisory only: the seat is claimed, under this same lock, at acceptance.
    require_active_clinician_seat_capacity(session, practice)

    # The token binds the id, and the database forbids changing token_digest
    # later, so the id is chosen here rather than at insert.
    invitation_id = TypeID("practice_invitation")
    token = invitation_token(invitation_id)
    invitation = PracticeInvitation.model_validate(
        {
            "id": invitation_id,
            "practice_id": practice.id,
            "invited_by_doctor_id": owner_member.doctor_id,
            "token_digest": token_digest(token),
            "invitee_email": email,
            "invitee_email_digest": email_digest,
            "idempotency_key_digest": key_digest,
            "issued_at": now,
            "expires_at": now + INVITATION_TTL,
        }
    )
    session.add(invitation)
    return IssuedInvitation(invitation=invitation, token=token, created=True)


def _commit(session: Session, issued: IssuedInvitation) -> IssuedInvitation:
    session.commit()
    session.refresh(issued.invitation)
    return issued


def issue_invitation(
    session: Session,
    *,
    owner_member: PracticeMember,
    email: str,
    idempotency_key: str,
    now: datetime,
) -> IssuedInvitation:
    practice = _lock_owner_practice(session, owner_member)
    try:
        normalized = normalize_email(email)
    except OrganizationAccessError:
        raise InvalidInvitationEmail from None
    return _commit(
        session,
        _issue_locked(
            session,
            practice=practice,
            owner_member=owner_member,
            email=normalized,
            idempotency_key=idempotency_key,
            now=now,
        ),
    )


def revoke_invitation(
    session: Session,
    *,
    owner_member: PracticeMember,
    invitation_id: Any,
    now: datetime,
) -> PracticeInvitation:
    practice = _lock_owner_practice(session, owner_member, issuing=False)
    invitation = _lock_practice_invitation(session, practice, invitation_id)
    if invitation.accepted_at is not None:
        raise InvitationConflict("An accepted invitation cannot be revoked.")
    if invitation.revoked_at is None:
        invitation.revoked_at = now
        invitation.revoked_by_doctor_id = owner_member.doctor_id
        invitation.revocation_reason = OWNER_REVOCATION_REASON
        session.add(invitation)
        session.commit()
        session.refresh(invitation)
    return invitation


def resend_invitation(
    session: Session,
    *,
    owner_member: PracticeMember,
    invitation_id: Any,
    idempotency_key: str,
    now: datetime,
) -> IssuedInvitation:
    practice = _lock_owner_practice(session, owner_member)
    invitation = _lock_practice_invitation(session, practice, invitation_id)
    replacement = session.exec(
        select(PracticeInvitation).where(
            PracticeInvitation.practice_id == practice.id,
            PracticeInvitation.idempotency_key_digest
            == _idempotency_digest(idempotency_key),
        )
    ).one_or_none()
    if replacement is not None and replacement.id != invitation.id:
        # A retried resend whose first attempt already replaced this row.
        return _commit(
            session,
            _issue_locked(
                session,
                practice=practice,
                owner_member=owner_member,
                email=invitation.invitee_email,
                idempotency_key=idempotency_key,
                now=now,
            ),
        )
    if invitation.accepted_at is not None:
        raise InvitationConflict("This invitation was already accepted.")
    if invitation.revoked_at is not None:
        raise InvitationInactive
    token = _token_for(invitation)
    if token is not None and not invitation_expired(invitation, now):
        return _commit(
            session,
            IssuedInvitation(invitation=invitation, token=token, created=False),
        )

    invitation.revoked_at = now
    invitation.revoked_by_doctor_id = owner_member.doctor_id
    invitation.revocation_reason = REPLACEMENT_REVOCATION_REASON
    session.add(invitation)
    session.flush()
    return _commit(
        session,
        _issue_locked(
            session,
            practice=practice,
            owner_member=owner_member,
            email=invitation.invitee_email,
            idempotency_key=idempotency_key,
            now=now,
        ),
    )


def _invitation_by_token(session: Session, token: str) -> PracticeInvitation:
    invitation = session.exec(
        select(PracticeInvitation).where(
            PracticeInvitation.token_digest == token_digest(token)
        )
    ).one_or_none()
    if invitation is None:
        raise InvitationNotFound
    return invitation


def _practice_ready(practice: Practice) -> bool:
    return (
        practice.organization_id is None
        and not practice.negotiated_baa_required
        and practice.baa_in_force()
    )


def inspect_invitation(
    session: Session,
    *,
    token: str,
    doctor: Doctor,
    verified_email: str,
    now: datetime,
) -> InvitationInspection:
    invitation = _invitation_by_token(session, token)
    if not hmac.compare_digest(
        invitation.invitee_email_digest, _email_digest(verified_email)
    ):
        return InvitationInspection(
            state="wrong_account", legal_name=None, baa_accepted=False
        )
    practice = session.get(Practice, invitation.practice_id)
    assert practice is not None
    member = session.exec(
        select(PracticeMember).where(PracticeMember.doctor_id == doctor.id)
    ).one_or_none()

    state: InspectionState
    if member is not None and member.practice_id != practice.id:
        state = "member_elsewhere"
    elif member is not None:
        state = "already_member"
    elif invitation.accepted_at is not None:
        state = "used"
    elif invitation.revoked_at is not None:
        state = "revoked"
    elif invitation_expired(invitation, now):
        state = "expired"
    elif not _practice_ready(practice):
        state = "practice_not_ready"
    else:
        state = "ready"
    return InvitationInspection(
        state=state,
        legal_name=practice.legal_name,
        baa_accepted=practice.baa_in_force(),
    )


def accept_invitation(
    session: Session,
    *,
    token: str,
    doctor: Doctor,
    verified_email: str,
    attestation_name: str,
    reverify_email: Callable[[Doctor], str],
    now: datetime,
) -> tuple[Practice, PracticeMember]:
    """Admit the invited clinician under their own workforce attestation.

    ``reverify_email`` re-reads the live identity provider while the rows
    below are locked, so a stale email cannot accept during an identity change.
    """

    candidate = _invitation_by_token(session, token)
    practice = lock_practice_for_active_seat_mutation(session, candidate.practice_id)
    lock_roster_enrollment_email(session, verified_email)
    invitation = _lock_practice_invitation(session, practice, candidate.id)
    locked_doctor = session.exec(
        select(Doctor).where(Doctor.id == doctor.id).with_for_update()
    ).one()
    if (locked_doctor.email or "").strip().lower() != verified_email:
        raise InvitationConflict(
            "The account email changed during enrollment. Please retry."
        )
    if reverify_email(locked_doctor) != verified_email:
        raise InvitationConflict(
            "The account email changed during enrollment. Please retry."
        )
    if doctor_has_managed_roster_assignment(locked_doctor, session=session):
        raise InvitationConflict(
            "This account has organization-managed access. Use that enrollment path."
        )
    member = session.exec(
        select(PracticeMember)
        .where(PracticeMember.doctor_id == locked_doctor.id)
        .with_for_update()
    ).one_or_none()

    if practice.organization_id is not None or practice.negotiated_baa_required:
        raise _managed_practice_conflict()
    if not practice.baa_in_force():
        raise InvitationConflict(
            "This practice has not accepted the Cosmaneura Inc BAA yet."
        )
    if not hmac.compare_digest(
        invitation.invitee_email_digest, _email_digest(verified_email)
    ):
        raise InvitationWrongAccount
    if member is not None and member.practice_id != practice.id:
        raise AccountInAnotherPractice
    if member is not None:
        if invitation.accepted_by_doctor_id == locked_doctor.id:
            # A replay returns current state; it never revives a seat the
            # owner has since changed.
            session.commit()
            session.refresh(member)
            return practice, member
        raise InvitationConflict("This account is already a member of the practice.")
    if not _is_pending(invitation) or invitation_expired(invitation, now):
        raise InvitationInactive
    require_active_clinician_seat_capacity(session, practice)

    member = PracticeMember(
        practice_id=practice.id,
        doctor_id=locked_doctor.id,
        role=PracticeRole.workforce.value,
        attested_at=now,
        attestation_name=attestation_name.strip(),
    )
    session.add(member)
    invitation.accepted_at = now
    invitation.accepted_by_doctor_id = locked_doctor.id
    session.add(invitation)
    if practice.is_solo:
        practice.is_solo = False
        session.add(practice)
    record_active_clinician_seat_quantity(session, practice)
    session.commit()
    session.refresh(practice)
    session.refresh(member)
    return practice, member


def pending_invitations(
    session: Session, practice_id: Any, now: datetime
) -> list[tuple[PracticeInvitation, bool]]:
    """Unanswered invitations, oldest first, each with its expired flag."""

    rows = session.exec(
        select(PracticeInvitation)
        .where(
            PracticeInvitation.practice_id == practice_id,
            col(PracticeInvitation.accepted_at).is_(None),
            col(PracticeInvitation.revoked_at).is_(None),
        )
        .order_by(col(PracticeInvitation.issued_at), col(PracticeInvitation.id))
    ).all()
    return [(row, invitation_expired(row, now)) for row in rows]
