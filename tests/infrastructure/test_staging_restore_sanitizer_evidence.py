from __future__ import annotations

import copy
import dataclasses
import hashlib
import inspect
import json
import pickle
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import bind_staging_restore_fixed_catalog_source_state as fixed
from scripts import bind_staging_restore_sanitizer_evidence as binding
from scripts import compose_staging_restore_evidence_binding as composition
from scripts import staging_restore_safe_submit as safe_submit
from scripts import staging_restore_sanitizer as sanitizer

from tests.infrastructure import (
    _staging_restore_fixed_catalog_source_state_helpers as fixed_helpers,
)
from tests.infrastructure import (
    test_staging_controller_receipt_v2 as content_helpers,
)

ROOT = Path(__file__).parents[2]
POLICY = ROOT / "contracts/staging-rehearsal/v1/restore-sanitizer-policy.json"
SCRIPT = ROOT / "scripts/bind_staging_restore_sanitizer_evidence.py"
BIND_AT = fixed_helpers.NOW + timedelta(seconds=45)
EVIDENCE_EXPIRES_AT = "2026-09-05T12:10:30Z"


@pytest.fixture(scope="module")
def real_case() -> Iterator[SimpleNamespace]:
    monkeypatch = pytest.MonkeyPatch()
    original_evidence = content_helpers._evidence

    def evidence() -> dict[str, Any]:
        value = original_evidence()
        value["expires_at"] = EVIDENCE_EXPIRES_AT
        return value

    monkeypatch.setattr(content_helpers, "_evidence", evidence)
    document = fixed_helpers._inventory_document()
    raw = fixed_helpers._seal(document)
    try:
        with fixed_helpers._chain(raw) as (authority, built, written, target):
            source = fixed.FixedCatalogSourceStateBinder._from_reviewed_configuration(
                clock=lambda: fixed_helpers.NOW
            ).bind(target_binding=target, inventory_receipt_bytes=raw)
            record = json.loads(built.intent_bytes)["evidence_binding"]
            restore_evidence = composition._mint_binding(record)
            assert set(record) == set(composition.OUTPUT_FIELDS)
            assert (
                safe_submit.SafeRestoreSubmitter._binding_record(restore_evidence)
                == record
            )
            yield SimpleNamespace(
                authority=authority,
                built=built,
                written=written,
                target=target,
                source=source,
                record=record,
                restore_evidence=restore_evidence,
            )
    finally:
        monkeypatch.undo()


def _binder(*times: datetime) -> binding.SanitizerEvidenceBinder:
    values = iter(times or (BIND_AT, BIND_AT))
    return binding.SanitizerEvidenceBinder._from_reviewed_configuration(
        clock=lambda: next(values)
    )


def _bind(case: SimpleNamespace, *times: datetime) -> Any:
    return _binder(*times).bind(
        fixed_catalog_source_state=case.source,
        restore_evidence_binding=case.restore_evidence,
    )


def _alternate(record: dict[str, Any], field: str, value: Any) -> Any:
    changed = copy.deepcopy(record)
    changed[field] = value
    return composition._mint_binding(changed)


def test_policy_is_hash_pinned_closed_world_and_dark() -> None:
    policy = json.loads(POLICY.read_bytes())
    inventory_policy = json.loads(sanitizer.INVENTORY_POLICY_PATH.read_bytes())
    sanitizer.validate_policy(policy, inventory_policy)
    contract = policy["trusted_evidence_binding"]

    assert hashlib.sha256(POLICY.read_bytes()).hexdigest() == (
        sanitizer.CANONICAL_POLICY_SHA256
    )
    assert policy["status"]["trusted_evidence_binding_implemented"] is True
    assert "authenticated_server_scope_evidence_binding" not in (
        sanitizer.ACTIVATION_BLOCKERS
    )
    assert contract["implemented"] is True
    assert contract["input"]["only_public_data_inputs"] == [
        "fixed_catalog_source_state",
        "restore_evidence_binding",
    ]
    assert contract["upstream_policies"]["restore_evidence_composition"] == {
        "policy_id": "cosmaneura-staging-restore-evidence-binding-v1",
        "sha256": f"sha256:{composition.POLICY_SHA256}",
    }
    assert contract["output"]["public_fields"] == list(binding.PUBLIC_FIELDS)
    assert contract["claims"] == binding.CLAIMS
    assert binding.CLAIMS["server_scope_authenticated"] is True
    for name in (
        "database_connection_authority",
        "database_mutation_authority",
        "executable",
        "live_provider_calls_enabled",
        "post_projection_authenticated",
        "provider_calls_permitted",
        "provider_facts_authenticated",
        "runtime_authority",
        "sanitizer_authority",
        "workflow_integrated",
    ):
        assert binding.CLAIMS[name] is False
    assert policy["execution_contract"]["implemented"] is False
    assert policy["execution_contract"]["database_connections_permitted"] is False
    assert policy["execution_contract"]["sql_mutation_permitted"] is False


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["trusted_evidence_binding"].update({"implemented": False}),
        lambda value: value["trusted_evidence_binding"].update(
            {"caller_selected_authority": True}
        ),
        lambda value: value["trusted_evidence_binding"]["binding"].update(
            {"historical_restore_evidence_expiry_is_current_activation_deadline": True}
        ),
        lambda value: value["trusted_evidence_binding"]["output"][
            "public_fields"
        ].pop(),
        lambda value: value["trusted_evidence_binding"]["claims"].update(
            {"database_connection_authority": True}
        ),
    ],
)
def test_trusted_evidence_policy_mutations_fail_closed(mutate: Any) -> None:
    policy = json.loads(POLICY.read_bytes())
    inventory_policy = json.loads(sanitizer.INVENTORY_POLICY_PATH.read_bytes())
    mutate(policy)
    with pytest.raises(sanitizer.SanitizerFailure):
        sanitizer.validate_policy(policy, inventory_policy)


def test_real_chain_joins_final_evidence_after_historical_expiry(
    real_case: SimpleNamespace,
) -> None:
    result = _bind(real_case)

    assert type(result) is binding.AuthenticatedSanitizerEvidence
    assert result._is_authenticated()
    assert result._fixed_catalog_source_state is real_case.source
    assert result._restore_evidence_binding is real_case.restore_evidence
    assert json.loads(result._evidence_record_bytes) == real_case.record
    assert result.rehearsal_id == real_case.target.rehearsal_id
    assert result.controller_receipt_sha256 == real_case.target.receipt_sha256
    assert result.inventory_receipt_sha256 == real_case.source.inventory_receipt_sha256
    assert result.evidence_binding_sha256 == binding._digest(
        result._evidence_record_bytes
    )
    assert result.evidence_expires_at < result.authenticated_at
    assert result.authenticated_at < result.target_binding_expires_at
    assert all(getattr(result, name) is item for name, item in binding.CLAIMS.items())
    assert not hasattr(result, "__dict__")
    assert {field.name for field in dataclasses.fields(result)} == {
        *binding.PUBLIC_FIELDS,
        *binding.CLAIMS,
        "_fixed_catalog_source_state",
        "_restore_evidence_binding",
        "_evidence_record_bytes",
        "_sanitizer_policy_bytes",
        "_composition_policy_bytes",
        "_proof_sha256",
        "_seal",
    }
    rendered = repr(result)
    assert "target_fqdn=" not in rendered
    assert "approved_database_role=" not in rendered


@pytest.mark.parametrize("field", sanitizer.TRUSTED_EVIDENCE_DIGEST_FIELDS)
def test_every_evidence_digest_is_bound_to_the_controller_intent(
    real_case: SimpleNamespace, field: str
) -> None:
    alternate = _alternate(real_case.record, field, f"sha256:{'f' * 64}")
    with pytest.raises(binding.SanitizerEvidenceFailure):
        _binder().bind(
            fixed_catalog_source_state=real_case.source,
            restore_evidence_binding=alternate,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_sha", "f" * 40),
        ("candidate_sha", "e" * 40),
        ("run_id", "42"),
        ("run_attempt", "2"),
        ("approved_restore_time", "2026-09-05T12:00:01Z"),
        ("observed_at", "2026-09-05T12:00:01Z"),
    ],
)
def test_source_restore_and_run_context_cannot_be_substituted(
    real_case: SimpleNamespace, field: str, value: str
) -> None:
    record = copy.deepcopy(real_case.record)
    record[field] = value
    if field == "approved_restore_time":
        record["observed_at"] = value
    if field == "observed_at":
        record["approved_restore_time"] = value
    alternate = composition._mint_binding(record)
    with pytest.raises(binding.SanitizerEvidenceFailure):
        _binder().bind(
            fixed_catalog_source_state=real_case.source,
            restore_evidence_binding=alternate,
        )


