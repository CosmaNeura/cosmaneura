import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import status
from fastapi.exceptions import HTTPException
from fastapi.testclient import TestClient
from typeid import TypeID

import app.jobs.practice_invitation_email as practice_invitation_email
from app.configuration.redis import get_redis
from app.routes import internal
from app.server import api_app
from app.services import practice_invitations
from app.services.public_entitlements import (
    MAX_ACTIVE_CLINICIAN_SEATS,
    active_clinician_seat_count,
)

from activemodel.session_manager import get_session
from app.models.baa import BAA_CURRENT_REFERENCE
from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember
from app.models.public_onboarding import PracticeInvitation

from tests.factories import save_practice_member
from tests.routes.utils import (
    FakeClerkUser,
    FakeEmailAddress,
    get_clerk_dev_user,
    provision_practice_in_force,
)

ORIGIN = "https://cosma.localhost"
CHECKS = ["workforce", "not-party", "direction", "phi"]
DECISION_2 = (
    "This account already belongs to another practice. "
    "Sign up with a different email to join a new one."
)
INACTIVE = "This invitation is no longer active. Ask the practice owner for a new one."
WRONG_ACCOUNT = (
    "This invitation was sent to a different email address. Sign in with that address."
)


@pytest.fixture(autouse=True)
def _verified_clerk_email_matches_bound_doctor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def current_email(clerk_id: str) -> str | None:
        doctor = Doctor.get(clerk_id=clerk_id)
        return (doctor.email or "").strip().lower() if doctor else None

    monkeypatch.setattr(internal, "verified_primary_email", current_email)


def _owner() -> Doctor:
    _, _, clerk_user = get_clerk_dev_user()
    doctor = Doctor.find_or_create_by(clerk_id=clerk_user.id)
    assert doctor.email
    return doctor


def _owner_practice() -> Practice:
    return provision_practice_in_force(_owner())


def _doctor(email: str | None = None) -> Doctor:
    nonce = secrets.token_hex(4)
    return Doctor(
        clerk_id=f"user_invitee_{nonce}",
        email=email or f"invitee-{nonce}@gmail.com",
        biography="",
    ).save()


def _as(client: TestClient, doctor: Doctor | None) -> None:
    user = (
        None
        if doctor is None
        else FakeClerkUser(
            id=doctor.clerk_id,
            email_addresses=[FakeEmailAddress(email_address=doctor.email or "")],
        )
    )
    client.override_user(user)  # type: ignore[attr-defined]


def _invite(client: TestClient, email: str, *, key: str | None = None):
    return client.post(
        api_app.url_path_for("create_practice_invitation"),
        json={"email": email},
        headers={"origin": ORIGIN, "Idempotency-Key": key or secrets.token_hex(8)},
    )


def _invited(client: TestClient, email: str) -> tuple[dict, str]:
    resp = _invite(client, email)
    assert resp.status_code == status.HTTP_201_CREATED, resp.text
    body = resp.json()
    return body, body["invite_url"].split("#t=", 1)[1]


def _accept(client: TestClient, token: str, **overrides):
    return client.post(
        api_app.url_path_for("accept_practice_invitation"),
        json={"token": token, "attestation_name": "Riley Clinician", "checks": CHECKS}
        | overrides,
    )


def _inspect(client: TestClient, token: str):
    return client.post(
        api_app.url_path_for("inspect_practice_invitation"), json={"token": token}
    )


def _roster(client: TestClient) -> dict:
    resp = client.get(api_app.url_path_for("practice_roster"))
    assert resp.status_code == status.HTTP_200_OK, resp.text
    return resp.json()


def _expire(invitation_id: str) -> None:
    """Back-date an invitation; the guard forbids touching expires_at."""

    invitation = PracticeInvitation.get(id=invitation_id)
    assert invitation
    trigger = "practice_invitation_guard_mutation"
    with get_session() as session:
        session.connection().exec_driver_sql(
            f"ALTER TABLE practice_invitation DISABLE TRIGGER {trigger}"
        )
        session.connection().exec_driver_sql(
            "UPDATE practice_invitation SET issued_at = issued_at - interval '8 days', "
            "expires_at = expires_at - interval '8 days' WHERE id = %s",
            (invitation.id.uuid,),
        )
        session.connection().exec_driver_sql(
            f"ALTER TABLE practice_invitation ENABLE TRIGGER {trigger}"
        )
        session.commit()


