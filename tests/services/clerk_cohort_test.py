from types import SimpleNamespace

import pytest

from app.services import clerk_cohort


def _user(
    clerk_id: str,
    email: str,
    *,
    verified: bool = True,
    banned: bool = False,
    locked: bool = False,
    primary_id: str | None = None,
    duplicate_primary: bool = False,
):
    address_id = primary_id or f"email_{clerk_id}"
    primary = SimpleNamespace(
        id=address_id,
        email_address=email,
        verification=SimpleNamespace(status="verified" if verified else "unverified"),
    )
    addresses = [primary]
    if duplicate_primary:
        addresses.append(
            SimpleNamespace(
                id=address_id,
                email_address=email,
                verification=SimpleNamespace(status="verified"),
            )
        )
    return SimpleNamespace(
        id=clerk_id,
        primary_email_address_id=address_id,
        email_addresses=addresses,
        banned=banned,
        locked=locked,
    )


class _Users:
    def __init__(self, users, *, failure: Exception | None = None):
        self.users = users
        self.failure = failure
        self.requests = []

    def list(self, *, request):
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        offset = request["offset"]
        limit = request["limit"]
        return self.users[offset : offset + limit]


def _install_users(monkeypatch, users, *, failure: Exception | None = None):
    fake_users = _Users(users, failure=failure)
    monkeypatch.setattr(clerk_cohort, "clerk", SimpleNamespace(users=fake_users))
    return fake_users


def test_resolver_paginates_normalizes_hashes_and_returns_minimal_records(monkeypatch):
    monkeypatch.setattr(clerk_cohort, "CLERK_USERS_PAGE_SIZE", 2)
    users = [
        _user("user_3", " Third@Example.com "),
        _user("user_1", "first@example.com"),
        _user("user_2", "SECOND@example.com"),
    ]
    fake_users = _install_users(monkeypatch, users)
    expected_sha256 = clerk_cohort.canonical_email_set_sha256(
        ["first@example.com", "second@example.com", "third@example.com"]
    )
    assert (
        expected_sha256
        == "6436bdacacc0b97a51a23b972f7c20afdcfc12e199bc971ab1bd0ad27e1f3453"
    )

    result = clerk_cohort.resolve_live_clerk_cohort(
        expected_count=3,
        expected_sha256=expected_sha256,
    )

    assert result == (
        clerk_cohort.ClerkCohortIdentity("user_1", "first@example.com"),
        clerk_cohort.ClerkCohortIdentity("user_2", "second@example.com"),
        clerk_cohort.ClerkCohortIdentity("user_3", "third@example.com"),
    )
    assert fake_users.requests == [
        {"limit": 2, "offset": 0, "order_by": "+created_at"},
        {"limit": 2, "offset": 2, "order_by": "+created_at"},
        {"limit": 2, "offset": 3, "order_by": "+created_at"},
    ]


@pytest.mark.parametrize(
    ("users", "message"),
    [
        ([_user("user_1", "one@example.com", banned=True)], "ineligible"),
        ([_user("user_1", "one@example.com", locked=True)], "ineligible"),
        ([_user("user_1", "one@example.com", verified=False)], "verified primary"),
        (
            [_user("user_1", "one@example.com", duplicate_primary=True)],
            "verified primary",
        ),
        (
            [
                _user("user_1", "same@example.com"),
                _user("user_2", " SAME@example.com "),
            ],
            "duplicate primary",
        ),
        (
            [
                _user("user_same", "one@example.com"),
                _user("user_same", "two@example.com"),
            ],
            "duplicate Clerk",
        ),
    ],
)
def test_resolver_rejects_ineligible_or_ambiguous_identities(
    monkeypatch,
    users,
    message,
):
    _install_users(monkeypatch, users)

    with pytest.raises(clerk_cohort.ClerkCohortError, match=message):
        clerk_cohort.resolve_live_clerk_cohort(
            expected_count=len(users),
            expected_sha256="0" * 64,
        )


def test_resolver_requires_exact_count_and_digest(monkeypatch):
    users = [_user("user_1", "one@example.com")]
    _install_users(monkeypatch, users)
    correct_digest = clerk_cohort.canonical_email_set_sha256(["one@example.com"])

    with pytest.raises(clerk_cohort.ClerkCohortError, match="count does not match"):
        clerk_cohort.resolve_live_clerk_cohort(
            expected_count=2,
            expected_sha256=correct_digest,
        )
    with pytest.raises(clerk_cohort.ClerkCohortError, match="digest does not match"):
        clerk_cohort.resolve_live_clerk_cohort(
            expected_count=1,
            expected_sha256="0" * 64,
        )


def test_resolver_sanitizes_clerk_failures(monkeypatch):
    sensitive = "private-person@example.com"
    _install_users(
        monkeypatch,
        [],
        failure=RuntimeError(f"upstream payload included {sensitive}"),
    )

    with pytest.raises(clerk_cohort.ClerkCohortError) as exc_info:
        clerk_cohort.resolve_live_clerk_cohort(
            expected_count=47,
            expected_sha256="0" * 64,
        )

    assert str(exc_info.value) == "live Clerk cohort retrieval failed"
    assert sensitive not in str(exc_info.value)


@pytest.mark.parametrize(
    ("expected_count", "expected_sha256", "message"),
    [
        (0, "0" * 64, "positive integer"),
        (True, "0" * 64, "positive integer"),
        (47, "not-a-digest", "64-character hex"),
        (47, None, "64-character hex"),
    ],
)
def test_resolver_rejects_invalid_expectations_before_calling_clerk(
    monkeypatch,
    expected_count,
    expected_sha256,
    message,
):
    fake_users = _install_users(monkeypatch, [])

    with pytest.raises(clerk_cohort.ClerkCohortError, match=message):
        clerk_cohort.resolve_live_clerk_cohort(
            expected_count=expected_count,
            expected_sha256=expected_sha256,
        )

    assert fake_users.requests == []
