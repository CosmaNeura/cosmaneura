from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from scripts import (
    validate_staging_restore_launch_binding as launch,
)
from scripts import (
    validate_staging_restore_runtime_binding as binding,
)

POLICY = binding.POLICY_PATH


def _policy() -> dict[str, Any]:
    return json.loads(POLICY.read_text(encoding="utf-8"))


def _at(policy: dict[str, Any], path: tuple[str, ...]) -> dict[str, Any]:
    value: Any = policy
    for name in path:
        value = value[name]
    assert type(value) is dict
    return value


def _activated(policy: dict[str, Any]) -> dict[str, Any]:
    policy = deepcopy(policy)
    policy["activation_ready"] = True
    policy["activation"] = dict.fromkeys(policy["activation"], True)
    policy["activation_blockers"] = []
    launch = policy["launch_binding"]
    launch["mailbox"]["implemented"] = True
    launch["broker_request_authentication"]["replay_store"]["implemented"] = True
    launch["receipt_signing"]["implemented"] = True
    handoff = launch["mailbox"]["execution_receipt_handoff"]
    handoff["execution_name_source_adapter_implemented"] = True
    handoff["platform_source_validation"]["adapter_implemented"] = True
    return policy


def _configured_policy() -> dict[str, Any]:
    policy = _activated(_policy())
    placeholders = sorted(set(binding._placeholder_values(policy)))
    replacements: dict[str, str] = {}
    digest_index = 1
    resource_index = 1
    uuid_index = 1
    for placeholder in placeholders:
        if placeholder.endswith("_SHA256"):
            replacements[placeholder] = f"sha256:{digest_index:064x}"
            digest_index += 1
        elif placeholder.endswith("_UUID"):
            replacements[placeholder] = f"00000000-0000-4000-8000-{uuid_index:012x}"
            uuid_index += 1
        elif placeholder.endswith("_IMAGE_REPOSITORY"):
            replacements[placeholder] = (
                "restoreexecutor.azurecr.io/cosmaneura/restore-executor"
            )
        elif placeholder.endswith("_SIGNER_IDENTITY"):
            replacements[placeholder] = (
                "https://github.com/CosmaNeura/cosmaneura/.github/workflows/"
                "build_and_publish_manual.yml@refs/heads/master"
            )
        elif "LAUNCH_MAILBOX_STORAGE_ACCOUNT_HOST" in placeholder:
            replacements[placeholder] = "restorelaunch.blob.core.windows.net"
        elif "MARKER_STORAGE_ACCOUNT_HOST" in placeholder:
            replacements[placeholder] = "restoremarkers.blob.core.windows.net"
        elif placeholder.endswith("_STORAGE_ACCOUNT_HOST"):
            replacements[placeholder] = "restoreevidence.blob.core.windows.net"
        elif placeholder.endswith("_REGISTRY_HOST"):
            replacements[placeholder] = "restoreexecutor.azurecr.io"
        elif placeholder.endswith("_VAULT_HOST"):
            replacements[placeholder] = "restoresecrets.vault.azure.net"
        elif placeholder.endswith("_VERSION"):
            replacements[placeholder] = f"version-{resource_index}"
            resource_index += 1
        elif placeholder.endswith("_HTTPS_URI"):
            continue
        else:
            replacements[placeholder] = (
                "/subscriptions/11111111-1111-4111-8111-111111111111/"
                "resourceGroups/runtime/providers/Cosmaneura.Bindings/"
                f"resources/{resource_index}"
            )
            resource_index += 1

    secrets = policy["evidence_ingress"]["private_versioned_secret_source"]["secrets"]
    for name, version in (
        ("server_scope_hmac_key", "secret-version-1"),
        ("github_trusted_root", "secret-version-2"),
    ):
        item = secrets[name]
        replacements[item["version"]] = version
        replacements[item["versioned_https_uri"]] = (
            f"https://restoresecrets.vault.azure.net/secrets/{name}/{version}"
        )

    def replace(value: Any) -> Any:
        if type(value) is str:
            return replacements.get(value, value)
        if type(value) is list:
            return [replace(item) for item in value]
        if type(value) is dict:
            return {key: replace(item) for key, item in value.items()}
        return value

    configured = replace(policy)
    assert not binding._placeholder_values(configured)
    for action in ("provision", "cleanup"):
        projection, _pointer, _template = launch._job_projection(
            _job_document(configured, action), policy=configured, job_kind=action
        )
        configured["runtime"]["jobs"][action]["configuration_template_sha256"] = (
            binding._digest(binding._canonical(projection))
        )
    return configured


