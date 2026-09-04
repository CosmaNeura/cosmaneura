from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import pytest

from scripts import staging_restore_launch_material as material
from scripts import staging_restore_trusted_executor as executor
from scripts import validate_staging_restore_launch_binding as launch
from scripts import validate_staging_restore_runtime_binding as binding

from tests.infrastructure import (
    test_staging_broker_launch_receipt_authentication as auth_helpers,
)
from tests.infrastructure import (
    test_staging_broker_launch_receipt_mailbox as mailbox_helpers,
)
from tests.infrastructure import test_staging_restore_runtime_binding as helpers

NOW = datetime(2026, 9, 4, 12, 4, tzinfo=UTC)
LAUNCH_BYTES = b'{"launch":"exact"}'
RUNTIME_BYTES = b'{"runtime":"exact"}'
RUNTIME_SIGNATURE_BYTES = b'{"sigstore":"exact"}'
_DEFAULT_RUNTIME_RESULT = object()


def _mailbox_objects(policy: dict[str, Any]) -> dict[str, bytes]:
    receipt = json.loads(mailbox_helpers._receipt(policy, "provision"))
    launch_sha256 = binding._digest(LAUNCH_BYTES)
    runtime_sha256 = binding._digest(RUNTIME_BYTES)
    runtime_signature_sha256 = binding._digest(RUNTIME_SIGNATURE_BYTES)
    mailbox = policy["launch_binding"]["mailbox"]
    runtime_ingress = policy["runtime_record_ingress"]
    receipt.update(
        {
            "launch_record_uri": (
                f"https://{mailbox['account_host']}/"
                f"{mailbox['container_names']['provision']}/"
                f"{launch_sha256[7:]}.json"
            ),
            "launch_record_sha256": launch_sha256,
            "runtime_record_uri": (
                f"https://{runtime_ingress['account_host']}/"
                f"{runtime_ingress['container_name']}/{runtime_sha256[7:]}.json"
            ),
            "runtime_record_sha256": runtime_sha256,
            "runtime_signature_bundle_uri": (
                f"https://{runtime_ingress['account_host']}/"
                f"{runtime_ingress['container_name']}/"
                f"{runtime_signature_sha256[7:]}.sigstore.json"
            ),
            "runtime_signature_bundle_sha256": runtime_signature_sha256,
        }
    )
    raw = binding._canonical(receipt)
    return {
        "receipt": raw,
        "signature_envelope": auth_helpers._envelope(policy, raw),
    }


def _signature_proof(
    policy: dict[str, Any],
) -> binding.VerifiedExternalRuntimeRecordSignature:
    return binding.VerifiedExternalRuntimeRecordSignature._mint_from_reviewed_verifier(
        record_sha256=binding._digest(RUNTIME_BYTES),
        bundle_sha256=binding._digest(RUNTIME_SIGNATURE_BYTES),
        trusted_root_sha256=policy["evidence_ingress"][
            "private_versioned_secret_source"
        ]["secrets"]["github_trusted_root"]["sha256"],
        certificate_oidc_issuer=policy["external_runtime_record"]["signature"][
            "certificate_oidc_issuer"
        ],
        certificate_identity=policy["external_runtime_record"]["signature"][
            "certificate_identity"
        ],
    )


