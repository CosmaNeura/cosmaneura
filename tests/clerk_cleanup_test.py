from types import SimpleNamespace
from unittest.mock import MagicMock, call

import pytest

from tests.clerk_cleanup import delete_stale_test_users, stale_clerk_test_user_id
from tests.integration.clerk import clerk_test_email

CREATED_NS = 1_765_843_200_123_456_789
AFTER_STALE_CUTOFF_NS = CREATED_NS + (2 * 60 * 60 * 1_000_000_000)


def _user(user_id: object, *emails: object) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        email_addresses=[SimpleNamespace(email_address=email) for email in emails],
    )


def test_generated_user_becomes_cleanup_eligible_only_after_cutoff(monkeypatch):
    monkeypatch.setattr(
        "tests.integration.clerk.time.time_ns",
        lambda: CREATED_NS,
    )

    email = clerk_test_email()

    assert email == "test-1765843200123456789+clerk_test@example.com"
    user = _user("user_ephemeral", email)
    assert stale_clerk_test_user_id(user, now_ns=CREATED_NS) is None
    assert (
        stale_clerk_test_user_id(user, now_ns=AFTER_STALE_CUTOFF_NS)
        == "user_ephemeral"
    )


@pytest.mark.parametrize(
    "user",
    [
        _user("user_john", "john@cosmaneura.com"),
        _user("user_greg", "greg@mycatholicdoctor.com"),
        _user("user_future_pilot", "future.pilot@provider.example"),
        _user("user_dev", "user+clerk_test@example.com"),
        _user("user_admin", "admin+clerk_test@example.com"),
        _user("user_qa", "qa+clerk_test@example.com"),
        _user("user_seed", "seed+clerk_test@example.com"),
        _user("user_near_miss", "test-1765843200123456789@example.com"),
        _user("user_near_miss", "someone+clerk_test@example.com"),
        _user("user_near_miss", "test-not-a-timestamp+clerk_test@example.com"),
        _user("user_near_miss", "test-1765843200123456789+clerk_test@example.org"),
        _user(
            "user_multiple_emails",
            "test-1765843200123456789+clerk_test@example.com",
            "pilot@cosmaneura.com",
        ),
        _user("user_no_email"),
        _user(None, "test-1765843200123456789+clerk_test@example.com"),
        _user("not-a-clerk-id", "test-1765843200123456789+clerk_test@example.com"),
        SimpleNamespace(id="user_malformed", email_addresses=None),
    ],
)
def test_non_owned_or_ambiguous_users_fail_closed(user):
    assert stale_clerk_test_user_id(user, now_ns=AFTER_STALE_CUTOFF_NS) is None


def test_cleanup_deletes_only_positive_test_owned_cohort():
    users = [
        _user("user_ephemeral_1", "test-1765843200123456789+clerk_test@example.com"),
        _user("user_john", "john@cosmaneura.com"),
        _user("user_greg", "greg@mycatholicdoctor.com"),
        _user("user_future_pilot", "new.provider@example.net"),
        _user(
            "user_ambiguous",
            "test-1765843200123456790+clerk_test@example.com",
            "real.person@example.net",
        ),
        _user("user_ephemeral_2", "test-1765843200123456791+clerk_test@example.com"),
    ]
    users_api = MagicMock()
    users_api.list.return_value = users

    assert (
        delete_stale_test_users(
            users_api=users_api,
            secret_key="sk_test_unit",
            test_environment=True,
            now_ns=AFTER_STALE_CUTOFF_NS,
        )
        == 2
    )
    assert users_api.delete.call_args_list == [
        call(user_id="user_ephemeral_1"),
        call(user_id="user_ephemeral_2"),
    ]


def test_cleanup_requests_the_complete_dev_instance_page():
    users_api = MagicMock()
    users_api.list.return_value = []

    assert (
        delete_stale_test_users(
            users_api=users_api,
            secret_key="sk_test_unit",
            test_environment=True,
            now_ns=AFTER_STALE_CUTOFF_NS,
        )
        == 0
    )
    users_api.list.assert_called_once_with(request={"limit": 500})
    users_api.delete.assert_not_called()


@pytest.mark.parametrize("secret_key", ["sk_live_unit", "not-a-clerk-key"])
def test_cleanup_refuses_non_test_clerk_keys(secret_key):
    users_api = MagicMock()

    with pytest.raises(RuntimeError, match="non-test secret key"):
        delete_stale_test_users(
            users_api=users_api,
            secret_key=secret_key,
            test_environment=True,
            now_ns=AFTER_STALE_CUTOFF_NS,
        )

    users_api.list.assert_not_called()
    users_api.delete.assert_not_called()


def test_cleanup_refuses_to_run_outside_test_environment():
    users_api = MagicMock()

    with pytest.raises(RuntimeError, match="outside of testing"):
        delete_stale_test_users(
            users_api=users_api,
            secret_key="sk_test_unit",
            test_environment=False,
            now_ns=AFTER_STALE_CUTOFF_NS,
        )

    users_api.list.assert_not_called()
    users_api.delete.assert_not_called()


def test_cleanup_does_not_delete_when_clerk_returns_no_list():
    users_api = MagicMock()
    users_api.list.return_value = None

    with pytest.raises(RuntimeError, match="returned no user list"):
        delete_stale_test_users(
            users_api=users_api,
            secret_key="sk_test_unit",
            test_environment=True,
            now_ns=AFTER_STALE_CUTOFF_NS,
        )

    users_api.delete.assert_not_called()


def test_cleanup_selects_every_candidate_before_the_first_delete():
    class UninspectableUser:
        id = "user_uninspectable"

        @property
        def email_addresses(self):
            raise RuntimeError("malformed Clerk response")

    users_api = MagicMock()
    users_api.list.return_value = [
        _user("user_candidate", "test-1765843200123456789+clerk_test@example.com"),
        UninspectableUser(),
    ]

    with pytest.raises(RuntimeError, match="malformed Clerk response"):
        delete_stale_test_users(
            users_api=users_api,
            secret_key="sk_test_unit",
            test_environment=True,
            now_ns=AFTER_STALE_CUTOFF_NS,
        )

    users_api.delete.assert_not_called()


def test_cleanup_does_not_delete_another_concurrent_runs_fresh_user():
    fresh_created_ns = AFTER_STALE_CUTOFF_NS - (5 * 60 * 1_000_000_000)
    users_api = MagicMock()
    users_api.list.return_value = [
        _user(
            "user_fresh_concurrent_run",
            f"test-{fresh_created_ns}+clerk_test@example.com",
        ),
        _user(
            "user_stale_previous_run",
            "test-1765843200123456789+clerk_test@example.com",
        ),
    ]

    assert (
        delete_stale_test_users(
            users_api=users_api,
            secret_key="sk_test_unit",
            test_environment=True,
            now_ns=AFTER_STALE_CUTOFF_NS,
        )
        == 1
    )
    users_api.delete.assert_called_once_with(user_id="user_stale_previous_run")


def test_cleanup_without_a_key_is_a_noop():
    users_api = MagicMock()

    assert (
        delete_stale_test_users(
            users_api=users_api,
            secret_key="",
            test_environment=True,
            now_ns=AFTER_STALE_CUTOFF_NS,
        )
        == 0
    )
    users_api.list.assert_not_called()
    users_api.delete.assert_not_called()