def _pointer_values() -> dict[str, str]:
    record_digest = f"sha256:{'a' * 64}"
    bundle_digest = f"sha256:{'b' * 64}"
    return {
        "record_uri": (
            "https://restoreevidence.blob.core.windows.net/restore-runtime-bindings-v1/"
            f"{record_digest[7:]}.json"
        ),
        "record_sha256": record_digest,
        "signature_bundle_uri": (
            "https://restoreevidence.blob.core.windows.net/restore-runtime-bindings-v1/"
            f"{bundle_digest[7:]}.sigstore.json"
        ),
        "signature_bundle_sha256": bundle_digest,
    }


def _job_document(
    policy: dict[str, Any], action: str, pointers: dict[str, str] | None = None
) -> dict[str, Any]:
    job = policy["runtime"]["jobs"][action]
    identity = policy["identities"]["jobs"][action]["resource_id"]
    pointer_values = pointers or _pointer_values()
    env_names = policy["external_runtime_record"]["reference"][
        "fixed_plain_environment_variables"
    ]
    fields = tuple(binding.RUNTIME_REFERENCE_FIELDS)
    return {
        "id": job["resource_id"],
        "name": job["name"],
        "type": "Microsoft.App/jobs",
        "location": "eastus",
        "identity": {
            "type": "UserAssigned",
            "userAssignedIdentities": {
                identity: {
                    "clientId": policy["identities"]["jobs"][action]["client_id"],
                    "principalId": policy["identities"]["jobs"][action]["principal_id"],
                }
            },
        },
        "properties": {
            "environmentId": job["environment_resource_id"],
            "workloadProfileName": "Consumption",
            "configuration": {
                "triggerType": "Manual",
                "replicaTimeout": 3600,
                "replicaRetryLimit": 0,
                "manualTriggerConfig": {
                    "parallelism": 1,
                    "replicaCompletionCount": 1,
                },
                "registries": [
                    {
                        "server": policy["private_registry"]["host"],
                        "identity": identity,
                    }
                ],
            },
            "template": {
                "containers": [
                    {
                        "name": f"restore-{action}",
                        "image": (f"{job['image_repository']}@sha256:{'a' * 64}"),
                        "command": ["python"],
                        "args": [f"restore_{action}.py"],
                        "env": [
                            {"name": name, "value": pointer_values[field]}
                            for name, field in zip(env_names, fields, strict=True)
                        ],
                        "resources": {"cpu": 1, "memory": "2Gi"},
                    }
                ],
                "initContainers": [],
                "volumes": [],
            },
        },
    }


def _job_observation(
    policy: dict[str, Any], action: str, pointers: dict[str, str]
) -> launch.AuthenticatedJobConfigurationObservation:
    body = binding._canonical(_job_document(policy, action, pointers))
    response = (
        launch.AuthenticatedArmGetResponse._from_reviewed_managed_identity_adapter(
            resource_id=policy["runtime"]["jobs"][action]["resource_id"],
            api_version="2024-03-01",
            status_code=200,
            content_type="application/json",
            observed_at=launch.datetime(2026, 9, 4, 12, 2, tzinfo=launch.UTC),
            body=body,
        )
    )
    return launch.validate_job_configuration_observation(
        policy, job_kind=action, response=response
    )


