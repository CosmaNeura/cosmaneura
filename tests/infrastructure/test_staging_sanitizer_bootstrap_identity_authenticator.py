from __future__ import annotations

import ast
import copy
import hashlib
import json
import pickle
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from scripts import authenticate_staging_sanitizer_bootstrap_identity as auth

ROOT = Path(__file__).parents[2]
POLICY = (
    ROOT
    / "contracts/staging-rehearsal/v1/restore-sanitizer-bootstrap-identity-evidence-policy.json"
)
SCRIPT = ROOT / "scripts/authenticate_staging_sanitizer_bootstrap_identity.py"
WORKFLOWS = ROOT / ".github/workflows"
NOW = datetime(2026, 9, 8, 12, 30, tzinfo=UTC)


def _policy() -> dict[str, Any]:
    return json.loads(POLICY.read_text())


def _state(policy: dict[str, Any]) -> dict[str, Any]:
    identity = policy["identity"]
    foundation = policy["foundation"]
    guard = {
        "cleanup_not_after": foundation["cleanup_not_after"],
        "enabled": True,
        "exact_plan_approved": True,
        "lifetime_hours": 6,
        "rehearsal_id": foundation["rehearsal_id"],
    }
    identity_output = {
        "attached_job_resource_ids": [],
        "cleanup_not_after": foundation["cleanup_not_after"],
        "client_id": identity["client_id"],
        "database_authority_bound": False,
        "destruction_protected": True,
        "exact_ids_authenticated": False,
        "federated_credential_ids": [],
        "lifecycle": "ephemeral",
        "name": identity["name"],
        "principal_id": identity["principal_id"],
        "provider_authority_bound": False,
        "rehearsal_id": foundation["rehearsal_id"],
        "resource_id": identity["resource_id"],
        "role_assignment_ids": [],
        "single_use_job_bound": False,
        "tenant_id": identity["tenant_id"],
        "workflow_binding_implemented": False,
    }
    return {
        "version": 4,
        "terraform_version": policy["state"]["opentofu_version"],
        "serial": policy["state"]["post_apply_serial"],
        "lineage": policy["state"]["lineage"],
        "outputs": {
            "sanitizer_bootstrap_identity_foundation": {
                "value": identity_output,
            },
            "state_boundary": {
                "value": {
                    "container": "cosmaneura-tfstate",
                    "key": policy["state"]["backend_key"],
                    "resource_group": "cosmaneura-tfstate",
                    "storage_account": "cosmaneuratfstate",
                }
            },
        },
        "resources": [
            {
                "mode": "data",
                "type": "azurerm_resource_group",
                "name": "control",
                "instances": [{}],
            },
            {
                "mode": "managed",
                "type": "azurerm_user_assigned_identity",
                "name": "sanitizer_bootstrap",
                "instances": [
                    {
                        "attributes": {
                            "client_id": identity["client_id"],
                            "id": identity["resource_id"],
                            "isolation_scope": "",
                            "location": identity["location"],
                            "name": identity["name"],
                            "principal_id": identity["principal_id"],
                            "resource_group_name": identity["resource_group"],
                            "tags": identity["tags"],
                            "tenant_id": identity["tenant_id"],
                        }
                    }
                ],
            },
            {
                "mode": "managed",
                "type": "terraform_data",
                "name": "activation_guard",
                "instances": [
                    {
                        "attributes": {
                            "input": {"value": guard},
                            "output": {"value": guard},
                        }
                    }
                ],
            },
        ],
        "check_results": [
            {"config_addr": name, "status": "pass"}
            for name in policy["state"]["required_passing_checks"]
        ],
    }


def _identity(policy: dict[str, Any]) -> dict[str, Any]:
    identity = policy["identity"]
    return {
        "assignmentRestrictions": identity["assignment_restrictions"],
        "clientId": identity["client_id"],
        "id": identity["resource_id"].replace("/resourceGroups/", "/resourcegroups/"),
        "isolationScope": identity["isolation_scope"],
        "location": identity["location"],
        "name": identity["name"],
        "principalId": identity["principal_id"],
        "resourceGroup": identity["resource_group"],
        "tags": identity["tags"],
        "tenantId": identity["tenant_id"],
        "type": identity["type"],
    }


