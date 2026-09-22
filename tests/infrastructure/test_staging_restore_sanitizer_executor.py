from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import bind_staging_restore_fixed_catalog_source_state as fixed
from scripts import bind_staging_restore_sanitizer_evidence as evidence_binding
from scripts import compose_staging_restore_evidence_binding as composition
from scripts import staging_restore_sanitizer as sanitizer
from scripts import staging_restore_sanitizer_evidence_authority as authority
from scripts import staging_restore_sanitizer_executor as executor

from tests.infrastructure import (
    _staging_restore_fixed_catalog_source_state_helpers as fixed_helpers,
)
from tests.infrastructure import (
    test_staging_controller_receipt_v2 as content_helpers,
)

ROOT = Path(__file__).parents[2]
POLICY = ROOT / "contracts/staging-rehearsal/v1/restore-sanitizer-policy.json"
SCRIPT = ROOT / "scripts/staging_restore_sanitizer_executor.py"
AUTHORITY_SCRIPT = ROOT / "scripts/staging_restore_sanitizer_evidence_authority.py"
BIND_AT = fixed_helpers.NOW + timedelta(seconds=45)
PREFLIGHT_AT = BIND_AT + timedelta(seconds=5)
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
    raw = fixed_helpers._seal(fixed_helpers._inventory_document())
    try:
        with fixed_helpers._chain(raw) as (_authority, built, _written, target):
            source = fixed.FixedCatalogSourceStateBinder._from_reviewed_configuration(
                clock=lambda: fixed_helpers.NOW
            ).bind(target_binding=target, inventory_receipt_bytes=raw)
            record = json.loads(built.intent_bytes)["evidence_binding"]
            restore_evidence = composition._mint_binding(record)
            authenticated = (
                evidence_binding.SanitizerEvidenceBinder._from_reviewed_configuration(
                    clock=lambda: BIND_AT
                ).bind(
                    fixed_catalog_source_state=source,
                    restore_evidence_binding=restore_evidence,
                )
            )
            yield SimpleNamespace(
                authenticated=authenticated,
                source=source,
                target=target,
                record=record,
                restore_evidence=restore_evidence,
            )
    finally:
        monkeypatch.undo()


def _preflight(*times: datetime) -> executor.SanitizerExecutorPreflight:
    values = iter(times or (PREFLIGHT_AT, PREFLIGHT_AT))
    return executor.SanitizerExecutorPreflight._from_reviewed_configuration(
        clock=lambda: next(values)
    )


def _run(value: Any, *times: datetime) -> None:
    _preflight(*times).preflight(authenticated_sanitizer_evidence=value)


