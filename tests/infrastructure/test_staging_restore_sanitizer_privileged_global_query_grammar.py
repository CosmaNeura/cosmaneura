from __future__ import annotations

import ast
import copy
import hashlib
from pathlib import Path
from typing import Any, cast

import pytest

from scripts import (
    staging_restore_sanitizer_privileged_global_observer_contract as observer_contract,
)
from scripts import (
    staging_restore_sanitizer_privileged_global_query_grammar as grammar,
)

ROOT = Path(__file__).parents[2]
CONTRACT = (
    ROOT
    / "contracts/staging-rehearsal/v1/restore-sanitizer-privileged-global-query-grammar-contract.json"
)
TEMPLATES = (
    ROOT
    / "contracts/staging-rehearsal/v1/restore-sanitizer-privileged-global-query-templates.sql"
)
SCRIPT = ROOT / "scripts/staging_restore_sanitizer_privileged_global_query_grammar.py"
Failure = grammar.SanitizerPrivilegedGlobalQueryGrammarFailure


def test_contract_is_hash_pinned_partition_exact_and_dormant() -> None:
    raw = CONTRACT.read_bytes()
    contract = grammar.load_privileged_global_query_grammar_contract()

    assert hashlib.sha256(raw).hexdigest() == grammar.CONTRACT_SHA256
    assert contract["closed_world"] is True
    assert contract["upstream_policies"] == {
        "privileged_global_observer_envelope": {
            "policy_id": observer_contract.load_privileged_global_observer_contract()[
                "policy_id"
            ],
            "sha256": f"sha256:{observer_contract.CONTRACT_SHA256}",
        }
    }
    assert contract["claims"] == grammar.CLAIMS
    assert all(value is False for value in contract["claims"].values())
    assert contract["status"]["query_templates_selected"] is True
    assert contract["status"]["bootstrap_authority_selected"] is False
    assert contract["status"]["observer_implemented"] is False
    assert contract["status"]["activation_ready"] is False


def test_templates_are_exact_hash_pinned_catalog_only_and_phase_ordered() -> None:
    raw = TEMPLATES.read_bytes()
    templates = grammar._parse_templates(raw)
    contract = grammar.load_privileged_global_query_grammar_contract()

    assert hashlib.sha256(raw).hexdigest() == grammar.TEMPLATE_SET_SHA256
    assert tuple(templates) == grammar.FRAME_NAMES
    assert contract["grammar"]["server_once_exact_order"] == list(
        grammar.SERVER_ONCE_FRAME_NAMES
    )
    assert contract["grammar"]["per_database_exact_order"] == list(
        grammar.PER_DATABASE_FRAME_NAMES
    )
    assert [item["frame"] for item in contract["grammar"]["frames"]] == list(
        grammar.FRAME_NAMES
    )
    for frame, statement in templates.items():
        assert (
            hashlib.sha256(statement.encode("ascii")).hexdigest()
            == (grammar.TEMPLATE_SHA256[frame])
        )
        assert statement.startswith(("SELECT ", "WITH "))
        assert statement.endswith(";")
        assert statement.count(";") == 1


def test_sensitive_catalog_columns_and_vendor_functions_cannot_escape() -> None:
    templates = grammar._parse_templates(TEMPLATES.read_bytes())
    all_sql = "\n".join(templates.values())
    password_sql = templates["password_null_state"]

    assert password_sql.count("rolpassword") == 1
    assert "roles.rolpassword IS NULL" in password_sql
    assert "pgaadauth_list_principals" not in all_sql
    assert "subconninfo" not in all_sql
    assert "activity.query" not in all_sql
    assert "client_addr" not in all_sql
    assert "client_hostname" not in all_sql
    assert "pg_catalog.pg_shseclabel" in all_sql
    assert "labels.provider = 'pgaadauth'" in all_sql


def test_requirement_coverage_is_exact_disjoint_and_exhaustive() -> None:
    contract = grammar.load_privileged_global_query_grammar_contract()
    coverage = contract["requirement_coverage"]
    requirements = [item["requirement"] for item in coverage]
    covered_frames = {frame for item in coverage for frame in item["covered_by_frames"]}

    assert requirements == list(observer_contract.PRIVILEGED_GLOBAL_REQUIREMENTS)
    assert len(requirements) == len(set(requirements))
    assert covered_frames == set(grammar.FRAME_NAMES)
    assert set(grammar.SERVER_ONCE_FRAME_NAMES).isdisjoint(
        grammar.PER_DATABASE_FRAME_NAMES
    )


