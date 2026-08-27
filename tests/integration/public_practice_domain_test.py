"""Postgres serialization regression for public practice creation."""

import secrets
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request, status

from app.routes import internal
from app.routes.internal import CreatePracticeBody

from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember


def test_same_domain_public_practice_creation_is_atomic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    domain = f"practice-race-{secrets.token_hex(4)}.example.com"
    doctors = tuple(
        Doctor(
            clerk_id=f"user_practice_race_{secrets.token_hex(8)}",
            email=f"clinician-{ordinal}@{domain}",
            biography="",
        ).save()
        for ordinal in range(2)
    )
    verified_emails = {doctor.clerk_id: doctor.email for doctor in doctors}
    monkeypatch.setattr(
        internal,
        "verified_primary_email",
        lambda clerk_id: verified_emails.get(clerk_id),
    )

    first_has_lock = threading.Event()
    release_first = threading.Event()
    call_state = threading.local()
    real_lock = internal.lock_provider_practice_domain

    def controlled_lock(session, email_domain):
        real_lock(session, email_domain)
        if getattr(call_state, "pause_while_locked", False):
            first_has_lock.set()
            assert release_first.wait(timeout=10)

    monkeypatch.setattr(internal, "lock_provider_practice_domain", controlled_lock)

    def create(doctor: Doctor, *, pause_while_locked: bool) -> str:
        call_state.pause_while_locked = pause_while_locked
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/internal/v1/practice",
                "headers": [],
                "state": {},
            }
        )
        request.state.doctor = doctor
        request.state.auth_state = SimpleNamespace(payload={"sub": doctor.clerk_id})
        request.state.view_as = None
        try:
            internal.create_practice(
                request,
                CreatePracticeBody(
                    legal_name="Concurrent Public Practice, PLLC",
                    is_solo=False,
                    channel="direct",
                    pastoral_pack=False,
                ),
            )
        except HTTPException as exc:
            assert exc.status_code == status.HTTP_409_CONFLICT
            assert "single-use invitation" in str(exc.detail)
            return "rejected"
        return "created"

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(create, doctors[0], pause_while_locked=True)
        assert first_has_lock.wait(timeout=10)
        second = executor.submit(create, doctors[1], pause_while_locked=False)
        try:
            try:
                second.result(timeout=0.2)
            except TimeoutError:
                pass
            else:  # pragma: no cover - proves the advisory lock serialized creation
                raise AssertionError("same-domain practice creation bypassed its lock")
        finally:
            release_first.set()
        outcomes = {first.result(timeout=10), second.result(timeout=10)}

    assert outcomes == {"created", "rejected"}
    assert Practice.where(Practice.email_domain == domain).count() == 1
    memberships = [PracticeMember.get(doctor_id=doctor.id) for doctor in doctors]
    assert sum(member is not None for member in memberships) == 1


def test_same_doctor_concurrent_practice_creation_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    domain = f"practice-retry-{secrets.token_hex(4)}.example.com"
    doctor = Doctor(
        clerk_id=f"user_practice_retry_{secrets.token_hex(8)}",
        email=f"clinician@{domain}",
        biography="",
    ).save()
    monkeypatch.setattr(
        internal,
        "verified_primary_email",
        lambda clerk_id: doctor.email if clerk_id == doctor.clerk_id else None,
    )

    first_has_lock = threading.Event()
    release_first = threading.Event()
    call_state = threading.local()
    real_lock = internal.lock_provider_practice_domain

    def controlled_lock(session, email_domain):
        real_lock(session, email_domain)
        if getattr(call_state, "pause_while_locked", False):
            first_has_lock.set()
            assert release_first.wait(timeout=10)

    monkeypatch.setattr(internal, "lock_provider_practice_domain", controlled_lock)

    def create(*, pause_while_locked: bool) -> str:
        call_state.pause_while_locked = pause_while_locked
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/internal/v1/practice",
                "headers": [],
                "state": {},
            }
        )
        request.state.doctor = doctor
        request.state.auth_state = SimpleNamespace(payload={"sub": doctor.clerk_id})
        request.state.view_as = None
        response = internal.create_practice(
            request,
            CreatePracticeBody(
                legal_name="Idempotent Public Practice, PLLC",
                is_solo=False,
                channel="direct",
                pastoral_pack=False,
            ),
        )
        assert response.legal_name == "Idempotent Public Practice, PLLC"
        member = PracticeMember.get(doctor_id=doctor.id)
        assert member is not None
        return str(member.practice_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(create, pause_while_locked=True)
        assert first_has_lock.wait(timeout=10)
        second = executor.submit(create, pause_while_locked=False)
        try:
            try:
                second.result(timeout=0.2)
            except TimeoutError:
                pass
            else:  # pragma: no cover - proves the advisory lock serialized retries
                raise AssertionError("same-account retry bypassed its domain lock")
        finally:
            release_first.set()
        practice_ids = {first.result(timeout=10), second.result(timeout=10)}

    assert len(practice_ids) == 1
    assert Practice.where(Practice.email_domain == domain).count() == 1
    member = PracticeMember.get(doctor_id=doctor.id)
    assert member is not None
    assert str(member.practice_id) in practice_ids