class Sources:
    def __init__(
        self,
        policy: dict[str, Any],
        *,
        launch_result: Any = LAUNCH_BYTES,
        runtime_result: Any = _DEFAULT_RUNTIME_RESULT,
        launch_effect: Callable[[], None] | None = None,
    ) -> None:
        self.policy = policy
        self.launch_result = launch_result
        self.runtime_result = (
            material.AuthenticatedRuntimeRecordMaterial(
                record_bytes=RUNTIME_BYTES,
                signature_verification=_signature_proof(policy),
            )
            if runtime_result is _DEFAULT_RUNTIME_RESULT
            else runtime_result
        )
        self.launch_effect = launch_effect
        self.calls: list[str] = []
        self.receipt: launch.AuthenticatedBrokerLaunchReceipt | None = None

    def read_launch_record(self, **kwargs: Any) -> Any:
        self.calls.append("launch_record")
        assert set(kwargs) == {"policy", "authenticated_receipt"}
        assert kwargs["policy"] == self.policy
        self.receipt = kwargs["authenticated_receipt"]
        assert type(self.receipt) is launch.AuthenticatedBrokerLaunchReceipt
        assert self.receipt._is_authenticated()
        if self.launch_effect is not None:
            self.launch_effect()
        if isinstance(self.launch_result, Exception):
            raise self.launch_result
        return self.launch_result

    def read_and_authenticate_runtime_record(self, **kwargs: Any) -> Any:
        self.calls.append("runtime_record")
        assert set(kwargs) == {
            "policy",
            "authenticated_receipt",
            "provision_reference",
            "cleanup_reference",
        }
        assert kwargs["policy"] == self.policy
        assert kwargs["authenticated_receipt"] is self.receipt
        provision = kwargs["provision_reference"]
        cleanup = kwargs["cleanup_reference"]
        assert provision.source_job_kind == "provision"
        assert cleanup.source_job_kind == "cleanup"
        assert all(
            getattr(provision, field) == getattr(cleanup, field)
            for field in binding.RUNTIME_REFERENCE_FIELDS
        )
        if isinstance(self.runtime_result, Exception):
            raise self.runtime_result
        return self.runtime_result


def _receive(
    monkeypatch: pytest.MonkeyPatch,
    *,
    policy: dict[str, Any] | None = None,
    sources: Sources | None = None,
    transport: Any | None = None,
    wall_clock: Callable[[], datetime] = lambda: NOW,
) -> tuple[material.ProvisionLaunchMaterial, Sources, Any]:
    configured = policy or helpers._configured_policy()
    selected_sources = sources or Sources(configured)
    selected_transport = transport or mailbox_helpers.FakeTransport(
        _mailbox_objects(configured)
    )
    monotonic = mailbox_helpers.Clock()
    monkeypatch.setenv(launch.EXECUTION_NAME_ENV, mailbox_helpers.EXECUTION_NAME)
    result = material.receive_provision_launch_material(
        configured,
        dependencies=material.LaunchMaterialDependencies(
            receipt_transport=selected_transport,
            read_launch_record=selected_sources.read_launch_record,
            read_and_authenticate_runtime_record=(
                selected_sources.read_and_authenticate_runtime_record
            ),
        ),
        clock=wall_clock,
        monotonic=monotonic,
        sleeper=monotonic.sleep,
    )
    return result, selected_sources, selected_transport