def _external_binding(
    policy: dict[str, Any],
) -> tuple[
    bytes,
    binding.ExternalRuntimeBindingReference,
    binding.ExternalRuntimeBindingReference,
    binding.VerifiedExternalRuntimeRecordSignature,
]:
    runtime = policy["runtime"]
    environment = runtime["container_apps_environment"]
    jobs = runtime["jobs"]
    identities = policy["identities"]["jobs"]
    record = {
        "schema_version": 1,
        "record_id": "runtime-0123456789abcdef",
        "policy_id": policy["policy_id"],
        "policy_binding_sha256": binding._policy_binding_digest(policy),
        "repository": "CosmaNeura/cosmaneura",
        "protected_ref": "refs/heads/master",
        "image_repository": jobs["provision"]["image_repository"],
        "image_digest": f"sha256:{'a' * 64}",
        "provision_job_image_reference": (
            f"{jobs['provision']['image_repository']}@sha256:{'a' * 64}"
        ),
        "cleanup_job_image_reference": (
            f"{jobs['cleanup']['image_repository']}@sha256:{'a' * 64}"
        ),
        "image_published_at": "2026-09-04T12:00:00Z",
        "environment_resource_id": environment["resource_id"],
        "provision_job_resource_id": jobs["provision"]["resource_id"],
        "provision_job_configuration_template_sha256": jobs["provision"][
            "configuration_template_sha256"
        ],
        "provision_job_identity_resource_id": identities["provision"]["resource_id"],
        "cleanup_job_resource_id": jobs["cleanup"]["resource_id"],
        "cleanup_job_configuration_template_sha256": jobs["cleanup"][
            "configuration_template_sha256"
        ],
        "cleanup_job_identity_resource_id": identities["cleanup"]["resource_id"],
        "network_policy_sha256": environment["network_policy_sha256"],
        "created_at": "2026-09-04T12:01:00Z",
    }
    raw = binding._canonical(record)
    record_digest = binding._digest(raw)
    bundle_digest = f"sha256:{'b' * 64}"
    pointer_values = {
        "record_uri": (
            "https://restoreevidence.blob.core.windows.net/restore-runtime-bindings-v1/"
            f"{record_digest.removeprefix('sha256:')}.json"
        ),
        "record_sha256": record_digest,
        "signature_bundle_uri": (
            "https://restoreevidence.blob.core.windows.net/restore-runtime-bindings-v1/"
            f"{bundle_digest.removeprefix('sha256:')}.sigstore.json"
        ),
        "signature_bundle_sha256": bundle_digest,
    }
    observations = tuple(
        _job_observation(policy, job_kind, pointer_values)
        for job_kind in ("provision", "cleanup")
    )
    references = tuple(
        binding.ExternalRuntimeBindingReference._from_authenticated_job_observation(
            observation
        )
        for observation in observations
    )
    signature = (
        binding.VerifiedExternalRuntimeRecordSignature._mint_from_reviewed_verifier(
            record_sha256=record_digest,
            bundle_sha256=bundle_digest,
            trusted_root_sha256=policy["evidence_ingress"][
                "private_versioned_secret_source"
            ]["secrets"]["github_trusted_root"]["sha256"],
            certificate_oidc_issuer="https://token.actions.githubusercontent.com",
            certificate_identity=policy["external_runtime_record"]["signature"][
                "certificate_identity"
            ],
        )
    )
    return raw, references[0], references[1], signature


def test_canonical_policy_is_strict_and_dormant(capsys: Any) -> None:
    policy = binding.load_policy()

    assert policy == _policy()
    assert binding.validate_runtime_binding_policy(policy) is policy
    assert policy["activation_ready"] is False
    assert set(policy["activation"].values()) == {False}
    assert policy["activation_blockers"] == [
        "restore_runtime_binding_policy_disabled",
        *(blocker for _flag, blocker in binding.ACTIVATION),
    ]
    assert binding.main([]) == 0
    assert "valid (dormant)" in capsys.readouterr().out


