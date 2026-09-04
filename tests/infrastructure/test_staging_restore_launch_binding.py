from __future__ import annotations

# fmt: off
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import pytest

from scripts import (
    validate_staging_restore_launch_binding as launch,
)
from scripts import (
    validate_staging_restore_runtime_binding as runtime,
)

from tests.infrastructure import test_staging_restore_runtime_binding as helpers

NOW = datetime(2026, 9, 4, 12, 1, tzinfo=UTC)


def _policy() -> dict[str, Any]:
    return helpers._configured_policy()


def _arm_response(policy: dict[str, Any], action: str, *, body: dict[str, Any] | None = None) -> launch.AuthenticatedArmGetResponse:
    document = body or helpers._job_document(policy, action)
    return launch.AuthenticatedArmGetResponse._from_reviewed_managed_identity_adapter(
        resource_id=policy["runtime"]["jobs"][action]["resource_id"],
        api_version="2024-03-01",
        status_code=200,
        content_type="application/json; charset=utf-8",
        etag=f'"{action}-etag"',
        observed_at=datetime(2026, 9, 4, 12, 2, tzinfo=UTC),
        body=runtime._canonical(document),
    )


def _observations(policy: dict[str, Any], pointers: dict[str, str] | None = None) -> tuple[launch.AuthenticatedJobConfigurationObservation, launch.AuthenticatedJobConfigurationObservation]:
    values = pointers or helpers._pointer_values()
    observations = []
    for action in ("provision", "cleanup"):
        response = _arm_response(policy, action, body=helpers._job_document(policy, action, values))
        observations.append(launch.validate_job_configuration_observation(policy, job_kind=action, response=response))
    return observations[0], observations[1]


def _record(policy: dict[str, Any], *, action: str, runtime_record_sha256: str) -> tuple[bytes, str, str]:
    secret_policy = policy["evidence_ingress"]["private_versioned_secret_source"]
    root = secret_policy["secrets"]["github_trusted_root"]
    rehearsal_id = "0123456789ab"
    common: dict[str, Any] = {
        "schema_version": 1,
        "launch_id": "launch-0123456789abcdef",
        "policy_id": policy["policy_id"],
        "policy_binding_sha256": runtime._policy_binding_digest(policy),
        "action": action,
        "repository": "CosmaNeura/cosmaneura",
        "protected_ref": "refs/heads/master",
        "workflow_sha": "d" * 40,
        "run_id": "12345",
        "run_attempt": "1",
        "runtime_record_sha256": runtime_record_sha256,
        "github_trusted_root": {
            "versioned_https_uri": root["versioned_https_uri"],
            "version": root["version"],
            "sha256": root["sha256"],
        },
        "created_at": "2026-09-04T12:01:00Z",
        "expires_at": "2026-09-04T13:01:00Z",
    }
    if action == "provision":
        evidence_files = {}
        names = policy["launch_binding"]["launch_record"]["provision"]["exact_evidence_file_names"]
        for index, name in enumerate(names, 1):
            version = f"evidence-version-{index}"
            evidence_files[name] = {
                "versioned_https_uri": (f"https://restoreevidence.blob.core.windows.net/restore-evidence-v1/{rehearsal_id}/{name}?versionid={version}"),
                "version_id": version,
                "sha256": f"sha256:{index:064x}",
            }
        hmac = secret_policy["secrets"]["server_scope_hmac_key"]
        common["payload"] = {
            "rehearsal_id": rehearsal_id,
            "evidence_files": evidence_files,
            "github_attestation_bundle": {
                "versioned_https_uri": (f"https://restoreevidence.blob.core.windows.net/restore-evidence-v1/{rehearsal_id}/attestation.json?versionid=attestation-version"),
                "version_id": "attestation-version",
                "sha256": f"sha256:{'e' * 64}",
            },
            "server_scope_hmac_key": {
                "versioned_https_uri": hmac["versioned_https_uri"],
                "version": hmac["version"],
                "encoding": "base64url-no-padding",
                "decoded_sha256": f"sha256:{'f' * 64}",
            },
            "ttl_hours": 1,
        }
    else:
        common["payload"] = {
            "mode": "selected_rehearsal",
            "rehearsal_id": rehearsal_id,
        }
    raw = runtime._canonical(common)
    digest = runtime._digest(raw)
    mailbox = policy["launch_binding"]["mailbox"]
    uri = f"https://{mailbox['account_host']}/{mailbox['container_names'][action]}/{digest[7:]}.json"
    return raw, digest, uri


