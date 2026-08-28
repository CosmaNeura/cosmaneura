"""Postgres serialization regression for concurrent clinician activations."""

import hashlib
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request, status

from app.routes import public_onboarding
from app.routes.public_onboarding import AcceptInvitationBody
from app.services.public_entitlements import (
    MAX_ACTIVE_CLINICIAN_SEATS,
    ActiveClinicianSeatLimitReached,
    active_clinician_seat_count,
    lock_practice_for_active_seat_mutation,
    require_active_clinician_seat_capacity,
)
from app.services.public_legal import CURRENT_LEGAL_DOCUMENTS

from activemodel.session_manager import get_session
from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember
from app.models.public_onboarding import PracticeInvitation, PublicLegalAcceptance


def test_practice_lock_serializes_competing_twentieth_seat_activations() -> None:
    domain = f"seat-race-{secrets.token_hex(4)}.example.com"
    practice = Practice(
        legal_name="Seat Race Practice",
        join_code=f"R{secrets.token_hex(4).upper()[:7]}",
        email_domain=domain,
        origin_channel="direct",
        is_solo=False,
    ).save()
    for ordinal in range(MAX_ACTIVE_CLINICIAN_SEATS - 1):
        doctor = Doctor(
            clerk_id=f"user_seat_race_existing_{secrets.token_hex(8)}",
            email=f"existing-{ordinal}-{secrets.token_hex(3)}@{domain}",
            biography="",
        ).save()
        PracticeMember(
            practice_id=practice.id,
            doctor_id=doctor.id,
            role="workforce",
        ).save()
    candidates = tuple(
        Doctor(
            clerk_id=f"user_seat_race_candidate_{secrets.token_hex(8)}",
            email=f"candidate-{ordinal}-{secrets.token_hex(3)}@{domain}",
            biography="",
        ).save()
        for ordinal in range(2)
    )

    first_has_lock = threading.Event()
    release_first = threading.Event()

    def activate(candidate: Doctor, *, pause_while_locked: bool) -> str:
        with get_session() as session:
            locked_practice = lock_practice_for_active_seat_mutation(
                session,
                practice.id,
            )
            if pause_while_locked:
                first_has_lock.set()
                assert release_first.wait(timeout=10)
            try:
                require_active_clinician_seat_capacity(session, locked_practice)
            except ActiveClinicianSeatLimitReached:
                return "rejected"
            session.add(
                PracticeMember(
                    practice_id=locked_practice.id,
                    doctor_id=candidate.id,
                    role="workforce",
                )
            )
            session.commit()
            return "activated"

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(activate, candidates[0], pause_while_locked=True)
        assert first_has_lock.wait(timeout=10)
        second = executor.submit(activate, candidates[1], pause_while_locked=False)
        try:
            try:
                second.result(timeout=0.2)
            except TimeoutError:
                pass
            else:  # pragma: no cover - proves the second transaction blocked
                raise AssertionError("competing activation bypassed the Practice lock")
        finally:
            release_first.set()
        results = {first.result(timeout=10), second.result(timeout=10)}

    assert results == {"activated", "rejected"}
    with get_session() as session:
        assert (
            active_clinician_seat_count(session, practice.id)
            == MAX_ACTIVE_CLINICIAN_SEATS
        )