def test_policy_hash_and_closed_world_preflight_are_exact() -> None:
    policy = json.loads(POLICY.read_bytes())
    inventory_policy = json.loads(sanitizer.INVENTORY_POLICY_PATH.read_bytes())
    sanitizer.validate_policy(policy, inventory_policy)
    contract = policy["execution_contract"]
    preflight = contract["trusted_evidence_preflight"]

    assert hashlib.sha256(POLICY.read_bytes()).hexdigest() == (
        sanitizer.CANONICAL_POLICY_SHA256
    )
    assert contract["implemented"] is False
    assert contract["database_connections_permitted"] is False
    assert contract["sql_mutation_permitted"] is False
    assert authority.PUBLIC_FIELDS == sanitizer.TRUSTED_EVIDENCE_PUBLIC_FIELDS
    assert authority.CLAIMS == sanitizer.TRUSTED_EVIDENCE_CLAIMS
    assert authority.CONTEXT_FIELDS == sanitizer.FIXED_CATALOG_CONTEXT_FIELDS
    assert authority.DIGEST_FIELDS == sanitizer.TRUSTED_EVIDENCE_DIGEST_FIELDS
    assert (
        authority.COMPOSITION_POLICY_SHA256
        == sanitizer.RESTORE_EVIDENCE_COMPOSITION_POLICY_SHA256
    )
    assert preflight["closed_world"] is True
    assert preflight["implemented"] is True
    assert preflight["input"]["only_public_data_input"] == (
        "authenticated_sanitizer_evidence"
    )
    assert preflight["authority"] == sanitizer.TRUSTED_EVIDENCE_PREFLIGHT_AUTHORITY
    assert not any(preflight["authority"].values())
    assert preflight["outcome"] == {
        "always_refuses_before_io": True,
        "failure_code": executor.ACTIVATION_FAILURE,
        "output_minted": False,
    }
    assert "data_contract_executor_implementation" in sanitizer.ACTIVATION_BLOCKERS


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update({"closed_world": False}),
        lambda value: value.update({"implemented": False}),
        lambda value: value["input"].update(
            {"only_public_data_input": "restore_evidence_binding"}
        ),
        lambda value: value["reauthentication"].update(
            {"complete_retained_ancestry_at_current_time_required": False}
        ),
        lambda value: value["outcome"].update({"output_minted": True}),
        lambda value: value["authority"].update(
            {"database_connection_authority": True}
        ),
        lambda value: value.update({"unexpected": False}),
    ],
)
def test_every_preflight_policy_surface_mutation_fails_closed(mutate: Any) -> None:
    policy = json.loads(POLICY.read_bytes())
    inventory_policy = json.loads(sanitizer.INVENTORY_POLICY_PATH.read_bytes())
    mutate(policy["execution_contract"]["trusted_evidence_preflight"])
    with pytest.raises(sanitizer.SanitizerFailure, match="policy.execution"):
        sanitizer.validate_policy(policy, inventory_policy)


