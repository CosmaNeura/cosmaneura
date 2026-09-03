from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import secrets
import urllib.parse
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/staging_server_scope_evidence.py"
POLICY = ROOT / "contracts/staging-rehearsal/v1/server-scope-evidence-policy.json"
KEY = b"server-scope-test-key-with-at-least-thirty-two-bytes"
RESOURCE_ID = (
    "/subscriptions/11111111-1111-4111-8111-111111111111/"
    "resourceGroups/staging/providers/Microsoft.DBforPostgreSQL/"
    "flexibleServers/staging-postgres"
)
REVISION = "1" * 40
POLICY_SHA256 = "2" * 64


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "standalone_staging_server_scope_evidence", SCRIPT
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def module() -> ModuleType:
    return _load_module()


def _database(
    name: str, *, template: bool = False, connect: bool = True
) -> dict[str, Any]:
    return {
        "name": name,
        "is_template": template,
        "allow_connections": connect,
        "owner": "owner-role",
        "encoding": "UTF8",
        "collation": "C",
        "ctype": "C",
        "locale_provider": "c",
        "icu_locale": None,
        "icu_rules": None,
        "collation_version": None,
        "default_tablespace": "pg_default",
        "connection_limit": -1,
    }


def _server(module: ModuleType) -> Any:
    role = ["owner-role", *([True] * 7), -1, None, True, "scram-sha-256"] + [
        "SCRAM-SHA-256$private-verifier"
    ]
    version = ["server_version_num", "160015", None, "default", "160015"] + [
        "160015",
        False,
    ]
    return module.ServerSnapshot(
        version_major=16,
        visibility={
            "pg_authid": True,
            "pg_monitor": True,
            "pg_read_all_settings": True,
        },
        surfaces={
            "databases": [
                _database("appdb"),
                _database("postgres"),
                _database("template0", template=True, connect=False),
                _database("template1", template=True),
            ],
            "roles": [role],
            "memberships": [],
            "role_settings": [],
            "database_privileges": [
                ["appdb", "owner-role", "PUBLIC", "owner-role", "CONNECT", False]
            ],
            "tablespaces": [
                ["pg_default", "owner-role", "", None, "PUBLIC", "owner-role"]
            ],
            "replication_slots": [],
            "replication_origins": [],
            "prepared_transactions": [],
            "subscriptions": [["appdb", "subscription-private", "password=secret"]],
            "parameter_privileges": [],
            "shared_security_labels": [],
            "shared_descriptions": [],
            "settings": [
                ["archive_mode", "off", None, "default", "off", "off", False],
                version,
            ],
        },
    )


def _database_surfaces(
    label: str, *, contains_user_relations: bool = False
) -> dict[str, list[Any]]:
    return {
        "schema": [{"kind": "schema", "label": label}],
        "user_relations": (
            [["public", "table-private", "r"]] if contains_user_relations else []
        ),
        "shared_dependencies": (
            [
                {
                    "dependent_type": "table",
                    "dependent_names": ["public", "table-private"],
                    "dependent_arguments": [],
                    "referenced_type": "role",
                    "referenced_names": ["owner-role"],
                    "referenced_arguments": [],
                    "dependency_type": "o",
                },
                {
                    "dependent_type": "extension",
                    "dependent_names": ["plpgsql"],
                    "dependent_arguments": [],
                    "referenced_type": "role",
                    "referenced_names": ["owner-role"],
                    "referenced_arguments": [],
                    "dependency_type": "o",
                },
                {
                    "dependent_type": "collation",
                    "dependent_names": ["public", "collation-private"],
                    "dependent_arguments": [],
                    "referenced_type": "role",
                    "referenced_names": ["owner-role"],
                    "referenced_arguments": [],
                    "dependency_type": "o",
                },
                {
                    "dependent_type": "table",
                    "dependent_names": ["public", "table-private"],
                    "dependent_arguments": [],
                    "referenced_type": "tablespace",
                    "referenced_names": ["tablespace-private"],
                    "referenced_arguments": [],
                    "dependency_type": "t",
                },
            ]
            if contains_user_relations
            else []
        ),
        "extensions": [["plpgsql", "1.0", "pg_catalog", "owner-role", False]],
        "foreign_data_wrappers": [["fdw-private", ["token=fdw-secret"]]],
        "foreign_servers": [["server-private", ["host=private.invalid"]]],
        "foreign_tables": (
            [["private", "table-private", "server-private"]]
            if contains_user_relations
            else []
        ),
        "foreign_privileges": [["server", "server-private", "user-private"]],
        "language_privileges": [["plpgsql", "PUBLIC", "USAGE"]],
        "user_mappings": [["server-private", "user-private", ["password=secret"]]],
        "publications": [["publication-private", False]],
        "publication_memberships": (
            [["publication-private", "table-private"]]
            if contains_user_relations
            else []
        ),
        "publication_namespaces": [["publication-private", "private"]],
        "subscription_state": [["subscription-private", False, 0]],
        "subscription_relations": (
            [["subscription-private", "table-private", "r"]]
            if contains_user_relations
            else []
        ),
        "security_labels": [["provider-private", "label-private"]],
        "event_triggers": [["event-private", "ddl_command_start"]],
        "large_objects": (
            [["9812", "owner-role", 0, "c2VjcmV0"]] if contains_user_relations else []
        ),
    }


