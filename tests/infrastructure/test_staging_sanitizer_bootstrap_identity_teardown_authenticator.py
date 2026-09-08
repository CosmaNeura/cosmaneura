from __future__ import annotations

import copy
import gc
import hashlib
import json
import os
import pickle
import weakref
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from scripts import authenticate_staging_sanitizer_bootstrap_identity_teardown as auth
from scripts import staging_sanitizer_bootstrap_teardown_evidence_grammar as grammar

from tests.infrastructure.test_staging_sanitizer_bootstrap_teardown_evidence_grammar import (
    _bytes,
    _envelope,
    _pretty_sha,
    _review_response,
    _sha,
)

NOW = datetime(2026, 9, 8, 18, 10, tzinfo=UTC)


def _fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    process_id: Callable[[], int] = os.getpid,
    mutate_evidence: Callable[[dict[str, Any]], None] | None = None,
    mutate_request: Callable[[dict[str, Any]], None] | None = None,
    mutate_response: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[Path, dict[str, Any]]:
    policy = copy.deepcopy(auth.load_bootstrap_identity_teardown_evidence_policy())
    evidence = _envelope(policy)
    if mutate_evidence is not None:
        mutate_evidence(evidence)
    state_raw = _bytes(evidence["prior_envelope"]["raw_evidence"]["isolated_state"])
    evidence["prior_envelope"]["evidence_hashes"]["isolated_state"] = hashlib.sha256(
        state_raw
    ).hexdigest()
    evidence["prior_envelope_sha256"] = _pretty_sha(evidence["prior_envelope"])
    wire_evidence = json.loads(_bytes(evidence))
    evidence_raw = _bytes(evidence)
    policy["evidence"]["files"][auth.EVIDENCE_FILE] = _sha(evidence_raw)
    review_input = "fixture\n\nEVIDENCE V3\n" + json.dumps(
        wire_evidence, ensure_ascii=True, separators=(",", ":")
    )
    policy["independent_review"]["request_input_sha256"] = _sha(review_input.encode())
    policy["independent_review"]["binding_id"] = grammar.expected_review_binding(
        evidence_sha256=policy["evidence"]["files"][auth.EVIDENCE_FILE],
        request_input_sha256=policy["independent_review"]["request_input_sha256"],
        maximum_output_tokens=policy["independent_review"]["maximum_output_tokens"],
        model=policy["independent_review"]["model"],
    )
    metadata = {
        "binding_id": policy["independent_review"]["binding_id"],
        "evidence_sha256": policy["evidence"]["files"][auth.EVIDENCE_FILE],
        "request_input_sha256": policy["independent_review"]["request_input_sha256"],
    }
    request = {
        "model": policy["independent_review"]["model"],
        "reasoning": {"effort": "high"},
        "metadata": metadata,
        "max_output_tokens": policy["independent_review"]["maximum_output_tokens"],
        "input": review_input,
    }
    if mutate_request is not None:
        mutate_request(request)
    response = _review_response(policy, metadata)
    if mutate_response is not None:
        mutate_response(response)
    payloads = {
        auth.EVIDENCE_FILE: evidence_raw,
        auth.STATE_EVIDENCE_FILE: state_raw,
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
    policy_sha256 = hashlib.sha256(policy_raw).hexdigest()
    monkeypatch.setattr(auth, "POLICY_SHA256", policy_sha256)
    read_policy = auth._read_policy
    authenticate, is_authenticated, project = auth._build_provenance(
        read_policy=lambda: read_policy(
            policy_path=policy_path,
            policy_sha256=policy_sha256,
        ),
        clock=lambda: NOW,
        policy_sha256=policy_sha256,
        process_id=process_id,
    )
    monkeypatch.setattr(auth, "_authenticate", authenticate)
    monkeypatch.setattr(
        auth.AuthenticatedBootstrapIdentityTeardownEvidence,
        "_is_authenticated",
        is_authenticated,
    )
    monkeypatch.setattr(
        auth,
        "is_authenticated_bootstrap_identity_teardown_evidence",
        is_authenticated,
    )
    monkeypatch.setattr(auth, "_public_projection", project)
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
    assert auth.is_authenticated_bootstrap_identity_teardown_evidence(value)
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


def test_provenance_is_closure_bound_against_module_global_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory, _policy = _fixture(tmp_path, monkeypatch)
    value = auth.authenticate_bootstrap_identity_teardown_evidence(
        evidence_directory=directory
    )

    monkeypatch.setattr(auth, "_SEAL", object())
    monkeypatch.setattr(auth, "_fail", lambda _code: None)
    monkeypatch.setattr(
        auth,
        "_validated_candidate",
        lambda **_kwargs: pytest.fail("mutable global validator was invoked"),
    )
    monkeypatch.setattr(
        auth.grammar,
        "validate_evidence_directory",
        lambda *_args, **_kwargs: pytest.fail("mutable grammar global was invoked"),
    )
    assert value._is_authenticated()
    with pytest.raises(auth.BootstrapIdentityTeardownEvidenceFailure):
        auth.authenticate_bootstrap_identity_teardown_evidence(
            evidence_directory=directory / "missing"
        )

    def elevating_dict(value: Any = (), **kwargs: Any) -> dict[Any, Any]:
        result = dict(value, **kwargs)
        return result | {"executable": True} if "executable" in result else result

    monkeypatch.setattr(auth, "dict", elevating_dict, raising=False)
    monkeypatch.setattr(auth, "type", lambda item: type(item), raising=False)
    third = auth.authenticate_bootstrap_identity_teardown_evidence(
        evidence_directory=directory
    )
    assert third.executable is False and third._is_authenticated()
    reference = weakref.ref(third)
    del third
    gc.collect()
    assert reference() is None

    real_hmac, real_hashlib = auth.hmac, auth.hashlib
    monkeypatch.setattr(auth, "datetime", object())
    monkeypatch.setattr(auth, "hashlib", object())
    assert value._is_authenticated()
    monkeypatch.setattr(auth, "hashlib", real_hashlib)
    monkeypatch.setattr(
        auth,
        "hmac",
        SimpleNamespace(new=real_hmac.new, compare_digest=lambda *_args: True),
    )
    read_attr = getattr
    monkeypatch.setattr(
        auth,
        "getattr",
        lambda item, name: False if name == "executable" else read_attr(item, name),
        raising=False,
    )
    expected_fields = auth.PUBLIC_FIELDS
    expected_public = auth._public_projection(value)
    monkeypatch.setattr(auth, "PUBLIC_FIELDS", ("_target",))
    assert value._is_authenticated()
    assert auth._public_projection(value) == expected_public
    assert "_target" not in expected_public
    object.__setattr__(value, "executable", True)
    assert not value._is_authenticated()
    with pytest.raises(auth.BootstrapIdentityTeardownEvidenceFailure):
        auth._public_projection(value)

    forged = object.__new__(auth.AuthenticatedBootstrapIdentityTeardownEvidence)
    for name in (*expected_fields, *auth.PRIVATE_FIELDS):
        object.__setattr__(forged, name, getattr(value, name))
    assert not forged._is_authenticated()


def test_issuance_uses_private_policy_copy_across_aba_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory, policy = _fixture(tmp_path, monkeypatch)
    original_target = copy.deepcopy(policy["target"])
    original_digest = policy["evidence"]["files"][auth.EVIDENCE_FILE]
    calls = 0

    def mutating_clock() -> datetime:
        nonlocal calls
        calls += 1
        if calls == 1:
            policy["target"]["subscription_id"] = "00000000-0000-0000-0000-000000000001"
            policy["evidence"]["files"][auth.EVIDENCE_FILE] = "sha256:" + "0" * 64
        else:
            policy["target"]["subscription_id"] = original_target["subscription_id"]
            policy["evidence"]["files"][auth.EVIDENCE_FILE] = original_digest
        return NOW

    authenticate, verify, _project = auth._build_provenance(
        read_policy=lambda: (policy, b"retained-policy-alias"), clock=mutating_clock
    )
    monkeypatch.setattr(auth, "_fail", lambda _code: None)
    monkeypatch.setattr(auth, "dict", lambda value=(), **_kwargs: value, raising=False)
    monkeypatch.setattr(auth, "datetime", SimpleNamespace(now=mutating_clock))
    value = authenticate(evidence_directory=directory)
    assert verify(value)
    assert dict(value._target) == original_target


def test_module_verifier_rejects_replaced_slot_and_getattribute_descriptors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory, _policy = _fixture(tmp_path, monkeypatch)
    value = auth.authenticate_bootstrap_identity_teardown_evidence(
        evidence_directory=directory
    )
    value_type = auth.AuthenticatedBootstrapIdentityTeardownEvidence
    executable_slot = vars(value_type)["executable"]
    executable_slot.__set__(value, True)

    class MaskingExecutable:
        def __get__(self, instance: object | None, _owner: object) -> object:
            return executable_slot if instance is None else True

    with monkeypatch.context() as descriptor_patch:
        descriptor_patch.setattr(value_type, "executable", MaskingExecutable())
        assert value_type.executable is executable_slot
        assert value.executable is True
        assert not auth.is_authenticated_bootstrap_identity_teardown_evidence(value)
        with pytest.raises(auth.BootstrapIdentityTeardownEvidenceFailure):
            auth._public_projection(value)

    value = auth.authenticate_bootstrap_identity_teardown_evidence(
        evidence_directory=directory
    )
    original_getattribute = value_type.__getattribute__
    with monkeypatch.context() as getattribute_patch:
        getattribute_patch.setattr(
            value_type,
            "__getattribute__",
            lambda item, name: (
                False if name == "executable" else original_getattribute(item, name)
            ),
        )
        assert not auth.is_authenticated_bootstrap_identity_teardown_evidence(value)
        with pytest.raises(auth.BootstrapIdentityTeardownEvidenceFailure):
            auth._public_projection(value)


def test_provenance_rejects_replaced_weak_registry_operations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory, policy = _fixture(tmp_path, monkeypatch)
    value = auth.authenticate_bootstrap_identity_teardown_evidence(
        evidence_directory=directory
    )
    expected_public = auth._public_projection(value)
    registry_type = weakref.WeakKeyDictionary
    original_get = registry_type.get

    def elevating_get(registry: Any, key: Any, default: Any = None) -> Any:
        record = original_get(registry, key, default)
        if record is None:
            return default
        projection = tuple(
            (name, True if name == "executable" else item) for name, item in record[2]
        )
        return (record[0], record[1], projection)

    with monkeypatch.context() as registry_patch:
        registry_patch.setattr(registry_type, "get", elevating_get)
        assert not auth.is_authenticated_bootstrap_identity_teardown_evidence(value)
        with pytest.raises(auth.BootstrapIdentityTeardownEvidenceFailure):
            auth._public_projection(value)
    assert auth._public_projection(value) == expected_public

    original_set = registry_type.__setitem__

    def elevating_set(registry: Any, key: Any, record: Any) -> None:
        projection = tuple(
            (name, True if name == "executable" else item) for name, item in record[2]
        )
        original_set(registry, key, (record[0], record[1], projection))

    class StatefulProjection(tuple):
        def __iter__(self) -> Any:
            return iter((("executable", True),))

    def stateful_set(registry: Any, key: Any, record: Any) -> None:
        original_set(
            registry, key, (record[0], record[1], StatefulProjection(record[2]))
        )

    for replacement in (elevating_set, stateful_set):
        with monkeypatch.context() as registry_patch:
            registry_patch.setattr(registry_type, "__setitem__", replacement)
            forged_authenticate, _verify, _project = auth._build_provenance(
                read_policy=lambda: (policy, b"registry-write-test"), clock=lambda: NOW
            )
            with pytest.raises(auth.BootstrapIdentityTeardownEvidenceFailure):
                forged_authenticate(evidence_directory=directory)


def test_exact_field_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory, _policy = _fixture(tmp_path, monkeypatch)
    mutations = (
        ("authenticated_at", NOW.astimezone(timezone(timedelta(hours=1)))),
        ("authenticated_at", NOW.replace(microsecond=1)),
        ("authenticated_at", NOW.replace(fold=1)),
        ("_target", lambda value: tuple(reversed(value._target))),
        ("_target", lambda value: value._target + (value._target[0],)),
        (
            "_evidence_digests",
            lambda value: tuple(reversed(value._evidence_digests)),
        ),
    )
    for name, mutation in mutations:
        value = auth.authenticate_bootstrap_identity_teardown_evidence(
            evidence_directory=directory
        )
        replacement = mutation(value) if callable(mutation) else mutation
        object.__setattr__(value, name, replacement)
        assert not value._is_authenticated()


def test_authenticated_value_does_not_survive_fork(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent_pid = os.getpid()
    directory, _policy = _fixture(tmp_path, monkeypatch, process_id=lambda: parent_pid)
    value = auth.authenticate_bootstrap_identity_teardown_evidence(
        evidence_directory=directory
    )
    read_fd, write_fd = os.pipe()
    child_pid = os.fork()
    if child_pid == 0:
        os.close(read_fd)
        exit_code = 0
        try:
            os.write(write_fd, b"1" if value._is_authenticated() else b"0")
        except BaseException:
            exit_code = 2
        finally:
            os.close(write_fd)
        os._exit(exit_code)

    os.close(write_fd)
    try:
        child_result = os.read(read_fd, 1)
    finally:
        os.close(read_fd)
    _waited_pid, status = os.waitpid(child_pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0
    assert child_result == b"0"
    assert value._is_authenticated()


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
        mutate_response=lambda value: value["output"][-1]["content"][0].update(
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
