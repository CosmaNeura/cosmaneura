from __future__ import annotations

import hashlib
import importlib
import inspect
import json
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from tests.infrastructure import test_staging_restore_runtime_binding as runtime_helpers

executor = importlib.import_module("scripts.staging_restore_trusted_executor")

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
KEY_BYTES = b"k" * 32
ATTESTATION_BYTES = b'{"mediaType":"application/vnd.dev.sigstore.bundle.v0.3+json"}'
TRUSTED_ROOT_BYTES = b'{"certificateAuthorities":[],"timestampAuthorities":[]}\n'
RAW_STAGE_SENTINEL = "must-not-escape-stage-projections"
EXPECTED_POLICY_SHA256 = (
    "b153084a079542d08ff86a34a150ac01cdfa0c1fd1e61fa1dfdf6c487159f5a8"
)
EXPECTED_ACTIVATION = {
    "trusted_executor_implemented": False,
    "runtime_adapter_implemented": False,
    "launch_binding_validator_implemented": False,
    "launch_mailbox_adapter_implemented": False,
    "launch_broker_adapter_implemented": False,
    "launch_receipt_validation_integrated": False,
    "source_provider_adapter_implemented": False,
    "offline_attestation_verifier_adapter_implemented": False,
    "safe_submit_adapter_implemented": False,
    "private_runner_binding_implemented": False,
    "workflow_integrated": False,
    "restore_activation_authorized": False,
}
EXPECTED_BLOCKERS = (
    "restore_trusted_executor_policy_disabled",
    "upstream_policies_disabled",
    "trusted_executor_unimplemented",
    "runtime_adapter_unimplemented",
    "launch_binding_validator_unimplemented",
    "launch_mailbox_adapter_unimplemented",
    "launch_broker_adapter_unimplemented",
    "launch_receipt_validation_unintegrated",
    "source_provider_adapter_unimplemented",
    "offline_attestation_verifier_adapter_unimplemented",
    "safe_submit_adapter_unimplemented",
    "private_runner_binding_unimplemented",
    "restore_runtime_binding_policy_disabled",
    "external_runtime_record_unconfigured",
    "attestation_verifier_and_root_pins_unconfigured",
    "restore_envelope_policy_unconfigured",
    "restore_safe_submit_policy_unconfigured",
    "workflow_integration_unimplemented",
    "restore_activation_unimplemented",
)
LAUNCH_CALLS = [
    "launch_receipt",
    "runtime_reference.provision",
    "runtime_reference.cleanup",
    "launch_record",
    "runtime_record",
    "launch_binding",
]


def _configured_runtime_policy() -> dict[str, Any]:
    policy = runtime_helpers._configured_policy()
    policy["evidence_ingress"]["private_versioned_secret_source"]["secrets"][
        "github_trusted_root"
    ]["sha256"] = executor.launch_binding._digest(TRUSTED_ROOT_BYTES)
    return policy


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


