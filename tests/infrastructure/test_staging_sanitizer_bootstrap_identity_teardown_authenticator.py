from __future__ import annotations

import copy
import hashlib
import json
import pickle
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import pytest

from scripts import authenticate_staging_sanitizer_bootstrap_identity_teardown as auth

NOW = datetime(2026, 9, 8, 18, 10, tzinfo=UTC)


def _bytes(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode() + b"\n"


def _sha(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _pretty_sha(value: Any) -> str:
    return hashlib.sha256(
        (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    ).hexdigest()


def _state(policy: dict[str, Any]) -> dict[str, Any]:
    target = policy["target"]
    state = policy["state"]
    cleanup = policy["cleanup"]
    guard = {
        "cleanup_not_after": cleanup["cleanup_not_after"],
        "identity_resource_id": target["identity_resource_id"],
        "mode": "teardown-only",
        "rehearsal_id": target["rehearsal_id"],
        "teardown_authorized": True,
        "teardown_evidence_sha256": state["teardown_evidence_sha256"],
    }
    checks = [
        {
            "object_kind": "resource" if name.startswith("terraform_data") else "var",
            "config_addr": name,
            "status": "pass",
            "objects": [{"object_addr": name, "status": "pass"}],
        }
        for name in state["exact_passing_check_addresses"]
    ]
    return {
        "version": 4,
        "terraform_version": state["opentofu_version"],
        "serial": state["post_apply_serial"],
        "lineage": state["lineage"],
        "outputs": {
            "sanitizer_bootstrap_identity_teardown": {
                "value": {
                    "apply_authorized": False,
                    "cleanup_not_after": cleanup["cleanup_not_after"],
                    "founder_apply_required": True,
                    "identity_resource_id": target["identity_resource_id"],
                    "mode": "teardown-only",
                    "rehearsal_id": target["rehearsal_id"],
                    "teardown_evidence_sha256": state["teardown_evidence_sha256"],
                },
                "type": "fixture",
            },
            "state_boundary": {
                "value": {
                    "container": "cosmaneura-tfstate",
                    "key": state["backend_key"],
                    "resource_group": "cosmaneura-tfstate",
                    "storage_account": "cosmaneuratfstate",
                },
                "type": "fixture",
            },
        },
        "resources": [
            {
                "mode": "managed",
                "type": "terraform_data",
                "name": "activation_guard",
                "provider": 'provider["terraform.io/builtin/terraform"]',
                "instances": [
                    {
                        "index_key": 0,
                        "schema_version": 0,
                        "attributes": {
                            "id": "6f6ac9c3-68ec-f69b-8e3b-e466d5bf478e",
                            "input": {"value": guard, "type": "fixture"},
                            "output": {"value": guard, "type": "fixture"},
                            "triggers_replace": {
                                "value": {
                                    "cleanup_not_after": cleanup["cleanup_not_after"],
                                    "lifetime_hours": 6,
                                    "rehearsal_id": target["rehearsal_id"],
                                },
                                "type": "fixture",
                            },
                        },
                        "sensitive_attributes": [],
                    }
                ],
            }
        ],
        "check_results": checks,
    }


def _envelope(policy: dict[str, Any]) -> dict[str, Any]:
    target = policy["target"]
    cleanup = policy["cleanup"]
    state_policy = policy["state"]
    authority = policy["authority"]
    source = policy["source"]
    state = _state(policy)
    jobs = [
        {
            "id": (
                f"/subscriptions/{target['subscription_id']}/resourceGroups/fixture/"
                f"providers/Microsoft.App/jobs/fixture-{index}"
            ),
            "identity": None,
        }
        for index in range(authority["subscription_container_apps_job_count"])
    ]
    servers = []
    for index in range(authority["subscription_postgres_server_count"]):
        enabled = index == 0
        servers.append(
            {
                "id": (
                    authority["entra_enabled_postgres_server_id"]
                    if enabled
                    else (
                        f"/subscriptions/{target['subscription_id']}/resourceGroups/"
                        f"fixture/providers/Microsoft.DBforPostgreSQL/flexibleServers/fixture-{index}"
                    )
                ),
                "state": "Ready",
                "authConfig": {
                    "activeDirectoryAuth": "Enabled" if enabled else "Disabled",
                    "passwordAuth": "Disabled" if enabled else "Enabled",
                    "tenantId": target["tenant_id"] if enabled else None,
                },
            }
        )
    capture_script = "#!/bin/zsh\nset -euo pipefail\n"
    query_contract = {
        "identity": (
            "az identity list scoped to exact subscription and resource group, "
            "filtered by exact identity name"
        ),
        "roles": (
            "az role assignment list --all scoped to exact subscription, filtered "
            "by exact immutable principalId"
        ),
        "jobs": (
            "az containerapp job list scoped to exact subscription; verifier searches "
            "serialized identity blocks for exact client ID, principal ID, and identity name"
        ),
        "postgres": (
            "az postgres flexible-server list scoped to exact subscription plus exact "
            "Entra-admin list for the only Entra-enabled server; verifier searches for "
            "exact client ID, principal ID, and identity name"
        ),
        "state": (
            "OpenTofu state pull using the exact isolated backend execution directory"
        ),
    }
    verification = {
        "schema_version": 1,
        "policy_id": "cosmaneura-staging-sanitizer-bootstrap-identity-post-apply-v1",
        "source_commit": source["source_commit"],
        "applied_plan_sha256": source["applied_plan_sha256"].removeprefix("sha256:"),
        "cleanup_not_after": cleanup["cleanup_not_after"],
        "evidence_started_at": cleanup["evidence_started_at"],
        "evidence_completed_at": cleanup["evidence_completed_at"],
        "account": {
            "environmentName": "AzureCloud",
            "id": target["subscription_id"],
            "isDefault": True,
            "state": "Enabled",
            "tenantId": target["tenant_id"],
        },
        "identity_count": 0,
        "role_assignment_count": 0,
        "job_count": len(jobs),
        "job_attachment_matches": 0,
        "postgres_server_count": len(servers),
        "postgres_admin_matches": 0,
        "state_lineage": state_policy["lineage"],
        "state_serial": state_policy["post_apply_serial"],
        "state_outputs": state_policy["exact_output_keys"],
        "state_resources": [
            {
                "mode": "managed",
                "type": "terraform_data",
                "name": "activation_guard",
                "provider": 'provider["terraform.io/builtin/terraform"]',
            }
        ],
        "pass": True,
    }
    observed_target = {
        name: target[name]
        for name in (
            "subscription_id",
            "tenant_id",
            "resource_group",
            "identity_name",
            "identity_resource_id",
            "client_id",
            "principal_id",
        )
    }
    prior = {
        "schema_version": 2,
        "policy_id": "cosmaneura-staging-sanitizer-bootstrap-identity-post-apply-v2",
        "target": observed_target,
        "source_commit": source["source_commit"],
        "applied_plan_sha256": source["applied_plan_sha256"].removeprefix("sha256:"),
        "cleanup_not_after": cleanup["cleanup_not_after"],
        "capture_script_sha256": hashlib.sha256(capture_script.encode()).hexdigest(),
        "capture_script": capture_script,
        "query_contract": query_contract,
        "evidence_hashes": {
            name: hashlib.sha256(name.encode()).hexdigest()
            for name in (
                "v1_verification",
                "identities",
                "role_assignments",
                "jobs",
                "postgres_admins",
                "isolated_state",
            )
        },
        "verification": verification,
        "raw_evidence": {
            "identities": [],
            "role_assignments": [],
            "jobs": jobs,
            "postgres_admins": [
                {
                    "id": "/subscriptions/fixture/administrators/unrelated",
                    "name": "unrelated-database-administrator",
                }
            ],
            "isolated_state": state,
        },
    }
    return {
        "schema_version": 3,
        "policy_id": "cosmaneura-staging-sanitizer-bootstrap-identity-post-apply-v3",
        "prior_envelope_sha256": "0" * 64,
        "prior_envelope": prior,
        "remediation": {
            "inherited_rbac": {
                "command": (
                    "az role assignment list --include-inherited --scope /subscriptions/"
                    f"{target['subscription_id']} --subscription "
                    f"{target['subscription_id']} --query [exact principalId "
                    f"{target['principal_id']}]"
                ),
                "started_at": "2026-09-08T17:48:31Z",
                "completed_at": "2026-09-08T17:48:33Z",
                "evidence_sha256": hashlib.sha256(b"[]\n").hexdigest(),
                "assignments": [],
                "count": 0,
            },
            "postgres_server_inventory": {
                "query": (
                    "az postgres flexible-server list scoped to exact subscription "
                    f"{target['subscription_id']}"
                ),
                "evidence_sha256": _pretty_sha(servers),
                "servers": servers,
                "count": len(servers),
                "entra_enabled_count": 1,
                "entra_enabled_server_ids": [
                    authority["entra_enabled_postgres_server_id"]
                ],
            },
        },
        "pass": True,
    }


def _fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    mutate_evidence: Callable[[dict[str, Any]], None] | None = None,
    mutate_request: Callable[[dict[str, Any]], None] | None = None,
    mutate_response: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[Path, dict[str, Any]]:
    policy = copy.deepcopy(auth.load_bootstrap_identity_teardown_evidence_policy())
    evidence = _envelope(policy)
    if mutate_evidence is not None:
        mutate_evidence(evidence)
    evidence["prior_envelope_sha256"] = _pretty_sha(evidence["prior_envelope"])
    wire_evidence = json.loads(_bytes(evidence))
    request = {
        "model": policy["independent_review"]["model"],
        "reasoning": {"effort": "high"},
        "input": "fixture\n\nEVIDENCE V3\n"
        + json.dumps(wire_evidence, ensure_ascii=True, separators=(",", ":")),
    }
    if mutate_request is not None:
        mutate_request(request)
    response = {
        "id": policy["independent_review"]["response_id"],
        "model": policy["independent_review"]["model"],
        "status": "completed",
        "error": None,
        "incomplete_details": None,
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": policy["independent_review"]["verdict"],
                    }
                ],
            }
        ],
    }
    if mutate_response is not None:
        mutate_response(response)
    payloads = {
        auth.EVIDENCE_FILE: _bytes(evidence),
        auth.REVIEW_REQUEST_FILE: _bytes(request),
        auth.REVIEW_RESPONSE_FILE: _bytes(response),
    }
    for name, raw in payloads.items():
        policy["evidence"]["files"][name] = _sha(raw)
    policy["independent_review"]["request_sha256"] = policy["evidence"]["files"][
        auth.REVIEW_REQUEST_FILE
    ]
    policy["independent_review"]["response_sha256"] = policy["evidence"]["files"][
        auth.REVIEW_RESPONSE_FILE
    ]
    policy_path = tmp_path / "policy.json"
    policy_raw = _bytes(policy)
    policy_path.write_bytes(policy_raw)
    policy_path.chmod(0o400)
    evidence_directory = tmp_path / "evidence"
    evidence_directory.mkdir(mode=0o700)
    for name, raw in payloads.items():
        path = evidence_directory / name
        path.write_bytes(raw)
        path.chmod(0o400)
    monkeypatch.setattr(auth, "POLICY_PATH", policy_path)
    monkeypatch.setattr(auth, "POLICY_SHA256", hashlib.sha256(policy_raw).hexdigest())
    monkeypatch.setattr(auth, "_utc_now", lambda: NOW)
    return evidence_directory, policy