def _databases() -> dict[str, dict[str, list[Any]]]:
    return {
        "appdb": _database_surfaces("app", contains_user_relations=True),
        "postgres": _database_surfaces("maintenance"),
        "template1": _database_surfaces("template"),
    }


def _policy(
    module: ModuleType,
    server: Any,
    databases: dict[str, dict[str, list[Any]]],
    *,
    primary_database: str = "appdb",
) -> dict[str, Any]:
    policy = json.loads(POLICY.read_text())
    policy["activation_ready"] = True
    policy["source"].update(
        resource_id=RESOURCE_ID,
        revision=REVISION,
        host="test.postgres.database.azure.com",
        maintenance_database="postgres",
        primary_database=primary_database,
    )
    policy["hmac"]["key_version"] = "test-v1"
    policy["required_settings"] = sorted(row[0] for row in server.surfaces["settings"])
    policy["expected_server_surfaces"] = {
        name: module._pin_json(
            module._surface_pin(KEY, f"server:{name}", server.surfaces[name])
        )
        for name in module.SERVER_SURFACES
    }
    expected = []
    for record in server.surfaces["databases"]:
        identity = module._identity_hmac(KEY, "database", record["name"])
        if not record["allow_connections"]:
            expected.append(
                {
                    "identity_hmac": identity,
                    "is_template": record["is_template"],
                    "allow_connections": False,
                    "status": "connections_disabled",
                    "surfaces": None,
                }
            )
            continue
        expected.append(
            {
                "identity_hmac": identity,
                "is_template": record["is_template"],
                "allow_connections": True,
                "status": (
                    "primary_inventory"
                    if record["name"] == primary_database
                    else "empty"
                ),
                "surfaces": {
                    name: module._pin_json(
                        module._surface_pin(
                            KEY,
                            f"database:{identity}:{name}",
                            databases[record["name"]][name],
                        )
                    )
                    for name in module.DATABASE_SURFACES
                },
            }
        )
    policy["expected_databases"] = expected
    return policy


def _clock() -> Any:
    values = iter(
        [
            datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
            datetime(2026, 9, 3, 12, 1, tzinfo=UTC),
        ]
    )
    return lambda: next(values)


def _collect(
    module: ModuleType, policy: dict[str, Any], adapter: Any
) -> dict[str, Any]:
    return module.collect_evidence(
        policy,
        POLICY_SHA256,
        adapter,
        KEY,
        source_resource_id=RESOURCE_ID,
        source_revision=REVISION,
        key_version="test-v1",
        clock=_clock(),
    )


def test_collects_only_keyed_aggregate_evidence(module: ModuleType) -> None:
    server = _server(module)
    databases = _databases()
    policy = _policy(module, server, databases)
    evidence = _collect(module, policy, module.SnapshotAdapter(server, databases))

    assert evidence["pass"] is True
    assert evidence["server_version_major"] == 16
    assert len(evidence["databases"]) == 4
    assert {item["status"] for item in evidence["databases"]} == {
        "primary_inventory",
        "empty",
        "connections_disabled",
    }
    assert evidence["primary_database_identity_hmac"] == module._identity_hmac(
        KEY, "database", "appdb"
    )
    rendered = json.dumps(evidence)
    for raw_value in (
        "appdb",
        "postgres",
        "template0",
        "template1",
        "owner-role",
        "plpgsql",
        "archive_mode",
        RESOURCE_ID,
        "fdw-secret",
        "password=secret",
        "private-verifier",
        "publication-private",
        "collation-private",
        "tablespace-private",
        "c2VjcmV0",
    ):
        assert raw_value not in rendered
    assert len(evidence["evidence_hmac"]) == 64
    assert evidence["capture_started_at"] == "2026-09-03T12:00:00Z"


def test_non_primary_user_data_and_disabled_non_template_fail_closed(
    module: ModuleType,
) -> None:
    server = _server(module)
    databases = _databases()
    policy = _policy(module, server, databases)
    databases["postgres"]["user_relations"].append(
        ["public", "restored_patient_rows", "r"]
    )
    with pytest.raises(module.EvidenceFailure, match="database.non_primary_not_empty"):
        _collect(module, policy, module.SnapshotAdapter(server, databases))

    changed_surfaces = deepcopy(server.surfaces)
    changed_surfaces["databases"][0]["allow_connections"] = False
    changed_server = replace(server, surfaces=changed_surfaces)
    policy = _policy(module, server, _databases())
    policy["expected_server_surfaces"]["databases"] = module._pin_json(
        module._surface_pin(KEY, "server:databases", changed_surfaces["databases"])
    )
    with pytest.raises(module.EvidenceFailure, match="database.disabled_non_template"):
        _collect(
            module,
            policy,
            module.SnapshotAdapter(changed_server, _databases()),
        )


def test_policy_binds_exactly_one_primary_database(module: ModuleType) -> None:
    server = _server(module)
    databases = _databases()
    policy = _policy(module, server, databases)
    policy["source"]["primary_database"] = "postgres"
    with pytest.raises(
        module.EvidenceFailure, match="database.primary_binding_mismatch"
    ):
        _collect(module, policy, module.SnapshotAdapter(server, databases))

    policy = _policy(module, server, databases)
    postgres_identity = module._identity_hmac(KEY, "database", "postgres")
    postgres = next(
        item
        for item in policy["expected_databases"]
        if item["identity_hmac"] == postgres_identity
    )
    postgres.update(
        is_template=False,
        allow_connections=False,
        status="connections_disabled",
        surfaces=None,
    )
    with pytest.raises(module.EvidenceFailure, match="policy.invalid_database"):
        module.validate_policy(policy)

    policy = _policy(module, server, databases)
    postgres = next(
        item
        for item in policy["expected_databases"]
        if item["identity_hmac"] == postgres_identity
    )
    postgres["surfaces"]["user_relations"]["count"] = 1
    with pytest.raises(module.EvidenceFailure, match="policy.invalid_database"):
        module.validate_policy(policy)


