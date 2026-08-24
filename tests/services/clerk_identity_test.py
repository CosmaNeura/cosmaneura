"""Clerk identity facts used for privileged enrollment fail closed."""

from types import SimpleNamespace

import pytest

from app.services import clerk_identity


def _clerk_with_primary(
    *,
    verification_status: object = ...,
    banned: object = False,
    locked: object = False,
):
    if verification_status is ...:
        verification = None
    else:
        verification = SimpleNamespace(status=verification_status)
    primary = SimpleNamespace(
        id="email_primary",
        email_address=" Clinician@Example.COM ",
        verification=verification,
    )
    return SimpleNamespace(
        users=SimpleNamespace(
            get=lambda *, user_id: SimpleNamespace(
                id=user_id,
                banned=banned,
                locked=locked,
                primary_email_address_id=primary.id,
                email_addresses=[primary],
            )
        ),
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


@pytest.mark.parametrize(
    ("banned", "locked"),
    [(True, False), (False, True), (None, False), (False, None)],
)
def test_verified_primary_email_rejects_ineligible_account_state(
    monkeypatch,
    banned,
    locked,
):
    monkeypatch.setattr(
        clerk_identity,
        "clerk",
        _clerk_with_primary(
            verification_status="verified",
            banned=banned,
            locked=locked,
        ),
    )

    assert clerk_identity.verified_primary_email("user_ineligible") is None


def test_verified_primary_email_requires_exact_requested_subject(monkeypatch):
    client = _clerk_with_primary(verification_status="verified")
    client.users.get = lambda *, user_id: SimpleNamespace(
        id="user_different",
        banned=False,
        locked=False,
        primary_email_address_id="email_primary",
        email_addresses=[
            SimpleNamespace(
                id="email_primary",
                email_address="clinician@example.com",
                verification=SimpleNamespace(status="verified"),
            )
        ],
    )
    monkeypatch.setattr(clerk_identity, "clerk", client)

    assert clerk_identity.verified_primary_email("user_requested") is None


def test_verified_primary_email_rejects_ambiguous_primary_records(monkeypatch):
    primary = SimpleNamespace(
        id="email_primary",
        email_address="clinician@example.com",
        verification=SimpleNamespace(status="verified"),
    )
    user = SimpleNamespace(
        id="user_ambiguous",
        banned=False,
        locked=False,
        primary_email_address_id=primary.id,
        email_addresses=[primary, primary],
    )
    monkeypatch.setattr(
        clerk_identity,
        "clerk",
        SimpleNamespace(users=SimpleNamespace(get=lambda *, user_id: user)),
    )

    assert clerk_identity.verified_primary_email("user_ambiguous") is None