def test_exact_authenticated_evidence_reauthenticates_then_refuses(
    real_case: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[datetime] = []
    original = sanitizer._binding_projection

    def observe(*, written_receipt: Any, policy: Any, now: datetime) -> Any:
        seen.append(now)
        return original(written_receipt=written_receipt, policy=policy, now=now)

    monkeypatch.setattr(sanitizer, "_binding_projection", observe)
    assert real_case.authenticated.evidence_expires_at < PREFLIGHT_AT
    assert PREFLIGHT_AT < real_case.authenticated.target_binding_expires_at
    with pytest.raises(
        executor.SanitizerExecutorFailure,
        match=f"^{executor.ACTIVATION_FAILURE}$",
    ):
        _run(real_case.authenticated)
    assert PREFLIGHT_AT in seen


def test_api_has_one_keyword_only_public_data_input_and_no_public_constructor(
    real_case: SimpleNamespace,
) -> None:
    signature = inspect.signature(executor.SanitizerExecutorPreflight.preflight)
    assert tuple(signature.parameters) == (
        "self",
        "authenticated_sanitizer_evidence",
    )
    assert (
        signature.parameters["authenticated_sanitizer_evidence"].kind
        is inspect.Parameter.KEYWORD_ONLY
    )
    with pytest.raises(
        executor.SanitizerExecutorFailure,
        match="sanitizer_executor.configuration_invalid",
    ):
        executor.SanitizerExecutorPreflight()

    class PreflightSubclass(executor.SanitizerExecutorPreflight):
        pass

    with pytest.raises(executor.SanitizerExecutorFailure):
        PreflightSubclass._from_reviewed_configuration(clock=lambda: PREFLIGHT_AT)
    with pytest.raises(executor.SanitizerExecutorFailure):
        _run({"authenticated_sanitizer_evidence": real_case.authenticated})


def test_subclass_and_forged_evidence_refuse(real_case: SimpleNamespace) -> None:
    class EvidenceSubclass(evidence_binding.AuthenticatedSanitizerEvidence):
        pass

    subclass = object.__new__(EvidenceSubclass)
    for field in dataclasses.fields(evidence_binding.AuthenticatedSanitizerEvidence):
        object.__setattr__(
            subclass, field.name, getattr(real_case.authenticated, field.name)
        )
    forged = object.__new__(evidence_binding.AuthenticatedSanitizerEvidence)
    for field in dataclasses.fields(evidence_binding.AuthenticatedSanitizerEvidence):
        object.__setattr__(
            forged,
            field.name,
            object()
            if field.name == "_seal"
            else getattr(real_case.authenticated, field.name),
        )
    for value in (subclass, forged):
        with pytest.raises(
            executor.SanitizerExecutorFailure,
            match="sanitizer_executor.authority_invalid",
        ):
            _run(value)


def test_current_time_bounds_refuse_before_activation(
    real_case: SimpleNamespace,
) -> None:
    with pytest.raises(
        executor.SanitizerExecutorFailure,
        match="sanitizer_executor.clock_regressed",
    ):
        _run(real_case.authenticated, BIND_AT - timedelta(seconds=1))
    with pytest.raises(
        executor.SanitizerExecutorFailure,
        match="sanitizer_executor.target_expired",
    ):
        _run(real_case.authenticated, real_case.target.expires_at)


def test_final_clock_read_is_reauthenticated_before_refusal(
    real_case: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    final = PREFLIGHT_AT + timedelta(seconds=1)
    seen: list[datetime] = []

    def observe(_value: Any, *, now: datetime) -> None:
        seen.append(now)
        if now == final:
            raise evidence_binding.SanitizerEvidenceFailure(
                "sanitizer_evidence.target_expired"
            )

    monkeypatch.setattr(authority, "_reauthenticate_at_current_time", observe)
    with pytest.raises(
        executor.SanitizerExecutorFailure,
        match="sanitizer_executor.target_expired",
    ):
        _run(real_case.authenticated, PREFLIGHT_AT, final)
    assert seen == [PREFLIGHT_AT, final]


def test_invalid_and_regressing_private_clock_refuse(
    real_case: SimpleNamespace,
) -> None:
    for invalid in (
        datetime(2026, 9, 5, 12, 11),
        datetime(2026, 9, 5, 12, 11, 0, 1, tzinfo=UTC),
        datetime(2026, 9, 5, 7, 11, tzinfo=timezone(timedelta(hours=-5))),
    ):
        with pytest.raises(
            executor.SanitizerExecutorFailure,
            match="sanitizer_executor.clock_invalid",
        ):
            _run(real_case.authenticated, invalid)
    with pytest.raises(
        executor.SanitizerExecutorFailure,
        match="sanitizer_executor.clock_regressed",
    ):
        preflight = _preflight(PREFLIGHT_AT, PREFLIGHT_AT - timedelta(seconds=1))
        assert preflight._read_clock() == PREFLIGHT_AT
        preflight._read_clock()


def test_retained_ancestor_tampering_refuses(
    real_case: SimpleNamespace,
) -> None:
    evidence = real_case.authenticated
    source = evidence._fixed_catalog_source_state
    original_projection = source._ordered_projection
    object.__setattr__(source, "_ordered_projection", (*original_projection, ("x", 1)))
    try:
        with pytest.raises(
            executor.SanitizerExecutorFailure,
            match="sanitizer_executor.authority_invalid",
        ):
            _run(evidence)
    finally:
        object.__setattr__(source, "_ordered_projection", original_projection)


def test_retained_restore_binding_requires_type_exact_canonical_equality(
    real_case: SimpleNamespace,
) -> None:
    restore_evidence = real_case.restore_evidence
    original_schema_version = restore_evidence.schema_version
    assert original_schema_version == 1
    object.__setattr__(restore_evidence, "schema_version", True)
    try:
        with pytest.raises(
            executor.SanitizerExecutorFailure,
            match="^sanitizer_executor.authority_invalid$",
        ):
            _run(real_case.authenticated)
    finally:
        object.__setattr__(
            restore_evidence,
            "schema_version",
            original_schema_version,
        )


def test_retained_fixed_catalog_requires_type_exact_canonical_equality(
    real_case: SimpleNamespace,
) -> None:
    source = real_case.source
    original_projection = source._ordered_projection
    changed_projection: list[tuple[str, Any]] = []
    for name, value in original_projection:
        if name != "fixed_catalog":
            changed_projection.append((name, value))
            continue
        fixed_catalog = list(value)
        table, count, state_hmac = fixed_catalog[0]
        assert table == "baa_version"
        assert type(count) is int and count == 1
        fixed_catalog[0] = (table, True, state_hmac)
        changed_projection.append((name, tuple(fixed_catalog)))
    object.__setattr__(source, "_ordered_projection", tuple(changed_projection))
    try:
        with pytest.raises(
            executor.SanitizerExecutorFailure,
            match="^sanitizer_executor.authority_invalid$",
        ):
            _run(real_case.authenticated)
    finally:
        object.__setattr__(source, "_ordered_projection", original_projection)


def test_plain_fake_source_cannot_replace_retained_exact_authority(
    real_case: SimpleNamespace,
) -> None:
    evidence = real_case.authenticated
    genuine = evidence._fixed_catalog_source_state

    class FakeSource:
        def __init__(self) -> None:
            self._target_binding = genuine._target_binding
            self._ordered_projection = genuine._ordered_projection
            self.inventory_receipt_sha256 = genuine.inventory_receipt_sha256
            self._sanitizer_policy_bytes = genuine._sanitizer_policy_bytes
            self._seal = genuine._seal

        def _reauthenticate_at_current_time(self, *, now: datetime) -> None:
            del now

    replacement = FakeSource()
    object.__setattr__(evidence, "_fixed_catalog_source_state", replacement)
    try:
        with pytest.raises(
            executor.SanitizerExecutorFailure,
            match="^sanitizer_executor.authority_invalid$",
        ):
            _run(evidence)
    finally:
        object.__setattr__(evidence, "_fixed_catalog_source_state", genuine)


def test_unexpected_internal_errors_are_bounded(
    real_case: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("private material must not escape")

    monkeypatch.setattr(authority, "_reauthenticate_at_current_time", unexpected)
    with pytest.raises(
        executor.SanitizerExecutorFailure,
        match="^sanitizer_executor.authority_invalid$",
    ) as caught:
        _run(real_case.authenticated)
    assert "private material" not in str(caught.value)


def test_executor_module_has_no_io_mutation_or_output_surface() -> None:
    source = SCRIPT.read_text()
    for forbidden in (
        "import psycopg",
        "import requests",
        "import socket",
        "import subprocess",
        "urllib",
        "azure.",
        "connect(",
        "execute(",
        "commit(",
        "rollback(",
        "database_url",
        "dsn",
        "staging_restore_safe_submit",
        "staging_restore_runtime_adapters",
    ):
        assert forbidden not in source.lower()
    assert "dataclass" not in source
    assert "authenticated_sanitizer_evidence" in source
    assert "restore_evidence_binding:" not in source
    assert "fixed_catalog_source_state:" not in source
    authority_source = AUTHORITY_SCRIPT.read_text()
    assert "from scripts" not in authority_source
    assert "import scripts" not in authority_source


def test_fresh_executor_import_does_not_load_adapters_or_process_clients() -> None:
    probe = f"""
import importlib.util
import json
import sys
sys.path.insert(0, {str(ROOT)!r})
spec = importlib.util.spec_from_file_location('sanitizer_executor_probe', {str(SCRIPT)!r})
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
forbidden_roots = (
    '_posixsubprocess',
    'asyncpg',
    'azure',
    'httpx',
    'psycopg',
    'requests',
    'sqlalchemy',
    'subprocess',
)
forbidden_scripts = {{
    'scripts.authenticate_staging_github_attestation',
    'scripts.bind_staging_restore_fixed_catalog_source_state',
    'scripts.bind_staging_restore_sanitizer_evidence',
    'scripts.build_staging_controller_receipt_v2',
    'scripts.compose_staging_restore_evidence_binding',
    'scripts.staging_restore_sanitizer',
    'scripts.staging_restore_runtime_adapters',
    'scripts.staging_restore_safe_submit',
    'scripts.verify_staging_source_provider_observation',
}}
loaded = sorted(
    name for name in sys.modules
    if name in forbidden_scripts
    or any(name == root or name.startswith(root + '.') for root in forbidden_roots)
)
print(json.dumps(loaded))
raise SystemExit(bool(loaded))
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-S", "-c", probe],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == []
