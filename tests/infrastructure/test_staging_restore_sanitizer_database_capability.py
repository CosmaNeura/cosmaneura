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
    profile = inventory["profiles"]["pre"]
    tables = inventory["database"]["tables"]
    application = sorted(set(tables) - set(profile["excluded_tables"]))
    rewrite = data_contract["row_actions"]["rewrite_rows"]
    pinned = document["schema"]["table_sets"]
    assert pinned["application"]["count"] == 62
    assert pinned["application"]["tables"] == application
    rewrite_types = {
        column[1] for table in rewrite for column in tables[table]["columns"]
    }
    assert rewrite_types == capability._REWRITE_VALUE_TYPES  # noqa: SLF001
    assert document["schema"]["descriptor"] == {
        "format": profile["schema_descriptor"]["format"],
        "sha256": profile["schema_descriptor"]["sha256"],
    }


def test_exact_database_table_and_transaction_posture() -> None:
    document = _document()
    privileges = document["privileges"]
    databases = privileges["databases"]
    assert databases["closed_world_enumeration_required"] is True
    assert (
        databases["authorization_stability_source"]
        == "privileges.server_wide_authorization_stability"
    )

    tables = privileges["tables"]
    rewrite = set(document["schema"]["table_sets"]["rewrite"]["tables"])
    assert (
        _resolve_table_sources(document, tables["insert"]["table_set_sources"])
        == rewrite
    )
    assert not any(privileges["other_authority"].values())
    transaction = privileges["transaction_compatibility"]
    assert transaction["control_table_write_privilege_permitted"] is False
    assert transaction["exclusive_ddl_window"]["all_migration_paths_cooperate"] is False

    stability = privileges["server_wide_authorization_stability"]
    assert stability["authority_mutator_classes"] == list(
        capability._AUTHORITY_MUTATOR_CLASSES
    )  # noqa: SLF001
    assert (
        stability["mechanism_selected"]
        is stability["implementation_authenticated"]
        is False
    )
    firing = tables["firing_triggers"]
    assert firing["user_triggers"]["allowed_set_source"] == "schema.descriptor.sha256"
    assert firing["internal_triggers"]["schema_descriptor_source_permitted"] is False
    assert tables["firing_rules"]["exact_non_view_rule_count"] == 0
    sessions = privileges["write_path_effects"]["competing_sessions"]
    assert sessions["require_no_competing_sessions"] is True
    assert sessions["recheck_no_competing_sessions_before_commit"] is True


def test_every_postgresql_privilege_class_is_closed_and_public_aware() -> None:
    document = _document()
    privileges = document["privileges"]
    scope = privileges["scope"]
    assert scope["postgresql_major_version"] == 16
    assert scope["public_role_acl_sources_included_for_every_object_class"] is True
    assert scope["all_grant_options_permitted"] is False

    routines = privileges["routines"]["public_schema"]
    assert routines["observed_schema_routine_count"] == 11
    assert routines["execute"] == {"expected_routine_count": 0, "routines": []}
    types = privileges["types"]["public_schema"]
    enums = types["acl_manageable_custom_enums"]
    rows = types["relation_row_types"]
    arrays = types["generated_array_types"]
    assert [
        enums["expected_type_count"],
        rows["expected_type_count"],
        arrays["expected_type_count"],
    ] == [14, 63, 77]
    assert enums["types"] == list(capability._APPLICATION_CUSTOM_TYPES)  # noqa: SLF001
    assert enums["usage"] == []
    assert set(types["rewrite_value_custom_types"]).issubset(enums["types"])
    assert rows["has_type_privilege_usage_is_intrinsic"] is True
    assert arrays["enum_element_arrays"]["expected_type_count"] == 14
    assert arrays["relation_row_element_arrays"]["expected_type_count"] == 63
    assert types["catalog_partition_is_exact_disjoint_and_exhaustive"] is True

    for name in ("languages", "foreign_data_wrappers", "foreign_servers"):
        assert privileges[name]["usage"] == []
        assert privileges[name]["public_role_privileges_permitted"] is False
    assert privileges["tablespaces"]["create"] == []
    assert (
        privileges["parameters"]["set"]
        == privileges["parameters"]["alter_system"]
        == []
    )
    assert privileges["user_mappings"]["entries"] == []
    assert privileges["large_objects"]["required_source_and_target_count"] == 0
    assert privileges["default_privileges"]["entries"] == []
    assert document["verification"]["must_authenticate"] == list(
        capability._VERIFICATION_REQUIREMENTS  # noqa: SLF001
    )


