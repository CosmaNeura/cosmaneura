from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import sys
import urllib.parse
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "p0_staging_evidence.py"


def _load_evidence() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "standalone_p0_staging_evidence",
        SCRIPT,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_ca_root(tmp_path: Path) -> Path:
    ca_root = tmp_path / "staging-trust-root.pem"
    ca_root.write_text(
        "-----BEGIN CERTIFICATE-----\nprivate-test-certificate\n"
        "-----END CERTIFICATE-----\n"
    )
    ca_root.chmod(0o644)
    return ca_root


def _staging_database_url(ca_root: Path, *, sslmode: str = "verify-full") -> str:
    query = urllib.parse.urlencode(
        {"sslmode": sslmode, "sslrootcert": os.fspath(ca_root)}
    )
    return (
        "postgresql://staging_user:private-password@"
        "cosmaneura-staging-postgres.postgres.database.azure.com:5432/"
        f"staging?{query}"
    )


def _rows(module: ModuleType) -> dict[str, list[dict[str, Any]]]:
    rows = {spec.name: [] for spec in module.TABLE_SPECS}
    rows["doctor"] = [
        {
            "id": "doctor_private_one",
            "clerk_id": "user_private_one",
            "email": "private-one@example.invalid",
            "role": "admin",
            "last_active_at": "2026-08-24T12:00:00+00:00",
            "api_key": "sk_live_private",
            "has_natmed_subscription": False,
            "terminology": "doctor_patient",
            "onboarding_completed_at": "2026-08-20T12:00:00+00:00",
            "deleted_at": None,
            "created_at": "2026-08-01T12:00:00+00:00",
            "updated_at": "2026-08-24T12:00:00+00:00",
        },
        {
            "id": "doctor_private_two",
            "clerk_id": "user_private_two",
            "email": "private-two@example.invalid",
            "role": "normal",
            "last_active_at": None,
            "api_key": None,
            "has_natmed_subscription": False,
            "terminology": "doctor_patient",
            "onboarding_completed_at": None,
            "deleted_at": "2026-08-23T12:00:00+00:00",
            "created_at": "2026-08-02T12:00:00+00:00",
            "updated_at": "2026-08-23T12:00:00+00:00",
        },
    ]
    rows["baa_version"] = [{"id": "baa_version_private", "is_current": True}]
    rows["baa_acceptance"] = [
        {
            "id": "baa_acceptance_private",
            "accepted_by_email": "baa-private@example.invalid",
            "accepted_by_name": "Private Person",
        }
    ]
    rows["practice"] = [
        {
            "id": "practice_private",
            "is_solo": False,
            "join_code": "PRIVATECODE",
            "email_domain": "private.example.invalid",
            "organization_id": "provider_organization_private",
            "baa_version": None,
            "baa_accepted_at": None,
            "baa_accepted_by_doctor_id": None,
            "baa_signature_name": None,
            "baa_signature_title": None,
            "billing_exempt": True,
            "billing_exempt_until": None,
            "negotiated_baa_on_file": True,
            "negotiated_baa_required": True,
            "negotiated_baa_effective_date": "2026-08-01T12:00:00+00:00",
            "negotiated_baa_reference": "controlled:private-baa",
            "stripe_customer_id": "cus_private",
            "stripe_subscription_id": None,
            "subscription_status": None,
            "stripe_last_event_created_at": None,
            "stripe_last_event_id": None,
            "stripe_state_version": 0,
            "stripe_checkout_attempt": 0,
            "stripe_checkout_state_version": 0,
            "stripe_checkout_session_id": None,
            "stripe_checkout_session_url": None,
            "stripe_checkout_session_expires_at": None,
            "stripe_quantity_sync_required": False,
            "stripe_quantity_sync_target": None,
            "stripe_quantity_sync_version": 0,
            "stripe_quantity_sync_attempts": 0,
            "stripe_quantity_sync_error": None,
            "stripe_quantity_synced_at": None,
            "trial_ends_at": None,
        }
    ]
    rows["practice_member"] = [
        {
            "id": "practice_member_private_active",
            "practice_id": "practice_private",
            "doctor_id": "doctor_private_one",
            "role": "owner",
            "attested_at": None,
            "attestation_name": None,
            "organization_roster_approval_id": "organization_roster_private",
            "organization_authorized_at": "2026-08-24T12:00:00+00:00",
            "access_revoked_at": None,
            "access_revoked_by_ref": None,
            "access_revocation_reason": None,
            "access_records_retain_until": None,
        },
        {
            "id": "practice_member_private_revoked",
            "practice_id": "practice_private",
            "doctor_id": "doctor_private_two",
            "role": "workforce",
            "attested_at": "2026-08-01T12:00:00+00:00",
            "attestation_name": "Private Person",
            "organization_roster_approval_id": None,
            "organization_authorized_at": None,
            "access_revoked_at": "2026-08-23T12:00:00+00:00",
            "access_revoked_by_ref": "change:private",
            "access_revocation_reason": "private reason",
            "access_records_retain_until": "2033-08-23T12:00:00+00:00",
        },
    ]
    rows["provider_organization"] = [
        {
            "id": "provider_organization_private",
            "email_domain": "private.example.invalid",
            "status": "active",
            "access_code_digest": "private-access-code-digest",
        }
    ]
    rows["organization_roster_approval"] = [
        {
            "id": "organization_roster_private",
            "approved_email": "roster-private@example.invalid",
            "claimed_doctor_id": "doctor_private_one",
            "revoked_at": None,
        }
    ]
    rows["practice_access_audit"] = [
        {"id": "practice_access_audit_private", "action": "access_granted"}
    ]
    rows["doctor_identity_rebind_audit"] = [
        {
            "id": "authority_audit_private_rebind",
            "event_type": "platform.doctor_identity.rebound",
            "resource_ref": "doctor-identity-rebind:private",
        }
    ]
    rows["doctor_video_integration"] = [
        {
            "id": "video_integration_private",
            "doctor_id": "doctor_private_one",
            "provider": "zoom",
            "provider_user_id": "provider_user_private",
            "access_token": "zoom_access_private",
            "refresh_token": "zoom_refresh_private",
        }
    ]
    rows["authority_actor_external_identity"] = [
        {
            "id": "authority_external_identity_private",
            "actor_id": "authority_actor_private",
            "issuer": "https://private.clerk.invalid",
            "subject": "user_private_one",
            "revoked_at": None,
        }
    ]
    rows["authority_person_binding"] = [
        {
            "id": "authority_person_binding_private",
            "person_id": "authority_person_private",
            "doctor_id": "doctor_private_one",
            "status": "active",
        }
    ]
    rows["authority_tenant_membership"] = [
        {
            "id": "authority_tenant_membership_private",
            "tenant_id": "authority_tenant_private",
            "actor_id": "authority_actor_private",
            "status": "active",
        }
    ]
    rows["pilot_access_grandfather_grant"] = [
        {
            "id": "pilot_grant_private_active",
            "clerk_id": "user_private_one",
            "doctor_id_snapshot": "doctor_private_one",
            "email_snapshot": "private-one@example.invalid",
            "revoked_at": None,
        },
        {
            "id": "pilot_grant_private_revoked",
            "clerk_id": "user_private_two",
            "doctor_id_snapshot": None,
            "email_snapshot": "private-two@example.invalid",
            "revoked_at": "2026-08-23T12:00:00+00:00",
        },
        {
            "id": "pilot_grant_private_amendment",
            "clerk_id": "user_private_three",
            "doctor_id_snapshot": "doctor_private_three",
            "practice_id_snapshot": None,
            "email_snapshot": "private-three@example.invalid",
            "email_sha256": module._plain_sha256("private-three@example.invalid"),
            "source_ref": "review:private-continuity",
            "source_sha256": "f" * 64,
            "grant_plan_sha256": "a" * 64,
            "granted_by_ref": "operator:private",
            "grant_reason": "Reviewed operational omission",
            "granted_at": "2026-08-24T12:00:00+00:00",
            "revoked_at": None,
        },
    ]
    rows["pilot_access_continuity_amendment"] = [
        {
            "id": "pilot_amendment_private",
            "environment": "staging",
            "clerk_issuer": "https://clerk.staging.invalid",
            "source_ref": "review:private-continuity",
            "manifest_sha256": "f" * 64,
            "account_count": 1,
            "plan_sha256": "a" * 64,
            "applied_by_ref": "operator:private",
            "reason": "Reviewed operational omission",
            "applied_at": "2026-08-24T12:00:00+00:00",
        }
    ]
    rows["pilot_access_continuity_amendment_identity"] = [
        {
            "id": "pilot_amendment_identity_private",
            "amendment_id": "pilot_amendment_private",
            "clerk_subject": "user_private_three",
            "grant_id": "pilot_grant_private_amendment",
            "clerk_issuer": "https://clerk.staging.invalid",
            "email_sha256": module._plain_sha256("private-three@example.invalid"),
            "doctor_id_snapshot": "doctor_private_three",
            "practice_id_snapshot": None,
            "safe_prelogin": False,
            "continuity_basis": "reviewed_operational_user_omission",
        }
    ]
    rows["superadmin_capability_grant"] = [
        {
            "id": "superadmin_private_platform",
            "environment": "staging",
            "capability_kind": "platform_admin",
            "clerk_issuer": "https://clerk.staging.invalid",
            "clerk_subject": "user_private_one",
            "doctor_id": "doctor_private_one",
            "issuer_sha256": module._plain_sha256("https://clerk.staging.invalid"),
            "subject_sha256": module._plain_sha256("user_private_one"),
            "verified_primary_email_sha256": module._plain_sha256(
                "private-one@example.invalid"
            ),
            "source_ref": "review:superadmin-batch",
            "source_sha256": "1" * 64,
            "grant_plan_sha256": "b" * 64,
            "granted_by_ref": "operator:private",
            "grant_reason_code": "initial.reviewed.grant",
            "granted_at": "2026-08-24T12:00:00+00:00",
            "revoked_at": None,
        },
        {
            "id": "superadmin_private_view",
            "environment": "staging",
            "capability_kind": "view_as",
            "clerk_issuer": "https://clerk.staging.invalid",
            "clerk_subject": "user_private_one",
            "doctor_id": "doctor_private_one",
            "issuer_sha256": module._plain_sha256("https://clerk.staging.invalid"),
            "subject_sha256": module._plain_sha256("user_private_one"),
            "verified_primary_email_sha256": module._plain_sha256(
                "private-one@example.invalid"
            ),
            "source_ref": "review:superadmin-batch",
            "source_sha256": "1" * 64,
            "grant_plan_sha256": "b" * 64,
            "granted_by_ref": "operator:private",
            "grant_reason_code": "initial.reviewed.grant",
            "granted_at": "2026-08-24T12:00:00+00:00",
            "revoked_at": None,
        },
        {
            "id": "superadmin_private_revoked",
            "environment": "staging",
            "capability_kind": "view_as",
            "clerk_issuer": "https://clerk.staging.invalid",
            "clerk_subject": "user_private_two",
            "doctor_id": "doctor_private_two",
            "issuer_sha256": module._plain_sha256("https://clerk.staging.invalid"),
            "subject_sha256": module._plain_sha256("user_private_two"),
            "verified_primary_email_sha256": module._plain_sha256(
                "private-two@example.invalid"
            ),
            "source_ref": "review:retired-superadmin",
            "source_sha256": "2" * 64,
            "grant_plan_sha256": "c" * 64,
            "granted_by_ref": "operator:private",
            "grant_reason_code": "historical.reviewed.grant",
            "granted_at": "2026-08-22T12:00:00+00:00",
            "revoked_at": "2026-08-23T12:00:00+00:00",
            "revoked_by_ref": "operator:private",
            "revocation_reason_code": "reviewed.revocation",
            "revocation_plan_sha256": "e" * 64,
        },
        {
            "id": "superadmin_private_wrong_environment",
            "environment": "production",
            "capability_kind": "view_as",
            "clerk_subject": "user_private_three",
            "grant_plan_sha256": "d" * 64,
            "revoked_at": None,
        },
    ]
    rows["superadmin_capability_audit"] = [
        {
            "grant_id": "superadmin_private_platform",
            "environment": "staging",
            "capability_kind": "platform_admin",
            "action": "granted",
            "evidence_sha256": "b" * 64,
            "identity_binding_sha256": "0" * 64,
            "operator_ref": "operator:private",
            "reason_code": "initial.reviewed.grant",
            "occurred_at": "2026-08-24T12:00:00+00:00",
            "recorded_at": "2026-08-24T12:00:00+00:00",
        },
        {
            "grant_id": "superadmin_private_view",
            "environment": "staging",
            "capability_kind": "view_as",
            "action": "granted",
            "evidence_sha256": "b" * 64,
            "identity_binding_sha256": "0" * 64,
            "operator_ref": "operator:private",
            "reason_code": "initial.reviewed.grant",
            "occurred_at": "2026-08-24T12:00:00+00:00",
            "recorded_at": "2026-08-24T12:00:00+00:00",
        },
        {
            "grant_id": "superadmin_private_revoked",
            "environment": "staging",
            "capability_kind": "view_as",
            "action": "granted",
            "evidence_sha256": "c" * 64,
            "identity_binding_sha256": "0" * 64,
            "operator_ref": "operator:private",
            "reason_code": "historical.reviewed.grant",
            "occurred_at": "2026-08-22T12:00:00+00:00",
            "recorded_at": "2026-08-22T12:00:00+00:00",
        },
        {
            "grant_id": "superadmin_private_revoked",
            "environment": "staging",
            "capability_kind": "view_as",
            "action": "revoked",
            "evidence_sha256": "e" * 64,
            "identity_binding_sha256": "0" * 64,
            "operator_ref": "operator:private",
            "reason_code": "reviewed.revocation",
            "occurred_at": "2026-08-23T12:00:00+00:00",
            "recorded_at": "2026-08-23T12:00:00+00:00",
        },
        {
            "environment": "production",
            "action": "granted",
            "evidence_sha256": "d" * 64,
        },
    ]

    amendment = rows["pilot_access_continuity_amendment"][0]
    amendment_identity = rows["pilot_access_continuity_amendment_identity"][0]
    amendment_grant = rows["pilot_access_grandfather_grant"][2]
    continuity_plan_sha256 = module._canonical_sha256(
        {
            "action": "pilot_access_continuity_amendment",
            "environment": amendment["environment"],
            "identities": [
                {
                    "continuity_basis": amendment_identity["continuity_basis"],
                    "doctor_id": amendment_identity["doctor_id_snapshot"],
                    "email_sha256": amendment_identity["email_sha256"],
                    "issuer_sha256": module._plain_sha256(
                        amendment_identity["clerk_issuer"]
                    ),
                    "practice_id": amendment_identity["practice_id_snapshot"],
                    "subject_sha256": module._plain_sha256(
                        amendment_identity["clerk_subject"]
                    ),
                }
            ],
            "manifest_sha256": amendment["manifest_sha256"],
            "operator_ref": amendment["applied_by_ref"],
            "reason": amendment["reason"],
            "schema_version": 1,
            "source_ref": amendment["source_ref"],
        }
    )
    amendment["plan_sha256"] = continuity_plan_sha256
    amendment_grant["grant_plan_sha256"] = continuity_plan_sha256

    staging_grants = {
        grant["id"]: grant
        for grant in rows["superadmin_capability_grant"]
        if grant["environment"] == "staging"
    }
    staging_audits = [
        audit
        for audit in rows["superadmin_capability_audit"]
        if audit["environment"] == "staging"
    ]

    def bind_superadmin_plan(grant_ids: tuple[str, ...]) -> None:
        grants = [staging_grants[grant_id] for grant_id in grant_ids]
        first = grants[0]
        plan_sha256 = module._canonical_sha256(
            {
                "action": "grant",
                "environment": first["environment"],
                "issuer_sha256": module._plain_sha256(first["clerk_issuer"]),
                "operator_ref": first["granted_by_ref"],
                "reason_code": first["grant_reason_code"],
                "schema_version": 1,
                "source_ref": first["source_ref"],
                "source_sha256": first["source_sha256"],
                "targets": [
                    {
                        "binding_sha256": (
                            module._superadmin_identity_binding_sha256(grant)
                        ),
                        "verified_primary_email_sha256": grant[
                            "verified_primary_email_sha256"
                        ],
                    }
                    for grant in sorted(
                        grants,
                        key=lambda row: (
                            row["clerk_subject"],
                            row["capability_kind"],
                        ),
                    )
                ],
            }
        )
        for grant in grants:
            grant["grant_plan_sha256"] = plan_sha256
            for audit in staging_audits:
                if audit["grant_id"] != grant["id"]:
                    continue
                audit["identity_binding_sha256"] = (
                    module._superadmin_identity_binding_sha256(grant)
                )
                if audit["action"] == "granted":
                    audit["evidence_sha256"] = plan_sha256

    bind_superadmin_plan(("superadmin_private_platform", "superadmin_private_view"))
    bind_superadmin_plan(("superadmin_private_revoked",))
    rows["view_as_session"] = [
        {
            "id": "view_as_session_private_open",
            "environment": "staging",
            "ended_at": None,
            "end_reason": None,
        },
        {
            "id": "view_as_session_private_revoked",
            "environment": "staging",
            "ended_at": "2026-08-24T12:15:00+00:00",
            "end_reason": "capability_revoked",
        },
        {
            "id": "view_as_session_private_production",
            "environment": "production",
            "ended_at": None,
            "end_reason": None,
        },
    ]
    rows["view_as_audit_event"] = [
        {"environment": "staging", "event_type": "began", "outcome": "accepted"},
        {
            "environment": "staging",
            "event_type": "rejected",
            "outcome": "denied",
        },
        {"environment": "production", "event_type": "began", "outcome": "accepted"},
    ]
    return rows


