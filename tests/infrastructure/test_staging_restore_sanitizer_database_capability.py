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


def test_exact_database_table_and_transaction_posture() -> None:
    document = _document()
    transaction = _document(TRANSACTION_PLAN)["transaction"]
    privileges = document["privileges"]
    table_sets = document["schema"]["table_sets"]

    databases = privileges["databases"]
    assert databases["closed_world_enumeration_required"] is True
    assert databases["unknown_or_unobserved_databases_permitted"] is False
    assert (
        databases["authorization_stability_source"]
        == "privileges.server_wide_authorization_stability"
    )

    tables = privileges["tables"]
    assert _resolve_table_sources(
        document, tables["select"]["table_set_sources"]
    ) == set(table_sets["application"]["tables"]) | {"alembic_version"}
    assert _resolve_table_sources(
        document, tables["truncate"]["table_set_sources"]
    ) == set(table_sets["application"]["tables"])
    assert _resolve_table_sources(
        document, tables["insert"]["table_set_sources"]
    ) == set(table_sets["rewrite"]["tables"])
    assert not any(privileges["other_authority"].values())
    assert (
        privileges["transaction_compatibility"]["isolation"] == transaction["isolation"]
    )
    assert (
        privileges["transaction_compatibility"][
            "control_table_write_privilege_permitted"
        ]
        is False
    )
    advisory = privileges["transaction_compatibility"]["exclusive_ddl_window"]
    assert advisory["protocol"] == "postgresql-session-level-advisory-lock-v1"
    assert advisory["signed_int32_key_parts"] == [916248462, -1848053680]
    assert advisory["all_migration_paths_cooperate"] is False

    stability = privileges["server_wide_authorization_stability"]
    assert stability["authority_mutator_classes"] == list(
        capability._AUTHORITY_MUTATOR_CLASSES
    )  # noqa: SLF001
    assert stability["final_observation_scope"] == list(
        capability._FINAL_AUTHENTICATION_SCOPE
    )  # noqa: SLF001
    assert (
        stability["sanitizer_must_acquire_before_final_closed_world_observation"]
        is True
    )
    assert stability["retain_through_database_connection_close"] is True
    assert (
        stability["validate_current_lease_and_fence_immediately_before_commit"] is True
    )
    assert stability["rollback_on_loss_or_uncertainty"] is True
    assert (
        stability["mechanism_selected"]
        is stability["implementation_authenticated"]
        is False
    )

    firing = tables["firing_triggers"]
    assert firing["allowed_set_source"] == "schema.descriptor.sha256"
    assert firing["routine_execute_revocation_is_trigger_suppression"] is False
    assert (
        firing["user_insert_triggers_on_rewrite_tables"]["expected_trigger_count"] == 0
    )
    assert (
        firing["user_truncate_triggers_on_truncate_tables"]["expected_trigger_count"]
        == 0
    )
    assert firing["internal_triggers"]["trigger_functions_must_authenticate"] == [
        "security-definer-flag",
        "owner",
        "proconfig",
        "definition",
    ]


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
    enums = types["acl_manageable_custom_enums"]
    assert enums["expected_type_count"] == 14
    assert enums["types"] == list(capability._APPLICATION_CUSTOM_TYPES)  # noqa: SLF001
    assert enums["usage"] == []
    assert set(types["rewrite_value_custom_types"]).issubset(enums["types"])
    relation_rows = types["relation_row_types"]
    assert relation_rows["expected_type_count"] == 63
    assert relation_rows["relation_name_sources"] == [
        "schema.table_sets.application.tables",
        "schema.table_sets.control.tables",
    ]
    generated_arrays = types["generated_array_types"]
    assert generated_arrays["expected_type_count"] == 77
    assert generated_arrays["exact_name_rule"] == (
        "underscore-prefixed-element-type-name"
    )
    assert generated_arrays["enum_element_arrays"] == {
        "effective_usage_must_equal_element_type_effective_usage": True,
        "element_type_source": (
            "privileges.types.public_schema.acl_manageable_custom_enums.types"
        ),
        "expected_type_count": 14,
    }
    assert generated_arrays["relation_row_element_arrays"] == {
        "element_type_source": "privileges.types.public_schema.relation_row_types",
        "expected_type_count": 63,
        "has_type_privilege_usage_is_intrinsic": True,
    }
    assert 14 + 63 + 77 == types["observed_schema_type_count"]
    assert types["catalog_partition_is_exact_disjoint_and_exhaustive"] is True
    assert types["unlisted_type_categories_permitted"] is False

    for policy in (routines, enums):
        assert policy["direct_grants_permitted"] is False
        assert policy["effective_privileges_must_match_exactly"] is True
        assert policy["public_role_privileges_permitted"] is False
        assert (
            policy["public_role_effective_privileges_included_in_verification"] is True
        )
        assert policy["grant_options_permitted"] is False
    assert relation_rows["independently_acl_manageable"] is False
    assert relation_rows["acl_mutation_must_not_be_used_to_establish_boundary"] is True
    assert relation_rows["effective_zero_usage_required"] is False
    assert relation_rows["has_type_privilege_usage_is_intrinsic"] is True
    assert generated_arrays["independently_acl_manageable"] is False
    assert generated_arrays["acl_mutation_supported"] is False
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
    ("path", "replacement"),
    [
        (
            "privileges.types.public_schema.acl_manageable_custom_enums.types",
            ["doctorrole"],
        ),
        (
            "privileges.types.public_schema.acl_manageable_custom_enums.usage",
            ["doctorrole"],
        ),
        ("privileges.types.public_schema.relation_row_types.expected_type_count", 62),
        (
            "privileges.types.public_schema.relation_row_types.effective_zero_usage_required",
            True,
        ),
        (
            "privileges.types.public_schema.generated_array_types.expected_type_count",
            76,
        ),
        (
            "privileges.types.public_schema.generated_array_types.acl_mutation_supported",
            True,
        ),
        (
            "privileges.types.public_schema.generated_array_types.enum_element_arrays.effective_usage_must_equal_element_type_effective_usage",
            False,
        ),
        ("privileges.databases.closed_world_enumeration_required", False),
        (
            "privileges.databases.templates_and_disallowed_connection_databases_included",
            False,
        ),
        ("privileges.databases.implicit_acl_defaults_must_be_expanded", False),
        ("privileges.databases.non_target.public_privileges.connect", True),
        ("privileges.databases.non_target.effective_privileges.connect", True),
        (
            "privileges.databases.non_target.existing_runtime_role_session_count_required",
            1,
        ),
        (
            "privileges.transaction_compatibility.exclusive_ddl_window.access_share_lock_is_exclusion_proof",
            True,
        ),
        (
            "privileges.transaction_compatibility.exclusive_ddl_window.participants_required",
            ["sanitizer-transaction-adapter"],
        ),
        (
            "privileges.transaction_compatibility.exclusive_ddl_window.acquire_before_serializable_transaction",
            False,
        ),
        (
            "privileges.transaction_compatibility.exclusive_ddl_window.retain_same_session_lock_through_commit_or_rollback",
            False,
        ),
        (
            "privileges.default_privileges.requires_authenticated_exclusive_ddl_window",
            False,
        ),
    ],
)
def test_privilege_semantic_mutations_fail_without_template_comparison(
    path: str, replacement: Any
) -> None:
    document = _document()
    _set_path(document, path, replacement)

    with pytest.raises(FAILURE, match="^database_capability.contract_invalid$"):
        capability._validate_privileges(  # noqa: SLF001
            document,
            capability._load_upstreams(),  # noqa: SLF001
        )


