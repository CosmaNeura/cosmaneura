from __future__ import annotations

import ast
import copy
import hashlib
from pathlib import Path
from typing import Any, cast

import pytest

from scripts import (
    staging_restore_sanitizer_target_role_observer_contract as observer_contract,
)
from scripts import (
    staging_restore_sanitizer_target_role_query_grammar as query_grammar,
)

ROOT = Path(__file__).parents[2]
CONTRACT = (
    ROOT
    / "contracts/staging-rehearsal/v1/restore-sanitizer-target-role-query-grammar-contract.json"
)
TEMPLATES = (
    ROOT
    / "contracts/staging-rehearsal/v1/restore-sanitizer-target-role-query-templates.sql"
)
SCRIPT = ROOT / "scripts/staging_restore_sanitizer_target_role_query_grammar.py"


def test_contract_and_template_source_are_hash_pinned_selected_and_dormant() -> None:
    assert (
        hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
        == query_grammar.CONTRACT_SHA256
    )
    assert (
        hashlib.sha256(TEMPLATES.read_bytes()).hexdigest()
        == query_grammar.TEMPLATE_SET_SHA256
    )
    contract = query_grammar.load_target_role_query_grammar_contract()

    assert contract["closed_world"] is True
    assert contract["upstream_policies"] == {
        "target_role_observer_envelope": {
            "policy_id": observer_contract.load_target_role_observer_contract()[
                "policy_id"
            ],
            "sha256": f"sha256:{observer_contract.CONTRACT_SHA256}",
        }
    }
    assert contract["status"]["query_templates_selected"] is True
    assert contract["status"]["frame_schemas_selected"] is True
    assert contract["status"]["observer_implemented"] is False
    assert contract["status"]["activation_ready"] is False
    assert contract["claims"] == query_grammar.CLAIMS
    assert all(value is False for value in contract["claims"].values())


def test_templates_are_exact_single_catalog_selects_in_closed_order() -> None:
    contract = query_grammar.load_target_role_query_grammar_contract()
    templates, raw = query_grammar._read_templates()

    assert len(raw) <= query_grammar.MAXIMUM_TEMPLATE_SET_BYTES
    assert tuple(templates) == query_grammar.FRAME_NAMES
    assert contract["grammar"]["exact_order"] == list(templates)
    assert contract["grammar"]["exact_template_count"] == len(templates) == 13
    for frame, statement in templates.items():
        assert statement.startswith("SELECT ")
        assert statement.endswith(";")
        assert statement.count(";") == 1
        assert (
            hashlib.sha256(statement.encode("ascii")).hexdigest()
            == (query_grammar.TEMPLATE_SHA256[frame])
        )
        assert "pg_catalog." in statement
        assert not query_grammar._FORBIDDEN_SQL.search(
            query_grammar._STRING_LITERAL.sub("''", statement)
        )


def test_requirements_catalogs_functions_frames_and_bounds_are_exhaustive() -> None:
    contract = query_grammar.load_target_role_query_grammar_contract()
    grammar = contract["grammar"]
    coverage = contract["requirement_coverage"]

    assert [item["requirement"] for item in coverage] == list(
        observer_contract.TARGET_REQUIREMENTS
    )
    assert all(item["covered_by_frames"] for item in coverage)
    assert {frame for item in coverage for frame in item["covered_by_frames"]} == set(
        query_grammar.FRAME_NAMES
    )
    assert grammar["catalog_allowlist"] == list(query_grammar.CATALOG_ALLOWLIST)
    assert [item["signature"] for item in grammar["function_allowlist"]] == [
        signature for signature, _volatility in query_grammar.FUNCTION_ALLOWLIST
    ]
    assert [item["frame"] for item in grammar["templates"]] == list(
        query_grammar.FRAME_NAMES
    )
    assert [item["maximum_rows"] for item in grammar["templates"]] == [
        query_grammar.MAXIMUM_ROWS[frame] for frame in query_grammar.FRAME_NAMES
    ]
    assert contract["resource_bounds"] == {
        "maximum_queries": 13,
        "maximum_template_bytes": 4096,
        "maximum_rows_total": 4096,
        "maximum_row_bytes": 16384,
        "maximum_output_bytes": 8388608,
        "maximum_elapsed_seconds_per_query": 5,
        "maximum_elapsed_seconds_total": 30,
        "retry_permitted": False,
    }


def test_frame_columns_are_exact_typed_unique_and_nullability_closed() -> None:
    contract = query_grammar.load_target_role_query_grammar_contract()
    templates = contract["grammar"]["templates"]

    for template in templates:
        columns = template["exact_columns"]
        names = [column["name"] for column in columns]
        assert names
        assert len(names) == len(set(names))
        assert all(
            column["type"] in {"boolean", "int16", "int32", "int64", "text"}
            and type(column["nullable"]) is bool
            for column in columns
        )
    assert [column["name"] for column in templates[0]["exact_columns"]] == [
        "database_name",
        "session_user_name",
        "current_user_name",
        "role_oid",
        "role_name",
        "can_login",
        "is_superuser",
        "inherits_privileges",
        "can_create_role",
        "can_create_database",
        "can_replicate",
        "bypasses_row_security",
        "connection_limit",
        "session_replication_role",
    ]
    assert all(column["nullable"] is False for column in templates[0]["exact_columns"])