def test_rewrite_rule_definition_changes_schema_pin(module: ModuleType) -> None:
    server = _server(module)
    databases = _databases()
    policy = _policy(module, server, databases)
    databases["appdb"]["schema"].append(
        {
            "kind": "rewrite_rule",
            "schema": "public",
            "relation": "patient_rows",
            "name": "discard_insert",
            "enabled": "O",
            "is_instead": True,
            "event": "3",
            "definition": "CREATE RULE discard_insert AS ON INSERT DO INSTEAD NOTHING",
        }
    )

    with pytest.raises(module.EvidenceFailure, match="database.schema_mismatch"):
        _collect(module, policy, module.SnapshotAdapter(server, databases))


@pytest.mark.parametrize("surface", list(_load_module().SERVER_SURFACES))
def test_every_server_surface_is_closed_world(module: ModuleType, surface: str) -> None:
    server = _server(module)
    databases = _databases()
    policy = _policy(module, server, databases)
    mutated = deepcopy(server.surfaces)
    mutated[surface] = [*mutated[surface], ["unexpected"]]
    changed = replace(server, surfaces=mutated)

    with pytest.raises(module.EvidenceFailure, match=f"server.{surface}_mismatch"):
        _collect(module, policy, module.SnapshotAdapter(changed, databases))


@pytest.mark.parametrize("surface", list(_load_module().DATABASE_SURFACES))
def test_every_database_surface_is_closed_world(
    module: ModuleType, surface: str
) -> None:
    server = _server(module)
    databases = _databases()
    policy = _policy(module, server, databases)
    databases["appdb"][surface].append(["unexpected"])

    with pytest.raises(module.EvidenceFailure, match=f"database.{surface}_mismatch"):
        _collect(module, policy, module.SnapshotAdapter(server, databases))


def test_unknown_missing_and_changed_databases_fail_closed(module: ModuleType) -> None:
    server = _server(module)
    databases = _databases()
    policy = _policy(module, server, databases)

    extra = deepcopy(server.surfaces)
    extra["databases"].append(
        {
            **extra["databases"][0],
            "name": "unknown",
        }
    )
    extra_server = replace(server, surfaces=extra)
    # Re-pin only the global surface so the per-database allowlist is the blocker.
    policy["expected_server_surfaces"]["databases"] = module._pin_json(
        module._surface_pin(KEY, "server:databases", extra["databases"])
    )
    with pytest.raises(module.EvidenceFailure, match="database.unknown"):
        _collect(module, policy, module.SnapshotAdapter(extra_server, databases))

    missing = deepcopy(server.surfaces)
    missing["databases"] = missing["databases"][:-1]
    missing_server = replace(server, surfaces=missing)
    policy = _policy(module, server, databases)
    policy["expected_server_surfaces"]["databases"] = module._pin_json(
        module._surface_pin(KEY, "server:databases", missing["databases"])
    )
    with pytest.raises(module.EvidenceFailure, match="database.missing"):
        _collect(module, policy, module.SnapshotAdapter(missing_server, databases))


@pytest.mark.parametrize(
    ("record_index", "referenced_name"),
    [
        (1, "changed-extension-owner"),
        (2, "changed-collation-owner"),
        (3, "changed-tablespace"),
    ],
)
def test_shared_owner_and_tablespace_dependency_drift_changes_pin(
    module: ModuleType, record_index: int, referenced_name: str
) -> None:
    server = _server(module)
    databases = _databases()
    policy = _policy(module, server, databases)
    databases["appdb"]["shared_dependencies"][record_index]["referenced_names"] = [
        referenced_name
    ]

    with pytest.raises(
        module.EvidenceFailure, match="database.shared_dependencies_mismatch"
    ):
        _collect(module, policy, module.SnapshotAdapter(server, databases))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("locale_provider", "i"),
        ("icu_locale", "en-US"),
        ("icu_rules", "private-rules"),
        ("collation_version", "changed"),
        ("default_tablespace", "other-space"),
    ],
)
def test_database_locale_and_tablespace_are_pinned(
    module: ModuleType, field: str, value: str
) -> None:
    server = _server(module)
    policy = _policy(module, server, _databases())
    changed = deepcopy(server.surfaces)
    changed["databases"][0][field] = value
    with pytest.raises(module.EvidenceFailure, match="server.databases_mismatch"):
        _collect(
            module,
            policy,
            module.SnapshotAdapter(replace(server, surfaces=changed), _databases()),
        )


def test_enabled_database_read_failure_and_visibility_are_blockers(
    module: ModuleType,
) -> None:
    server = _server(module)
    databases: dict[str, Any] = _databases()
    policy = _policy(module, server, databases)
    databases["appdb"] = module.DatabaseUnavailable()
    with pytest.raises(module.EvidenceFailure, match="database.read_blocked"):
        _collect(module, policy, module.SnapshotAdapter(server, databases))

    hidden = replace(server, visibility={**server.visibility, "pg_authid": False})
    with pytest.raises(module.EvidenceFailure, match="server.incomplete_visibility"):
        _collect(module, policy, module.SnapshotAdapter(hidden, _databases()))