def _fill_seats(practice: Practice) -> None:
    for _ in range(MAX_ACTIVE_CLINICIAN_SEATS - 1):
        save_practice_member(
            PracticeMember(
                practice_id=practice.id,
                doctor_id=_doctor().id,
                role="workforce",
            )
        )


def test_owner_invites_by_email_and_gets_a_copyable_link(
    authenticated_client: TestClient,
):
    practice = _owner_practice()

    body, token = _invited(authenticated_client, "  Riley@Gmail.com ")

    assert body["invite_url"].startswith(f"{ORIGIN}/invite#t=")
    assert body["email_queued"] is False
    assert body["invitation"]["invitee_email"] == "riley@gmail.com"
    assert body["invitation"]["expired"] is False
    row = PracticeInvitation.get(id=body["invitation"]["invitation_id"])
    assert row and row.practice_id == practice.id
    assert row.token_digest == hashlib.sha256(token.encode()).hexdigest()
    assert token not in str(row.model_dump())
    with get_session() as session:
        assert active_clinician_seat_count(session, practice.id) == 1
    practice.refresh()
    assert practice.is_solo is True


def test_invite_replays_the_same_idempotency_key(authenticated_client: TestClient):
    _owner_practice()
    first = _invite(authenticated_client, "replay@gmail.com", key="same-key-123")
    again = _invite(authenticated_client, "replay@gmail.com", key="same-key-123")
    other = _invite(authenticated_client, "other@gmail.com", key="same-key-123")

    assert first.status_code == again.status_code == status.HTTP_201_CREATED
    assert again.json() == first.json()
    assert other.status_code == status.HTTP_409_CONFLICT


def test_invite_refuses_duplicates_self_and_members(authenticated_client: TestClient):
    practice = _owner_practice()
    member = _doctor()
    save_practice_member(
        PracticeMember(practice_id=practice.id, doctor_id=member.id, role="workforce")
    )
    _invited(authenticated_client, "pending@gmail.com")

    for email in ("pending@gmail.com", _owner().email, member.email):
        assert email
        assert _invite(authenticated_client, email).status_code == 409


def test_invite_needs_an_idempotency_key_and_a_known_origin(
    authenticated_client: TestClient,
):
    _owner_practice()
    missing_key = authenticated_client.post(
        api_app.url_path_for("create_practice_invitation"),
        json={"email": "a@gmail.com"},
        headers={"origin": ORIGIN},
    )
    bad_origin = authenticated_client.post(
        api_app.url_path_for("create_practice_invitation"),
        json={"email": "a@gmail.com"},
        headers={"origin": "https://evil.example", "Idempotency-Key": "k" * 12},
    )

    assert missing_key.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert bad_origin.status_code == status.HTTP_400_BAD_REQUEST
    assert PracticeInvitation.get(invitee_email="a@gmail.com") is None


def test_invitee_in_another_practice_can_be_invited(authenticated_client: TestClient):
    _owner_practice()
    elsewhere = _doctor()
    provision_practice_in_force(elsewhere)

    assert _invite(authenticated_client, elsewhere.email or "").status_code == 201


def test_invite_at_the_cap_asks_the_owner_to_contact_us(
    authenticated_client: TestClient,
):
    practice = _owner_practice()
    _fill_seats(practice)

    resp = _invite(authenticated_client, "full@gmail.com")

    assert resp.status_code == status.HTTP_409_CONFLICT
    assert resp.json() == {
        "detail": "To add more clinicians, contact us at info@cosmaneura.com."
    }
    assert "20" not in resp.text


def test_workforce_cannot_invite_or_manage_invitations(
    bare_authenticated_client: TestClient,
):
    doctor = _owner()
    provision_practice_in_force(doctor)
    member = PracticeMember.get(doctor_id=doctor.id)
    assert member
    member.role = "workforce"
    member.save()

    created = _invite(bare_authenticated_client, "x@gmail.com")
    revoked = bare_authenticated_client.post(
        api_app.url_path_for(
            "revoke_practice_invitation",
            invitation_id=str(TypeID("practice_invitation")),
        )
    )

    assert created.status_code == status.HTTP_403_FORBIDDEN
    assert revoked.status_code == status.HTTP_403_FORBIDDEN


def _revoke(client: TestClient, invitation_id: str):
    return client.post(
        api_app.url_path_for("revoke_practice_invitation", invitation_id=invitation_id)
    )