def _activated_policy(
    mutate: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[bytes, str]:
    policy = json.loads(executor.POLICY_PATH.read_text())
    policy["activation_ready"] = True
    policy["activation"] = dict.fromkeys(policy["activation"], True)
    adapters = policy["adapters"]
    for name in (
        "runtime",
        "source_provider",
        "offline_attestation_verifier",
        "safe_submit",
    ):
        adapters[name]["implemented"] = True
    launch = adapters["launch_binding"]
    for name in (
        "implemented",
        "mailbox_transport_implemented",
        "broker_adapter_implemented",
        "launch_receipt_integration_implemented",
    ):
        launch[name] = True
    if mutate is not None:
        mutate(policy)
    raw = _canonical(policy)
    return raw, hashlib.sha256(raw).hexdigest()


def _sealed_snapshot(
    expected_file_sha256s: dict[str, str] | None = None,
) -> Any:
    snapshot = executor.evidence_manifest.ValidatedEvidenceBundle._from_validated_manifest_bytes(
        result_bytes=b"{}", server_scope_bytes=b"{}", manifest_bytes=b"{}"
    )
    if expected_file_sha256s is not None:
        proof = object.__new__(executor.evidence_manifest._LaunchDigestValidation)
        object.__setattr__(
            proof,
            "file_sha256s",
            tuple(sorted(expected_file_sha256s.items())),
        )
        object.__setattr__(
            proof,
            "_seal",
            executor.evidence_manifest._LAUNCH_DIGEST_VALIDATION_SEAL,
        )
        object.__setattr__(snapshot, "_launch_digest_validation", proof)
    return snapshot


def _sealed_server_binding() -> Any:
    value = object.__new__(executor.server_auth.ServerAuthenticatedSafeSubmitBinding)
    object.__setattr__(value, "_record_bytes", b"{}")
    object.__setattr__(
        value,
        "_authentication_seal",
        executor.server_auth._SERVER_AUTHENTICATED_SAFE_SUBMIT_SEAL,
    )
    return value


def _sealed_provider() -> Any:
    value = object.__new__(executor.provider_observation.VerifiedProviderObservation)
    raw = b"{}"
    object.__setattr__(value, "_receipt_bytes", raw)
    object.__setattr__(
        value, "_receipt_sha256", executor.provider_observation._digest(raw)
    )
    object.__setattr__(
        value,
        "_observation_seal",
        executor.provider_observation._VERIFIED_PROVIDER_OBSERVATION_SEAL,
    )
    return value


def _sealed_attestation() -> Any:
    value = object.__new__(executor.github_attestation.VerifiedGitHubAttestation)
    raw = b"{}"
    object.__setattr__(value, "_receipt_bytes", raw)
    object.__setattr__(
        value, "_receipt_sha256", executor.github_attestation._digest(raw)
    )
    object.__setattr__(
        value,
        "_attestation_seal",
        executor.github_attestation._VERIFIED_GITHUB_ATTESTATION_SEAL,
    )
    return value


def _sealed_final_binding() -> Any:
    envelope, safe_policy = executor.safe_submit.validate_canonical_contracts()
    config = executor.safe_submit.config_from_policies(
        envelope, safe_policy, executable=False
    )
    record: dict[str, Any] = dict.fromkeys(
        executor.composition.OUTPUT_FIELDS, f"sha256:{'1' * 64}"
    )
    record.update(
        schema_version=1,
        binding_policy_id=config.evidence_binding_policy_id,
        evidence_manifest_policy_id=config.evidence_manifest_policy_id,
        evidence_manifest_policy_sha256=config.evidence_manifest_policy_sha256,
        manifest_id="rehearsal-20260904",
        source_sha="a" * 40,
        candidate_sha="b" * 40,
        source_server_resource_id=config.source_server_id,
        source_database_name=config.evidence_source_database_name,
        approved_restore_time="2026-09-04T12:00:00Z",
        observed_at="2026-09-04T12:00:00Z",
        expires_at="2026-09-04T12:15:00Z",
        candidate_approval_pr_number=281,
        candidate_protected_master_sha="b" * 40,
        candidate_schema_revision="c" * 12,
        repository=config.evidence_repository,
        protected_ref=config.evidence_protected_ref,
        workflow_path=config.evidence_workflow_path,
        workflow_sha="b" * 40,
        run_id="901",
        run_attempt="1",
        event_name=config.evidence_event_name,
        signer_issuer=config.evidence_signer_issuer,
        signer_subject=config.evidence_signer_subject,
    )
    return executor.composition._mint_binding(record)


class SequenceClock:
    def __init__(self, values: list[datetime]) -> None:
        self.values = iter(values)
        self.calls = 0

    def __call__(self) -> datetime:
        self.calls += 1
        return next(self.values)


class Harness:
    def __init__(
        self,
        inputs: dict[str, Path],
        *,
        outcome: Any | None = None,
        mutate_inputs_during_validation: bool = False,
    ) -> None:
        self.bundle_dir = inputs["bundle_dir"]
        self.hmac_key_path = inputs["hmac_key_path"]
        self.attestation_bundle_path = inputs["attestation_bundle_path"]
        self.trusted_root_path = inputs["trusted_root_path"]
        self.server_binding = _sealed_server_binding()
        self.server_receipt = b'{"pass":true}'
        self.provider = _sealed_provider()
        self.attestation = _sealed_attestation()
        self.final_binding = _sealed_final_binding()
        self.runtime_policy = _configured_runtime_policy()
        envelope, safe_policy = executor.safe_submit.validate_canonical_contracts()
        config = executor.safe_submit.config_from_policies(
            envelope, safe_policy, executable=False
        )
        self.safe_submit_config = config
        self.request = executor.safe_submit.request_from_binding(
            binding=self.final_binding, config=config, ttl_hours=6
        )
        self.runtime_record_bytes = b'{"runtime":true}'
        runtime_record_sha256 = executor.launch_binding._digest(
            self.runtime_record_bytes
        )
        runtime_signature_sha256 = f"sha256:{'2' * 64}"
        pointer = {
            "record_uri": (
                "https://restoreevidence.blob.core.windows.net/"
                f"restore-runtime-bindings-v1/{runtime_record_sha256[7:]}.json"
            ),
            "record_sha256": runtime_record_sha256,
            "signature_bundle_uri": (
                "https://restoreevidence.blob.core.windows.net/"
                "restore-runtime-bindings-v1/"
                f"{runtime_signature_sha256[7:]}.sigstore.json"
            ),
            "signature_bundle_sha256": runtime_signature_sha256,
        }
        self.references = {
            job_kind: executor.runtime_binding.ExternalRuntimeBindingReference._from_values(
                job_kind,
                self.runtime_policy["runtime"]["jobs"][job_kind]["resource_id"],
                pointer,
            )
            for job_kind in ("provision", "cleanup")
        }
        self.runtime_signature = executor.runtime_binding.VerifiedExternalRuntimeRecordSignature._mint_from_reviewed_verifier(
            record_sha256=runtime_record_sha256,
            bundle_sha256=runtime_signature_sha256,
            trusted_root_sha256=self.runtime_policy["evidence_ingress"][
                "private_versioned_secret_source"
            ]["secrets"]["github_trusted_root"]["sha256"],
            certificate_oidc_issuer="https://token.actions.githubusercontent.com",
            certificate_identity=self.runtime_policy["external_runtime_record"][
                "signature"
            ]["certificate_identity"],
        )
        self.runtime_record = (
            executor.runtime_binding.ValidatedExternalRuntimeBinding._from_validated(
                record_bytes=self.runtime_record_bytes,
                reference=self.references["provision"],
            )
        )
        evidence_names = self.runtime_policy["launch_binding"]["launch_record"][
            "provision"
        ]["exact_evidence_file_names"]
        self.expected_file_sha256s = {
            name: executor.launch_binding._digest((self.bundle_dir / name).read_bytes())
            for name in evidence_names
        }
        rehearsal_id = self.request.rehearsal_id
        evidence_ingress = self.runtime_policy["evidence_ingress"]
        blob_source = evidence_ingress["private_restore_evidence_container"]
        secret_source = evidence_ingress["private_versioned_secret_source"]
        evidence_files = {
            name: {
                "versioned_https_uri": (
                    f"https://{blob_source['account_host']}/{blob_source['container_name']}"
                    f"/{rehearsal_id}/{name}?versionid=evidence-version-{index}"
                ),
                "version_id": f"evidence-version-{index}",
                "sha256": self.expected_file_sha256s[name],
            }
            for index, name in enumerate(evidence_names, 1)
        }
        root = secret_source["secrets"]["github_trusted_root"]
        hmac = secret_source["secrets"]["server_scope_hmac_key"]
        launch_document = {
            "schema_version": 1,
            "launch_id": "launch-0123456789abcdef",
            "policy_id": self.runtime_policy["policy_id"],
            "policy_binding_sha256": executor.runtime_binding._policy_binding_digest(
                self.runtime_policy
            ),
            "action": "provision",
            "repository": "CosmaNeura/cosmaneura",
            "protected_ref": "refs/heads/master",
            "workflow_sha": self.request.workflow_sha,
            "run_id": self.request.run_id,
            "run_attempt": self.request.run_attempt,
            "runtime_record_sha256": runtime_record_sha256,
            "github_trusted_root": {
                "versioned_https_uri": root["versioned_https_uri"],
                "version": root["version"],
                "sha256": root["sha256"],
            },
            "created_at": "2026-09-04T11:59:00Z",
            "expires_at": "2026-09-04T17:59:00Z",
            "payload": {
                "rehearsal_id": rehearsal_id,
                "evidence_files": evidence_files,
                "github_attestation_bundle": {
                    "versioned_https_uri": (
                        f"https://{blob_source['account_host']}/{blob_source['container_name']}"
                        f"/{rehearsal_id}/attestation.json?versionid=attestation-version"
                    ),
                    "version_id": "attestation-version",
                    "sha256": executor.launch_binding._digest(ATTESTATION_BYTES),
                },
                "server_scope_hmac_key": {
                    "versioned_https_uri": hmac["versioned_https_uri"],
                    "version": hmac["version"],
                    "encoding": "base64url-no-padding",
                    "decoded_sha256": executor.launch_binding._digest(KEY_BYTES),
                },
                "ttl_hours": 6,
            },
        }
        self.launch_record_bytes = executor.launch_binding._canonical(launch_document)
        launch_record_sha256 = executor.launch_binding._digest(self.launch_record_bytes)
        mailbox = self.runtime_policy["launch_binding"]["mailbox"]
        launch_record_uri = (
            f"https://{mailbox['account_host']}/"
            f"{mailbox['container_names']['provision']}/"
            f"{launch_record_sha256[7:]}.json"
        )
        self.snapshot = _sealed_snapshot(self.expected_file_sha256s)
        self.launch_record = executor.launch_binding.validate_launch_record(
            self.runtime_policy,
            self.launch_record_bytes,
            record_sha256=launch_record_sha256,
            record_uri=launch_record_uri,
        )
        self.authenticated_receipt = object.__new__(
            executor.launch_binding.AuthenticatedBrokerLaunchReceipt
        )
        receipt_document = {
            "schema_version": 1,
            "launch_record_uri": launch_record_uri,
            "launch_record_sha256": launch_record_sha256,
            "action": "provision",
            "selected_job_resource_id": self.runtime_policy["runtime"]["jobs"][
                "provision"
            ]["resource_id"],
            "runtime_record_uri": pointer["record_uri"],
            "runtime_record_sha256": pointer["record_sha256"],
            "runtime_signature_bundle_uri": pointer["signature_bundle_uri"],
            "runtime_signature_bundle_sha256": pointer["signature_bundle_sha256"],
            "broker_oidc_jti_sha256": f"sha256:{'5' * 64}",
            "broker_oidc_replay_receipt_sha256": f"sha256:{'6' * 64}",
            "provision_job_body_sha256": f"sha256:{'7' * 64}",
            "provision_job_projection_sha256": f"sha256:{'8' * 64}",
            "provision_job_execution_template_sha256": f"sha256:{'9' * 64}",
            "provision_job_image_reference": (
                "restoreexecutor.azurecr.io/cosmaneura/restore-executor@sha256:"
                + "a" * 64
            ),
            "cleanup_job_body_sha256": f"sha256:{'b' * 64}",
            "cleanup_job_projection_sha256": f"sha256:{'c' * 64}",
            "cleanup_job_execution_template_sha256": f"sha256:{'d' * 64}",
            "cleanup_job_image_reference": (
                "restoreexecutor.azurecr.io/cosmaneura/restore-executor@sha256:"
                + "e" * 64
            ),
            "start_response_body_sha256": f"sha256:{'f' * 64}",
            "start_client_request_id": "12345678-1234-4234-8234-123456789abc",
            "start_provider_request_id": "87654321-4321-4321-8321-cba987654321",
            "execution_resource_id": (
                f"{self.runtime_policy['runtime']['jobs']['provision']['resource_id']}"
                "/executions/execution-1"
            ),
            "execution_body_sha256": f"sha256:{'0' * 64}",
            "execution_template_sha256": f"sha256:{'1' * 64}",
            "observed_at": "2026-09-04T12:00:00Z",
        }
        assert set(receipt_document) == set(
            self.runtime_policy["launch_binding"]["launch_receipt"]["required_fields"]
        )
        receipt_bytes = executor.launch_binding._canonical(receipt_document)
        receipt_uri, signature_envelope_uri = (
            executor.launch_binding._execution_receipt_uris(
                self.runtime_policy, "provision", "execution-1"
            )
        )
        receipt_values = {
            "receipt_bytes": receipt_bytes,
            "receipt_sha256": executor.launch_binding._digest(receipt_bytes),
            "action": "provision",
            "execution_name": "execution-1",
            "launch_record_uri": launch_record_uri,
            "launch_record_sha256": launch_record_sha256,
            "runtime_reference": pointer,
            "receipt_uri": receipt_uri,
            "signature_envelope_uri": signature_envelope_uri,
            "signature_envelope_sha256": f"sha256:{'3' * 64}",
            "observed_at": NOW,
            "_seal": executor.launch_binding._AUTHENTICATED_RECEIPT_SEAL,
        }
        for name, value in receipt_values.items():
            object.__setattr__(self.authenticated_receipt, name, value)
        self.received_receipt = object.__new__(
            executor.launch_binding.ValidatedReceivedLaunchReceipt
        )
        received_values = {
            "receipt_sha256": receipt_values["receipt_sha256"],
            "action": "provision",
            "launch_record_sha256": launch_record_sha256,
            "runtime_record_sha256": runtime_record_sha256,
            "execution_resource_id": (
                f"{self.runtime_policy['runtime']['jobs']['provision']['resource_id']}"
                "/executions/execution-1"
            ),
            "execution_template_sha256": f"sha256:{'4' * 64}",
            "observed_at": NOW,
            "validated_at": NOW,
            "_seal": executor.launch_binding._VALIDATED_RECEIPT_SEAL,
        }
        for name, value in received_values.items():
            object.__setattr__(self.received_receipt, name, value)
        authenticated_runtime_record = executor.launch_material.AuthenticatedRuntimeRecordMaterial._from_authenticated_read(
            record_bytes=self.runtime_record_bytes,
            trusted_root_bytes=TRUSTED_ROOT_BYTES,
            signature_verification=self.runtime_signature,
        )
        self.launch_material = executor.ExecutorLaunchMaterial(
            authenticated_receipt=self.authenticated_receipt,
            launch_record_bytes=self.launch_record_bytes,
            authenticated_runtime_record=authenticated_runtime_record,
        )
        self.private_inputs = executor.evidence_ingress.PrivateProvisionInputs._from_exact_reads(
            evidence_entries=tuple(
                (name, (self.bundle_dir / name).read_bytes()) for name in evidence_names
            ),
            attestation_bundle_bytes=ATTESTATION_BYTES,
            server_scope_hmac_key_bytes=KEY_BYTES,
            receipt_sha256=self.authenticated_receipt.receipt_sha256,
            launch_record_sha256=self.launch_record.record_sha256,
            execution_binding=(
                f"{self.runtime_policy['runtime']['jobs']['provision']['resource_id']}"
                "/executions/execution-1"
            ),
        )
        submitter = object.__new__(executor.safe_submit.SafeRestoreSubmitter)
        submitter.config = config
        intent = submitter._build_intent(self.request, require_fresh=False)
        self.outcome = (
            outcome
            if outcome is not None
            else executor.safe_submit.SubmitOutcome(
                status="ready",
                target_resource_id=intent["target_server_resource_id"],
                intent_sha256=f"sha256:{hashlib.sha256(executor.safe_submit._canonical(intent)).hexdigest()}",
                target_observed=True,
                cleanup_required=False,
            )
        )
        self.mutate_inputs = mutate_inputs_during_validation
        self.calls: list[str] = []
        self.clocks: list[Callable[[], datetime]] = []
        self.clock_values: list[datetime] = []
        self.launch_validation_now: datetime | None = None
        self.manifest_validation_now: datetime | None = None
        self.submitted_request: Any | None = None

    def _clock(self, kwargs: dict[str, Any]) -> None:
        clock = kwargs.pop("clock")
        self.clocks.append(clock)
        self.clock_values.append(clock())

    def receive_launch(self, **kwargs: Any) -> Any:
        self.calls.append("launch_receipt")
        self._clock(kwargs)
        assert set(kwargs) == {"runtime_policy"}
        assert kwargs["runtime_policy"] == self.runtime_policy
        return self.launch_material

    def runtime_reference_from_receipt(self, **kwargs: Any) -> Any:
        job_kind = kwargs["source_job_kind"]
        self.calls.append(f"runtime_reference.{job_kind}")
        assert set(kwargs) == {"policy", "receipt", "source_job_kind"}
        assert kwargs["policy"] == self.runtime_policy
        assert kwargs["receipt"] is self.authenticated_receipt
        return self.references[job_kind]

    def validate_launch_record(self, **kwargs: Any) -> Any:
        self.calls.append("launch_record")
        assert set(kwargs) == {
            "policy",
            "record_bytes",
            "record_sha256",
            "record_uri",
        }
        assert kwargs["policy"] == self.runtime_policy
        assert kwargs["record_bytes"] is self.launch_record_bytes
        assert kwargs["record_sha256"] == self.launch_record.record_sha256
        assert kwargs["record_uri"] == self.launch_record.record_uri
        return self.launch_record

    def validate_runtime_binding(self, **kwargs: Any) -> Any:
        self.calls.append("runtime_record")
        assert set(kwargs) == {
            "policy",
            "record_bytes",
            "provision_reference",
            "cleanup_reference",
            "signature_verification",
        }
        assert kwargs["policy"] == self.runtime_policy
        assert kwargs["record_bytes"] is self.runtime_record_bytes
        assert kwargs["provision_reference"] is self.references["provision"]
        assert kwargs["cleanup_reference"] is self.references["cleanup"]
        assert kwargs["signature_verification"] is self.runtime_signature
        return self.runtime_record

    def validate_launch_receipt(self, **kwargs: Any) -> Any:
        self.calls.append("launch_binding")
        self.launch_validation_now = kwargs.pop("validation_now")
        assert set(kwargs) == {
            "policy",
            "authenticated_receipt",
            "launch_record",
            "runtime_record",
        }
        assert kwargs["policy"] == self.runtime_policy
        assert kwargs["authenticated_receipt"] is self.authenticated_receipt
        assert kwargs["launch_record"] is self.launch_record
        assert kwargs["runtime_record"] is self.runtime_record
        object.__setattr__(
            self.received_receipt,
            "validated_at",
            self.launch_validation_now,
        )
        return self.received_receipt

    def read_private_inputs(self, **kwargs: Any) -> Any:
        self.calls.append("private_inputs")
        assert set(kwargs) == {
            "policy",
            "authenticated_receipt",
            "launch_record",
        }
        assert kwargs["policy"] == self.runtime_policy
        assert kwargs["authenticated_receipt"] is self.authenticated_receipt
        assert kwargs["launch_record"] is self.launch_record
        return self.private_inputs

    def validate_snapshot(self, **kwargs: Any) -> Any:
        self.calls.append("manifest")
        assert set(kwargs) == {
            "files",
            "validation_now",
            "expected_file_sha256s",
        }
        assert kwargs["files"] == self.private_inputs.evidence_files()
        self.manifest_validation_now = kwargs["validation_now"]
        assert kwargs["expected_file_sha256s"] == self.expected_file_sha256s
        if self.mutate_inputs:
            kwargs["files"]["candidate.json"] = b"changed-candidate"
        return self.snapshot

    def authenticate_server(self, **kwargs: Any) -> tuple[dict[str, Any], Any, bytes]:
        self.calls.append("server")
        self._clock(kwargs)
        assert set(kwargs) == {"snapshot", "key"}
        assert kwargs["snapshot"] is self.snapshot
        assert kwargs["key"] == KEY_BYTES
        return (
            {"raw_evidence": RAW_STAGE_SENTINEL},
            self.server_binding,
            self.server_receipt,
        )

    def observe_provider(self, **kwargs: Any) -> tuple[dict[str, Any], Any]:
        self.calls.append("provider")
        self._clock(kwargs)
        assert set(kwargs) == {"binding"}
        assert kwargs["binding"] is self.server_binding
        return {"raw_evidence": RAW_STAGE_SENTINEL}, self.provider

    def authenticate_attestation(self, **kwargs: Any) -> tuple[dict[str, Any], Any]:
        self.calls.append("attestation")
        self._clock(kwargs)
        assert set(kwargs) == {
            "binding",
            "snapshot",
            "attestation_bundle_bytes",
            "trusted_root_bytes",
        }
        assert kwargs["binding"] is self.server_binding
        assert kwargs["snapshot"] is self.snapshot
        assert kwargs["attestation_bundle_bytes"] == ATTESTATION_BYTES
        assert kwargs["trusted_root_bytes"] == TRUSTED_ROOT_BYTES
        return {"raw_evidence": RAW_STAGE_SENTINEL}, self.attestation

    def compose_binding(self, **kwargs: Any) -> tuple[dict[str, Any], Any]:
        self.calls.append("compose")
        self._clock(kwargs)
        assert set(kwargs) == {
            "server_binding",
            "server_authentication_receipt_bytes",
            "provider_observation",
            "github_attestation",
        }
        assert kwargs["server_binding"] is self.server_binding
        assert kwargs["server_authentication_receipt_bytes"] is self.server_receipt
        assert kwargs["provider_observation"] is self.provider
        assert kwargs["github_attestation"] is self.attestation
        return {"raw_evidence": RAW_STAGE_SENTINEL}, self.final_binding

    def request_from_binding(self, **kwargs: Any) -> Any:
        self.calls.append("request")
        assert set(kwargs) == {"binding", "ttl_hours"}
        assert kwargs["binding"] is self.final_binding
        assert kwargs["ttl_hours"] == 6
        return self.request

    def submit(self, **kwargs: Any) -> Any:
        self.calls.append("submit")
        self._clock(kwargs)
        assert set(kwargs) == {"request"}
        assert kwargs["request"] == self.request
        assert kwargs["request"] is not self.request
        assert kwargs["request"].evidence_binding is self.final_binding
        self.submitted_request = kwargs["request"]
        return self.outcome

    def dependencies(self) -> Any:
        return executor.ExecutorDependencies(
            safe_submit_config=self.safe_submit_config,
            receive_launch=self.receive_launch,
            runtime_reference_from_receipt=self.runtime_reference_from_receipt,
            validate_launch_record=self.validate_launch_record,
            validate_runtime_binding=self.validate_runtime_binding,
            validate_launch_receipt=self.validate_launch_receipt,
            read_private_inputs=self.read_private_inputs,
            validate_snapshot=self.validate_snapshot,
            authenticate_server=self.authenticate_server,
            observe_provider=self.observe_provider,
            authenticate_attestation=self.authenticate_attestation,
            compose_binding=self.compose_binding,
            request_from_binding=self.request_from_binding,
            submit=self.submit,
        )


@pytest.fixture
def inputs(tmp_path: Path) -> dict[str, Path]:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    for name in (
        "SHA256SUMS",
        "candidate.json",
        "evidence-manifest.json",
        "inventory.json",
        "quiescence.json",
        "server-scope.json",
    ):
        (bundle / name).write_bytes(b"{}")
    key = tmp_path / "hmac.key"
    attestation = tmp_path / "attestation.json"
    root = tmp_path / "trusted-root.jsonl"
    key.write_bytes(KEY_BYTES)
    key.chmod(0o600)
    attestation.write_bytes(ATTESTATION_BYTES)
    root.write_bytes(TRUSTED_ROOT_BYTES)
    attestation.chmod(0o600)
    root.chmod(0o600)
    return {
        "bundle_dir": bundle,
        "hmac_key_path": key,
        "attestation_bundle_path": attestation,
        "trusted_root_path": root,
    }


@pytest.fixture(autouse=True)
def provision_execution_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(executor.launch_binding.EXECUTION_NAME_ENV, "execution-1")


def _execute(
    inputs: dict[str, Path],
    dependencies: Any,
    clock: Any,
    mutate_policy: Callable[[dict[str, Any]], None] | None = None,
    runtime_policy: Any | None = None,
) -> Any:
    policy_bytes, policy_sha256 = _activated_policy(mutate_policy)
    return executor._execute_trusted_restore_with_dependencies(
        policy_bytes=policy_bytes,
        policy_sha256=policy_sha256,
        runtime_policy=runtime_policy or _configured_runtime_policy(),
        dependencies=dependencies,
        clock=clock,
    )


def _run(harness: Harness, inputs: dict[str, Path], clock: Any) -> Any:
    return _execute(inputs, harness.dependencies(), clock)


def test_checked_in_policy_is_exact_dormant_and_public_refuses_before_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = executor.POLICY_PATH.read_bytes()
    policy = json.loads(raw)
    assert hashlib.sha256(raw).hexdigest() == EXPECTED_POLICY_SHA256
    assert executor.POLICY_SHA256 == EXPECTED_POLICY_SHA256
    assert policy["schema_version"] == 1
    assert policy["policy_id"] == "cosmaneura-staging-restore-trusted-executor-v1"
    assert policy["closed_world"] is True
    assert policy["activation_ready"] is False
    assert policy["activation"] == EXPECTED_ACTIVATION
    assert tuple(policy["activation_blockers"]) == EXPECTED_BLOCKERS
    assert tuple(policy["activation_blockers"]) == executor.ACTIVATION_BLOCKERS
    runtime_pin = policy["policy_pins"]["restore_runtime_binding"]
    assert runtime_pin["sha256"] == (
        f"sha256:{executor.runtime_binding.CANONICAL_POLICY_SHA256}"
    )
    assert policy["adapters"]["runtime"] == {
        "implemented": False,
        "policy_pin": "policy_pins.restore_runtime_binding",
        "external_record_source": "authenticated_broker_launch_receipt_runtime_pointer",
        "image_digest_source": "verified_external_runtime_record",
        "caller_override_permitted": False,
    }
    assert policy["private_runtime_ingress"] == {
        "status": "dormant_dependency_assembly_implemented_unintegrated",
        "authority_preservation_implemented": True,
        "in_memory_evidence_validator_available": True,
        "dependency_assembly_implemented": True,
        "workflow_integrated": False,
        "evidence_bundle": {
            "type": "exact_builtin_dict_of_immutable_bytes",
            "validator": "scripts.validate_staging_evidence_manifest.validate_bundle_snapshot_bytes",
            "selector_source": "validated_provision_launch_record.evidence_file_authorities",
        },
        "github_attestation_bundle_selector_source": "validated_provision_launch_record.attestation_bundle_authority",
        "server_scope_hmac_key_selector_source": "validated_provision_launch_record.server_scope_hmac_key_authority",
        "trusted_root_bytes_source": "authenticated_runtime_record_material.trusted_root_bytes",
        "future_cleanup_mode_source": "validated_cleanup_launch_record.cleanup_mode",
        "parent_launch_record_validation_required_before_selector_use": True,
        "caller_path_or_uri_override_permitted": False,
    }
    launch_adapter = policy["adapters"]["launch_binding"]
    assert launch_adapter["implemented"] is False
    assert launch_adapter["broker_receipt_authentication"] == (
        "pinned-key-signature-over-canonical-bytes"
    )
    assert launch_adapter["broker_side_validation_runs_in_trusted_executor"] is False
    assert policy["execution"]["stage_order"][:5] == [
        "validate_staging_restore_launch_binding.AuthenticatedBrokerLaunchReceipt._from_reviewed_platform_metadata_mailbox_signature_adapter",
        "validate_staging_restore_runtime_binding.ExternalRuntimeBindingReference._from_authenticated_broker_launch_receipt",
        "validate_staging_restore_launch_binding.validate_launch_record",
        "validate_staging_restore_runtime_binding.validate_external_runtime_binding",
        "validate_staging_restore_launch_binding.validate_received_launch_receipt",
    ]
    assert policy["execution"]["provision_cleanup_handoff"] == {
        "kind": "exact-dominant-tombstone-v1",
        "create_and_read_back_required": True,
        "resource_delete_permitted": False,
        "cleanup_entrypoint": (
            "staging_restore_safe_submit.SafeRestoreSubmitter.cleanup_intent"
        ),
    }
    assert "cleaned" not in policy["output"]["allowed_statuses"]
    assert policy["snapshot_rules"]["launch_digest_binding_proof_required"] is True
    assert (
        policy["snapshot_rules"]["launch_digest_binding_proof_exact_map_match_required"]
        is True
    )
    assert policy["trusted_clock"]["launch_receipt_validation_freshness_reads"] == 1
    assert policy["trusted_clock"]["launch_authorization_final_pre_submit_reads"] == 1
    assert (
        policy["trusted_clock"]["launch_authorization_fresh_at_submit_start_required"]
        is True
    )

    signature = inspect.signature(executor.execute_trusted_restore)
    assert list(signature.parameters) == []
    construction_calls: list[str] = []

    def unexpected(*_args: Any, **_kwargs: Any) -> None:
        construction_calls.append("unexpected")

    monkeypatch.setattr(executor, "load_executor_policy_document", unexpected)
    monkeypatch.setattr(executor, "_runtime_dependencies", unexpected)
    monkeypatch.setattr(executor.runtime_binding, "load_policy", unexpected)
    monkeypatch.setattr(
        executor.receipt_receiver.ManagedIdentityPrivateBlobTransport,
        "from_environment",
        unexpected,
    )
    monkeypatch.setattr(
        executor.launch_record_ingress,
        "build_private_launch_record_reader",
        unexpected,
    )
    monkeypatch.setattr(
        executor.runtime_ingress,
        "build_private_runtime_material_reader",
        unexpected,
    )
    monkeypatch.setattr(
        executor.evidence_ingress,
        "build_private_evidence_material_reader",
        unexpected,
    )
    monkeypatch.setattr(
        executor.runtime_adapters.ManagedIdentityTokenSource,
        "from_environment",
        unexpected,
    )
    monkeypatch.setattr(executor.safe_submit, "build_policy_bound_runtime", unexpected)
    with pytest.raises(
        executor.TrustedExecutorFailure, match="executor_policy.disabled"
    ):
        executor.execute_trusted_restore()
    assert construction_calls == []
    with pytest.raises(TypeError):
        executor.execute_trusted_restore(bundle_dir=Path("/tmp"))  # type: ignore[call-arg]


def test_closed_world_policy_and_every_activation_flag_fail_before_stages(
    inputs: dict[str, Path],
) -> None:
    calls: list[str] = []

    def unexpected(**_kwargs: Any) -> None:
        calls.append("unexpected")

    dependencies = executor.ExecutorDependencies(
        safe_submit_config=executor.safe_submit.config_from_policies(
            *executor.safe_submit.validate_canonical_contracts(), executable=False
        ),
        receive_launch=unexpected,
        runtime_reference_from_receipt=unexpected,
        validate_launch_record=unexpected,
        validate_runtime_binding=unexpected,
        validate_launch_receipt=unexpected,
        read_private_inputs=unexpected,
        validate_snapshot=unexpected,
        authenticate_server=unexpected,
        observe_provider=unexpected,
        authenticate_attestation=unexpected,
        compose_binding=unexpected,
        request_from_binding=unexpected,
        submit=unexpected,
    )

    mutations: list[Callable[[dict[str, Any]], None]] = [
        lambda value: value.__setitem__("unexpected", True)
    ]
    for flag in json.loads(executor.POLICY_PATH.read_text())["activation"]:
        mutations.append(
            lambda value, flag=flag: value["activation"].__setitem__(flag, False)
        )
    for adapter, field in (
        ("runtime", "implemented"),
        ("launch_binding", "implemented"),
        ("launch_binding", "mailbox_transport_implemented"),
        ("launch_binding", "broker_adapter_implemented"),
        ("launch_binding", "launch_receipt_integration_implemented"),
        ("source_provider", "implemented"),
        ("offline_attestation_verifier", "implemented"),
        ("safe_submit", "implemented"),
    ):
        mutations.append(
            lambda value, adapter=adapter, field=field: value["adapters"][
                adapter
            ].__setitem__(field, False)
        )

    for mutate in mutations:
        with pytest.raises(executor.TrustedExecutorFailure):
            _execute(inputs, dependencies, lambda: NOW, mutate)
    assert calls == []


def test_dependency_factory_constructs_once_with_distinct_verifiers_and_shared_clock(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = Harness(inputs)
    runtime_policy = harness.runtime_policy
    verifier_policy = runtime_policy["offline_github_verifier"]
    documents = executor._PinnedRuntimeDocuments(
        runtime_policy=runtime_policy,
        manifest_policy={},
        manifest_policy_sha256="0" * 64,
        server_scope_policy_bytes=b"{}",
        server_scope_policy_sha256="0" * 64,
        authentication_policy_bytes=b"{}",
        authentication_policy_sha256="0" * 64,
        provider_policy={},
        provider_policy_bytes=b"{}",
        provider_policy_sha256="0" * 64,
        attestation_policy={
            "verifier": {
                "version": verifier_policy["version"],
                "executable_sha256": verifier_policy["executable_sha256"],
            }
        },
        attestation_policy_bytes=b"{}",
        attestation_policy_sha256="0" * 64,
        composition_policy_bytes=b"{}",
        composition_policy_sha256="0" * 64,
        safe_submit_config=harness.safe_submit_config,
    )
    calls: list[str] = []
    verifiers: list[Any] = []
    receipt_transport = SimpleNamespace(get_exact_private_blob=lambda _request: None)
    launch_reader = SimpleNamespace(read_launch_record=lambda **_kwargs: b"{}")

    def receipt_from_environment(_cls: Any, _policy: Any, *, action: str) -> Any:
        calls.append("receipt_transport")
        assert action == "provision"
        return receipt_transport

    def build_launch_reader(_policy: Any) -> Any:
        calls.append("launch_reader")
        return launch_reader

    original_runtime_builder = (
        executor.runtime_ingress.build_private_runtime_material_reader
    )

    def build_runtime_reader(policy: Any) -> Any:
        calls.append("runtime_reader")
        return original_runtime_builder(policy)

    original_evidence_builder = (
        executor.evidence_ingress.build_private_evidence_material_reader
    )

    def build_evidence_reader(policy: Any, *, clock: Any) -> Any:
        calls.append("evidence_reader")
        assert clock is shared_clock
        return original_evidence_builder(policy, clock=clock)

    class FakeVerifier:
        def __init__(self, **kwargs: Any) -> None:
            calls.append(f"verifier.{kwargs['profile']}")
            self.profile = kwargs["profile"]
            verifiers.append(self)

        def verify(self, **_kwargs: Any) -> None:
            return None

    def token_from_environment(_cls: Any, *, client_id: str) -> Any:
        calls.append("provider_token")
        assert (
            client_id == runtime_policy["identities"]["jobs"]["provision"]["client_id"]
        )
        return object()

    def provider_from_token(_token: Any) -> Any:
        calls.append("provider")
        return SimpleNamespace(get_flexible_server=lambda **_kwargs: None)

    def build_safe_runtime(**kwargs: Any) -> Any:
        calls.append("safe_runtime")
        assert kwargs["trusted_clock"] is shared_clock
        return SimpleNamespace(arm_transport=object(), marker_store=object())

    class FakeSubmitter:
        def __init__(self, *_args: Any, clock: Any, **_kwargs: Any) -> None:
            calls.append("submitter")
            self.clock = clock

        def submit(self, **_kwargs: Any) -> None:
            return None

    monkeypatch.setattr(
        executor.receipt_receiver.ManagedIdentityPrivateBlobTransport,
        "from_environment",
        classmethod(receipt_from_environment),
    )
    monkeypatch.setattr(
        executor.launch_record_ingress,
        "build_private_launch_record_reader",
        build_launch_reader,
    )
    monkeypatch.setattr(
        executor.runtime_ingress,
        "build_private_runtime_material_reader",
        build_runtime_reader,
    )
    monkeypatch.setattr(
        executor.evidence_ingress,
        "build_private_evidence_material_reader",
        build_evidence_reader,
    )
    monkeypatch.setattr(
        executor.runtime_adapters,
        "DigestPinnedGitHubCliVerifier",
        FakeVerifier,
    )
    monkeypatch.setattr(
        executor.runtime_adapters.ManagedIdentityTokenSource,
        "from_environment",
        classmethod(token_from_environment),
    )
    monkeypatch.setattr(
        executor.runtime_adapters,
        "AzureSourceObservationProvider",
        provider_from_token,
    )
    monkeypatch.setattr(
        executor.safe_submit, "build_policy_bound_runtime", build_safe_runtime
    )
    monkeypatch.setattr(executor.safe_submit, "SafeRestoreSubmitter", FakeSubmitter)

    shared_clock = executor._NonRegressingClock(lambda: NOW)
    dependencies = executor._assemble_provision_dependencies(
        documents=documents,
        clock=shared_clock,
    )

    assert calls == [
        "receipt_transport",
        "launch_reader",
        "runtime_reader",
        f"verifier.{executor.runtime_adapters.RUNTIME_RECORD_SIGNATURE_PROFILE}",
        "evidence_reader",
        "provider_token",
        "provider",
        f"verifier.{executor.runtime_adapters.EVIDENCE_ATTESTATION_PROFILE}",
        "safe_runtime",
        "submitter",
    ]
    assert len(verifiers) == 2
    assert verifiers[0] is not verifiers[1]
    assert dependencies.read_private_inputs.__self__._clock is shared_clock
    assert dependencies.submit.__self__.clock is shared_clock


@pytest.mark.parametrize(
    "pin_name",
    [
        "evidence_manifest",
        "server_scope_evidence",
        "server_scope_authentication",
        "source_provider_observation",
        "github_attestation_authentication",
        "restore_evidence_composition",
        "restore_envelope",
        "restore_safe_submit",
        "restore_runtime_binding",
    ],
)
def test_dependency_factory_refuses_each_bad_pin_before_adapter_construction(
    pin_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = json.loads(executor.POLICY_PATH.read_text())
    policy["policy_pins"][pin_name]["sha256"] = f"sha256:{'0' * 64}"
    construction_calls: list[str] = []

    def unexpected(**_kwargs: Any) -> None:
        construction_calls.append("unexpected")

    monkeypatch.setattr(executor, "_assemble_provision_dependencies", unexpected)
    with pytest.raises(
        executor.TrustedExecutorFailure, match="^dependencies.policy_invalid$"
    ):
        executor._runtime_dependencies(
            executor_policy=policy,
            runtime_policy=_configured_runtime_policy(),
            clock=executor._NonRegressingClock(lambda: NOW),
        )
    assert construction_calls == []


def test_one_process_preserves_identity_order_clock_and_bounded_outcome(
    inputs: dict[str, Path],
) -> None:
    harness = Harness(inputs)
    values = [NOW + timedelta(seconds=index) for index in range(9)]
    clock = SequenceClock(values)
    outcome = _run(harness, inputs, clock)

    assert outcome == harness.outcome
    assert outcome is not harness.outcome
    assert harness.calls == [
        *LAUNCH_CALLS,
        "private_inputs",
        "manifest",
        "server",
        "provider",
        "attestation",
        "compose",
        "request",
        "submit",
    ]
    assert harness.calls.count("private_inputs") == 1
    assert len({id(value) for value in harness.clocks}) == 1
    assert harness.clock_values == [values[0], *values[3:7], values[8]]
    assert harness.launch_validation_now == values[1]
    assert harness.manifest_validation_now == values[2]
    assert clock.calls == len(values)
    assert {field.name for field in fields(outcome)} == {
        "status",
        "target_resource_id",
        "intent_sha256",
        "target_observed",
        "cleanup_required",
    }
    rendered = repr(outcome)
    assert KEY_BYTES.decode().strip() not in rendered
    assert ATTESTATION_BYTES.decode() not in rendered
    assert TRUSTED_ROOT_BYTES.decode().strip() not in rendered
    assert RAW_STAGE_SENTINEL not in rendered


def test_regressing_shared_clock_refuses_before_provider(
    inputs: dict[str, Path],
) -> None:
    harness = Harness(inputs)
    clock = SequenceClock([NOW, NOW - timedelta(seconds=1)])
    with pytest.raises(
        executor.TrustedExecutorFailure, match="trusted_clock.regressed"
    ):
        _run(harness, inputs, clock)
    assert harness.calls == LAUNCH_CALLS[:-1]


@pytest.mark.parametrize("failure", ["cleanup", "unsealed", "receipt_digest"])
def test_launch_validation_refuses_before_any_restore_input_read(
    failure: str,
    inputs: dict[str, Path],
) -> None:
    harness = Harness(inputs)
    if failure == "cleanup":
        object.__setattr__(harness.launch_record, "action", "cleanup")
        expected_calls = LAUNCH_CALLS[:4]
    elif failure == "unsealed":
        object.__setattr__(harness.launch_record, "_seal", object())
        expected_calls = LAUNCH_CALLS[:4]
    else:
        object.__setattr__(
            harness.received_receipt,
            "runtime_record_sha256",
            f"sha256:{'0' * 64}",
        )
        expected_calls = LAUNCH_CALLS

    with pytest.raises(executor.TrustedExecutorFailure, match="result_invalid"):
        _run(harness, inputs, lambda: NOW)
    assert harness.calls == expected_calls
    assert "private_inputs" not in harness.calls


def test_malformed_sealed_launch_result_is_bounded(
    inputs: dict[str, Path],
) -> None:
    harness = Harness(inputs)
    malformed = object.__new__(executor.launch_binding.ValidatedLaunchRecord)
    object.__setattr__(
        malformed,
        "_seal",
        executor.launch_binding._LAUNCH_RECORD_SEAL,
    )
    dependencies = replace(
        harness.dependencies(),
        validate_launch_record=lambda **_kwargs: malformed,
    )

    with pytest.raises(
        executor.TrustedExecutorFailure, match="launch_record.result_invalid"
    ):
        _execute(inputs, dependencies, lambda: NOW)


def test_carried_root_must_match_validated_launch_root(
    inputs: dict[str, Path],
) -> None:
    harness = Harness(inputs)
    alternate_root = b'{"certificateAuthorities":[{}],"timestampAuthorities":[]}\n'
    alternate_signature = executor.runtime_binding.VerifiedExternalRuntimeRecordSignature._mint_from_reviewed_verifier(
        record_sha256=harness.runtime_signature.record_sha256,
        bundle_sha256=harness.runtime_signature.bundle_sha256,
        trusted_root_sha256=executor.launch_binding._digest(alternate_root),
        certificate_oidc_issuer=harness.runtime_signature.certificate_oidc_issuer,
        certificate_identity=harness.runtime_signature.certificate_identity,
    )
    alternate_runtime_material = executor.launch_material.AuthenticatedRuntimeRecordMaterial._from_authenticated_read(
        record_bytes=harness.runtime_record_bytes,
        trusted_root_bytes=alternate_root,
        signature_verification=alternate_signature,
    )
    harness.launch_material = executor.ExecutorLaunchMaterial(
        authenticated_receipt=harness.authenticated_receipt,
        launch_record_bytes=harness.launch_record_bytes,
        authenticated_runtime_record=alternate_runtime_material,
    )

    with pytest.raises(
        executor.TrustedExecutorFailure, match="launch_record.result_invalid"
    ):
        _run(harness, inputs, lambda: NOW)
    assert harness.calls == LAUNCH_CALLS[:4]


def test_generic_snapshot_from_validator_that_ignores_launch_hashes_is_rejected(
    inputs: dict[str, Path],
) -> None:
    harness = Harness(inputs)
    generic_snapshot = _sealed_snapshot()

    def ignore_launch_hashes(**_kwargs: Any) -> Any:
        harness.calls.append("manifest")
        return generic_snapshot

    dependencies = replace(
        harness.dependencies(),
        validate_snapshot=ignore_launch_hashes,
    )
    with pytest.raises(
        executor.TrustedExecutorFailure, match="manifest.result_invalid"
    ):
        _execute(inputs, dependencies, lambda: NOW)
    assert harness.calls == [*LAUNCH_CALLS, "private_inputs", "manifest"]
    assert "server" not in harness.calls


def test_validator_cannot_replace_the_mutable_expected_hash_map(
    inputs: dict[str, Path],
) -> None:
    harness = Harness(inputs)

    def replace_expected_hashes(**kwargs: Any) -> Any:
        harness.calls.append("manifest")
        supplied = kwargs["expected_file_sha256s"]
        supplied["candidate.json"] = f"sha256:{'0' * 64}"
        return _sealed_snapshot(supplied)

    dependencies = replace(
        harness.dependencies(),
        validate_snapshot=replace_expected_hashes,
    )
    with pytest.raises(
        executor.TrustedExecutorFailure, match="manifest.result_invalid"
    ):
        _execute(inputs, dependencies, lambda: NOW)
    assert harness.calls == [*LAUNCH_CALLS, "private_inputs", "manifest"]
    assert "server" not in harness.calls


def test_stale_received_launch_proof_is_rejected_before_input_reads(
    inputs: dict[str, Path],
) -> None:
    harness = Harness(inputs)

    def return_stale_proof(**_kwargs: Any) -> Any:
        harness.calls.append("launch_binding")
        return harness.received_receipt

    dependencies = replace(
        harness.dependencies(),
        validate_launch_receipt=return_stale_proof,
    )
    clock = SequenceClock([NOW, NOW + timedelta(seconds=1)])
    with pytest.raises(
        executor.TrustedExecutorFailure, match="launch_binding.result_invalid"
    ):
        _execute(inputs, dependencies, clock)
    assert harness.calls == LAUNCH_CALLS


def test_launch_must_remain_fresh_immediately_before_submit(
    inputs: dict[str, Path],
) -> None:
    harness = Harness(inputs)
    values = [NOW] * 7 + [harness.launch_record.expires_at]
    clock = SequenceClock(values)

    with pytest.raises(executor.TrustedExecutorFailure, match="launch_binding.expired"):
        _run(harness, inputs, clock)
    assert harness.calls[-1] == "request"
    assert "submit" not in harness.calls
    assert clock.calls == len(values)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("server_scope_hmac_key_bytes", b"z" * 32),
        ("attestation_bundle_bytes", b'{"different":true}'),
    ],
)
def test_sealed_private_input_tamper_refuses_before_manifest(
    field: str,
    replacement: bytes,
    inputs: dict[str, Path],
) -> None:
    harness = Harness(inputs)
    object.__setattr__(harness.private_inputs, field, replacement)

    with pytest.raises(
        executor.TrustedExecutorFailure, match="private_inputs.result_invalid"
    ):
        _run(harness, inputs, lambda: NOW)
    assert harness.calls == [*LAUNCH_CALLS, "private_inputs"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rehearsal_id", "000000000000"),
        ("workflow_sha", "f" * 40),
        ("run_id", "902"),
        ("run_attempt", "2"),
    ],
)
def test_request_identity_must_match_validated_provision_launch(
    field: str,
    value: str,
    inputs: dict[str, Path],
) -> None:
    harness = Harness(inputs)
    object.__setattr__(harness.launch_record, field, value)

    with pytest.raises(
        executor.TrustedExecutorFailure, match="launch_record.result_invalid"
    ):
        _run(harness, inputs, lambda: NOW)
    assert harness.calls == LAUNCH_CALLS[:4]
    assert "submit" not in harness.calls


