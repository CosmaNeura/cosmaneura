"""Pure offline validator for the dormant sanitizer database capability."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any, NoReturn

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT
    / "contracts/staging-rehearsal/v1/restore-sanitizer-database-capability-contract.json"
)
CONTRACT_SHA256 = "d103d401c64a190c8bebede57b25a81dbff4dfd7113ad051058e93b69254f40b"
ACTIVATION_FAILURE = "activation.database_role_state_authenticator_unimplemented"
MAXIMUM_DOCUMENT_BYTES = 2 * 1024 * 1024

_REWRITE_VALUE_TYPES = frozenset(
    {
        "boolean",
        "character varying",
        "character varying(10)",
        "character varying(120)",
        "character varying(16)",
        "character varying(160)",
        "character varying(2048)",
        "character varying(253)",
        "character varying(255)",
        "character varying(32)",
        "character varying(40)",
        "character varying(512)",
        "character varying(64)",
        "character varying(80)",
        "doctorrole",
        "double precision",
        "integer",
        "json",
        "jsonb",
        "planordering",
        "pronounstyle",
        "subjectivesource",
        "terminology",
        "text",
        "timestamp with time zone",
        "timestamp without time zone",
        "uuid",
    }
)
_REWRITE_CUSTOM_TYPES = (
    "doctorrole",
    "planordering",
    "pronounstyle",
    "subjectivesource",
    "terminology",
)
_BOOTSTRAP_SCOPE = (
    "create-or-map-exact-database-role",
    "set-exact-role-posture",
    "install-exact-direct-database-schema-and-table-grants",
    "revoke-disallowed-public-existing-object-privileges",
    "remove-nondefault-system-acl-deltas-and-role-settings",
    "verify-exact-role-and-privilege-state",
)
_VERIFICATION_REQUIREMENTS = (
    "exact-database-role-name",
    "exact-provision-job-uami-mapping",
    "role-flags-and-connection-limit",
    "null-password-state",
    "empty-outbound-memberships",
    "empty-inbound-members",
    "set-role-escalation-unavailable",
    "empty-ownership-sets",
    "no-database-or-shared-object-ownership-dependencies",
    "exact-direct-and-effective-database-and-schema-privileges",
    "exact-direct-and-effective-table-privileges",
    "empty-explicit-column-acls",
    "column-effective-privileges-derived-only-from-exact-table-privileges",
    "empty-sequence-privileges",
    "empty-public-schema-routine-execute-privileges",
    "empty-public-schema-type-usage-privileges",
    "empty-language-usage-privileges",
    "empty-tablespace-create-privileges",
    "empty-parameter-set-and-alter-system-privileges",
    "empty-foreign-data-wrapper-and-server-usage-privileges",
    "empty-user-mappings",
    "no-large-objects-or-large-object-privileges",
    "empty-default-privilege-entries-and-no-future-user-object-creation",
    "empty-role-settings",
    "empty-publication-subscription-extension-and-event-trigger-authority",
    "all-grant-options-absent",
    "system-object-explicit-and-public-acl-deltas-absent",
    "public-role-acl-sources-included-for-every-object-class",
)

_CONTRACTS = ROOT / "contracts/staging-rehearsal/v1"
_UPSTREAM_SPECS = {
    "sanitizer": (
        _CONTRACTS / "restore-sanitizer-policy.json",
        "233476e83f8f45419735052a334ea06aa0153d9b463e599c849c1f212e34dad3",
        "policy_id",
        "cosmaneura-isolated-restore-sanitizer-contract-v1",
    ),
    "synthetic_inventory": (
        _CONTRACTS / "synthetic-data-policy.json",
        "daaf7fc72c9e44dd5b9ae3467e39bb137697868a2beba848d48681ff0141886b",
        "policy_id",
        "cosmaneura-staging-synthetic-inventory-v1",
    ),
    "data_contract": (
        _CONTRACTS / "restore-sanitizer-data-contract.json",
        "f1c0c7dce56cd2aa42647578940f807186d8e888b70c66da9aa64aa7402b1360",
        "contract_id",
        "cosmaneura-isolated-restore-sanitizer-data-v1",
    ),
    "transaction_plan": (
        _CONTRACTS / "restore-sanitizer-transaction-plan-contract.json",
        "374fefc36125a3c5e74d54e4fd67b8b92c229c256aaed6fca19ef4b13b172732",
        "policy_id",
        "cosmaneura-staging-sanitizer-transaction-plan-v1",
    ),
    "controller_receipt_v2": (
        _CONTRACTS / "controller-receipt-v2-policy.json",
        "522d69df772712a111644c471127da95d698cf2b1c4e994465f5e98860b13436",
        "policy_id",
        "cosmaneura-staging-controller-receipt-v2",
    ),
    "controller_database_observation": (
        _CONTRACTS / "controller-receipt-v2-database-observation-policy.json",
        "66dd80c22dd9f21021ec0c4cf604af5ec6afc5fb5d53bd8e946b72abcf8f43bb",
        "policy_id",
        "cosmaneura-staging-controller-receipt-v2-database-observation-v1",
    ),
    "restore_runtime_binding": (
        _CONTRACTS / "restore-runtime-binding-policy.json",
        "8b5854323f2e783674f510ee63c0d030bb51d2ec35ff85c511b88514f9b1c8f3",
        "policy_id",
        "cosmaneura-staging-restore-runtime-binding-v1",
    ),
}


class SanitizerDatabaseCapabilityFailure(RuntimeError):
    """Bounded refusal without role, identity, or database observations."""


def _fail(code: str = "database_capability.contract_invalid") -> NoReturn:
    raise SanitizerDatabaseCapabilityFailure(code) from None


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        _fail()


def _parse_json(raw: bytes) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in pairs:
            if name in result:
                _fail()
            result[name] = value
        return result

    try:
        value = json.loads(
            raw,
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _value: _fail(),
        )
    except SanitizerDatabaseCapabilityFailure:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        _fail()
    if type(value) is not dict:
        _fail()
    return value


def _read_pinned(path: Path, digest: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError:
        _fail()
    if not 1 <= len(raw) <= MAXIMUM_DOCUMENT_BYTES or not hmac.compare_digest(
        hashlib.sha256(raw).hexdigest(), digest
    ):
        _fail()
    return _parse_json(raw)


def _load_upstreams() -> dict[str, dict[str, Any]]:
    documents: dict[str, dict[str, Any]] = {}
    for name, (path, digest, id_field, expected_id) in _UPSTREAM_SPECS.items():
        document = _read_pinned(path, digest)
        if document.get(id_field) != expected_id:
            _fail()
        documents[name] = document
    return documents


def _validate_upstream_pins(
    contract: dict[str, Any], documents: dict[str, dict[str, Any]]
) -> None:
    expected = {
        name: {id_field: expected_id, "sha256": f"sha256:{digest}"}
        for name, (_path, digest, id_field, expected_id) in _UPSTREAM_SPECS.items()
    }
    if contract.get("upstream_policies") != expected or set(documents) != set(expected):
        _fail()


def _validate_catalog(contract: dict[str, Any], documents: dict[str, Any]) -> None:
    inventory = documents["synthetic_inventory"]
    data_contract = documents["data_contract"]
    transaction_plan = documents["transaction_plan"]
    try:
        profile = inventory["profiles"]["pre"]
        database = inventory["database"]
        inventory_tables = database["tables"]
        excluded = profile["excluded_tables"]
        application = sorted(set(inventory_tables) - set(excluded))
        rewrite = data_contract["row_actions"]["rewrite_rows"]
        discard = data_contract["row_actions"]["discard_rows"]
        required_empty = sorted(
            name
            for name in application
            if inventory_tables[name]["disposition"] == "required_empty"
        )
        insert_order = transaction_plan["transaction"]["inserts"]["table_order"]
        transaction_schema = transaction_plan["schema"]
        schema = contract["schema"]
        descriptor_counts = profile["schema_descriptor"]["counts"]
        rewrite_value_types = {
            column[1]
            for table in rewrite
            for column in inventory_tables[table]["columns"]
        }
    except (KeyError, TypeError):
        _fail()

    partitions = (set(rewrite), set(discard), set(required_empty))
    if (
        type(inventory_tables) is not dict
        or type(excluded) is not list
        or excluded != ["practice_invitation", "public_legal_acceptance"]
        or type(data_contract.get("tables")) is not dict
        or set(data_contract["tables"]) != partitions[0] | partitions[1]
        or any(
            partitions[index] & partitions[other]
            for index in range(3)
            for other in range(index)
        )
        or partitions[0] | partitions[1] | partitions[2] != set(application)
        or (len(application), len(rewrite), len(discard), len(required_empty))
        != (62, 19, 10, 33)
        or len(insert_order) != 19
        or set(insert_order) != partitions[0]
        or database.get("sequences") != []
        or database.get("reject_large_objects") is not True
        or data_contract.get("source_profile") != "pre"
        or rewrite_value_types != _REWRITE_VALUE_TYPES
        or {
            name: descriptor_counts.get(name)
            for name in ("routines", "types", "default_privileges")
        }
        != {"routines": 11, "types": 154, "default_privileges": 0}
        or transaction_schema.get("application_table_count") != 62
        or transaction_schema.get("rewrite_table_count") != 19
        or transaction_schema.get("discard_table_count") != 10
        or transaction_schema.get("required_empty_table_count") != 33
    ):
        _fail()

    expected_table_sets = {
        "application": {"count": 62, "tables": application},
        "rewrite": {"count": 19, "tables": rewrite},
        "discard": {"count": 10, "tables": discard},
        "required_empty": {"count": 33, "tables": required_empty},
        "control": {"count": 1, "tables": [database["control_table"]["name"]]},
        "sequences": {"count": 0, "names": []},
    }
    expected_descriptor = profile["schema_descriptor"]
    if (
        schema.get("profile") != "pre"
        or schema.get("revision") != profile.get("revision")
        or schema.get("descriptor")
        != {
            "format": expected_descriptor.get("format"),
            "sha256": expected_descriptor.get("sha256"),
        }
        or schema.get("namespace") != database.get("schema")
        or schema.get("allowed_non_system_schemas")
        != database.get("allowed_non_system_schemas")
        or schema.get("post_profile_permitted") is not False
        or schema.get("table_sets_are_exact_disjoint_partition") is not True
        or schema.get("table_sets") != expected_table_sets
    ):
        _fail()


def _validate_identity_and_role(
    contract: dict[str, Any], documents: dict[str, Any]
) -> None:
    controller = documents["controller_receipt_v2"]
    observation = documents["controller_database_observation"]
    runtime = documents["restore_runtime_binding"]
    try:
        fixed = controller["fixed_bindings"]
        job = runtime["identities"]["jobs"]["provision"]
        runtime_auth = runtime["per_rehearsal_postgres"]["authentication"]
        observed_role = observation["observation"]["queries"]["role"]
    except (KeyError, TypeError):
        _fail()

    expected_identity = {
        "database_role": fixed["approved_database_role"],
        "database_role_source": "controller_policy.fixed_bindings.approved_database_role",
        "runtime_identity_source": "restore_runtime_binding.identities.jobs.provision",
        "runtime_identity": {
            "kind": "azure-user-assigned-managed-identity",
            "name": "cosmaneura-staging-restore-provision-job",
            "resource_id": job["resource_id"],
            "client_id": job["client_id"],
            "principal_id": job["principal_id"],
        },
        "authentication": {
            "mode": "microsoft-entra-access-token-via-explicit-user-assigned-managed-identity",
            "oauth_resource": runtime_auth["oauth_resource"],
            "password_authentication_permitted": False,
            "password_connection_string_permitted": False,
            "default_or_ambient_credential_permitted": False,
            "exact_database_role_to_uami_mapping_required": True,
            "session_user_must_equal_approved_database_role": True,
            "current_user_must_equal_approved_database_role": True,
        },
        "caller_role_identity_or_authentication_overrides_permitted": False,
        "forbidden_identity_sources": [
            "restore_runtime_binding.identities.jobs.cleanup",
            "restore_runtime_binding.identities.brokers.provision",
            "restore_runtime_binding.identities.brokers.cleanup",
            "restore_runtime_binding.identities.dispatchers.provision",
            "restore_runtime_binding.identities.dispatchers.cleanup",
            "cosmaneura-staging-gha-identity",
            "any-scribe-identity",
        ],
    }
    role = contract.get("role_posture", {})
    if (
        contract.get("identity_binding") != expected_identity
        or fixed.get("provision_job_uami_client_id") != job.get("client_id")
        or fixed.get("provision_job_uami_principal_id") != job.get("principal_id")
        or runtime_auth.get("database_role_source")
        != "authenticated_controller_receipt.approved_database_role"
        or runtime_auth.get("managed_identity_resource_id_source")
        != "identities.jobs.provision.resource_id"
        or runtime_auth.get(
            "password_connection_string_or_default_credential_permitted"
        )
        is not False
        or role.get("required_flags") != observed_role.get("required_flags")
        or role.get("required_connection_limit")
        != observed_role.get("required_connection_limit")
        or role.get("allowed_outbound_memberships")
        != observed_role.get("allowed_memberships")
        or role.get("forbidden_role_names") != observed_role.get("forbidden_role_names")
        or role.get("required_password_state") != "null"
        or role.get("password_authentication_permitted") is not False
        or role.get("allowed_inbound_members") != []
        or any(
            role.get(name) is not False
            for name in (
                "membership_admin_option_permitted",
                "membership_inherit_option_permitted",
                "membership_set_option_permitted",
            )
        )
        or role.get("set_role")
        != {
            "mode": "direct-grants-no-role-switch",
            "allowed_targets": [],
            "escalation_permitted": False,
        }
        or set(role.get("ownership", {}))
        != {
            "any_database_or_shared_object_ownership_permitted",
            "databases",
            "extensions",
            "foreign_data_wrappers",
            "foreign_servers",
            "languages",
            "large_objects",
            "publications",
            "routines",
            "schemas",
            "sequences",
            "subscriptions",
            "tables",
            "tablespaces",
            "types",
            "named_lists_are_diagnostics_not_exhaustive_boundary",
        }
        or role.get("ownership", {}).get(
            "any_database_or_shared_object_ownership_permitted"
        )
        is not False
        or role.get("ownership", {}).get(
            "named_lists_are_diagnostics_not_exhaustive_boundary"
        )
        is not True
        or any(
            value != []
            for name, value in role.get("ownership", {}).items()
            if name
            not in {
                "any_database_or_shared_object_ownership_permitted",
                "named_lists_are_diagnostics_not_exhaustive_boundary",
            }
        )
        or role.get("role_settings")
        != {"global": [], "database_specific": [], "permitted": False}
    ):
        _fail()


def _validate_privileges(contract: dict[str, Any], documents: dict[str, Any]) -> None:
    inventory = documents["synthetic_inventory"]
    transaction = documents["transaction_plan"]["transaction"]
    expected_tables = {
        "column_grants": {
            "select": [],
            "insert": [],
            "update": [],
            "references": [],
            "grant_options_permitted": False,
            "any_column_acl_entry_permitted": False,
            "effective_privileges_must_derive_only_from_exact_table_privileges": True,
        },
        "direct_grants_required": True,
        "effective_privileges_must_match_exactly": True,
        "public_role_effective_privileges_included_in_verification": True,
        "public_role_table_privileges_permitted": False,
        "unlisted_table_privileges_permitted": False,
        "grant_options_permitted": False,
        "select": {
            "table_set_sources": [
                "schema.table_sets.application.tables",
                "schema.table_sets.control.tables",
            ],
            "expected_table_count": 63,
        },
        "truncate": {
            "table_set_sources": ["schema.table_sets.application.tables"],
            "expected_table_count": 62,
        },
        "insert": {
            "table_set_sources": ["schema.table_sets.rewrite.tables"],
            "expected_table_count": 19,
        },
        **{
            name: {"tables": [], "expected_table_count": 0}
            for name in ("update", "delete", "references", "trigger")
        },
    }
    empty_usage = {
        "usage": [],
        "expected_usage_count": 0,
        "direct_grants_permitted": False,
        "effective_privileges_must_match_exactly": True,
        "public_role_privileges_permitted": False,
        "public_role_effective_privileges_included_in_verification": True,
        "grant_options_permitted": False,
    }
    expected_privileges = {
        "scope": {
            "postgresql_major_version": 16,
            "user_object_schemas": ["public"],
            "non_system_schemas_must_match_exactly": True,
            "public_role_acl_sources_included_for_every_object_class": True,
            "all_grant_options_permitted": False,
            "system_objects": {
                "builtin_default_acl_baseline_out_of_scope": True,
                "explicit_grants_to_runtime_role_permitted": False,
                "nondefault_public_acl_deltas_permitted": False,
                "system_object_ownership_permitted": False,
                "privilege_escalation_paths_permitted": False,
            },
        },
        "database": {
            "name": inventory["database"]["expected_name"],
            "connect": True,
            "create": False,
            "temporary": False,
            "direct_connect_grant_required": True,
            "effective_privileges_must_match_exactly": True,
            "public_role_privileges_permitted": False,
            "public_role_effective_privileges_included_in_verification": True,
            "grant_options_permitted": False,
        },
        "schemas": {
            "public": {
                "usage": True,
                "create": False,
                "direct_usage_grant_required": True,
                "effective_privileges_must_match_exactly": True,
                "public_role_privileges_permitted": False,
                "public_role_effective_privileges_included_in_verification": True,
                "grant_options_permitted": False,
            }
        },
        "tables": expected_tables,
        "sequences": {
            "usage": [],
            "select": [],
            "update": [],
            "direct_grants_permitted": False,
            "effective_privileges_must_match_exactly": True,
            "public_role_privileges_permitted": False,
            "public_role_effective_privileges_included_in_verification": True,
            "grant_options_permitted": False,
        },
        "routines": {
            "public_schema": {
                "observed_schema_routine_count": 11,
                "execute": {"expected_routine_count": 0, "routines": []},
                "direct_grants_permitted": False,
                "effective_privileges_must_match_exactly": True,
                "public_role_privileges_permitted": False,
                "public_role_effective_privileges_included_in_verification": True,
                "grant_options_permitted": False,
                "unlisted_routine_privileges_permitted": False,
            }
        },
        "types": {
            "public_schema": {
                "observed_schema_type_count": 154,
                "rewrite_value_custom_types": list(_REWRITE_CUSTOM_TYPES),
                "usage": {"expected_type_count": 0, "types": []},
                "direct_grants_permitted": False,
                "effective_privileges_must_match_exactly": True,
                "public_role_privileges_permitted": False,
                "public_role_effective_privileges_included_in_verification": True,
                "grant_options_permitted": False,
                "unlisted_type_privileges_permitted": False,
                "future_adapter_must_prove_rewrite_without_type_usage_grants": True,
            }
        },
        "languages": {
            **empty_usage,
            "object_scope": "user-defined-non-system-languages",
        },
        "foreign_data_wrappers": {
            **empty_usage,
            "object_scope": "user-defined-non-system-foreign-data-wrappers",
        },
        "foreign_servers": {
            **empty_usage,
            "object_scope": "user-defined-non-system-foreign-servers",
        },
        "tablespaces": {
            "object_scope": "user-defined-non-system-tablespaces",
            "create": [],
            "expected_create_count": 0,
            "direct_grants_permitted": False,
            "effective_privileges_must_match_exactly": True,
            "public_role_privileges_permitted": False,
            "public_role_effective_privileges_included_in_verification": True,
            "grant_options_permitted": False,
        },
        "parameters": {
            "set": [],
            "alter_system": [],
            "explicit_parameter_acl_entries_must_match_exactly": True,
            "public_role_acl_entries_included_in_verification": True,
            "grant_options_permitted": False,
            "effective_alter_system_permitted": False,
            "ordinary_user_settable_session_parameters_remain_postgresql_baseline": True,
        },
        "user_mappings": {
            "entries": [],
            "runtime_role_mapping_permitted": False,
        },
        "large_objects": {
            "required_source_and_target_count": 0,
            "select": [],
            "update": [],
            "direct_grants_permitted": False,
            "effective_privileges_must_match_exactly": True,
            "public_role_privileges_permitted": False,
            "public_role_effective_privileges_included_in_verification": True,
            "grant_options_permitted": False,
        },
        "default_privileges": {
            "entries": [],
            "explicit_default_privilege_entries_must_be_empty": True,
            "all_owners_and_global_and_public_schema_entries_included": True,
            "public_role_entries_included": True,
            "existing_object_public_routine_execute_and_type_usage_must_be_reconciled": True,
            "future_user_object_creation_during_capability_window_permitted": False,
            "implicit_public_defaults_cannot_materialize_during_capability_window": True,
        },
        "other_authority": dict.fromkeys(
            {
                "column_security_label_mutation_permitted",
                "create_extension_permitted",
                "any_database_or_shared_object_ownership_permitted",
                "database_ownership_permitted",
                "ddl_permitted",
                "default_privileges_permitted",
                "event_trigger_ownership_permitted",
                "extension_ownership_permitted",
                "foreign_data_wrapper_ownership_permitted",
                "foreign_server_ownership_permitted",
                "language_ownership_permitted",
                "large_object_ownership_permitted",
                "migration_permitted",
                "publication_ownership_permitted",
                "relation_ownership_permitted",
                "role_settings_permitted",
                "routine_ownership_permitted",
                "schema_ownership_permitted",
                "security_definer_creation_permitted",
                "security_label_authority_permitted",
                "subscription_ownership_permitted",
                "tablespace_ownership_permitted",
                "type_ownership_permitted",
                "unlisted_owner_bearing_object_classes_permitted",
                "user_mapping_permitted",
            },
            False,
        ),
        "transaction_compatibility": {
            "isolation": transaction["isolation"],
            "read_only": transaction["read_only"],
            "autocommit": transaction["autocommit"],
            "synchronous_commit": transaction["synchronous_commit"],
            "control_table_lock_and_revision_revalidation_required": True,
            "control_table_write_privilege_permitted": False,
            "select_privilege_compatible_control_table_lock_required": True,
            "set_role_permitted": False,
        },
    }
    if (
        contract.get("privileges") != expected_privileges
        or transaction.get("ddl_permitted") is not False
        or transaction.get("migration_permitted") is not False
    ):
        _fail()


def _validate_dormancy(contract: dict[str, Any]) -> None:
    status = contract.get("status", {})
    claims = contract.get("claims", {})
    provisioning = contract.get("provisioning", {})
    verification = contract.get("verification", {})
    if (
        contract.get("schema_version") != 1
        or contract.get("policy_id")
        != "cosmaneura-staging-restore-sanitizer-database-capability-v1"
        or contract.get("closed_world") is not True
        or contract.get("activation_ready") is not False
        or contract.get("public_activation_failure") != ACTIVATION_FAILURE
        or contract.get("activation_blockers")
        != [
            "exact_database_role_unselected",
            "provision_job_identity_exact_ids_unpinned",
            "bootstrap_authority_unselected",
            "database_role_provisioning_unimplemented",
            "database_role_state_authenticator_unimplemented",
            "runtime_integration_unimplemented",
        ]
        or status.get("contract_only") is not True
        or any(
            value is not False
            for name, value in status.items()
            if name != "contract_only"
        )
        or not claims
        or any(value is not False for value in claims.values())
        or provisioning.get("implemented") is not False
        or provisioning.get("separate_bootstrap_authority_required") is not True
        or provisioning.get("bootstrap_identity_selected") is not False
        or provisioning.get("runtime_identity_may_bootstrap") is not False
        or provisioning.get("runtime_identity_may_create_alter_drop_or_grant_roles")
        is not False
        or provisioning.get("bootstrap_scope_must_be_limited_to")
        != list(_BOOTSTRAP_SCOPE)
        or provisioning.get("bootstrap_session_must_end_before_runtime_execution")
        is not True
        or provisioning.get("bootstrap_credentials_retained_by_runtime") is not False
        or provisioning.get("bootstrap_authority_retention_permitted") is not False
        or provisioning.get("teardown_required") is not True
        or provisioning.get("teardown_implementation_selected") is not False
        or verification.get("implemented") is not False
        or verification.get("must_authenticate") != list(_VERIFICATION_REQUIREMENTS)
        or verification.get(
            "caller_supplied_role_identity_privilege_or_catalog_facts_permitted"
        )
        is not False
        or verification.get("application_data_read_permitted") is not False
        or verification.get("database_mutation_permitted") is not False
        or verification.get("role_or_privilege_mutation_permitted") is not False
        or verification.get("live_database_calls_enabled") is not False
    ):
        _fail()


def validate_database_capability_contract(
    contract: Any, *, executable: bool = False
) -> dict[str, Any]:
    """Validate the exact closed-world contract without external access."""

    if type(contract) is not dict or type(executable) is not bool:
        _fail()
    template = _read_pinned(CONTRACT_PATH, CONTRACT_SHA256)
    document = _parse_json(_canonical(contract))
    if not hmac.compare_digest(_canonical(document), _canonical(template)):
        _fail()
    documents = _load_upstreams()
    _validate_upstream_pins(document, documents)
    _validate_catalog(document, documents)
    _validate_identity_and_role(document, documents)
    _validate_privileges(document, documents)
    _validate_dormancy(document)
    if executable:
        _fail(ACTIVATION_FAILURE)
    return document


def load_database_capability_contract(*, executable: bool = False) -> dict[str, Any]:
    """Load and validate only the repository-pinned contract."""

    return validate_database_capability_contract(
        _read_pinned(CONTRACT_PATH, CONTRACT_SHA256), executable=executable
    )


def preflight_database_capability_contract() -> NoReturn:
    """Validate the dormant contract, then refuse activation unconditionally."""

    load_database_capability_contract()
    _fail(ACTIVATION_FAILURE)
