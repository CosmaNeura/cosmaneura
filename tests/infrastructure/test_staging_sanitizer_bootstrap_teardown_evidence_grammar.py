from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable

import pytest

from scripts import staging_sanitizer_bootstrap_teardown_evidence_grammar as grammar


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
    guard_type = [
        "object",
        {
            "cleanup_not_after": "string",
            "identity_resource_id": "string",
            "mode": "string",
            "rehearsal_id": "string",
            "teardown_authorized": "bool",
            "teardown_evidence_sha256": "string",
        },
    ]
    checks = [
        {
            "object_kind": "resource" if name.startswith("terraform_data") else "var",
            "config_addr": name,
            "status": "pass",
            "objects": [
                {
                    "object_addr": (
                        f"{name}[0]" if name.startswith("terraform_data") else name
                    ),
                    "status": "pass",
                }
            ],
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
                "type": [
                    "object",
                    {
                        "apply_authorized": "bool",
                        "cleanup_not_after": "string",
                        "founder_apply_required": "bool",
                        "identity_resource_id": "string",
                        "mode": "string",
                        "rehearsal_id": "string",
                        "teardown_evidence_sha256": "string",
                    },
                ],
            },
            "state_boundary": {
                "value": {
                    "container": "cosmaneura-tfstate",
                    "key": state["backend_key"],
                    "resource_group": "cosmaneura-tfstate",
                    "storage_account": "cosmaneuratfstate",
                },
                "type": [
                    "object",
                    {
                        "container": "string",
                        "key": "string",
                        "resource_group": "string",
                        "storage_account": "string",
                    },
                ],
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
                            "input": {"value": guard, "type": guard_type},
                            "output": {"value": guard, "type": guard_type},
                            "triggers_replace": {
                                "value": {
                                    "cleanup_not_after": cleanup["cleanup_not_after"],
                                    "lifetime_hours": 6,
                                    "rehearsal_id": target["rehearsal_id"],
                                },
                                "type": [
                                    "object",
                                    {
                                        "cleanup_not_after": "string",
                                        "lifetime_hours": "number",
                                        "rehearsal_id": "string",
                                    },
                                ],
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
            "identity": {"type": "None"},
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
    admins = [
        {
            "id": (
                f"{authority['entra_enabled_postgres_server_id']}/administrators/"
                "5ff4de97-43ca-4c0b-843a-5930b0da449e"
            ),
            "name": "unrelated-database-administrator",
            "principalName": "unrelated-database-administrator",
            "principalType": "ServicePrincipal",
            "tenantId": target["tenant_id"],
        }
    ]
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
            "v1_verification": _pretty_sha(verification),
            "identities": _pretty_sha([]),
            "role_assignments": _pretty_sha([]),
            "jobs": _pretty_sha(jobs),
            "postgres_admins": _pretty_sha(admins),
            "isolated_state": hashlib.sha256(_bytes(state)).hexdigest(),
        },
        "verification": verification,
        "raw_evidence": {
            "identities": [],
            "role_assignments": [],
            "jobs": jobs,
            "postgres_admins": admins,
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


def _review_response(
    policy: dict[str, Any], metadata: dict[str, str], input_length: int
) -> dict[str, Any]:
    return {
        "background": False,
        "completed_at": 2,
        "content_filters": [
            {
                "blocked": False,
                "source_type": "prompt",
                "content_filter_raw": [],
                "content_filter_results": {},
                "content_filter_offsets": {
                    "start_offset": 0,
                    "end_offset": input_length,
                    "check_offset": 0,
                },
            }
        ],
        "created_at": 1,
        "error": None,
        "frequency_penalty": 0.0,
        "id": policy["independent_review"]["response_id"],
        "incomplete_details": None,
        "instructions": None,
        "max_output_tokens": policy["independent_review"]["maximum_output_tokens"],
        "max_tool_calls": None,
        "metadata": metadata,
        "model": policy["independent_review"]["model"],
        "moderation": None,
        "object": "response",
        "output": [
            {
                "id": "rs_fixture",
                "type": "reasoning",
                "content": [],
                "encrypted_content": "fixture",
                "summary": [],
            },
            {
                "id": "msg_fixture",
                "type": "message",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "annotations": [],
                        "logprobs": [],
                        "text": policy["independent_review"]["verdict"],
                    }
                ],
                "phase": "final_answer",
                "role": "assistant",
            },
        ],
        "parallel_tool_calls": True,
        "presence_penalty": 0.0,
        "previous_response_id": None,
        "prompt_cache_key": None,
        "prompt_cache_retention": "24h",
        "reasoning": {
            "context": "all_turns",
            "effort": "high",
            "mode": "standard",
            "summary": None,
        },
        "safety_identifier": None,
        "service_tier": "default",
        "status": "completed",
        "store": True,
        "temperature": 1.0,
        "text": {"format": {"type": "text"}, "verbosity": "medium"},
        "tool_choice": "auto",
        "tool_usage": {
            "image_gen": {
                "input_tokens": 0,
                "input_tokens_details": {"image_tokens": 0, "text_tokens": 0},
                "output_tokens": 0,
                "output_tokens_details": {"image_tokens": 0, "text_tokens": 0},
                "total_tokens": 0,
            },
            "web_search": {"num_requests": 0},
        },
        "tools": [],
        "top_logprobs": 0,
        "top_p": 0.98,
        "truncation": "disabled",
        "usage": {
            "input_tokens": 1,
            "input_tokens_details": {"cache_write_tokens": 0, "cached_tokens": 0},
            "output_tokens": 2,
            "output_tokens_details": {"reasoning_tokens": 1},
            "total_tokens": 3,
        },
        "user": None,
    }


def _policy() -> dict[str, Any]:
    subscription = "bab2a709-1fcb-4c87-a9fc-802f7477ec13"
    tenant = "d51754d0-bba5-4810-820e-afac542844d6"
    identity = "cosmaneura-staging-restore-bootstrap-job"
    enabled_server = (
        f"/subscriptions/{subscription}/resourceGroups/cosmaneura-scheduling-development/"
        "providers/Microsoft.DBforPostgreSQL/flexibleServers/cosmaneura-scheduling-dev-pg"
    )
    return {
        "source": {
            "source_commit": "8c5e32e1197e83892c0487be2e259d621d276324",
            "applied_plan_sha256": "sha256:" + "a" * 64,
        },
        "target": {
            "subscription_id": subscription,
            "tenant_id": tenant,
            "resource_group": "cosmaneura-staging-rehearsal-control",
            "identity_name": identity,
            "identity_resource_id": (
                f"/subscriptions/{subscription}/resourceGroups/"
                "cosmaneura-staging-rehearsal-control/providers/"
                f"Microsoft.ManagedIdentity/userAssignedIdentities/{identity}"
            ),
            "client_id": "1d494ade-7391-479b-b860-c0b13ef98c34",
            "principal_id": "10e9110b-9a64-4961-9210-06ab05b4e5ab",
            "rehearsal_id": "21506aaeec3c",
        },
        "evidence": {
            "maximum_file_bytes": 1048576,
            "files": {name: "sha256:" + "0" * 64 for name in grammar.EVIDENCE_FILES},
        },
        "cleanup": {
            "cleanup_not_after": "2026-09-08T17:40:01Z",
            "evidence_started_at": "2026-09-08T17:42:35Z",
            "evidence_completed_at": "2026-09-08T17:42:44Z",
            "started_overrun_seconds": 154,
            "completed_overrun_seconds": 163,
        },
        "state": {
            "backend_key": "staging-sanitizer-bootstrap-identity.tfstate",
            "opentofu_version": "1.12.5",
            "lineage": "a0c8b939-2fb2-0604-3e70-d925aeee5950",
            "post_apply_serial": 3,
            "exact_resource_addresses": ["managed.terraform_data.activation_guard"],
            "exact_output_keys": [
                "sanitizer_bootstrap_identity_teardown",
                "state_boundary",
            ],
            "exact_passing_check_addresses": [
                "terraform_data.activation_guard",
                "var.sanitizer_bootstrap_rehearsal_id",
                "var.sanitizer_bootstrap_lifetime_hours",
                "var.sanitizer_bootstrap_teardown_rehearsal_id",
                "var.sanitizer_bootstrap_teardown_evidence_sha256",
            ],
            "teardown_evidence_sha256": "sha256:" + "b" * 64,
        },
        "authority": {
            "identity_count": 0,
            "direct_or_inherited_role_assignment_count": 0,
            "subscription_container_apps_job_count": 6,
            "bootstrap_identity_job_attachment_count": 0,
            "subscription_postgres_server_count": 5,
            "entra_enabled_postgres_server_count": 1,
            "entra_enabled_postgres_server_id": enabled_server,
            "bootstrap_identity_postgres_administrator_count": 0,
        },
        "independent_review": {
            "model": "codex-auto-review",
            "request_input_sha256": "sha256:" + "0" * 64,
            "binding_id": "sha256:" + "0" * 64,
            "maximum_output_tokens": 20000,
            "response_id": "resp_fixture",
            "verdict": "POST-APPLY VERIFIED",
        },
    }


def _bundle(
    tmp_path: Path,
    *,
    mutate_evidence: Callable[[dict[str, Any]], None] | None = None,
    mutate_request: Callable[[dict[str, Any]], None] | None = None,
    mutate_response: Callable[[dict[str, Any]], None] | None = None,
    binding_override: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    policy = _policy()
    evidence = _envelope(policy)
    if mutate_evidence:
        mutate_evidence(evidence)
    state_raw = _bytes(evidence["prior_envelope"]["raw_evidence"]["isolated_state"])
    evidence["prior_envelope"]["evidence_hashes"]["isolated_state"] = hashlib.sha256(
        state_raw
    ).hexdigest()
    evidence["prior_envelope_sha256"] = _pretty_sha(evidence["prior_envelope"])
    evidence_raw = _bytes(evidence)
    policy["evidence"]["files"][grammar.EVIDENCE_FILE] = _sha(evidence_raw)
    review_input = "fixture\n\nEVIDENCE V3\n" + json.dumps(
        json.loads(evidence_raw), ensure_ascii=True, separators=(",", ":")
    )
    review = policy["independent_review"]
    review["request_input_sha256"] = _sha(review_input.encode())
    review["binding_id"] = grammar.expected_review_binding(
        evidence_sha256=policy["evidence"]["files"][grammar.EVIDENCE_FILE],
        request_input_sha256=review["request_input_sha256"],
        maximum_output_tokens=review["maximum_output_tokens"],
    )
    metadata = {
        "binding_id": review["binding_id"],
        "evidence_sha256": policy["evidence"]["files"][grammar.EVIDENCE_FILE],
        "request_input_sha256": review["request_input_sha256"],
    }
    request = {
        "model": review["model"],
        "reasoning": {"effort": "high"},
        "metadata": metadata,
        "max_output_tokens": review["maximum_output_tokens"],
        "input": review_input,
    }
    if mutate_request:
        mutate_request(request)
    response = _review_response(policy, metadata, len(review_input))
    if binding_override is not None:
        review["binding_id"] = binding_override
        metadata["binding_id"] = binding_override
    if mutate_response:
        mutate_response(response)
    payloads = {
        grammar.EVIDENCE_FILE: evidence_raw,
        grammar.STATE_EVIDENCE_FILE: state_raw,
        grammar.REVIEW_REQUEST_FILE: _bytes(request),
        grammar.REVIEW_RESPONSE_FILE: _bytes(response),
    }
    for name, raw in payloads.items():
        policy["evidence"]["files"][name] = _sha(raw)
    directory = tmp_path / "evidence"
    directory.mkdir(mode=0o700)
    for name, raw in payloads.items():
        path = directory / name
        path.write_bytes(raw)
        path.chmod(0o400)
    return directory, policy


def test_validates_closed_world_bundle(tmp_path: Path) -> None:
    directory, policy = _bundle(tmp_path)
    assert grammar.validate_evidence_directory(directory, policy) == {
        name: (directory / name).read_bytes() for name in grammar.EVIDENCE_FILES
    }


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["prior_envelope"]["raw_evidence"]["jobs"][0].update(
            id=value["prior_envelope"]["raw_evidence"]["jobs"][0]["id"].replace(
                "bab2a709-1fcb-4c87-a9fc-802f7477ec13",
                "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            )
        ),
        lambda value: value["prior_envelope"]["raw_evidence"]["postgres_admins"][
            0
        ].update(
            id=value["prior_envelope"]["raw_evidence"]["postgres_admins"][0][
                "id"
            ].replace("cosmaneura-scheduling-dev-pg", "wrong-server")
        ),
        lambda value: value["prior_envelope"]["verification"].update(
            state_serial=False
        ),
        lambda value: value["prior_envelope"]["raw_evidence"]["isolated_state"].update(
            unexpected=True
        ),
    ],
)
def test_scope_hash_and_closed_world_mutations_refuse(
    tmp_path: Path, mutation: Callable[[dict[str, Any]], None]
) -> None:
    directory, policy = _bundle(tmp_path, mutate_evidence=mutation)
    with pytest.raises(grammar.EvidenceGrammarFailure):
        grammar.validate_evidence_directory(directory, policy)


@pytest.mark.parametrize(
    ("request_mutation", "response_mutation"),
    [
        (lambda value: value.update(input="unrelated"), None),
        (
            None,
            lambda value: value["metadata"].update(
                request_input_sha256="sha256:" + "f" * 64
            ),
        ),
        (None, lambda value: value.update(unexpected=True)),
        (None, lambda value: value.update(max_output_tokens=20000.0)),
        (
            None,
            lambda value: value["usage"].update(
                output_tokens=20001, total_tokens=20002
            ),
        ),
        (
            None,
            lambda value: value["output"][-1]["content"][0].update(text="APPROVED"),
        ),
    ],
)
def test_review_binding_and_shape_mutations_refuse(
    tmp_path: Path,
    request_mutation: Callable[[dict[str, Any]], None] | None,
    response_mutation: Callable[[dict[str, Any]], None] | None,
) -> None:
    directory, policy = _bundle(
        tmp_path,
        mutate_request=request_mutation,
        mutate_response=response_mutation,
    )
    with pytest.raises(grammar.EvidenceGrammarFailure):
        grammar.validate_evidence_directory(directory, policy)


def test_verification_provider_is_bound_to_raw_state(tmp_path: Path) -> None:
    def mutate(value: dict[str, Any]) -> None:
        verification = value["prior_envelope"]["verification"]
        verification["state_resources"][0]["provider"] = 'provider["unexpected"]'
        value["prior_envelope"]["evidence_hashes"]["v1_verification"] = _pretty_sha(
            verification
        )

    directory, policy = _bundle(tmp_path, mutate_evidence=mutate)
    with pytest.raises(grammar.EvidenceGrammarFailure):
        grammar.validate_evidence_directory(directory, policy)


def test_consistently_replaced_review_binding_refuses(tmp_path: Path) -> None:
    directory, policy = _bundle(tmp_path, binding_override="sha256:" + "f" * 64)
    with pytest.raises(grammar.EvidenceGrammarFailure):
        grammar.validate_evidence_directory(directory, policy)


def test_file_mode_symlink_and_hardlink_refuse(tmp_path: Path) -> None:
    directory, policy = _bundle(tmp_path)
    evidence = directory / grammar.EVIDENCE_FILE
    evidence.chmod(0o600)
    with pytest.raises(grammar.EvidenceGrammarFailure):
        grammar.validate_evidence_directory(directory, policy)
    evidence.chmod(0o400)

    state = directory / grammar.STATE_EVIDENCE_FILE
    retained = tmp_path / "retained-state"
    retained.write_bytes(state.read_bytes())
    retained.chmod(0o400)
    state.unlink()
    state.symlink_to(retained)
    with pytest.raises(grammar.EvidenceGrammarFailure):
        grammar.validate_evidence_directory(directory, policy)
    state.unlink()
    os.link(retained, state)
    with pytest.raises(grammar.EvidenceGrammarFailure):
        grammar.validate_evidence_directory(directory, policy)