def _validated_record(policy: dict[str, Any], *, action: str, runtime_record_sha256: str) -> launch.ValidatedLaunchRecord:
    raw, digest, uri = _record(policy, action=action, runtime_record_sha256=runtime_record_sha256)
    return launch.validate_launch_record(policy, raw, record_sha256=digest, record_uri=uri)


def _caller(policy: dict[str, Any], action: str, record: launch.ValidatedLaunchRecord, *, claim_changes: dict[str, Any] | None = None, replay_key_sha256: str | None = None) -> launch.VerifiedOneUseGithubOidc:
    request = policy["identities"]["dispatchers"][action]["broker_request"]
    now = int(NOW.timestamp())
    claims = {
        "iss": request["exact_issuer"],
        "sub": request["exact_subject"],
        "aud": request["oidc_audience_client_id"],
        "repository": request["exact_repository"],
        "ref": request["exact_ref"],
        "environment": request["exact_environment"],
        "workflow_ref": request["exact_workflow_ref"],
        "workflow_sha": record.workflow_sha,
        "run_id": record.run_id,
        "run_attempt": record.run_attempt,
        "jti": "one-use-token-id",
        "iat": now - 1,
        "exp": now + 299,
    }
    claims.update(claim_changes or {})
    replay_key = runtime._digest(
        runtime._canonical(
            {
                "iss": claims["iss"],
                "jti": claims["jti"],
                "action": action,
                "launch_record_sha256": record.record_sha256,
            }
        )
    )
    return launch.VerifiedOneUseGithubOidc._from_reviewed_signature_nonce_replay_adapter(
        policy=policy,
        action=action,
        launch_record=record,
        verified_claim_projection=claims,
        replay_key_sha256=replay_key_sha256 or replay_key,
        replay_receipt_sha256=f"sha256:{'c' * 64}",
        consumed_at=NOW,
    )


def _start(policy: dict[str, Any], action: str, record: launch.ValidatedLaunchRecord) -> launch.BrokerStartAuthorization:
    return launch.validate_broker_start_request(policy, action=action, caller=_caller(policy, action, record), launch_record=record, observed_at=datetime(2026, 9, 4, 12, 1, 30, tzinfo=UTC), method="POST", entity=b"", query_parameters=(("api-version", "2024-03-01"),), content_type=None, attempt_number=1, retry_count=0, ambiguous_previous_attempt=False)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda body: body["properties"]["template"]["containers"].append(deepcopy(body["properties"]["template"]["containers"][0])),
        lambda body: body["properties"]["template"].update(initContainers=[{"name": "untrusted"}]),
        lambda body: body.update(type={}),
        lambda body: next(iter(body["identity"]["userAssignedIdentities"].values())).update(clientId="00000000-0000-4000-8000-000000000000"),
        lambda body: body["properties"]["template"]["containers"][0]["env"].append({"name": "COSMANEURA_RUNTIME_RECORD_URI", "value": "https://duplicate.invalid"}),
        lambda body: body["properties"]["template"]["containers"][0]["env"].append({"name": launch.EXECUTION_NAME_ENV, "value": "spoofed"}),
        lambda body: body["properties"]["template"]["containers"][0]["env"][0].update(secretRef="forbidden"),
    ],
)
def test_job_projection_rejects_override_surfaces(mutate: Any) -> None:
    policy = _policy()
    body = helpers._job_document(policy, "provision")
    mutate(body)

    with pytest.raises(launch.LaunchBindingFailure):
        launch.validate_job_configuration_observation(
            policy,
            job_kind="provision",
            response=_arm_response(policy, "provision", body=body),
        )


def test_launch_record_materializes_exact_versioned_evidence_and_action_union() -> None:
    policy = _policy()
    runtime_digest = helpers._pointer_values()["record_sha256"]

    provision = _validated_record(policy, action="provision", runtime_record_sha256=runtime_digest)
    cleanup = _validated_record(policy, action="cleanup", runtime_record_sha256=runtime_digest)
    with pytest.raises(launch.LaunchBindingFailure, match="launch_record.invalid"):
        launch.validate_launch_record(policy, b"{", record_sha256=runtime._digest(b"{"), record_uri=provision.record_uri)

    assert provision.action == "provision"
    assert cleanup.action == "cleanup"
    raw, digest, uri = _record(policy, action="provision", runtime_record_sha256=runtime_digest)
    changed = runtime._parse_policy(raw)
    evidence_uri = changed["payload"]["evidence_files"]["SHA256SUMS"]
    evidence_uri["versioned_https_uri"] = evidence_uri["versioned_https_uri"].replace("0123456789ab", "fedcba987654")
    changed_raw = runtime._canonical(changed)
    changed_digest = runtime._digest(changed_raw)
    with pytest.raises(launch.LaunchBindingFailure, match="launch_record.evidence"):
        launch.validate_launch_record(policy, changed_raw, record_sha256=changed_digest, record_uri=uri.replace(digest[7:], changed_digest[7:]))