def test_revoke_pending_twice_and_refuse_accepted_or_foreign(
    authenticated_client: TestClient,
):
    _owner_practice()
    pending, _ = _invited(authenticated_client, "revoke@gmail.com")
    invitee = _doctor()
    used, used_token = _invited(authenticated_client, invitee.email or "")
    _as(authenticated_client, invitee)
    assert _accept(authenticated_client, used_token).status_code == 200
    foreign_owner = _doctor()
    provision_practice_in_force(foreign_owner)
    _as(authenticated_client, foreign_owner)
    foreign, _ = _invited(authenticated_client, "foreign@gmail.com")
    _as(authenticated_client, None)

    pending_id = pending["invitation"]["invitation_id"]
    assert _revoke(authenticated_client, pending_id).status_code == 204
    assert _revoke(authenticated_client, pending_id).status_code == 204
    accepted = _revoke(authenticated_client, used["invitation"]["invitation_id"])
    assert accepted.status_code == status.HTTP_409_CONFLICT
    other = _revoke(authenticated_client, foreign["invitation"]["invitation_id"])
    assert other.status_code == status.HTTP_404_NOT_FOUND


def _resend(client: TestClient, invitation_id: str):
    return client.post(
        api_app.url_path_for("resend_practice_invitation", invitation_id=invitation_id),
        headers={"origin": ORIGIN, "Idempotency-Key": secrets.token_hex(8)},
    )


def test_resend_keeps_a_live_link_and_replaces_an_expired_one(
    authenticated_client: TestClient,
):
    practice = _owner_practice()
    live, _ = _invited(authenticated_client, "live@gmail.com")
    stale, _ = _invited(authenticated_client, "stale@gmail.com")
    _expire(stale["invitation"]["invitation_id"])

    kept = _resend(authenticated_client, live["invitation"]["invitation_id"])
    replaced = _resend(authenticated_client, stale["invitation"]["invitation_id"])

    assert kept.status_code == replaced.status_code == status.HTTP_200_OK
    assert kept.json()["invite_url"] == live["invite_url"]
    new = replaced.json()
    assert new["invitation"]["invitation_id"] != stale["invitation"]["invitation_id"]
    assert new["invite_url"] != stale["invite_url"]
    assert new["invitation"]["expired"] is False
    rows = PracticeInvitation.where(PracticeInvitation.practice_id == practice.id)
    assert rows.count() == 3


def test_roster_lists_pending_and_expired_invitations_beside_seats(
    authenticated_client: TestClient,
):
    _owner_practice()
    live, _ = _invited(authenticated_client, "live@gmail.com")
    stale, _ = _invited(authenticated_client, "stale@gmail.com")
    _expire(stale["invitation"]["invitation_id"])
    gone, _ = _invited(authenticated_client, "gone@gmail.com")
    _revoke(authenticated_client, gone["invitation"]["invitation_id"])
    invitee = _doctor()
    _, token = _invited(authenticated_client, invitee.email or "")
    _as(authenticated_client, invitee)
    assert _accept(authenticated_client, token).status_code == 200
    _as(authenticated_client, None)

    roster = _roster(authenticated_client)

    assert roster["occupied_seats"] == 2
    assert [(i["invitee_email"], i["expired"]) for i in roster["invitations"]] == [
        ("stale@gmail.com", True),
        ("live@gmail.com", False),
    ]
    member_ids = {m["member_id"] for m in roster["members"]}
    joined = PracticeMember.get(doctor_id=invitee.id)
    assert joined and str(joined.id) in member_ids
    assert len(member_ids) == 2


def test_roster_hides_invitee_email_under_view_as(authenticated_client: TestClient):
    _owner_practice()
    _invited(authenticated_client, "hidden@gmail.com")
    request = SimpleNamespace(state=SimpleNamespace(doctor=_owner(), view_as=object()))

    roster = internal.practice_roster(request)  # type: ignore[arg-type]

    assert [i.invitee_email for i in roster.invitations] == [None]


def test_invitee_accepts_through_the_workforce_attestation(
    authenticated_client: TestClient,
):
    practice = _owner_practice()
    practice.stripe_subscription_id = f"sub_invite_{secrets.token_hex(6)}"
    practice.save()
    invitee = _doctor()
    _, token = _invited(authenticated_client, invitee.email or "")
    _as(authenticated_client, invitee)

    resp = _accept(authenticated_client, token)

    assert resp.status_code == status.HTTP_200_OK, resp.text
    assert resp.json()["role"] == "workforce"
    assert resp.json()["attested"] is True
    assert resp.json()["legal_name"] == practice.legal_name
    member = PracticeMember.get(doctor_id=invitee.id)
    assert member and member.attestation_name == "Riley Clinician"
    practice.refresh()
    assert practice.is_solo is False
    assert practice.stripe_quantity_sync_required is True
    assert practice.stripe_quantity_sync_target == 2
    notes = authenticated_client.get(api_app.url_path_for("note_list"))
    assert notes.status_code == status.HTTP_200_OK, notes.text