def _job(resource_id: str) -> dict[str, Any]:
    return {
        "id": resource_id,
        "identity": {
            "principalId": None,
            "tenantId": None,
            "type": "None",
            "userAssignedIdentities": None,
        },
    }


def _write(path: Path, value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=True, indent=2).encode("ascii") + b"\n"
    if path.exists():
        path.chmod(0o600)
    path.write_bytes(raw)
    path.chmod(0o400)
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _receipt(
    policy: dict[str, Any], evidence_digests: dict[str, str]
) -> dict[str, Any]:
    foundation = policy["foundation"]
    identity = policy["identity"]
    state = policy["state"]
    return {
        "schema_version": "cosmaneura.staging-sanitizer-bootstrap-post-apply-evidence.v1",
        "created_at_utc": "2026-09-08T12:18:26Z",
        "scope": "non-phi staging control metadata",
        "result": "pass",
        "source": {
            "repository": foundation["repository"],
            "protected_master_sha": foundation["source_commit"],
            "remote_master_readback_sha": foundation["source_commit"],
            "saved_plan_sha256": foundation["saved_plan_sha256"].removeprefix(
                "sha256:"
            ),
            "decoded_plan_sha256": "1" * 64,
            "validator_receipt_sha256": "2" * 64,
            "independent_plan_review_response_id": "resp_test",
            "independent_plan_review_verdict": "No actionable findings.",
        },
        "operation": {
            "operator": "founder",
            "apply_reported_by_user": True,
            "apply_executed_by_agent": False,
            "plan_status": "spent_and_quarantined",
            "spent_plan_path": "/private/tmp/cosmaneura-bootstrap-live-s54/APPLIED-DO-NOT-REAPPLY-bootstrap.tfplan",
            "reapply_authorized": False,
            "create_count": 2,
            "azure_create_count": 1,
            "update_count": 0,
            "destroy_or_replace_count": 0,
        },
        "isolated_state": {
            "backend_key": state["backend_key"],
            "opentofu_version": state["opentofu_version"],
            "lineage": state["lineage"],
            "pre_apply_serial": state["pre_apply_serial"],
            "post_apply_serial": state["post_apply_serial"],
            "post_apply_resource_addresses": state["exact_resource_addresses"],
            "output_keys": state["exact_output_keys"],
            "check_count": len(state["required_passing_checks"]),
            "all_checks_pass": True,
            "raw_state_sha256": evidence_digests[auth.STATE_FILE].removeprefix(
                "sha256:"
            ),
            "normalized_state_sha256": evidence_digests[
                auth.NORMALIZED_STATE_FILE
            ].removeprefix("sha256:"),
        },
        "bootstrap_identity": {
            "name": identity["name"],
            "resource_id": identity["resource_id"].replace(
                "/resourceGroups/", "/resourcegroups/"
            ),
            "resource_id_comparison": "Azure resource IDs compared case-insensitively",
            "client_id": identity["client_id"],
            "principal_id": identity["principal_id"],
            "tenant_id": identity["tenant_id"],
            "location": identity["location"],
            "rehearsal_id": foundation["rehearsal_id"],
            "lifecycle": "ephemeral",
            "cleanup_not_after": foundation["cleanup_not_after"],
            "destruction_protected": True,
            "live_readback_matches_state": True,
            "required_tags_match": True,
        },
        "negative_authority_evidence": {
            "federated_credential_count": 0,
            "direct_or_inherited_role_assignment_count": 0,
            "subscription_container_apps_job_count": len(auth.JOB_FILES),
            "bootstrap_identity_job_attachment_count": 0,
            "database_authority_bound": False,
            "provider_authority_bound": False,
            "single_use_job_bound": False,
            "workflow_binding_implemented": False,
            "exact_ids_authenticated": False,
        },
        "container_apps_jobs_checked": list(
            policy["job_inventory"]["exact_jobs"].values()
        ),
        "evidence_files": {
            name: digest.removeprefix("sha256:")
            for name, digest in evidence_digests.items()
            if name != auth.RECEIPT_FILE
        },
        "safety": {
            "no_second_apply": True,
            "no_destroy_or_teardown_executed": True,
            "no_federation_created": True,
            "no_role_assignment_created": True,
            "no_job_attachment_created": True,
            "cleanup_deadline_is_hard_gate": True,
        },
    }


