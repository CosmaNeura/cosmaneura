from __future__ import annotations

import ast
import hashlib
import inspect
import json
import pickle
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import staging_restore_sanitizer_data_contract_executor as executor
from scripts import staging_restore_sanitizer_executor as public_executor
from scripts import (
    staging_restore_sanitizer_fixed_catalog_post_projection as projection,
)
from scripts import staging_restore_sanitizer_source_snapshot as source_snapshot
from scripts import staging_synthetic_inventory as inventory

from tests.infrastructure import (
    test_staging_restore_sanitizer_source_snapshot as source_tests,
)
from tests.infrastructure.test_staging_restore_sanitizer_data_contract_executor import (
    KEY,
    OTHER_KEY,
    PREFLIGHT_AT,
    _clone,
    _engine,
    _snapshot_from_rows,
    _source_rows,
)
from tests.infrastructure.test_staging_restore_sanitizer_source_snapshot import (
    KEY as INVENTORY_KEY,
)

authenticated_case = source_tests.authenticated_case

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/staging_restore_sanitizer_fixed_catalog_post_projection.py"
EXECUTOR_SCRIPT = ROOT / "scripts/staging_restore_sanitizer_data_contract_executor.py"
CONTRACTS = ROOT / "contracts/staging-rehearsal/v1"
POLICY = CONTRACTS / "restore-sanitizer-policy.json"
INVENTORY_POLICY = CONTRACTS / "synthetic-data-policy.json"
DATA_CONTRACT = CONTRACTS / "restore-sanitizer-data-contract.json"
SOURCE_CONTRACT = CONTRACTS / "restore-sanitizer-source-snapshot-contract.json"
CONTRACT = CONTRACTS / "restore-sanitizer-fixed-catalog-post-projection-contract.json"
KEY_VERSION = "staging-sanitizer-transform-v1"
FAILURE = projection.SanitizerFixedCatalogPostProjectionFailure


def _authenticator(
    *,
    clock: Callable[[], datetime] | None = None,
    inventory_key: bytes = INVENTORY_KEY,
    transform_key: bytes = KEY,
    inventory_version: str = inventory.SUPPORTED_HMAC_KEY_VERSION,
    transform_version: str = KEY_VERSION,
    contract_bytes: bytes | None = None,
) -> Any:
    return projection.SanitizerFixedCatalogPostProjectionAuthenticator._from_reviewed_configuration(
        clock=clock or (lambda: PREFLIGHT_AT),
        inventory_hmac_key=inventory_key,
        inventory_hmac_key_version=inventory_version,
        transform_key=transform_key,
        transform_key_version=transform_version,
        sanitizer_policy_bytes=POLICY.read_bytes(),
        inventory_policy_bytes=INVENTORY_POLICY.read_bytes(),
        data_contract_bytes=DATA_CONTRACT.read_bytes(),
        source_snapshot_contract_bytes=SOURCE_CONTRACT.read_bytes(),
        post_projection_contract_bytes=contract_bytes or CONTRACT.read_bytes(),
    )


@pytest.fixture(scope="module")
def artifacts(authenticated_case: SimpleNamespace) -> SimpleNamespace:
    snapshot = _snapshot_from_rows(authenticated_case, _source_rows())
    candidate = _engine().transform(authenticated_source_snapshot=snapshot)
    return SimpleNamespace(
        evidence=authenticated_case.authenticated,
        snapshot=snapshot,
        candidate=candidate,
    )


def _resign(candidate: Any, **changes: Any) -> Any:
    value = _clone(candidate, **changes)
    object.__setattr__(
        value,
        "_proof_sha256",
        executor._candidate_proof(  # noqa: SLF001
            key=KEY,
            tables=value._tables,
            evidence=value._authenticated_evidence,
            source_snapshot_contract_sha256=(value._source_snapshot_contract_sha256),
            source_snapshot_proof_sha256=value._source_snapshot_proof_sha256,
            source_snapshot_identity=value._source_snapshot_identity,
            source_snapshot_type_identity=value._source_snapshot_type_identity,
            source_snapshot_seal_identity=value._source_snapshot_seal_identity,
            source_row_count=value.source_row_count,
            transformed_at=value.transformed_at,
            transform_key_version=value.transform_key_version,
        ),
    )
    return value


