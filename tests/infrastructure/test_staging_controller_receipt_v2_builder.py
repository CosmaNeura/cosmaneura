from __future__ import annotations

import ast
import inspect
import json
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from scripts import build_staging_controller_receipt_v2 as builder
from scripts import (
    observe_staging_controller_receipt_v2_isolation_inventory as isolation,
)
from scripts import validate_staging_controller_receipt_v2 as content

from tests.infrastructure import (
    test_staging_controller_receipt_v2_arm_observation as arm_helpers,
)
from tests.infrastructure import (
    test_staging_controller_receipt_v2_isolation_inventory as isolation_helpers,
)

NOW = arm_helpers.NOW
Failure = builder.ControllerReceiptV2BuildFailure
SECRET = "Bearer secret://database-authority"


def _authority(monkeypatch: pytest.MonkeyPatch) -> Any:
    return isolation_helpers._observe(isolation_helpers._case(monkeypatch))


def _build(
    monkeypatch: pytest.MonkeyPatch, *, now: Any = NOW
) -> tuple[Any, builder.BuiltControllerReceiptV2]:
    authority = _authority(monkeypatch)
    instance = builder.ControllerReceiptV2Builder._from_reviewed_configuration(
        clock=lambda: now
    )
    return authority, instance.build(isolation_inventory=authority)


def _documents(authority: Any) -> tuple[Any, Any, Any, Any]:
    database_authority = authority._database_observation
    arm_observation = database_authority._arm_observation
    production_authority = database_authority._production_authority
    return (
        production_authority.authority,
        arm_observation.authority,
        database_authority.authority,
        authority.authority,
    )


@pytest.fixture(scope="module")
def built_chain() -> Any:
    monkeypatch = pytest.MonkeyPatch()
    try:
        yield _build(monkeypatch)
    finally:
        monkeypatch.undo()


def test_builds_exact_canonical_receipt_from_the_sealed_chain(
    built_chain: Any,
) -> None:
    authority, result = built_chain
    production_document, arm_document, database_document, inventory_document = (
        _documents(authority)
    )
    receipt = json.loads(result.receipt_bytes)
    controller = content._parse_canonical(
        authority._database_observation._production_authority._controller_policy,
        content.MAXIMUM_POLICY_BYTES,
    )

    assert result._is_built()
    assert set(receipt) == set(content.TOP_LEVEL_FIELDS)
    assert result.receipt_bytes == content._canonical(receipt)
    assert not result.receipt_bytes.endswith(b"\n")
    assert receipt["schema_version"] == 2
    assert type(receipt["schema_version"]) is int
    assert receipt["kind"] == "staging-provision-controller-receipt"
    assert receipt["policy_id"] == controller["policy_id"]
    assert receipt["policy_binding_sha256"] == content._policy_binding_digest(
        controller
    )
    assert receipt["repository"] == controller["fixed_bindings"]["repository"]
    assert receipt["identity"] == production_document["identity"]
    assert receipt["authorization"] == production_document["authorization"]
    assert receipt["launch"] == production_document["launch"]
    assert receipt["source"] == production_document["source"]
    assert receipt["restore_observed_at"] == arm_document["observed_at"]
    assert receipt["created_at"] == NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert (
        receipt["cleanup_not_after"]
        == production_document["request"]["cleanup_not_after"]
    )
    assert receipt["pass"] is True

    expected_target = {
        "target_fqdn": arm_document["target"]["target_fqdn"],
        "approved_database_role": database_document["target"]["approved_database_role"],
        **{
            name: arm_document["target"][name]
            for name in content.RUNTIME_FIELDS
            if name not in {"target_fqdn", "approved_database_role"}
        },
    }
    expected_resource_set = content._digest(content._canonical(expected_target))
    assert receipt["target"] == {
        **expected_target,
        "resource_set_sha256": expected_resource_set,
    }
    assert receipt["isolation"] == {
        name: inventory_document["facts"][name] for name in content.ISOLATION_FIELDS
    }
    assert receipt["isolation"] == controller["fixed_bindings"]["isolation"]
    assert result.resource_set_sha256 == expected_resource_set
    assert (
        result.known_resource_inventory_sha256
        == inventory_document["facts"]["known_resource_inventory_sha256"]
    )


