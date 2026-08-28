import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import cast

import pytest
import typer
from click import unstyle
from typer.testing import CliRunner

from app.cli import (
    _multi_practice_configuration_plan_sha256,
    _read_multi_practice_roster_manifest,
    _read_roster_manifest,
    _write_new_private_secret,
    app,
)
from app.services.organization_access import (
    OrganizationAccessError,
    RosterAssignmentInput,
)

from app.models.doctor import Doctor
from app.models.organization_access import (
    OrganizationRosterApproval,
    ProviderOrganization,
)
from app.models.practice import Practice, PracticeMember
from app.models.practice_access_audit import PracticeAccessAction, PracticeAccessAudit
from sqlalchemy.exc import SQLAlchemyError


def _normalized_cli_output(output: str) -> str:
    lines = (line.strip().strip("│").strip() for line in unstyle(output).splitlines())
    return " ".join(" ".join(lines).split())


def _private_manifest(tmp_path, payload, *, name="roster.json"):
    path = tmp_path / name
    raw = json.dumps(payload, separators=(",", ":")).encode()
    path.write_bytes(raw)
    path.chmod(0o600)
    return path, raw


def test_roster_manifest_uses_exact_raw_digest_and_normalizes_email(tmp_path):
    path, raw = _private_manifest(
        tmp_path,
        {
            "schema_version": 1,
            "members": [{"email": " Pilot@Example.COM "}],
        },
    )

    entries, digest = _read_roster_manifest(str(path))

    assert digest == hashlib.sha256(raw).hexdigest()
    assert len(entries) == 1
    assert entries[0].email == "pilot@example.com"
    assert entries[0].expires_at is None


def test_roster_manifest_rejects_permissive_file(tmp_path):
    path, _ = _private_manifest(
        tmp_path,
        {"schema_version": 1, "members": [{"email": "pilot@example.com"}]},
    )
    path.chmod(0o640)

    with pytest.raises(typer.BadParameter, match="chmod 600"):
        _read_roster_manifest(str(path))


def test_roster_manifest_rejects_symlink(tmp_path):
    path, _ = _private_manifest(
        tmp_path,
        {"schema_version": 1, "members": [{"email": "pilot@example.com"}]},
    )
    link = tmp_path / "linked-roster.json"
    link.symlink_to(path)

    with pytest.raises(typer.BadParameter, match="symbolic link"):
        _read_roster_manifest(str(link))


def test_roster_manifest_validation_does_not_disclose_email(tmp_path):
    sensitive_email = "sensitive-provider@example.com"
    path, _ = _private_manifest(
        tmp_path,
        {
            "schema_version": 1,
            "members": [
                {"email": sensitive_email},
                {"email": sensitive_email.upper()},
            ],
        },
    )

    with pytest.raises(typer.BadParameter) as raised:
        _read_roster_manifest(str(path))

    assert sensitive_email not in str(raised.value).lower()


def test_roster_manifest_rejects_email_longer_than_storage_contract(tmp_path):
    path, _ = _private_manifest(
        tmp_path,
        {
            "schema_version": 1,
            "members": [{"email": f"{'a' * 309}@example.com"}],
        },
    )

    with pytest.raises(typer.BadParameter, match="invalid email"):
        _read_roster_manifest(str(path))


def test_multi_practice_manifest_requires_explicit_exact_targets(tmp_path):
    first_practice_id = "practice_01h455vb4pex5vsknk084sn02q"
    second_practice_id = "practice_01h455vb4pex5vsknk084sn02r"
    payload = {
        "schema_version": 2,
        "organization_slug": "my-catholic-doctor",
        "email_domain": "mycatholicdoctor.com",
        "members": [
            {
                "email": "first@mycatholicdoctor.com",
                "target_practice_id": first_practice_id,
            },
            {
                "email": "second@mycatholicdoctor.com",
                "target_practice_id": second_practice_id,
            },
        ],
    }
    path, raw = _private_manifest(tmp_path, payload, name="mcd-v2.json")

    assignments, slug, domain, digest = _read_multi_practice_roster_manifest(str(path))

    assert slug == "my-catholic-doctor"
    assert domain == "mycatholicdoctor.com"
    assert digest == hashlib.sha256(raw).hexdigest()
    assert {str(item.target_practice_id) for item in assignments} == {
        first_practice_id,
        second_practice_id,
    }


