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
CONTRACT_SHA256 = "c776764647e3209cb793f62fbb5e768845dd3d7625d93f5da37fad72ce83b762"
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
_APPLICATION_CUSTOM_TYPES = (
    "confidenceband",
    "cpsrole",
    "doctorrole",
    "labpanelsource",
    "labpanelstatus",
    "medicationkind",
    "medicationsource",
    "medicationstatus",
    "planordering",
    "pronounstyle",
    "subjectivesource",
    "terminology",
    "videoprovider",
    "zoommeetingstatus",
)
_ADVISORY_LOCK_KEY_SOURCE = "cosmaneura:staging:sanitizer-ddl-exclusion:v1"
_ADVISORY_LOCK_KEY_SHA256 = (
    "369cd78e91d8f0507a5d72aa89df02f240b7f4bbb4d97f3030625aea41bb02a3"
)
_ADVISORY_LOCK_KEY_PARTS = (916248462, -1848053680)
_AUTHORITY_MUTATOR_CLASSES = tuple(
    (
        "bootstrap-and-admin-grant-revoke alter-role-membership alter-role-attributes "
        "role-ownership-reassignment "
        "database-schema-and-shared-object-create-alter-drop-including-migrations-and-ad-hoc-ddl "
        "role-and-database-setting-set-reset server-and-startup-configuration-mutation "
        "database-create-drop-rename-allowconn-owner-acl "
        "schema-owner-acl table-owner-acl column-acl sequence-owner-acl routine-owner-acl "
        "type-owner-acl language-owner-acl tablespace-owner-acl parameter-acl-and-alter-system "
        "foreign-data-wrapper-owner-acl foreign-server-owner-acl user-mapping-create-alter-drop "
        "large-object-owner-acl default-acl entra-database-role-mapping"
    ).split()
)
_FINAL_AUTHENTICATION_SCOPE = tuple(
    (
        "database-role-flags-password-memberships-and-ownership entra-to-database-role-mapping "
        "every-database-row-allowconn-owner-acl-and-effective-privileges "
        "schema-table-column-sequence-routine-type-language-and-tablespace-authority "
        "parameter-foreign-data-user-mapping-large-object-and-default-acl-authority "
        "role-database-and-effective-session-settings schema-descriptor-and-all-write-path-effects"
    ).split()
)
_ADMISSION_WRITER_CLASSES = tuple(
    "ordinary-dml-connections concurrent-jobs logical-and-background-writers "
    "owner-and-admin-writers provider-controlled-writers authority-and-configuration-mutators".split()
)
_ALLOWED_EXECUTION_STATEMENT_CLASSES = tuple(
    "begin-serializable fixed-adapter-session-settings fixed-capability-authentication-selects "
    "fixed-session-advisory-lock-acquire-release lock-and-read-exact-control-row "
    "truncate-exact-transaction-plan-table-set parameterized-insert-exact-transaction-plan-operations "
    "commit-or-rollback-current-transaction fresh-postcommit-verification-selects".split()
)
_FORBIDDEN_EXECUTION_ENTRY_POINTS = tuple(
    "unlisted-select call do copy ddl lo-create-lo-creat-lo-from-bytea-and-any-large-object-mutation "
    "pg-logical-emit-message pg-notify-listen-and-unlisten prepare-transaction-commit-prepared-and-rollback-prepared "
    "unlisted-advisory-lock-operations unlisted-guc-set-reset-set-config-operations".split()
)
_EXECUTION_PROTOCOL_PHASES = tuple(
    "acquire-and-drain-external-admission-fence acquire-target-advisory-lock-before-transaction "
    "begin-serializable apply-fixed-settings-and-final-authentication lock-and-read-control-row "
    "execute-exact-truncate-and-parameterized-inserts immediate-precommit-revalidation commit-or-rollback "
    "validate-fence-close-sanitizer-and-handoff fresh-verification-close-verifier-and-release-fence".split()
)
_ACTIVATION_BLOCKERS = (
    "exact_database_role_unselected",
    "provision_job_identity_exact_ids_unpinned",
    "bootstrap_authority_unselected",
    "database_role_provisioning_unimplemented",
    "cross_database_role_confinement_unimplemented",
    "server_wide_authorization_stability_unimplemented",
    "effective_session_timeout_values_unselected",
    "effective_session_settings_authenticator_unimplemented",
    "database_migration_exclusion_cooperation_unimplemented",
    "write_path_effects_authenticator_unimplemented",
    "database_role_state_authenticator_unimplemented",
    "runtime_integration_unimplemented",
)
_BOOTSTRAP_SCOPE = (
    "create-or-map-exact-database-role",
    "set-exact-role-posture",
    "reconcile-connect-create-and-temporary-acls-across-every-server-database",
    "install-exact-direct-database-schema-and-table-grants",
    "revoke-disallowed-public-existing-object-privileges",
    "remove-nondefault-system-acl-deltas-and-applicable-role-and-database-settings",
    "verify-fixed-advisory-lock-cooperation-across-every-migration-path",
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
    "closed-world-every-pg-database-entry-and-datallowconn",
    "exact-direct-public-membership-and-effective-database-privileges",
    "target-only-effective-connect-and-no-create-or-temporary-anywhere",
    "server-wide-authorization-state-freeze-from-final-observation-through-transaction",
    "exact-direct-and-effective-schema-privileges",
    "exact-direct-and-effective-table-privileges",
    "closed-world-write-path-effects-and-no-competing-sessions-under-held-stability-windows",
    "empty-explicit-column-acls",
    "column-effective-privileges-derived-only-from-exact-table-privileges",
    "empty-sequence-privileges",
    "empty-public-schema-routine-execute-privileges",
    "exact-fourteen-acl-manageable-custom-enum-types",
    "empty-acl-and-effective-usage-for-acl-manageable-custom-enums",
    "postgresql16-intrinsic-relation-row-and-row-array-usage-baseline",
    "postgresql16-enum-array-usage-derived-from-element-enum-acl",
    "empty-language-usage-privileges",
    "empty-tablespace-create-privileges",
    "empty-parameter-set-and-alter-system-privileges",
    "empty-foreign-data-wrapper-and-server-usage-privileges",
    "empty-user-mappings",
    "zero-large-objects-and-fixed-grammar-confinement-of-ambient-large-object-routines",
    "zero-prepared-transactions-and-max-prepared-transactions-zero",
    "all-pg-db-role-setting-source-rows-including-setrole-zero",
    "all-effective-session-setting-sources-and-exact-safe-values",
    "session-replication-role-origin",
    "shared-session-advisory-lock-cooperation-across-every-migration-path-and-sanitizer",
    "revision-and-schema-revalidated-under-held-exclusive-ddl-window",
    "empty-default-privilege-entries-and-no-future-user-object-creation-under-exclusive-ddl-window",
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
        application_value_types = {
            column[1]
            for table in application
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
        or not set(_APPLICATION_CUSTOM_TYPES).issubset(application_value_types)
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
    expected_role_settings = {
        "caller_or_environment_overrides_permitted": False,
        "expected_role_owned_entries": [],
        "permitted": False,
        "pg_db_role_setting": {
            "catalog": "pg_catalog.pg_db_role_setting",
            "closed_world_source_rows_required": True,
            "observation_implemented": False,
            "observed_rows": [],
            "expected_empty_scopes": {
                "database_all_roles": [],
                "role_and_database": [],
                "role_global": [],
            },
            "source_scopes_required": [
                "role-global:setdatabase=0,setrole=runtime-role-oid",
                "database-wide:setdatabase=target-database-oid,setrole=0",
                "role-and-database:setdatabase=target-database-oid,setrole=runtime-role-oid",
            ],
            "setrole_zero_database_wide_rows_included": True,
            "unknown_or_unobserved_rows_permitted": False,
        },
        "effective_session": {
            "all_effective_settings_must_match_authenticated_baseline": True,
            "observation_implemented": False,
            "required_exact_values": {
                "default_transaction_isolation": "serializable",
                "default_transaction_read_only": "off",
                "max_prepared_transactions": "0",
                "row_security": "on",
                "search_path": "pg_catalog",
                "session_replication_role": "origin",
                "synchronous_commit": "on",
                "transaction_isolation": "serializable",
                "transaction_read_only": "off",
            },
            "required_exact_timeout_values": {
                "idle_in_transaction_session_timeout": (
                    "CONFIGURE_EXACT_SANITIZER_IDLE_TRANSACTION_TIMEOUT"
                ),
                "lock_timeout": "CONFIGURE_EXACT_SANITIZER_LOCK_TIMEOUT",
                "statement_timeout": "CONFIGURE_EXACT_SANITIZER_STATEMENT_TIMEOUT",
            },
            "adapter_owned_session_commands_only": True,
            "controller_read_only_observer_settings_are_execution_proof": False,
            "server_startup_and_system_sources_included": True,
            "source_precedence_must_include": [
                "server-startup-and-system-configuration",
                "database-wide-pg-db-role-setting",
                "role-global-pg-db-role-setting",
                "role-and-database-pg-db-role-setting",
                "connection-startup-options",
                "adapter-session-commands",
            ],
            "unknown_or_unobserved_effective_settings_permitted": False,
        },
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
        or role.get("role_settings") != expected_role_settings
    ):
        _fail()


def _validate_privileges(contract: dict[str, Any], documents: dict[str, Any]) -> None:
    inventory = documents["synthetic_inventory"]
    sanitizer = documents["sanitizer"]
    transaction = documents["transaction_plan"]["transaction"]
    future = sanitizer["execution_contract"]["future_transaction_requirements"]
    admission_fence = {"kind": "authenticated-continuous-data-writer-admission-fence-v1", **dict.fromkeys("required owner_admin_and_provider_paths_must_participate external_to_target_database_session_required covers_server_wide_authority_mutator_classes drain_and_deny_every_other_connection_or_data_writer same_fence_instance_required controlled_handoff_required original_sanitizer_connection_must_close_before_verifier_admission exact_sanitizer_must_acquire_before_final_closed_world_observation validate_immediately_before_sanitizer_commit validate_immediately_after_sanitizer_commit_before_connection_close verifier_validates_before_and_after_fresh_verification fresh_verifier_reobserves_admitted_and_competing_sets_before_and_after_verification release_only_after_fresh_verifier_close_and_handoff_completion fail_closed_on_loss_uncertainty_or_uncontrolled_admission".split(), True), **dict.fromkeys("mechanism_selected authentication_implemented enforcement_authority_selected runtime_role_can_alter_or_bypass_fence unknown_or_uncontrolled_writer_permitted".split(), False), "enforcement_binding_sources": "authenticated-server-identity privileges.databases.target.name identity_binding.runtime_identity identity_binding.database_role".split(), "denied_writer_classes": list(_ADMISSION_WRITER_CLASSES), "authority_mutator_classes_source": "privileges.server_wide_authorization_stability.authority_mutator_classes", "admitted_connection_sequence": "exact-sanitizer-connection exact-fresh-postcommit-verifier-connection".split(), "retain_continuously_through": "sanitizer-commit-or-rollback immediate-postcommit-fence-validation original-sanitizer-connection-close controlled-sanitizer-to-verifier-handoff exact-fresh-postcommit-verification fresh-verifier-connection-close".split()}  # fmt: skip
    statement_grammar = {**dict.fromkeys("allowed_statements_exactly_derived_from_sources fixed_select_ast_and_result_shapes_must_be_pinned fixed_advisory_function_signatures_and_key_must_be_pinned fixed_guc_names_values_and_contexts_must_be_pinned ordinary_commit_or_rollback_only".split(), True), **dict.fromkeys("implemented template_or_ast_digests_selected caller_sql_identifiers_function_calls_or_fragments_permitted connection_or_cursor_escape_permitted unlisted_statement_or_function_permitted".split(), False), "sealed_mutation_operation_source": "transaction_plan.execution_handoff", "transaction_invariant_source": "transaction_plan.transaction", "fixed_protocol_template_source": "future-adapter-owned-exact-ast-template-digests", "template_or_ast_digests": [], "allowed_statement_classes": list(_ALLOWED_EXECUTION_STATEMENT_CLASSES), "ordered_protocol_phases": list(_EXECUTION_PROTOCOL_PHASES), "advisory_protocol_source": "privileges.transaction_compatibility.exclusive_ddl_window", "guc_protocol_source": "role_posture.role_settings.effective_session", "forbidden_statement_or_entry_point_classes": list(_FORBIDDEN_EXECUTION_ENTRY_POINTS)}  # fmt: skip
    large_object_safety = {**dict.fromkeys("ambient_system_baseline_explicitly_acknowledged complete_oid_and_owner_visibility_required zero_metadata_rows_proves_no_large_object_pages same_admission_fence_and_exact_runtime_role_binding_required not_an_exhaustive_list_of_public_executable_large_object_routines".split(), True), **dict.fromkeys("creation_or_mutation_permitted observation_implemented".split(), False), "authoritative_catalog_source": "pg_catalog.pg_largeobject_metadata", "exact_effective_postgresql16_public_execute_creation_entry_points": [{"routine": name, "privilege": "EXECUTE"} for name in ("pg_catalog.lo_creat(integer)", "pg_catalog.lo_create(oid)", "pg_catalog.lo_from_bytea(oid,bytea)")], "precommit_immediate_observation": {"required": True, "large_object_count": 0, "runtime_owned_large_object_count": 0}, "fresh_postcommit_verification": {"required": True, "fresh_connection_required": True, "large_object_count": 0, "runtime_owned_large_object_count": 0}}  # fmt: skip
    prepared_transaction_safety = {**dict.fromkeys("complete_server_wide_visibility_required same_admission_fence_required fresh_postcommit_connection_required".split(), True), **dict.fromkeys("observation_implemented prepare_commit_or_rollback_prepared_permitted".split(), False), "effective_max_prepared_transactions": "0", "max_prepared_transactions_setting": {"source": "pg_catalog.pg_settings", "context": "postmaster", "pending_restart": False, "setting": "0"}, "pg_prepared_xacts_source": "pg_catalog.pg_prepared_xacts", "precommit_immediate_prepared_xact_count": 0, "fresh_postcommit_prepared_xact_count": 0}  # fmt: skip
    advisory_key_digest = hashlib.sha256(_ADVISORY_LOCK_KEY_SOURCE.encode()).digest()
    if (
        advisory_key_digest.hex() != _ADVISORY_LOCK_KEY_SHA256
        or tuple(
            int.from_bytes(advisory_key_digest[offset : offset + 4], "big", signed=True)
            for offset in (0, 4)
        )
        != _ADVISORY_LOCK_KEY_PARTS
    ):
        _fail()
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
        "firing_triggers": {
            "authentication_implemented": False,
            "revalidation_windows_required": "privileges.server_wide_authorization_stability privileges.transaction_compatibility.exclusive_ddl_window".split(),
            "routine_execute_revocation_is_trigger_suppression": False,
            "user_triggers": {
                "allowed_set_source": "schema.descriptor.sha256",
                "exact_allowed_set_and_definitions_must_match_authenticated_schema_descriptor": True,
                "unlisted_or_changed_trigger_may_fire": False,
                "user_insert_triggers_on_rewrite_tables": {
                    "expected_trigger_count": 0,
                    "table_set_source": "schema.table_sets.rewrite.tables",
                    "triggers": [],
                },
                "user_truncate_triggers_on_truncate_tables": {
                    "expected_trigger_count": 0,
                    "table_set_source": "schema.table_sets.application.tables",
                    "triggers": [],
                },
            },
            "internal_triggers": {
                "observation_source": (
                    "future-closed-world-database-trigger-and-constraint-catalog-observation"
                ),
                "allowed_set_source": (
                    "future-exact-authenticated-constraint-catalog-observation"
                ),
                "observation_implemented": False,
                "authentication_implemented": False,
                "schema_descriptor_source_permitted": False,
                "exact_set_required": True,
                "every_observed_internal_trigger_must_link_to_exactly_one_expected_constraint": True,
                "every_expected_constraint_trigger_cardinality_event_timing_and_function_identity_must_match": True,
                "missing_extra_or_unlinked_internal_triggers_permitted": False,
                "oid_values_are_linkage_only_not_portable_expected_identities": True,
                "built_in_trigger_function_body_authenticated": False,
                "built_in_trigger_function_properties_source": (
                    "pinned-postgresql-16-system-catalog-baseline"
                ),
                "must_observe": [
                    "trigger-name-and-relation-identity",
                    "trigger-oid-and-constraint-oid-linkage",
                    "tgtype-event-and-timing",
                    "tgenabled-and-effective-firing-state",
                    "pg-get-triggerdef",
                    "constraint-identity-linkage-and-definition",
                    "trigger-function-oid-linkage-schema-and-name",
                    "trigger-function-owner",
                    "trigger-function-security-definer-flag",
                    "trigger-function-proconfig",
                    "trigger-function-language-kind-volatility-parallel-and-leakproof",
                ],
            },
        },
        "firing_rules": {
            "observation_source": (
                "future-closed-world-pg-rewrite-and-relation-catalog-observation"
            ),
            "observation_implemented": False,
            "authentication_implemented": False,
            "schema_descriptor_source_permitted": False,
            "target_table_set_source": "schema.table_sets.rewrite.tables",
            "exact_non_view_rule_count": 0,
            "allowed_non_view_rules": [],
            "view_return_rules_out_of_scope": True,
            "relhasrules_must_be_observed": True,
            "pg_rewrite_fields_must_observe": [
                "rule-oid-and-name",
                "relation-identity",
                "ev-type",
                "ev-enabled",
                "is-instead",
                "ev-action",
                "ev-qual",
            ],
            "execution_identity_semantics_must_authenticate": (
                "rule-actions-execute-as-table-owner-no-independent-rule-owner"
            ),
            "revalidation_windows_required": [
                "privileges.server_wide_authorization_stability",
                "privileges.transaction_compatibility.exclusive_ddl_window",
            ],
            "unknown_or_unobserved_non_view_rule_permitted": False,
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
        "databases": {
            "catalog": "pg_catalog.pg_database",
            "closed_world_enumeration_required": True,
            "observation_implemented": False,
            "observed_entries": [],
            "entry_identity_fields": ["oid", "datname"],
            "required_entry_fields": [
                "oid",
                "datname",
                "datallowconn",
                "runtime_role_is_owner",
                "direct_connect",
                "direct_create",
                "direct_temporary",
                "public_connect",
                "public_create",
                "public_temporary",
                "membership_connect",
                "membership_create",
                "membership_temporary",
                "effective_connect",
                "effective_create",
                "effective_temporary",
            ],
            "acl_sources_required": [
                "direct-runtime-role",
                "PUBLIC",
                "inherited-role-memberships",
                "database-ownership",
            ],
            "effective_privileges_must_match_exactly": True,
            "grant_options_permitted": False,
            "implicit_acl_defaults_must_be_expanded": True,
            "templates_and_disallowed_connection_databases_included": True,
            "unknown_or_unobserved_databases_permitted": False,
            "target_must_appear_exactly_once": True,
            "runtime_role_database_ownership_permitted": False,
            "authorization_stability_source": (
                "privileges.server_wide_authorization_stability"
            ),
            "target": {
                "name": inventory["database"]["expected_name"],
                "datallowconn_required": True,
                "runtime_role_is_owner": False,
                "direct_privileges": {
                    "connect": True,
                    "create": False,
                    "temporary": False,
                },
                "public_privileges": {
                    "connect": False,
                    "create": False,
                    "temporary": False,
                },
                "membership_privileges": {
                    "connect": False,
                    "create": False,
                    "temporary": False,
                },
                "effective_privileges": {
                    "connect": True,
                    "create": False,
                    "temporary": False,
                },
            },
            "non_target": {
                "every_observed_entry_except_target": True,
                "datallowconn_must_be_recorded": True,
                "runtime_role_is_owner": False,
                "existing_runtime_role_session_count_required": 0,
                "direct_privileges": {
                    "connect": False,
                    "create": False,
                    "temporary": False,
                },
                "public_privileges": {
                    "connect": False,
                    "create": False,
                    "temporary": False,
                },
                "membership_privileges": {
                    "connect": False,
                    "create": False,
                    "temporary": False,
                },
                "effective_privileges": {
                    "connect": False,
                    "create": False,
                    "temporary": False,
                },
            },
        },
        "server_wide_authorization_stability": {
            "kind": "authenticated-server-wide-external-lease-or-freeze-v1",
            "required": True,
            "mechanism_selected": False,
            "implementation_authenticated": False,
            "lease_key_source": "authenticated-server-identity",
            "external_fencing_required": True,
            "closed_world_mutator_scope": True,
            "authority_mutator_classes": list(_AUTHORITY_MUTATOR_CLASSES),
            "unknown_or_uncovered_authority_mutator_permitted": False,
            "each_authority_mutator_must_acquire_before_database_or_provider_operation": True,
            "each_authority_mutator_must_retain_continuously_through_durable_commit_or_rollback": True,
            "each_authority_mutator_must_validate_current_lease_and_fence_immediately_before_own_commit": True,
            "each_authority_mutator_must_abort_or_rollback_on_loss_or_uncertainty": True,
            "sanitizer_must_acquire_before_final_closed_world_observation": True,
            "final_observation_scope": list(_FINAL_AUTHENTICATION_SCOPE),
            "final_observation_and_revalidation_under_held_window": True,
            "reauthenticate_every_capability_fact_while_held": True,
            "mutation_may_begin_only_after_final_revalidation_is_accepted": True,
            "retain_through_sanitizer_commit_or_rollback": True,
            "retain_through_database_connection_close": True,
            "sanitizer_must_validate_current_lease_and_fence_immediately_before_commit": True,
            "release_only_after_sanitizer_commit_or_rollback_and_connection_close": True,
            "sanitizer_must_rollback_on_loss_or_uncertainty": True,
            "fail_closed_if_acquisition_retention_revalidation_or_release_is_uncertain": True,
            "server_wide_external_lease_or_freeze_required": True,
            "target_database_advisory_lock_is_sufficient": False,
            "serializable_transaction_is_sufficient": False,
        },
        # fmt: off
        "write_path_effects": {
            "authentication_implemented": False,
            "exact_allowed_effects_required": True,
            "unknown_or_unobserved_effect_permitted": False,
            "surface_sources": {
                "rewrite_rules": "privileges.tables.firing_rules",
                "user_trigger_firing": "privileges.tables.firing_triggers.user_triggers",
                "internal_trigger_firing": "privileges.tables.firing_triggers.internal_triggers",
                "partition_and_inheritance_routing": "privileges.write_path_effects.partition_routing",
                "logical_subscription_and_apply_worker_writers": "privileges.write_path_effects.logical_writers_and_egress",
                "publication_and_replication_slot_egress": "privileges.write_path_effects.logical_writers_and_egress",
                "relation_kind_table_access_method_and_foreign_table_server_mapping": "schema.descriptor.sha256",
                "expression_dependency_closure": "privileges.write_path_effects.expression_dependency_closure",
                "connection_and_writer_admission": "privileges.write_path_effects.competing_sessions.admission_fence",
                "large_object_creation": "privileges.write_path_effects.execution_surface.large_object_safety",
                "prepared_transactions": "privileges.write_path_effects.execution_surface.prepared_transaction_safety",
            },
            "partition_routing": {
                "observation_source": "future-closed-world-pg-inherits-and-partition-catalog-observation",
                "observation_implemented": False,
                "expected_routing_edge_count": 0,
                "pg_inherits_edges": [],
                "redirection_permitted": False,
                "unknown_or_unobserved_routing_permitted": False,
            },
            "logical_writers_and_egress": {
                "observation_source": "future-closed-world-logical-replication-catalog-and-worker-observation",
                "observation_implemented": False,
                "subscriptions": [],
                "apply_workers": [],
                "publications": [],
                "logical_replication_slots": [],
                "unknown_or_unobserved_writer_or_egress_permitted": False,
            },
            "expression_dependency_closure": {
                "observation_source": "future-closed-world-expression-dependency-catalog-observation",
                "observation_implemented": False,
                "classes": "column-defaults generated-columns check-constraints exclusion-constraints index-expressions-and-predicates row-level-security-policies types casts operators operator-classes collations".split(),
                "allowed_dependency_sources": "authenticated-side-effect-free-pinned-public-schema-routines authenticated-side-effect-free-pinned-postgresql-16-built-ins".split(),
                "exact_callable_allowlist_required": True,
                "side_effectful_dependency_permitted": False,
                "exact_transitive_closure_required": True,
                "unknown_or_unobserved_dependency_permitted": False,
            },
            "competing_sessions": {
                "requirement_source": "sanitizer_policy.target.require_no_competing_sessions",
                "require_no_competing_sessions": sanitizer["target"][
                    "require_no_competing_sessions"
                ],
                "recheck_source": "sanitizer_policy.execution_contract.future_transaction_requirements.recheck_no_competing_sessions_before_commit",
                "recheck_no_competing_sessions_before_commit": future[
                    "recheck_no_competing_sessions_before_commit"
                ],
                "fresh_verification_source": "sanitizer_policy.execution_contract.future_transaction_requirements.fresh_connection_post_commit_verification",
                "fresh_connection_post_commit_verification": future[
                    "fresh_connection_post_commit_verification"
                ],
                "admission_fence": admission_fence,
            },
            "execution_surface": {
                "implemented": False,
                "adapter_owned_fixed_statement_grammar": statement_grammar,
                "large_object_safety": large_object_safety,
                "prepared_transaction_safety": prepared_transaction_safety,
            },
            "revalidation_windows_required": [
                "privileges.server_wide_authorization_stability",
                "privileges.transaction_compatibility.exclusive_ddl_window",
            ],
            "revalidate_immediately_before_sanitizer_commit": True,
        },
        # fmt: on
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
                "acl_manageable_custom_enums": {
                    "types": list(_APPLICATION_CUSTOM_TYPES),
                    "expected_type_count": 14,
                    "usage": [],
                    "direct_grants_permitted": False,
                    "effective_privileges_must_match_exactly": True,
                    "public_role_privileges_permitted": False,
                    "public_role_effective_privileges_included_in_verification": True,
                    "grant_options_permitted": False,
                },
                "relation_row_types": {
                    "expected_type_count": 63,
                    "relation_name_sources": [
                        "schema.table_sets.application.tables",
                        "schema.table_sets.control.tables",
                    ],
                    "independently_acl_manageable": False,
                    "acl_mutation_must_not_be_used_to_establish_boundary": True,
                    "effective_zero_usage_required": False,
                    "has_type_privilege_usage_is_intrinsic": True,
                },
                "generated_array_types": {
                    "expected_type_count": 77,
                    "exact_name_rule": "underscore-prefixed-element-type-name",
                    "independently_acl_manageable": False,
                    "acl_mutation_supported": False,
                    "enum_element_arrays": {
                        "expected_type_count": 14,
                        "element_type_source": (
                            "privileges.types.public_schema."
                            "acl_manageable_custom_enums.types"
                        ),
                        "effective_usage_must_equal_element_type_effective_usage": True,
                    },
                    "relation_row_element_arrays": {
                        "expected_type_count": 63,
                        "element_type_source": (
                            "privileges.types.public_schema.relation_row_types"
                        ),
                        "has_type_privilege_usage_is_intrinsic": True,
                    },
                },
                "catalog_partition_is_exact_disjoint_and_exhaustive": True,
                "unlisted_type_categories_permitted": False,
                "unlisted_type_privileges_permitted": False,
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
            "execution_surface_source": (
                "privileges.write_path_effects.execution_surface.large_object_safety"
            ),
        },
        "default_privileges": {
            "entries": [],
            "explicit_default_privilege_entries_must_be_empty": True,
            "all_owners_and_global_and_public_schema_entries_included": True,
            "public_role_entries_included": True,
            "existing_object_public_routine_execute_and_type_usage_must_be_reconciled": True,
            "future_user_object_creation_during_capability_window_permitted": False,
            "implicit_public_defaults_cannot_materialize_during_capability_window": True,
            "requires_authenticated_exclusive_ddl_window": True,
            "exclusive_ddl_window_source": (
                "privileges.transaction_compatibility.exclusive_ddl_window"
            ),
            "server_wide_authorization_stability_source": (
                "privileges.server_wide_authorization_stability"
            ),
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
            "control_table_write_privilege_permitted": False,
            "set_role_permitted": False,
            "exclusive_ddl_window": {
                "protocol": "postgresql-session-level-advisory-lock-v1",
                "key_source_utf8": _ADVISORY_LOCK_KEY_SOURCE,
                "key_source_sha256": f"sha256:{_ADVISORY_LOCK_KEY_SHA256}",
                "signed_int32_key_parts": list(_ADVISORY_LOCK_KEY_PARTS),
                "participants_required": [
                    "every-schema-migration-path",
                    "sanitizer-transaction-adapter",
                ],
                "blocking_acquisition_required": True,
                "dedicated_session_required": True,
                "connection_pooling_permitted": False,
                "acquire_before_serializable_transaction": True,
                "revision_and_schema_revalidation_after_lock_before_mutation": True,
                "retain_same_session_lock_through_commit_or_rollback": True,
                "unlock_and_close_session_required": True,
                "fail_closed_if_lock_state_uncertain": True,
                "access_share_lock_is_exclusion_proof": False,
                "protects_default_acl_and_no_future_object_assumptions": True,
                "all_migration_paths_cooperate": False,
                "sanitizer_adapter_cooperates": False,
                "implementation_authenticated": False,
            },
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
        or contract.get("activation_blockers") != list(_ACTIVATION_BLOCKERS)
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