def test_legacy_path_authority_is_absent_from_public_and_private_seams() -> None:
    legacy = {
        "bundle_dir",
        "hmac_key_path",
        "attestation_bundle_path",
        "trusted_root_path",
    }
    assert not legacy & set(
        inspect.signature(executor.execute_trusted_restore).parameters
    )
    assert not legacy & set(
        inspect.signature(
            executor._execute_trusted_restore_with_dependencies
        ).parameters
    )
    source = inspect.getsource(executor)
    assert "def _read_input_file" not in source
    assert "def _validate_directory" not in source
    assert "import stat" not in source


def test_private_input_result_must_be_exact_sealed_type(
    inputs: dict[str, Path],
) -> None:
    harness = Harness(inputs)
    for result in (
        object(),
        {"evidence_entries": harness.private_inputs.evidence_entries},
    ):
        dependencies = replace(
            harness.dependencies(),
            read_private_inputs=lambda **_kwargs: result,
        )
        with pytest.raises(
            executor.TrustedExecutorFailure, match="private_inputs.result_invalid"
        ):
            _execute(inputs, dependencies, lambda: NOW)


@pytest.mark.parametrize(
    "case",
    [
        "receipt_sha256",
        "launch_record_sha256",
        "execution_binding",
        "evidence_order",
        "evidence_digest",
        "attestation_digest",
        "hmac_digest",
    ],
)
def test_resealed_private_input_authority_mismatch_is_rejected(
    case: str, inputs: dict[str, Path]
) -> None:
    harness = Harness(inputs)
    source = harness.private_inputs
    entries = list(source.evidence_entries)
    values = {
        "evidence_entries": source.evidence_entries,
        "attestation_bundle_bytes": source.attestation_bundle_bytes,
        "server_scope_hmac_key_bytes": source.server_scope_hmac_key_bytes,
        "receipt_sha256": source.receipt_sha256,
        "launch_record_sha256": source.launch_record_sha256,
        "execution_binding": source.execution_binding,
    }
    if case == "evidence_order":
        values["evidence_entries"] = tuple(reversed(entries))
    elif case == "evidence_digest":
        entries[1] = (entries[1][0], b'{"changed":true}')
        values["evidence_entries"] = tuple(entries)
    elif case == "attestation_digest":
        values["attestation_bundle_bytes"] = b'{"changed":true}'
    elif case == "hmac_digest":
        values["server_scope_hmac_key_bytes"] = b"z" * 32
    elif case == "execution_binding":
        values[case] = f"{source.execution_binding}-other"
    else:
        values[case] = f"sha256:{'0' * 64}"
    harness.private_inputs = (
        executor.evidence_ingress.PrivateProvisionInputs._from_exact_reads(**values)
    )

    with pytest.raises(
        executor.TrustedExecutorFailure, match="private_inputs.result_invalid"
    ):
        _run(harness, inputs, lambda: NOW)
    assert harness.calls == [*LAUNCH_CALLS, "private_inputs"]


