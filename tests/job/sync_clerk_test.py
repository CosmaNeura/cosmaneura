import json
from types import SimpleNamespace
from unittest.mock import patch

from structlog.testing import capture_logs

from app.jobs.sync_clerk import perform

from app.models.doctor import Doctor

from tests.factories import DoctorFactory


def _clerk_user(*, primary_email_id: str | None, emails: list[tuple[str, str]]):
    """Build a SimpleNamespace that quacks like clerk_backend_api.models.User.

    `emails` is a list of (email_id, email_address) tuples.
    """
    return SimpleNamespace(
        primary_email_address_id=primary_email_id,
        email_addresses=[
            SimpleNamespace(id=eid, email_address=addr) for eid, addr in emails
        ],
    )


def test_collision_skips_doctor_and_continues_with_others(global_session):
    """When a Clerk-reported email is already owned by another doctor row,
    the job must skip the colliding doctor and keep syncing the rest —
    not raise an IntegrityError that kills the whole run."""

    owner = DoctorFactory.build(email="taken@example.com").save()
    colliding = DoctorFactory.build(email=None).save()
    fresh = DoctorFactory.build(email=None).save()

    def fake_get(*, user_id: str):
        if user_id == owner.clerk_id:
            return _clerk_user(
                primary_email_id="e_owner",
                emails=[("e_owner", "taken@example.com")],
            )
        if user_id == colliding.clerk_id:
            return _clerk_user(
                primary_email_id="e_colliding",
                emails=[("e_colliding", "taken@example.com")],
            )
        if user_id == fresh.clerk_id:
            return _clerk_user(
                primary_email_id="e_fresh",
                emails=[("e_fresh", "fresh@example.com")],
            )
        raise AssertionError(f"unexpected user_id {user_id}")

    with (
        patch("app.jobs.sync_clerk.clerk.users.get", side_effect=fake_get),
        capture_logs() as logs,
    ):
        perform()

    owner_after = Doctor.get(id=owner.id)
    colliding_after = Doctor.get(id=colliding.id)
    fresh_after = Doctor.get(id=fresh.id)
    assert owner_after and colliding_after and fresh_after
    assert owner_after.email == "taken@example.com"
    assert colliding_after.email is None
    assert fresh_after.email == "fresh@example.com"

    serialized = json.dumps(logs, sort_keys=True)
    for private_value in (
        str(owner.id),
        str(colliding.id),
        str(fresh.id),
        owner.clerk_id,
        colliding.clerk_id,
        fresh.clerk_id,
        "taken@example.com",
        "fresh@example.com",
        "e_owner",
        "e_colliding",
        "e_fresh",
    ):
        assert private_value not in serialized
    assert "hmac:" in serialized
    assert any(entry.get("event_code") == "clerk_email_collision" for entry in logs)


def test_uses_primary_email_not_first_in_list(global_session):
    """Clerk returns email_addresses as a list whose order is not guaranteed
    to put the primary email first. The job must look up the primary email
    via primary_email_address_id."""

    doctor = DoctorFactory.build(email=None).save()

    def fake_get(*, user_id: str):
        assert user_id == doctor.clerk_id
        return _clerk_user(
            primary_email_id="e_primary",
            emails=[
                ("e_secondary", "secondary@example.com"),
                ("e_primary", "primary@example.com"),
            ],
        )

    with patch("app.jobs.sync_clerk.clerk.users.get", side_effect=fake_get):
        perform()

    after = Doctor.get(id=doctor.id)
    assert after is not None
    assert after.email == "primary@example.com"


def test_skips_doctor_when_primary_email_id_not_in_list(global_session):
    """Defensive: if Clerk reports a primary_email_address_id that does not
    match any entry in email_addresses, skip the doctor instead of crashing
    or assigning a wrong email."""

    doctor = DoctorFactory.build(email=None).save()

    def fake_get(*, user_id: str):
        assert user_id == doctor.clerk_id
        return _clerk_user(
            primary_email_id="e_missing",
            emails=[("e_other", "other@example.com")],
        )

    with patch("app.jobs.sync_clerk.clerk.users.get", side_effect=fake_get):
        perform()

    after = Doctor.get(id=doctor.id)
    assert after is not None
    assert after.email is None