def test_accept_requires_every_check_and_a_typed_name(
    authenticated_client: TestClient,
):
    _owner_practice()
    invitee = _doctor()
    _, token = _invited(authenticated_client, invitee.email or "")
    _as(authenticated_client, invitee)

    missing = _accept(authenticated_client, token, checks=CHECKS[:3])
    short = _accept(authenticated_client, token, attestation_name="R")

    assert missing.status_code == status.HTTP_400_BAD_REQUEST
    assert short.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert PracticeMember.get(doctor_id=invitee.id) is None


def test_accept_refuses_wrong_expired_revoked_and_unknown(
    authenticated_client: TestClient,
):
    _owner_practice()
    invitee = _doctor()
    _, wrong_token = _invited(authenticated_client, "someone-else@gmail.com")
    expired, expired_token = _invited(authenticated_client, invitee.email or "")
    _expire(expired["invitation"]["invitation_id"])
    other = _doctor()
    revoked, revoked_token = _invited(authenticated_client, other.email or "")
    _revoke(authenticated_client, revoked["invitation"]["invitation_id"])

    _as(authenticated_client, invitee)
    wrong = _accept(authenticated_client, wrong_token)
    late = _accept(authenticated_client, expired_token)
    unknown = _accept(authenticated_client, "x" * 43)
    _as(authenticated_client, other)
    withdrawn = _accept(authenticated_client, revoked_token)

    assert (wrong.status_code, wrong.json()) == (403, {"detail": WRONG_ACCOUNT})
    assert (late.status_code, late.json()) == (409, {"detail": INACTIVE})
    assert (withdrawn.status_code, withdrawn.json()) == (409, {"detail": INACTIVE})
    assert unknown.status_code == status.HTTP_404_NOT_FOUND
    assert PracticeMember.get(doctor_id=invitee.id) is None
    assert PracticeMember.get(doctor_id=other.id) is None


def test_accept_replay_by_the_same_doctor_is_idempotent(
    authenticated_client: TestClient,
):
    practice = _owner_practice()
    invitee = _doctor()
    _, token = _invited(authenticated_client, invitee.email or "")
    _as(authenticated_client, invitee)

    first = _accept(authenticated_client, token)
    second = _accept(authenticated_client, token)

    assert first.status_code == second.status_code == status.HTTP_200_OK
    assert second.json() == first.json()
    with get_session() as session:
        assert active_clinician_seat_count(session, practice.id) == 2


def test_member_of_another_practice_is_told_to_sign_up_again(
    authenticated_client: TestClient,
):
    _owner_practice()
    elsewhere = _doctor()
    provision_practice_in_force(elsewhere)
    _, token = _invited(authenticated_client, elsewhere.email or "")
    _as(authenticated_client, elsewhere)

    resp = _accept(authenticated_client, token)

    assert (resp.status_code, resp.json()) == (409, {"detail": DECISION_2})


def test_accept_at_the_cap_uses_invitee_wording(authenticated_client: TestClient):
    practice = _owner_practice()
    invitee = _doctor()
    _, token = _invited(authenticated_client, invitee.email or "")
    _fill_seats(practice)
    _as(authenticated_client, invitee)

    resp = _accept(authenticated_client, token)

    assert resp.status_code == status.HTTP_409_CONFLICT
    assert resp.json() == {
        "detail": (
            "This practice can't add more clinicians right now. "
            "Ask the practice owner to contact Cosmaneura."
        )
    }


def test_accept_refuses_a_practice_whose_baa_lapsed(
    authenticated_client: TestClient,
):
    practice = _owner_practice()
    invitee = _doctor()
    _, token = _invited(authenticated_client, invitee.email or "")
    practice.baa_version = "CN-BAA-CUST-CW v0.1"
    practice.save()
    _as(authenticated_client, invitee)

    assert _accept(authenticated_client, token).status_code == 409
    assert _inspect(authenticated_client, token).json()["state"] == (
        "practice_not_ready"
    )