def _replace_cell(candidate: Any, table: str, column: str, value: Any) -> Any:
    tables = []
    for name, rows in candidate._tables:
        if name != table:
            tables.append((name, rows))
            continue
        row = rows[0]
        changed = executor._FrozenMap(  # noqa: SLF001
            tuple(
                (field, value if field == column else item)
                for field, item in row._items  # noqa: SLF001
            )
        )
        tables.append((name, (changed, *rows[1:])))
    return _resign(candidate, _tables=tuple(tables))


def _trace(error: BaseException) -> str:
    rendered, seen = [], set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        frame = current.__traceback__
        while frame is not None:
            if frame.tb_frame.f_globals.get("__name__") == projection.__name__:
                rendered.append(repr(frame.tb_frame.f_locals))
            frame = frame.tb_next
        current = current.__cause__ or current.__context__
    return "".join(rendered)


def test_authenticates_exact_fixed_catalog_projection(
    artifacts: SimpleNamespace,
) -> None:
    authority = _authenticator().authenticate(
        authenticated_source_snapshot=artifacts.snapshot,
        sanitizer_candidate=artifacts.candidate,
    )
    assert authority.fixed_catalog_table_count == 5
    assert authority.fixed_catalog_column_count == 46
    assert authority.fixed_catalog_row_count == 5
    assert authority.transform_key_version == KEY_VERSION
    assert authority.authenticated_at == PREFLIGHT_AT
    assert authority.source_snapshot_authenticated is True
    assert authority.candidate_dataset_authenticated is True
    assert authority.fixed_catalog_post_projection_authenticated is True
    assert authority.post_projection_authenticated is True
    for name in projection._NEGATIVE_CLAIMS:  # noqa: SLF001
        assert getattr(authority, name) is False
    _authenticator().validate(authority)


def test_contract_is_hash_pinned_closed_world_and_exact() -> None:
    raw = CONTRACT.read_bytes()
    document = json.loads(raw)
    assert hashlib.sha256(raw).hexdigest() == projection.POST_PROJECTION_CONTRACT_SHA256
    assert document["closed_world"] is True
    assert document["status"]["existing_executor_preflight_remains_terminal"] is True
    assert (
        document["status"]["public_activation_failure"] == projection.ACTIVATION_FAILURE
    )
    assert document["status"]["activation_blockers"] == [
        "transaction_adapter_unimplemented"
    ]
    assert document["projection"]["table_count"] == 5
    assert document["projection"]["column_count"] == 46
    assert document["projection"]["row_order"] == (
        "canonical-transformed-identifier-tuple"
    )
    assert document["authentication"]["candidate_copy_domain"] == (
        executor.CANDIDATE_COPY_DOMAIN
    )
    assert (
        document["authentication"]["projection_domain"]
        != document["authentication"]["authority_domain"]
    )
    assert document["claims"] == {
        **projection._POSITIVE_CLAIMS,  # noqa: SLF001
        **projection._NEGATIVE_CLAIMS,  # noqa: SLF001
    }