def test_real_receiver_flows_into_exact_executor_material(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = helpers._configured_policy()
    result, sources, transport = _receive(monkeypatch, policy=policy)
    assert type(result) is material.ProvisionLaunchMaterial
    assert executor.ExecutorLaunchMaterial is material.ProvisionLaunchMaterial
    assert result.authenticated_receipt._is_authenticated()
    assert result.authenticated_receipt.action == "provision"
    assert result.launch_record_bytes == LAUNCH_BYTES
    assert result.runtime_record_bytes == RUNTIME_BYTES
    assert result.runtime_signature_verification._is_verified()
    assert sources.calls == ["launch_record", "runtime_record"]
    assert [request.object_kind for request in transport.requests] == [
        "receipt",
        "signature_envelope",
    ]


def test_checked_in_policy_refuses_before_environment_or_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checked_in = json.loads(binding.POLICY_PATH.read_text())
    sources = Sources(helpers._configured_policy())
    transport = mailbox_helpers.FakeTransport(
        {"receipt": b"unread", "signature_envelope": b"unread"}
    )
    monkeypatch.delenv(launch.EXECUTION_NAME_ENV, raising=False)
    with pytest.raises(
        material.LaunchMaterialFailure,
        match="^launch_material.policy_invalid$",
    ):
        _receive(
            monkeypatch,
            policy=checked_in,
            sources=sources,
            transport=transport,
        )
    assert transport.requests == []
    assert sources.calls == []


@pytest.mark.parametrize(
    "launch_result",
    (
        pytest.param(None, id="none"),
        pytest.param(b"", id="empty"),
        pytest.param(b"wrong", id="wrong-digest"),
        pytest.param(b"x" * 65_537, id="oversized"),
        pytest.param(bytearray(LAUNCH_BYTES), id="wrong-type"),
    ),
)
def test_launch_record_bytes_are_exact_and_bounded_before_runtime_authentication(
    monkeypatch: pytest.MonkeyPatch, launch_result: Any
) -> None:
    policy = helpers._configured_policy()
    sources = Sources(policy, launch_result=launch_result)
    with pytest.raises(
        material.LaunchMaterialFailure,
        match="^launch_material.launch_record_result_invalid$",
    ):
        _receive(monkeypatch, policy=policy, sources=sources)
    assert sources.calls == ["launch_record"]


@pytest.mark.parametrize(
    "runtime_result",
    (
        None,
        (RUNTIME_BYTES, object()),
        material.AuthenticatedRuntimeRecordMaterial(
            record_bytes=b"wrong",
            signature_verification=object(),  # type: ignore[arg-type]
        ),
        material.AuthenticatedRuntimeRecordMaterial(
            record_bytes=b"x" * 65_537,
            signature_verification=object(),  # type: ignore[arg-type]
        ),
    ),
)
def test_runtime_record_result_requires_exact_carrier_bytes_and_proof(
    monkeypatch: pytest.MonkeyPatch, runtime_result: Any
) -> None:
    policy = helpers._configured_policy()
    sources = Sources(policy, runtime_result=runtime_result)
    with pytest.raises(
        material.LaunchMaterialFailure,
        match="^launch_material.runtime_record_result_invalid$",
    ):
        _receive(monkeypatch, policy=policy, sources=sources)
    assert sources.calls == ["launch_record", "runtime_record"]


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("record_sha256", f"sha256:{'1' * 64}"),
        ("bundle_sha256", f"sha256:{'2' * 64}"),
        ("trusted_root_sha256", f"sha256:{'3' * 64}"),
        ("certificate_oidc_issuer", "https://wrong.invalid"),
        ("certificate_identity", "https://wrong.invalid/workflow"),
    ),
)
def test_runtime_signature_proof_is_exactly_cross_bound(
    monkeypatch: pytest.MonkeyPatch, field: str, value: str
) -> None:
    policy = helpers._configured_policy()
    proof = _signature_proof(policy)
    object.__setattr__(proof, field, value)
    sources = Sources(
        policy,
        runtime_result=material.AuthenticatedRuntimeRecordMaterial(
            record_bytes=RUNTIME_BYTES,
            signature_verification=proof,
        ),
    )
    with pytest.raises(
        material.LaunchMaterialFailure,
        match="^launch_material.runtime_record_result_invalid$",
    ):
        _receive(monkeypatch, policy=policy, sources=sources)


def test_unsealed_signature_and_cleanup_receipt_are_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = helpers._configured_policy()
    unsealed = object.__new__(binding.VerifiedExternalRuntimeRecordSignature)
    sources = Sources(
        policy,
        runtime_result=material.AuthenticatedRuntimeRecordMaterial(
            record_bytes=RUNTIME_BYTES,
            signature_verification=unsealed,
        ),
    )
    with pytest.raises(
        material.LaunchMaterialFailure,
        match="^launch_material.runtime_record_result_invalid$",
    ):
        _receive(monkeypatch, policy=policy, sources=sources)

    receipt = _receive(monkeypatch, policy=policy)[0].authenticated_receipt
    object.__setattr__(receipt, "action", "cleanup")
    monkeypatch.setattr(
        material.receipt_receiver,
        "receive_broker_launch_receipt",
        lambda **_kwargs: receipt,
    )
    sources = Sources(policy)
    with pytest.raises(
        material.LaunchMaterialFailure,
        match="^launch_material.receipt_result_invalid$",
    ):
        _receive(monkeypatch, policy=policy, sources=sources)
    assert sources.calls == []