def test_server_wide_stability_mutations_fail_without_template_comparison() -> None:
    prefix = "privileges.server_wide_authorization_stability"
    mutations = (
        ("mechanism_selected", True),
        ("external_fencing_required", False),
        ("lease_key_source", "target-database"),
        ("authority_mutator_classes", []),
        ("mutators_must_acquire_before_authority_mutation", False),
        ("sanitizer_must_acquire_before_final_closed_world_observation", False),
        ("reauthenticate_every_capability_fact_while_held", False),
        ("retain_through_database_connection_close", False),
        ("validate_current_lease_and_fence_immediately_before_commit", False),
        ("rollback_on_loss_or_uncertainty", False),
        ("release_only_after_sanitizer_commit_or_rollback_and_connection_close", False),
        ("target_database_advisory_lock_is_sufficient", True),
    )
    for field, replacement in mutations:
        document = _document()
        _set_path(document, f"{prefix}.{field}", replacement)
        with pytest.raises(FAILURE, match="^database_capability.contract_invalid$"):
            capability._validate_privileges(document, capability._load_upstreams())  # noqa: SLF001


def test_firing_trigger_mutations_fail_without_template_comparison() -> None:
    prefix = "privileges.tables.firing_triggers"
    mutations = (
        ("allowed_set_source", "untrusted"),
        ("routine_execute_revocation_is_trigger_suppression", True),
        ("user_insert_triggers_on_rewrite_tables.expected_trigger_count", 1),
        ("user_truncate_triggers_on_truncate_tables.expected_trigger_count", 1),
        ("internal_triggers.trigger_functions_must_authenticate", ["definition"]),
        ("revalidation_windows_required", []),
        ("unlisted_or_changed_trigger_may_fire", True),
    )
    for field, replacement in mutations:
        document = _document()
        _set_path(document, f"{prefix}.{field}", replacement)
        with pytest.raises(FAILURE, match="^database_capability.contract_invalid$"):
            capability._validate_privileges(document, capability._load_upstreams())  # noqa: SLF001


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (
            "role_posture.role_settings.pg_db_role_setting.source_scopes_required",
            ["role-global:setdatabase=0,setrole=runtime-role-oid"],
        ),
        (
            "role_posture.role_settings.pg_db_role_setting.setrole_zero_database_wide_rows_included",
            False,
        ),
        (
            "role_posture.role_settings.pg_db_role_setting.expected_empty_scopes.database_all_roles",
            ["session_replication_role=replica"],
        ),
        (
            "role_posture.role_settings.effective_session.required_exact_values.session_replication_role",
            "replica",
        ),
        (
            "role_posture.role_settings.effective_session.source_precedence_must_include",
            ["adapter-session-commands"],
        ),
        (
            "role_posture.role_settings.effective_session.controller_read_only_observer_settings_are_execution_proof",
            True,
        ),
    ],
)
def test_setting_semantic_mutations_fail_without_template_comparison(
    path: str, replacement: Any
) -> None:
    document = _document()
    _set_path(document, path, replacement)

    with pytest.raises(FAILURE, match="^database_capability.contract_invalid$"):
        capability._validate_identity_and_role(  # noqa: SLF001
            document,
            capability._load_upstreams(),  # noqa: SLF001
        )


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        ("claims.cross_database_role_confinement_authenticated", True),
        ("claims.server_wide_authorization_stability_authenticated", True),
        ("claims.firing_trigger_set_authenticated", True),
        ("claims.database_migration_exclusion_authenticated", True),
        ("status.cross_database_role_confinement_implemented", True),
        ("status.server_wide_authorization_stability_implemented", True),
        ("status.firing_trigger_set_authenticator_implemented", True),
        ("status.database_migration_exclusion_cooperation_implemented", True),
    ],
)
def test_new_capabilities_remain_dormant_without_template_comparison(
    path: str, replacement: Any
) -> None:
    document = _document()
    _set_path(document, path, replacement)

    with pytest.raises(FAILURE, match="^database_capability.contract_invalid$"):
        capability._validate_dormancy(document)  # noqa: SLF001


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
        (
            "role_posture.role_settings.expected_role_owned_entries",
            ["search_path=attacker"],
        ),
        (
            "role_posture.ownership.any_database_or_shared_object_ownership_permitted",
            True,
        ),
        ("privileges.databases.target.public_privileges.connect", True),
        ("privileges.schemas.public.public_role_privileges_permitted", True),
        ("privileges.tables.insert.expected_table_count", 20),
        ("privileges.tables.update.tables", ["patient"]),
        ("privileges.tables.grant_options_permitted", True),
        ("privileges.tables.column_grants.select", ["patient.email"]),
        ("privileges.routines.public_schema.execute.routines", ["typeid_parse(text)"]),
        (
            "privileges.types.public_schema.acl_manageable_custom_enums.usage",
            ["doctorrole"],
        ),
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
