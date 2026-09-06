from __future__ import annotations

import copy
import hashlib
import importlib.util
import inspect
import json
import pickle
import sys
import urllib.parse
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from typeid import TypeID

from scripts import read_staging_controller_receipt_v2 as private_reader
from scripts import validate_staging_controller_receipt_v2 as receipt_content
from scripts import write_staging_controller_receipt_v2 as receipt_writer

from tests.infrastructure import (
    test_staging_controller_receipt_v2_builder as builder_helpers,
)
from tests.infrastructure import (
    test_staging_controller_receipt_v2_writer as writer_helpers,
)

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/staging_restore_sanitizer.py"
POLICY = ROOT / "contracts/staging-rehearsal/v1/restore-sanitizer-policy.json"
DATA_CONTRACT = (
    ROOT / "contracts/staging-rehearsal/v1/restore-sanitizer-data-contract.json"
)
INVENTORY_POLICY = ROOT / "contracts/staging-rehearsal/v1/synthetic-data-policy.json"
KEY = b"sanitizer-test-key-that-is-at-least-32-bytes"
OTHER_KEY = b"different-test-key-that-is-at-least-32-bytes"
SUBSCRIPTION = "11111111-1111-4111-8111-111111111111"
REHEARSAL = "012345abcdef"
EXPIRY = "1788458400"
BASE = f"cnr-{REHEARSAL}-e{EXPIRY}"
SERVER = f"{BASE}-pg"
GROUP = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/cosmaneura-staging-rehearsal"