def test_only_provision_job_identity_is_the_future_database_principal() -> None:
    document = _document()
    binding = document["identity_binding"]
    controller = _document(CONTROLLER)["fixed_bindings"]
    job = _document(RUNTIME_BINDING)["identities"]["jobs"]["provision"]
    assert binding["database_role"] == controller["approved_database_role"]
    assert (
        binding["runtime_identity_source"]
        == "restore_runtime_binding.identities.jobs.provision"
    )
    assert binding["runtime_identity"]["resource_id"] == job["resource_id"]
    assert binding["runtime_identity"]["client_id"] == job["client_id"]
    assert binding["runtime_identity"]["principal_id"] == job["principal_id"]
    assert {"cosmaneura-staging-gha-identity", "any-scribe-identity"} <= set(
        binding["forbidden_identity_sources"]
    )
    provisioning = document["provisioning"]
    assert provisioning["separate_bootstrap_authority_required"] is True
    assert provisioning["runtime_identity_may_bootstrap"] is False
    assert provisioning["teardown_required"] is True


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
        (
            "each_authority_mutator_must_acquire_before_database_or_provider_operation",
            False,
        ),
        (
            "each_authority_mutator_must_retain_continuously_through_durable_commit_or_rollback",
            False,
        ),
        (
            "each_authority_mutator_must_validate_current_lease_and_fence_immediately_before_own_commit",
            False,
        ),
        ("each_authority_mutator_must_abort_or_rollback_on_loss_or_uncertainty", False),
        ("sanitizer_must_acquire_before_final_closed_world_observation", False),
        ("reauthenticate_every_capability_fact_while_held", False),
        ("retain_through_database_connection_close", False),
        (
            "sanitizer_must_validate_current_lease_and_fence_immediately_before_commit",
            False,
        ),
        ("sanitizer_must_rollback_on_loss_or_uncertainty", False),
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
        ("user_triggers.allowed_set_source", "untrusted"),
        ("routine_execute_revocation_is_trigger_suppression", True),
        (
            "user_triggers.user_insert_triggers_on_rewrite_tables.expected_trigger_count",
            1,
        ),
        (
            "user_triggers.user_truncate_triggers_on_truncate_tables.expected_trigger_count",
            1,
        ),
        ("internal_triggers.schema_descriptor_source_permitted", True),
        ("internal_triggers.allowed_set_source", "schema.descriptor.sha256"),
        ("internal_triggers.must_observe", []),
        ("revalidation_windows_required", []),
        ("user_triggers.unlisted_or_changed_trigger_may_fire", True),
    )
    for field, replacement in mutations:
        document = _document()
        _set_path(document, f"{prefix}.{field}", replacement)
        with pytest.raises(FAILURE, match="^database_capability.contract_invalid$"):
            capability._validate_privileges(document, capability._load_upstreams())  # noqa: SLF001
    prefix = "privileges.tables.firing_rules"
    for field, replacement in (
        ("schema_descriptor_source_permitted", True),
        ("exact_non_view_rule_count", 1),
        ("allowed_non_view_rules", ["attacker_rule"]),
        ("relhasrules_must_be_observed", False),
        ("pg_rewrite_fields_must_observe", []),
        ("execution_identity_semantics_must_authenticate", "invoker"),
        ("revalidation_windows_required", []),
    ):
        document = _document()
        _set_path(document, f"{prefix}.{field}", replacement)
        with pytest.raises(FAILURE, match="^database_capability.contract_invalid$"):
            capability._validate_privileges(document, capability._load_upstreams())  # noqa: SLF001
    prefix = "privileges.write_path_effects"
    for field, replacement in (
        ("unknown_or_unobserved_effect_permitted", True),
        (
            "surface_sources.relation_kind_table_access_method_and_foreign_table_server_mapping",
            "untrusted",
        ),
        ("partition_routing.redirection_permitted", True),
        ("logical_writers_and_egress.subscriptions", ["attacker_subscription"]),
        ("logical_writers_and_egress.publications", ["egress"]),
        ("logical_writers_and_egress.logical_replication_slots", ["egress"]),
        ("expression_dependency_closure.allowed_dependency_sources", ["untrusted"]),
        (
            "expression_dependency_closure.unknown_or_unobserved_dependency_permitted",
            True,
        ),
        ("competing_sessions.require_no_competing_sessions", False),
        ("competing_sessions.recheck_no_competing_sessions_before_commit", False),
        ("revalidation_windows_required", []),
        ("revalidate_immediately_before_sanitizer_commit", False),
    ):
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
        ("claims.server_wide_authorization_stability_authenticated", True),
        ("claims.write_path_effects_authenticated", True),
        ("claims.cross_database_role_confinement_authenticated", True),
        ("claims.database_migration_exclusion_authenticated", True),
        ("status.server_wide_authorization_stability_implemented", True),
        ("status.write_path_effects_authenticator_implemented", True),
        ("status.cross_database_role_confinement_implemented", True),
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
