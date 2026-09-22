from __future__ import annotations

import copy
from typing import Any

import pytest

from scripts import bind_staging_restore_fixed_catalog_source_state as binding

from tests.infrastructure._staging_restore_fixed_catalog_source_state_helpers import (
    SCRIPT,
    RealChain,
    _chain,
    _reject,
    _seal,
    authenticated,
    real_chain,
)

# Keep the shared fixture objects visible to pytest in this split test module.
__all__ = ("authenticated", "real_chain")


@pytest.mark.parametrize(
    "mutation",
    (
        "top_extra",
        "top_missing",
        "negative_count",
        "boolean_count",
        "uppercase_hmac",
        "missing_table",
        "extra_table",
        "failed_outcome",
    ),
)
def test_closed_world_counts_hmacs_and_outcome_refuse(
    real_chain: RealChain,
    mutation: str,
) -> None:
    document, _raw, _authority, _built, _written, target = real_chain
    changed = copy.deepcopy(document)
    if mutation == "top_extra":
        changed["unexpected"] = True
    elif mutation == "top_missing":
        del changed["catalog_hmac"]
    elif mutation == "negative_count":
        changed["counts"]["database.baa_version"] = -1
    elif mutation == "boolean_count":
        changed["counts"]["database.doctor_note_schema"] = False
    elif mutation == "uppercase_hmac":
        changed["state_hmacs"]["drug_reference"] = "A" * 64
    elif mutation == "missing_table":
        del changed["state_hmacs"]["lab_range_set"]
    elif mutation == "extra_table":
        changed["state_hmacs"]["unexpected"] = "a" * 64
    else:
        changed["outcome"] = "fail"
        changed["errors"] = ["database.table.baa_version.approved_state"]
    _reject(target, _seal(changed), "inventory_invalid")


def test_digest_tamper_and_mixed_real_binding_refuse(
    real_chain: RealChain,
) -> None:
    document, raw, _authority, _built, _written, target = real_chain
    changed = copy.deepcopy(document)
    changed["approved_state_sha256"] = "f" * 64
    other_raw = _seal(changed)
    _reject(target, other_raw, "digest_invalid")

    with _chain(other_raw) as (_a, _b, _w, other_target):
        _reject(other_target, raw, "digest_invalid")


def test_output_tamper_forgery_and_ancestor_tamper_refuse(
    real_chain: RealChain,
    authenticated: Any,
) -> None:
    _document, _raw, authority, _built, _written, _target = real_chain
    result = authenticated
    forged = object.__new__(binding.AuthenticatedFixedCatalogSourceState)
    assert forged._is_authenticated() is False

    class OutputSubclass(binding.AuthenticatedFixedCatalogSourceState):
        pass

    assert object.__new__(OutputSubclass)._is_authenticated() is False
    original_digest = result.fixed_catalog_projection_sha256
    try:
        object.__setattr__(
            result, "fixed_catalog_projection_sha256", "sha256:" + "0" * 64
        )
        assert result._is_authenticated() is False
    finally:
        object.__setattr__(result, "fixed_catalog_projection_sha256", original_digest)
    original_projection = result._ordered_projection
    try:
        object.__setattr__(result, "_ordered_projection", ())
        assert result._is_authenticated() is False
    finally:
        object.__setattr__(result, "_ordered_projection", original_projection)
    original_rehearsal = authority.rehearsal_id
    try:
        object.__setattr__(authority, "rehearsal_id", "fedcba987654")
        assert result._is_authenticated() is False
    finally:
        object.__setattr__(authority, "rehearsal_id", original_rehearsal)
    assert result._is_authenticated()


def test_claims_are_narrow_and_module_has_no_io_execution_surface(
    authenticated: Any,
) -> None:
    for name, expected in binding.CLAIMS.items():
        assert getattr(authenticated, name) is expected
    source = SCRIPT.read_text()
    for forbidden in (
        "import psycopg",
        "import socket",
        "import subprocess",
        "urllib.request",
        "server_scope_hmac_key_bytes",
        "connect(",
        "execute(",
    ):
        assert forbidden not in source