def _load_module() -> ModuleType:
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location(
        "standalone_staging_restore_sanitizer", SCRIPT
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def module() -> ModuleType:
    return _load_module()


@pytest.fixture(scope="module")
def written_chain() -> Iterator[
    tuple[Any, Any, receipt_writer.WrittenControllerReceiptV2]
]:
    monkeypatch = pytest.MonkeyPatch()
    try:
        authority, built = builder_helpers._build(monkeypatch)
        instance, _identity, _blob = writer_helpers._instance(monkeypatch, built)
        yield authority, built, instance.write(built_receipt=built)
    finally:
        monkeypatch.undo()


@pytest.fixture(scope="module")
def target_binding(
    module: ModuleType,
    written_chain: tuple[Any, Any, receipt_writer.WrittenControllerReceiptV2],
) -> Any:
    _authority, _built, written = written_chain
    binder = module.SanitizerTargetBinder._from_reviewed_configuration(
        clock=lambda: builder_helpers.NOW
    )
    return binder.bind(written_receipt=written)


def _policies() -> tuple[dict[str, Any], dict[str, Any]]:
    return json.loads(POLICY.read_bytes()), json.loads(INVENTORY_POLICY.read_bytes())


def _data_contract() -> dict[str, Any]:
    return json.loads(DATA_CONTRACT.read_bytes())


def _receipt() -> dict[str, Any]:
    vnet = f"{GROUP}/providers/Microsoft.Network/virtualNetworks/{BASE}-vnet"
    pe = f"{GROUP}/providers/Microsoft.Network/privateEndpoints/{BASE}-pep"
    zone = (
        f"{GROUP}/providers/Microsoft.Network/privateDnsZones/"
        "privatelink.postgres.database.azure.com"
    )
    return {
        "schema_version": 1,
        "policy_id": "cosmaneura-staging-restore-envelope-v1",
        "policy_sha256": "sha256:6a80a9681bf2527b7c69f48c896baa01a7b5face3ddb415c1300632dab020d92",
        "repository": "CosmaNeura/cosmaneura",
        "workflow_sha": "a" * 40,
        "run_id": "901",
        "run_attempt": "1",
        "rehearsal_id": REHEARSAL,
        "source_inventory": {
            "artifact_sha256": "sha256:" + "b" * 64,
            "quiescence_sha256": "sha256:" + "c" * 64,
            "profile": "pre",
            "revision": "89c31f0ae275",
            "source_sha": "d" * 40,
            "candidate_sha": "e" * 40,
        },
        "source_postgres": {
            "resource_id": (
                f"/subscriptions/{SUBSCRIPTION}/resourceGroups/cosmaneura-staging/"
                "providers/Microsoft.DBforPostgreSQL/flexibleServers/"
                "cosmaneura-staging-postgres"
            ),
            "fqdn": "cosmaneura-staging-postgres.postgres.database.azure.com",
            "database_name": "staging",
        },
        "target": {
            "postgres_resource_id": (
                f"{GROUP}/providers/Microsoft.DBforPostgreSQL/flexibleServers/{SERVER}"
            ),
            "database_name": "staging",
            "vnet_id": vnet,
            "subnet_id": f"{vnet}/subnets/private-endpoints",
            "private_endpoint_id": pe,
            "network_interface_id": (
                f"{GROUP}/providers/Microsoft.Network/networkInterfaces/{BASE}-nic"
            ),
            "private_dns_zone_id": zone,
            "private_dns_link_id": f"{zone}/virtualNetworkLinks/{BASE}-dnslink",
            "private_dns_zone_group_id": f"{pe}/privateDnsZoneGroups/default",
        },
        "provisioner": {
            "client_id": "22222222-2222-4222-8222-222222222222",
            "principal_id": "33333333-3333-4333-8333-333333333333",
        },
        "created_at": "2026-09-03T12:00:00Z",
        "expires_at": "2026-09-03T18:00:00Z",
        "cleanup_not_after": "2026-09-03T18:00:00Z",
        "isolation": {
            "public_access": "Disabled",
            "firewall_rule_count": 0,
            "vnet_peering_count": 0,
            "public_ip_count": 0,
            "unknown_resource_count": 0,
            "blob_copied": False,
            "redis_copied": False,
        },
        "pass": True,
    }


def test_canonical_policy_is_pinned_closed_world_and_dormant(
    module: ModuleType,
) -> None:
    policy, inventory_policy = _policies()
    assert hashlib.sha256(POLICY.read_bytes()).hexdigest() == (
        module.CANONICAL_POLICY_SHA256
    )
    assert hashlib.sha256(INVENTORY_POLICY.read_bytes()).hexdigest() == (
        module.inventory.CANONICAL_POLICY_SHA256
    )
    assert hashlib.sha256(DATA_CONTRACT.read_bytes()).hexdigest() == (
        module.CANONICAL_DATA_CONTRACT_SHA256
    )
    data_contract = _data_contract()
    module.validate_policy(policy, inventory_policy, data_contract)
    assert data_contract["retained_table_count"] == 29
    assert data_contract["retained_column_count"] == 343
    assert policy["status"]["controller_receipt_v2_target_binding_implemented"] is True
    assert policy["status"]["fixed_catalog_source_state_binding_implemented"] is True
    assert policy["status"]["executable"] is False
    assert policy["status"]["trusted_evidence_binding_implemented"] is False
    assert policy["status"]["live_provider_calls_enabled"] is False
    assert policy["status"]["workflow_integrated"] is False
    assert policy["execution_contract"]["database_connections_permitted"] is False
    assert policy["execution_contract"]["sql_mutation_permitted"] is False
    receipt_binding = policy["controller_receipt_v2_binding"]
    assert receipt_binding["input"]["only_public_data_input"] == "written_receipt"
    assert (
        receipt_binding["input"][
            "caller_bytes_maps_paths_urls_dsns_hostnames_roles_digests_policies_statuses_or_times_permitted"
        ]
        is False
    )
    assert receipt_binding["output"]["non_serializable"] is True
    source_state = policy["fixed_catalog_source_state_binding"]
    assert source_state["implemented"] is True
    assert source_state["binding"]["fixed_catalog_tables"] == list(
        module.FIXED_CATALOG_TABLE_ORDER
    )
    assert source_state["claims"] == module.FIXED_CATALOG_SOURCE_STATE_CLAIMS
    transform = policy["transform_contract"]
    assert transform["retained_column_policy_status"] == (
        "complete_sha256_pinned_343_columns"
    )
    assert transform["data_contract_path"] == str(DATA_CONTRACT.relative_to(ROOT))
    assert (
        transform["data_contract_sha256"]
        == hashlib.sha256(DATA_CONTRACT.read_bytes()).hexdigest()
    )
    assert set(policy["status"]["activation_blockers"]) == module.ACTIVATION_BLOCKERS
    assert "complete_retained_column_transform_policy" not in module.ACTIVATION_BLOCKERS
    assert transform["dependent_digest_recipes"] == {}
    assert transform["semantic_domains"]["typed_text_references"] == {
        "mode": "derive_typeid_text_from_transformed_uuid",
        "references": {
            "authority_person_binding.binding_ref": {
                "source": "appointment.id",
                "typeid_prefix": "appointment",
            }
        },
        "implementation_status": "data_contract_complete_execution_missing",
    }
    assert (
        set(
            policy["transform_contract"]["fixed_catalog"][
                "requires_authenticated_source_state_hmacs"
            ]
        )
        == module.FIXED_CATALOG_TABLES
    )
    source = SCRIPT.read_text()
    assert "import psycopg" not in source
    assert "def rewrite_database" not in source
    assert not hasattr(module, "validate_controller_receipt_shape")


@pytest.mark.parametrize("change", ["missing", "extra"])
def test_data_contract_requires_exact_column_coverage(
    module: ModuleType, change: str
) -> None:
    policy, inventory_policy = _policies()
    contract = _data_contract()
    if change == "missing":
        del contract["tables"]["doctor"]["columns"]["email"]
    else:
        contract["tables"]["doctor"]["columns"]["unexpected"] = "preserve_approved"
    with pytest.raises(module.SanitizerFailure, match="data_contract.column_coverage"):
        module.validate_data_contract(contract, inventory_policy, policy)


def test_data_contract_partitions_rewritten_and_discarded_rows(
    module: ModuleType,
) -> None:
    policy, inventory_policy = _policies()
    contract = _data_contract()
    assert set(contract["row_actions"]["discard_rows"]) == module.GUARDED_TABLES
    for table_name in module.GUARDED_TABLES:
        assert set(contract["tables"][table_name]["columns"].values()) == {
            "discard_row"
        }

    changed = copy.deepcopy(contract)
    changed["tables"]["baa_acceptance"]["columns"]["id"] = (
        "hmac_uuid7:typeid:baa_acceptance"
    )
    with pytest.raises(module.SanitizerFailure, match="data_contract.discard_action"):
        module.validate_data_contract(changed, inventory_policy, policy)


def test_data_contract_enforces_fk_domains_and_exact_graph(module: ModuleType) -> None:
    policy, inventory_policy = _policies()
    contract = _data_contract()
    assert len(contract["foreign_keys"]) == 46
    changed = copy.deepcopy(contract)
    changed["tables"]["appointment"]["columns"]["doctor_id"] = (
        "hmac_uuid7:typeid:patient"
    )
    with pytest.raises(
        module.SanitizerFailure, match="data_contract.foreign_key_action"
    ):
        module.validate_data_contract(changed, inventory_policy, policy)

    changed = copy.deepcopy(contract)
    changed["foreign_keys"] = [
        binding
        for binding in changed["foreign_keys"]
        if binding["source"] != ["authority_person_origin.person_id"]
    ]
    with pytest.raises(module.SanitizerFailure, match="data_contract.foreign_keys"):
        module.validate_data_contract(changed, inventory_policy, policy)


def test_data_contract_enforces_sensitive_and_nullable_actions(
    module: ModuleType,
) -> None:
    policy, inventory_policy = _policies()
    contract = _data_contract()
    changed = copy.deepcopy(contract)
    changed["tables"]["doctor"]["columns"]["email"] = "preserve_approved"
    with pytest.raises(module.SanitizerFailure, match="data_contract.semantic_domains"):
        module.validate_data_contract(changed, inventory_policy, policy)

    changed = copy.deepcopy(contract)
    changed["tables"]["doctor"]["columns"]["role"] = "set_null"
    with pytest.raises(module.SanitizerFailure, match="data_contract.nullability"):
        module.validate_data_contract(changed, inventory_policy, policy)


def test_data_contract_enforces_fixed_catalog_and_derived_recipes(
    module: ModuleType,
) -> None:
    policy, inventory_policy = _policies()
    contract = _data_contract()
    changed = copy.deepcopy(contract)
    changed["tables"]["doctor_note_schema"]["columns"]["fields"] = "preserve_approved"
    with pytest.raises(module.SanitizerFailure, match="data_contract.fixed_catalog"):
        module.validate_data_contract(changed, inventory_policy, policy)

    changed = copy.deepcopy(contract)
    changed["derived_values"]["practice.join_code"]["length"] = 8
    with pytest.raises(module.SanitizerFailure, match="data_contract.derived_values"):
        module.validate_data_contract(changed, inventory_policy, policy)


def test_policy_rejects_unknown_fields_and_activation(module: ModuleType) -> None:
    policy, inventory_policy = _policies()
    changed = copy.deepcopy(policy)
    changed["unexpected"] = True
    with pytest.raises(module.SanitizerFailure, match="policy.closed_world"):
        module.validate_policy(changed, inventory_policy)
    changed = copy.deepcopy(policy)
    changed["status"]["executable"] = True
    with pytest.raises(module.SanitizerFailure, match="policy.status"):
        module.validate_policy(changed, inventory_policy)
    changed = copy.deepcopy(policy)
    changed["status"]["activation_blockers"][4] = (
        "complete_retained_column_transform_policy"
    )
    with pytest.raises(module.SanitizerFailure, match="policy.status"):
        module.validate_policy(changed, inventory_policy)
    changed = copy.deepcopy(policy)
    changed["transform_contract"]["data_contract_sha256"] = "0" * 64
    with pytest.raises(module.SanitizerFailure, match="policy.transform"):
        module.validate_policy(changed, inventory_policy)
    changed = copy.deepcopy(policy)
    changed["target"]["unexpected"] = True
    with pytest.raises(module.SanitizerFailure, match="policy.target"):
        module.validate_policy(changed, inventory_policy)
    for path, value in (
        (("input", "only_public_data_input"), "receipt_bytes"),
        (
            ("upstream_policies", "controller_receipt_v2_writer", "sha256"),
            "sha256:" + "0" * 64,
        ),
        (("claims", "database_mutation_authority"), True),
    ):
        changed = copy.deepcopy(policy)
        target: dict[str, Any] = changed["controller_receipt_v2_binding"]
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        with pytest.raises(
            module.SanitizerFailure, match="policy.controller_receipt_v2_binding"
        ):
            module.validate_policy(changed, inventory_policy)


def test_preflight_refuses_before_connector(
    module: ModuleType, target_binding: Any
) -> None:
    called = False

    def connector() -> None:
        nonlocal called
        called = True

    with pytest.raises(
        module.SanitizerFailure, match="activation.trusted_binding_unimplemented"
    ):
        module.preflight(target_binding, connector)
    assert called is False


def test_four_migration_files_are_exactly_pinned(module: ModuleType) -> None:
    policy, _ = _policies()
    module.validate_migration_files(policy, ROOT)
    expected_baa = "b1f2db1d21bb4587b92f84f6ad846377d58db046ee7e76291e1aad213bf5a715"
    path = "migrations/versions/2026_08_29_9a2399212488_publish_baa_v2_3.py"
    assert policy["migration_contract"]["files"][path] == expected_baa
    changed = copy.deepcopy(policy)
    changed["migration_contract"]["files"][path] = "0" * 64
    with pytest.raises(module.SanitizerFailure, match="migration.contract"):
        module.validate_migration_files(changed, ROOT)


def test_real_builder_writer_chain_mints_narrow_nonserializable_binding(
    module: ModuleType,
    written_chain: tuple[Any, Any, receipt_writer.WrittenControllerReceiptV2],
    target_binding: Any,
) -> None:
    _authority, built, written = written_chain
    receipt = json.loads(built.receipt_bytes)
    binding = target_binding
    assert binding._is_bound()
    assert binding.rehearsal_id == receipt["authorization"]["rehearsal_id"]
    assert binding.target_fqdn == receipt["target"]["target_fqdn"]
    assert binding.database_name == receipt["source"]["source_database_name"]
    assert binding.approved_database_role == receipt["target"]["approved_database_role"]
    assert (
        binding.target_postgres_server_id
        == receipt["target"]["target_postgres_server_id"]
    )
    assert binding.approved_restore_time == receipt_content._utc(
        receipt["source"]["approved_restore_time"]
    )
    for name in (
        "source_sha",
        "candidate_sha",
        "workflow_sha",
        "run_id",
        "run_attempt",
    ):
        assert getattr(binding, name) == receipt["source"][name]
    assert binding.receipt_sha256 == written.receipt_sha256 == built.receipt_sha256
    assert binding.expires_at == built.expires_at
    assert binding.bound_at == builder_helpers.NOW
    assert repr(binding).startswith("<")
    with pytest.raises(TypeError):
        json.dumps(binding)
    with pytest.raises(module.SanitizerFailure, match="output_invalid"):
        pickle.dumps(binding)
    for name, expected in module.BINDING_CLAIMS.items():
        assert getattr(binding, name) is expected


def test_binding_api_accepts_only_written_receipt_and_no_overrides(
    module: ModuleType,
    written_chain: tuple[Any, Any, receipt_writer.WrittenControllerReceiptV2],
) -> None:
    _authority, built, _written = written_chain
    parameters = inspect.signature(module.SanitizerTargetBinder.bind).parameters
    assert tuple(parameters) == ("self", "written_receipt")
    assert parameters["written_receipt"].kind is inspect.Parameter.KEYWORD_ONLY
    binder = module.SanitizerTargetBinder._from_reviewed_configuration(
        clock=lambda: builder_helpers.NOW
    )
    controller = receipt_content._parse_canonical(
        built._isolation_inventory._database_observation._production_authority._controller_policy,
        receipt_content.MAXIMUM_POLICY_BYTES,
    )
    content_only = receipt_content.validate_controller_receipt_v2_content(
        controller,
        intent_bytes=built.intent_bytes,
        submission_bytes=built.submission_bytes,
        receipt_bytes=built.receipt_bytes,
        clock=lambda: builder_helpers.NOW,
    )

    class Lookalike:
        def _is_authenticated(self) -> bool:
            return True

    class WrittenSubclass(receipt_writer.WrittenControllerReceiptV2):
        pass

    rejected = (
        _receipt(),
        built.receipt_bytes,
        json.loads(built.receipt_bytes),
        content_only,
        object.__new__(
            private_reader.AuthenticatedControllerReceiptV2RecordChainSource
        ),
        built,
        Lookalike(),
        object.__new__(WrittenSubclass),
        object.__new__(receipt_writer.WrittenControllerReceiptV2),
    )
    for value in rejected:
        with pytest.raises(
            module.SanitizerFailure, match="^sanitizer_binding.authority_invalid$"
        ) as error:
            binder.bind(written_receipt=value)
        assert error.value.__cause__ is None
        assert error.value.__context__ is None
    with pytest.raises(
        module.SanitizerFailure, match="sanitizer_binding.configuration_invalid"
    ):
        module.SanitizerTargetBinder()
    with pytest.raises(
        module.SanitizerFailure, match="sanitizer_binding.output_invalid"
    ):
        module.SanitizerTargetBinding()
    with pytest.raises(
        module.SanitizerFailure, match="activation.trusted_binding_unimplemented"
    ):
        module.build_sanitizer_target_binder()


def test_tampered_writer_builder_ancestor_and_binding_refuse(
    module: ModuleType,
    written_chain: tuple[Any, Any, receipt_writer.WrittenControllerReceiptV2],
    target_binding: Any,
) -> None:
    authority, built, written = written_chain
    binder = module.SanitizerTargetBinder._from_reviewed_configuration(
        clock=lambda: builder_helpers.NOW
    )
    original_writer_digest = written.receipt_sha256
    try:
        object.__setattr__(written, "receipt_sha256", "sha256:" + "0" * 64)
        with pytest.raises(module.SanitizerFailure, match="authority_invalid"):
            binder.bind(written_receipt=written)
        assert target_binding._is_bound() is False
    finally:
        object.__setattr__(written, "receipt_sha256", original_writer_digest)

    original_digest = built.receipt_sha256
    try:
        object.__setattr__(built, "receipt_sha256", "sha256:" + "0" * 64)
        with pytest.raises(module.SanitizerFailure, match="authority_invalid"):
            binder.bind(written_receipt=written)
    finally:
        object.__setattr__(built, "receipt_sha256", original_digest)

    original_rehearsal = authority.rehearsal_id
    try:
        object.__setattr__(authority, "rehearsal_id", "fedcba987654")
        with pytest.raises(module.SanitizerFailure, match="authority_invalid"):
            binder.bind(written_receipt=written)
        assert target_binding._is_bound() is False
    finally:
        object.__setattr__(authority, "rehearsal_id", original_rehearsal)

    original_fqdn = target_binding.target_fqdn
    try:
        object.__setattr__(target_binding, "target_fqdn", "staging.invalid")
        assert target_binding._is_bound() is False
    finally:
        object.__setattr__(target_binding, "target_fqdn", original_fqdn)
    assert target_binding._is_bound()


@pytest.mark.parametrize(
    "clock",
    (
        lambda: datetime(2026, 9, 6, 0, 0),
        lambda: builder_helpers.NOW + timedelta(microseconds=1),
    ),
    ids=("naive", "subsecond"),
)
def test_binding_requires_whole_second_utc(
    module: ModuleType,
    written_chain: tuple[Any, Any, receipt_writer.WrittenControllerReceiptV2],
    clock: Any,
) -> None:
    _authority, _built, written = written_chain
    binder = module.SanitizerTargetBinder._from_reviewed_configuration(clock=clock)
    with pytest.raises(module.SanitizerFailure, match="clock_invalid"):
        binder.bind(written_receipt=written)


def test_binding_rejects_expiry_staleness_and_clock_regression(
    module: ModuleType,
    written_chain: tuple[Any, Any, receipt_writer.WrittenControllerReceiptV2],
) -> None:
    _authority, built, written = written_chain
    expired = module.SanitizerTargetBinder._from_reviewed_configuration(
        clock=lambda: built.expires_at
    )
    with pytest.raises(module.SanitizerFailure, match="expired"):
        expired.bind(written_receipt=written)

    stale = module.SanitizerTargetBinder._from_reviewed_configuration(
        clock=lambda: builder_helpers.NOW + timedelta(seconds=61)
    )
    with pytest.raises(module.SanitizerFailure, match="expired"):
        stale.bind(written_receipt=written)

    values = iter((builder_helpers.NOW, builder_helpers.NOW - timedelta(seconds=1)))
    regressed = module.SanitizerTargetBinder._from_reviewed_configuration(
        clock=lambda: next(values)
    )
    with pytest.raises(module.SanitizerFailure, match="clock_regressed"):
        regressed.bind(written_receipt=written)


def test_binding_failures_are_bounded_and_hide_exception_content(
    module: ModuleType,
    written_chain: tuple[Any, Any, receipt_writer.WrittenControllerReceiptV2],
) -> None:
    _authority, _built, written = written_chain
    secret = "postgresql://role:secret@host/database"
    binder = module.SanitizerTargetBinder._from_reviewed_configuration(
        clock=lambda: (_ for _ in ()).throw(RuntimeError(secret))
    )
    with pytest.raises(module.SanitizerFailure, match="clock_invalid") as error:
        binder.bind(written_receipt=written)
    assert secret not in repr(error.value)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None


def test_target_url_requires_exact_sealed_binding_and_verify_full(
    module: ModuleType, target_binding: Any
) -> None:
    policy, _ = _policies()
    root = urllib.parse.quote("/tmp/test-root.pem", safe="")
    valid = (
        f"postgresql://{target_binding.approved_database_role}:secret@"
        f"{target_binding.target_fqdn}:5432/staging"
        f"?sslmode=verify-full&sslrootcert={root}"
        f"&application_name={module.APP_NAME}&connect_timeout=10"
    )
    module.validate_target_url(valid, target_binding)
    for invalid in (
        valid.replace(
            target_binding.target_fqdn, policy["target"]["rejected_hosts"][1]
        ),
        valid.replace("verify-full", "require"),
        valid.replace("/staging", "/production"),
        valid.replace(target_binding.approved_database_role, "another_role"),
        valid + "&options=-csearch_path%3Dpublic",
        valid + "&hostaddr=",
        valid + "&options=",
        valid + "&sslmode=",
        valid + "&sslrootcert=",
    ):
        with pytest.raises(module.SanitizerFailure, match="target.invalid"):
            module.validate_target_url(invalid, target_binding)
    unsealed = object.__new__(module.SanitizerTargetBinding)
    with pytest.raises(module.SanitizerFailure, match="output_invalid"):
        module.validate_target_url(valid, unsealed)


def test_binding_and_preflight_have_no_io_or_execution_surface(
    module: ModuleType, target_binding: Any
) -> None:
    source = SCRIPT.read_text()
    for forbidden in (
        "import psycopg",
        "import socket",
        "import subprocess",
        "urllib.request",
        "def rewrite_database",
    ):
        assert forbidden not in source
    assert module.BINDING_CLAIMS["provider_facts_authenticated"] is False
    assert module.BINDING_CLAIMS["database_mutation_authority"] is False
    assert module.BINDING_CLAIMS["sanitizer_authority"] is False
    assert module.BINDING_CLAIMS["runtime_authority"] is False
    assert module.BINDING_CLAIMS["live_provider_calls_enabled"] is False
    assert module.BINDING_CLAIMS["workflow_integrated"] is False
    assert module.BINDING_CLAIMS["executable"] is False
    assert target_binding._is_bound()


def test_uuid_transform_is_deterministic_v7_and_typeid_round_trips(
    module: ModuleType,
) -> None:
    source = uuid.UUID("11111111-1111-7111-8111-111111111111")
    mapped = module.pseudonymize_uuid7(source, key=KEY, domain="typeid:doctor")
    assert mapped == module.pseudonymize_uuid7(source, key=KEY, domain="typeid:doctor")
    assert mapped != module.pseudonymize_uuid7(
        source, key=KEY, domain="typeid:practice"
    )
    assert mapped != module.pseudonymize_uuid7(
        source, key=OTHER_KEY, domain="typeid:doctor"
    )
    assert mapped.version == 7 and mapped.variant == uuid.RFC_4122
    encoded = TypeID.from_uuid(mapped, "doctor")
    assert TypeID.from_string(str(encoded)).uuid == mapped


def test_correlated_identity_values_recompute_plain_dependent_digests(
    module: ModuleType,
) -> None:
    bundle = module.pseudonymize_identity_bundle(
        issuer="https://clerk.example.invalid",
        subject="user_source_identity",
        email="person@example.com",
        key=KEY,
    )
    repeated = module.pseudonymize_identity_bundle(
        issuer="https://clerk.example.invalid",
        subject="user_source_identity",
        email="person@example.com",
        key=KEY,
    )
    assert bundle == repeated
    assert bundle["subject"].startswith("user_")
    assert bundle["email"].endswith("@example.invalid")
    assert bundle["issuer"].startswith("https://")
    for value, digest in (
        ("issuer", "issuer_sha256"),
        ("subject", "subject_sha256"),
        ("email", "email_sha256"),
    ):
        assert bundle[digest] == module.plain_sha256(bundle[value])
    assert bundle != module.pseudonymize_identity_bundle(
        issuer="https://clerk.example.invalid",
        subject="user_source_identity",
        email="person@example.com",
        key=OTHER_KEY,
    )


def test_shared_domain_and_access_code_pair_are_correlated(module: ModuleType) -> None:
    domain = "practice.example"
    assert module.pseudonymize_domain_name(domain, key=KEY) == (
        module.pseudonymize_domain_name(domain, key=KEY)
    )
    organization_id = uuid.UUID("22222222-2222-7222-8222-222222222222")
    first = module.pseudonymize_provider_access_credentials(organization_id, key=KEY)
    second = module.pseudonymize_provider_access_credentials(organization_id, key=KEY)
    rotated = module.pseudonymize_provider_access_credentials(
        organization_id, key=OTHER_KEY
    )
    assert first == second and first != rotated
    assert len(first["access_code_salt"]) == 32
    assert len(first["access_code_digest"]) == 64
    assert set(first) == {"access_code_salt", "access_code_digest"}
    source = b"uuid:" + organization_id.bytes
    material = module._hmac_digest(KEY, "provider_access_code.code", source)
    code = "".join(
        module.ACCESS_CODE_ALPHABET[byte % len(module.ACCESS_CODE_ALPHABET)]
        for byte in material[:12]
    )
    assert (
        first["access_code_digest"]
        == hashlib.scrypt(
            code.encode(),
            salt=bytes.fromhex(first["access_code_salt"]),
            n=2**14,
            r=8,
            p=1,
            dklen=32,
        ).hex()
    )


def test_typed_text_references_and_unsafe_execution_remain_blockers(
    module: ModuleType,
) -> None:
    policy, _ = _policies()
    typed = policy["transform_contract"]["semantic_domains"]["typed_text_references"]
    assert typed["references"] == {
        "authority_person_binding.binding_ref": {
            "source": "appointment.id",
            "typeid_prefix": "appointment",
        },
    }
    assert (
        _data_contract()["tables"]["authority_person_origin"]["columns"]["source_ref"]
        == "discard_row"
    )
    assert typed["implementation_status"] == (
        "data_contract_complete_execution_missing"
    )
    assert _data_contract()["derived_values"]["authority_person_binding.binding_ref"][
        "required_source_values"
    ] == {"authority_person_binding.binding_kind": ["provider_record"]}
    future = policy["execution_contract"]["future_transaction_requirements"]
    assert future["disable_triggers"] is False
    assert future["truncate_cascade"] is False
    assert future["reject_unexpected_on_truncate_triggers"] is True
    assert future["prove_rollback_before_activation"] is True


def test_cli_outputs_only_bounded_contract_status(
    module: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    assert module.main(["validate-policy"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {
        "schema_version": 1,
        "operation": "validate-policy",
        "executable": False,
        "pass": True,
    }
    assert module.main(["preflight"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result == {
        "schema_version": 1,
        "errors": ["activation.trusted_binding_unimplemented"],
        "pass": False,
    }