def test_digest_formulas_expiry_clamp_and_shared_validator(
    built_chain: Any,
) -> None:
    authority, result = built_chain
    production_authority = authority._database_observation._production_authority
    production_document, arm_document, database_document, inventory_document = (
        _documents(authority)
    )
    policy = builder.load_controller_receipt_v2_builder_policy()
    controller = content._parse_canonical(
        production_authority._controller_policy, content.MAXIMUM_POLICY_BYTES
    )
    expected_expiry = min(
        NOW
        + timedelta(
            seconds=controller["freshness"]["maximum_receipt_lifetime_seconds"]
        ),
        content._utc(inventory_document["expires_at"]),
        content._utc(database_document["expires_at"]),
        content._utc(arm_document["expires_at"]),
        content._utc(production_document["launch"]["launch_expires_at"]),
        content._utc(production_document["request"]["cleanup_not_after"]),
    )
    assert result.expires_at == expected_expiry
    assert result.created_at < result.expires_at <= result.cleanup_not_after
    assert result.intent_sha256 == content._digest(result.intent_bytes)
    assert result.submission_sha256 == content._digest(result.submission_bytes)
    assert result.receipt_sha256 == content._digest(result.receipt_bytes)
    assert result.record_chain_sha256 == builder._record_chain_sha256(
        result.intent_sha256, result.submission_sha256, result.receipt_sha256
    )
    assert result.authority_chain_sha256 == builder._authority_chain_sha256(
        policy=policy,
        production_sha256=result.production_authority_sha256,
        arm_sha256=result.arm_observation_sha256,
        database_sha256=result.database_observation_sha256,
        inventory_sha256=result.isolation_inventory_authority_sha256,
    )
    validated = content.validate_controller_receipt_v2_content(
        controller,
        intent_bytes=result.intent_bytes,
        submission_bytes=result.submission_bytes,
        receipt_bytes=result.receipt_bytes,
        clock=lambda: NOW,
    )
    assert validated.receipt_sha256 == result.receipt_sha256
    assert validated.provenance_authenticated is False


def test_claims_are_narrow_and_persisted_bytes_have_no_provenance_claim(
    built_chain: Any,
) -> None:
    _authority_value, result = built_chain
    for name in (
        "content_built_and_validated",
        "intent_submission_source_authenticated",
        "arm_network_facts_authenticated",
        "database_role_authenticated",
        "tls_transport_authenticated",
        "private_route_reachability_authenticated",
        "point_in_time_isolation_inventory_authenticated",
    ):
        assert getattr(result, name) is True
    for name in (
        "controller_receipt_exists",
        "full_three_record_source_authenticated",
        "create_only_behavior_proven",
        "writer_identity_use_authenticated",
        "provider_facts_authenticated",
        "execution_history_authenticated",
        "blob_or_redis_copy_absence_proven",
        "database_mutation_authority",
        "sanitizer_authority",
        "runtime_authority",
        "executable",
        "workflow_integrated",
    ):
        assert getattr(result, name) is False