def test_exact_api_rejects_mappings_partials_subclasses_and_forged_seals(
    real_case: SimpleNamespace,
) -> None:
    signature = inspect.signature(binding.SanitizerEvidenceBinder.bind)
    assert tuple(signature.parameters) == (
        "self",
        "fixed_catalog_source_state",
        "restore_evidence_binding",
    )
    assert all(
        item.kind is inspect.Parameter.KEYWORD_ONLY
        for name, item in signature.parameters.items()
        if name != "self"
    )
    with pytest.raises(binding.SanitizerEvidenceFailure):
        binding.SanitizerEvidenceBinder()
    with pytest.raises(binding.SanitizerEvidenceFailure):
        binding.build_sanitizer_evidence_binder()
    with pytest.raises(binding.SanitizerEvidenceFailure):
        _binder().bind(
            fixed_catalog_source_state=real_case.source,
            restore_evidence_binding=real_case.record,
        )

    class EvidenceSubclass(safe_submit.RestoreEvidenceBinding):
        pass

    subclass = object.__new__(EvidenceSubclass)
    for field in dataclasses.fields(safe_submit.RestoreEvidenceBinding):
        object.__setattr__(
            subclass, field.name, getattr(real_case.restore_evidence, field.name)
        )
    with pytest.raises(binding.SanitizerEvidenceFailure):
        _binder().bind(
            fixed_catalog_source_state=real_case.source,
            restore_evidence_binding=subclass,
        )

    forged = object.__new__(safe_submit.RestoreEvidenceBinding)
    for field in dataclasses.fields(safe_submit.RestoreEvidenceBinding):
        object.__setattr__(
            forged,
            field.name,
            object()
            if field.name == "_binding_seal"
            else getattr(real_case.restore_evidence, field.name),
        )
    with pytest.raises(binding.SanitizerEvidenceFailure):
        _binder().bind(
            fixed_catalog_source_state=real_case.source,
            restore_evidence_binding=forged,
        )


def test_mixed_real_chains_and_tampered_outputs_refuse(
    real_case: SimpleNamespace,
) -> None:
    result = _bind(real_case)
    object.__setattr__(result, "server_scope_authenticated", False)
    assert not result._is_authenticated()

    replacement_record = copy.deepcopy(real_case.record)
    replacement_record["inventory_receipt_sha256"] = f"sha256:{'f' * 64}"
    replacement = composition._mint_binding(replacement_record)
    with pytest.raises(binding.SanitizerEvidenceFailure):
        _binder().bind(
            fixed_catalog_source_state=real_case.source,
            restore_evidence_binding=replacement,
        )

    forged_source = object.__new__(fixed.AuthenticatedFixedCatalogSourceState)
    for field in dataclasses.fields(fixed.AuthenticatedFixedCatalogSourceState):
        object.__setattr__(
            forged_source,
            field.name,
            "0" * 64
            if field.name == "_proof_sha256"
            else getattr(real_case.source, field.name),
        )
    with pytest.raises(
        binding.SanitizerEvidenceFailure,
        match="sanitizer_evidence.authority_invalid",
    ):
        _binder().bind(
            fixed_catalog_source_state=forged_source,
            restore_evidence_binding=real_case.restore_evidence,
        )


def test_clock_shape_regression_and_target_expiry_refuse(
    real_case: SimpleNamespace,
) -> None:
    for invalid in (
        datetime(2026, 9, 5, 12, 10, 45),
        datetime(2026, 9, 5, 12, 10, 45, 1, tzinfo=UTC),
    ):
        with pytest.raises(binding.SanitizerEvidenceFailure):
            _binder(invalid).bind(
                fixed_catalog_source_state=real_case.source,
                restore_evidence_binding=real_case.restore_evidence,
            )
    with pytest.raises(
        binding.SanitizerEvidenceFailure,
        match="sanitizer_evidence.clock_regressed",
    ):
        _binder(BIND_AT, BIND_AT - timedelta(seconds=1)).bind(
            fixed_catalog_source_state=real_case.source,
            restore_evidence_binding=real_case.restore_evidence,
        )
    with pytest.raises(
        binding.SanitizerEvidenceFailure,
        match="sanitizer_evidence.target_expired",
    ):
        _binder(real_case.target.expires_at).bind(
            fixed_catalog_source_state=real_case.source,
            restore_evidence_binding=real_case.restore_evidence,
        )


def test_output_cannot_be_constructed_or_serialized(
    real_case: SimpleNamespace,
) -> None:
    with pytest.raises(binding.SanitizerEvidenceFailure):
        binding.AuthenticatedSanitizerEvidence()
    result = _bind(real_case)
    for operation in (pickle.dumps, copy.copy, copy.deepcopy):
        with pytest.raises(binding.SanitizerEvidenceFailure):
            operation(result)


def test_module_has_no_runtime_or_mutation_surface() -> None:
    source = SCRIPT.read_text()
    for forbidden in (
        "import psycopg",
        "import requests",
        "import socket",
        "import subprocess",
        "azure.identity",
        "azure.mgmt",
        "execute(",
        "commit(",
        "rollback(",
        "connect(",
    ):
        assert forbidden not in source
    assert "server_scope_hmac_key_bytes" not in source
    assert "inventory_receipt_bytes:" not in source