def test_acl_baseline_is_closed_world_and_delta_set_ops_are_isolated() -> None:
    baseline = grammar.load_privileged_global_query_grammar_contract()[
        "postgresql16_builtin_acl_baseline"
    ]
    sql = grammar._parse_templates(TEMPLATES.read_bytes())["system_object_acl_deltas"]

    assert baseline["postgresql_major_version"] == 16
    assert baseline["pg_init_privs_types"] == ["i", "e"]
    assert baseline["information_schema_public_usage"] is True
    assert baseline["information_schema_public_select_relations"] == list(
        grammar.INFORMATION_SCHEMA_PUBLIC_SELECT_BASELINE
    )
    assert len(grammar.INFORMATION_SCHEMA_PUBLIC_SELECT_BASELINE) == len(
        set(grammar.INFORMATION_SCHEMA_PUBLIC_SELECT_BASELINE)
    )
    assert baseline["unknown_builtin_or_extension_acl_baseline_permitted"] is False
    for relation in grammar.INFORMATION_SCHEMA_PUBLIC_SELECT_BASELINE:
        assert f"'information_schema.{relation}'" in sql

    current_only_body = sql.split("current_only_entries AS (\n", 1)[1].split(
        "\n),\nbaseline_only_entries AS", 1
    )[0]
    baseline_only_body = sql.split("baseline_only_entries AS (\n", 1)[1].split(
        "\n),\ndelta_entries AS", 1
    )[0]
    delta_body = sql.split("delta_entries AS (\n", 1)[1].split(
        "\n)\nSELECT delta_entries", 1
    )[0]
    assert current_only_body.count("\n    EXCEPT\n") == 1
    assert baseline_only_body.count("\n    EXCEPT\n") == 1
    assert "UNION" not in current_only_body
    assert "UNION" not in baseline_only_body
    assert delta_body.count("\n    UNION ALL\n") == 1
    assert "EXCEPT" not in delta_body


@pytest.mark.parametrize(
    ("path", "value"),
    (
        ("closed_world", False),
        (
            "input.caller_sql_identifiers_fragments_parameters_templates_order_catalogs_functions_bounds_output_schemas_or_bootstrap_authorities_permitted",
            True,
        ),
        ("grammar.caller_parameter_binding_permitted", True),
        ("grammar.application_schema_or_table_reads_permitted", True),
        ("grammar.raw_password_or_password_verifier_selection_permitted", True),
        ("grammar.vendor_principal_listing_function_execution_permitted", True),
        (
            "postgresql16_builtin_acl_baseline.unknown_builtin_or_extension_acl_baseline_permitted",
            True,
        ),
        ("resource_bounds.maximum_database_entries", 0),
        ("resource_bounds.retry_permitted", True),
        ("execution_envelope.disabled_database_strategy_remains_unselected", False),
        ("claims.privileged_global_observation_authenticated", True),
        ("status.bootstrap_authority_selected", True),
        ("status.live_database_calls_enabled", True),
        ("status.activation_ready", True),
    ),
)
def test_security_relevant_contract_mutations_fail_closed(
    path: str, value: object
) -> None:
    changed = copy.deepcopy(grammar.load_privileged_global_query_grammar_contract())
    owner = changed
    parts = path.split(".")
    for name in parts[:-1]:
        owner = owner[name]
    owner[parts[-1]] = value
    with pytest.raises(Failure, match="privileged_global_query_grammar.invalid"):
        grammar.validate_privileged_global_query_grammar_contract(changed)


