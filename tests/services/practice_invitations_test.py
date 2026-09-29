import secrets
from datetime import datetime, timedelta, timezone

import pytest

from app.routes.public_onboarding import _pending_invitation_count
from app.services import practice_invitations as invitations
from app.services.doctor_account_closure import (
    close_doctor_account,
    plan_doctor_account_closure,
)
from app.services.organization_access import configure_provider_organization
from app.services.public_entitlements import (
    MAX_ACTIVE_CLINICIAN_SEATS,
    ActiveClinicianSeatLimitReached,
    active_clinician_seat_count,
)

from activemodel.session_manager import get_session
from app.models.baa import BAA_CURRENT_REFERENCE
from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember, lock_practice_seats
from app.models.public_onboarding import PracticeInvitation
from sqlmodel import select

from tests.factories import save_practice_member


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _doctor(domain: str = "clinic-invite.example", label: str = "doc") -> Doctor:
    nonce = secrets.token_hex(4)
    return Doctor(
        clerk_id=f"user_invite_{label}_{nonce}",
        email=f"{label}-{nonce}@{domain}",
        biography="",
    ).save()


def _practice(*, is_solo: bool = True, **overrides) -> tuple[Practice, PracticeMember]:
    fields = {
        "legal_name": "Invite Practice, PLLC",
        "join_code": f"I{secrets.token_hex(4).upper()[:7]}",
        "email_domain": "clinic-invite.example",
        "origin_channel": "direct",
        "is_solo": is_solo,
        "baa_accepted_at": _now(),
        "baa_version": BAA_CURRENT_REFERENCE,
        "billing_exempt": True,
    } | overrides
    practice = Practice(**fields).save()
    owner = save_practice_member(
        PracticeMember(
            practice_id=practice.id,
            doctor_id=_doctor(label="owner").id,
            role="owner",
            attested_at=_now(),
            attestation_name="Pat Owner",
        )
    )
    return practice, owner


def _issue(
    owner: PracticeMember,
    email: str,
    *,
    key: str | None = None,
    now: datetime | None = None,
) -> invitations.IssuedInvitation:
    with get_session() as session:
        return invitations.issue_invitation(
            session,
            owner_member=owner,
            email=email,
            idempotency_key=key or secrets.token_hex(8),
            now=now or _now(),
        )


def _accept(
    token: str,
    doctor: Doctor,
    *,
    email: str | None = None,
    live_email: str | None = None,
) -> tuple[Practice, PracticeMember]:
    verified = email or (doctor.email or "").lower()
    with get_session() as session:
        return invitations.accept_invitation(
            session,
            token=token,
            doctor=doctor,
            verified_email=verified,
            attestation_name="  Riley Clinician ",
            reverify_email=lambda _doctor: live_email or verified,
            now=_now(),
        )


def _invitation_rows(practice: Practice) -> list[PracticeInvitation]:
    with get_session() as session:
        return list(
            session.exec(
                select(PracticeInvitation).where(
                    PracticeInvitation.practice_id == practice.id
                )
            ).all()
        )


def _fill_seats(practice: Practice, total: int) -> None:
    with get_session() as session:
        occupied = active_clinician_seat_count(session, practice.id)
    for _ in range(total - occupied):
        save_practice_member(
            PracticeMember(
                practice_id=practice.id,
                doctor_id=_doctor(label="seat").id,
                role="workforce",
            )
        )


@pytest.mark.parametrize("change", ["disabled", "inactive", "role", "attestation"])
def test_issue_rechecks_owner_loaded_before_authority_changed(change):
    practice, owner = _practice()
    with get_session() as session:
        cached_owner = session.get(PracticeMember, owner.id)
        cached_doctor = session.get(Doctor, owner.doctor_id)
        assert cached_owner is not None and cached_doctor is not None
        with get_session() as writer:
            assert lock_practice_seats(writer, practice.id) is not None
            current = writer.get(PracticeMember, owner.id)
            doctor = writer.get(Doctor, owner.doctor_id)
            assert current is not None and doctor is not None
            if change == "disabled":
                doctor.deleted_at = _now()
            elif change == "inactive":
                current.seat_state = "inactive"
            elif change == "role":
                current.role = "workforce"
            else:
                current.attested_at = None
            writer.add_all([current, doctor])
            writer.commit()
        assert cached_owner.role == "owner" and cached_doctor.deleted_at is None
        with pytest.raises(invitations.InvitationNotFound):
            invitations.issue_invitation(
                session,
                owner_member=cached_owner,
                email="stale-owner@synthetic.invalid",
                idempotency_key=secrets.token_hex(8),
                now=_now(),
            )
    assert not _invitation_rows(practice)


