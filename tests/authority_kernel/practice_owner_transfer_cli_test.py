"""Operator CLI confirmation, identity adapter and safe failure behavior."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from click import unstyle
from typeid import TypeID
from typer.testing import CliRunner

import app.cli as cli
from app.configuration import clerk as clerk_configuration
from app.services import clerk_identity
from app.services import practice_owner_transfer as transfer

from sqlalchemy.exc import SQLAlchemyError


@pytest.fixture
def command(monkeypatch):
    calls = []
    practice = str(TypeID("practice"))

    def invoke(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            practice_id=practice,
            previous_owner_member_id=str(TypeID("practice_member")),
            new_owner_member_id=str(TypeID("practice_member")),
            plan_sha256="a" * 64,
            audit_id=None,
            already_applied=False,
        )

    monkeypatch.setattr(cli, "global_session", nullcontext)
    monkeypatch.setattr(transfer, "transfer_practice_owner", invoke)
    args = [
        "transfer-practice-owner",
        "--practice-id",
        practice,
        "--expected-owner-subject",
        "user_SyntheticPreviousOwner",
        "--new-owner-subject",
        "user_SyntheticNewOwner",
        "--transfer-ref",
        "synthetic-transfer-001",
        "--operator-ref",
        "operator-test-001",
        "--reason",
        "owner_correction",
    ]
    return args, calls


def test_cli_defaults_to_dry_run_and_passes_live_identity_adapter(command):
    args, calls = command
    result = CliRunner().invoke(cli.app, args, input="new@example.invalid\n")
    assert result.exit_code == 0, result.output
    assert len(calls) == 1 and calls[0]["apply"] is False
    assert calls[0]["verify_primary_email"] is clerk_identity.verified_primary_email
    assert calls[0]["new_owner_email_confirmation"] == "new@example.invalid"
    assert calls[0]["recover_orphaned_owner"] is False
    assert calls[0]["verify_owner_absence"] is None
    assert "DRY RUN" in result.output and "plan_sha256=" in result.output
    assert "new@example.invalid" not in result.output


def test_cli_orphan_recovery_is_explicit_and_uses_only_configured_proof(command):
    args, calls = command
    result = CliRunner().invoke(
        cli.app, [*args, "--recover-orphaned-owner"], input="new@example.invalid\n"
    )
    assert result.exit_code == 0, result.output
    assert len(calls) == 1 and calls[0]["apply"] is False
    assert calls[0]["recover_orphaned_owner"] is True
    assert (
        calls[0]["verify_owner_absence"]
        is clerk_configuration.prove_absent_owner_identity
    )
    assert calls[0]["verify_primary_email"] is clerk_identity.verified_primary_email
    assert "new@example.invalid" not in result.output


def test_cli_orphan_apply_still_requires_reviewed_hash_before_any_prompt(command):
    args, calls = command
    result = CliRunner().invoke(cli.app, [*args, "--recover-orphaned-owner", "--apply"])
    assert result.exit_code == 2 and calls == []
    assert (
        "--confirm-plan-sha256" in unstyle(result.output)
        and "primary email" not in result.output
    )


def test_cli_apply_without_hash_does_not_prompt_or_call_service(command):
    args, calls = command
    result = CliRunner().invoke(cli.app, [*args, "--apply"])
    assert result.exit_code == 2
    assert "--confirm-plan-sha256" in unstyle(result.output)
    assert "primary email" not in result.output and calls == []


def test_cli_apply_passes_exact_confirmation_hash(command):
    args, calls = command
    result = CliRunner().invoke(
        cli.app,
        [*args, "--apply", "--confirm-plan-sha256", "a" * 64],
        input="new@example.invalid\n",
    )
    assert result.exit_code == 0, result.output
    assert calls[0]["apply"] is True
    assert calls[0]["confirm_plan_sha256"] == "a" * 64


@pytest.mark.parametrize("apply", [False, True])
def test_cli_database_failure_has_no_details_or_automatic_retry(
    command, monkeypatch, apply
):
    args, calls = command

    def fail(**kwargs):
        calls.append(kwargs)
        raise SQLAlchemyError("sensitive database detail must stay private")

    monkeypatch.setattr(transfer, "transfer_practice_owner", fail)
    if apply:
        args = [*args, "--apply", "--confirm-plan-sha256", "a" * 64]
    result = CliRunner().invoke(cli.app, args, input="new@example.invalid\n")
    assert result.exit_code == 2 and len(calls) == 1
    assert "sensitive database detail" not in result.output
    assert "unconfirmed" in result.output if apply else "no mutation" in result.output
