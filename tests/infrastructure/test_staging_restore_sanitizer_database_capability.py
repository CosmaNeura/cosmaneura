from __future__ import annotations

import ast
import hashlib
import inspect
import json
import re
from pathlib import Path
from typing import Any

import pytest

from scripts import (
    staging_restore_sanitizer_database_capability as capability,
)

ROOT = Path(__file__).parents[2]
CONTRACTS = ROOT / "contracts/staging-rehearsal/v1"
CONTRACT = CONTRACTS / "restore-sanitizer-database-capability-contract.json"
SCRIPT = ROOT / "scripts/staging_restore_sanitizer_database_capability.py"
INVENTORY = CONTRACTS / "synthetic-data-policy.json"
DATA_CONTRACT = CONTRACTS / "restore-sanitizer-data-contract.json"
TRANSACTION_PLAN = CONTRACTS / "restore-sanitizer-transaction-plan-contract.json"
CONTROLLER = CONTRACTS / "controller-receipt-v2-policy.json"
DATABASE_OBSERVATION = (
    CONTRACTS / "controller-receipt-v2-database-observation-policy.json"
)
RUNTIME_BINDING = CONTRACTS / "restore-runtime-binding-policy.json"
FAILURE = capability.SanitizerDatabaseCapabilityFailure


def _document(path: Path = CONTRACT) -> dict[str, Any]:
    return json.loads(path.read_bytes())