def test_source_contract_pin_rolls_forward_and_preflight_stays_terminal(
    artifacts: SimpleNamespace,
) -> None:
    source_contract = json.loads(SOURCE_CONTRACT.read_bytes())
    digest = hashlib.sha256(SOURCE_CONTRACT.read_bytes()).hexdigest()
    assert digest == source_snapshot.SOURCE_SNAPSHOT_CONTRACT_SHA256
    assert digest == executor.SOURCE_SNAPSHOT_CONTRACT_SHA256
    assert digest == projection.SOURCE_SNAPSHOT_CONTRACT_SHA256
    assert (
        source_contract["status"][
            "fixed_catalog_post_projection_authentication_implemented"
        ]
        is True
    )
    assert source_contract["status"]["activation_blockers"] == [
        "transaction_adapter_unimplemented"
    ]
    preflight = public_executor.SanitizerExecutorPreflight._from_reviewed_configuration(
        clock=lambda: PREFLIGHT_AT
    )
    with pytest.raises(
        public_executor.SanitizerExecutorFailure,
        match=projection.ACTIVATION_FAILURE,
    ):
        preflight.preflight(authenticated_sanitizer_evidence=artifacts.evidence)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda raw: raw + b"\n",
        lambda raw: raw.replace(b'"table_count":5', b'"table_count":6'),
        lambda raw: raw.replace(
            b'"projection_domain":"fixed-catalog:post-projection"',
            b'"projection_domain":"fixed-catalog:candidate-copy"',
        ),
    ],
)
def test_contract_mutations_fail_closed(mutate: Callable[[bytes], bytes]) -> None:
    with pytest.raises(FAILURE, match="fixed_catalog_post_projection.policy_invalid"):
        _authenticator(contract_bytes=mutate(CONTRACT.read_bytes()))


@pytest.mark.parametrize(
    ("inventory_key", "transform_key"),
    [
        (OTHER_KEY, KEY),
        (INVENTORY_KEY, OTHER_KEY),
        (b"short", KEY),
        (INVENTORY_KEY, b"short"),
    ],
)
def test_wrong_or_short_keys_refuse(
    artifacts: SimpleNamespace, inventory_key: bytes, transform_key: bytes
) -> None:
    if len(inventory_key) < 32 or len(transform_key) < 32:
        with pytest.raises(FAILURE, match="configuration_invalid"):
            _authenticator(inventory_key=inventory_key, transform_key=transform_key)
        return
    with pytest.raises(FAILURE):
        _authenticator(
            inventory_key=inventory_key, transform_key=transform_key
        ).authenticate(
            authenticated_source_snapshot=artifacts.snapshot,
            sanitizer_candidate=artifacts.candidate,
        )


def test_equal_inventory_and_transform_key_material_is_domain_separated(
    artifacts: SimpleNamespace,
) -> None:
    candidate = _engine(INVENTORY_KEY).transform(
        authenticated_source_snapshot=artifacts.snapshot
    )
    authority = _authenticator(transform_key=INVENTORY_KEY).authenticate(
        authenticated_source_snapshot=artifacts.snapshot,
        sanitizer_candidate=candidate,
    )
    assert authority.post_projection_authenticated is True
    material = b"same"
    digests = {
        executor._hmac_digest(  # noqa: SLF001
            INVENTORY_KEY, executor.CANDIDATE_COPY_DOMAIN, material
        ).hex(),
        projection._digest(  # noqa: SLF001
            INVENTORY_KEY, projection.PROJECTION_DOMAIN, material
        ),
        projection._digest(  # noqa: SLF001
            INVENTORY_KEY, projection.AUTHORITY_DOMAIN, material
        ),
    }
    assert len(digests) == 3


@pytest.mark.parametrize(
    ("inventory_version", "transform_version"),
    [
        ("wrong", KEY_VERSION),
        (inventory.SUPPORTED_HMAC_KEY_VERSION, "INVALID"),
    ],
)
def test_wrong_key_versions_refuse(
    inventory_version: str, transform_version: str
) -> None:
    with pytest.raises(FAILURE, match="configuration_invalid"):
        _authenticator(
            inventory_version=inventory_version,
            transform_version=transform_version,
        )


@pytest.mark.parametrize(
    ("table", "column", "value"),
    [
        ("baa_version", "doc_id", "substituted"),
        ("drug_reference", "sections", executor._FrozenMap((("x", True),))),  # noqa: SLF001
        (
            "note_type_customization",
            "doctor_note_schema_id",
            uuid.UUID("01890f4c-7b00-7000-8000-000000000099"),
        ),
    ],
)
def test_resigned_fixed_catalog_substitution_refuses(
    artifacts: SimpleNamespace, table: str, column: str, value: Any
) -> None:
    candidate = _replace_cell(artifacts.candidate, table, column, value)
    _engine().validate_candidate(candidate)
    with pytest.raises(FAILURE, match="projection_invalid"):
        _authenticator().authenticate(
            authenticated_source_snapshot=artifacts.snapshot,
            sanitizer_candidate=candidate,
        )


