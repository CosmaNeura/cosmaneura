import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import superadmin_capability
from app.services.superadmin_capability import (
    SuperadminCapabilityError,
    get_active_runtime_superadmin_capability_kinds,
    get_active_superadmin_capability,
    grant_superadmin_capabilities,
    load_private_superadmin_capability_manifest,
    parse_superadmin_capability_manifest,
    plan_superadmin_capability_grants,
    plan_superadmin_capability_revocations,
    revoke_superadmin_capabilities,
)

from activemodel.session_manager import get_session
from app.models.doctor import Doctor, DoctorRole
from app.models.superadmin_capability import (
    SuperadminCapabilityAudit,
    SuperadminCapabilityEnvironment,
    SuperadminCapabilityGrant,
    SuperadminCapabilityKind,
)

SOURCE_REF = "controlled:superadmin-test"
OPERATOR_REF = "ticket:superadmin-test"
GRANT_REASON = "reviewed_initial_grant"
REVOKE_REASON = "reviewed_access_revocation"
ISSUER = "https://staging.example.clerk.accounts.dev"


def _identity(*, role: DoctorRole = DoctorRole.normal):
    nonce = secrets.token_hex(6)
    subject = f"user_superadmin_{nonce}"
    email = f"superadmin-{nonce}@example.com"
    doctor = Doctor(
        clerk_id=subject,
        email=email,
        biography="",
        role=role,
    ).save()
    return doctor, subject, email


