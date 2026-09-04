from __future__ import annotations

# fmt: off
from copy import copy, deepcopy
from datetime import UTC, datetime
from typing import Any

import pytest

from scripts import validate_staging_restore_launch_binding as launch
from scripts import validate_staging_restore_runtime_binding as runtime

from tests.infrastructure import test_staging_restore_runtime_binding as helpers

NOW = datetime(2026, 9, 4, 12, 1, tzinfo=UTC)
VALIDATION_NOW = datetime(2026, 9, 4, 12, 5, tzinfo=UTC)
def _policy() -> dict[str, Any]:
    return helpers._configured_policy()
def _arm_response(policy: dict[str, Any], action: str, *, body: dict[str, Any] | bytes | None = None) -> launch.AuthenticatedArmGetResponse:
    document = body or helpers._job_document(policy, action)
    return launch.AuthenticatedArmGetResponse._from_reviewed_managed_identity_adapter(resource_id=policy["runtime"]["jobs"][action]["resource_id"], api_version="2024-03-01", status_code=200, content_type="application/json; charset=utf-8", observed_at=datetime(2026, 9, 4, 12, 2, tzinfo=UTC), body=document if type(document) is bytes else runtime._canonical(document))
def _observations(policy: dict[str, Any], pointers: dict[str, str] | None = None) -> tuple[launch.AuthenticatedJobConfigurationObservation, launch.AuthenticatedJobConfigurationObservation]:
    values = pointers or helpers._pointer_values()
    documents = [helpers._job_document(policy, action, values) for action in ("provision", "cleanup")]
    documents[0]["properties"]["template"].pop("initContainers")
    documents[1]["properties"]["template"].pop("volumes")
    observations = [launch.validate_job_configuration_observation(policy, job_kind=action, response=_arm_response(policy, action, body=body)) for action, body in zip(("provision", "cleanup"), documents, strict=True)]
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
        "github_trusted_root": {"versioned_https_uri": root["versioned_https_uri"], "version": root["version"], "sha256": root["sha256"]},
        "created_at": "2026-09-04T12:01:00Z",
        "expires_at": "2026-09-04T13:01:00Z",
    }
    if action == "provision":
        evidence_files = {}
        names = policy["launch_binding"]["launch_record"]["provision"]["exact_evidence_file_names"]
        for index, name in enumerate(names, 1):
            version = f"evidence-version-{index}"
            evidence_files[name] = {"versioned_https_uri": f"https://restoreevidence.blob.core.windows.net/restore-evidence-v1/{rehearsal_id}/{name}?versionid={version}", "version_id": version, "sha256": f"sha256:{index:064x}"}
        hmac = secret_policy["secrets"]["server_scope_hmac_key"]
        common["payload"] = {
            "rehearsal_id": rehearsal_id,
            "evidence_files": evidence_files,
            "github_attestation_bundle": {"versioned_https_uri": f"https://restoreevidence.blob.core.windows.net/restore-evidence-v1/{rehearsal_id}/attestation.json?versionid=attestation-version", "version_id": "attestation-version", "sha256": f"sha256:{'e' * 64}"},
            "server_scope_hmac_key": {"versioned_https_uri": hmac["versioned_https_uri"], "version": hmac["version"], "encoding": "base64url-no-padding", "decoded_sha256": f"sha256:{'f' * 64}"},
            "ttl_hours": 1,
        }
    else:
        common["payload"] = {"mode": "selected_rehearsal", "rehearsal_id": rehearsal_id}
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
        runtime._canonical({"iss": claims["iss"], "jti": claims["jti"]})
    )
    return launch.VerifiedOneUseGithubOidc._from_reviewed_signature_nonce_replay_adapter(policy=policy, action=action, launch_record=record, verified_claim_projection=claims, replay_key_sha256=replay_key_sha256 or replay_key, replay_receipt_sha256=f"sha256:{'c' * 64}", consumed_at=NOW)