def _rejects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: Callable[[dict[str, Any]], None],
    code: str = "bootstrap_identity_teardown_evidence.evidence_invalid",
) -> None:
    directory, _policy = _fixture(tmp_path, monkeypatch, mutate_evidence=mutation)
    with pytest.raises(auth.BootstrapIdentityTeardownEvidenceFailure, match=code):
        auth.authenticate_bootstrap_identity_teardown_evidence(
            evidence_directory=directory
        )


def test_checked_in_policy_is_exact_and_non_authoritative() -> None:
    policy = auth.load_bootstrap_identity_teardown_evidence_policy()
    assert policy["claims"] == auth.CLAIMS
    assert policy["cleanup"]["cleanup_target_met"] is False
    assert policy["cleanup"]["completed_overrun_seconds"] == 163
    assert policy["status"]["executable"] is False
    assert policy["status"]["bootstrap_identity_recreation_authorized"] is False


def test_authenticates_exact_evidence_without_exposing_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory, policy = _fixture(tmp_path, monkeypatch)
    value = auth.authenticate_bootstrap_identity_teardown_evidence(
        evidence_directory=directory
    )
    assert value._is_authenticated()
    assert value.kind == auth.KIND
    assert value.authenticated_at == NOW
    assert value.evidence_completed_at == datetime(2026, 9, 8, 17, 42, 44, tzinfo=UTC)
    assert value.cleanup_overrun_seconds == 163
    assert value.bootstrap_identity_absence_authenticated is True
    assert value.current_live_state_authenticated is False
    assert value.executable is False
    assert dict(value._target) == policy["target"]
    public = auth._public_projection(value)
    serialized = json.dumps(public)
    assert tuple(public) == auth.PUBLIC_FIELDS
    assert policy["target"]["client_id"] not in serialized
    assert policy["target"]["principal_id"] not in serialized
    assert repr(value) == "<AuthenticatedBootstrapIdentityTeardownEvidence sealed>"
    assert not hasattr(value, "__dict__")


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda value: value.update({"pass": False}), "evidence_invalid"),
        (
            lambda value: value["prior_envelope"]["verification"].update(
                identity_count=1
            ),
            "evidence_invalid",
        ),
        (
            lambda value: value["remediation"]["inherited_rbac"].update(
                assignments=[{"principalId": "unexpected"}], count=1
            ),
            "evidence_invalid",
        ),
        (
            lambda value: value["prior_envelope"]["raw_evidence"]["jobs"][0].update(
                identity={
                    "userAssignedIdentities": {
                        value["prior_envelope"]["target"]["identity_resource_id"]: {}
                    }
                }
            ),
            "evidence_invalid",
        ),
        (
            lambda value: value["prior_envelope"]["raw_evidence"][
                "postgres_admins"
            ].append(
                {"principalId": value["prior_envelope"]["target"]["principal_id"]}
            ),
            "evidence_invalid",
        ),
        (
            lambda value: value["prior_envelope"]["verification"].update(
                state_serial=False
            ),
            "evidence_invalid",
        ),
        (
            lambda value: value["prior_envelope"]["raw_evidence"][
                "isolated_state"
            ].update(serial=2),
            "state_invalid",
        ),
        (
            lambda value: value["prior_envelope"]["raw_evidence"]["isolated_state"][
                "check_results"
            ][0].update(status="fail"),
            "state_invalid",
        ),
        (
            lambda value: value["remediation"]["postgres_server_inventory"].update(
                entra_enabled_count=0
            ),
            "evidence_invalid",
        ),
        (
            lambda value: value["prior_envelope"].update(source_commit="0" * 40),
            "evidence_invalid",
        ),
    ],
)
def test_security_relevant_evidence_mutations_refuse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: Callable[[dict[str, Any]], None],
    code: str,
) -> None:
    _rejects(
        tmp_path,
        monkeypatch,
        mutation,
        code=f"bootstrap_identity_teardown_evidence.{code}",
    )