def test_multi_practice_manifest_rejects_role_or_implicit_target(tmp_path):
    path, _ = _private_manifest(
        tmp_path,
        {
            "schema_version": 2,
            "organization_slug": "my-catholic-doctor",
            "email_domain": "mycatholicdoctor.com",
            "members": [
                {
                    "email": "first@mycatholicdoctor.com",
                    "target_practice_id": "practice_01h455vb4pex5vsknk084sn02q",
                    "role": "owner",
                }
            ],
        },
        name="invalid-mcd-v2.json",
    )

    with pytest.raises(typer.BadParameter, match="exactly email"):
        _read_multi_practice_roster_manifest(str(path))


def test_multi_practice_plan_hash_binds_manifest_and_every_operator_argument(tmp_path):
    first_practice_id = "practice_01h455vb4pex5vsknk084sn02q"
    second_practice_id = "practice_01h455vb4pex5vsknk084sn02r"
    path, _ = _private_manifest(
        tmp_path,
        {
            "schema_version": 2,
            "organization_slug": "my-catholic-doctor",
            "email_domain": "mycatholicdoctor.com",
            "members": [
                {
                    "email": "first@mycatholicdoctor.com",
                    "target_practice_id": first_practice_id,
                },
                {
                    "email": "second@mycatholicdoctor.com",
                    "target_practice_id": second_practice_id,
                },
            ],
        },
        name="mcd-plan-v2.json",
    )
    assignments, slug, domain, roster_sha256 = _read_multi_practice_roster_manifest(
        str(path)
    )
    parameters = {
        "organization_slug": slug,
        "email_domain": domain,
        "legal_name": "MyCatholicDoctor",
        "operator_ref": "change:mcd-cutover",
        "reason": "Approved multi-practice cutover",
        "source_ref": "private://mcd-roster-v2",
        "roster_sha256": roster_sha256,
    }
    plan_sha256 = _multi_practice_configuration_plan_sha256(
        assignments,
        **parameters,
    )

    assert re.fullmatch(r"[0-9a-f]{64}", plan_sha256)
    assert (
        _multi_practice_configuration_plan_sha256(
            list(reversed(assignments)),
            **parameters,
        )
        == plan_sha256
    )
    for field, changed in {
        "organization_slug": "other-organization",
        "legal_name": "Different Legal Name",
        "operator_ref": "change:other-cutover",
        "reason": "Different approved reason",
        "source_ref": "private://other-roster-v2",
        "roster_sha256": "f" * 64,
    }.items():
        changed_parameters = parameters | {field: changed}
        assert (
            _multi_practice_configuration_plan_sha256(
                assignments,
                **changed_parameters,
            )
            != plan_sha256
        )
    changed_assignments = [
        RosterAssignmentInput(
            email="changed@mycatholicdoctor.com",
            target_practice_id=assignments[0].target_practice_id,
        ),
        assignments[1],
    ]
    assert (
        _multi_practice_configuration_plan_sha256(
            changed_assignments,
            **parameters,
        )
        != plan_sha256
    )
    other_domain_assignments = [
        RosterAssignmentInput(
            email="first@other.example.com",
            target_practice_id=assignments[0].target_practice_id,
        ),
        RosterAssignmentInput(
            email="second@other.example.com",
            target_practice_id=assignments[1].target_practice_id,
        ),
    ]
    assert (
        _multi_practice_configuration_plan_sha256(
            other_domain_assignments,
            **(parameters | {"email_domain": "other.example.com"}),
        )
        != plan_sha256
    )


def test_access_code_output_is_private_and_never_overwrites(tmp_path):
    path = tmp_path / "organization-code.txt"

    _write_new_private_secret(str(path), "ABCDEFGH2345")

    assert path.read_text() == "ABCDEFGH2345\n"
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(typer.BadParameter, match="new writable file"):
        _write_new_private_secret(str(path), "ZZZZZZZZ9999")
    assert path.read_text() == "ABCDEFGH2345\n"


