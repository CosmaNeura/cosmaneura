from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
import urllib.parse
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from typeid import TypeID

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


@pytest.fixture
def module() -> ModuleType:
    return _load_module()


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
    assert policy["status"]["executable"] is False
    assert policy["execution_contract"]["database_connections_permitted"] is False
    assert policy["execution_contract"]["sql_mutation_permitted"] is False
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


def test_preflight_refuses_before_connector(module: ModuleType) -> None:
    policy, inventory_policy = _policies()
    called = False

    def connector() -> None:
        nonlocal called
        called = True

    with pytest.raises(
        module.SanitizerFailure, match="activation.trusted_binding_unimplemented"
    ):
        module.preflight(policy, inventory_policy, connector)
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


def test_actual_controller_receipt_shape_cross_binds_target(module: ModuleType) -> None:
    policy, _ = _policies()
    binding = module.validate_controller_receipt_shape(_receipt(), policy)
    assert binding.server_name == SERVER
    assert binding.rehearsal_id == REHEARSAL
    assert binding.derived_fqdn == f"{SERVER}.postgres.database.azure.com"
    assert binding.source_sha == "d" * 40
    assert binding.candidate_sha == "e" * 40
    assert policy["controller_receipt"]["missing_required_bindings"] == [
        "authenticated_receipt_verification",
        "target_fqdn_emitted_by_controller",
        "authoritative_restore_point_emitted_by_controller",
    ]


@pytest.mark.parametrize(
    "change",
    [
        "extra_top_level",
        "extra_target",
        "wrong_rehearsal",
        "wrong_expiry",
        "wrong_network",
        "weak_isolation",
        "wrong_controller_policy",
    ],
)
def test_controller_receipt_rejects_unbound_or_unknown_fields(
    module: ModuleType, change: str
) -> None:
    policy, _ = _policies()
    receipt = _receipt()
    if change == "extra_top_level":
        receipt["unknown"] = True
    elif change == "extra_target":
        receipt["target"]["fqdn"] = f"{SERVER}.postgres.database.azure.com"
    elif change == "wrong_rehearsal":
        receipt["rehearsal_id"] = "fedcba987654"
    elif change == "wrong_expiry":
        receipt["expires_at"] = "2026-09-03T18:00:01Z"
    elif change == "wrong_network":
        receipt["target"]["vnet_id"] += "-other"
    elif change == "weak_isolation":
        receipt["isolation"]["firewall_rule_count"] = 1
    else:
        receipt["policy_sha256"] = "sha256:" + "0" * 64
    with pytest.raises(module.SanitizerFailure):
        module.validate_controller_receipt_shape(receipt, policy)


def test_target_url_requires_exact_derived_target_and_verify_full(
    module: ModuleType,
) -> None:
    policy, _ = _policies()
    binding = module.validate_controller_receipt_shape(_receipt(), policy)
    root = urllib.parse.quote("/tmp/test-root.pem", safe="")
    valid = (
        f"postgresql://sanitizer_role:secret@{binding.derived_fqdn}:5432/staging"
        f"?sslmode=verify-full&sslrootcert={root}"
        f"&application_name={module.APP_NAME}&connect_timeout=10"
    )
    module.validate_target_url(valid, binding, policy, expected_role="sanitizer_role")
    for invalid in (
        valid.replace(binding.derived_fqdn, policy["target"]["rejected_hosts"][1]),
        valid.replace("verify-full", "require"),
        valid.replace("/staging", "/production"),
        valid + "&options=-csearch_path%3Dpublic",
    ):
        with pytest.raises(module.SanitizerFailure, match="target.invalid"):
            module.validate_target_url(
                invalid, binding, policy, expected_role="sanitizer_role"
            )
    with pytest.raises(module.SanitizerFailure, match="target.invalid"):
        module.validate_target_url(valid, binding, policy, expected_role="another_role")


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
