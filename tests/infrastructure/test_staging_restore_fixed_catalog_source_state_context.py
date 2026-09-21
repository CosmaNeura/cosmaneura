from __future__ import annotations

import copy
from datetime import datetime, timedelta
from typing import Any

import pytest

from scripts import bind_staging_restore_fixed_catalog_source_state as binding

from tests.infrastructure._staging_restore_fixed_catalog_source_state_helpers import (
    NOW,
    RealChain,
    _binder,
    _reject,
    _seal,
    _set,
    real_chain,
)

# Keep the shared fixture object visible to pytest in this split test module.
__all__ = ("real_chain",)


@pytest.mark.parametrize(
    ("path", "value", "code"),
    (
        ("schema_version", None, "inventory_invalid"),
        ("policy_id", None, "inventory_invalid"),
        ("policy_sha256", "F" * 64, "inventory_invalid"),
        ("hmac_key_version", None, "inventory_invalid"),
        ("phase", "post", "inventory_invalid"),
        ("schema_descriptor_sha256", "f" * 64, "inventory_invalid"),
        ("source_resource.cloud", "aws", "inventory_invalid"),
        ("source_sha", "d" * 40, "context_invalid"),
        ("candidate_sha", "e" * 40, "context_invalid"),
        ("source_resource.postgres_server_id", "/other/source", "inventory_invalid"),
        ("source_resource.database_name", "other", "inventory_invalid"),
        ("observed_at", "2026-09-05T11:59:00Z", "context_invalid"),
        ("provenance.repository", "Other/repository", "inventory_invalid"),
        ("provenance.protected_ref", "refs/heads/other", "inventory_invalid"),
        ("provenance.workflow_path", "wrong", "inventory_invalid"),
        ("signer_claims.issuer", "https://issuer.invalid", "inventory_invalid"),
        ("signer_claims.subject", "wrong", "inventory_invalid"),
    ),
)
def test_inventory_context_and_policy_mismatches_refuse(
    real_chain: RealChain,
    path: str,
    value: Any,
    code: str,
) -> None:
    document, _raw, _authority, _built, _written, target = real_chain
    changed = copy.deepcopy(document)
    _set(changed, path, value)
    _reject(target, _seal(changed), code)


def test_clock_target_expiry_and_regression_refuse(
    real_chain: RealChain,
) -> None:
    _document, raw, _authority, _built, _written, target = real_chain
    for clock in (
        lambda: datetime(2026, 9, 5, 12, 10),
        lambda: NOW + timedelta(microseconds=1),
    ):
        with pytest.raises(
            binding.FixedCatalogSourceStateFailure, match="clock_invalid"
        ):
            binding.FixedCatalogSourceStateBinder._from_reviewed_configuration(
                clock=clock
            ).bind(target_binding=target, inventory_receipt_bytes=raw)
    with pytest.raises(binding.FixedCatalogSourceStateFailure, match="target_expired"):
        _binder(target.expires_at).bind(
            target_binding=target, inventory_receipt_bytes=raw
        )
    with pytest.raises(
        binding.FixedCatalogSourceStateFailure, match="authority_invalid"
    ):
        _binder(target.bound_at - timedelta(seconds=1)).bind(
            target_binding=target, inventory_receipt_bytes=raw
        )
    values = iter((NOW, NOW - timedelta(seconds=1)))
    binder = binding.FixedCatalogSourceStateBinder._from_reviewed_configuration(
        clock=lambda: next(values)
    )
    with pytest.raises(binding.FixedCatalogSourceStateFailure, match="clock_regressed"):
        binder.bind(target_binding=target, inventory_receipt_bytes=raw)