def test_a_wrong_account_is_not_told_the_practice_lost_its_baa(
    authenticated_client: TestClient,
):
    practice = _owner_practice()
    _, token = _invited(authenticated_client, _doctor().email or "")
    practice.baa_version = "CN-BAA-CUST-CW v0.1"
    practice.save()
    _as(authenticated_client, _doctor())

    refused = _accept(authenticated_client, token)

    assert refused.status_code == status.HTTP_403_FORBIDDEN
    assert refused.json() == {"detail": WRONG_ACCOUNT}
    assert _inspect(authenticated_client, token).json() == {
        "state": "wrong_account",
        "legal_name": None,
        "baa_accepted": False,
    }


def test_managed_addresses_can_neither_be_invited_nor_accept(
    authenticated_client: TestClient,
):
    _owner_practice()
    managed_domain = f"managed-{secrets.token_hex(4)}.example"
    Practice(
        legal_name="Managed Health System",
        join_code=f"M{secrets.token_hex(4).upper()[:7]}",
        email_domain=managed_domain,
        is_solo=False,
        negotiated_baa_required=True,
    ).save()
    invitee = _doctor(email=f"clinician@{managed_domain}")
    assert _invite(authenticated_client, invitee.email or "").status_code == 409
    _as(authenticated_client, invitee)
    refused = _accept(authenticated_client, "z" * 43)
    assert refused.status_code == status.HTTP_409_CONFLICT
    assert "organization-managed" in refused.json()["detail"]


