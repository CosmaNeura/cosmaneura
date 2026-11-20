"""CLI read-only count for owner_reassignment_needed_at practices."""

import json
import secrets
from datetime import datetime, timezone

from typer.testing import CliRunner

from app.cli import app

from app.models.practice import Practice

NOW = datetime(2026, 11, 19, 18, tzinfo=timezone.utc)


def test_count_owner_reassignment_needed_is_read_only_json():
    Practice(
        legal_name="Count Flagged Practice",
        join_code=f"C{secrets.token_hex(4).upper()[:7]}",
        email_domain="synthetic.invalid",
        owner_reassignment_needed_at=NOW,
    ).save()
    Practice(
        legal_name="Count Clear Practice",
        join_code=f"D{secrets.token_hex(4).upper()[:7]}",
        email_domain="synthetic.invalid",
    ).save()

    result = CliRunner().invoke(app, ["count-owner-reassignment-needed"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["read_only"] is True
    assert payload["owner_reassignment_needed_count"] >= 1


def test_runbook_names_open_john_as_refund_owner():
    from pathlib import Path

    text = Path("docs/self-delete-account-billing-runbook.md").read_text()
    assert "OPEN-JOHN" in text
    assert "stripe_refund_needed" in text
    assert "LateCheckoutRefundNeeded" in text