def test_server_change_during_capture_is_rejected(module: ModuleType) -> None:
    server = _server(module)
    databases = _databases()
    policy = _policy(module, server, databases)

    class UnstableAdapter(module.SnapshotAdapter):
        calls = 0

        def server_snapshot(self, required_settings: list[str]) -> Any:
            self.calls += 1
            value = super().server_snapshot(required_settings)
            if self.calls == 2:
                changed = deepcopy(value.surfaces)
                changed["roles"] = [*changed["roles"], ["late-role"]]
                return replace(value, surfaces=changed)
            return value

    with pytest.raises(module.EvidenceFailure, match="server.changed_during_capture"):
        _collect(module, policy, UnstableAdapter(server, databases))


def test_database_change_between_capture_passes_is_rejected(module: ModuleType) -> None:
    server, databases = _server(module), _databases()
    policy = _policy(module, server, databases)

    class UnstableAdapter(module.SnapshotAdapter):
        calls = 0

        def database_snapshot(self, database: str) -> Any:
            value = super().database_snapshot(database)
            if database == "appdb":
                self.calls += 1
                if self.calls == 2:
                    value["schema"].append({"kind": "late-change"})
            return value

    with pytest.raises(module.EvidenceFailure, match="database.changed_during_capture"):
        _collect(module, policy, UnstableAdapter(server, databases))


def test_source_revision_key_and_capture_window_are_bound(module: ModuleType) -> None:
    server = _server(module)
    databases = _databases()
    policy = _policy(module, server, databases)
    adapter = module.SnapshotAdapter(server, databases)
    kwargs = {
        "policy": policy,
        "policy_sha256": POLICY_SHA256,
        "adapter": adapter,
        "key": KEY,
        "source_resource_id": RESOURCE_ID,
        "source_revision": REVISION,
        "key_version": "test-v1",
    }
    for field, value, code in (
        ("source_resource_id", RESOURCE_ID + "/other", "source.resource_mismatch"),
        ("source_revision", "3" * 40, "source.revision_mismatch"),
        ("key_version", "other", "hmac_key.version_mismatch"),
        ("policy_sha256", "bad", "policy.hash_invalid"),
    ):
        changed = dict(kwargs)
        changed[field] = value
        with pytest.raises(module.EvidenceFailure, match=code):
            module.collect_evidence(**changed)

    times = iter(
        [
            datetime(2026, 9, 3, tzinfo=UTC),
            datetime(2026, 9, 3, tzinfo=UTC)
            + timedelta(seconds=module.MAXIMUM_CAPTURE_SECONDS + 1),
        ]
    )
    with pytest.raises(module.EvidenceFailure, match="capture.invalid_duration"):
        module.collect_evidence(**kwargs, clock=lambda: next(times))