def _start(policy: dict[str, Any], action: str, record: launch.ValidatedLaunchRecord) -> launch.BrokerStartAuthorization:
    job_id = policy["runtime"]["jobs"][action]["resource_id"]
    return launch.validate_broker_start_request(policy, action=action, caller=_caller(policy, action, record), launch_record=record, observed_at=datetime(2026, 9, 4, 12, 1, 30, tzinfo=UTC), request_url=f"https://management.azure.com{job_id}/start?api-version=2024-03-01", method="POST", entity=b"", query_parameters=(("api-version", "2024-03-01"),), content_type=None, attempt_number=1, retry_count=0, ambiguous_previous_attempt=False)
@pytest.mark.parametrize(
    "mutate",
    [
        lambda body: body["properties"]["template"]["containers"].append(deepcopy(body["properties"]["template"]["containers"][0])),
        lambda body: body["properties"]["template"].update(initContainers=[{"name": "untrusted"}]),
        lambda body: body["properties"]["template"].update(volumes=[{"name": "untrusted"}]),
        lambda body: body["properties"]["template"].update(volumes={}),
        lambda body: body.update(type={}),
        lambda body: next(iter(body["identity"]["userAssignedIdentities"].values())).update(clientId="00000000-0000-4000-8000-000000000000"),
        lambda body: body["properties"]["template"]["containers"][0]["env"].append({"name": "COSMANEURA_RUNTIME_RECORD_URI", "value": "https://duplicate.invalid"}),
        lambda body: body["properties"]["template"]["containers"][0]["env"].append({"name": launch.EXECUTION_NAME_ENV, "value": "spoofed"}),
        lambda body: body["properties"]["template"]["containers"][0]["env"][0].update(secretRef="forbidden"),
        lambda body: body["properties"]["configuration"].update(replicaRetryLimit=False),
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
@pytest.mark.parametrize(("field", "value"), [("parallelism", 2), ("replicaCompletionCount", 2), ("parallelism", True), ("replicaCompletionCount", "1")])
@pytest.mark.parametrize("action", ("provision", "cleanup"))
def test_manual_trigger_cardinality_is_exact_even_with_matching_job_digest(action: str, field: str, value: Any) -> None:
    policy = _policy()
    body = helpers._job_document(policy, action)
    projection, _pointer, _template = launch._job_projection(body, policy=policy, job_kind=action)
    body["properties"]["configuration"]["manualTriggerConfig"][field] = value
    projection["properties"]["configuration"]["manualTriggerConfig"][field] = value
    policy["runtime"]["jobs"][action]["configuration_template_sha256"] = runtime._digest(runtime._canonical(projection))
    with pytest.raises(launch.LaunchBindingFailure, match="job_observation.trigger"):
        launch.validate_job_configuration_observation(policy, job_kind=action, response=_arm_response(policy, action, body=body))
@pytest.mark.parametrize("literal", (b"1e400", b"-1e400"))
def test_job_observation_rejects_exponent_overflow_before_matching_digest(literal: bytes) -> None:
    policy, number = _policy(), float(literal)
    body = helpers._job_document(policy, "provision")
    projection, _pointer, _template = launch._job_projection(body, policy=policy, job_kind="provision")
    raw = runtime._canonical(body).replace(b'"cpu":1', b'"cpu":' + literal, 1)
    body["properties"]["template"]["containers"][0]["resources"]["cpu"] = number
    projection["properties"]["template"]["containers"][0]["resources"]["cpu"] = number
    policy["runtime"]["jobs"]["provision"]["configuration_template_sha256"] = runtime._digest(runtime.json.dumps(projection, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode())
    with pytest.raises(launch.LaunchBindingFailure, match="job_observation.invalid"):
        launch.validate_job_configuration_observation(policy, job_kind="provision", response=_arm_response(policy, "provision", body=raw))
    with pytest.raises(launch.LaunchBindingFailure, match="canonical.invalid"):
        launch._canonical({"value": number})
@pytest.mark.parametrize("mutate", [lambda value: value["payload"]["evidence_files"]["SHA256SUMS"].update(versioned_https_uri=value["payload"]["evidence_files"]["SHA256SUMS"]["versioned_https_uri"].replace("0123456789ab", "fedcba987654")), lambda value: value["payload"]["evidence_files"]["SHA256SUMS"].update(sha256={}), lambda value: value.update(schema_version=True)])
def test_launch_record_materializes_exact_versioned_evidence_and_action_union(mutate: Any) -> None:
    policy = _policy()
    runtime_digest = helpers._pointer_values()["record_sha256"]
    provision = _validated_record(policy, action="provision", runtime_record_sha256=runtime_digest)
    cleanup = _validated_record(policy, action="cleanup", runtime_record_sha256=runtime_digest)
    with pytest.raises(launch.LaunchBindingFailure, match="launch_record.invalid"):
        launch.validate_launch_record(policy, b"{", record_sha256=runtime._digest(b"{"), record_uri=provision.record_uri)
    assert provision.action == "provision"
    assert cleanup.action == "cleanup"
    expected_names = policy["launch_binding"]["launch_record"]["provision"][
        "exact_evidence_file_names"
    ]
    assert provision.evidence_file_sha256s == tuple(
        (name, f"sha256:{index:064x}")
        for index, name in enumerate(expected_names, 1)
    )
    assert provision.attestation_bundle_sha256 == f"sha256:{'e' * 64}"
    assert provision.server_scope_hmac_key_sha256 == f"sha256:{'f' * 64}"
    assert provision.trusted_root_sha256 == policy["evidence_ingress"][
        "private_versioned_secret_source"
    ]["secrets"]["github_trusted_root"]["sha256"]
    assert provision.ttl_hours == 1
    assert cleanup.evidence_file_sha256s == ()
    assert cleanup.attestation_bundle_sha256 is None
    assert cleanup.server_scope_hmac_key_sha256 is None
    assert cleanup.ttl_hours is None
    raw, digest, uri = _record(policy, action="provision", runtime_record_sha256=runtime_digest)
    with pytest.raises(launch.LaunchBindingFailure, match="launch_record.uri"):
        launch.validate_launch_record(policy, raw, record_sha256=digest, record_uri=7)  # type: ignore[arg-type]
    changed = runtime._parse_policy(raw)
    mutate(changed)
    changed_raw = runtime._canonical(changed)
    changed_digest = runtime._digest(changed_raw)
    with pytest.raises(launch.LaunchBindingFailure, match="launch_record"):
        launch.validate_launch_record(policy, changed_raw, record_sha256=changed_digest, record_uri=uri.replace(digest[7:], changed_digest[7:]))
@pytest.mark.parametrize(
    "changes",
    [
        {"entity": b"{}"},
        {"entity": {}},
        {"entity": []},
        {"request_url": "https://management.azure.com/wrong/start?api-version=2024-03-01"},
        {"request_url": lambda job_id: f"https://management.azure.com{job_id}/starts?api-version=2024-03-01"},
        {"request_url": lambda job_id: f"https://management.azure.com{job_id}?api-version=2024-03-01"},
        {"request_url": lambda job_id: f"https://management.azure.com{job_id}/%73tart?api-version=2024-03-01"},
        {"request_url": lambda job_id: f"https://management.azure.com{job_id}/start?api-version=2024-03-01&api-version=2024-03-01"},
        {"request_url": lambda job_id: f"https://management.azure.com{job_id}/start?api-version=2024-03-01#fragment"},
        {"request_url": lambda job_id: f"https://management.example{job_id}/start?api-version=2024-03-01"},
        {"query_parameters": ()},
        {"query_parameters": (("api-version", "2024-03-01"), ("x", "y"))},
        {"content_type": "application/json"},
        {"attempt_number": 2},
        {"attempt_number": True},
        {"retry_count": 1},
        {"retry_count": False},
        {"ambiguous_previous_attempt": True},
        {"observed_at": datetime(2026, 9, 4, 13, 1, tzinfo=UTC)},
        {"caller": {}},
    ],
)
def test_broker_start_is_exact_zero_body_one_attempt(changes: dict[str, Any]) -> None:
    policy = _policy()
    record = _validated_record(policy, action="provision", runtime_record_sha256=helpers._pointer_values()["record_sha256"])
    job_id = policy["runtime"]["jobs"]["provision"]["resource_id"]
    arguments: dict[str, Any] = {"policy": policy, "action": "provision", "caller": _caller(policy, "provision", record), "launch_record": record, "observed_at": datetime(2026, 9, 4, 12, 1, 30, tzinfo=UTC), "request_url": f"https://management.azure.com{job_id}/start?api-version=2024-03-01", "method": "POST", "entity": None, "query_parameters": (("api-version", "2024-03-01"),), "content_type": None, "attempt_number": 1, "retry_count": 0, "ambiguous_previous_attempt": False}
    for name, value in changes.items():
        arguments[name] = value(job_id) if callable(value) else value
    with pytest.raises(launch.LaunchBindingFailure, match="job_start"):
        launch.validate_broker_start_request(**arguments)
@pytest.mark.parametrize("changes", [{"claim_changes": {"workflow_sha": "e" * 40}}, {"replay_key_sha256": f"sha256:{'0' * 64}"}, {"claim_changes": {"jti": "\ud800"}}])
def test_oidc_is_sealed_one_use_and_bound_to_launch_claims(changes: dict[str, Any]) -> None:
    policy = _policy()
    record = _validated_record(policy, action="cleanup", runtime_record_sha256=helpers._pointer_values()["record_sha256"])
    assert _caller(policy, "cleanup", record).action == "cleanup"
    with pytest.raises(launch.LaunchBindingFailure, match="broker_authentication"):
        _caller(policy, "cleanup", record, **changes)
def test_oidc_replay_key_is_global_to_issuer_and_jti() -> None:
    policy = _policy()
    digest = helpers._pointer_values()["record_sha256"]
    records = [_validated_record(policy, action=action, runtime_record_sha256=digest) for action in ("provision", "cleanup")]
    key = runtime._digest(runtime._canonical({"iss": "https://token.actions.githubusercontent.com", "jti": "one-use-token-id"}))
    assert {_caller(policy, action, record, replay_key_sha256=key).oidc_jti_sha256 for action, record in zip(("provision", "cleanup"), records, strict=True)} == {runtime._digest(b"one-use-token-id")}
def test_receipt_binds_runtime_both_jobs_and_actual_execution_template(monkeypatch: pytest.MonkeyPatch) -> None:
    policy = _policy()
    runtime_raw, provision_ref, _cleanup_ref, signature = helpers._external_binding(policy)
    pointer = {field: getattr(provision_ref, field) for field in runtime.RUNTIME_REFERENCE_FIELDS}
    provision, cleanup = _observations(policy, pointer)
    record = _validated_record(
        policy,
        action="provision",
        runtime_record_sha256=runtime._digest(runtime_raw),
    )
    execution_template = {"containers": helpers._job_document(policy, "provision", pointer)["properties"]["template"]["containers"]}
    execution_id = f"{provision.job_resource_id}/executions/execution-1"
    execution_body = {
        "name": "execution-1",
        "properties": {"template": execution_template},
    }
    response = launch.AuthenticatedArmGetResponse._from_reviewed_managed_identity_adapter(resource_id=execution_id, api_version="2024-03-01", status_code=200, content_type="application/json", observed_at=datetime(2026, 9, 4, 12, 3, tzinfo=UTC), body=runtime._canonical(execution_body))
    execution = launch.validate_execution_observation(policy, action="provision", response=response)
    assert execution.template_sha256 == provision.execution_template_sha256
    for allowed_body in (execution_body | {"id": execution_id}, execution_body | {"type": "Microsoft.App/jobs/executions"}, {"name": "execution-1", "properties": {"template": execution_template | {"initContainers": []}}}):
        assert launch.validate_execution_observation(policy, action="provision", response=launch.AuthenticatedArmGetResponse._from_reviewed_managed_identity_adapter(resource_id=execution_id, api_version="2024-03-01", status_code=200, content_type="application/json", observed_at=datetime(2026, 9, 4, 12, 3, tzinfo=UTC), body=runtime._canonical(allowed_body))).template_sha256 == provision.execution_template_sha256
    for extra in ({"id": f"{execution_id}-wrong"}, {"type": "microsoft.app/jobs/executions"}, {"name": "execution-2"}, {"properties": {}}):
        with pytest.raises(launch.LaunchBindingFailure, match="execution_observation"):
            launch.validate_execution_observation(policy, action="provision", response=launch.AuthenticatedArmGetResponse._from_reviewed_managed_identity_adapter(resource_id=execution_id, api_version="2024-03-01", status_code=200, content_type="application/json", observed_at=datetime(2026, 9, 4, 12, 3, tzinfo=UTC), body=runtime._canonical(execution_body | extra)))
    for invalid_template in ({}, {"containers": {}}, execution_template | {"initContainers": [{}]}, execution_template | {"initContainers": {}}, execution_template | {"volumes": []}, execution_template | {"unknown": []}):
        with pytest.raises(launch.LaunchBindingFailure, match="execution_observation.template"):
            launch.validate_execution_observation(policy, action="provision", response=launch.AuthenticatedArmGetResponse._from_reviewed_managed_identity_adapter(resource_id=execution_id, api_version="2024-03-01", status_code=200, content_type="application/json", observed_at=datetime(2026, 9, 4, 12, 3, tzinfo=UTC), body=runtime._canonical({"name": "execution-1", "properties": {"template": invalid_template}})))
    authorization = _start(policy, "provision", record)
    start_body = {"id": execution_id, "name": "execution-1"}
    start_arguments: dict[str, Any] = {
        "authorization": authorization,
        "status_code": 200,
        "content_type": "application/json",
        "response_body": runtime._canonical(start_body),
        "echoed_client_request_id": authorization.client_request_id,
        "observed_at": datetime(2026, 9, 4, 12, 2, 30, tzinfo=UTC),
    }
    with pytest.raises(launch.LaunchBindingFailure, match="job_start_response.invalid"):
        launch.AuthenticatedBrokerStartResponse._from_reviewed_broker_transport(**start_arguments, provider_request_id="x" * 65_536)
    for status, body in ((202, start_body), (200, start_body | {"type": "Microsoft.App/jobs/executions"}), (200, start_body | {"id": f"{cleanup.job_resource_id}/executions/execution-1"}), (200, start_body | {"name": "execution-2"})):
        with pytest.raises(launch.LaunchBindingFailure, match="job_start_response"):
            launch.AuthenticatedBrokerStartResponse._from_reviewed_broker_transport(**(start_arguments | {"status_code": status, "response_body": runtime._canonical(body)}), provider_request_id="12345678-1234-1234-1234-123456789abc")
    start_response = launch.AuthenticatedBrokerStartResponse._from_reviewed_broker_transport(**start_arguments, provider_request_id="12345678-1234-1234-1234-123456789abc")
    receipt_bytes = launch.serialize_broker_launch_receipt(policy, launch_record=record, provision_observation=provision, cleanup_observation=cleanup, start_response=start_response, execution_observation=execution)
    receipt_uri, signature_uri = launch._execution_receipt_uris(policy, "provision", "execution-1")
    signing = policy["launch_binding"]["receipt_signing"]
    adapter_arguments: dict[str, Any] = {"policy": policy, "action": "provision", "receipt_uri": receipt_uri, "signature_bundle_uri": signature_uri, "retrieval_statuses": (404, 404, 200), "retrieval_elapsed_seconds": 4, "receipt_bytes": receipt_bytes, "signature_bundle_bytes": b"verified-signature-bundle", "verified_receipt_sha256": runtime._digest(receipt_bytes), "verified_algorithm": signing["algorithm"], "verified_signing_key_resource_id": signing["key_resource_id"], "verified_signing_key_version": signing["key_version"], "verified_public_key_sha256": signing["public_key_sha256"], "observed_at": datetime(2026, 9, 4, 12, 4, tzinfo=UTC)}
    monkeypatch.delenv(launch.EXECUTION_NAME_ENV, raising=False)
    with pytest.raises(launch.LaunchBindingFailure, match="launch_receipt.authentication"):
        launch.AuthenticatedBrokerLaunchReceipt._from_reviewed_platform_metadata_mailbox_signature_adapter(**adapter_arguments)
    monkeypatch.setenv(launch.EXECUTION_NAME_ENV, "execution-1")
    with pytest.raises(launch.LaunchBindingFailure, match="launch_receipt.authentication"):
        launch.AuthenticatedBrokerLaunchReceipt._from_reviewed_platform_metadata_mailbox_signature_adapter(**(adapter_arguments | {"retrieval_statuses": (404,)}))
    monkeypatch.setenv(launch.EXECUTION_NAME_ENV, "invalid/name")
    with pytest.raises(launch.LaunchBindingFailure, match="launch_receipt"):
        launch.AuthenticatedBrokerLaunchReceipt._from_reviewed_platform_metadata_mailbox_signature_adapter(**adapter_arguments)
    monkeypatch.setenv(launch.EXECUTION_NAME_ENV, "execution-1")
    authenticated = launch.AuthenticatedBrokerLaunchReceipt._from_reviewed_platform_metadata_mailbox_signature_adapter(**adapter_arguments)
    job_record = launch.validate_launch_record(policy, record.record_bytes, record_sha256=authenticated.launch_record_sha256, record_uri=authenticated.launch_record_uri)
    provision_ref = runtime.ExternalRuntimeBindingReference._from_authenticated_broker_launch_receipt(policy, authenticated, source_job_kind="provision")
    cleanup_ref = runtime.ExternalRuntimeBindingReference._from_authenticated_broker_launch_receipt(policy, authenticated, source_job_kind="cleanup")
    runtime_record = runtime.validate_external_runtime_binding(policy, runtime_raw, provision_reference=provision_ref, cleanup_reference=cleanup_ref, signature_verification=signature)
    receipt = launch.validate_received_launch_receipt(
        policy,
        authenticated_receipt=authenticated,
        launch_record=job_record,
        runtime_record=runtime_record,
        validation_now=VALIDATION_NOW,
    )
    assert receipt._is_validated()
    assert receipt.launch_record_sha256 == record.record_sha256
    assert receipt.execution_template_sha256 == provision.execution_template_sha256
    assert not hasattr(receipt, "__dict__")
    def authenticate(raw: bytes) -> launch.AuthenticatedBrokerLaunchReceipt:
        return launch.AuthenticatedBrokerLaunchReceipt._from_reviewed_platform_metadata_mailbox_signature_adapter(**(adapter_arguments | {"receipt_bytes": raw, "verified_receipt_sha256": runtime._digest(raw)}))
    for field, value, code in (("provision_job_projection_sha256", f"sha256:{'0' * 64}", "binding"), ("start_client_request_id", "00000000-0000-4000-8000-000000000000", "binding"), ("schema_version", True, "authentication")):
        changed = runtime._parse_policy(receipt_bytes)
        changed[field] = value
        with pytest.raises(launch.LaunchBindingFailure, match=f"launch_receipt.{code}"):
            launch.validate_received_launch_receipt(policy, authenticated_receipt=authenticate(runtime._canonical(changed)), launch_record=job_record, runtime_record=runtime_record, validation_now=VALIDATION_NOW)
    mismatched_record = copy(job_record)
    object.__setattr__(mismatched_record, "runtime_record_sha256", f"sha256:{'0' * 64}")
    with pytest.raises(launch.LaunchBindingFailure, match="launch_receipt.binding"):
        launch.validate_received_launch_receipt(policy, authenticated_receipt=authenticated, launch_record=mismatched_record, runtime_record=runtime_record, validation_now=VALIDATION_NOW)
    with pytest.raises(launch.LaunchBindingFailure, match="launch_receipt.expired"):
        launch.validate_received_launch_receipt(
            policy,
            authenticated_receipt=authenticated,
            launch_record=job_record,
            runtime_record=runtime_record,
            validation_now=job_record.expires_at,
        )
    old_execution = copy(execution)
    object.__setattr__(old_execution, "execution_resource_id", f"{provision.job_resource_id}/executions/older-execution")
    with pytest.raises(launch.LaunchBindingFailure, match="launch_receipt.binding"):
        launch.serialize_broker_launch_receipt(
            policy,
            launch_record=record,
            provision_observation=provision,
            cleanup_observation=cleanup,
            start_response=start_response,
            execution_observation=old_execution,
        )
    divergent_cleanup = copy(cleanup)
    object.__setattr__(divergent_cleanup, "runtime_reference", {**pointer, "record_sha256": f"sha256:{'9' * 64}"})
    with pytest.raises(launch.LaunchBindingFailure, match="launch_receipt.binding"):
        launch.serialize_broker_launch_receipt(
            policy,
            launch_record=record,
            provision_observation=provision,
            cleanup_observation=divergent_cleanup,
            start_response=start_response,
            execution_observation=execution,
        )
# fmt: on
