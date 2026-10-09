"""Qualify the test helper against the installed SDK without contacting Clerk."""

from types import SimpleNamespace
from unittest.mock import Mock, create_autospec, sentinel
from uuid import uuid4

import pytest

from tests.routes import utils


@pytest.mark.parametrize("existing_count", [0, 1, 2])
def test_test_user_creation_uses_sdk_signature_and_preserves_existing_users(
    monkeypatch, existing_count
):
    email = f"synthetic-{uuid4().hex}@example.invalid"
    create = create_autospec(
        utils.clerk.users.create, return_value=sentinel.created_user
    )
    existing = [sentinel.existing_user] * existing_count
    users = SimpleNamespace(list=Mock(return_value=existing), create=create)
    monkeypatch.setattr(utils, "CLERK_PRIVATE_KEY", "synthetic")
    monkeypatch.setattr(utils, "clerk", SimpleNamespace(users=users))

    if existing_count == 2:
        with pytest.raises(ValueError, match="more than one user found"):
            utils._get_or_create_clerk_user(email)
    else:
        expected = (
            sentinel.created_user if existing_count == 0 else sentinel.existing_user
        )
        assert utils._get_or_create_clerk_user(email) is expected

    users.list.assert_called_once_with(request={"email_address": [email]})
    if existing_count == 0:
        create.assert_called_once_with(
            email_address=[email], password=utils.CLERK_DEV_USER_PASSWORD
        )
    else:
        create.assert_not_called()