def _payload(doctor: Doctor, subject: str, email: str) -> dict[str, object]:
    return {
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


def _mock_live_clerk(monkeypatch, subject: str, email: str) -> None:
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
        primary_email_address_id="email_primary",
        email_addresses=[
            SimpleNamespace(
                id="email_primary",
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


def _grant(monkeypatch, *, role: DoctorRole = DoctorRole.normal):
    doctor, subject, email = _identity(role=role)
    _mock_live_clerk(monkeypatch, subject, email)
    manifest = parse_superadmin_capability_manifest(_payload(doctor, subject, email))
    plan = plan_superadmin_capability_grants(
        manifest,
        source_ref=SOURCE_REF,
        operator_ref=OPERATOR_REF,
        reason_code=GRANT_REASON,
    )
    result = grant_superadmin_capabilities(
        manifest,
        source_ref=SOURCE_REF,
        operator_ref=OPERATOR_REF,
        reason_code=GRANT_REASON,
        confirm_plan_sha256=plan.plan_sha256,
    )
    return doctor, subject, email, manifest, plan, result


def test_manifest_is_strict_canonical_and_private_file_requires_mode_0600(
    tmp_path: Path,
):
    doctor, subject, email = _identity()
    payload = _payload(doctor, subject, email)
    first = parse_superadmin_capability_manifest(payload)
    reordered = dict(payload)
    reordered["accounts"] = list(reversed(payload["accounts"]))  # type: ignore[arg-type]
    second = parse_superadmin_capability_manifest(reordered)
    assert first.source_sha256 == second.source_sha256

    manifest_path = tmp_path / "cohort.superadmin-capabilities.private.json"
    manifest_path.write_text(__import__("json").dumps(payload))
    manifest_path.chmod(0o644)
    with pytest.raises(SuperadminCapabilityError, match="mode-0600"):
        load_private_superadmin_capability_manifest(manifest_path)
    manifest_path.chmod(0o600)
    loaded = load_private_superadmin_capability_manifest(manifest_path)
    assert loaded.source_sha256 == first.source_sha256

    payload["unexpected"] = True
    with pytest.raises(SuperadminCapabilityError, match="shape"):
        parse_superadmin_capability_manifest(payload)


def test_grant_is_atomic_audited_idempotent_and_email_is_not_persisted(monkeypatch):
    doctor, subject, email, manifest, plan, result = _grant(monkeypatch)

    assert result.created_count == 2
    assert result.unchanged_count == 0
    assert SuperadminCapabilityGrant.count() == 2
    assert SuperadminCapabilityAudit.count() == 2
    grants = list(SuperadminCapabilityGrant.all())
    assert {grant.capability_kind for grant in grants} == {
        SuperadminCapabilityKind.platform_admin,
        SuperadminCapabilityKind.view_as,
    }
    assert all(grant.clerk_subject == subject for grant in grants)
    assert all(grant.doctor_id == doctor.id for grant in grants)
    assert all(
        grant.verified_primary_email_sha256
        == hashlib.sha256(email.encode()).hexdigest()
        for grant in grants
    )
    assert email not in " ".join(str(grant.model_dump()) for grant in grants)
    assert all(
        not hasattr(audit, "clerk_subject") for audit in SuperadminCapabilityAudit.all()
    )

    retried = grant_superadmin_capabilities(
        manifest,
        source_ref=SOURCE_REF,
        operator_ref=OPERATOR_REF,
        reason_code=GRANT_REASON,
        confirm_plan_sha256=plan.plan_sha256,
    )
    assert retried.created_count == 0
    assert retried.unchanged_count == 2
    assert SuperadminCapabilityAudit.count() == 2


def test_runtime_check_requires_every_binding_and_never_uses_doctor_role(monkeypatch):
    doctor, subject, _, _, _, _ = _grant(monkeypatch, role=DoctorRole.normal)
    ungranted_admin, admin_subject, _ = _identity(role=DoctorRole.admin)

    with get_session() as session:
        assert (
            get_active_superadmin_capability(
                session,
                issuer=ISSUER,
                subject=subject,
                doctor_id=doctor.id,
                environment=SuperadminCapabilityEnvironment.staging,
                capability_kind=SuperadminCapabilityKind.view_as,
            )
            is not None
        )
        assert (
            get_active_superadmin_capability(
                session,
                issuer=ISSUER,
                subject=admin_subject,
                doctor_id=ungranted_admin.id,
                environment=SuperadminCapabilityEnvironment.staging,
                capability_kind=SuperadminCapabilityKind.view_as,
            )
            is None
        )
        for override in (
            {"issuer": "https://other.example.clerk.accounts.dev"},
            {"subject": f"user_wrong_{secrets.token_hex(4)}"},
            {"doctor_id": ungranted_admin.id},
            {"environment": SuperadminCapabilityEnvironment.production},
        ):
            arguments = {
                "issuer": ISSUER,
                "subject": subject,
                "doctor_id": doctor.id,
                "environment": SuperadminCapabilityEnvironment.staging,
                "capability_kind": SuperadminCapabilityKind.view_as,
                **override,
            }
            assert get_active_superadmin_capability(session, **arguments) is None


def test_runtime_lifecycle_check_is_environment_scoped_and_fails_closed(
    monkeypatch,
):
    doctor, subject, _, _, _, _ = _grant(monkeypatch, role=DoctorRole.admin)

    with get_session() as session:
        assert get_active_runtime_superadmin_capability_kinds(
            session,
            subject=subject,
            doctor_id=doctor.id,
        ) == frozenset(
            {
                SuperadminCapabilityKind.platform_admin,
                SuperadminCapabilityKind.view_as,
            }
        )

    monkeypatch.setattr(
        superadmin_capability,
        "configured_superadmin_capability_environment",
        lambda: SuperadminCapabilityEnvironment.production,
    )
    with get_session() as session:
        assert not get_active_runtime_superadmin_capability_kinds(
            session,
            subject=subject,
            doctor_id=doctor.id,
        )

    monkeypatch.setattr(
        superadmin_capability,
        "configured_superadmin_capability_environment",
        lambda: SuperadminCapabilityEnvironment.staging,
    )
    monkeypatch.setattr(
        superadmin_capability,
        "configured_superadmin_clerk_issuer",
        lambda: "https://different.example.clerk.accounts.dev",
    )
    with get_session() as session:
        with pytest.raises(SuperadminCapabilityError, match="ambiguous"):
            get_active_runtime_superadmin_capability_kinds(
                session,
                subject=subject,
                doctor_id=doctor.id,
            )


def test_live_clerk_mismatch_and_wrong_confirmation_leave_no_state(monkeypatch):
    doctor, subject, email = _identity()
    _mock_live_clerk(monkeypatch, subject, f"different-{email}")
    manifest = parse_superadmin_capability_manifest(_payload(doctor, subject, email))
    with pytest.raises(SuperadminCapabilityError, match="live Clerk"):
        plan_superadmin_capability_grants(
            manifest,
            source_ref=SOURCE_REF,
            operator_ref=OPERATOR_REF,
            reason_code=GRANT_REASON,
        )
    assert SuperadminCapabilityGrant.count() == 0
    assert SuperadminCapabilityAudit.count() == 0

    _mock_live_clerk(monkeypatch, subject, email)
    with pytest.raises(SuperadminCapabilityError, match="confirmed plan"):
        grant_superadmin_capabilities(
            manifest,
            source_ref=SOURCE_REF,
            operator_ref=OPERATOR_REF,
            reason_code=GRANT_REASON,
            confirm_plan_sha256="f" * 64,
        )
    assert SuperadminCapabilityGrant.count() == 0
    assert SuperadminCapabilityAudit.count() == 0


def test_revocation_is_atomic_retained_audited_and_idempotent(monkeypatch):
    doctor, subject, _, manifest, _, _ = _grant(monkeypatch)
    plan = plan_superadmin_capability_revocations(
        manifest,
        source_ref="controlled:revocation-test",
        operator_ref=OPERATOR_REF,
        reason_code=REVOKE_REASON,
    )
    # Revocation must follow the grant created above regardless of wall-clock
    # test execution time.
    now = datetime.now(timezone.utc) + timedelta(seconds=1)
    result = revoke_superadmin_capabilities(
        manifest,
        source_ref="controlled:revocation-test",
        operator_ref=OPERATOR_REF,
        reason_code=REVOKE_REASON,
        confirm_plan_sha256=plan.plan_sha256,
        now=now,
    )
    assert result.revoked_count == 2
    assert SuperadminCapabilityGrant.count() == 2
    assert SuperadminCapabilityAudit.count() == 4
    assert all(grant.revoked_at == now for grant in SuperadminCapabilityGrant.all())
    assert all(
        grant.retain_until is not None for grant in SuperadminCapabilityGrant.all()
    )

    with get_session() as session:
        assert (
            get_active_superadmin_capability(
                session,
                issuer=ISSUER,
                subject=subject,
                doctor_id=doctor.id,
                environment=SuperadminCapabilityEnvironment.staging,
                capability_kind=SuperadminCapabilityKind.platform_admin,
            )
            is None
        )

    retried = revoke_superadmin_capabilities(
        manifest,
        source_ref="controlled:revocation-test",
        operator_ref=OPERATOR_REF,
        reason_code=REVOKE_REASON,
        confirm_plan_sha256=plan.plan_sha256,
        now=now,
    )
    assert retried.revoked_count == 0
    assert retried.unchanged_count == 2
    assert SuperadminCapabilityAudit.count() == 4


def test_configured_environment_fails_closed_in_test_process():
    with pytest.raises(SuperadminCapabilityError, match="unavailable"):
        superadmin_capability.configured_superadmin_capability_environment()