def _set_path(document: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cursor = document
    for part in parts[:-1]:
        cursor = cursor[part]
    cursor[parts[-1]] = value


def _resolve_table_sources(document: dict[str, Any], sources: list[str]) -> set[str]:
    resolved: set[str] = set()
    for source in sources:
        value: Any = document
        for part in source.split("."):
            value = value[part]
        resolved.update(value)
    return resolved


def test_exact_contract_and_upstream_hashes_are_pinned() -> None:
    raw = CONTRACT.read_bytes()
    document = capability.load_database_capability_contract()

    assert hashlib.sha256(raw).hexdigest() == capability.CONTRACT_SHA256
    assert raw == capability._canonical(document) + b"\n"  # noqa: SLF001
    assert set(document) == {
        "activation_blockers",
        "activation_ready",
        "claims",
        "closed_world",
        "identity_binding",
        "policy_id",
        "privileges",
        "provisioning",
        "public_activation_failure",
        "role_posture",
        "schema",
        "schema_version",
        "status",
        "upstream_policies",
        "verification",
    }
    assert set(document["upstream_policies"]) == set(capability._UPSTREAM_SPECS)  # noqa: SLF001
    for name, (
        path,
        digest,
        id_field,
        expected_id,
    ) in capability._UPSTREAM_SPECS.items():  # noqa: SLF001
        upstream = _document(path)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
        assert upstream[id_field] == expected_id
        assert document["upstream_policies"][name] == {
            id_field: expected_id,
            "sha256": f"sha256:{digest}",
        }


def test_closed_world_pre_schema_catalog_and_partition_are_exact() -> None:
    document = _document()
    inventory = _document(INVENTORY)
    data_contract = _document(DATA_CONTRACT)
    transaction_plan = _document(TRANSACTION_PLAN)
    profile = inventory["profiles"]["pre"]
    excluded = profile["excluded_tables"]
    tables = inventory["database"]["tables"]
    application = sorted(set(tables) - set(excluded))
    rewrite = data_contract["row_actions"]["rewrite_rows"]
    discard = data_contract["row_actions"]["discard_rows"]
    required_empty = sorted(
        name for name in application if tables[name]["disposition"] == "required_empty"
    )
    rewrite_value_types = {
        column[1] for table in rewrite for column in tables[table]["columns"]
    }
    pinned = document["schema"]["table_sets"]

    assert excluded == ["practice_invitation", "public_legal_acceptance"]
    assert pinned == {
        "application": {"count": 62, "tables": application},
        "rewrite": {"count": 19, "tables": rewrite},
        "discard": {"count": 10, "tables": discard},
        "required_empty": {"count": 33, "tables": required_empty},
        "control": {
            "count": 1,
            "tables": [inventory["database"]["control_table"]["name"]],
        },
        "sequences": {"count": 0, "names": []},
    }
    partitions = tuple(
        set(pinned[name]["tables"]) for name in ("rewrite", "discard", "required_empty")
    )
    assert not any(
        partitions[left] & partitions[right]
        for left in range(3)
        for right in range(left)
    )
    assert set().union(*partitions) == set(application)
    assert set(transaction_plan["transaction"]["inserts"]["table_order"]) == set(
        rewrite
    )
    assert rewrite_value_types == capability._REWRITE_VALUE_TYPES  # noqa: SLF001
    assert inventory["database"]["reject_large_objects"] is True
    assert {
        name: profile["schema_descriptor"]["counts"][name]
        for name in ("routines", "types", "default_privileges")
    } == {"routines": 11, "types": 154, "default_privileges": 0}
    assert document["schema"]["descriptor"] == {
        "format": profile["schema_descriptor"]["format"],
        "sha256": profile["schema_descriptor"]["sha256"],
    }


def test_exact_least_privilege_role_and_transaction_posture() -> None:
    document = _document()
    observation = _document(DATABASE_OBSERVATION)
    transaction = _document(TRANSACTION_PLAN)["transaction"]
    role = document["role_posture"]
    privileges = document["privileges"]
    table_sets = document["schema"]["table_sets"]

    assert (
        role["required_flags"]
        == observation["observation"]["queries"]["role"]["required_flags"]
    )
    assert role["required_connection_limit"] == 1
    assert role["required_password_state"] == "null"
    assert role["password_authentication_permitted"] is False
    assert role["allowed_outbound_memberships"] == []
    assert role["allowed_inbound_members"] == []
    assert role["set_role"] == {
        "mode": "direct-grants-no-role-switch",
        "allowed_targets": [],
        "escalation_permitted": False,
    }
    ownership = role["ownership"]
    assert ownership["any_database_or_shared_object_ownership_permitted"] is False
    assert ownership["named_lists_are_diagnostics_not_exhaustive_boundary"] is True
    assert all(
        value == []
        for name, value in ownership.items()
        if name
        not in {
            "any_database_or_shared_object_ownership_permitted",
            "named_lists_are_diagnostics_not_exhaustive_boundary",
        }
    )
    assert role["role_settings"] == {
        "global": [],
        "database_specific": [],
        "permitted": False,
    }

    assert privileges["database"]["direct_connect_grant_required"] is True
    assert privileges["database"]["public_role_privileges_permitted"] is False
    assert privileges["schemas"]["public"]["direct_usage_grant_required"] is True
    assert privileges["schemas"]["public"]["public_role_privileges_permitted"] is False

    table_privileges = privileges["tables"]
    assert _resolve_table_sources(
        document, table_privileges["select"]["table_set_sources"]
    ) == set(table_sets["application"]["tables"]) | {"alembic_version"}
    assert _resolve_table_sources(
        document, table_privileges["truncate"]["table_set_sources"]
    ) == set(table_sets["application"]["tables"])
    assert _resolve_table_sources(
        document, table_privileges["insert"]["table_set_sources"]
    ) == set(table_sets["rewrite"]["tables"])
    assert table_privileges["direct_grants_required"] is True
    assert table_privileges["effective_privileges_must_match_exactly"] is True
    assert (
        table_privileges["public_role_effective_privileges_included_in_verification"]
        is True
    )
    assert table_privileges["public_role_table_privileges_permitted"] is False
    assert table_privileges["unlisted_table_privileges_permitted"] is False
    assert table_privileges["grant_options_permitted"] is False
    assert table_privileges["column_grants"] == {
        "any_column_acl_entry_permitted": False,
        "effective_privileges_must_derive_only_from_exact_table_privileges": True,
        "grant_options_permitted": False,
        "insert": [],
        "references": [],
        "select": [],
        "update": [],
    }
    for name in ("update", "delete", "references", "trigger"):
        assert table_privileges[name] == {"expected_table_count": 0, "tables": []}
    assert privileges["sequences"] == {
        "direct_grants_permitted": False,
        "effective_privileges_must_match_exactly": True,
        "grant_options_permitted": False,
        "public_role_effective_privileges_included_in_verification": True,
        "public_role_privileges_permitted": False,
        "select": [],
        "update": [],
        "usage": [],
    }
    assert not any(privileges["other_authority"].values())
    assert privileges["transaction_compatibility"] == {
        "autocommit": transaction["autocommit"],
        "control_table_lock_and_revision_revalidation_required": True,
        "control_table_write_privilege_permitted": False,
        "isolation": transaction["isolation"],
        "read_only": transaction["read_only"],
        "select_privilege_compatible_control_table_lock_required": True,
        "set_role_permitted": False,
        "synchronous_commit": transaction["synchronous_commit"],
    }


def test_every_postgresql_privilege_class_is_closed_and_public_aware() -> None:
    document = _document()
    privileges = document["privileges"]
    scope = privileges["scope"]

    assert scope == {
        "all_grant_options_permitted": False,
        "non_system_schemas_must_match_exactly": True,
        "postgresql_major_version": 16,
        "public_role_acl_sources_included_for_every_object_class": True,
        "system_objects": {
            "builtin_default_acl_baseline_out_of_scope": True,
            "explicit_grants_to_runtime_role_permitted": False,
            "nondefault_public_acl_deltas_permitted": False,
            "privilege_escalation_paths_permitted": False,
            "system_object_ownership_permitted": False,
        },
        "user_object_schemas": ["public"],
    }
    routines = privileges["routines"]["public_schema"]
    assert routines["observed_schema_routine_count"] == 11
    assert routines["execute"] == {"expected_routine_count": 0, "routines": []}
    types = privileges["types"]["public_schema"]
    assert types["observed_schema_type_count"] == 154
    assert types["rewrite_value_custom_types"] == list(
        capability._REWRITE_CUSTOM_TYPES  # noqa: SLF001
    )
    assert types["usage"] == {"expected_type_count": 0, "types": []}
    assert types["future_adapter_must_prove_rewrite_without_type_usage_grants"] is True

    for policy in (routines, types):
        assert policy["direct_grants_permitted"] is False
        assert policy["effective_privileges_must_match_exactly"] is True
        assert policy["public_role_privileges_permitted"] is False
        assert (
            policy["public_role_effective_privileges_included_in_verification"] is True
        )
        assert policy["grant_options_permitted"] is False
    usage_scopes = {
        "languages": "user-defined-non-system-languages",
        "foreign_data_wrappers": "user-defined-non-system-foreign-data-wrappers",
        "foreign_servers": "user-defined-non-system-foreign-servers",
    }
    for name, object_scope in usage_scopes.items():
        assert privileges[name] == {
            "direct_grants_permitted": False,
            "effective_privileges_must_match_exactly": True,
            "expected_usage_count": 0,
            "grant_options_permitted": False,
            "object_scope": object_scope,
            "public_role_effective_privileges_included_in_verification": True,
            "public_role_privileges_permitted": False,
            "usage": [],
        }
    assert privileges["tablespaces"]["object_scope"] == (
        "user-defined-non-system-tablespaces"
    )
    assert privileges["tablespaces"]["create"] == []
    assert privileges["parameters"]["set"] == []
    assert privileges["parameters"]["alter_system"] == []
    assert (
        privileges["parameters"][
            "ordinary_user_settable_session_parameters_remain_postgresql_baseline"
        ]
        is True
    )
    assert privileges["parameters"]["effective_alter_system_permitted"] is False
    assert privileges["user_mappings"] == {
        "entries": [],
        "runtime_role_mapping_permitted": False,
    }
    assert privileges["large_objects"]["required_source_and_target_count"] == 0
    assert privileges["large_objects"]["select"] == []
    assert privileges["large_objects"]["update"] == []
    assert privileges["default_privileges"]["entries"] == []
    assert (
        privileges["default_privileges"][
            "existing_object_public_routine_execute_and_type_usage_must_be_reconciled"
        ]
        is True
    )
    assert (
        privileges["default_privileges"][
            "future_user_object_creation_during_capability_window_permitted"
        ]
        is False
    )
    assert (
        privileges["default_privileges"][
            "implicit_public_defaults_cannot_materialize_during_capability_window"
        ]
        is True
    )
    assert (
        privileges["other_authority"][
            "any_database_or_shared_object_ownership_permitted"
        ]
        is False
    )
    assert document["verification"]["must_authenticate"] == list(
        capability._VERIFICATION_REQUIREMENTS  # noqa: SLF001
    )


def test_only_provision_job_identity_is_the_future_database_principal() -> None:
    document = _document()
    binding = document["identity_binding"]
    controller = _document(CONTROLLER)["fixed_bindings"]
    runtime = _document(RUNTIME_BINDING)
    job = runtime["identities"]["jobs"]["provision"]

    assert binding["database_role"] == controller["approved_database_role"]
    assert binding["database_role"] == "CONFIGURE_EXACT_SANITIZER_ROLE"
    assert binding["runtime_identity_source"] == (
        "restore_runtime_binding.identities.jobs.provision"
    )
    assert binding["runtime_identity"] == {
        "kind": "azure-user-assigned-managed-identity",
        "name": "cosmaneura-staging-restore-provision-job",
        "resource_id": job["resource_id"],
        "client_id": job["client_id"],
        "principal_id": job["principal_id"],
    }
    assert set(binding["forbidden_identity_sources"]) == {
        "restore_runtime_binding.identities.jobs.cleanup",
        "restore_runtime_binding.identities.brokers.provision",
        "restore_runtime_binding.identities.brokers.cleanup",
        "restore_runtime_binding.identities.dispatchers.provision",
        "restore_runtime_binding.identities.dispatchers.cleanup",
        "cosmaneura-staging-gha-identity",
        "any-scribe-identity",
    }
    assert all(
        value is False
        for name, value in binding["authentication"].items()
        if name.endswith("_permitted")
    )
    provisioning = document["provisioning"]
    assert provisioning["separate_bootstrap_authority_required"] is True
    assert provisioning["bootstrap_identity_selected"] is False
    assert provisioning["runtime_identity_may_bootstrap"] is False
    assert (
        provisioning["runtime_identity_may_create_alter_drop_or_grant_roles"] is False
    )
    assert provisioning["teardown_required"] is True
    assert provisioning["teardown_implementation_selected"] is False


@pytest.mark.parametrize(
    "replacement",
    [
        "restore_runtime_binding.identities.jobs.cleanup",
        "restore_runtime_binding.identities.brokers.provision",
        "restore_runtime_binding.identities.brokers.cleanup",
        "restore_runtime_binding.identities.dispatchers.provision",
        "restore_runtime_binding.identities.dispatchers.cleanup",
        "cosmaneura-staging-gha-identity",
        "any-scribe-identity",
    ],
)
def test_forbidden_identity_substitutions_are_rejected(replacement: str) -> None:
    document = _document()
    document["identity_binding"]["runtime_identity_source"] = replacement
    with pytest.raises(FAILURE, match="^database_capability.contract_invalid$"):
        capability.validate_database_capability_contract(document)


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        ("closed_world", False),
        ("activation_ready", True),
        ("status.database_mutation_authority", True),
        ("claims.database_role_state_authenticated", True),
        ("claims.sanitizer_authority", True),
        ("upstream_policies.transaction_plan.sha256", f"sha256:{'0' * 64}"),
        ("schema.revision", "9a2399212488"),
        ("schema.table_sets.application.count", 61),
        ("schema.table_sets.required_empty.tables", []),
        ("role_posture.required_flags.rolsuper", True),
        ("role_posture.required_password_state", "present"),
        ("role_posture.allowed_outbound_memberships", ["azure_pg_admin"]),
        ("role_posture.allowed_inbound_members", ["another_role"]),
        ("role_posture.set_role.allowed_targets", ["azure_pg_admin"]),
        ("role_posture.role_settings.global", ["search_path=attacker"]),
        (
            "role_posture.ownership.any_database_or_shared_object_ownership_permitted",
            True,
        ),
        ("privileges.database.public_role_privileges_permitted", True),
        ("privileges.schemas.public.public_role_privileges_permitted", True),
        ("privileges.tables.insert.expected_table_count", 20),
        ("privileges.tables.update.tables", ["patient"]),
        ("privileges.tables.grant_options_permitted", True),
        ("privileges.tables.column_grants.select", ["patient.email"]),
        ("privileges.routines.public_schema.execute.routines", ["typeid_parse(text)"]),
        ("privileges.types.public_schema.usage.types", ["doctorrole"]),
        ("privileges.languages.usage", ["plpgsql"]),
        ("privileges.tablespaces.create", ["pg_default"]),
        ("privileges.parameters.set", ["session_replication_role"]),
        ("privileges.parameters.alter_system", ["shared_preload_libraries"]),
        ("privileges.foreign_data_wrappers.usage", ["postgres_fdw"]),
        ("privileges.foreign_servers.usage", ["attacker_server"]),
        ("privileges.user_mappings.entries", ["attacker_server"]),
        ("privileges.large_objects.select", [1]),
        ("privileges.default_privileges.entries", ["PUBLIC=EXECUTE"]),
        ("privileges.other_authority.extension_ownership_permitted", True),
        (
            "privileges.scope.system_objects.nondefault_public_acl_deltas_permitted",
            True,
        ),
        ("privileges.transaction_compatibility.set_role_permitted", True),
        ("provisioning.runtime_identity_may_bootstrap", True),
        ("verification.live_database_calls_enabled", True),
    ],
)
def test_security_relevant_contract_mutations_are_rejected(
    path: str, replacement: Any
) -> None:
    document = _document()
    _set_path(document, path, replacement)
    with pytest.raises(FAILURE, match="^database_capability.contract_invalid$"):
        capability.validate_database_capability_contract(document)