def _fixture(
    root: Path,
    *,
    mutate_documents: Callable[[dict[str, Any], dict[str, dict[str, Any]]], None]
    | None = None,
    mutate_receipt: Callable[[dict[str, Any]], None] | None = None,
    mutate_policy: Callable[[dict[str, Any]], None] | None = None,
    preserve_policy_digests: bool = False,
) -> tuple[dict[str, Any], Path]:
    root.chmod(0o700)
    policy = _policy()
    state = _state(policy)
    documents: dict[str, Any] = {
        auth.STATE_FILE: state,
        auth.NORMALIZED_STATE_FILE: copy.deepcopy(state),
        auth.IDENTITY_FILE: _identity(policy),
        auth.FEDERATED_FILE: [],
        auth.ROLES_FILE: [],
        auth.INVENTORY_FILE: list(policy["job_inventory"]["exact_jobs"].values()),
        **{
            name: _job(resource_id)
            for name, resource_id in policy["job_inventory"]["exact_jobs"].items()
        },
    }
    if mutate_documents is not None:
        mutate_documents(policy, documents)
    digests = {name: _write(root / name, value) for name, value in documents.items()}
    receipt = _receipt(
        policy, policy["evidence"]["files"] if preserve_policy_digests else digests
    )
    if mutate_receipt is not None:
        mutate_receipt(receipt)
    digests[auth.RECEIPT_FILE] = _write(root / auth.RECEIPT_FILE, receipt)
    if not preserve_policy_digests:
        policy["evidence"]["files"] = {
            name: digests[name] for name in auth.EVIDENCE_FILES
        }
    if mutate_policy is not None:
        mutate_policy(policy)
    return policy, root


def _authenticate(
    policy: dict[str, Any], root: Path, *times: datetime
) -> auth.AuthenticatedBootstrapIdentityEvidence:
    values = iter(times or (NOW, NOW))
    return auth._validated_candidate(
        policy=policy,
        evidence_directory=root,
        clock=lambda: next(values),
    )[0]