def test_provider_practice_bootstrap_cli_is_private_dry_run_then_apply(
    tmp_path,
    monkeypatch,
):
    domain = f"fiat-{secrets.token_hex(4)}.example.com"
    owner_email = f"caroline@{domain}"
    owner = Doctor(
        clerk_id=f"user_bootstrap_cli_{secrets.token_hex(8)}",
        email=owner_email,
        biography="",
    ).save()
    workforce = Doctor(
        clerk_id=f"user_bootstrap_cli_{secrets.token_hex(8)}",
        email=f"provider@{domain}",
        biography="",
    ).save()
    manifest, raw = _private_manifest(
        tmp_path,
        {
            "schema_version": 1,
            "members": [
                {"email": owner.email},
                {"email": workforce.email},
            ],
        },
        name="fiat-roster.json",
    )
    monkeypatch.setattr(
        "app.services.clerk_identity.verified_primary_email",
        lambda _clerk_id: owner_email,
    )
    command = [
        "bootstrap-provider-practice",
        "--manifest",
        str(manifest),
        "--owner-doctor-id",
        str(owner.id),
        "--legal-name",
        "Fiat Integrative Health, PLLC",
        "--email-domain",
        domain,
        "--bootstrap-ref",
        f"bootstrap:{secrets.token_hex(8)}",
        "--operator-ref",
        "change:fiat-bootstrap",
        "--reason",
        "Approved pilot practice bootstrap",
    ]
    runner = CliRunner()

    dry_run = runner.invoke(app, command, input=f"{owner_email}\n")

    assert dry_run.exit_code == 0, dry_run.output
    assert "DRY RUN" in dry_run.output
    assert owner_email not in dry_run.output
    assert Practice.where(Practice.email_domain == domain).count() == 0
    roster_match = re.search(r"roster_sha256=([0-9a-f]{64})", dry_run.output)
    plan_match = re.search(r"plan_sha256=([0-9a-f]{64})", dry_run.output)
    assert roster_match
    assert roster_match.group(1) == hashlib.sha256(raw).hexdigest()
    assert plan_match

    applied = runner.invoke(
        app,
        command
        + [
            "--confirm-roster-sha256",
            roster_match.group(1),
            "--confirm-plan-sha256",
            plan_match.group(1),
            "--apply",
        ],
        input=f"{owner_email}\n",
    )

    assert applied.exit_code == 0, applied.output
    assert "bootstrap applied" in applied.output
    assert owner_email not in applied.output
    practice = Practice.get(email_domain=domain)
    assert practice is not None
    assert practice.join_code not in applied.output
    member = PracticeMember.get(doctor_id=owner.id)
    assert member is not None
    assert member.role == "owner"


def test_provider_practice_bootstrap_cli_sanitizes_database_errors(
    tmp_path,
    monkeypatch,
):
    sensitive_email = "private-bootstrap@example.com"
    manifest, _ = _private_manifest(
        tmp_path,
        {
            "schema_version": 1,
            "members": [{"email": sensitive_email}],
        },
        name="private-bootstrap-error.json",
    )

    def raise_database_error(_doctor_id):
        raise SQLAlchemyError(f"database parameter: {sensitive_email}")

    monkeypatch.setattr("app.cli._doctor_by_id", raise_database_error)
    result = CliRunner().invoke(
        app,
        [
            "bootstrap-provider-practice",
            "--manifest",
            str(manifest),
            "--owner-doctor-id",
            "doctor_01h455vb4pex5vsknk084sn02q",
            "--legal-name",
            "Private Bootstrap Test",
            "--email-domain",
            "example.com",
            "--bootstrap-ref",
            "test:private-bootstrap-error",
            "--operator-ref",
            "change:private-bootstrap-error",
            "--reason",
            "Prove database errors are redacted",
        ],
        input=f"{sensitive_email}\n",
    )

    assert result.exit_code != 0
    normalized_output = " ".join(result.output.split())
    assert "database validation failed" in normalized_output
    assert "identities were" in normalized_output
    assert "printed" in normalized_output
    assert sensitive_email not in result.output


def test_multi_practice_configuration_cli_sanitizes_database_errors(
    tmp_path,
    monkeypatch,
):
    sensitive_email = "private-multi@mycatholicdoctor.com"
    manifest, _ = _private_manifest(
        tmp_path,
        {
            "schema_version": 2,
            "organization_slug": "private-multi-error",
            "email_domain": "mycatholicdoctor.com",
            "members": [
                {
                    "email": sensitive_email,
                    "target_practice_id": "practice_01h455vb4pex5vsknk084sn02q",
                },
                {
                    "email": "second@mycatholicdoctor.com",
                    "target_practice_id": "practice_01h455vb4pex5vsknk084sn02r",
                },
            ],
        },
        name="private-multi-error.json",
    )

    monkeypatch.setattr("app.cli._practice_by_id", lambda _practice_id: object())

    def raise_database_error(*_args, **_kwargs):
        raise SQLAlchemyError(f"database parameter: {sensitive_email}")

    monkeypatch.setattr(
        "app.services.organization_access.validate_provider_organization_with_roster",
        raise_database_error,
    )
    result = CliRunner().invoke(
        app,
        [
            "configure-provider-organization-with-roster",
            "--manifest",
            str(manifest),
            "--legal-name",
            "Private Multi Test",
            "--operator-ref",
            "change:private-multi-error",
            "--reason",
            "Prove database errors are redacted",
            "--source-ref",
            "test://private-multi-error",
        ],
    )

    assert result.exit_code != 0
    normalized_output = " ".join(result.output.split())
    assert "database validation failed" in normalized_output
    assert "identities were" in normalized_output
    assert "printed" in normalized_output
    assert sensitive_email not in result.output


