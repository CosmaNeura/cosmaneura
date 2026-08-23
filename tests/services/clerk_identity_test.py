"""Clerk identity facts used for privileged enrollment fail closed."""

from types import SimpleNamespace

import pytest

from app.services import clerk_identity


def _clerk_with_primary(*, verification_status: object = ...):
    if verification_status is ...:
        verification = None
    else:
        verification = SimpleNamespace(status=verification_status)
    primary = SimpleNamespace(
        id="email_primary",
        email_address=" Clinician@Example.COM ",
        verification=verification,
    )
    user = SimpleNamespace(
        primary_email_address_id=primary.id,
        email_addresses=[primary],
    )
    return SimpleNamespace(
        users=SimpleNamespace(get=lambda *, user_id: user),
    )


def test_verified_primary_email_requires_explicit_verified_status(monkeypatch):
    monkeypatch.setattr(
        clerk_identity,
        "clerk",
        _clerk_with_primary(verification_status="verified"),
    )

    assert clerk_identity.verified_primary_email("user_verified") == (
        "clinician@example.com"
    )


@pytest.mark.parametrize("verification_status", [None, "unverified", "pending"])
def test_verified_primary_email_rejects_non_verified_status(
    monkeypatch,
    verification_status,
):
    monkeypatch.setattr(
        clerk_identity,
        "clerk",
        _clerk_with_primary(verification_status=verification_status),
    )

    assert clerk_identity.verified_primary_email("user_not_verified") is None


def test_verified_primary_email_rejects_missing_verification(monkeypatch):
    monkeypatch.setattr(clerk_identity, "clerk", _clerk_with_primary())

    assert clerk_identity.verified_primary_email("user_missing_verification") is None