def test_manifest_cannot_mutate_detached_evidence_mapping(
    inputs: dict[str, Path],
) -> None:
    harness = Harness(inputs, mutate_inputs_during_validation=True)
    with pytest.raises(
        executor.TrustedExecutorFailure, match="manifest.result_invalid"
    ):
        _run(harness, inputs, lambda: NOW)
    assert harness.private_inputs.evidence_files()["candidate.json"] == b"{}"
    assert harness.calls == [*LAUNCH_CALLS, "private_inputs", "manifest"]


def test_receipt_launch_uri_toctou_is_rejected_after_private_read(
    inputs: dict[str, Path],
) -> None:
    harness = Harness(inputs)

    def mutate_receipt_authority(**kwargs: Any) -> Any:
        result = harness.read_private_inputs(**kwargs)
        object.__setattr__(
            harness.authenticated_receipt,
            "launch_record_uri",
            f"{harness.authenticated_receipt.launch_record_uri}?detached=true",
        )
        return result

    dependencies = replace(
        harness.dependencies(), read_private_inputs=mutate_receipt_authority
    )
    with pytest.raises(
        executor.TrustedExecutorFailure, match="private_inputs.result_invalid"
    ):
        _execute(inputs, dependencies, lambda: NOW)
    assert harness.calls == [*LAUNCH_CALLS, "private_inputs"]