def test_resigned_missing_fixed_catalog_row_refuses(artifacts: SimpleNamespace) -> None:
    tables = tuple(
        (name, () if name == "baa_version" else rows)
        for name, rows in artifacts.candidate._tables
    )
    candidate = _resign(
        artifacts.candidate,
        _tables=tables,
        candidate_row_count=artifacts.candidate.candidate_row_count - 1,
    )
    _engine().validate_candidate(candidate)
    with pytest.raises(FAILURE, match="projection_invalid"):
        _authenticator().authenticate(
            authenticated_source_snapshot=artifacts.snapshot,
            sanitizer_candidate=candidate,
        )


def test_candidate_from_another_exact_snapshot_refuses(
    authenticated_case: SimpleNamespace,
) -> None:
    first = _snapshot_from_rows(authenticated_case, _source_rows())
    second = _snapshot_from_rows(authenticated_case, _source_rows())
    candidate = _engine().transform(authenticated_source_snapshot=first)
    with pytest.raises(FAILURE, match="authority_invalid"):
        _authenticator().authenticate(
            authenticated_source_snapshot=second,
            sanitizer_candidate=candidate,
        )


def test_executor_owned_copy_is_fixed_only_keyed_and_tamper_evident(
    artifacts: SimpleNamespace,
) -> None:
    engine = _engine()
    copied = engine._copy_verified_fixed_catalog_for_post_projection(  # noqa: SLF001
        artifacts.candidate, now=PREFLIGHT_AT
    )
    assert type(copied) is executor._VerifiedFixedCatalogCandidateCopy  # noqa: SLF001
    assert tuple(name for name, _rows in copied._tables) == tuple(  # noqa: SLF001
        sorted(executor.FIXED_CATALOG_TABLES)
    )
    assert copied._candidate_identity == id(artifacts.candidate)  # noqa: SLF001
    assert copied._candidate_type_identity == id(executor.SanitizerCandidateDataset)  # noqa: SLF001
    assert copied._candidate_seal_identity == id(executor._CANDIDATE_SEAL)  # noqa: SLF001
    with pytest.raises(
        executor.SanitizerDataContractExecutorFailure, match="output_invalid"
    ):
        pickle.dumps(copied)


def test_executor_owned_copy_tampering_refuses(artifacts: SimpleNamespace) -> None:
    engine = _engine()
    copied = engine._copy_verified_fixed_catalog_for_post_projection(  # noqa: SLF001
        artifacts.candidate, now=PREFLIGHT_AT
    )
    forged = _clone(copied, _proof_sha256="0" * 64)
    with pytest.raises(
        executor.SanitizerDataContractExecutorFailure, match="candidate_invalid"
    ):
        engine._validate_verified_fixed_catalog_copy_at_current_time(  # noqa: SLF001
            forged, now=PREFLIGHT_AT
        )