def test_issue_stores_only_digests_and_a_normalized_address():
    practice, owner = _practice()

    issued = _issue(owner, "  Riley.Clinician@Gmail.COM ")

    row = issued.invitation
    assert issued.created is True
    assert row.invitee_email == "riley.clinician@gmail.com"
    assert row.role == "workforce"
    assert row.expires_at - row.issued_at == timedelta(days=7)
    assert row.token_digest == invitations.token_digest(issued.token)
    assert issued.token == invitations.invitation_token(row.id)
    stored = " ".join(str(value) for value in row.model_dump().values())
    assert issued.token not in stored
    assert "riley.clinician@gmail.com" not in row.invitee_email_digest


def test_issue_reserves_no_seat_and_records_no_quantity_change():
    practice, owner = _practice(
        is_solo=False,
        stripe_subscription_id=f"sub_invite_{secrets.token_hex(6)}",
        stripe_quantity_sync_version=4,
    )

    _issue(owner, "new-clinician@clinic-invite.example")

    practice.refresh()
    assert practice.stripe_quantity_sync_required is False
    assert practice.stripe_quantity_sync_version == 4
    assert practice.is_solo is False
    with get_session() as session:
        assert active_clinician_seat_count(session, practice.id) == 1


def test_issue_replays_the_same_key_and_address():
    practice, owner = _practice()
    first = _issue(owner, "replay@clinic-invite.example", key="replay-key-1")

    second = _issue(owner, "REPLAY@clinic-invite.example", key="replay-key-1")

    assert second.created is False
    assert second.invitation.id == first.invitation.id
    assert second.token == first.token
    assert len(_invitation_rows(practice)) == 1


def test_issue_refuses_a_key_reused_for_another_address():
    practice, owner = _practice()
    _issue(owner, "first@clinic-invite.example", key="shared-key-1")

    with pytest.raises(invitations.InvitationConflict):
        _issue(owner, "second@clinic-invite.example", key="shared-key-1")

    assert len(_invitation_rows(practice)) == 1


def test_issue_refuses_self_existing_member_and_duplicate_pending():
    practice, owner = _practice(is_solo=False)
    owner_doctor = Doctor.get(id=owner.doctor_id)
    assert owner_doctor and owner_doctor.email
    member = _doctor(label="member")
    save_practice_member(
        PracticeMember(practice_id=practice.id, doctor_id=member.id, role="workforce")
    )
    _issue(owner, "pending@clinic-invite.example")

    for email in (
        owner_doctor.email.upper(),
        member.email,
        "pending@clinic-invite.example",
    ):
        with pytest.raises(invitations.InvitationConflict):
            _issue(owner, email)

    assert len(_invitation_rows(practice)) == 1


def test_issue_allows_a_new_invitation_once_the_old_one_expired():
    practice, owner = _practice()
    _issue(owner, "again@clinic-invite.example", now=_now() - timedelta(days=8))

    issued = _issue(owner, "again@clinic-invite.example")

    assert issued.created is True
    assert len(_invitation_rows(practice)) == 2


def test_issue_does_not_look_at_membership_in_other_practices():
    _, owner = _practice()
    _, other_owner = _practice(legal_name="Elsewhere Practice, PLLC")
    elsewhere = Doctor.get(id=other_owner.doctor_id)
    assert elsewhere and elsewhere.email

    issued = _issue(owner, elsewhere.email)

    assert issued.created is True


def test_issue_refuses_managed_practices_and_managed_addresses():
    managed_domain = f"managed-{secrets.token_hex(4)}.example"
    _, managed_owner = _practice(
        email_domain=managed_domain,
        negotiated_baa_required=True,
        baa_accepted_at=None,
        baa_version=None,
    )
    _, owner = _practice()

    with pytest.raises(invitations.InvitationConflict):
        _issue(managed_owner, "anyone@clinic-invite.example")
    with pytest.raises(invitations.InvitationConflict):
        _issue(owner, f"clinician@{managed_domain}")


