import json
import re
import secrets
from pathlib import Path
from types import SimpleNamespace

from click.utils import strip_ansi
from typer.testing import CliRunner

from app.cli import app
from app.services import superadmin_capability

from app.models.doctor import Doctor
from app.models.superadmin_capability import (
    SuperadminCapabilityAudit,
    SuperadminCapabilityEnvironment,
    SuperadminCapabilityGrant,
)

ISSUER = "https://staging.example.clerk.accounts.dev"
SOURCE_REF = "controlled:superadmin-cli-test"
OPERATOR_REF = "ticket:superadmin-cli-test"


def _private_manifest(tmp_path: Path):
    nonce = secrets.token_hex(6)
    subject = f"user_superadmin_cli_{nonce}"
    email = f"superadmin-cli-{nonce}@example.com"
    doctor = Doctor(clerk_id=subject, email=email, biography="").save()
    path = tmp_path / "targets.superadmin-capabilities.private.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "environment": "staging",
                "clerk_issuer": ISSUER,
                "accounts": [
                    {
                        "clerk_subject": subject,
                        "verified_primary_email": email,
                        "doctor_id": str(doctor.id),
                        "capability_kinds": ["platform_admin", "view_as"],
                    }
                ],
            }
        )
    )
    path.chmod(0o600)
    return path, subject, email, str(doctor.id)


def _mock_environment_and_clerk(monkeypatch, subject: str, email: str):
    monkeypatch.setattr(
        superadmin_capability,
        "configured_superadmin_capability_environment",
        lambda: SuperadminCapabilityEnvironment.staging,
    )
    monkeypatch.setattr(
        superadmin_capability,
        "configured_superadmin_clerk_issuer",
        lambda: ISSUER,
    )
    user = SimpleNamespace(
        id=subject,
        banned=False,
        locked=False,
        primary_email_address_id="primary",
        email_addresses=[
            SimpleNamespace(
                id="primary",
                email_address=email,
                verification=SimpleNamespace(status="verified"),
            )
        ],
    )
    monkeypatch.setattr(
        superadmin_capability,
        "clerk",
        SimpleNamespace(users=SimpleNamespace(get=lambda *, user_id: user)),
    )


def _command(action: str, manifest: Path, reason_code: str) -> list[str]:
    return [
        action,
        "--manifest",
        str(manifest),
        "--source-ref",
        SOURCE_REF,
        "--operator-ref",
        OPERATOR_REF,
        "--reason-code",
        reason_code,
    ]


def _assert_redacted(output: str, *private_values: str) -> None:
    for value in private_values:
        assert value not in output


def test_cli_grant_list_and_revoke_are_dry_run_first_and_aggregate_only(
    monkeypatch,
    tmp_path: Path,
):
    manifest, subject, email, doctor_id = _private_manifest(tmp_path)
    _mock_environment_and_clerk(monkeypatch, subject, email)
    runner = CliRunner()

    grant_command = _command(
        "grant-superadmin-capabilities",
        manifest,
        "reviewed_initial_grant",
    )
    dry_run = runner.invoke(app, grant_command)
    assert dry_run.exit_code == 0, dry_run.output
    assert "state=deployable" in dry_run.output
    assert "accounts=1" in dry_run.output
    assert "capabilities=2" in dry_run.output
    assert SuperadminCapabilityGrant.count() == 0
    plan_match = re.search(r"plan_sha256=([0-9a-f]{64})", dry_run.output)
    assert plan_match
    _assert_redacted(dry_run.output, subject, email, doctor_id)

    applied = runner.invoke(
        app,
        grant_command + ["--confirm-plan-sha256", plan_match.group(1), "--apply"],
    )
    assert applied.exit_code == 0, applied.output
    assert "state=applied" in applied.output
    assert "created=2" in applied.output
    assert SuperadminCapabilityGrant.count() == 2
    assert SuperadminCapabilityAudit.count() == 2
    _assert_redacted(applied.output, subject, email, doctor_id)

    listed = runner.invoke(app, ["list-superadmin-capabilities"])
    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.output) == {
        "active_binding_sha256": json.loads(listed.output)["active_binding_sha256"],
        "active_count": 2,
        "audit_count": 2,
        "environment": "staging",
        "platform_admin_active_count": 1,
        "revoked_count": 0,
        "total_count": 2,
        "view_as_active_count": 1,
    }
    assert re.fullmatch(
        r"[0-9a-f]{64}",
        json.loads(listed.output)["active_binding_sha256"],
    )
    _assert_redacted(listed.output, subject, email, doctor_id)

    revoke_command = _command(
        "revoke-superadmin-capabilities",
        manifest,
        "reviewed_access_revocation",
    )
    revoke_dry_run = runner.invoke(app, revoke_command)
    assert revoke_dry_run.exit_code == 0, revoke_dry_run.output
    assert "state=revocable" in revoke_dry_run.output
    revoke_plan = re.search(
        r"plan_sha256=([0-9a-f]{64})",
        revoke_dry_run.output,
    )
    assert revoke_plan
    _assert_redacted(revoke_dry_run.output, subject, email, doctor_id)

    revoked = runner.invoke(
        app,
        revoke_command + ["--confirm-plan-sha256", revoke_plan.group(1), "--apply"],
    )
    assert revoked.exit_code == 0, revoked.output
    assert "state=revoked" in revoked.output
    assert "revoked=2" in revoked.output
    assert SuperadminCapabilityGrant.count() == 2
    assert SuperadminCapabilityAudit.count() == 4
    _assert_redacted(revoked.output, subject, email, doctor_id)


def test_cli_rejects_non_private_manifest_without_disclosing_identity(
    monkeypatch,
    tmp_path: Path,
):
    manifest, subject, email, doctor_id = _private_manifest(tmp_path)
    _mock_environment_and_clerk(monkeypatch, subject, email)
    manifest.chmod(0o644)
    result = CliRunner().invoke(
        app,
        _command(
            "grant-superadmin-capabilities",
            manifest,
            "reviewed_initial_grant",
        ),
    )
    assert result.exit_code != 0
    normalized_output = " ".join(strip_ansi(result.output).split())
    assert "validation failed" in normalized_output
    assert "no identities were printed" in normalized_output
    _assert_redacted(result.output, subject, email, doctor_id)