@pytest.mark.parametrize(
    "path",
    [
        (),
        ("activation",),
        ("external_runtime_record",),
        ("external_runtime_record", "record"),
        ("external_runtime_record", "signature"),
        ("runtime_record_ingress",),
        ("runtime_record_ingress", "publisher"),
        ("private_registry",),
        ("runtime",),
        ("runtime", "container_apps_environment"),
        ("runtime", "jobs", "provision"),
        ("identities",),
        ("identities", "dispatchers", "provision"),
        ("identities", "dispatchers", "provision", "federated_credential"),
        ("identities", "dispatchers", "provision", "broker_request"),
        ("identities", "brokers", "provision", "job_control"),
        ("identities", "jobs", "cleanup"),
        ("identities", "jobs", "cleanup", "role_assignment_ids"),
        ("evidence_ingress",),
        ("evidence_ingress", "private_restore_evidence_container"),
        ("evidence_ingress", "private_versioned_secret_source"),
        ("private_control_marker_store",),
        ("authenticated_controller_receipt",),
        ("egress",),
        ("offline_github_verifier",),
        ("per_rehearsal_postgres",),
        ("per_rehearsal_postgres", "private_dns"),
        ("cleanup",),
    ],
)
def test_policy_rejects_unknown_fields_at_every_boundary(
    path: tuple[str, ...],
) -> None:
    policy = _policy()
    _at(policy, path)["unexpected"] = "refuse"

    with pytest.raises(binding.RuntimeBindingFailure):
        binding.validate_policy(policy)


@pytest.mark.parametrize(
    ("path", "field", "value"),
    [
        (
            ("identities", "brokers", "provision", "job_control"),
            "actions",
            {
                "read": [
                    "Microsoft.App/jobs/read",
                    "Microsoft.Resources/subscriptions/resourceGroups/read",
                ],
                "start": ["Microsoft.App/jobs/start/action"],
            },
        ),
        (
            ("identities", "brokers", "cleanup", "job_control"),
            "data_actions",
            ["Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read"],
        ),
        (
            ("identities", "dispatchers", "provision"),
            "other_role_assignment_ids",
            ["CONFIGURE_EXACT_BROAD_ROLE_ASSIGNMENT_ID"],
        ),
        (
            ("identities", "jobs", "provision"),
            "federated_credential_ids",
            ["CONFIGURE_EXACT_FORBIDDEN_FIC_RESOURCE_ID"],
        ),
        (
            ("runtime", "jobs", "provision"),
            "system_assigned_identity",
            True,
        ),
        (
            ("identities", "jobs", "provision"),
            "delete_actions",
            ["Microsoft.Network/privateEndpoints/delete"],
        ),
    ],
)
def test_policy_rejects_identity_or_authority_broadening(
    path: tuple[str, ...], field: str, value: Any
) -> None:
    policy = _policy()
    _at(policy, path)[field] = value

    with pytest.raises(binding.RuntimeBindingFailure):
        binding.validate_policy(policy)


def test_dispatchers_and_unfederated_jobs_are_exactly_cross_bound() -> None:
    policy = binding.validate_policy(_policy())
    identities = policy["identities"]
    runtime_jobs = policy["runtime"]["jobs"]

    for action in ("provision", "cleanup"):
        dispatcher = identities["dispatchers"][action]
        job = identities["jobs"][action]
        assert dispatcher["federated_credential"]["subject"] == (
            f"repo:CosmaNeura/cosmaneura:environment:staging-restore-{action}"
        )
        broker = identities["brokers"][action]
        assert dispatcher["broker_request"]["job_read_or_start_actions"] == []
        assert (
            broker["job_control"]["selected_job_resource_id"]
            == runtime_jobs[action]["resource_id"]
        )
        assert broker["job_control"]["actions"] == {
            "read": ["Microsoft.App/jobs/read"],
            "start": ["Microsoft.App/jobs/start/action"],
        }
        assert broker["job_control"]["data_actions"] == []
        start = broker["job_control"]["start_request"]
        assert start["entity"] == "absent-or-zero-length"
        assert start["execution_template_overrides_permitted"] is False
        assert start["maximum_attempts"] == 1
        assert start["retries"] == 0
        assert job["federated_credential_ids"] == []
        assert job["attached_job_resource_ids"] == [runtime_jobs[action]["resource_id"]]
        assert runtime_jobs[action]["user_assigned_identity_resource_ids"] == [
            job["resource_id"]
        ]