class FakeResult:
    def __init__(self, rows: Sequence[tuple[Any, ...]]):
        self.rows = rows

    def fetchall(self) -> Sequence[tuple[Any, ...]]:
        return self.rows


class FakeConnection:
    def __init__(self, returned: Sequence[Sequence[tuple[Any, ...]]]):
        self.returned = iter(returned)
        self.queries: list[str] = []

    def execute(self, query: str) -> FakeResult:
        self.queries.append(query)
        return FakeResult(next(self.returned))


class FakeManagedConnection(FakeConnection):
    def __init__(self, returned: Sequence[Sequence[tuple[Any, ...]]]):
        super().__init__(returned)
        self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> bool:
        return False

    def rollback(self) -> None:
        self.rolled_back = True


def test_script_import_is_isolated_from_app_and_alembic() -> None:
    probe = f"""
import importlib.util
import json
import sys
spec = importlib.util.spec_from_file_location('p0_evidence_probe', {str(SCRIPT)!r})
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
forbidden = sorted(
    name for name in sys.modules
    if name == 'app' or name.startswith('app.')
    or name == 'alembic' or name.startswith('alembic.')
)
print(json.dumps(forbidden))
raise SystemExit(bool(forbidden))
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-c", probe],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == []


def test_snapshot_is_deterministic_aggregate_only_and_sensitive_to_state() -> None:
    evidence = _load_evidence()
    rows = _rows(evidence)
    key = b"k" * 32

    first = evidence.build_snapshot(rows, key, phase="post")
    reordered = copy.deepcopy(rows)
    for table_rows in reordered.values():
        table_rows.reverse()
    second = evidence.build_snapshot(reordered, key, phase="post")

    assert first == second
    serialized = json.dumps(first, sort_keys=True)
    for private_value in (
        "doctor_private_one",
        "user_private_one",
        "private-one@example.invalid",
        "Private Person",
        "PRIVATECODE",
        "sk_live_private",
        "cus_private",
        "zoom_access_private",
        "https://private.clerk.invalid",
    ):
        assert private_value not in serialized

    sections = first["sections"]
    assert sections["baa"]["acceptances"]["count"] == 1
    assert sections["doctor_account_state"]["onboarding_completed_count"] == 1
    assert sections["practice_business_state"]["memberships"]["revoked_count"] == 1
    assert sections["continuity"]["grants"]["active_count"] == 2
    assert sections["continuity"]["grants"]["revoked_count"] == 1
    assert sections["continuity"]["base_grants"]["count"] == 2
    assert sections["continuity"]["base_grants"]["active_count"] == 1
    assert sections["continuity"]["amendment_grants"]["count"] == 1
    assert sections["identity_recovery"]["rebind_audit"]["count"] == 1
    assert sections["identity_recovery"]["authority_external_identities"]["count"] == 1
    assert sections["provider_credentials"]["video_integrations"]["count"] == 1
    capability_counts = sections["superadmin_capabilities"]["grants"]
    assert capability_counts["active_count"] == 2
    assert capability_counts["non_staging_count"] == 1
    assert capability_counts["platform_admin_active_count"] == 1
    assert capability_counts["revoked_count"] == 1
    assert capability_counts["view_as_active_count"] == 1
    assert sections["view_as"]["sessions"]["unended_count"] == 1
    assert sections["view_as"]["sessions"]["capability_revoked_count"] == 1
    assert sections["view_as"]["audit"]["denied_count"] == 1

    changed = copy.deepcopy(rows)
    changed["doctor"][0]["onboarding_completed_at"] = "2026-08-25T12:00:00+00:00"
    third = evidence.build_snapshot(changed, key, phase="post")
    third_doctor_state = third["sections"]["doctor_account_state"]
    assert third_doctor_state["count"] == 2
    assert (
        third_doctor_state["state_hmac_sha256"]
        != sections["doctor_account_state"]["state_hmac_sha256"]
    )
    assert (
        third_doctor_state["onboarding_state_hmac_sha256"]
        != sections["doctor_account_state"]["onboarding_state_hmac_sha256"]
    )
    assert third["snapshot_hmac_sha256"] != first["snapshot_hmac_sha256"]

    activity_only = copy.deepcopy(rows)
    activity_only["doctor"][0]["last_active_at"] = "2026-08-26T12:00:00+00:00"
    fourth = evidence.build_snapshot(activity_only, key, phase="post")
    assert (
        fourth["sections"]["doctor_account_state"]["onboarding_state_hmac_sha256"]
        == sections["doctor_account_state"]["onboarding_state_hmac_sha256"]
    )
    assert (
        fourth["sections"]["doctor_account_state"][
            "identity_authority_state_hmac_sha256"
        ]
        == sections["doctor_account_state"]["identity_authority_state_hmac_sha256"]
    )

    identity_changed = copy.deepcopy(rows)
    identity_changed["doctor"][0]["email"] = "changed@example.invalid"
    with pytest.raises(evidence.EvidenceFailure, match="identity binding"):
        evidence.build_snapshot(identity_changed, key, phase="post")

    authority_state_changed = copy.deepcopy(rows)
    authority_state_changed["doctor"][0]["api_key"] = "rotated-private-key"
    fifth = evidence.build_snapshot(authority_state_changed, key, phase="post")
    assert (
        fifth["sections"]["doctor_account_state"][
            "identity_authority_state_hmac_sha256"
        ]
        != sections["doctor_account_state"]["identity_authority_state_hmac_sha256"]
    )

    rotated_provider_token = copy.deepcopy(rows)
    rotated_provider_token["doctor_video_integration"][0]["access_token"] = (
        "rotated-private-token"
    )
    sixth = evidence.build_snapshot(rotated_provider_token, key, phase="post")
    assert (
        sixth["sections"]["provider_credentials"]["video_integrations"][
            "stable_binding_state_hmac_sha256"
        ]
        == sections["provider_credentials"]["video_integrations"][
            "stable_binding_state_hmac_sha256"
        ]
    )

    changed_provider_binding = copy.deepcopy(rows)
    changed_provider_binding["doctor_video_integration"][0]["provider_user_id"] = (
        "provider_user_changed"
    )
    seventh = evidence.build_snapshot(changed_provider_binding, key, phase="post")
    assert (
        seventh["sections"]["provider_credentials"]["video_integrations"][
            "stable_binding_state_hmac_sha256"
        ]
        != sections["provider_credentials"]["video_integrations"][
            "stable_binding_state_hmac_sha256"
        ]
    )


def test_snapshot_rejects_continuity_and_superadmin_binding_drift() -> None:
    evidence = _load_evidence()
    rows = _rows(evidence)
    key = b"k" * 32

    continuity_drift = copy.deepcopy(rows)
    continuity_drift["pilot_access_continuity_amendment_identity"][0][
        "clerk_subject"
    ] = "user_private_mismatch"
    with pytest.raises(evidence.EvidenceFailure, match="continuity grant binding"):
        evidence.build_snapshot(continuity_drift, key, phase="post")

    audit_drift = copy.deepcopy(rows)
    audit_drift["superadmin_capability_audit"][0]["operator_ref"] = "operator:mismatch"
    with pytest.raises(evidence.EvidenceFailure, match="superadmin audit binding"):
        evidence.build_snapshot(audit_drift, key, phase="post")


def test_table_reader_uses_only_fixed_selects_and_validates_shape() -> None:
    evidence = _load_evidence()
    presence = [(name, "r", True) for name in sorted(evidence.P0_SCHEMA_TABLES)]
    returned: list[Sequence[tuple[Any, ...]]] = [presence]
    returned.extend([[({"marker": spec.name},)] for spec in evidence.TABLE_SPECS])
    connection = FakeConnection(returned)

    snapshot_rows = evidence.read_snapshot_rows(connection, phase="post")
    rows = snapshot_rows.tables

    assert set(rows) == {spec.name for spec in evidence.TABLE_SPECS}
    assert snapshot_rows.p0_schema_presence == {
        name: True for name in sorted(evidence.P0_SCHEMA_TABLES)
    }
    assert len(connection.queries) == len(evidence.TABLE_SPECS) + 1
    assert connection.queries[0] == evidence.P0_SCHEMA_PRESENCE_QUERY
    assert "pg_catalog.pg_class" in connection.queries[0]
    assert "namespace.nspname = 'public'" in connection.queries[0]
    for query in connection.queries[1:]:
        normalized = " ".join(query.lower().split())
        assert normalized.startswith("select to_jsonb(")
        assert " from public." in normalized
        assert all(
            token not in normalized for token in (" insert ", " update ", " delete ")
        )

    invalid = FakeConnection([[("not-a-schema-table", "r", True)]])
    with pytest.raises(evidence.EvidenceFailure, match="invalid shape"):
        evidence.read_snapshot_rows(invalid, phase="post")


def test_table_reader_supports_expected_pre_deploy_schema_absence() -> None:
    evidence = _load_evidence()
    preexisting_specs = [
        spec
        for spec in evidence.TABLE_SPECS
        if spec.name not in evidence.P0_SCHEMA_TABLES
    ]
    returned: list[Sequence[tuple[Any, ...]]] = [
        [(name, None, None) for name in sorted(evidence.P0_SCHEMA_TABLES)]
    ]
    returned.extend([[({"marker": spec.name},)] for spec in preexisting_specs])
    connection = FakeConnection(returned)

    snapshot_rows = evidence.read_snapshot_rows(connection, phase="pre")

    assert snapshot_rows.p0_schema_presence == {
        name: False for name in sorted(evidence.P0_SCHEMA_TABLES)
    }
    assert all(snapshot_rows.tables[name] == [] for name in evidence.P0_SCHEMA_TABLES)
    assert len(connection.queries) == len(preexisting_specs) + 1
    queried = "\n".join(connection.queries[1:])
    assert all(name not in queried for name in evidence.P0_SCHEMA_TABLES)

    pre_deploy_rows = _rows(evidence)
    for name in evidence.P0_SCHEMA_TABLES:
        pre_deploy_rows[name] = []
    snapshot = evidence.build_snapshot(
        pre_deploy_rows,
        b"k" * 32,
        phase="pre",
        p0_schema_presence=snapshot_rows.p0_schema_presence,
    )
    assert snapshot["p0_schema"]["present_count"] == 0


def test_table_reader_rejects_permission_hidden_and_partial_p0_schema() -> None:
    evidence = _load_evidence()
    names = sorted(evidence.P0_SCHEMA_TABLES)

    unreadable = [(name, "r", name != names[0]) for name in names]
    with pytest.raises(evidence.EvidenceFailure, match="not readable"):
        evidence.read_snapshot_rows(FakeConnection([unreadable]), phase="post")

    partial = [
        (name, "r", True) if name == names[0] else (name, None, None) for name in names
    ]
    with pytest.raises(evidence.EvidenceFailure, match="capture phase"):
        evidence.read_snapshot_rows(FakeConnection([partial]), phase="pre")
    with pytest.raises(evidence.EvidenceFailure, match="capture phase"):
        evidence.read_snapshot_rows(FakeConnection([partial]), phase="post")


def test_database_url_validation_binds_exact_staging_target(tmp_path: Path) -> None:
    evidence = _load_evidence()
    ca_root = _write_ca_root(tmp_path)
    staging_database_url = _staging_database_url(ca_root)
    encoded_ca_root = urllib.parse.quote(os.fspath(ca_root), safe="")
    invalid_urls = [
        (
            "postgresql://user:password@"
            "cosmaneura-prod-postgres.postgres.database.azure.com/staging"
            f"?sslmode=verify-full&sslrootcert={encoded_ca_root}"
        ),
        (
            "postgresql://user:password@"
            "cosmaneura-staging-postgres.postgres.database.azure.com/production"
            f"?sslmode=verify-full&sslrootcert={encoded_ca_root}"
        ),
        _staging_database_url(ca_root, sslmode="require"),
        _staging_database_url(ca_root, sslmode="prefer"),
        _staging_database_url(ca_root, sslmode="allow"),
        _staging_database_url(ca_root, sslmode="disable"),
        _staging_database_url(ca_root, sslmode="verify-ca"),
        (
            "postgresql://user:password@"
            "cosmaneura-staging-postgres.postgres.database.azure.com/staging"
            f"?sslmode=verify-full&sslrootcert={encoded_ca_root}"
            "&hostaddr=203.0.113.9"
        ),
        (
            "postgresql://user:password@"
            "cosmaneura-staging-postgres.postgres.database.azure.com/staging"
            "?sslmode=verify-full"
        ),
        (
            "postgresql://user:password@"
            "cosmaneura-staging-postgres.postgres.database.azure.com/staging"
            f"?sslmode=verify-full&sslrootcert={encoded_ca_root}"
            f"&sslrootcert={encoded_ca_root}"
        ),
    ]

    assert evidence.validate_staging_database_url(staging_database_url) == ca_root
    for database_url in invalid_urls:
        with pytest.raises(evidence.EvidenceFailure, match="target") as captured:
            evidence.validate_staging_database_url(database_url)
        assert database_url not in str(captured.value)


def test_database_url_validation_requires_safe_readable_ca_root(
    tmp_path: Path,
) -> None:
    evidence = _load_evidence()
    ca_root = _write_ca_root(tmp_path)

    ca_root.chmod(0o666)
    with pytest.raises(evidence.EvidenceFailure, match="trust root"):
        evidence.validate_staging_database_url(_staging_database_url(ca_root))

    ca_root.chmod(0o644)
    symlink = tmp_path / "staging-trust-root-link.pem"
    symlink.symlink_to(ca_root)
    with pytest.raises(evidence.EvidenceFailure, match="trust root"):
        evidence.validate_staging_database_url(_staging_database_url(symlink))

    invalid_pem = tmp_path / "not-a-ca.pem"
    invalid_pem.write_text("not a trust root")
    invalid_pem.chmod(0o644)
    with pytest.raises(evidence.EvidenceFailure, match="trust root"):
        evidence.validate_staging_database_url(_staging_database_url(invalid_pem))

    missing = tmp_path / "missing.pem"
    with pytest.raises(evidence.EvidenceFailure, match="trust root"):
        evidence.validate_staging_database_url(_staging_database_url(missing))

    relative = Path("relative-trust-root.pem")
    with pytest.raises(evidence.EvidenceFailure, match="trust root"):
        evidence.validate_staging_database_url(_staging_database_url(relative))

    unsafe_parent = tmp_path / "unsafe-parent"
    unsafe_parent.mkdir(mode=0o700)
    unsafe_root = _write_ca_root(unsafe_parent)
    unsafe_parent.chmod(0o777)
    try:
        with pytest.raises(evidence.EvidenceFailure, match="trust root"):
            evidence.validate_staging_database_url(_staging_database_url(unsafe_root))
    finally:
        unsafe_parent.chmod(0o700)


def test_private_key_requires_owner_only_regular_file(tmp_path: Path) -> None:
    evidence = _load_evidence()
    key_path = tmp_path / "snapshot-key"
    key_path.write_bytes(b"s" * 32)
    key_path.chmod(0o600)

    assert evidence.load_private_hmac_key(key_path) == b"s" * 32

    key_path.chmod(0o640)
    with pytest.raises(evidence.EvidenceFailure, match="owner-only"):
        evidence.load_private_hmac_key(key_path)

    key_path.chmod(0o600)
    link_path = tmp_path / "snapshot-key-link"
    link_path.symlink_to(key_path)
    with pytest.raises(evidence.EvidenceFailure, match="owner-only"):
        evidence.load_private_hmac_key(link_path)


def test_main_fails_closed_outside_staging_without_connecting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    evidence = _load_evidence()
    key_path = tmp_path / "snapshot-key"
    key_path.write_bytes(b"s" * 32)
    key_path.chmod(0o600)
    monkeypatch.setenv("PYTHON_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://private-dsn")
    monkeypatch.setattr(
        evidence.psycopg,
        "connect",
        lambda *_args, **_kwargs: pytest.fail("must not connect"),
    )

    assert (
        evidence.main(["--hmac-key-file", os.fspath(key_path), "--phase", "pre"]) == 2
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "P0 staging evidence collection refused outside staging.\n"
    assert "private-dsn" not in captured.err


def test_main_rejects_wrong_database_target_without_connecting_or_echoing_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    evidence = _load_evidence()
    key_path = tmp_path / "snapshot-key"
    key_path.write_bytes(b"s" * 32)
    key_path.chmod(0o600)
    ca_root = _write_ca_root(tmp_path)
    query = urllib.parse.urlencode(
        {"sslmode": "verify-full", "sslrootcert": os.fspath(ca_root)}
    )
    private_wrong_target = (
        "postgresql://private-user:private-password@"
        "cosmaneura-prod-postgres.postgres.database.azure.com/production"
        f"?{query}"
    )
    monkeypatch.setenv("PYTHON_ENV", "staging")
    monkeypatch.setenv("DATABASE_URL", private_wrong_target)
    monkeypatch.setattr(
        evidence.psycopg,
        "connect",
        lambda *_args, **_kwargs: pytest.fail("must not connect"),
    )

    assert (
        evidence.main(["--hmac-key-file", os.fspath(key_path), "--phase", "pre"]) == 2
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "P0 staging evidence collection failed.\n"
    assert private_wrong_target not in captured.err
    assert "private-password" not in captured.err


def test_main_rejects_unexpected_connected_server_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    evidence = _load_evidence()
    key_path = tmp_path / "snapshot-key"
    key_path.write_bytes(b"s" * 32)
    key_path.chmod(0o600)
    ca_root = _write_ca_root(tmp_path)
    staging_database_url = _staging_database_url(ca_root)
    monkeypatch.setenv("PYTHON_ENV", "staging")
    monkeypatch.setenv("DATABASE_URL", staging_database_url)
    connection = FakeManagedConnection(
        [
            [],
            [("production", "private-user", "16.4", "203.0.113.8", 5432, True)],
        ]
    )
    monkeypatch.setattr(
        evidence.psycopg, "connect", lambda *_args, **_kwargs: connection
    )

    assert (
        evidence.main(["--hmac-key-file", os.fspath(key_path), "--phase", "pre"]) == 2
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "P0 staging evidence collection failed.\n"
    assert "production" not in captured.err
    assert connection.queries == [
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
        evidence.SERVER_IDENTITY_QUERY,
    ]


def test_main_uses_repeatable_read_only_transaction_and_emits_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    evidence = _load_evidence()
    key_path = tmp_path / "snapshot-key"
    key_path.write_bytes(b"s" * 32)
    key_path.chmod(0o600)
    ca_root = _write_ca_root(tmp_path)
    staging_database_url = _staging_database_url(ca_root)
    monkeypatch.setenv("PYTHON_ENV", "staging")
    monkeypatch.setenv("DATABASE_URL", staging_database_url)

    table_rows = _rows(evidence)
    returned: list[Sequence[tuple[Any, ...]]] = [
        [],
        [("staging", "staging_user", "16.4", "203.0.113.7", 5432, True)],
        [(name, "r", True) for name in sorted(evidence.P0_SCHEMA_TABLES)],
    ]
    returned.extend(
        [(row,) for row in table_rows[spec.name]] for spec in evidence.TABLE_SPECS
    )
    connection = FakeManagedConnection(returned)
    connect_kwargs: dict[str, object] = {}

    def connect(database_url: str, **kwargs: object) -> FakeManagedConnection:
        assert database_url == staging_database_url
        connect_kwargs.update(kwargs)
        return connection

    monkeypatch.setattr(evidence.psycopg, "connect", connect)

    assert (
        evidence.main(["--hmac-key-file", os.fspath(key_path), "--phase", "post"]) == 0
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert captured.err == ""
    assert payload["capture_phase"] == "post"
    assert payload["environment"] == "staging"
    assert payload["sections"]["baa"]["acceptances"]["count"] == 1
    assert "private-one@example.invalid" not in captured.out
    assert "private-password" not in captured.out
    assert connection.rolled_back is True
    assert connection.queries[0] == (
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
    )
    assert connection.queries[1] == evidence.SERVER_IDENTITY_QUERY
    assert "default_transaction_read_only=on" in str(connect_kwargs["options"])
    assert "timezone=UTC" in str(connect_kwargs["options"])