def test_public_invitation_accept_serializes_competing_twentieth_seats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    domain = f"public-invite-race-{secrets.token_hex(4)}.example.com"
    now = datetime.now(timezone.utc)
    practice = Practice(
        legal_name="Public Invitation Seat Race Practice",
        join_code=f"I{secrets.token_hex(4).upper()[:7]}",
        email_domain=domain,
        origin_channel="direct",
        is_solo=False,
        public_onboarding_required=True,
        baa_accepted_at=now,
        baa_version="CN-BAA-CUST-CW v2.2",
        stripe_subscription_id=f"sub_invite_race_{secrets.token_hex(8)}",
    ).save()
    owner = Doctor(
        clerk_id=f"user_invite_race_owner_{secrets.token_hex(8)}",
        email=f"owner@{domain}",
        biography="",
    ).save()
    PracticeMember(
        practice_id=practice.id,
        doctor_id=owner.id,
        role="owner",
    ).save()
    for document in CURRENT_LEGAL_DOCUMENTS:
        PublicLegalAcceptance(
            practice_id=practice.id,
            document_kind=document.kind.value,
            document_version=document.version,
            content_sha256=document.sha256,
            signer_doctor_id=owner.id,
            signer_email=owner.email,
            signer_role="owner",
            signature_name="Synthetic Owner",
            signature_title="Physician / owner",
            authority_confirmed=True,
            signer_authority="Authorized practice owner",
            accepted_at_utc=now,
        ).save()
    for ordinal in range(MAX_ACTIVE_CLINICIAN_SEATS - 2):
        doctor = Doctor(
            clerk_id=f"user_invite_race_existing_{secrets.token_hex(8)}",
            email=f"existing-{ordinal}-{secrets.token_hex(3)}@{domain}",
            biography="",
        ).save()
        PracticeMember(
            practice_id=practice.id,
            doctor_id=doctor.id,
            role="workforce",
        ).save()

    candidates = tuple(
        Doctor(
            clerk_id=f"user_invite_race_candidate_{secrets.token_hex(8)}",
            email=f"candidate-{ordinal}-{secrets.token_hex(3)}@{domain}",
            biography="",
        ).save()
        for ordinal in range(2)
    )
    tokens = tuple(secrets.token_urlsafe(32) for _ in candidates)
    invitations = tuple(
        PracticeInvitation(
            practice_id=practice.id,
            invited_by_doctor_id=owner.id,
            token_digest=hashlib.sha256(token.encode()).hexdigest(),
            invitee_email=candidate.email or "",
            invitee_email_digest=public_onboarding._email_digest(
                practice.id,
                candidate.email or "",
            ),
            idempotency_key_digest=hashlib.sha256(f"race:{token}".encode()).hexdigest(),
            issued_at=now,
            expires_at=now + timedelta(days=7),
        ).save()
        for candidate, token in zip(candidates, tokens, strict=True)
    )
    verified_emails = {candidate.clerk_id: candidate.email for candidate in candidates}
    monkeypatch.setattr(
        public_onboarding,
        "verified_primary_email",
        lambda clerk_id: verified_emails.get(clerk_id),
    )

    first_has_lock = threading.Event()
    release_first = threading.Event()
    call_state = threading.local()
    real_lock = public_onboarding.lock_practice_for_active_seat_mutation

    def controlled_lock(session, practice_id):
        locked = real_lock(session, practice_id)
        if getattr(call_state, "pause_while_locked", False):
            first_has_lock.set()
            assert release_first.wait(timeout=10)
        return locked

    monkeypatch.setattr(
        public_onboarding,
        "lock_practice_for_active_seat_mutation",
        controlled_lock,
    )

    def accept(candidate: Doctor, token: str, *, pause_while_locked: bool) -> str:
        call_state.pause_while_locked = pause_while_locked
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/internal/v1/practice/invitations/accept",
                "headers": [],
                "state": {},
            }
        )
        request.state.doctor = candidate
        request.state.auth_state = SimpleNamespace(payload={"sub": candidate.clerk_id})
        request.state.view_as = None
        try:
            result = public_onboarding.accept_practice_invitation(
                request,
                AcceptInvitationBody(
                    token=token,
                    attestation_name="Concurrent Invitee",
                    checks=["workforce", "not-party", "direction", "phi"],
                ),
            )
        except HTTPException as exc:
            assert exc.status_code == status.HTTP_409_CONFLICT
            assert exc.detail == "A practice may have at most 20 active clinicians."
            return "rejected"
        assert result.seat_state == "active"
        return "activated"

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            accept,
            candidates[0],
            tokens[0],
            pause_while_locked=True,
        )
        assert first_has_lock.wait(timeout=10)
        second = executor.submit(
            accept,
            candidates[1],
            tokens[1],
            pause_while_locked=False,
        )
        try:
            try:
                second.result(timeout=0.2)
            except TimeoutError:
                pass
            else:  # pragma: no cover - proves the route took the Practice lock
                raise AssertionError(
                    "competing invitation acceptance bypassed the Practice lock"
                )
        finally:
            release_first.set()
        results = {first.result(timeout=10), second.result(timeout=10)}

    assert results == {"activated", "rejected"}
    assert (
        sum(PracticeMember.get(doctor_id=item.id) is not None for item in candidates)
        == 1
    )
    for invitation in invitations:
        invitation.refresh()
    assert sum(item.accepted_at is not None for item in invitations) == 1
    practice.refresh()
    assert practice.stripe_quantity_sync_required is True
    assert practice.stripe_quantity_sync_target == MAX_ACTIVE_CLINICIAN_SEATS
    assert practice.stripe_quantity_sync_version == 1
    with get_session() as session:
        assert (
            active_clinician_seat_count(session, practice.id)
            == MAX_ACTIVE_CLINICIAN_SEATS
        )