@pytest.mark.parametrize("kind", ["source", "candidate"])
def test_valid_private_copy_substitution_refuses(
    authenticated_case: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    first = _snapshot_from_rows(authenticated_case, _source_rows())
    second = _snapshot_from_rows(authenticated_case, _source_rows())
    candidate = _engine().transform(authenticated_source_snapshot=first)
    other = _engine().transform(authenticated_source_snapshot=second)
    if kind == "source":
        copied = source_snapshot._copy_verified_tables_for_executor(  # noqa: SLF001
            second, now=PREFLIGHT_AT, inventory_hmac_key=INVENTORY_KEY
        )
        monkeypatch.setattr(
            source_snapshot,
            "_copy_verified_tables_for_executor",
            lambda *_args, **_kwargs: copied,
        )
    else:
        copied = _engine()._copy_verified_fixed_catalog_for_post_projection(  # noqa: SLF001
            other, now=PREFLIGHT_AT
        )
        monkeypatch.setattr(
            executor.SanitizerDataContractExecutor,
            "_copy_verified_fixed_catalog_for_post_projection",
            lambda *_args, **_kwargs: copied,
        )
    with pytest.raises(FAILURE, match="authority_invalid"):
        _authenticator().authenticate(
            authenticated_source_snapshot=first,
            sanitizer_candidate=candidate,
        )


def test_hostile_copy_fields_are_rejected_without_callbacks(
    artifacts: SimpleNamespace,
) -> None:
    callbacks: list[str] = []

    class HostileTuple(tuple[Any, ...]):
        def __iter__(self) -> Any:
            callbacks.append("iter")
            raise AssertionError

    class HostileStr(str):
        def __ne__(self, other: object) -> bool:
            callbacks.append("compared")
            return super().__ne__(other)

    engine = _engine()
    copied = engine._copy_verified_fixed_catalog_for_post_projection(  # noqa: SLF001
        artifacts.candidate, now=PREFLIGHT_AT
    )
    for forged in (
        _clone(copied, _tables=(HostileTuple(),)),
        _clone(copied, _transform_key_version=HostileStr(KEY_VERSION)),
    ):
        with pytest.raises(
            executor.SanitizerDataContractExecutorFailure,
            match="candidate_invalid",
        ):
            engine._validate_verified_fixed_catalog_copy_at_current_time(  # noqa: SLF001
                forged, now=PREFLIGHT_AT
            )
    assert callbacks == []


@pytest.mark.parametrize("target", ["source", "candidate"])
def test_clock_callback_input_mutation_is_detected(
    authenticated_case: SimpleNamespace, target: str
) -> None:
    snapshot = _snapshot_from_rows(authenticated_case, _source_rows())
    candidate = _engine().transform(authenticated_source_snapshot=snapshot)
    calls = 0

    def clock() -> datetime:
        nonlocal calls
        calls += 1
        if calls == 2:
            object.__setattr__(
                snapshot if target == "source" else candidate,
                "_proof_sha256",
                "0" * 64,
            )
        return PREFLIGHT_AT

    with pytest.raises(FAILURE):
        _authenticator(clock=clock).authenticate(
            authenticated_source_snapshot=snapshot,
            sanitizer_candidate=candidate,
        )
    assert calls == 2


def test_advancing_regressing_and_expired_clocks(artifacts: SimpleNamespace) -> None:
    times = iter((PREFLIGHT_AT, PREFLIGHT_AT + timedelta(seconds=1)))
    authority = _authenticator(clock=lambda: next(times)).authenticate(
        authenticated_source_snapshot=artifacts.snapshot,
        sanitizer_candidate=artifacts.candidate,
    )
    assert authority.authenticated_at == PREFLIGHT_AT + timedelta(seconds=1)
    backwards = iter((PREFLIGHT_AT, PREFLIGHT_AT - timedelta(seconds=1)))
    with pytest.raises(FAILURE, match="clock_regressed"):
        _authenticator(clock=lambda: next(backwards)).authenticate(
            authenticated_source_snapshot=artifacts.snapshot,
            sanitizer_candidate=artifacts.candidate,
        )
    with pytest.raises(FAILURE, match="target_expired"):
        _authenticator(clock=lambda: PREFLIGHT_AT + timedelta(days=1)).authenticate(
            authenticated_source_snapshot=artifacts.snapshot,
            sanitizer_candidate=artifacts.candidate,
        )


def test_exact_inputs_reject_mapping_subclasses_without_callbacks(
    artifacts: SimpleNamespace,
) -> None:
    callbacks: list[str] = []

    class Hostile(Mapping[str, Any]):
        def __getitem__(self, key: str) -> Any:
            callbacks.append(key)
            raise AssertionError

        def __iter__(self) -> Any:
            callbacks.append("iter")
            raise AssertionError

        def __len__(self) -> int:
            callbacks.append("len")
            raise AssertionError

    with pytest.raises(FAILURE, match="authority_invalid"):
        _authenticator().authenticate(
            authenticated_source_snapshot=Hostile(),  # type: ignore[arg-type]
            sanitizer_candidate=artifacts.candidate,
        )
    with pytest.raises(FAILURE, match="authority_invalid"):
        _authenticator().authenticate(
            authenticated_source_snapshot=artifacts.snapshot,
            sanitizer_candidate=Hostile(),  # type: ignore[arg-type]
        )
    assert callbacks == []


def test_output_is_sealed_nonserializable_and_retains_no_rows_or_inputs(
    artifacts: SimpleNamespace,
) -> None:
    authority = _authenticator().authenticate(
        authenticated_source_snapshot=artifacts.snapshot,
        sanitizer_candidate=artifacts.candidate,
    )
    assert "non-authoritative" in repr(authority)
    assert projection.AuthenticatedFixedCatalogPostProjection.__slots__
    assert not hasattr(authority, "__dict__")
    forbidden = {
        "_tables",
        "_rows",
        "_source_snapshot",
        "_candidate_dataset",
        "_inventory_hmac_key",
        "_transform_key",
        "_raw_fragment_proofs",
    }
    assert forbidden.isdisjoint(authority.__dataclass_fields__)
    with pytest.raises(FAILURE, match="output_invalid"):
        projection.AuthenticatedFixedCatalogPostProjection()
    with pytest.raises(FAILURE, match="output_invalid"):
        pickle.dumps(authority)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("fixed_catalog_table_count", True),
        ("fixed_catalog_row_count", -1),
        ("post_projection_authenticated", False),
        ("_projection_proof_sha256", "0" * 64),
        ("_candidate_dataset_identity", 0),
    ],
)
def test_output_tampering_refuses(
    artifacts: SimpleNamespace, field: str, value: Any
) -> None:
    authority = _authenticator().authenticate(
        authenticated_source_snapshot=artifacts.snapshot,
        sanitizer_candidate=artifacts.candidate,
    )
    with pytest.raises(FAILURE, match="output_invalid"):
        _authenticator().validate(_clone(authority, **{field: value}))