@pytest.mark.parametrize(
    "changes",
    [
        {"entity": b"{}"},
        {"query_parameters": ()},
        {"query_parameters": (("api-version", "2024-03-01"), ("x", "y"))},
        {"content_type": "application/json"},
        {"attempt_number": 2},
        {"retry_count": 1},
        {"ambiguous_previous_attempt": True},
        {"observed_at": datetime(2026, 9, 4, 13, 1, tzinfo=UTC)},
        {"caller": {}},
    ],
)
def test_broker_start_is_exact_zero_body_one_attempt(changes: dict[str, Any]) -> None:
    policy = _policy()
    record = _validated_record(policy, action="provision", runtime_record_sha256=helpers._pointer_values()["record_sha256"])
    arguments: dict[str, Any] = {"policy": policy, "action": "provision", "caller": _caller(policy, "provision", record), "launch_record": record, "observed_at": datetime(2026, 9, 4, 12, 1, 30, tzinfo=UTC), "method": "POST", "entity": None, "query_parameters": (("api-version", "2024-03-01"),), "content_type": None, "attempt_number": 1, "retry_count": 0, "ambiguous_previous_attempt": False}
    arguments.update(changes)

    with pytest.raises(launch.LaunchBindingFailure, match="job_start"):
        launch.validate_broker_start_request(**arguments)


@pytest.mark.parametrize("changes", [{"claim_changes": {"workflow_sha": "e" * 40}}, {"replay_key_sha256": f"sha256:{'0' * 64}"}])
def test_oidc_is_sealed_one_use_and_bound_to_launch_claims(changes: dict[str, Any]) -> None:
    policy = _policy()
    record = _validated_record(policy, action="cleanup", runtime_record_sha256=helpers._pointer_values()["record_sha256"])
    assert _caller(policy, "cleanup", record).action == "cleanup"
    with pytest.raises(launch.LaunchBindingFailure, match="broker_authentication"):
        _caller(policy, "cleanup", record, **changes)


