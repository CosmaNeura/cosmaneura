from __future__ import annotations

import copy
import dataclasses
import inspect
import json
import pickle
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from scripts import bind_staging_restore_fixed_catalog_source_state as binding
from scripts import staging_restore_sanitizer as sanitizer
from scripts import staging_synthetic_inventory as inventory

from tests.infrastructure._staging_restore_fixed_catalog_source_state_helpers import (
    INVENTORY_POLICY,
    NOW,
    POLICY,
    RealChain,
    _binder,
    _reject,
    _seal,
    authenticated,
    real_chain,
)

# Keep the shared fixture objects visible to pytest in this split test module.
__all__ = ("authenticated", "real_chain")


def test_policy_is_pinned_closed_world_narrow_and_dark() -> None:
    policy = json.loads(POLICY.read_bytes())
    inventory_policy = json.loads(INVENTORY_POLICY.read_bytes())
    sanitizer.validate_policy(policy, inventory_policy)
    contract = policy["fixed_catalog_source_state_binding"]
    assert contract["implemented"] is True
    assert contract["binding"]["fixed_catalog_tables"] == list(
        sanitizer.FIXED_CATALOG_TABLE_ORDER
    )
    assert (
        contract["inventory"]["historical_receipt_expiry_is_activation_deadline"]
        is False
    )
    assert contract["output"]["public_fields"] == list(binding.PUBLIC_FIELDS)
    assert contract["claims"] == binding.CLAIMS
    assert policy["status"]["fixed_catalog_source_state_binding_implemented"] is True
    assert policy["status"]["trusted_evidence_binding_implemented"] is True
    for name in (
        "database_mutation_implemented",
        "executable",
        "workflow_integrated",
        "live_provider_calls_enabled",
    ):
        assert policy["status"][name] is False
    assert "fixed_catalog_post_projection_verification" in sanitizer.ACTIVATION_BLOCKERS
    assert (
        "fixed_catalog_source_state_and_post_projection_verification"
        not in sanitizer.ACTIVATION_BLOCKERS
    )


def test_real_chain_mints_private_ordered_source_state_after_historical_expiry(
    real_chain: RealChain,
    authenticated: Any,
) -> None:
    document, raw, _authority, _built, _written, target = real_chain
    result = authenticated
    assert result._is_authenticated()
    assert result.inventory_receipt_sha256 == f"sha256:{document['receipt_sha256']}"
    assert (
        result.inventory_policy_sha256 == f"sha256:{inventory.CANONICAL_POLICY_SHA256}"
    )
    assert result.approved_state_sha256 == f"sha256:{document['approved_state_sha256']}"
    assert result.hmac_key_version == inventory.SUPPORTED_HMAC_KEY_VERSION
    assert (result.source_profile, result.source_revision) == ("pre", "89c31f0ae275")
    assert result.inventory_observed_at == datetime(2026, 9, 5, 12, tzinfo=UTC)
    assert result.inventory_expires_at < result.authenticated_at == NOW
    assert result.target_binding_expires_at == target.expires_at
    assert result._target_binding is target
    assert result._inventory_receipt_bytes is raw
    fixed = dict(result._ordered_projection)["fixed_catalog"]
    assert tuple(item[0] for item in fixed) == binding.FIXED_CATALOG_TABLES
    assert all(type(count) is int and count >= 0 for _, count, _ in fixed)
    assert all(binding.HEX_SHA256_RE.fullmatch(value) for _, _, value in fixed)
    public = {
        field.name
        for field in dataclasses.fields(result)
        if not field.name.startswith("_") and field.name not in binding.CLAIMS
    }
    assert public == set(binding.PUBLIC_FIELDS)
    with pytest.raises(binding.FixedCatalogSourceStateFailure, match="output_invalid"):
        pickle.dumps(result)


def test_exact_api_rejects_other_types_construction_and_overrides(
    real_chain: RealChain,
) -> None:
    _document, raw, _authority, built, written, target = real_chain
    parameters = inspect.signature(
        binding.FixedCatalogSourceStateBinder.bind
    ).parameters
    assert tuple(parameters) == ("self", "target_binding", "inventory_receipt_bytes")
    assert all(
        parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        for name in ("target_binding", "inventory_receipt_bytes")
    )

    class Lookalike:
        def _is_bound(self) -> bool:
            return True

    class TargetSubclass(sanitizer.SanitizerTargetBinding):
        pass

    for other in (
        built,
        written,
        Lookalike(),
        object.__new__(sanitizer.SanitizerTargetBinding),
        object.__new__(TargetSubclass),
    ):
        _reject(other, raw, "authority_invalid")
    for other in (
        bytearray(raw),
        memoryview(raw),
        raw.decode(),
        json.loads(raw),
        Path("x"),
    ):
        _reject(target, other, "inventory_invalid")
    with pytest.raises(TypeError):
        cast(Any, _binder().bind)(
            target_binding=target,
            inventory_receipt_bytes=raw,
            source_sha="a" * 40,
        )
    with pytest.raises(
        binding.FixedCatalogSourceStateFailure, match="configuration_invalid"
    ):
        binding.FixedCatalogSourceStateBinder()
    with pytest.raises(binding.FixedCatalogSourceStateFailure, match="output_invalid"):
        binding.AuthenticatedFixedCatalogSourceState()
    with pytest.raises(
        binding.FixedCatalogSourceStateFailure,
        match="activation.sanitizer_executor_unimplemented",
    ):
        binding.build_fixed_catalog_source_state_binder()


def test_duplicate_noncanonical_malformed_and_oversized_bytes_refuse(
    real_chain: RealChain,
) -> None:
    _document, raw, _authority, _built, _written, target = real_chain
    duplicates = raw[:-1] + b',"schema_version":2}'
    nested_duplicate = raw.replace(
        b'"provenance":{"event_name":',
        b'"provenance":{"event_name":"workflow_dispatch","event_name":',
        1,
    )
    for value in (
        b"",
        b"[]",
        b"{}\n",
        b'{"value":NaN}',
        b"\xff",
        duplicates,
        nested_duplicate,
        b"{" + b" " * binding.MAXIMUM_INVENTORY_RECEIPT_BYTES + b"}",
    ):
        _reject(target, value, "inventory_invalid")


@pytest.mark.parametrize(
    ("name", "value"),
    (("workflow_sha", "d" * 40), ("run_id", "7"), ("run_attempt", "2")),
)
def test_workflow_and_run_cross_binding_mismatches_refuse(
    real_chain: RealChain,
    name: str,
    value: str,
) -> None:
    document, _raw, _authority, _built, _written, target = real_chain
    changed = copy.deepcopy(document)
    changed["provenance"][name] = value
    changed["signer_claims"][name] = value
    if name == "workflow_sha":
        changed["signer_claims"]["sha"] = value
    _reject(target, _seal(changed), "context_invalid")