def test_issue_rejects_an_invalid_address():
    _, owner = _practice()

    with pytest.raises(invitations.InvalidInvitationEmail):
        _issue(owner, "not-an-email")


def test_issue_at_the_seat_cap_is_refused():
    practice, owner = _practice(is_solo=False)
    _fill_seats(practice, MAX_ACTIVE_CLINICIAN_SEATS)

    with pytest.raises(ActiveClinicianSeatLimitReached):
        _issue(owner, "one-too-many@clinic-invite.example")

    assert _invitation_rows(practice) == []


def test_every_owner_action_rechecks_the_owner_role_in_the_database():
    practice, owner = _practice(is_solo=False)
    live = _issue(owner, "owner-only@clinic-invite.example")
    workforce = save_practice_member(
        PracticeMember(
            practice_id=practice.id,
            doctor_id=_doctor(label="workforce").id,
            role="workforce",
        )
    )
    # A caller holding a stale "owner" copy of what is now a workforce row.
    stale_owner = workforce.model_copy(update={"role": "owner"})

    with pytest.raises(invitations.InvitationNotFound):
        _issue(stale_owner, "not-by-workforce@clinic-invite.example")
    with pytest.raises(invitations.InvitationNotFound):
        _revoke(stale_owner, live.invitation.id)
    with pytest.raises(invitations.InvitationNotFound):
        _resend(stale_owner, live.invitation.id, "workforce-resend-1")

    rows = _invitation_rows(practice)
    assert [(row.id, row.revoked_at) for row in rows] == [(live.invitation.id, None)]


def _revoke(owner: PracticeMember, invitation_id) -> PracticeInvitation:
    with get_session() as session:
        return invitations.revoke_invitation(
            session, owner_member=owner, invitation_id=invitation_id, now=_now()
        )


def test_revoke_a_pending_invitation_and_repeat_is_a_no_op():
    _, owner = _practice()
    issued = _issue(owner, "revoke-me@clinic-invite.example")

    revoked = _revoke(owner, issued.invitation.id)
    again = _revoke(owner, issued.invitation.id)

    assert revoked.revoked_by_doctor_id == owner.doctor_id
    assert revoked.revocation_reason == "Revoked by the practice owner."
    assert again.revoked_at == revoked.revoked_at


def test_revoke_an_expired_invitation():
    _, owner = _practice()
    issued = _issue(
        owner, "expired@clinic-invite.example", now=_now() - timedelta(days=8)
    )

    assert _revoke(owner, issued.invitation.id).revoked_at is not None


def test_revoke_refuses_accepted_and_hides_other_practices():
    _, owner = _practice()
    _, other_owner = _practice(legal_name="Other Practice, PLLC")
    invitee = _doctor(domain="gmail.com", label="invitee")
    issued = _issue(owner, invitee.email)
    _accept(issued.token, invitee)
    foreign = _issue(other_owner, "foreign@clinic-invite.example")

    with pytest.raises(invitations.InvitationConflict):
        _revoke(owner, issued.invitation.id)
    with pytest.raises(invitations.InvitationNotFound):
        _revoke(owner, foreign.invitation.id)


def _resend(
    owner: PracticeMember, invitation_id, key: str, *, now: datetime | None = None
) -> invitations.IssuedInvitation:
    with get_session() as session:
        return invitations.resend_invitation(
            session,
            owner_member=owner,
            invitation_id=invitation_id,
            idempotency_key=key,
            now=now or _now(),
        )


def test_resend_of_a_live_invitation_keeps_its_link():
    practice, owner = _practice()
    issued = _issue(owner, "live@clinic-invite.example")

    resent = _resend(owner, issued.invitation.id, "resend-key-1")

    assert resent.created is False
    assert resent.invitation.id == issued.invitation.id
    assert resent.token == issued.token
    assert len(_invitation_rows(practice)) == 1