def test_accept_refuses_a_borrowed_identity():
    invitee = _doctor()
    request = SimpleNamespace(
        state=SimpleNamespace(
            doctor=invitee,
            auth_state=SimpleNamespace(payload={"sub": "user_support_actor"}),
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        internal.accept_practice_invitation(
            request,  # type: ignore[arg-type]
            internal.AcceptPracticeInvitationBody(
                token="x" * 43, attestation_name="Riley", checks=CHECKS
            ),
        )

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


def test_inspect_reports_each_state(authenticated_client: TestClient):
    practice = _owner_practice()
    ready_doc, used_doc, late_doc, gone_doc = (_doctor() for _ in range(4))
    _, ready = _invited(authenticated_client, ready_doc.email or "")
    _, used = _invited(authenticated_client, used_doc.email or "")
    late_body, late = _invited(authenticated_client, late_doc.email or "")
    _expire(late_body["invitation"]["invitation_id"])
    gone_body, gone = _invited(authenticated_client, gone_doc.email or "")
    _revoke(authenticated_client, gone_body["invitation"]["invitation_id"])
    elsewhere = _doctor()
    provision_practice_in_force(elsewhere)
    _, elsewhere_token = _invited(authenticated_client, elsewhere.email or "")

    def state(doctor: Doctor, token: str) -> dict:
        _as(authenticated_client, doctor)
        resp = _inspect(authenticated_client, token)
        assert resp.status_code == status.HTTP_200_OK, resp.text
        return resp.json()

    ready_state = state(ready_doc, ready)
    assert ready_state == {
        "state": "ready",
        "legal_name": practice.legal_name,
        "baa_accepted": True,
    }
    assert state(ready_doc, used) == {
        "state": "wrong_account",
        "legal_name": None,
        "baa_accepted": False,
    }
    assert _accept(authenticated_client, used).status_code == 403
    _as(authenticated_client, used_doc)
    assert _accept(authenticated_client, used).status_code == 200
    assert state(used_doc, used)["state"] == "already_member"
    assert state(late_doc, late)["state"] == "expired"
    assert state(gone_doc, gone)["state"] == "revoked"
    assert state(elsewhere, elsewhere_token)["state"] == "member_elsewhere"
    assert _inspect(authenticated_client, "y" * 43).status_code == 404


def test_link_used_by_another_account_is_inactive_to_its_addressee():
    practice = Practice(
        legal_name="Used Link Practice",
        join_code=f"U{secrets.token_hex(4).upper()[:7]}",
        email_domain="gmail.com",
        is_solo=True,
        billing_exempt=True,
        baa_accepted_at=datetime.now(timezone.utc),
        baa_version=BAA_CURRENT_REFERENCE,
    ).save()
    owner = _doctor()
    invitee = _doctor()
    save_practice_member(
        PracticeMember(practice_id=practice.id, doctor_id=owner.id, role="owner")
    )
    now = datetime.now(timezone.utc)
    with get_session() as session:
        issued = practice_invitations.issue_invitation(
            session,
            owner_member=cast(PracticeMember, PracticeMember.get(doctor_id=owner.id)),
            email=invitee.email or "",
            idempotency_key="used-link-key",
            now=now,
        )
    # Mark it used by a different account holding the same address.
    other = _doctor()
    with get_session() as session:
        row = session.get(PracticeInvitation, issued.invitation.id)
        assert row
        row.accepted_at = now
        row.accepted_by_doctor_id = other.id
        session.add(row)
        session.commit()

    with get_session() as session:
        inspection = practice_invitations.inspect_invitation(
            session,
            token=issued.token,
            doctor=invitee,
            verified_email=(invitee.email or "").lower(),
            now=now + timedelta(minutes=1),
        )

    assert inspection.state == "used"
    with (
        get_session() as session,
        pytest.raises(practice_invitations.InvitationInactive),
    ):
        practice_invitations.accept_invitation(
            session,
            token=issued.token,
            doctor=invitee,
            verified_email=(invitee.email or "").lower(),
            attestation_name="Riley Clinician",
            reverify_email=lambda doctor: (doctor.email or "").lower(),
            now=now + timedelta(minutes=1),
        )


def test_gmail_solo_converts_on_acceptance_and_keeps_its_join_code_hidden(
    bare_authenticated_client: TestClient,
):
    owner = _owner()
    owner.email = f"solo-{secrets.token_hex(3)}@gmail.com"
    owner.save()
    practice = provision_practice_in_force(owner)
    assert practice.email_domain == "gmail.com"
    invitee = _doctor()
    _, token = _invited(bare_authenticated_client, invitee.email or "")
    _as(bare_authenticated_client, invitee)
    assert _accept(bare_authenticated_client, token).status_code == 200
    _as(bare_authenticated_client, None)

    status_body = bare_authenticated_client.get(
        api_app.url_path_for("practice_status")
    ).json()

    practice.refresh()
    assert practice.is_solo is False
    assert status_body["join_code"] is None


def test_work_domain_solo_shows_its_join_code_after_conversion(
    authenticated_client: TestClient,
):
    practice = _owner_practice()
    before = authenticated_client.get(api_app.url_path_for("practice_status")).json()
    invitee = _doctor()
    _, token = _invited(authenticated_client, invitee.email or "")
    _as(authenticated_client, invitee)
    assert _accept(authenticated_client, token).status_code == 200
    _as(authenticated_client, None)

    after = authenticated_client.get(api_app.url_path_for("practice_status")).json()

    assert before["join_code"] is None
    assert after["join_code"] == practice.join_code


def test_invitation_rate_limit_allows_twenty_per_practice_hour():
    practice_id = f"practice_{secrets.token_hex(8)}"
    key_id = hashlib.sha256(practice_id.encode("ascii")).hexdigest()
    key = f"practice-invitation-attempt:{key_id}"
    redis = get_redis()
    redis.delete(key)
    try:
        for _ in range(20):
            internal._consume_practice_invitation_attempt(practice_id)
        with pytest.raises(HTTPException) as exc_info:
            internal._consume_practice_invitation_attempt(practice_id)
        assert exc_info.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert 0 < cast(int, redis.ttl(key)) <= 60 * 60
    finally:
        redis.delete(key)


def test_email_is_queued_by_invitation_id_only_when_enabled(
    authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    _owner_practice()
    queued: list[tuple] = []
    monkeypatch.setattr(
        practice_invitation_email.perform_celery,
        "delay",
        lambda *args, **kwargs: queued.append((args, kwargs)),
    )

    off, _ = _invited(authenticated_client, "quiet@gmail.com")
    monkeypatch.setattr(practice_invitation_email, "email_enabled", lambda: True)
    on, token = _invited(authenticated_client, "loud@gmail.com")
    resent = _resend(authenticated_client, on["invitation"]["invitation_id"])

    assert off["email_queued"] is False
    assert on["email_queued"] is True
    assert resent.json()["email_queued"] is True
    assert queued == [((on["invitation"]["invitation_id"],), {})] * 2
    assert token not in repr(queued)


def test_a_broker_outage_still_returns_the_link(
    authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    _owner_practice()
    monkeypatch.setattr(practice_invitation_email, "email_enabled", lambda: True)

    def broker_down(*_args, **_kwargs):
        raise ConnectionError("broker unavailable")

    monkeypatch.setattr(practice_invitation_email.perform_celery, "delay", broker_down)

    body, token = _invited(authenticated_client, "outage@gmail.com")

    assert body["email_queued"] is False
    assert token