def test_unknown_missing_duplicate_and_nonfinite_fields_are_rejected() -> None:
    document = _document()
    document["unknown"] = False
    with pytest.raises(FAILURE, match="^database_capability.contract_invalid$"):
        capability.validate_database_capability_contract(document)

    document = _document()
    del document["claims"]
    with pytest.raises(FAILURE, match="^database_capability.contract_invalid$"):
        capability.validate_database_capability_contract(document)

    for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b"[]"):
        with pytest.raises(FAILURE, match="^database_capability.contract_invalid$"):
            capability._parse_json(raw)  # noqa: SLF001


def test_validator_has_no_runtime_network_database_provider_env_sql_or_write_surface() -> (
    None
):
    source = SCRIPT.read_text()
    tree = ast.parse(source)
    imports = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imports.isdisjoint(
        {
            "asyncio",
            "azure",
            "boto3",
            "httpx",
            "os",
            "psycopg",
            "requests",
            "socket",
            "sqlalchemy",
            "subprocess",
            "urllib",
        }
    )
    calls = {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, (ast.Attribute, ast.Name))
    }
    assert calls.isdisjoint(
        {
            "Popen",
            "commit",
            "connect",
            "execute",
            "getenv",
            "open",
            "remove",
            "rename",
            "replace",
            "rollback",
            "run",
            "touch",
            "unlink",
            "urlopen",
            "write_bytes",
            "write_text",
        }
    )
    sql = re.compile(
        r"\b(?:alter|create|delete|drop|grant|insert|revoke|select|truncate|update)\s+"
        r"(?:database|from|into|on|role|schema|sequence|table)\b",
        re.IGNORECASE,
    )
    strings = (
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )
    assert not any(sql.search(value) for value in strings)
    assert all(
        token not in source
        for token in (
            "IDENTITY_ENDPOINT",
            "AZURE_CLIENT_ID",
            "DATABASE_URL",
            "workflow_dispatch",
            "azure/login",
        )
    )
    assert (
        list(
            inspect.signature(
                capability.preflight_database_capability_contract
            ).parameters
        )
        == []
    )
    assert list(
        inspect.signature(capability.load_database_capability_contract).parameters
    ) == ["executable"]
    assert list(
        inspect.signature(capability.validate_database_capability_contract).parameters
    ) == [
        "contract",
        "executable",
    ]


def test_public_preflight_and_executable_paths_are_terminal() -> None:
    assert capability.ACTIVATION_FAILURE == (
        "activation.database_role_state_authenticator_unimplemented"
    )
    for invoke in (
        capability.preflight_database_capability_contract,
        lambda: capability.load_database_capability_contract(executable=True),
        lambda: capability.validate_database_capability_contract(
            _document(), executable=True
        ),
    ):
        with pytest.raises(FAILURE, match=f"^{capability.ACTIVATION_FAILURE}$"):
            invoke()


@pytest.mark.parametrize("value", [None, [], (), "contract", 1, True])
def test_non_exact_contract_types_are_rejected(value: Any) -> None:
    with pytest.raises(FAILURE, match="^database_capability.contract_invalid$"):
        capability.validate_database_capability_contract(value)


@pytest.mark.parametrize("value", [None, 0, 1, "false"])
def test_non_boolean_execution_switches_are_rejected(value: Any) -> None:
    with pytest.raises(FAILURE, match="^database_capability.contract_invalid$"):
        capability.validate_database_capability_contract(
            _document(),
            executable=value,  # type: ignore[arg-type]
        )