def test_public_failure_traceback_drops_inputs_and_keys(
    artifacts: SimpleNamespace,
) -> None:
    marker = b"projection-secret-marker-that-is-32-bytes"
    candidate = _replace_cell(artifacts.candidate, "baa_version", "doc_id", "bad")
    try:
        _authenticator(transform_key=marker).authenticate(
            authenticated_source_snapshot=artifacts.snapshot,
            sanitizer_candidate=candidate,
        )
    except FAILURE as error:
        trace = _trace(error)
        assert repr(marker) not in trace
        assert repr(candidate) not in trace
        assert repr(artifacts.snapshot) not in trace
    else:  # pragma: no cover
        raise AssertionError("expected bounded refusal")


def test_module_is_adapter_free_and_candidate_internals_are_producer_owned() -> None:
    tree = ast.parse(SCRIPT.read_text())
    imports = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imports.isdisjoint(
        {"os", "pathlib", "socket", "subprocess", "requests", "sqlalchemy", "azure"}
    )
    source = SCRIPT.read_text()
    assert "sanitizer_candidate._tables" not in source
    assert not any(
        isinstance(node, ast.Attribute)
        and node.attr == "_tables"
        and isinstance(node.value, ast.Name)
        and node.value.id in {"candidate", "sanitizer_candidate"}
        for node in ast.walk(tree)
    )
    assert (
        "_copy_verified_fixed_catalog_for_post_projection"
        in EXECUTOR_SCRIPT.read_text()
    )
    assert set(
        inspect.signature(
            projection.SanitizerFixedCatalogPostProjectionAuthenticator.authenticate
        ).parameters
    ) == {
        "self",
        "authenticated_source_snapshot",
        "sanitizer_candidate",
    }