def test_execution_identity_is_rechecked_between_authority_stages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = helpers._configured_policy()
    sources = Sources(
        policy,
        launch_effect=lambda: monkeypatch.setenv(
            launch.EXECUTION_NAME_ENV, "execution-mutated"
        ),
    )
    with pytest.raises(
        material.LaunchMaterialFailure,
        match="^launch_material.execution_identity_invalid$",
    ):
        _receive(monkeypatch, policy=policy, sources=sources)
    assert sources.calls == ["launch_record"]


@pytest.mark.parametrize(
    ("stage", "error"),
    (
        ("launch", "launch_material.launch_record_failed"),
        ("runtime", "launch_material.runtime_record_failed"),
    ),
)
def test_dependency_failures_are_bounded_without_input_text(
    monkeypatch: pytest.MonkeyPatch, stage: str, error: str
) -> None:
    policy = helpers._configured_policy()
    secret = "must-not-escape-launch-material"
    sources = Sources(
        policy,
        launch_result=RuntimeError(secret) if stage == "launch" else LAUNCH_BYTES,
        runtime_result=RuntimeError(secret) if stage == "runtime" else None,
    )
    with pytest.raises(material.LaunchMaterialFailure, match=f"^{error}$") as raised:
        _receive(monkeypatch, policy=policy, sources=sources)
    assert secret not in str(raised.value)


def test_dependencies_are_closed_and_clock_is_shared_with_receiver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = helpers._configured_policy()
    with pytest.raises(
        material.LaunchMaterialFailure,
        match="^launch_material.dependencies_invalid$",
    ):
        material.receive_provision_launch_material(
            policy,
            dependencies=object(),  # type: ignore[arg-type]
            clock=lambda: NOW,
        )

    seen: list[Any] = []
    authenticated = _receive(monkeypatch, policy=policy)[0].authenticated_receipt

    def fake_receiver(**kwargs: Any) -> Any:
        seen.append(kwargs["wall_clock"])
        return authenticated

    def shared_clock() -> datetime:
        return NOW

    monkeypatch.setattr(
        material.receipt_receiver, "receive_broker_launch_receipt", fake_receiver
    )
    _receive(monkeypatch, policy=policy, wall_clock=shared_clock)
    assert seen == [shared_clock]


def test_policy_validation_exception_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = helpers._configured_policy()
    secret = "must-not-escape-policy-validation"
    sources = Sources(policy)
    transport = mailbox_helpers.FakeTransport(_mailbox_objects(policy))

    def fail_validation(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError(secret)

    monkeypatch.setattr(
        material.runtime_binding,
        "validate_runtime_binding_policy",
        fail_validation,
    )
    with pytest.raises(
        material.LaunchMaterialFailure,
        match="^launch_material.policy_invalid$",
    ) as raised:
        _receive(
            monkeypatch,
            policy=policy,
            sources=sources,
            transport=transport,
        )
    assert secret not in str(raised.value)
    assert transport.requests == []
    assert sources.calls == []


def test_policy_and_runtime_entrypoints_remain_dormant() -> None:
    policy = json.loads(binding.POLICY_PATH.read_text())
    executor_policy = json.loads(executor.POLICY_PATH.read_text())
    assert policy["activation_ready"] is False
    assert set(policy["activation"].values()) == {False}
    assert executor_policy["activation_ready"] is False
    assert set(executor_policy["activation"].values()) == {False}
    with pytest.raises(
        executor.TrustedExecutorFailure, match="^runtime_adapter.unimplemented$"
    ):
        executor._runtime_dependencies()


def test_receipt_input_cannot_be_mutated_into_an_unsealed_substitute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = helpers._configured_policy()
    authenticated = _receive(monkeypatch, policy=policy)[0].authenticated_receipt
    forged = deepcopy(authenticated)
    object.__setattr__(forged, "_seal", object())
    monkeypatch.setattr(
        material.receipt_receiver,
        "receive_broker_launch_receipt",
        lambda **_kwargs: forged,
    )
    sources = Sources(policy)
    with pytest.raises(
        material.LaunchMaterialFailure,
        match="^launch_material.receipt_result_invalid$",
    ):
        _receive(monkeypatch, policy=policy, sources=sources)
    assert sources.calls == []