def test_security_relevant_contract_mutations_fail_closed() -> None:
    contract = query_grammar.load_target_role_query_grammar_contract()
    mutations: tuple[tuple[tuple[str | int, ...], object], ...] = (
        (("closed_world",), False),
        (("input", "parameter_count"), 1),
        (("grammar", "statement_class"), "SELECT_OR_CALL"),
        (("grammar", "exact_template_count"), 12),
        (("grammar", "exact_template_bytes_only"), False),
        (("grammar", "application_schema_or_table_reads_permitted"), True),
        (("grammar", "templates", 0, "maximum_rows"), 2),
        (("grammar", "templates", 0, "exact_columns", 0, "nullable"), True),
        (("resource_bounds", "retry_permitted"), True),
        (("execution_envelope", "read_only_repeatable_read_required"), False),
        (
            (
                "execution_envelope",
                "raw_rows_sql_tokens_credentials_connections_cursors_or_callbacks_"
                "escape_process_permitted",
            ),
            True,
        ),
        (("claims", "database_connection_authority"), True),
        (("status", "observer_implemented"), True),
    )

    for path, value in mutations:
        changed = copy.deepcopy(contract)
        owner: Any = changed
        for part in path[:-1]:
            owner = owner[part]
        owner[path[-1]] = value
        with pytest.raises(
            query_grammar.SanitizerTargetRoleQueryGrammarFailure,
            match="target_role_query_grammar.invalid",
        ):
            query_grammar.validate_target_role_query_grammar_contract(changed)


def test_template_tampering_unknown_frames_reordering_and_extra_sql_refuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = TEMPLATES.read_bytes()
    variants = (
        raw + b" ",
        raw.replace(b"-- frame: session_and_role", b"-- frame: unknown", 1),
        raw.replace(
            b"-- frame: session_and_role",
            b"-- frame: memberships",
            1,
        ),
        raw.replace(
            b"SELECT pg_catalog.current_database()",
            b"DELETE FROM pg_catalog.pg_roles; SELECT pg_catalog.current_database()",
            1,
        ),
        raw.replace(b"ORDER BY r.oid;", b"ORDER BY r.oid; SELECT 1;", 1),
        raw.replace(b"pg_catalog.pg_roles", b"public.patient", 1),
        raw.replace(b"-- Contract data only.", b"-- \xff", 1),
    )
    for changed in variants:
        monkeypatch.setattr(
            query_grammar,
            "TEMPLATE_SET_SHA256",
            hashlib.sha256(changed).hexdigest(),
        )
        with pytest.raises(
            query_grammar.SanitizerTargetRoleQueryGrammarFailure,
            match="target_role_query_grammar.invalid",
        ):
            query_grammar._parse_templates(changed)

    for changed in (
        raw.replace(b"pg_catalog.pg_roles", b"public.patient", 1),
        raw.replace(
            b"pg_catalog.current_database()",
            b"pg_read_file('/etc/passwd')",
            1,
        ),
    ):
        parts = query_grammar._FRAME_MARKER.split(
            changed.decode("ascii")[len(query_grammar._TEMPLATE_HEADER) :]
        )
        changed_digests = dict(query_grammar.TEMPLATE_SHA256)
        changed_digests[parts[1]] = hashlib.sha256(
            parts[2].strip().encode("ascii")
        ).hexdigest()
        monkeypatch.setattr(
            query_grammar,
            "TEMPLATE_SET_SHA256",
            hashlib.sha256(changed).hexdigest(),
        )
        monkeypatch.setattr(query_grammar, "TEMPLATE_SHA256", changed_digests)
        with pytest.raises(
            query_grammar.SanitizerTargetRoleQueryGrammarFailure,
            match="target_role_query_grammar.invalid",
        ):
            query_grammar._parse_templates(changed)


def test_upstream_drift_and_unexpected_failures_are_content_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = query_grammar.load_target_role_query_grammar_contract()
    monkeypatch.setattr(observer_contract, "CONTRACT_SHA256", "0" * 64)
    with pytest.raises(
        query_grammar.SanitizerTargetRoleQueryGrammarFailure,
        match="target_role_query_grammar.invalid",
    ):
        query_grammar.validate_target_role_query_grammar_contract(contract)

    monkeypatch.setattr(
        observer_contract,
        "CONTRACT_SHA256",
        query_grammar.OBSERVER_CONTRACT_SHA256,
    )
    monkeypatch.setattr(
        query_grammar,
        "_read_templates",
        lambda: (_ for _ in ()).throw(RuntimeError("secret://must-not-escape")),
    )
    with pytest.raises(
        query_grammar.SanitizerTargetRoleQueryGrammarFailure,
        match="target_role_query_grammar.invalid",
    ) as captured:
        query_grammar.validate_target_role_query_grammar_contract(contract)
    assert "secret" not in str(captured.value)


def test_module_contains_no_database_network_provider_or_process_surface() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
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
    with pytest.raises(
        query_grammar.SanitizerTargetRoleQueryGrammarFailure,
        match=query_grammar.ACTIVATION_FAILURE,
    ):
        query_grammar.load_target_role_query_grammar_contract(executable=True)
    with pytest.raises(
        query_grammar.SanitizerTargetRoleQueryGrammarFailure,
        match="target_role_query_grammar.invalid",
    ):
        query_grammar.load_target_role_query_grammar_contract(executable=cast(Any, 1))
