import json
import secrets

from typer.testing import CliRunner

import app.services.continuity_reconciliation as continuity
from app.cli import app
from app.services.continuity_reconciliation import ContinuityAmendmentPlan


def _manifest(tmp_path):
    subject = f"user_cli_private_{secrets.token_hex(6)}"
    email = f"cli-private-{secrets.token_hex(5)}@example.com"
    path = tmp_path / "continuity-private.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "environment": "production",
                "identities": [
                    {
                        "issuer": "https://cli-test.clerk.invalid",
                        "subject": subject,
                        "verified_primary_email": email,
                        "doctor_id": None,
                        "safe_prelogin": True,
                        "continuity_basis": "reviewed_operational_user_omission",
                        "practice_id": None,
                    }
                ],
            }
        )
    )
    path.chmod(0o600)
    return path, subject, email


def _command(path) -> list[str]:
    return [
        "amend-pilot-continuity",
        "--manifest",
        str(path),
        "--source-ref",
        "change:cli-source",
        "--operator-ref",
        "change:cli-operator",
        "--reason",
        "Approved omission test",
    ]


def test_continuity_cli_dry_run_is_aggregate_only(monkeypatch, tmp_path):
    path, subject, email = _manifest(tmp_path)
    monkeypatch.setattr(
        continuity,
        "plan_continuity_amendment",
        lambda *args, **kwargs: ContinuityAmendmentPlan(
            plan_sha256="b" * 64,
            manifest_sha256="a" * 64,
            account_count=1,
            existing_doctor_count=0,
            safe_prelogin_count=1,
            already_applied=False,
        ),
    )

    result = CliRunner().invoke(app, _command(path))

    assert result.exit_code == 0, result.output
    assert "accounts=1" in result.output
    assert "safe_prelogin=1" in result.output
    assert "plan_sha256=" + "b" * 64 in result.output
    assert subject not in result.output
    assert email not in result.output


def test_continuity_cli_redacts_upstream_identity_failure(monkeypatch, tmp_path):
    path, subject, email = _manifest(tmp_path)

    def fail(*args, **kwargs):
        raise RuntimeError(f"upstream disclosed {subject} {email}")

    monkeypatch.setattr(continuity, "plan_continuity_amendment", fail)

    result = CliRunner().invoke(app, _command(path))

    assert result.exit_code != 0
    assert "validation failed" in " ".join(result.output.split())
    assert "no identities were printed" in " ".join(result.output.split())
    assert subject not in result.output
    assert email not in result.output


def test_continuity_cli_apply_requires_confirmed_plan(tmp_path):
    path, subject, email = _manifest(tmp_path)

    result = CliRunner().invoke(app, _command(path) + ["--apply"])

    assert result.exit_code != 0
    assert "--apply requires --confirm-plan-sha256" in result.output
    assert subject not in result.output
    assert email not in result.output