def test_frame_hash_schema_order_and_bounds_mutations_fail_closed() -> None:
    contract = grammar.load_privileged_global_query_grammar_contract()
    variants = []
    for key, value in (
        ("sha256", "sha256:" + "0" * 64),
        ("maximum_rows", 0),
        ("phase", "caller_selected"),
    ):
        changed = copy.deepcopy(contract)
        changed["grammar"]["frames"][0][key] = value
        variants.append(changed)
    changed = copy.deepcopy(contract)
    changed["grammar"]["frames"][0]["exact_columns"].reverse()
    variants.append(changed)
    changed = copy.deepcopy(contract)
    changed["grammar"]["frames"].reverse()
    variants.append(changed)

    for document in variants:
        with pytest.raises(Failure, match="privileged_global_query_grammar.invalid"):
            grammar.validate_privileged_global_query_grammar_contract(document)


def test_unknown_missing_duplicate_nonfinite_and_malformed_contracts_refuse() -> None:
    contract = grammar.load_privileged_global_query_grammar_contract()
    unknown = copy.deepcopy(contract)
    unknown["unknown"] = True
    missing = copy.deepcopy(contract)
    del missing["claims"]
    for document in (unknown, missing, [], None):
        with pytest.raises(Failure, match="privileged_global_query_grammar.invalid"):
            grammar.validate_privileged_global_query_grammar_contract(
                cast(Any, document)
            )

    for raw in (
        b'{"schema_version":1,"schema_version":1}',
        b'{"value":NaN}',
        b"[]",
        b"\xff",
        b"",
    ):
        with pytest.raises(Failure, match="privileged_global_query_grammar.invalid"):
            grammar._parse_json(raw, expected_sha256=hashlib.sha256(raw).hexdigest())


def test_template_tampering_and_sensitive_projection_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = TEMPLATES.read_bytes()
    tampered = original.replace(
        b"(roles.rolpassword IS NULL)::boolean AS password_is_null",
        b"(roles.rolpassword::text IS NULL)::boolean AS password_is_null",
    )
    assert tampered != original
    monkeypatch.setattr(
        grammar,
        "TEMPLATE_SET_SHA256",
        hashlib.sha256(tampered).hexdigest(),
    )
    parts = grammar._FRAME_MARKER.split(
        tampered.decode("ascii")[len(grammar._TEMPLATE_HEADER) :]
    )
    statement = parts[parts.index("password_null_state") + 1].strip()
    changed_hashes = dict(grammar.TEMPLATE_SHA256)
    changed_hashes["password_null_state"] = hashlib.sha256(
        statement.encode("ascii")
    ).hexdigest()
    monkeypatch.setattr(grammar, "TEMPLATE_SHA256", changed_hashes)

    with pytest.raises(Failure, match="privileged_global_query_grammar.invalid"):
        grammar._parse_templates(tampered)


def test_upstream_digest_drift_and_unexpected_failures_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = grammar.load_privileged_global_query_grammar_contract()
    original = observer_contract.CONTRACT_SHA256
    monkeypatch.setattr(observer_contract, "CONTRACT_SHA256", "0" * 64)
    with pytest.raises(Failure, match="privileged_global_query_grammar.invalid"):
        grammar.validate_privileged_global_query_grammar_contract(contract)

    monkeypatch.setattr(observer_contract, "CONTRACT_SHA256", original)
    monkeypatch.setattr(
        grammar,
        "_read_templates",
        lambda: (_ for _ in ()).throw(RuntimeError("secret://must-not-escape")),
    )
    with pytest.raises(Failure) as captured:
        grammar.validate_privileged_global_query_grammar_contract(contract)
    assert "secret" not in repr(captured.value)


def test_module_contains_no_live_database_network_provider_or_process_surface() -> None:
    source = SCRIPT.read_text()
    tree = ast.parse(source)
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imported.isdisjoint(
        {
            "azure",
            "httpx",
            "os",
            "psycopg",
            "requests",
            "socket",
            "subprocess",
            "urllib",
        }
    )
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert calls.isdisjoint(
        {
            "connect",
            "cursor",
            "execute",
            "executemany",
            "getenv",
            "open",
            "Popen",
            "run",
            "system",
            "urlopen",
        }
    )


def test_executable_validation_remains_terminal() -> None:
    with pytest.raises(Failure, match=grammar.ACTIVATION_FAILURE):
        grammar.load_privileged_global_query_grammar_contract(executable=True)
    with pytest.raises(Failure, match="privileged_global_query_grammar.invalid"):
        grammar.load_privileged_global_query_grammar_contract(executable=cast(Any, 1))