def test_monotonic_deadline_and_resource_limits_fail_closed(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _server(module)
    databases = _databases()
    policy = _policy(module, server, databases)
    ticks = iter([0.0, module.MAXIMUM_CAPTURE_SECONDS])
    with pytest.raises(module.EvidenceFailure, match="capture.deadline_exceeded"):
        module.collect_evidence(
            policy,
            POLICY_SHA256,
            module.SnapshotAdapter(server, databases),
            KEY,
            source_resource_id=RESOURCE_ID,
            source_revision=REVISION,
            key_version="test-v1",
            monotonic=lambda: next(ticks),
        )

    with monkeypatch.context() as limits:
        limits.setattr(module, "MAXIMUM_BYTES_PER_SURFACE", 3)
        with pytest.raises(module.EvidenceFailure, match="catalog.surface_too_large"):
            module._surface_pin(KEY, "bounded", ["four"])
    monkeypatch.setattr(module, "MAXIMUM_DATABASES", 3)
    policy["expected_databases"] = policy["expected_databases"][:3]
    with pytest.raises(module.EvidenceFailure, match="database.count_exceeded"):
        _collect(module, policy, module.SnapshotAdapter(server, databases))


def test_live_row_iteration_stops_before_materializing_over_limit(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Cursor:
        def __enter__(self) -> Any:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def execute(self, query: str, parameters: Any) -> None:
            del query, parameters

        def __iter__(self) -> Any:
            return iter(((1,), (2,), (3,)))

    class Connection:
        class Result:
            def __init__(self, bounds: tuple[int, int, int]) -> None:
                self.bounds = bounds

            def fetchone(self) -> tuple[int, int, int]:
                return self.bounds

        def __init__(self, bounds: tuple[int, int, int] = (2, 2, 1)) -> None:
            self.bounds = bounds
            self.queries: list[str] = []
            self.cursor_calls = 0

        def execute(self, query: str, parameters: Any) -> Any:
            del parameters
            self.queries.append(query)
            return self.Result(self.bounds)

        def cursor(self, *, name: str) -> Cursor:
            assert name.startswith("server_scope_")
            self.cursor_calls += 1
            return Cursor()

    adapter = object.__new__(module.LivePostgresAdapter)
    adapter.start_capture(60)
    monkeypatch.setattr(module, "MAXIMUM_RECORDS_PER_SURFACE", 2)
    bounded_connection = Connection()
    with pytest.raises(module.EvidenceFailure, match="catalog.resource_limit"):
        adapter._rows(bounded_connection, "SELECT bounded", ())
    assert "row_to_json" in bounded_connection.queries[-1]
    assert "octet_length" in bounded_connection.queries[-1]
    assert "pg_column_size" not in bounded_connection.queries[-1]
    rendered_limit = module.MAXIMUM_ROW_BYTES // module.MAXIMUM_JSON_ASCII_EXPANSION
    huge = (1, rendered_limit + 1, rendered_limit + 1)
    huge_connection = Connection(huge)
    with pytest.raises(module.EvidenceFailure, match="catalog.resource_limit"):
        adapter._rows(huge_connection, "SELECT huge", ())
    assert huge_connection.cursor_calls == 0


def test_live_row_iteration_rejects_large_canonical_row_after_small_preflight(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Cursor:
        itersize = 0

        def __enter__(self) -> Any:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def execute(self, query: str, parameters: Any) -> None:
            del query, parameters

        def __iter__(self) -> Any:
            return iter((("compressible" * 10,),))

    class Result:
        def fetchone(self) -> tuple[int, int, int]:
            return (1, 1, 1)

    class Connection:
        def execute(self, query: str, parameters: Any) -> Result:
            del query, parameters
            return Result()

        def cursor(self, *, name: str) -> Cursor:
            assert name.startswith("server_scope_")
            return Cursor()

    adapter = object.__new__(module.LivePostgresAdapter)
    adapter.start_capture(60)
    monkeypatch.setattr(module, "MAXIMUM_ROW_BYTES", 32)

    with pytest.raises(module.EvidenceFailure, match="catalog.resource_limit"):
        adapter._rows(Connection(), "SELECT compressible", ())
    assert adapter._total_records == 0
    assert adapter._total_bytes == 0


def test_database_url_comes_only_from_fixed_environment(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(module.DATABASE_URL_ENVIRONMENT, raising=False)
    with pytest.raises(module.EvidenceFailure, match="database_url.missing_or_invalid"):
        module.database_url_from_environment()
    monkeypatch.setenv(
        module.DATABASE_URL_ENVIRONMENT, "postgresql://secret.invalid/db"
    )
    assert module.database_url_from_environment() == "postgresql://secret.invalid/db"


def _clear_libpq_environment(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    from psycopg import pq

    for option in pq.Conninfo.get_defaults():
        if option.envvar:
            monkeypatch.delenv(option.envvar.decode("ascii"), raising=False)
    for name in module.ADDITIONAL_CONNECTION_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def _server_scope_dsn(host: str) -> str:
    return (
        f"postgresql://scope-reader:local-test-only@{host}:5432/postgres"
        "?sslmode=verify-full&sslrootcert=system"
    )


@pytest.mark.parametrize(
    ("malicious_dsn", "error_code"),
    [
        ("{base}&hostaddr=203.0.113.9", "database_url.unsupported_parameter"),
        ("{base}&service=attacker", "database_url.unsupported_parameter"),
        (
            (
                "host={host} port=5432 dbname=postgres user=scope-reader "
                "password=local-test-only sslmode=verify-full sslrootcert=system "
                "servicefile=/tmp/attacker.conf"
            ),
            "database_url.invalid",
        ),
        (
            (
                "postgresql://scope-reader:local-test-only@{host}:5432,"
                "attacker.invalid:5432/postgres"
                "?sslmode=verify-full&sslrootcert=system"
            ),
            "database_url.invalid",
        ),
        (
            "{base}&options=-c%20search_path%3Dattacker",
            "database_url.unsupported_parameter",
        ),
        ("{base}&sslcert=/tmp/attacker.pem", "database_url.unsupported_parameter"),
        ("{base}&sslcertmode=require", "database_url.unsupported_parameter"),
        (
            (
                "postgresql://scope-reader:local-test-only@{host}:5432/postgres"
                "?sslmode=require&sslrootcert=system"
            ),
            "database_url.tls_required",
        ),
        (
            (
                "postgresql://scope-reader:local-test-only@{host}:5432/postgres"
                "?sslmode=verify-full&sslrootcert=/tmp/attacker.pem"
            ),
            "database_url.tls_required",
        ),
        (
            (
                "postgresql://scope-reader:local-test-only@{host}:5432/postgres"
                "?sslmode=verify-full"
            ),
            "database_url.tls_required",
        ),
    ],
)
def test_live_adapter_rejects_routing_indirection_and_tls_overrides(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    malicious_dsn: str,
    error_code: str,
) -> None:
    _clear_libpq_environment(module, monkeypatch)
    policy = json.loads(POLICY.read_text())
    host = policy["source"]["host"]
    rendered = malicious_dsn.format(base=_server_scope_dsn(host), host=host)

    with pytest.raises(module.EvidenceFailure, match=error_code) as captured:
        module.LivePostgresAdapter(rendered, policy)
    assert "local-test-only" not in str(captured.value)


@pytest.mark.parametrize(
    "invalid_host",
    [
        "good.postgres.database.azure.com,attacker.invalid",
        " good.postgres.database.azure.com",
        "good.postgres.database.azure.com ",
        "/var/run/postgresql",
        "GOOD.postgres.database.azure.com",
    ],
)
def test_policy_host_is_one_canonical_azure_fqdn(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    invalid_host: str,
) -> None:
    _clear_libpq_environment(module, monkeypatch)
    policy = json.loads(POLICY.read_text())
    policy["source"]["host"] = invalid_host
    with pytest.raises(module.EvidenceFailure, match="policy.invalid_source"):
        module.validate_policy(policy)
    if "," in invalid_host:
        dsn = (
            f"host={invalid_host} port=5432 dbname=postgres user=scope-reader "
            "password=local-test-only sslmode=verify-full sslrootcert=system"
        )
        with pytest.raises(module.EvidenceFailure, match="policy.invalid_source"):
            module.LivePostgresAdapter(dsn, policy)


def test_live_adapter_accepts_only_canonical_azure_and_local_parameters(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_libpq_environment(module, monkeypatch)
    policy = json.loads(POLICY.read_text())
    host = policy["source"]["host"]
    adapter = module.LivePostgresAdapter(_server_scope_dsn(host), policy)
    assert adapter._connection_parameters == {
        "host": host,
        "port": 5432,
        "user": "scope-reader",
        "password": "local-test-only",
        "sslmode": "verify-full",
        "sslcertmode": "disable",
        "gssencmode": "disable",
        "sslrootcert": "system",
    }
    assert not hasattr(adapter, "_dsn")

    keyword_dsn = (
        f"host={host} port=5432 dbname=postgres user=scope-reader "
        "password=local-test-only sslmode=verify-full sslrootcert=system"
    )
    module.LivePostgresAdapter(keyword_dsn, policy)

    local_policy = deepcopy(policy)
    local_policy["source"].update(host="localhost", maintenance_database="test")
    local = module.LivePostgresAdapter(
        "postgresql://local:local@localhost:5432/test",
        local_policy,
        allow_local=True,
    )
    assert local._connection_parameters == {
        "host": "localhost",
        "port": 5432,
        "user": "local",
        "password": "local",
        "sslmode": "disable",
        "sslcertmode": "disable",
        "gssencmode": "disable",
    }


def test_connect_uses_only_sanitized_parameters(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    import psycopg

    _clear_libpq_environment(module, monkeypatch)
    policy = json.loads(POLICY.read_text())
    host = policy["source"]["host"]
    adapter = module.LivePostgresAdapter(_server_scope_dsn(host), policy)
    marker = object()
    captured: dict[str, Any] = {}

    def fake_connect(*args: Any, **kwargs: Any) -> object:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return marker

    monkeypatch.setattr(psycopg, "connect", fake_connect)
    assert adapter._connect("another-database") is marker
    assert captured["args"] == ()
    assert captured["kwargs"] == {
        "host": host,
        "port": 5432,
        "user": "scope-reader",
        "password": "local-test-only",
        "sslmode": "verify-full",
        "sslcertmode": "disable",
        "gssencmode": "disable",
        "sslrootcert": "system",
        "dbname": "another-database",
        "connect_timeout": 10,
    }


@pytest.mark.parametrize(
    "environment_name", ["PGHOSTADDR", "PGSERVICEFILE", "SSL_CERT_FILE"]
)
def test_live_adapter_rejects_libpq_and_tls_environment_overrides(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    environment_name: str,
) -> None:
    _clear_libpq_environment(module, monkeypatch)
    policy = json.loads(POLICY.read_text())
    monkeypatch.setenv(environment_name, "/tmp/attacker-controlled")

    with pytest.raises(
        module.EvidenceFailure, match="database_url.environment_override"
    ):
        module.LivePostgresAdapter(_server_scope_dsn(policy["source"]["host"]), policy)


def test_connect_rechecks_environment_before_using_credentials(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    import psycopg

    _clear_libpq_environment(module, monkeypatch)
    policy = json.loads(POLICY.read_text())
    adapter = module.LivePostgresAdapter(
        _server_scope_dsn(policy["source"]["host"]), policy
    )
    called = False

    def fake_connect(**_kwargs: Any) -> object:
        nonlocal called
        called = True
        return object()

    monkeypatch.setattr(psycopg, "connect", fake_connect)
    monkeypatch.setenv("PGHOSTADDR", "203.0.113.9")
    with pytest.raises(
        module.EvidenceFailure, match="database_url.environment_override"
    ):
        adapter._connect("postgres")
    assert called is False


def test_committed_policy_is_hash_pinned_and_deliberately_dormant(
    module: ModuleType,
) -> None:
    assert hashlib.sha256(POLICY.read_bytes()).hexdigest() == (
        module.CANONICAL_POLICY_SHA256
    )
    with pytest.raises(module.EvidenceFailure, match="policy.unconfigured"):
        module.read_policy()


def test_hmac_key_file_must_be_private(module: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "evidence.key"
    path.write_bytes(KEY)
    path.chmod(0o600)
    assert module.read_hmac_key(path) == KEY
    path.chmod(0o644)
    with pytest.raises(module.EvidenceFailure, match="hmac_key.unsafe"):
        module.read_hmac_key(path)


def test_live_adapter_enumerates_disposable_databases_and_detects_mutations(
    module: ModuleType,
) -> None:
    import psycopg
    from psycopg import sql

    base_url = os.environ.get("TEST_DATABASE_URL")
    if not base_url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    try:
        parsed = urllib.parse.urlsplit(base_url)
        valid_port = parsed.port is None or 1 <= parsed.port <= 65535
    except ValueError:
        pytest.fail(
            "TEST_DATABASE_URL is not a valid local PostgreSQL URL", pytrace=False
        )
    if (
        parsed.scheme != "postgresql"
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or not valid_port
        or parsed.path != "/test"
    ):
        pytest.fail(
            "TEST_DATABASE_URL is not a valid local PostgreSQL URL", pytrace=False
        )
    local_policy = json.loads(POLICY.read_text())
    local_policy["source"].update(
        host=parsed.hostname,
        port=parsed.port or 5432,
        maintenance_database="test",
    )
    adapter = module.LivePostgresAdapter(base_url, local_policy, allow_local=True)

    def local_connection_parameters(database: str) -> dict[str, Any]:
        return {
            **adapter._connection_parameters,
            "dbname": database,
            "connect_timeout": 10,
        }

    first = f"scope_a_{secrets.token_hex(6)}"
    second = f"scope_b_{secrets.token_hex(6)}"
    disabled = f"scope_disabled_{secrets.token_hex(6)}"
    role = f"scope_role_{secrets.token_hex(6)}"
    created: list[str] = []
    role_created = False
    try:
        with psycopg.connect(
            **local_connection_parameters("test"), autocommit=True
        ) as admin:
            for name in (first, second, disabled):
                admin.execute(
                    sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(
                        sql.Identifier(name)
                    )
                )
                created.append(name)
            admin.execute(
                sql.SQL("ALTER DATABASE {} ALLOW_CONNECTIONS false").format(
                    sql.Identifier(disabled)
                )
            )
        settings = ["server_version_num", "wal_level"]
        server_before = adapter.server_snapshot(settings)
        names = {record["name"] for record in server_before.surfaces["databases"]}
        assert {first, second, disabled, "template0"} <= names
        disabled_record = next(
            record
            for record in server_before.surfaces["databases"]
            if record["name"] == disabled
        )
        disabled_metadata = module._database_metadata(disabled_record)
        with pytest.raises(
            module.EvidenceFailure, match="database.disabled_non_template"
        ):
            module._database_scope_status(
                disabled_metadata[0],
                is_template=disabled_metadata[1],
                allow_connections=disabled_metadata[2],
                primary_database=first,
            )
        first_before = adapter.database_snapshot(first)
        second_before = adapter.database_snapshot(second)
        assert set(first_before) == set(module.DATABASE_SURFACES)
        assert set(second_before) == set(module.DATABASE_SURFACES)

        with psycopg.connect(
            **local_connection_parameters("test"), autocommit=True
        ) as admin:
            admin.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'local-test-only'").format(
                    sql.Identifier(role)
                )
            )
        role_created = True
        with psycopg.connect(
            **local_connection_parameters(first), autocommit=True
        ) as connection:
            connection.execute("CREATE TABLE public.server_scope_probe (id integer)")
            connection.execute(
                "CREATE VIEW public.scope_view WITH "
                "(security_barrier=true,security_invoker=true) AS "
                "SELECT id FROM public.server_scope_probe"
            )
            connection.execute(
                sql.SQL("GRANT SELECT (id) ON public.server_scope_probe TO {}").format(
                    sql.Identifier(role)
                )
            )
            connection.execute("CREATE FOREIGN DATA WRAPPER scope_fdw")
            connection.execute(
                "CREATE SERVER scope_server FOREIGN DATA WRAPPER scope_fdw "
                "OPTIONS (host 'private.invalid')"
            )
            connection.execute(
                "CREATE FOREIGN TABLE public.scope_foreign "
                "(id integer OPTIONS (column_name 'remote_id')) SERVER scope_server "
                "OPTIONS (table_name 'remote')"
            )
            for target in (
                "FOREIGN DATA WRAPPER scope_fdw",
                "FOREIGN SERVER scope_server",
                "LANGUAGE plpgsql",
            ):
                connection.execute(
                    sql.SQL("GRANT USAGE ON " + target + " TO {}").format(
                        sql.Identifier(role)
                    )
                )
            connection.execute(
                "CREATE PUBLICATION scope_schema_pub FOR TABLES IN SCHEMA public"
            )
            connection.execute("SELECT pg_catalog.lo_create(9812)")
        first_without_rule = adapter.database_snapshot(first)
        with psycopg.connect(
            **local_connection_parameters(first), autocommit=True
        ) as connection:
            connection.execute(
                "CREATE RULE server_scope_no_insert AS ON INSERT "
                "TO public.server_scope_probe DO INSTEAD NOTHING"
            )
        first_after = adapter.database_snapshot(first)
        assert module._surface_pin(KEY, "rule", first_without_rule["schema"]) != (
            module._surface_pin(KEY, "rule", first_after["schema"])
        )
        with psycopg.connect(
            **local_connection_parameters(second), autocommit=True
        ) as connection:
            connection.execute("CREATE TABLE public.scope_payload (payload text)")
            connection.execute(
                "INSERT INTO public.scope_payload VALUES (repeat('x', 1200000))"
            )
            sizes = connection.execute(
                "SELECT pg_catalog.pg_column_size(payload),"
                "pg_catalog.octet_length(pg_catalog.row_to_json(q)::text) "
                "FROM public.scope_payload q"
            ).fetchone()
        assert sizes is not None
        compressed, rendered = sizes
        assert compressed * module.MAXIMUM_JSON_ASCII_EXPANSION < (
            module.MAXIMUM_ROW_BYTES
        )
        assert rendered * module.MAXIMUM_JSON_ASCII_EXPANSION > (
            module.MAXIMUM_ROW_BYTES
        )
        reader = adapter._connect(second)

        class PreflightOnlyConnection:
            def execute(self, query: str, parameters: Any = None) -> Any:
                return reader.execute(query, parameters)

            def cursor(self, *, name: str) -> Any:
                raise AssertionError(f"cursor unexpectedly created: {name}")

        try:
            adapter._configure(reader)
            with pytest.raises(module.EvidenceFailure, match="catalog.resource_limit"):
                adapter._rows(
                    PreflightOnlyConnection(),
                    "SELECT payload FROM public.scope_payload",
                )
        finally:
            reader.close()
        second_after = adapter.database_snapshot(second)
        with pytest.raises(
            module.EvidenceFailure, match="database.non_primary_not_empty"
        ):
            module._require_non_primary_empty(second_after)
        live_surfaces = deepcopy(server_before.surfaces)
        live_surfaces["databases"] = [
            record
            for record in live_surfaces["databases"]
            if record["name"] in {first, second}
        ]
        live_server = replace(server_before, surfaces=live_surfaces)
        live_databases = {first: first_before, second: second_before}
        live_policy = _policy(
            module,
            live_server,
            live_databases,
            primary_database=first,
        )
        with pytest.raises(
            module.EvidenceFailure, match="database.non_primary_not_empty"
        ):
            _collect(
                module,
                live_policy,
                module.SnapshotAdapter(
                    live_server,
                    {first: first_before, second: second_after},
                ),
            )
        assert module._surface_pin(KEY, "probe", first_before["schema"]) != (
            module._surface_pin(KEY, "probe", first_after["schema"])
        )
        assert module._surface_pin(
            KEY, "probe", second_before["user_relations"]
        ) != module._surface_pin(KEY, "probe", second_after["user_relations"])
        rendered_schema = "\n".join(first_after["schema"])
        assert "security_barrier=true" in rendered_schema
        assert "column_fdw_option" in rendered_schema
        assert "column_authorization" in rendered_schema
        assert "server_scope_no_insert" in rendered_schema
        assert '"kind": "rewrite_rule"' in rendered_schema
        assert any(row[4] == role for row in first_after["foreign_privileges"])
        assert any(row[5] == role for row in first_after["language_privileges"])
        assert first_after["publication_namespaces"]
        assert first_after["large_objects"]

        with psycopg.connect(
            **local_connection_parameters("test"), autocommit=True
        ) as admin:
            admin.execute(
                sql.SQL("COMMENT ON ROLE {} IS 'private'").format(sql.Identifier(role))
            )
        server_after = adapter.server_snapshot(settings)
        assert module._surface_pin(KEY, "roles", server_before.surfaces["roles"]) != (
            module._surface_pin(KEY, "roles", server_after.surfaces["roles"])
        )
        role_row = next(row for row in server_after.surfaces["roles"] if row[0] == role)
        assert role_row[10:12] == [True, "scram-sha-256"]
        assert server_after.surfaces["shared_descriptions"]
    finally:
        if role_created or created:
            with psycopg.connect(
                **local_connection_parameters("test"), autocommit=True
            ) as admin:
                for name in created:
                    admin.execute(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = %s AND pid <> pg_backend_pid()",
                        (name,),
                    )
                    admin.execute(
                        sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                            sql.Identifier(name)
                        )
                    )
                if role_created:
                    admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def test_script_is_standalone_read_only_and_aggregate_only() -> None:
    source = SCRIPT.read_text()
    assert "from app" not in source
    assert "import app" not in source
    assert "CREATE " not in source
    assert "ALTER " not in source
    assert "DROP " not in source
    assert "REPEATABLE READ READ ONLY DEFERRABLE" in source
    assert "row_security = off" in source
    assert "pg_catalog.pg_auth_members" in source
    assert "pg_catalog.pg_replication_slots" in source
    assert "pg_catalog.pg_subscription" in source
    assert "pg_catalog.pg_subscription_rel" in source
    assert "pg_catalog.pg_publication_rel" in source
    assert "pg_catalog.pg_publication_namespace" in source
    assert "pg_catalog.pg_foreign_table" in source
    assert "pg_catalog.pg_event_trigger" in source
    assert "pg_catalog.pg_largeobject" in source
    assert "pg_catalog.pg_shseclabel" in source
    assert "umoptions" in source
    assert "attfdwoptions" in source
    assert "pg_catalog.pg_authid" in source
    assert "pg_catalog.pg_tablespace_location" in source
    assert "pg_catalog.pg_replication_origin_status" in source
    assert "pg_catalog.pg_shdepend" in source
    assert "pg_catalog.pg_rewrite" in source
    assert "pg_catalog.pg_get_ruledef" in source
    assert "pg_catalog.row_to_json" in source
    assert "pg_catalog.octet_length" in source
    assert "dependency.refclassid" in source
    assert "dependency.refobjid" in source
    assert "c.reltablespace" in source
    assert "d.dattablespace" in source
    assert "c.collowner" in source
    assert "e.extowner" in source
    assert "subskiplsn='0/0'" in source and "srsublsn='0/0'" in source
    assert "array_agg" not in source
    assert "fetchall" not in source
    assert "--database-url" not in source
    assert "STAGING_SERVER_SCOPE_DATABASE_URL" in source