def test_record_ingress_network_and_cleanup_contracts_are_fail_closed() -> None:
    policy = binding.validate_policy(_policy())
    record = policy["external_runtime_record"]
    ingress = policy["evidence_ingress"]
    postgres = policy["per_rehearsal_postgres"]
    cleanup = policy["cleanup"]

    assert record["embedded_in_image"] is False
    assert record["image_self_digest_permitted"] is False
    assert "uri" not in record["record"] and "sha256" not in record["record"]
    assert record["policy_binding_projection"]["excluded_top_level_fields"] == [
        "activation_ready",
        "activation",
        "activation_blockers",
    ]
    assert record["cycle_prohibitions"] == {
        "record_uri_or_digest_in_policy_permitted": False,
        "image_digest_in_policy_or_image_permitted": False,
        "final_job_configuration_digest_in_record_permitted": False,
        "configuration_template_digest": {
            "algorithm": "sha256",
            "serialization": "canonical-json-no-trailing-newline",
            "projection": "closed-world-aca-job-pointer-free-v1",
            "computed_from_authenticated_exact_get": True,
            "excluded_environment_variables": list(launch.POINTER_ENV_TO_FIELD),
            "excluded_arm_computed_fields": policy["launch_binding"][
                "canonical_job_projection"
            ]["excluded_arm_computed_fields"],
            "other_exclusions_permitted": False,
        },
    }
    assert all(
        "configuration_template_sha256" in policy["runtime"]["jobs"][action]
        and "configuration_sha256" not in policy["runtime"]["jobs"][action]
        for action in ("provision", "cleanup")
    )
    assert record["record"]["unknown_fields_permitted"] is False
    assert record["signature"]["offline_verification_required"] is True
    assert ingress["only_sources"] == [
        "immutable_launch_record",
        "private_restore_evidence_container",
        "private_versioned_secret_source",
    ]
    assert ingress["private_restore_evidence_container"]["container_name"] == (
        "restore-evidence-v1"
    )
    assert (
        ingress["private_restore_evidence_container"]["versionless_blob_uris_permitted"]
        is False
    )
    assert ingress["private_restore_evidence_container"]["blob_versioning_enabled"]
    assert policy["egress"]["default_action"] == "deny"
    assert set(policy["egress"]["allowed_destinations"]) == {
        "azure_resource_manager",
        "managed_identity",
        "private_evidence_blob",
        "private_runtime_binding_blob",
        "private_launch_mailbox_blob",
        "private_secret_vault",
        "private_control_marker_blob",
        "private_rehearsal_postgres",
        "azure_vnet_dns",
    }
    assert postgres["subnet_cidr"] == "10.76.0.64/27"
    assert postgres["private_dns"]["other_vnet_links_permitted"] is False
    assert (
        cleanup["dominant_tombstone"][
            "provision_may_create_after_partial_or_unknown_outcome"
        ]
        is True
    )
    assert cleanup["dominant_tombstone"]["retention_preconditions"] == {
        "legal_hold_enabled": True,
        "immutability_policy_mode": "Locked",
        "provider_create_only_semantics_proven": True,
    }
    assert cleanup["activation_order"] == [
        "dominant_tombstone_retention_bound",
        "cleanup_binding_implemented",
    ]
    assert cleanup["execution_order"] == [
        "validate_external_runtime_record_and_signature",
        "authenticate_controller_receipt_and_resource_ownership",
        "observe_or_create_and_read_back_dominant_tombstone",
        "delete_only_receipt_bound_resources",
        "prove_all_bound_resources_absent",
    ]
    assert cleanup["execution_order_activation_evidence"] == {
        "gate": "cleanup_binding_implemented",
        "required_before_gate_is_true": True,
        "must_cover_every_ordered_step": True,
        "evidence_sha256": "CONFIGURE_EXACT_RESTORE_CLEANUP_EXECUTION_ORDER_EVIDENCE_SHA256",
    }
    assert cleanup["delete_authority"]["authorized_job_identity"] == "cleanup"
    assert cleanup["bound_resource_fields"] == {
        "target": list(binding.TARGET_CLEANUP_FIELDS),
        "executor_side": list(binding.EXECUTOR_CLEANUP_FIELDS),
    }
    runtime_ingress = policy["runtime_record_ingress"]
    assert runtime_ingress["container_name"] == "restore-runtime-bindings-v1"
    assert len(runtime_ingress["allowed_job_identity_resource_ids"]) == 2
    assert runtime_ingress["content_addressed_versionless_blob_uris_permitted"] is True
    assert runtime_ingress["immutability"]["mode"] == "Locked"
    assert runtime_ingress["publisher"]["forbidden_authority_classes"] == [
        "blob-read",
        "blob-overwrite",
        "blob-delete",
        "source-data",
        "target-resource",
        "secret-read",
        "job-read-or-start",
    ]
    assert policy["private_registry"]["public_network_access"] == "Disabled"
    assert policy["private_registry"]["image_reference_mode"].startswith("digest-only")
    secret = ingress["private_versioned_secret_source"]["secrets"]
    assert (
        len(secret["server_scope_hmac_key"]["allowed_job_identity_resource_ids"]) == 1
    )
    assert len(secret["github_trusted_root"]["allowed_job_identity_resource_ids"]) == 2
    marker = policy["private_control_marker_store"]
    assert (
        marker["account_host"]
        != ingress["private_restore_evidence_container"]["account_host"]
    )
    assert policy["offline_github_verifier"]["maximum_invocations_per_job"] == {
        "provision": 2,
        "cleanup": 1,
    }
    assert policy["per_rehearsal_postgres"]["authentication"]["mode"].startswith(
        "microsoft-entra-"
    )