def test_access_code_cli_retains_secret_for_ambiguous_single_and_rotation(
    tmp_path,
    monkeypatch,
):
    from app.services import organization_access as organization_access_service

    now = datetime.now(timezone.utc)
    domain = f"ambiguous-{secrets.token_hex(4)}.example.com"
    practice = Practice(
        legal_name="Ambiguous Outcome Practice",
        join_code=f"A{secrets.token_hex(4).upper()[:7]}",
        email_domain=domain,
        origin_channel="direct",
        is_solo=False,
        negotiated_baa_on_file=True,
        negotiated_baa_effective_date=now - timedelta(days=1),
        negotiated_baa_reference="test://ambiguous-baa",
        billing_exempt=True,
    ).save()
    slug = f"ambiguous-{secrets.token_hex(5)}"
    sensitive_value = f"private@{domain}"
    real_configure = organization_access_service.configure_provider_organization

    def raise_ambiguous_configuration(*_args, **_kwargs):
        raise SQLAlchemyError(f"database parameter: {sensitive_value}")

    monkeypatch.setattr(
        organization_access_service,
        "configure_provider_organization",
        raise_ambiguous_configuration,
    )
    configure_code_path = tmp_path / "ambiguous-configure-code.txt"
    configured = CliRunner().invoke(
        app,
        [
            "configure-provider-organization",
            "--practice-id",
            str(practice.id),
            "--legal-name",
            "Ambiguous Outcome Organization",
            "--slug",
            slug,
            "--email-domain",
            domain,
            "--operator-ref",
            "change:ambiguous-configure",
            "--reason",
            "Prove ambiguous output retention",
            "--access-code-output",
            str(configure_code_path),
            "--apply",
        ],
    )

    configured_output = _normalized_cli_output(configured.output)
    assert configured.exit_code != 0
    assert "outcome is ambiguous" in configured_output
    assert "output file was retained" in configured_output
    assert "Verify database state" in configured_output
    assert sensitive_value not in configured.output
    assert configure_code_path.is_file()
    assert configure_code_path.stat().st_mode & 0o777 == 0o600

    monkeypatch.setattr(
        organization_access_service,
        "configure_provider_organization",
        real_configure,
    )
    organization, _, _ = real_configure(
        practice,
        legal_name="Ambiguous Outcome Organization",
        slug=slug,
        email_domain=domain,
        operator_ref="change:prepare-rotation",
        reason="Prepare rotation recovery test",
        now=now,
    )

    def raise_ambiguous_rotation(*_args, **_kwargs):
        raise SQLAlchemyError(f"database parameter: {sensitive_value}")

    monkeypatch.setattr(
        organization_access_service,
        "rotate_access_code",
        raise_ambiguous_rotation,
    )
    rotation_code_path = tmp_path / "ambiguous-rotation-code.txt"
    rotated = CliRunner().invoke(
        app,
        [
            "rotate-organization-code",
            "--organization-slug",
            organization.slug,
            "--operator-ref",
            "change:ambiguous-rotation",
            "--reason",
            "Prove ambiguous rotation output retention",
            "--access-code-output",
            str(rotation_code_path),
            "--apply",
        ],
    )

    rotated_output = _normalized_cli_output(rotated.output)
    assert rotated.exit_code != 0
    assert "outcome is ambiguous" in rotated_output
    assert "output file was retained" in rotated_output
    assert "Verify database state" in rotated_output
    assert sensitive_value not in rotated.output
    assert rotation_code_path.is_file()
    assert rotation_code_path.stat().st_mode & 0o777 == 0o600