def test_policy_pins_dormancy_and_only_public_input() -> None:
    policy = builder.load_controller_receipt_v2_builder_policy()
    expected = builder._expected_upstream_policies()
    assert policy["upstream_policies"] == expected
    assert policy["activation_ready"] is False
    assert policy["activation"]["controller_receipt_builder_implemented"] is True
    assert all(
        value is False
        for name, value in policy["activation"].items()
        if name != "controller_receipt_builder_implemented"
    )
    assert set(
        inspect.signature(builder.ControllerReceiptV2Builder.build).parameters
    ) == {"self", "isolation_inventory"}
    assert set(
        inspect.signature(
            builder.ControllerReceiptV2Builder._from_reviewed_configuration
        ).parameters
    ) == {"clock"}
    changed = deepcopy(policy)
    changed["claims"]["controller_receipt_exists"] = True
    with pytest.raises(Failure, match="policy_invalid"):
        builder.validate_controller_receipt_v2_builder_policy(changed)
    with pytest.raises(Failure, match="policy_disabled"):
        builder.load_controller_receipt_v2_builder_policy(executable=True)
    with pytest.raises(Failure, match="configuration_invalid"):
        builder.ControllerReceiptV2Builder()
    fake = object.__new__(builder.ControllerReceiptV2Builder)
    with pytest.raises(Failure, match="configuration_invalid"):
        fake.build(isolation_inventory=object())  # type: ignore[arg-type]

    class BuilderSubclass(builder.ControllerReceiptV2Builder):
        pass

    with pytest.raises(Failure, match="configuration_invalid"):
        BuilderSubclass._from_reviewed_configuration(clock=lambda: NOW)
    with pytest.raises(Failure, match="authority_invalid"):
        builder.BuiltControllerReceiptV2()


@pytest.mark.parametrize(
    "level",
    (
        "inventory",
        "database",
        "arm",
        "production",
        "intent",
        "submission",
        "launch",
        "runtime",
    ),
)
def test_tamper_at_every_ancestor_refuses(built_chain: Any, level: str) -> None:
    authority, _result = built_chain
    database_authority = authority._database_observation
    arm_observation = database_authority._arm_observation
    production_authority = database_authority._production_authority
    target: Any
    field: str
    value: Any
    if level == "inventory":
        target, field, value = authority, "rehearsal_id", "0" * 12
    elif level == "database":
        target, field, value = database_authority, "rehearsal_id", "0" * 12
    elif level == "arm":
        target, field, value = arm_observation, "rehearsal_id", "0" * 12
    elif level == "production":
        target, field, value = production_authority, "rehearsal_id", "0" * 12
    elif level == "intent":
        target, field, value = production_authority, "intent_bytes", b"{}"
    elif level == "submission":
        target, field, value = production_authority, "submission_bytes", b"{}"
    elif level == "launch":
        target = production_authority._launch_material
        field, value = "launch_record_bytes", b"{}"
    else:
        target = production_authority._launch_material.authenticated_runtime_record
        field, value = "record_bytes", b"{}"
    original = getattr(target, field)
    try:
        object.__setattr__(target, field, value)
        instance = builder.ControllerReceiptV2Builder._from_reviewed_configuration(
            clock=lambda: NOW
        )
        with pytest.raises(Failure) as error:
            instance.build(isolation_inventory=authority)
        assert error.value.__cause__ is None
        assert error.value.__context__ is None
    finally:
        object.__setattr__(target, field, original)


def test_mixed_object_chain_refuses(
    monkeypatch: pytest.MonkeyPatch, built_chain: Any
) -> None:
    first, _result = built_chain
    second = _authority(monkeypatch)
    database_authority = first._database_observation
    original = database_authority._arm_observation
    try:
        object.__setattr__(
            database_authority,
            "_arm_observation",
            second._database_observation._arm_observation,
        )
        instance = builder.ControllerReceiptV2Builder._from_reviewed_configuration(
            clock=lambda: NOW
        )
        with pytest.raises(Failure, match="authority_invalid"):
            instance.build(isolation_inventory=first)
    finally:
        object.__setattr__(database_authority, "_arm_observation", original)


def test_clock_freshness_and_expiry_are_strict(
    built_chain: Any,
) -> None:
    authority, _result = built_chain
    for invalid_now in (
        NOW.replace(microsecond=1),
        NOW - timedelta(seconds=1),
        "2026-09-06T00:00:00Z",
        True,
    ):
        instance = builder.ControllerReceiptV2Builder._from_reviewed_configuration(
            clock=lambda invalid_now=invalid_now: invalid_now  # type: ignore[return-value]
        )
        with pytest.raises(Failure):
            instance.build(isolation_inventory=authority)

    near = builder.ControllerReceiptV2Builder._from_reviewed_configuration(
        clock=lambda: NOW + timedelta(seconds=59)
    ).build(isolation_inventory=authority)
    assert near.expires_at - near.created_at == timedelta(seconds=1)
    with pytest.raises(Failure):
        builder.ControllerReceiptV2Builder._from_reviewed_configuration(
            clock=lambda: NOW + timedelta(seconds=60)
        ).build(isolation_inventory=authority)