def test_executable_mode_rejects_dormancy_then_placeholders() -> None:
    with pytest.raises(binding.RuntimeBindingFailure, match="policy.not_activated"):
        binding.validate_policy(_policy(), executable=True)

    activated = _activated(_policy())
    with pytest.raises(binding.RuntimeBindingFailure, match="policy.placeholders"):
        binding.validate_policy(activated, executable=True)

    for path in binding.IMPLEMENTATION_PATHS:
        inconsistent = _activated(_policy())
        _at(inconsistent, path[:-1])[path[-1]] = False
        with pytest.raises(
            binding.RuntimeBindingFailure, match="activation.implementation"
        ):
            binding.validate_policy(inconsistent, executable=True)


def test_fully_pinned_activated_fixture_passes_executable_checks() -> None:
    configured = _configured_policy()

    assert binding.validate_policy(configured, executable=True) is configured


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (
            lambda policy: policy["runtime"]["jobs"]["cleanup"].__setitem__(
                "configuration_template_sha256",
                policy["runtime"]["jobs"]["provision"]["configuration_template_sha256"],
            ),
            "runtime.jobs.distinct",
        ),
        (
            lambda policy: policy["identities"]["dispatchers"]["cleanup"][
                "federated_credential"
            ].__setitem__(
                "resource_id",
                policy["identities"]["dispatchers"]["provision"][
                    "federated_credential"
                ]["resource_id"],
            ),
            "identities.distinct",
        ),
        (
            lambda policy: policy["identities"]["jobs"]["cleanup"][
                "role_assignment_ids"
            ].__setitem__(
                "target_cleanup",
                policy["identities"]["jobs"]["provision"]["role_assignment_ids"][
                    "source_reader"
                ],
            ),
            "identities.distinct",
        ),
        (
            lambda policy: policy["runtime_record_ingress"]["publisher"].__setitem__(
                "identity_resource_id",
                policy["identities"]["jobs"]["provision"]["resource_id"],
            ),
            "identities.distinct",
        ),
        (
            lambda policy: policy["launch_binding"]["mailbox"][
                "container_resource_ids"
            ].__setitem__(
                "provision",
                policy["runtime_record_ingress"]["container_resource_id"].replace(
                    "/runtime/", "/RUNTIME/"
                ),
            ),
            "evidence_ingress.binding",
        ),
        (
            lambda policy: policy["runtime_record_ingress"]["publisher"].__setitem__(
                "identity_resource_id",
                policy["identities"]["jobs"]["provision"]["resource_id"].replace(
                    "/runtime/", "/RUNTIME/"
                ),
            ),
            "identities.distinct",
        ),
        (
            lambda policy: policy["launch_binding"]["receipt_signing"][
                "broker_role_assignment_role_definition_ids"
            ].__setitem__(
                "cleanup", policy["identities"]["jobs"]["cleanup"]["resource_id"]
            ),
            "evidence_ingress.binding",
        ),
        (
            lambda policy: policy["private_registry"].__setitem__(
                "host", "other.azurecr.io"
            ),
            "evidence_ingress.private_binding",
        ),
        (
            lambda policy: policy["private_registry"].__setitem__(
                "repository", "restoreexecutor.azurecr.io/image:latest"
            ),
            "policy.private_registry.repository",
        ),
        (
            lambda policy: policy["private_registry"].__setitem__(
                "resource_id",
                "/subscriptions/------------------------------------/providers/Microsoft.ContainerRegistry/registries/x",
            ),
            "policy.private_registry.resource_id",
        ),
    ],
)
def test_executable_mode_rejects_identity_registry_or_digest_aliasing(
    mutate: Any, error: str
) -> None:
    policy = _configured_policy()
    mutate(policy)

    with pytest.raises(binding.RuntimeBindingFailure, match=error):
        binding.validate_policy(policy, executable=True)


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (
            lambda policy: policy["evidence_ingress"][
                "private_versioned_secret_source"
            ]["secrets"]["server_scope_hmac_key"].update(
                versioned_https_uri=(
                    "https://restoresecrets.vault.azure.net/secrets/hmac/wrong"
                )
            ),
            "evidence_ingress.secret.version_binding",
        ),
        (
            lambda policy: policy["evidence_ingress"][
                "private_restore_evidence_container"
            ]["objects"].update(source="caller_supplied"),
            "policy.evidence_ingress.private_restore_evidence_container.objects.source",
        ),
    ],
)
def test_executable_mode_rejects_unbound_external_inputs(
    mutate: Any, error: str
) -> None:
    policy = _configured_policy()
    mutate(policy)

    with pytest.raises(binding.RuntimeBindingFailure, match=error):
        binding.validate_policy(policy, executable=True)