def test_single_practice_configuration_dry_run_rejects_public_conversion() -> None:
    now = datetime.now(timezone.utc)
    domain = f"public-cli-{secrets.token_hex(4)}.example.com"
    practice = Practice(
        legal_name="Public CLI Practice",
        join_code=f"P{secrets.token_hex(4).upper()[:7]}",
        email_domain=domain,
        origin_channel="direct",
        is_solo=False,
        public_onboarding_required=True,
        negotiated_baa_on_file=True,
        negotiated_baa_effective_date=now - timedelta(days=1),
        negotiated_baa_reference="test://public-cli-baa",
        billing_exempt=True,
    ).save()

    result = CliRunner().invoke(
        app,
        [
            "configure-provider-organization",
            "--practice-id",
            str(practice.id),
            "--legal-name",
            "Public CLI Organization",
            "--slug",
            f"public-cli-{secrets.token_hex(5)}",
            "--email-domain",
            domain,
            "--operator-ref",
            "change:public-cli-conversion",
            "--reason",
            "Prove dry-run and apply conversion parity",
        ],
    )

    assert result.exit_code != 0
    assert "audited conversion" in _normalized_cli_output(result.output)
    assert "DRY RUN" not in result.output
    practice.refresh()
    assert practice.organization_id is None


def test_multi_practice_configuration_cli_dry_run_then_atomic_apply(
    tmp_path,
    monkeypatch,
):
    now = datetime.now(timezone.utc)
    domain = f"mcd-{secrets.token_hex(4)}.example.com"
    practices = [
        Practice(
            legal_name=f"MCD Practice {label}",
            join_code=f"C{secrets.token_hex(4).upper()[:7]}",
            email_domain=domain,
            origin_channel="mcd",
            is_solo=False,
            negotiated_baa_on_file=True,
            negotiated_baa_effective_date=now - timedelta(days=1),
            negotiated_baa_reference="test://current-mcd-baa",
            billing_exempt=True,
        ).save()
        for label in ("One", "Two")
    ]
    owners = [
        Doctor(
            clerk_id=f"user_multi_cli_{secrets.token_hex(8)}",
            email=f"owner{index}@{domain}",
            biography="",
        ).save()
        for index in (1, 2)
    ]
    for practice, owner in zip(practices, owners, strict=True):
        PracticeMember(
            practice_id=practice.id,
            doctor_id=owner.id,
            role="owner",
        ).save()
    workforce = Doctor(
        clerk_id=f"user_multi_cli_{secrets.token_hex(8)}",
        email=f"workforce@{domain}",
        biography="",
    ).save()
    manifest, raw = _private_manifest(
        tmp_path,
        {
            "schema_version": 2,
            "organization_slug": f"mcd-{secrets.token_hex(5)}",
            "email_domain": domain,
            "members": [
                {
                    "email": owners[0].email,
                    "target_practice_id": str(practices[0].id),
                },
                {
                    "email": owners[1].email,
                    "target_practice_id": str(practices[1].id),
                },
                {
                    "email": workforce.email,
                    "target_practice_id": str(practices[1].id),
                },
            ],
        },
        name="mcd-atomic-v2.json",
    )
    command = [
        "configure-provider-organization-with-roster",
        "--manifest",
        str(manifest),
        "--legal-name",
        "MyCatholicDoctor",
        "--operator-ref",
        "change:mcd-atomic-cli",
        "--reason",
        "Preserve two practices under one provider organization",
        "--source-ref",
        "test://mcd-atomic-v2",
    ]
    runner = CliRunner()

    dry_run = runner.invoke(app, command)

    assert dry_run.exit_code == 0, dry_run.output
    assert "DRY RUN" in dry_run.output
    assert "members=3" in dry_run.output
    assert all((owner.email or "") not in dry_run.output for owner in owners)
    expected_counts = ",".join(
        f"{practice_id}:{count}"
        for practice_id, count in sorted(
            ((str(practices[0].id), 1), (str(practices[1].id), 2))
        )
    )
    assert f"assignments_by_practice={expected_counts}" in dry_run.output
    roster_sha256 = hashlib.sha256(raw).hexdigest()
    roster_match = re.search(r"roster_sha256=([0-9a-f]{64})", dry_run.output)
    plan_match = re.search(r"plan_sha256=([0-9a-f]{64})", dry_run.output)
    assert roster_match
    assert roster_match.group(1) == roster_sha256
    assert plan_match
    assert plan_match.group(1) != roster_sha256
    assert ProviderOrganization.get(email_domain=domain) is None
    for practice in practices:
        practice.refresh()
        assert practice.organization_id is None

    missing_plan_code_path = tmp_path / "mcd-code-missing-plan.txt"
    missing_plan = runner.invoke(
        app,
        command
        + [
            "--confirm-roster-sha256",
            roster_sha256,
            "--access-code-output",
            str(missing_plan_code_path),
            "--apply",
        ],
    )
    assert missing_plan.exit_code != 0
    assert "--confirm-plan-sha256" in _normalized_cli_output(missing_plan.output)
    assert not missing_plan_code_path.exists()

    changed_reason_command = list(command)
    changed_reason_command[changed_reason_command.index("--reason") + 1] = (
        "A different mutation reason"
    )
    stale_plan_code_path = tmp_path / "mcd-code-stale-plan.txt"
    stale_plan = runner.invoke(
        app,
        changed_reason_command
        + [
            "--confirm-roster-sha256",
            roster_sha256,
            "--confirm-plan-sha256",
            plan_match.group(1),
            "--access-code-output",
            str(stale_plan_code_path),
            "--apply",
        ],
    )
    assert stale_plan.exit_code != 0
    assert "matching the dry-run digests" in stale_plan.output
    assert not stale_plan_code_path.exists()

    from app.services import organization_access as organization_access_service

    real_configure = (
        organization_access_service.configure_provider_organization_with_roster
    )

    def reject_before_commit(*_args, **_kwargs):
        raise OrganizationAccessError("known pre-commit rejection")

    monkeypatch.setattr(
        organization_access_service,
        "configure_provider_organization_with_roster",
        reject_before_commit,
    )
    rejected_code_path = tmp_path / "mcd-code-rejected.txt"
    rejected = runner.invoke(
        app,
        command
        + [
            "--confirm-roster-sha256",
            roster_sha256,
            "--confirm-plan-sha256",
            plan_match.group(1),
            "--access-code-output",
            str(rejected_code_path),
            "--apply",
        ],
    )
    assert rejected.exit_code != 0
    assert "cutover failed" in rejected.output
    assert not rejected_code_path.exists()

    sensitive_value = f"private@{domain}"

    def raise_ambiguous_cutover(*_args, **_kwargs):
        raise SQLAlchemyError(f"database parameter: {sensitive_value}")

    monkeypatch.setattr(
        organization_access_service,
        "configure_provider_organization_with_roster",
        raise_ambiguous_cutover,
    )
    ambiguous_code_path = tmp_path / "mcd-code-ambiguous.txt"
    ambiguous = runner.invoke(
        app,
        command
        + [
            "--confirm-roster-sha256",
            roster_sha256,
            "--confirm-plan-sha256",
            plan_match.group(1),
            "--access-code-output",
            str(ambiguous_code_path),
            "--apply",
        ],
    )
    ambiguous_output = _normalized_cli_output(ambiguous.output)
    assert ambiguous.exit_code != 0
    assert "outcome is ambiguous" in ambiguous_output
    assert "output file was retained" in ambiguous_output
    assert "Verify database state" in ambiguous_output
    assert sensitive_value not in ambiguous.output
    assert ambiguous_code_path.is_file()
    assert ambiguous_code_path.stat().st_mode & 0o777 == 0o600

    monkeypatch.setattr(
        organization_access_service,
        "configure_provider_organization_with_roster",
        real_configure,
    )
    code_path = tmp_path / "mcd-code.txt"
    applied = runner.invoke(
        app,
        command
        + [
            "--confirm-roster-sha256",
            roster_sha256,
            "--confirm-plan-sha256",
            plan_match.group(1),
            "--access-code-output",
            str(code_path),
            "--apply",
        ],
    )

    assert applied.exit_code == 0, applied.output
    assert "cutover applied" in applied.output
    assert f"created_by_practice={expected_counts}" in applied.output
    assert all((owner.email or "") not in applied.output for owner in owners)
    assert code_path.stat().st_mode & 0o777 == 0o600
    assert code_path.read_text().strip() not in applied.output
    organization = ProviderOrganization.get(email_domain=domain)
    assert organization is not None
    for practice in practices:
        practice.refresh()
        assert practice.organization_id == organization.id
    assert (
        OrganizationRosterApproval.where(
            OrganizationRosterApproval.organization_id == organization.id
        ).count()
        == 3
    )
    for practice in practices:
        configured_audit = cast(
            PracticeAccessAudit,
            PracticeAccessAudit.where(
                PracticeAccessAudit.action
                == PracticeAccessAction.provider_organization_configured.value,
                PracticeAccessAudit.practice_id == practice.id,
            ).one(),
        )
        assert configured_audit.evidence_sha256 == plan_match.group(1)
