from __future__ import annotations

import hashlib
import importlib
import inspect
import json
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import pytest

executor = importlib.import_module("scripts.staging_restore_trusted_executor")

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
KEY_BYTES = b"k" * 32
ATTESTATION_BYTES = b'{"mediaType":"application/vnd.dev.sigstore.bundle.v0.3+json"}'
TRUSTED_ROOT_BYTES = b'{"certificateAuthorities":[],"timestampAuthorities":[]}\n'
RAW_STAGE_SENTINEL = "must-not-escape-stage-projections"
EXPECTED_POLICY_SHA256 = (
    "c07b6a557de46c8da4e7b051d9d0ef36d924108bb471dd639c46488d888150d4"
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


def _sealed_snapshot() -> Any:
    return executor.evidence_manifest.ValidatedEvidenceBundle._from_validated_manifest_bytes(
        result_bytes=b"{}", server_scope_bytes=b"{}", manifest_bytes=b"{}"
    )


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
        self.snapshot = _sealed_snapshot()
        self.server_binding = _sealed_server_binding()
        self.server_receipt = b'{"pass":true}'
        self.provider = _sealed_provider()
        self.attestation = _sealed_attestation()
        self.final_binding = _sealed_final_binding()
        envelope, safe_policy = executor.safe_submit.validate_canonical_contracts()
        config = executor.safe_submit.config_from_policies(
            envelope, safe_policy, executable=False
        )
        self.request = executor.safe_submit.request_from_binding(
            binding=self.final_binding, config=config, ttl_hours=6
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
        self.submitted_request: Any | None = None

    def _clock(self, kwargs: dict[str, Any]) -> None:
        clock = kwargs.pop("clock")
        self.clocks.append(clock)
        self.clock_values.append(clock())

    def validate_snapshot(self, **kwargs: Any) -> Any:
        self.calls.append("manifest")
        assert set(kwargs) == {"bundle_dir", "validation_now"}
        assert kwargs["bundle_dir"] == self.bundle_dir
        assert kwargs["validation_now"] == NOW
        if self.mutate_inputs:
            self.hmac_key_path.write_bytes(b"changed-key")
            self.attestation_bundle_path.write_bytes(b"changed-attestation")
            self.trusted_root_path.write_bytes(b"changed-root")
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


def _execute(
    inputs: dict[str, Path],
    dependencies: Any,
    clock: Any,
    mutate_policy: Callable[[dict[str, Any]], None] | None = None,
) -> Any:
    policy_bytes, policy_sha256 = _activated_policy(mutate_policy)
    return executor._execute_trusted_restore_with_dependencies(
        policy_bytes=policy_bytes,
        policy_sha256=policy_sha256,
        **inputs,
        ttl_hours=6,
        dependencies=dependencies,
        clock=clock,
    )


def _run(harness: Harness, inputs: dict[str, Path], clock: Any) -> Any:
    return _execute(inputs, harness.dependencies(), clock)


def test_checked_in_policy_is_exact_dormant_and_public_refuses_before_inputs() -> None:
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

    signature = inspect.signature(executor.execute_trusted_restore)
    assert list(signature.parameters) == [
        "bundle_dir",
        "hmac_key_path",
        "attestation_bundle_path",
        "trusted_root_path",
        "ttl_hours",
    ]
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in signature.parameters.values()
    )
    missing = Path("/definitely/not/a/trusted-restore-input")
    with pytest.raises(
        executor.TrustedExecutorFailure, match="executor_policy.disabled"
    ):
        executor.execute_trusted_restore(
            bundle_dir=missing,
            hmac_key_path=missing,
            attestation_bundle_path=missing,
            trusted_root_path=missing,
            ttl_hours=6,
        )


def test_closed_world_policy_and_every_activation_flag_fail_before_stages(
    inputs: dict[str, Path],
) -> None:
    calls: list[str] = []

    def unexpected(**_kwargs: Any) -> None:
        calls.append("unexpected")

    dependencies = executor.ExecutorDependencies(
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


def test_one_process_preserves_identity_order_clock_and_bounded_outcome(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = Harness(inputs, mutate_inputs_during_validation=True)
    values = [NOW + timedelta(seconds=index) for index in range(6)]
    clock = SequenceClock(values)
    reads: list[Path] = []
    original_read = executor._read_input_file

    def counted_read(path: Path, **kwargs: Any) -> bytes:
        reads.append(path)
        return original_read(path, **kwargs)

    monkeypatch.setattr(executor, "_read_input_file", counted_read)

    outcome = _run(harness, inputs, clock)

    assert outcome == harness.outcome
    assert outcome is not harness.outcome
    assert harness.calls == [
        "manifest",
        "server",
        "provider",
        "attestation",
        "compose",
        "request",
        "submit",
    ]
    assert len({id(value) for value in harness.clocks}) == 1
    assert harness.clock_values == values[1:]
    assert clock.calls == len(values)
    assert reads == [
        inputs["hmac_key_path"],
        inputs["attestation_bundle_path"],
        inputs["trusted_root_path"],
    ]
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
    assert harness.calls == ["manifest", "server"]


@pytest.mark.parametrize(
    "name",
    ["bundle_dir", "hmac_key_path", "attestation_bundle_path", "trusted_root_path"],
)
def test_symlink_inputs_refuse_before_any_stage(
    name: str, inputs: dict[str, Path], tmp_path: Path
) -> None:
    link = tmp_path / f"linked-{name}"
    link.symlink_to(inputs[name], target_is_directory=name == "bundle_dir")
    changed = {**inputs, name: link}
    harness = Harness(changed)
    with pytest.raises(executor.TrustedExecutorFailure, match="input.untrusted_path"):
        _execute(changed, harness.dependencies(), lambda: NOW)
    assert harness.calls == []


@pytest.mark.parametrize(
    ("name", "mode"),
    [
        ("hmac_key_path", 0o640),
        ("attestation_bundle_path", 0o620),
        ("trusted_root_path", 0o602),
    ],
)
def test_unsafe_input_modes_refuse_before_any_stage(
    name: str, mode: int, inputs: dict[str, Path]
) -> None:
    inputs[name].chmod(mode)
    harness = Harness(inputs)
    with pytest.raises(executor.TrustedExecutorFailure, match="input.untrusted_path"):
        _execute(inputs, harness.dependencies(), lambda: NOW)
    assert harness.calls == []


@pytest.mark.parametrize(
    ("name", "policy_key"),
    [
        ("hmac_key_path", "hmac_key"),
        ("attestation_bundle_path", "attestation_bundle"),
        ("trusted_root_path", "trusted_root"),
    ],
)
def test_external_inputs_are_nonempty_and_policy_bounded(
    name: str, policy_key: str, inputs: dict[str, Path]
) -> None:
    policy = json.loads(executor.POLICY_PATH.read_text())
    maximum = policy["inputs"][policy_key]["maximum_bytes"]
    harness = Harness(inputs)
    for raw_value in (b"", b"x" * (maximum + 1)):
        inputs[name].write_bytes(raw_value)
        with pytest.raises(executor.TrustedExecutorFailure, match="input.invalid"):
            _execute(inputs, harness.dependencies(), lambda: NOW)
    assert harness.calls == []


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