def test_resend_of_an_expired_invitation_replaces_it():
    practice, owner = _practice()
    old = _issue(owner, "stale@clinic-invite.example", now=_now() - timedelta(days=8))

    resent = _resend(owner, old.invitation.id, "resend-key-2")
    retried = _resend(owner, old.invitation.id, "resend-key-2")

    assert resent.created is True
    assert resent.invitation.id != old.invitation.id
    assert resent.token != old.token
    assert resent.invitation.invitee_email == "stale@clinic-invite.example"
    assert retried.invitation.id == resent.invitation.id
    assert retried.token == resent.token
    previous = PracticeInvitation.get(id=old.invitation.id)
    assert previous and previous.revocation_reason == "Replaced by a new invitation."
    assert len(_invitation_rows(practice)) == 2


def test_resending_an_expired_replacement_with_its_own_key_replaces_it_again():
    practice, owner = _practice()
    original = _issue(
        owner, "cycle@clinic-invite.example", now=_now() - timedelta(days=16)
    )
    first = _resend(
        owner, original.invitation.id, "cycle-key-1", now=_now() - timedelta(days=8)
    )
    retried = _resend(
        owner, original.invitation.id, "cycle-key-1", now=_now() - timedelta(days=8)
    )

    second = _resend(owner, first.invitation.id, "cycle-key-1")

    assert (first.created, first.replayed) == (True, False)
    assert (retried.created, retried.replayed) == (False, True)
    assert retried.invitation.id == first.invitation.id
    assert (second.created, second.replayed) == (True, False)
    assert second.invitation.id not in {original.invitation.id, first.invitation.id}
    assert len(_invitation_rows(practice)) == 3


def test_resend_refuses_a_revoked_invitation_that_has_not_expired():
    practice, owner = _practice()
    issued = _issue(owner, "revoked-live@clinic-invite.example")
    _revoke(owner, issued.invitation.id)

    with pytest.raises(invitations.InvitationInactive):
        _resend(owner, issued.invitation.id, "resend-revoked-1")

    assert len(_invitation_rows(practice)) == 1


def test_accept_adds_one_attested_seat_and_converts_a_solo_account():
    practice, owner = _practice(
        stripe_subscription_id=f"sub_invite_{secrets.token_hex(6)}",
        stripe_quantity_sync_version=2,
    )
    invitee = _doctor(domain="gmail.com", label="invitee")
    issued = _issue(owner, invitee.email)

    accepted_practice, member = _accept(issued.token, invitee)

    assert accepted_practice.id == practice.id
    assert member.role == "workforce"
    assert member.attestation_name == "Riley Clinician"
    assert member.attested_at is not None
    invitation = PracticeInvitation.get(id=issued.invitation.id)
    assert invitation and invitation.accepted_by_doctor_id == invitee.id
    practice.refresh()
    assert practice.is_solo is False
    assert practice.stripe_quantity_sync_required is True
    assert practice.stripe_quantity_sync_target == 2
    assert practice.stripe_quantity_sync_version == 3
    with get_session() as session:
        assert active_clinician_seat_count(session, practice.id) == 2


def test_accept_replay_by_the_same_doctor_returns_current_state():
    practice, owner = _practice()
    invitee = _doctor(label="replay")
    issued = _issue(owner, invitee.email)
    _, first = _accept(issued.token, invitee)
    practice.refresh()
    version = practice.stripe_quantity_sync_version

    _, second = _accept(issued.token, invitee)

    assert second.id == first.id
    practice.refresh()
    assert practice.stripe_quantity_sync_version == version


def test_accept_after_joining_by_code_is_refused_not_replayed():
    practice, owner = _practice(is_solo=False)
    invitee = _doctor(label="joined-by-code")
    issued = _issue(owner, invitee.email)
    save_practice_member(
        PracticeMember(
            practice_id=practice.id,
            doctor_id=invitee.id,
            role="workforce",
            attested_at=_now(),
            attestation_name="Joined By Code",
        )
    )

    with pytest.raises(invitations.InvitationConflict) as exc_info:
        _accept(issued.token, invitee)

    assert str(exc_info.value) == "This account is already a member of the practice."
    invitation = PracticeInvitation.get(id=issued.invitation.id)
    assert invitation and invitation.accepted_at is None


