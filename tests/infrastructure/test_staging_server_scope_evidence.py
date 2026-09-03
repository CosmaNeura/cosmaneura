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


def _server(module: ModuleType) -> Any:
    return module.ServerSnapshot(
        version_major=16,
        visibility={"pg_monitor": True, "pg_read_all_settings": True},
        surfaces={
            "databases": [
                {
                    "name": "appdb",
                    "is_template": False,
                    "allow_connections": True,
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
                },
                {
                    "name": "postgres",
                    "is_template": False,
                    "allow_connections": True,
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
                },
                {
                    "name": "template0",
                    "is_template": True,
                    "allow_connections": False,
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
                },
                {
                    "name": "template1",
                    "is_template": True,
                    "allow_connections": True,
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
                },
            ],
            "roles": [
                ["owner-role", True, True, True, True, True, True, True, -1, None]
            ],
            "memberships": [],
            "role_settings": [],
            "database_privileges": [
                ["appdb", "owner-role", "PUBLIC", "owner-role", "CONNECT", False]
            ],
            "tablespaces": [["pg_default", "owner-role", None, ["(PUBLIC,CREATE,f)"]]],
            "replication_slots": [],
            "parameter_privileges": [],
            "shared_security_labels": [],
            "settings": [
                ["archive_mode", "off", None, "default", "off", "off", False],
                [
                    "server_version_num",
                    "160015",
                    None,
                    "default",
                    "160015",
                    "160015",
                    False,
                ],
            ],
        },
    )


def _database_surfaces(label: str) -> dict[str, list[Any]]:
    return {
        "schema": [{"kind": "schema", "label": label}],
        "extensions": [["plpgsql", "1.0", "pg_catalog", False]],
        "foreign_data_wrappers": [["fdw-private", ["token=fdw-secret"]]],
        "foreign_servers": [["server-private", ["host=private.invalid"]]],
        "foreign_tables": [["private", "table-private", "server-private"]],
        "user_mappings": [["server-private", "user-private", ["password=secret"]]],
        "publications": [["publication-private", False]],
        "publication_memberships": [["publication-private", "table-private"]],
        "subscriptions": [["subscription-private", "password=secret", False]],
        "subscription_relations": [["subscription-private", "table-private", "r"]],
        "security_labels": [["provider-private", "label-private"]],
        "event_triggers": [["event-private", "ddl_command_start"]],
        "large_objects": [["9812", "owner-role", 0, "c2VjcmV0"]],
    }


def _databases() -> dict[str, dict[str, list[Any]]]:
    return {
        "appdb": _database_surfaces("app"),
        "postgres": _database_surfaces("maintenance"),
        "template1": _database_surfaces("template"),
    }


def _policy(
    module: ModuleType,
    server: Any,
    databases: dict[str, dict[str, list[Any]]],
) -> dict[str, Any]:
    policy = json.loads(POLICY.read_text())
    policy["activation_ready"] = True
    policy["source"].update(
        resource_id=RESOURCE_ID,
        revision=REVISION,
        host="localhost",
        maintenance_database="postgres",
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
                "status": "fingerprinted",
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
        "fingerprinted",
        "connections_disabled",
    }
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
        "publication-private",
        "c2VjcmV0",
    ):
        assert raw_value not in rendered
    assert len(evidence["evidence_hmac"]) == 64
    assert evidence["capture_started_at"] == "2026-09-03T12:00:00Z"


@pytest.mark.parametrize(
    "surface",
    [
        "databases",
        "roles",
        "memberships",
        "role_settings",
        "database_privileges",
        "tablespaces",
        "replication_slots",
        "settings",
    ],
)
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

    hidden = replace(
        server, visibility={"pg_monitor": False, "pg_read_all_settings": True}
    )
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

    monkeypatch.setattr(module, "MAXIMUM_BYTES_PER_SURFACE", 3)
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
        def execute(self, query: str, parameters: Any) -> None:
            del query, parameters

        def cursor(self, *, name: str) -> Cursor:
            assert name.startswith("server_scope_")
            return Cursor()

    adapter = object.__new__(module.LivePostgresAdapter)
    adapter.start_capture(60)
    monkeypatch.setattr(module, "MAXIMUM_RECORDS_PER_SURFACE", 2)
    with pytest.raises(module.EvidenceFailure, match="catalog.resource_limit"):
        adapter._rows(Connection(), "SELECT bounded", ())


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

    base_url = os.environ["TEST_DATABASE_URL"]
    parsed = urllib.parse.urlsplit(base_url)
    assert parsed.scheme == "postgresql"
    assert parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    assert parsed.port in {None, 5432} and parsed.path == "/test"
    first = f"scope_a_{secrets.token_hex(6)}"
    second = f"scope_b_{secrets.token_hex(6)}"
    role = f"scope_role_{secrets.token_hex(6)}"
    created: list[str] = []
    role_created = False
    try:
        with psycopg.connect(base_url, autocommit=True) as admin:
            for name in (first, second):
                admin.execute(
                    sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(
                        sql.Identifier(name)
                    )
                )
                created.append(name)
        local_policy = json.loads(POLICY.read_text())
        local_policy["source"].update(
            host=parsed.hostname,
            port=parsed.port or 5432,
            maintenance_database="test",
        )
        adapter = module.LivePostgresAdapter(base_url, local_policy, allow_local=True)
        settings = ["server_version_num", "wal_level"]
        server_before = adapter.server_snapshot(settings)
        names = {record["name"] for record in server_before.surfaces["databases"]}
        assert {first, second, "template0"} <= names
        first_before = adapter.database_snapshot(first)
        second_before = adapter.database_snapshot(second)
        assert set(first_before) == set(module.DATABASE_SURFACES)
        assert set(second_before) == set(module.DATABASE_SURFACES)

        first_url = urllib.parse.urlunsplit(parsed._replace(path=f"/{first}"))
        with psycopg.connect(first_url, autocommit=True) as connection:
            connection.execute("CREATE TABLE public.server_scope_probe (id integer)")
        first_after = adapter.database_snapshot(first)
        assert module._surface_pin(KEY, "probe", first_before["schema"]) != (
            module._surface_pin(KEY, "probe", first_after["schema"])
        )
        assert module._surface_pin(KEY, "probe", second_before["schema"]) == (
            module._surface_pin(
                KEY, "probe", adapter.database_snapshot(second)["schema"]
            )
        )

        with psycopg.connect(base_url, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE ROLE {}").format(sql.Identifier(role)))
        role_created = True
        server_after = adapter.server_snapshot(settings)
        assert module._surface_pin(KEY, "roles", server_before.surfaces["roles"]) != (
            module._surface_pin(KEY, "roles", server_after.surfaces["roles"])
        )
    finally:
        if role_created or created:
            with psycopg.connect(base_url, autocommit=True) as admin:
                if role_created:
                    admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
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
    assert "pg_catalog.pg_foreign_table" in source
    assert "pg_catalog.pg_event_trigger" in source
    assert "pg_catalog.pg_largeobject" in source
    assert "pg_catalog.pg_shseclabel" in source
    assert "umoptions" in source
    assert "fetchall" not in source
    assert "--database-url" not in source
    assert "STAGING_SERVER_SCOPE_DATABASE_URL" in source