def test_review_verdict_mutation_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory, _policy = _fixture(
        tmp_path,
        monkeypatch,
        mutate_response=lambda value: value["output"][0]["content"][0].update(
            text="APPROVED"
        ),
    )
    with pytest.raises(
        auth.BootstrapIdentityTeardownEvidenceFailure,
        match="bootstrap_identity_teardown_evidence.review_invalid",
    ):
        auth.authenticate_bootstrap_identity_teardown_evidence(
            evidence_directory=directory
        )


def test_review_request_must_contain_exact_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory, _policy = _fixture(
        tmp_path,
        monkeypatch,
        mutate_request=lambda value: value.update(input="unrelated evidence"),
    )
    with pytest.raises(
        auth.BootstrapIdentityTeardownEvidenceFailure,
        match="bootstrap_identity_teardown_evidence.review_invalid",
    ):
        auth.authenticate_bootstrap_identity_teardown_evidence(
            evidence_directory=directory
        )


def test_directory_and_file_integrity_are_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory, _policy = _fixture(tmp_path, monkeypatch)
    extra = directory / "extra.json"
    extra.write_text("{}\n")
    extra.chmod(0o400)
    with pytest.raises(
        auth.BootstrapIdentityTeardownEvidenceFailure,
        match="bootstrap_identity_teardown_evidence.directory_invalid",
    ):
        auth.authenticate_bootstrap_identity_teardown_evidence(
            evidence_directory=directory
        )
    extra.unlink()
    evidence = directory / auth.EVIDENCE_FILE
    evidence.chmod(0o600)
    with pytest.raises(
        auth.BootstrapIdentityTeardownEvidenceFailure,
        match="bootstrap_identity_teardown_evidence.file_invalid",
    ):
        auth.authenticate_bootstrap_identity_teardown_evidence(
            evidence_directory=directory
        )