def test_receipt_binds_runtime_both_jobs_and_actual_execution_template() -> None:
    policy = _policy()
    runtime_raw, provision_ref, cleanup_ref, signature = helpers._external_binding(policy)
    runtime_record = runtime.validate_external_runtime_binding(
        policy,
        runtime_raw,
        provision_reference=provision_ref,
        cleanup_reference=cleanup_ref,
        signature_verification=signature,
    )
    pointer = {field: getattr(provision_ref, field) for field in runtime.RUNTIME_REFERENCE_FIELDS}
    provision, cleanup = _observations(policy, pointer)
    record = _validated_record(
        policy,
        action="provision",
        runtime_record_sha256=runtime_record.record_sha256,
    )
    job_body = helpers._job_document(policy, "provision", pointer)
    execution_id = f"{provision.job_resource_id}/executions/execution-1"
    execution_body = {
        "id": execution_id,
        "name": "execution-1",
        "type": "Microsoft.App/jobs/executions",
        "properties": {
            "status": "Running",
            "template": job_body["properties"]["template"],
        },
    }
    response = launch.AuthenticatedArmGetResponse._from_reviewed_managed_identity_adapter(
        resource_id=execution_id,
        api_version="2024-03-01",
        status_code=200,
        content_type="application/json",
        etag='"execution-etag"',
        observed_at=datetime(2026, 9, 4, 12, 3, tzinfo=UTC),
        body=runtime._canonical(execution_body),
    )
    execution = launch.validate_execution_observation(policy, action="provision", response=response)
    authorization = _start(policy, "provision", record)
    start_arguments: dict[str, Any] = {
        "authorization": authorization,
        "status_code": 200,
        "content_type": "application/json",
        "response_body": runtime._canonical(execution_body),
        "echoed_client_request_id": authorization.client_request_id,
        "observed_at": datetime(2026, 9, 4, 12, 2, 30, tzinfo=UTC),
    }
    with pytest.raises(launch.LaunchBindingFailure, match="job_start_response.invalid"):
        launch.AuthenticatedBrokerStartResponse._from_reviewed_broker_transport(**start_arguments, provider_request_id="x" * 65_536)
    start_response = launch.AuthenticatedBrokerStartResponse._from_reviewed_broker_transport(
        **start_arguments,
        provider_request_id="12345678-1234-1234-1234-123456789abc",
    )

    receipt_bytes = launch.serialize_broker_launch_receipt(
        policy,
        launch_record=record,
        runtime_record=runtime_record,
        provision_observation=provision,
        cleanup_observation=cleanup,
        start_response=start_response,
        execution_observation=execution,
    )
    receipt_uri, signature_uri = launch._execution_receipt_uris(policy, "provision", "execution-1")
    signing = policy["launch_binding"]["receipt_signing"]
    adapter_arguments: dict[str, Any] = {"policy": policy, "action": "provision", "execution_name": "execution-1", "execution_name_environment_variable": launch.EXECUTION_NAME_ENV, "receipt_uri": receipt_uri, "signature_bundle_uri": signature_uri, "retrieval_statuses": (404, 404, 200), "retrieval_elapsed_seconds": 4, "receipt_bytes": receipt_bytes, "signature_bundle_bytes": b"verified-signature-bundle", "verified_receipt_sha256": runtime._digest(receipt_bytes), "verified_algorithm": signing["algorithm"], "verified_signing_key_resource_id": signing["key_resource_id"], "verified_signing_key_version": signing["key_version"], "verified_public_key_sha256": signing["public_key_sha256"], "observed_at": datetime(2026, 9, 4, 12, 4, tzinfo=UTC)}
    with pytest.raises(launch.LaunchBindingFailure, match="launch_receipt.authentication"):
        launch.AuthenticatedBrokerLaunchReceipt._from_reviewed_platform_metadata_mailbox_signature_adapter(**(adapter_arguments | {"retrieval_statuses": (404,)}))
    authenticated = launch.AuthenticatedBrokerLaunchReceipt._from_reviewed_platform_metadata_mailbox_signature_adapter(**adapter_arguments)
    receipt = launch.validate_received_launch_receipt(
        policy,
        authenticated_receipt=authenticated,
        launch_record=record,
        runtime_record=runtime_record,
    )
    assert receipt["launch_record_sha256"] == record.record_sha256
    assert receipt["execution_template_sha256"] == (provision.execution_template_sha256)
    assert receipt["provision_job_etag"] == '"provision-etag"'

    old_body = deepcopy(execution_body)
    old_id = f"{provision.job_resource_id}/executions/older-execution"
    old_body.update(id=old_id, name="older-execution")
    old_response = launch.AuthenticatedArmGetResponse._from_reviewed_managed_identity_adapter(
        resource_id=old_id,
        api_version="2024-03-01",
        status_code=200,
        content_type="application/json",
        etag='"old-execution-etag"',
        observed_at=datetime(2026, 9, 4, 12, 3, tzinfo=UTC),
        body=runtime._canonical(old_body),
    )
    old_execution = launch.validate_execution_observation(policy, action="provision", response=old_response)
    with pytest.raises(launch.LaunchBindingFailure, match="launch_receipt.binding"):
        launch.serialize_broker_launch_receipt(
            policy,
            launch_record=record,
            runtime_record=runtime_record,
            provision_observation=provision,
            cleanup_observation=cleanup,
            start_response=start_response,
            execution_observation=old_execution,
        )

    divergent = dict(pointer)
    divergent["signature_bundle_sha256"] = f"sha256:{'9' * 64}"
    divergent["signature_bundle_uri"] = divergent["signature_bundle_uri"].replace(pointer["signature_bundle_sha256"][7:], "9" * 64)
    divergent_provision, divergent_cleanup = _observations(policy, divergent)
    with pytest.raises(launch.LaunchBindingFailure, match="launch_receipt.binding"):
        launch.serialize_broker_launch_receipt(
            policy,
            launch_record=record,
            runtime_record=runtime_record,
            provision_observation=divergent_provision,
            cleanup_observation=divergent_cleanup,
            start_response=start_response,
            execution_observation=execution,
        )

# fmt: on
