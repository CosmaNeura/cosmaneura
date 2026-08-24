import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta, timezone

from click._compat import strip_ansi
from typer.testing import CliRunner

from app.cli import app
from app.services.clerk_cohort import ClerkCohortIdentity
from app.services.pilot_access_grandfather import (
    GrandfatherGrantInput,
    grant_pilot_access_grants,
    pilot_access_grant_plan_sha256,
    pilot_access_revoke_plan_sha256,
    revoke_pilot_access_grants,
    revoked_pilot_access_grant,
)

from app.models.doctor import Doctor, DoctorRole
from app.models.pilot_access_grandfather_grant import (
    PilotAccessGrandfatherGrant,
)
from app.models.superadmin_capability import (
    SuperadminCapabilityEnvironment,
    SuperadminCapabilityGrant,
    SuperadminCapabilityKind,
)
from sqlmodel import col

SOURCE_REF = "controlled://legacy-pilot-export/2026-08-23"
OPERATOR_REF = "change:legacy-pilot-access"
GRANT_REASON = "Preserve approved legacy pilot access"
REVOKE_REASON = "Approved pilot offboarding"
SUPERADMIN_ISSUER = "https://staging.example.clerk.accounts.dev"


def _identities(label: str) -> tuple[ClerkCohortIdentity, ...]:
    nonce = secrets.token_hex(5)
    return tuple(
        ClerkCohortIdentity(
            clerk_id=f"user_{label}_{nonce}_{index:02d}",
            email=f"pilot-{label}-{nonce}-{index:02d}@example.com",
        )
        for index in range(47)
    )