def test_clock_must_be_after_evidence_and_monotonic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory, policy = _fixture(tmp_path, monkeypatch)
    future = datetime(2026, 9, 8, 17, 42, 43, tzinfo=UTC)
    with pytest.raises(
        auth.BootstrapIdentityTeardownEvidenceFailure,
        match="bootstrap_identity_teardown_evidence.evidence_from_future",
    ):
        auth._validated_candidate(
            policy=policy, evidence_directory=directory, clock=lambda: future
        )
    moments = iter((NOW, NOW - timedelta(seconds=1)))
    with pytest.raises(
        auth.BootstrapIdentityTeardownEvidenceFailure,
        match="bootstrap_identity_teardown_evidence.clock_regressed",
    ):
        auth._validated_candidate(
            policy=policy, evidence_directory=directory, clock=lambda: next(moments)
        )


def test_output_cannot_be_constructed_copied_pickled_or_mutated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory, _policy = _fixture(tmp_path, monkeypatch)
    value = auth.authenticate_bootstrap_identity_teardown_evidence(
        evidence_directory=directory
    )
    with pytest.raises(auth.BootstrapIdentityTeardownEvidenceFailure):
        auth.AuthenticatedBootstrapIdentityTeardownEvidence()
    for operation in (
        lambda: copy.copy(value),
        lambda: copy.deepcopy(value),
        lambda: pickle.dumps(value),
        lambda: setattr(value, "executable", True),
        lambda: delattr(value, "kind"),
    ):
        with pytest.raises(auth.BootstrapIdentityTeardownEvidenceFailure):
            operation()
    object.__setattr__(value, "executable", True)
    assert not value._is_authenticated()


def test_policy_semantic_mutation_refuses_even_when_rehashed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _directory, policy = _fixture(tmp_path, monkeypatch)
    policy["claims"]["executable"] = True
    raw = _bytes(policy)
    policy_path = tmp_path / "mutated-policy.json"
    policy_path.write_bytes(raw)
    policy_path.chmod(0o400)
    monkeypatch.setattr(auth, "POLICY_PATH", policy_path)
    monkeypatch.setattr(auth, "POLICY_SHA256", hashlib.sha256(raw).hexdigest())
    with pytest.raises(
        auth.BootstrapIdentityTeardownEvidenceFailure,
        match="bootstrap_identity_teardown_evidence.policy_invalid",
    ):
        auth.load_bootstrap_identity_teardown_evidence_policy()