def test_external_record_is_content_addressed_signed_and_sealed() -> None:
    policy = _configured_policy()
    raw, provision_reference, cleanup_reference, signature = _external_binding(policy)

    result = binding.validate_external_runtime_binding(
        policy,
        raw,
        provision_reference=provision_reference,
        cleanup_reference=cleanup_reference,
        signature_verification=signature,
    )

    assert type(result) is binding.ValidatedExternalRuntimeBinding
    assert result._is_validated()
    assert result.record_bytes is raw
    assert result.record_sha256 == binding._digest(raw)
    assert result.record["policy_binding_sha256"] == binding._policy_binding_digest(
        policy
    )
    assert result.record["policy_binding_sha256"] != binding._digest(
        binding._canonical(policy)
    )
    assert result.record["image_digest"] == f"sha256:{'a' * 64}"
    assert result.image_reference == (
        f"restoreexecutor.azurecr.io/cosmaneura/restore-executor@sha256:{'a' * 64}"
    )
    with pytest.raises(binding.RuntimeBindingFailure, match="reference_invalid"):
        binding.validate_external_runtime_binding(
            policy,
            raw,
            provision_reference=cleanup_reference,
            cleanup_reference=provision_reference,
            signature_verification=signature,
        )
    with pytest.raises(binding.RuntimeBindingFailure):
        binding.ValidatedExternalRuntimeBinding()
    with pytest.raises(binding.RuntimeBindingFailure):
        binding.VerifiedExternalRuntimeRecordSignature()
    with pytest.raises(binding.RuntimeBindingFailure):
        binding.ExternalRuntimeBindingReference()
    with pytest.raises(binding.RuntimeBindingFailure, match="reference_invalid"):
        binding.ExternalRuntimeBindingReference._from_values([], "invalid", {})  # type: ignore[arg-type]