def _source_sha256(identities: tuple[ClerkCohortIdentity, ...]) -> str:
    canonical = "".join(
        f"{identity.clerk_id}\t{identity.email.lower()}\n"
        for identity in sorted(identities, key=lambda item: item.clerk_id)
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _grant_superadmin_capability(monkeypatch, doctor: Doctor) -> None:
    monkeypatch.setattr(
        "app.services.superadmin_capability.configured_superadmin_capability_environment",
        lambda: SuperadminCapabilityEnvironment.staging,
    )
    monkeypatch.setattr(
        "app.services.superadmin_capability.configured_superadmin_clerk_issuer",
        lambda: SUPERADMIN_ISSUER,
    )
    assert doctor.email is not None
    SuperadminCapabilityGrant(
        environment=SuperadminCapabilityEnvironment.staging,
        capability_kind=SuperadminCapabilityKind.platform_admin,
        clerk_issuer=SUPERADMIN_ISSUER,
        clerk_subject=doctor.clerk_id,
        doctor_id=doctor.id,
        issuer_sha256=hashlib.sha256(SUPERADMIN_ISSUER.encode()).hexdigest(),
        subject_sha256=hashlib.sha256(doctor.clerk_id.encode()).hexdigest(),
        verified_primary_email_sha256=hashlib.sha256(
            doctor.email.strip().lower().encode()
        ).hexdigest(),
        source_ref="controlled:pilot-cli-test",
        source_sha256="c" * 64,
        grant_plan_sha256="d" * 64,
        granted_by_ref="ticket:pilot-cli-test",
        grant_reason_code="reviewed_test_grant",
        granted_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    ).save()


def _grant_command(source_sha256: str) -> list[str]:
    return [
        "grant-pilot-access",
        "--source-ref",
        SOURCE_REF,
        "--source-sha256",
        source_sha256,
        "--operator-ref",
        OPERATOR_REF,
        "--reason",
        GRANT_REASON,
    ]


def _mock_live_cohort(
    monkeypatch,
    identities: tuple[ClerkCohortIdentity, ...],
    source_sha256: str,
) -> list[tuple[int, str]]:
    calls: list[tuple[int, str]] = []

    def resolve(*, expected_count: int, expected_pair_sha256: str):
        calls.append((expected_count, expected_pair_sha256))
        return identities

    monkeypatch.setattr(
        "app.services.clerk_cohort.resolve_live_clerk_cohort",
        resolve,
    )
    return calls


def _entries(
    identities: tuple[ClerkCohortIdentity, ...],
) -> list[GrandfatherGrantInput]:
    return [
        GrandfatherGrantInput(clerk_id=item.clerk_id, email=item.email)
        for item in identities
    ]


def _seed_grants(
    identities: tuple[ClerkCohortIdentity, ...],
    source_sha256: str,
):
    entries = _entries(identities)
    plan_sha256 = pilot_access_grant_plan_sha256(
        entries,
        source_ref=SOURCE_REF,
        source_sha256=source_sha256,
        operator_ref=OPERATOR_REF,
        reason=GRANT_REASON,
    )
    return grant_pilot_access_grants(
        entries,
        source_ref=SOURCE_REF,
        source_sha256=source_sha256,
        operator_ref=OPERATOR_REF,
        reason=GRANT_REASON,
        confirm_plan_sha256=plan_sha256,
    )


def _assert_no_identity_output(
    output: str,
    identities: tuple[ClerkCohortIdentity, ...],
) -> None:
    assert SOURCE_REF not in output
    for identity in identities:
        assert identity.clerk_id not in output
        assert identity.email not in output


def test_grant_cli_dry_run_apply_and_exact_retry_are_aggregate_only(monkeypatch):
    identities = _identities("grant-cli")
    source_sha256 = _source_sha256(identities)
    calls = _mock_live_cohort(monkeypatch, identities, source_sha256)
    Doctor(
        clerk_id=identities[0].clerk_id,
        email=identities[0].email.upper(),
        biography="",
    ).save()
    command = _grant_command(source_sha256)
    runner = CliRunner()

    dry_run = runner.invoke(app, command)

    assert dry_run.exit_code == 0, dry_run.output
    assert "state=deployable" in dry_run.output
    assert "accounts=47" in dry_run.output
    assert "existing_doctors=1" in dry_run.output
    assert "prelogin_accounts=46" in dry_run.output
    assert "already_granted=0" in dry_run.output
    assert PilotAccessGrandfatherGrant.count() == 0
    match = re.search(r"plan_sha256=([0-9a-f]{64})", dry_run.output)
    assert match
    _assert_no_identity_output(dry_run.output, identities)

    applied = runner.invoke(
        app,
        command + ["--confirm-plan-sha256", match.group(1), "--apply"],
    )

    assert applied.exit_code == 0, applied.output
    assert "grant applied" in applied.output
    assert "accounts=47" in applied.output
    assert "created=47" in applied.output
    assert "unchanged=0" in applied.output
    assert PilotAccessGrandfatherGrant.count() == 47
    _assert_no_identity_output(applied.output, identities)

    retried = runner.invoke(
        app,
        command + ["--confirm-plan-sha256", match.group(1), "--apply"],
    )

    assert retried.exit_code == 0, retried.output
    assert "already applied" in retried.output
    assert "created=0" in retried.output
    assert "unchanged=47" in retried.output
    assert PilotAccessGrandfatherGrant.count() == 47
    assert calls == [(47, source_sha256)] * 3
    _assert_no_identity_output(retried.output, identities)


def test_grant_cli_dry_run_fails_closed_on_database_mismatch_without_pii(
    monkeypatch,
):
    identities = _identities("mismatch-cli")
    source_sha256 = _source_sha256(identities)
    _mock_live_cohort(monkeypatch, identities, source_sha256)
    sensitive_stored_email = f"different-{secrets.token_hex(4)}@example.com"
    Doctor(
        clerk_id=identities[0].clerk_id,
        email=sensitive_stored_email,
        biography="",
    ).save()

    result = CliRunner().invoke(app, _grant_command(source_sha256))

    assert result.exit_code != 0
    normalized_output = " ".join(
        strip_ansi(result.output).replace("│", " ").split()
    )
    assert "validation failed" in normalized_output
    assert "no identities were printed" in normalized_output
    assert sensitive_stored_email not in result.output
    assert PilotAccessGrandfatherGrant.count() == 0
    _assert_no_identity_output(result.output, identities)


def test_grant_cli_redacts_live_clerk_failure(monkeypatch):
    sensitive_email = f"private-{secrets.token_hex(4)}@example.com"
    sensitive_clerk_id = f"user_private_{secrets.token_hex(6)}"

    def reject_cohort(*, expected_count: int, expected_pair_sha256: str):
        assert expected_count == 47
        assert expected_pair_sha256 == "7" * 64
        raise RuntimeError(f"upstream included {sensitive_email} {sensitive_clerk_id}")

    monkeypatch.setattr(
        "app.services.clerk_cohort.resolve_live_clerk_cohort",
        reject_cohort,
    )

    result = CliRunner().invoke(app, _grant_command("7" * 64))

    assert result.exit_code != 0
    assert "validation failed" in " ".join(result.output.split())
    assert sensitive_email not in result.output
    assert sensitive_clerk_id not in result.output
    assert SOURCE_REF not in result.output


def test_revoke_cli_hidden_confirmation_apply_and_retry_are_aggregate_only():
    identities = _identities("revoke-cli")
    source_sha256 = _source_sha256(identities)
    doctor = Doctor(
        clerk_id=identities[0].clerk_id,
        email=identities[0].email,
        biography="",
    ).save()
    _seed_grants(identities, source_sha256)
    command = [
        "revoke-pilot-access",
        "--operator-ref",
        OPERATOR_REF,
        "--reason",
        REVOKE_REASON,
    ]
    runner = CliRunner()

    prompt_input = f"{identities[0].clerk_id}\n{identities[0].email}\n"
    dry_run = runner.invoke(app, command, input=prompt_input)

    assert dry_run.exit_code == 0, dry_run.output
    assert "state=revocable" in dry_run.output
    assert "accounts=1" in dry_run.output
    match = re.search(r"plan_sha256=([0-9a-f]{64})", dry_run.output)
    assert match
    _assert_no_identity_output(dry_run.output, identities)

    applied = runner.invoke(
        app,
        command + ["--confirm-plan-sha256", match.group(1), "--apply"],
        input=prompt_input,
    )

    assert applied.exit_code == 0, applied.output
    assert "grant revoked" in applied.output
    assert "revoked=1" in applied.output
    assert "unchanged=0" in applied.output
    doctor.refresh()
    assert doctor.deleted_at is not None
    assert revoked_pilot_access_grant(identities[0].clerk_id) is not None
    _assert_no_identity_output(applied.output, identities)

    retried = runner.invoke(
        app,
        command + ["--confirm-plan-sha256", match.group(1), "--apply"],
        input=prompt_input,
    )

    assert retried.exit_code == 0, retried.output
    assert "grant already revoked" in retried.output
    assert "revoked=0" in retried.output
    assert "unchanged=1" in retried.output
    _assert_no_identity_output(retried.output, identities)


def test_revoke_cli_wrong_email_refuses_without_disclosure_or_mutation():
    identities = _identities("wrong-email-cli")
    source_sha256 = _source_sha256(identities)
    _seed_grants(identities, source_sha256)
    sensitive_wrong_email = f"wrong-{secrets.token_hex(4)}@example.com"

    result = CliRunner().invoke(
        app,
        [
            "revoke-pilot-access",
            "--operator-ref",
            OPERATOR_REF,
            "--reason",
            REVOKE_REASON,
        ],
        input=f"{identities[0].clerk_id}\n{sensitive_wrong_email}\n",
    )

    assert result.exit_code != 0
    assert "validation failed" in " ".join(result.output.split())
    assert sensitive_wrong_email not in result.output
    assert (
        PilotAccessGrandfatherGrant.where(
            col(PilotAccessGrandfatherGrant.revoked_at).is_(None)
        ).count()
        == 47
    )
    _assert_no_identity_output(result.output, identities)


def test_revoke_cli_dry_run_does_not_treat_legacy_admin_role_as_authority():
    identities = _identities("admin-revoke-cli")
    source_sha256 = _source_sha256(identities)
    doctor = Doctor(
        clerk_id=identities[0].clerk_id,
        email=identities[0].email,
        biography="",
        role=DoctorRole.admin,
    ).save()
    _seed_grants(identities, source_sha256)

    result = CliRunner().invoke(
        app,
        [
            "revoke-pilot-access",
            "--operator-ref",
            OPERATOR_REF,
            "--reason",
            REVOKE_REASON,
        ],
        input=f"{identities[0].clerk_id}\n{identities[0].email}\n",
    )

    assert result.exit_code == 0, result.output
    assert "state=revocable" in result.output
    doctor.refresh()
    assert doctor.deleted_at is None
    _assert_no_identity_output(result.output, identities)


def test_revoke_cli_dry_run_refuses_an_active_superadmin_capability(monkeypatch):
    identities = _identities("capability-revoke-cli")
    source_sha256 = _source_sha256(identities)
    doctor = Doctor(
        clerk_id=identities[0].clerk_id,
        email=identities[0].email,
        biography="",
    ).save()
    _seed_grants(identities, source_sha256)
    _grant_superadmin_capability(monkeypatch, doctor)

    result = CliRunner().invoke(
        app,
        [
            "revoke-pilot-access",
            "--operator-ref",
            OPERATOR_REF,
            "--reason",
            REVOKE_REASON,
        ],
        input=f"{identities[0].clerk_id}\n{identities[0].email}\n",
    )

    assert result.exit_code != 0
    assert "validation failed" in " ".join(result.output.split())
    doctor.refresh()
    assert doctor.deleted_at is None
    _assert_no_identity_output(result.output, identities)


def test_audit_cli_reports_only_aggregate_digest_state():
    identities = _identities("audit-cli")
    source_sha256 = _source_sha256(identities)
    Doctor(
        clerk_id=identities[0].clerk_id,
        email=identities[0].email,
        biography="",
    ).save()
    grant_result = _seed_grants(identities, source_sha256)
    revoke_plan_sha256 = pilot_access_revoke_plan_sha256(
        [identities[0].clerk_id],
        operator_ref=OPERATOR_REF,
        reason=REVOKE_REASON,
    )
    revoke_pilot_access_grants(
        [identities[0].clerk_id],
        operator_ref=OPERATOR_REF,
        reason=REVOKE_REASON,
        confirm_plan_sha256=revoke_plan_sha256,
    )

    result = CliRunner().invoke(app, ["audit-pilot-access"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload == {
        "active_count": 46,
        "amendment_account_count": 0,
        "amendment_count": 0,
        "amendment_plan_digests": [],
        "base_count": 47,
        "canonical_source_sha256": source_sha256,
        "cohort_integrity_valid": True,
        "doctor_snapshot_count": 1,
        "grant_plan_digests": [{"count": 47, "sha256": grant_result.plan_sha256}],
        "integrity_error_count": 0,
        "lineage_count": 47,
        "prelogin_snapshot_count": 46,
        "rebind_count": 0,
        "revocation_plan_digests": [{"count": 1, "sha256": revoke_plan_sha256}],
        "revoked_count": 1,
        "source_digests": [{"count": 47, "sha256": source_sha256}],
        "state": "mixed",
        "total_count": 47,
    }
    assert SOURCE_REF not in result.output
    assert OPERATOR_REF not in result.output
    assert GRANT_REASON not in result.output
    assert REVOKE_REASON not in result.output
    _assert_no_identity_output(result.output, identities)


def test_audit_cli_marks_a_partial_active_cohort_invalid():
    identities = _identities("partial-audit-cli")
    identity = identities[0]
    email = identity.email.lower()
    PilotAccessGrandfatherGrant(
        clerk_id=identity.clerk_id,
        email_snapshot=email,
        email_sha256=hashlib.sha256(email.encode()).hexdigest(),
        source_ref=SOURCE_REF,
        source_sha256=hashlib.sha256(
            f"{identity.clerk_id}\t{email}\n".encode()
        ).hexdigest(),
        grant_plan_sha256="b" * 64,
        granted_by_ref=OPERATOR_REF,
        grant_reason=GRANT_REASON,
        granted_at=datetime.now(timezone.utc),
    ).save()

    result = CliRunner().invoke(app, ["audit-pilot-access"])

    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["active_count"] == 1
    assert payload["total_count"] == 1
    assert payload["cohort_integrity_valid"] is False
    assert payload["state"] == "invalid"


def test_audit_cli_recomputes_and_rejects_a_false_uniform_plan_digest():
    identities = _identities("invalid-plan-audit-cli")
    source_sha256 = _source_sha256(identities)
    granted_at = datetime.now(timezone.utc)
    for identity in identities:
        email = identity.email.lower()
        PilotAccessGrandfatherGrant(
            clerk_id=identity.clerk_id,
            email_snapshot=email,
            email_sha256=hashlib.sha256(email.encode()).hexdigest(),
            source_ref=SOURCE_REF,
            source_sha256=source_sha256,
            grant_plan_sha256="f" * 64,
            granted_by_ref=OPERATOR_REF,
            grant_reason=GRANT_REASON,
            granted_at=granted_at,
        ).save()

    result = CliRunner().invoke(app, ["audit-pilot-access"])

    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["total_count"] == 47
    assert payload["canonical_source_sha256"] == source_sha256
    assert payload["cohort_integrity_valid"] is False
    assert payload["state"] == "invalid"