def test_authenticated_root_toctou_is_rejected_before_submit(
    inputs: dict[str, Path],
) -> None:
    harness = Harness(inputs)
    runtime_material = harness.launch_material.authenticated_runtime_record

    def mutate_authenticated_root(**kwargs: Any) -> Any:
        request = harness.request_from_binding(**kwargs)
        object.__setattr__(
            runtime_material,
            "trusted_root_bytes",
            b'{"mutated":true}',
        )
        return request

    dependencies = replace(
        harness.dependencies(), request_from_binding=mutate_authenticated_root
    )
    with pytest.raises(
        executor.TrustedExecutorFailure, match="private_inputs.result_invalid"
    ):
        _execute(inputs, dependencies, lambda: NOW)
    assert harness.calls == [
        *LAUNCH_CALLS,
        "private_inputs",
        "manifest",
        "server",
        "provider",
        "attestation",
        "compose",
        "request",
    ]
    assert "submit" not in harness.calls


@pytest.mark.parametrize(
    "bad_outcome",
    [
        {"raw_evidence": "secret"},
        object(),
        object.__new__(executor.safe_submit.SubmitOutcome),
    ],
)
def test_unbounded_or_untyped_submit_result_never_escapes(
    bad_outcome: Any, inputs: dict[str, Path]
) -> None:
    harness = Harness(inputs, outcome=bad_outcome)
    with pytest.raises(executor.TrustedExecutorFailure, match="submit.result_invalid"):
        _run(harness, inputs, lambda: NOW)
    assert harness.calls[-1] == "submit"


