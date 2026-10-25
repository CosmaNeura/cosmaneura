"""Only the approved synthetic pair joins the staging Zoom capture cohort."""

import pytest

from app.services import zoom_webhooks

LAB_EMAILS = (
    "scribe-lab-a+clerk_test@cosmaneura.com",
    "scribe-lab-b+clerk_test@cosmaneura.com",
)


@pytest.mark.parametrize("email", LAB_EMAILS)
def test_lab_accounts_are_staging_only(monkeypatch, email):
    monkeypatch.setattr(zoom_webhooks, "is_staging", lambda: True)
    monkeypatch.setattr(zoom_webhooks, "is_production", lambda: False)
    assert zoom_webhooks.zoom_bot_email_allowed(email)
    assert zoom_webhooks.zoom_bot_email_allowed(f" {email.upper()} ")
    monkeypatch.setattr(zoom_webhooks, "is_staging", lambda: False)
    monkeypatch.setattr(zoom_webhooks, "is_production", lambda: True)
    assert not zoom_webhooks.zoom_bot_email_allowed(email)


def test_staging_preserves_reviewer_and_denies_every_other_account(monkeypatch):
    monkeypatch.setattr(zoom_webhooks, "is_staging", lambda: True)
    monkeypatch.setattr(zoom_webhooks, "is_production", lambda: False)
    assert zoom_webhooks.STAGING_ZOOM_EMAILS == frozenset(
        ("zoom-reviewer@cosmaneura.com", *LAB_EMAILS)
    )
    assert zoom_webhooks.zoom_bot_email_allowed("zoom-reviewer@cosmaneura.com")
    for email in (
        None,
        "",
        "scribe-lab-c+clerk_test@cosmaneura.com",
        "scribe-lab-a+clerk_test@cosmaneura.com.invalid",
        "unknown@example.com",
        *zoom_webhooks.PROD_ZOOM_EMAILS,
    ):
        assert not zoom_webhooks.zoom_bot_email_allowed(email)


def test_production_cohort_is_preserved(monkeypatch):
    monkeypatch.setattr(zoom_webhooks, "is_staging", lambda: False)
    monkeypatch.setattr(zoom_webhooks, "is_production", lambda: True)
    for email in zoom_webhooks.PROD_ZOOM_EMAILS:
        assert zoom_webhooks.zoom_bot_email_allowed(email)
    assert not zoom_webhooks.zoom_bot_email_allowed("zoom-reviewer@cosmaneura.com")
    assert not zoom_webhooks.zoom_bot_email_allowed("unknown@example.com")