@pytest.mark.parametrize("value", (False, 0, 0.0))
def test_shared_validator_rejects_non_true_pass(built_chain: Any, value: Any) -> None:
    authority, result = built_chain
    receipt = json.loads(result.receipt_bytes)
    receipt["pass"] = value
    controller = content._parse_canonical(
        authority._database_observation._production_authority._controller_policy,
        content.MAXIMUM_POLICY_BYTES,
    )
    with pytest.raises(content.ControllerReceiptV2Failure):
        content.validate_controller_receipt_v2_content(
            controller,
            intent_bytes=result.intent_bytes,
            submission_bytes=result.submission_bytes,
            receipt_bytes=content._canonical(receipt),
            clock=lambda: NOW,
        )


@pytest.mark.parametrize("value", (False, 0.0, 1))
def test_shared_validator_rejects_nonzero_or_non_int_isolation_count(
    built_chain: Any, value: Any
) -> None:
    authority, result = built_chain
    receipt = json.loads(result.receipt_bytes)
    receipt["isolation"]["firewall_rule_count"] = value
    controller = content._parse_canonical(
        authority._database_observation._production_authority._controller_policy,
        content.MAXIMUM_POLICY_BYTES,
    )
    with pytest.raises(content.ControllerReceiptV2Failure):
        content.validate_controller_receipt_v2_content(
            controller,
            intent_bytes=result.intent_bytes,
            submission_bytes=result.submission_bytes,
            receipt_bytes=content._canonical(receipt),
            clock=lambda: NOW,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("receipt_sha256", "sha256:" + "f" * 64),
        ("record_chain_sha256", "sha256:" + "f" * 64),
        ("authority_chain_sha256", "sha256:" + "f" * 64),
        ("resource_set_sha256", "sha256:" + "f" * 64),
        ("controller_receipt_exists", True),
        ("created_at", NOW + timedelta(seconds=1)),
    ),
)
def test_seal_self_check_detects_output_tampering(
    built_chain: Any, field: str, value: Any
) -> None:
    _authority_value, result = built_chain
    original = getattr(result, field)
    try:
        object.__setattr__(result, field, value)
        assert result._is_built() is False
    finally:
        object.__setattr__(result, field, original)


def test_secret_bearing_upstream_failure_is_sanitized(
    monkeypatch: pytest.MonkeyPatch, built_chain: Any
) -> None:
    authority, _result = built_chain

    def explode(_self: Any) -> bool:
        raise RuntimeError(SECRET)

    monkeypatch.setattr(
        isolation.ControllerReceiptV2IsolationInventoryAuthority,
        "_is_authenticated",
        explode,
    )
    instance = builder.ControllerReceiptV2Builder._from_reviewed_configuration(
        clock=lambda: NOW
    )
    with pytest.raises(Failure) as error:
        instance.build(isolation_inventory=authority)
    assert SECRET not in repr(error.value)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None


def test_builder_has_no_provider_storage_database_subprocess_or_mutation_surface(
    built_chain: Any,
) -> None:
    authority, _result = built_chain
    chain = (
        authority.authority_bytes,
        authority._database_observation.authority_bytes,
        authority._database_observation._arm_observation.authority_bytes,
        authority._database_observation._production_authority.authority_bytes,
    )
    assert chain == (
        authority.authority_bytes,
        authority._database_observation.authority_bytes,
        authority._database_observation._arm_observation.authority_bytes,
        authority._database_observation._production_authority.authority_bytes,
    )

    source_path = Path(builder.__file__)
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imported.isdisjoint(
        {"psycopg", "requests", "socket", "subprocess", "urllib"}
    )
    for token in ('method="POST"', 'method="PUT"', 'method="DELETE"', "connect("):
        assert token not in source