def test_external_record_rejects_unsealed_signature_and_circular_layout() -> None:
    policy = _configured_policy()
    raw, provision_reference, cleanup_reference, _signature = _external_binding(policy)
    forged = object.__new__(binding.VerifiedExternalRuntimeRecordSignature)
    with pytest.raises(
        binding.RuntimeBindingFailure, match="signature_verification.invalid"
    ):
        binding.validate_external_runtime_binding(
            policy,
            raw,
            provision_reference=provision_reference,
            cleanup_reference=cleanup_reference,
            signature_verification=forged,
        )

    circular = _policy()
    circular["external_runtime_record"]["record"]["sha256"] = (
        "CONFIGURE_EXACT_RUNTIME_RECORD_SHA256"
    )
    with pytest.raises(
        binding.RuntimeBindingFailure, match="external_runtime_record.record"
    ):
        binding.validate_runtime_binding_policy(circular)

    final_digest = _policy()
    fields = final_digest["external_runtime_record"]["record"]["required_fields"]
    index = fields.index("provision_job_configuration_template_sha256")
    fields[index] = "provision_job_configuration_sha256"
    with pytest.raises(
        binding.RuntimeBindingFailure, match="external_runtime_record.record"
    ):
        binding.validate_runtime_binding_policy(final_digest)


def test_external_record_rejects_noncanonical_content_addressed_path() -> None:
    policy = _configured_policy()
    raw, provision_reference, cleanup_reference, signature = _external_binding(policy)
    bad_pointer = {
        "record_uri": provision_reference.record_uri.replace(".json", "/record.json"),
        "record_sha256": provision_reference.record_sha256,
        "signature_bundle_uri": provision_reference.signature_bundle_uri,
        "signature_bundle_sha256": provision_reference.signature_bundle_sha256,
    }
    misplaced = (
        binding.ExternalRuntimeBindingReference._from_authenticated_job_observation(
            _job_observation(policy, "provision", bad_pointer)
        )
    )
    misplaced_cleanup = (
        binding.ExternalRuntimeBindingReference._from_authenticated_job_observation(
            _job_observation(policy, "cleanup", bad_pointer)
        )
    )

    with pytest.raises(binding.RuntimeBindingFailure, match="reference_binding"):
        binding.validate_external_runtime_binding(
            policy,
            raw,
            provision_reference=misplaced,
            cleanup_reference=misplaced_cleanup,
            signature_verification=signature,
        )


def test_external_record_rejects_divergent_job_configuration_pointers() -> None:
    policy = _configured_policy()
    raw, provision_reference, cleanup_reference, signature = _external_binding(policy)
    other_bundle_digest = f"sha256:{'c' * 64}"
    divergent_pointer = {
        "record_uri": cleanup_reference.record_uri,
        "record_sha256": cleanup_reference.record_sha256,
        "signature_bundle_uri": (
            "https://restoreevidence.blob.core.windows.net/restore-runtime-bindings-v1/"
            f"{other_bundle_digest[7:]}.sigstore.json"
        ),
        "signature_bundle_sha256": other_bundle_digest,
    }
    divergent_cleanup = (
        binding.ExternalRuntimeBindingReference._from_authenticated_job_observation(
            _job_observation(policy, "cleanup", divergent_pointer)
        )
    )

    with pytest.raises(binding.RuntimeBindingFailure, match="reference_divergence"):
        binding.validate_external_runtime_binding(
            policy,
            raw,
            provision_reference=provision_reference,
            cleanup_reference=divergent_cleanup,
            signature_verification=signature,
        )


def test_parser_rejects_duplicate_keys_and_nonfinite_numbers() -> None:
    for raw in (b'{"schema_version":1,"schema_version":1}', b'{"x":NaN}'):
        with pytest.raises(binding.RuntimeBindingFailure):
            binding._parse_policy(raw)
    for resource_id in (
        "/subscriptions/------------------------------------/providers/Microsoft.X/types/name",
        "/subscriptions/11111111-1111-4111-8111-111111111111/providers/Microsoft.X/types//name",
        "/subscriptions/11111111-1111-4111-8111-111111111111/providers/Microsoft.X/types/name/",
    ):
        assert not binding._azure_resource_id(resource_id)


def test_loader_rejects_noncanonical_paths(tmp_path: Path) -> None:
    copied = tmp_path / POLICY.name
    copied.write_bytes(POLICY.read_bytes())

    with pytest.raises(binding.RuntimeBindingFailure, match="policy.untrusted_path"):
        binding.load_policy(copied)