def test_accept_refuses_the_wrong_account():
    practice, owner = _practice()
    issued = _issue(owner, "intended@clinic-invite.example")
    stranger = _doctor(label="stranger")

    with pytest.raises(invitations.InvitationWrongAccount):
        _accept(issued.token, stranger)

    assert PracticeMember.get(doctor_id=stranger.id) is None


@pytest.mark.parametrize(
    "practice_state",
    [
        {"baa_accepted_at": None, "baa_version": None},
        {"baa_accepted_at": None, "baa_version": None, "negotiated_baa_required": True},
    ],
)
def test_the_wrong_account_learns_nothing_and_takes_no_lock(
    practice_state: dict, monkeypatch: pytest.MonkeyPatch
):
    _, owner = _practice(is_solo=False)
    issued = _issue(owner, "addressee@clinic-invite.example")
    practice = Practice.get(id=owner.practice_id)
    assert practice
    for field, value in practice_state.items():
        setattr(practice, field, value)
    practice.save()
    stranger = _doctor(label="stranger")

    def no_lock(*_args, **_kwargs):
        raise AssertionError("a wrong account must not take the seat lock")

    monkeypatch.setattr(invitations, "lock_practice_for_active_seat_mutation", no_lock)
    with pytest.raises(invitations.InvitationWrongAccount):
        _accept(issued.token, stranger)


def test_a_managed_account_is_told_so_by_inspect_and_refused_by_accept(
    monkeypatch: pytest.MonkeyPatch,
):
    _, owner = _practice()
    invitee = _doctor(label="rostered")
    issued = _issue(owner, invitee.email)
    monkeypatch.setattr(
        invitations,
        "doctor_has_managed_roster_assignment",
        lambda doctor, **_kwargs: doctor.id == invitee.id,
    )

    with get_session() as session:
        inspection = invitations.inspect_invitation(
            session,
            token=issued.token,
            doctor=invitee,
            verified_email=invitee.email.lower(),
            now=_now(),
        )
    assert inspection == invitations.InvitationInspection(
        state="managed_account", legal_name=None, baa_accepted=False
    )
    with pytest.raises(invitations.InvitationConflict) as exc_info:
        _accept(issued.token, invitee)
    assert "organization-managed access" in str(exc_info.value)
    assert PracticeMember.get(doctor_id=invitee.id) is None


def test_accept_refuses_expired_revoked_and_unknown_tokens():
    _, owner = _practice()
    late = _doctor(label="late")
    expired = _issue(owner, late.email, now=_now() - timedelta(days=8))
    revoked_invitee = _doctor(label="revoked")
    revoked = _issue(owner, revoked_invitee.email)
    _revoke(owner, revoked.invitation.id)

    with pytest.raises(invitations.InvitationInactive):
        _accept(expired.token, late)
    with pytest.raises(invitations.InvitationInactive):
        _accept(revoked.token, revoked_invitee)
    with pytest.raises(invitations.InvitationNotFound):
        _accept("not-a-real-token", late)


def test_accept_refuses_a_member_of_another_practice():
    _, owner = _practice()
    _, other_owner = _practice(legal_name="Current Employer, PLLC")
    elsewhere = Doctor.get(id=other_owner.doctor_id)
    assert elsewhere
    issued = _issue(owner, elsewhere.email)

    with pytest.raises(invitations.AccountInAnotherPractice) as exc_info:
        _accept(issued.token, elsewhere)

    assert str(exc_info.value) == (
        "This account already belongs to another practice. "
        "Sign up with a different email to join a new one."
    )


def test_accept_refuses_a_practice_without_a_baa():
    practice, owner = _practice()
    invitee = _doctor(label="nobaa")
    issued = _issue(owner, invitee.email)
    practice.baa_accepted_at = None
    practice.baa_version = None
    practice.save()

    with pytest.raises(invitations.InvitationConflict):
        _accept(issued.token, invitee)

    assert PracticeMember.get(doctor_id=invitee.id) is None