def test_request_and_outcome_are_slotted_closed_world(inputs: dict[str, Path]) -> None:
    harness = Harness(inputs)
    assert not hasattr(harness.request, "__dict__")
    assert not hasattr(harness.outcome, "__dict__")
    for value in (harness.request, harness.outcome):
        with pytest.raises((AttributeError, TypeError)):
            setattr(value, "raw_secret", KEY_BYTES)


@pytest.mark.parametrize(
    "case",
    ["wrong_target", "wrong_digest", "cleanup_only", "cleaned", "bad_flags", "raw_key"],
)
def test_exact_typed_invalid_submit_results_are_bounded(
    case: str, inputs: dict[str, Path]
) -> None:
    harness = Harness(inputs)
    if case == "wrong_target":
        harness.outcome = replace(
            harness.outcome,
            target_resource_id=f"{harness.outcome.target_resource_id}-other",
        )
    elif case == "wrong_digest":
        digest = harness.outcome.intent_sha256
        changed = ("0" if digest[7] != "0" else "1") + digest[8:]
        harness.outcome = replace(harness.outcome, intent_sha256=f"sha256:{changed}")
    elif case == "cleanup_only":
        harness.outcome = replace(
            harness.outcome,
            status="prepared_only",
            target_observed=False,
            cleanup_required=False,
        )
    elif case == "cleaned":
        harness.outcome = replace(
            harness.outcome,
            status="cleaned",
            target_observed=True,
            cleanup_required=False,
        )
    elif case == "bad_flags":
        harness.outcome = replace(harness.outcome, target_observed=False)
    else:
        target = harness.outcome.target_resource_id.rsplit("/", 1)
        harness.outcome = replace(
            harness.outcome,
            target_resource_id=f"{target[0]}/{KEY_BYTES.decode()}-{target[1]}",
        )
    with pytest.raises(executor.TrustedExecutorFailure, match="submit.result_invalid"):
        _run(harness, inputs, lambda: NOW)
    assert harness.calls[-1] == "submit"