def _authenticate_public(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> auth.AuthenticatedBootstrapIdentityEvidence:
    monkeypatch.setattr(
        auth,
        "_read_exact_evidence",
        lambda directory, name, _expected, _maximum: (directory / name).read_bytes(),
    )
    monkeypatch.setattr(auth, "_utc_now", lambda: NOW)
    return auth.authenticate_bootstrap_identity_evidence(evidence_directory=root)


def test_checked_in_policy_is_exact_hash_pinned_and_non_authoritative() -> None:
    raw = POLICY.read_bytes()
    policy = auth.load_bootstrap_identity_evidence_policy()
    assert hashlib.sha256(raw).hexdigest() == auth.POLICY_SHA256
    assert tuple(policy["evidence"]["files"]) == auth.EVIDENCE_FILES
    assert policy["claims"] == auth.CLAIMS
    assert policy["claims"]["bootstrap_identity_exact_ids_pinned"] is True
    assert (
        policy["claims"]["bootstrap_identity_current_live_state_authenticated"] is False
    )
    assert policy["claims"]["bootstrap_authority_authenticated"] is False
    assert policy["claims"]["provider_mutation_authority"] is False
    assert policy["claims"]["database_mutation_authority"] is False
    assert policy["status"]["activation_ready"] is False


def test_authenticates_exact_closed_evidence_without_exposing_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy, root = _fixture(tmp_path, preserve_policy_digests=True)
    result = _authenticate_public(root, monkeypatch)
    assert result._is_authenticated()
    assert result.kind == auth.KIND
    assert result.identity_binding_sha256.startswith("sha256:")
    assert len(result.identity_binding_sha256) == 71
    assert result.policy_sha256 == f"sha256:{auth.POLICY_SHA256}"
    assert result.foundation_source_commit == policy["foundation"]["source_commit"]
    assert (
        result.post_apply_receipt_sha256
        == policy["evidence"]["files"][auth.RECEIPT_FILE]
    )
    assert result.authenticated_at == NOW
    assert result.expires_at == datetime(2026, 9, 8, 17, 40, 1, tzinfo=UTC)
    assert all(
        getattr(result, name) is expected for name, expected in auth.CLAIMS.items()
    )

    public = auth._public_projection(result)
    public_text = json.dumps(public, sort_keys=True)
    identity = policy["identity"]
    assert set(public) == set(auth.PUBLIC_FIELDS)
    assert all(
        identity[name] not in public_text
        for name in ("resource_id", "client_id", "principal_id", "tenant_id")
    )


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (
            lambda _policy, documents: documents[auth.IDENTITY_FILE].update(
                clientId="00000000-0000-0000-0000-000000000000"
            ),
            "bootstrap_identity_evidence.identity_invalid",
        ),
        (
            lambda _policy, documents: documents[auth.STATE_FILE].update(serial=3),
            "bootstrap_identity_evidence.state_invalid",
        ),
        (
            lambda _policy, documents: documents[auth.NORMALIZED_STATE_FILE].update(
                serial=3
            ),
            "bootstrap_identity_evidence.state_invalid",
        ),
        (
            lambda _policy, documents: documents[auth.FEDERATED_FILE].append(
                {"name": "unexpected"}
            ),
            "bootstrap_identity_evidence.federation_present",
        ),
        (
            lambda _policy, documents: documents[auth.ROLES_FILE].append(
                {"id": "unexpected"}
            ),
            "bootstrap_identity_evidence.role_assignment_present",
        ),
        (
            lambda policy, documents: documents[auth.JOB_FILES[0]]["identity"].update(
                userAssignedIdentities={policy["identity"]["resource_id"]: {}}
            ),
            "bootstrap_identity_evidence.bootstrap_attachment_present",
        ),
        (
            lambda _policy, documents: documents[auth.INVENTORY_FILE].pop(),
            "bootstrap_identity_evidence.job_invalid",
        ),
    ],
)
def test_evidence_semantic_mutations_fail_closed(
    tmp_path: Path,
    mutation: Callable[[dict[str, Any], dict[str, dict[str, Any]]], None],
    code: str,
) -> None:
    policy, root = _fixture(tmp_path, mutate_documents=mutation)
    with pytest.raises(auth.BootstrapIdentityEvidenceFailure, match=code):
        _authenticate(policy, root)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda receipt: receipt.update(result="fail"),
        lambda receipt: receipt.update(unexpected=True),
        lambda receipt: receipt["operation"].update(reapply_authorized=True),
        lambda receipt: receipt["negative_authority_evidence"].update(
            exact_ids_authenticated=True
        ),
        lambda receipt: receipt["container_apps_jobs_checked"].append(
            receipt["container_apps_jobs_checked"][0]
        ),
        lambda receipt: receipt["safety"].update(no_second_apply=False),
        lambda receipt: receipt["bootstrap_identity"].update(
            cleanup_not_after="2026-09-08T18:40:01Z"
        ),
    ],
)
def test_receipt_overclaims_or_drift_fail_closed(
    tmp_path: Path, mutation: Callable[[dict[str, Any]], None]
) -> None:
    policy, root = _fixture(tmp_path, mutate_receipt=mutation)
    with pytest.raises(
        auth.BootstrapIdentityEvidenceFailure,
        match="bootstrap_identity_evidence.receipt_invalid",
    ):
        _authenticate(policy, root)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda policy: policy["claims"].update(executable=True),
        lambda policy: policy["identity"].update(name="other"),
        lambda policy: policy["evidence"].update(symlinks_permitted=True),
        lambda policy: policy["status"]["activation_blockers"].clear(),
        lambda policy: policy.update(extra=True),
    ],
)
def test_policy_mutations_fail_before_evidence_read(
    tmp_path: Path,
    mutation: Callable[[dict[str, Any]], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy, root = _fixture(tmp_path, mutate_policy=mutation)
    monkeypatch.setattr(
        auth,
        "_read_exact_evidence",
        lambda *_args, **_kwargs: pytest.fail("invalid policy must fail first"),
    )
    with pytest.raises(
        auth.BootstrapIdentityEvidenceFailure,
        match="bootstrap_identity_evidence.policy_invalid",
    ):
        _authenticate(policy, root)


def test_file_hash_mode_and_symlink_boundaries(tmp_path: Path) -> None:
    policy, root = _fixture(tmp_path)
    identity_path = root / auth.IDENTITY_FILE
    identity_path.chmod(0o600)
    identity_path.write_bytes(identity_path.read_bytes() + b" ")
    identity_path.chmod(0o400)
    with pytest.raises(
        auth.BootstrapIdentityEvidenceFailure,
        match="bootstrap_identity_evidence.file_invalid",
    ):
        _authenticate(policy, root)

    policy, root = _fixture(tmp_path)
    identity_path = root / auth.IDENTITY_FILE
    identity_path.chmod(0o440)
    with pytest.raises(
        auth.BootstrapIdentityEvidenceFailure,
        match="bootstrap_identity_evidence.file_invalid",
    ):
        _authenticate(policy, root)

    policy, root = _fixture(tmp_path)
    identity_path = root / auth.IDENTITY_FILE
    target = root / "identity-target.json"
    target.write_bytes(identity_path.read_bytes())
    target.chmod(0o400)
    identity_path.unlink()
    identity_path.symlink_to(target)
    with pytest.raises(
        auth.BootstrapIdentityEvidenceFailure,
        match="bootstrap_identity_evidence.file_invalid",
    ):
        _authenticate(policy, root)


def test_normalized_state_must_be_canonical_projection(tmp_path: Path) -> None:
    policy, root = _fixture(
        tmp_path,
        mutate_documents=lambda _policy, documents: documents[
            auth.NORMALIZED_STATE_FILE
        ]["check_results"].reverse(),
    )
    with pytest.raises(
        auth.BootstrapIdentityEvidenceFailure,
        match="bootstrap_identity_evidence.state_invalid",
    ):
        _authenticate(policy, root)


def test_clock_expiry_regression_and_naive_values_refuse(tmp_path: Path) -> None:
    policy, root = _fixture(tmp_path)
    expiry = datetime(2026, 9, 8, 17, 40, 1, tzinfo=UTC)
    with pytest.raises(
        auth.BootstrapIdentityEvidenceFailure,
        match="bootstrap_identity_evidence.expired",
    ):
        _authenticate(policy, root, expiry)
    with pytest.raises(
        auth.BootstrapIdentityEvidenceFailure,
        match="bootstrap_identity_evidence.clock_regressed",
    ):
        _authenticate(policy, root, NOW, NOW - timedelta(seconds=1))
    with pytest.raises(
        auth.BootstrapIdentityEvidenceFailure,
        match="bootstrap_identity_evidence.clock_invalid",
    ):
        _authenticate(policy, root, NOW.replace(tzinfo=None))
    with pytest.raises(
        auth.BootstrapIdentityEvidenceFailure,
        match="bootstrap_identity_evidence.clock_invalid",
    ):
        _authenticate(policy, root, NOW.astimezone(timezone(timedelta(hours=1))))


def test_sealed_result_refuses_copy_pickle_and_tampering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _policy, root = _fixture(tmp_path, preserve_policy_digests=True)
    result = _authenticate_public(root, monkeypatch)
    with pytest.raises(auth.BootstrapIdentityEvidenceFailure):
        auth.AuthenticatedBootstrapIdentityEvidence()
    for operation in (
        lambda: copy.copy(result),
        lambda: copy.deepcopy(result),
        lambda: pickle.dumps(result),
    ):
        with pytest.raises(auth.BootstrapIdentityEvidenceFailure):
            operation()
    duplicate = object.__new__(auth.AuthenticatedBootstrapIdentityEvidence)
    for name in result.__slots__:
        object.__setattr__(duplicate, name, getattr(result, name))
    assert duplicate._is_authenticated() is False
    for field, value in (
        ("identity_binding_sha256", f"sha256:{'0' * 64}"),
        ("expires_at", result.expires_at + timedelta(hours=1)),
        ("authenticated_at", result.authenticated_at.replace(tzinfo=None)),
        ("foundation_source_commit", "0" * 40),
    ):
        tampered = object.__new__(auth.AuthenticatedBootstrapIdentityEvidence)
        for name in result.__slots__:
            object.__setattr__(tampered, name, getattr(result, name))
        object.__setattr__(tampered, field, value)
        assert tampered._is_authenticated() is False


def test_surface_has_no_network_subprocess_credentials_or_workflow_wiring() -> None:
    source = SCRIPT.read_text()
    tree = ast.parse(source)
    imports = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imports.isdisjoint(
        {"azure", "httpx", "requests", "socket", "subprocess", "urllib"}
    )
    assert all(
        forbidden not in source
        for forbidden in (
            "DefaultAzureCredential",
            "ManagedIdentityCredential",
            "az rest",
            "psycopg",
            "cursor.execute",
            "tofu apply",
        )
    )
    for workflow in (*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")):
        assert SCRIPT.name not in workflow.read_text()