def test_pending_invitation_cannot_admit_after_sole_owner_closure():
    _, owner = _practice()
    invitee = _doctor(domain="synthetic.invalid", label="pending-after-closure")
    issued = _issue(owner, invitee.email)
    args = {
        "doctor_id": owner.doctor_id,
        "operator_ref": "test:pending-invite-owner-closure",
        "reason": "Invented sole-owner closure with a pending invitation",
    }
    plan = plan_doctor_account_closure(**args)
    assert close_doctor_account(**args, confirmed_plan_sha256=plan.plan_sha256).changed

    with pytest.raises(invitations.InvitationInactive):
        _accept(issued.token, invitee)

    assert PracticeMember.get(doctor_id=invitee.id) is None


def test_accepted_invitation_replay_preserves_member_after_owner_disabled():
    _, owner = _practice()
    invitee = _doctor(domain="synthetic.invalid", label="accepted-before-closure")
    issued = _issue(owner, invitee.email)
    _, original = _accept(issued.token, invitee)
    owner_doctor = Doctor.get(id=owner.doctor_id)
    assert owner_doctor is not None
    owner_doctor.deleted_at = _now()
    owner_doctor.save()

    _, replayed = _accept(issued.token, invitee)

    assert replayed.id == original.id
    assert replayed.seat_state == original.seat_state


@pytest.mark.parametrize("conversion", ["negotiated_baa_required", "organization"])
def test_accept_refuses_a_practice_converted_to_managed_after_issue(conversion: str):
    domain = f"converted-{secrets.token_hex(4)}.example"
    practice, owner = _practice(
        is_solo=False,
        email_domain=domain,
        baa_accepted_at=None,
        baa_version=None,
        negotiated_baa_on_file=True,
        negotiated_baa_effective_date=_now() - timedelta(days=1),
        negotiated_baa_reference=f"executed-baa:{domain}",
    )
    invitee = _doctor(domain="gmail.com", label="converted")
    issued = _issue(owner, invitee.email)
    if conversion == "organization":
        configure_provider_organization(
            practice,
            legal_name=f"{domain} Provider Organization",
            slug=f"converted-{secrets.token_hex(5)}",
            email_domain=domain,
            operator_ref="test-operator",
            reason="Convert after an invitation was issued",
        )
    else:
        practice.negotiated_baa_required = True
        practice.save()
    practice.refresh()
    assert practice.baa_in_force()

    with pytest.raises(invitations.InvitationConflict) as exc_info:
        _accept(issued.token, invitee)

    assert "organization-managed enrollment" in str(exc_info.value)
    assert PracticeMember.get(doctor_id=invitee.id) is None


def test_accept_at_the_seat_cap_is_refused():
    practice, owner = _practice(is_solo=False)
    invitee = _doctor(label="capped")
    issued = _issue(owner, invitee.email)
    _fill_seats(practice, MAX_ACTIVE_CLINICIAN_SEATS)

    with pytest.raises(ActiveClinicianSeatLimitReached):
        _accept(issued.token, invitee)

    assert PracticeMember.get(doctor_id=invitee.id) is None
    invitation = PracticeInvitation.get(id=issued.invitation.id)
    assert invitation and invitation.accepted_at is None


def test_accept_refuses_an_identity_that_changed_under_the_lock():
    _, owner = _practice()
    invitee = _doctor(label="rebind")
    issued = _issue(owner, invitee.email)

    with pytest.raises(invitations.InvitationConflict):
        _accept(issued.token, invitee, live_email="someone-else@example.com")

    assert PracticeMember.get(doctor_id=invitee.id) is None


def test_pending_invitations_exclude_answered_rows_and_flag_expiry():
    practice, owner = _practice()
    live = _issue(owner, "live@clinic-invite.example")
    stale = _issue(owner, "stale@clinic-invite.example", now=_now() - timedelta(days=8))
    _revoke(owner, _issue(owner, "gone@clinic-invite.example").invitation.id)
    accepted_invitee = _doctor(label="joined")
    _accept(_issue(owner, accepted_invitee.email).token, accepted_invitee)

    with get_session() as session:
        pending = invitations.pending_invitations(session, practice.id, _now())
        counted = _pending_invitation_count(session, practice.id, _now())

    assert [(row.id, expired) for row, expired in pending] == [
        (stale.invitation.id, True),
        (live.invitation.id, False),
    ]
    assert counted == 1