@pytest.mark.parametrize(
    ("status", "observed", "cleanup"),
    [
        ("ready", True, False),
        ("cleanup_marker_unproven", False, True),
        ("cleanup_marker_unproven", True, True),
        ("cleanup_pending", True, True),
        ("pending_late_materialization", False, True),
    ],
)
def test_exact_submit_status_contract(
    status: str, observed: bool, cleanup: bool, inputs: dict[str, Path]
) -> None:
    harness = Harness(inputs)
    harness.outcome = replace(
        harness.outcome,
        status=status,
        target_observed=observed,
        cleanup_required=cleanup,
    )
    assert _run(harness, inputs, lambda: NOW).status == status


def test_request_derivation_is_rechecked_before_submit(inputs: dict[str, Path]) -> None:
    mutations = (
        {"rehearsal_id": "not-derived"},
        {"evidence_binding": _sealed_final_binding()},
        {"cleanup_not_after": NOW + timedelta(hours=7)},
        {"repository": "Other/repository"},
        {"workflow_sha": "f" * 40},
        {"run_id": "902"},
        {"run_attempt": "2"},
    )
    for mutation in mutations:
        harness = Harness(inputs)
        harness.request = replace(harness.request, **mutation)
        with pytest.raises(
            executor.TrustedExecutorFailure, match="request.result_invalid"
        ):
            _run(harness, inputs, lambda: NOW)
        assert harness.calls[-1] == "request"
        assert "submit" not in harness.calls

    harness = Harness(inputs)
    harness.request = object.__new__(executor.safe_submit.RestoreRequest)
    with pytest.raises(executor.TrustedExecutorFailure, match="request.result_invalid"):
        _run(harness, inputs, lambda: NOW)
    assert "submit" not in harness.calls


def test_submit_cannot_mutate_validated_request(inputs: dict[str, Path]) -> None:
    harness = Harness(inputs)

    def mutate_request(**kwargs: Any) -> Any:
        outcome = harness.submit(**kwargs)
        object.__setattr__(kwargs["request"], "repository", "Other/repository")
        return outcome

    dependencies = replace(harness.dependencies(), submit=mutate_request)
    with pytest.raises(executor.TrustedExecutorFailure, match="request.result_invalid"):
        _execute(inputs, dependencies, lambda: NOW)


def test_dependency_exception_text_is_never_exposed(inputs: dict[str, Path]) -> None:
    harness = Harness(inputs)

    def leak(**_kwargs: Any) -> None:
        raise executor.TrustedExecutorFailure(RAW_STAGE_SENTINEL)

    dependencies = replace(harness.dependencies(), validate_snapshot=leak)
    with pytest.raises(executor.TrustedExecutorFailure, match="^manifest.failed$"):
        _execute(inputs, dependencies, lambda: NOW)
