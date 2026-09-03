from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import secrets
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "staging_synthetic_inventory.py"
POLICY = ROOT / "contracts" / "staging-rehearsal" / "v1" / "synthetic-data-policy.json"
KEY = b"inventory-test-key-that-is-at-least-32-bytes"
SOURCE_SHA = "1" * 40
CANDIDATE_SHA = "2" * 40


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "standalone_staging_synthetic_inventory",
        SCRIPT,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _policy() -> tuple[dict[str, Any], str]:
    raw = POLICY.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


@dataclass
class FakeProvider:
    module: ModuleType
    policy: dict[str, Any]
    profile: str = "post"
    catalog_extra: dict[str, tuple[str, bool, list[tuple[str, str, bool]]]] = field(
        default_factory=dict
    )
    column_mutation: tuple[str, int, tuple[str, str, bool]] | None = None
    relation_mutations: dict[str, tuple[str, bool]] = field(default_factory=dict)
    revisions: list[str] | None = None
    table_counts: dict[str, int] = field(default_factory=dict)
    rule_violations: dict[tuple[str, str], int] = field(default_factory=dict)
    containers: list[str] | None = None
    blob_counts: dict[str, int] = field(default_factory=dict)
    redis_size: int = 0
    unstable_blob_container: str | None = None
    unstable_redis: bool = False
    _blob_calls: dict[str, int] = field(default_factory=dict, init=False)
    _redis_calls: int = field(default=0, init=False)

    def _expected_tables(self) -> dict[str, dict[str, Any]]:
        return self.module._expected_tables(self.policy, self.profile)

    def database_catalog(
        self,
    ) -> dict[str, tuple[str, bool, list[tuple[str, str, bool]]]]:
        expected = self._expected_tables()
        catalog = {
            name: ("r", False, [tuple(column) for column in spec["columns"]])
            for name, spec in expected.items()
        }
        control = self.policy["database"]["control_table"]
        catalog[control["name"]] = (
            "r",
            False,
            [tuple(column) for column in control["columns"]],
        )
        catalog.update(self.catalog_extra)
        for table, (kind, row_security) in self.relation_mutations.items():
            catalog[table] = (kind, row_security, catalog[table][2])
        if self.column_mutation:
            table, index, column = self.column_mutation
            catalog[table][2][index] = column
        return catalog

    def alembic_revisions(self) -> list[str]:
        if self.revisions is not None:
            return self.revisions
        return [self.policy["profiles"][self.profile]["revision"]]

    def table_count(self, table: str) -> int:
        return self.table_counts.get(table, 0)

    def table_state(self, table: str, identity_columns: list[str]) -> Any:
        identity = "b" * 64 if identity_columns else None
        return self.module.StateDigest(1, "a" * 64, identity)

    def violation_count(self, table: str, rule: dict[str, Any]) -> int:
        return self.rule_violations.get((table, rule["id"]), 0)

    def blob_container_names(self) -> list[str]:
        return self.containers or list(self.policy["blob"]["containers"])

    def blob_state(self, container: str, *, include_content: bool) -> Any:
        assert include_content is (
            self.policy["blob"]["containers"][container] == "fixed_catalog"
        )
        self._blob_calls[container] = self._blob_calls.get(container, 0) + 1
        count = self.blob_counts.get(
            container,
            1 if self.policy["blob"]["containers"][container] == "fixed_catalog" else 0,
        )
        digest = (
            "d" * 64
            if container == self.unstable_blob_container
            and self._blob_calls[container] > 1
            else "c" * 64
        )
        return self.module.StateDigest(count, digest)

    def redis_dbsize(self) -> int:
        self._redis_calls += 1
        return self.redis_size + int(self.unstable_redis and self._redis_calls > 1)


def _approval(
    module: ModuleType,
    policy: dict[str, Any],
    policy_sha256: str,
    provider: FakeProvider,
) -> dict[str, Any]:
    expected = module._expected_tables(policy, provider.profile)
    tables: dict[str, dict[str, Any]] = {}
    for name, spec in expected.items():
        if spec["disposition"] == "required_empty":
            continue
        state = provider.table_state(name, spec.get("identity_columns", []))
        entry: dict[str, Any] = {
            "count": state.count,
            "state_hmac": state.state_hmac,
        }
        if state.identity_hmac:
            entry["identity_hmac"] = state.identity_hmac
        tables[name] = entry
    containers = {
        name: {"count": 1, "state_hmac": "c" * 64}
        for name, disposition in policy["blob"]["containers"].items()
        if disposition == "fixed_catalog"
    }
    return {
        "schema_version": 1,
        "policy_sha256": policy_sha256,
        "profile": provider.profile,
        "tables": tables,
        "containers": containers,
    }


def _verify(
    module: ModuleType,
    policy: dict[str, Any],
    policy_sha256: str,
    provider: FakeProvider,
    *,
    approved: dict[str, Any] | None = None,
    redis_quiesced: bool = True,
) -> dict[str, Any]:
    return module.verify_inventory(
        policy=policy,
        policy_sha256=policy_sha256,
        approved=approved
        if approved is not None
        else _approval(module, policy, policy_sha256, provider),
        profile_name=provider.profile,
        source_sha=SOURCE_SHA,
        candidate_sha=CANDIDATE_SHA,
        key=KEY,
        provider=provider,
        redis_quiesced=redis_quiesced,
    )


@pytest.fixture
def module() -> ModuleType:
    return _load_module()


def test_policy_is_closed_world_and_covers_both_exact_catalogs(
    module: ModuleType,
) -> None:
    policy, policy_sha256 = _policy()
    assert policy_sha256 == module.CANONICAL_POLICY_SHA256
    assert (
        hashlib.sha256(module._canonical_json(policy)).hexdigest()
        == module.CANONICAL_POLICY_SEMANTIC_SHA256
    )
    module.validate_policy(policy)
    post = module._expected_tables(policy, "post")
    pre = module._expected_tables(policy, "pre")

    assert len(pre) == 62
    assert len(post) == 64
    assert set(post) - set(pre) == {
        "practice_invitation",
        "public_legal_acceptance",
    }
    assert "doctor_intake_file" not in post
    assert {spec["disposition"] for spec in post.values()} == {
        "required_empty",
        "synthetic_lineage",
        "workforce_allowlist",
        "fixed_catalog",
    }
    assert all(spec["columns"] for spec in post.values())
    assert all(
        len({column[0] for column in spec["columns"]}) == len(spec["columns"])
        for spec in post.values()
    )
    for spec in post.values():
        not_null = {column[0]: column[2] for column in spec["columns"]}
        for rule in spec.get("rules", []):
            if rule["kind"] == "required_null":
                assert all(not not_null[column] for column in rule["columns"])
        assert all(not_null[column] for column in spec.get("rotation_columns", []))
    assert post["practice"]["rotation_columns"] == ["join_code"]
    assert post["provider_organization"]["rotation_columns"] == [
        "access_code_salt",
        "access_code_digest",
    ]

    pre_practice_columns = [column[0] for column in pre["practice"]["columns"]]
    assert "acquisition_source" not in pre_practice_columns
    assert "stripe_checkout_request_json" not in pre_practice_columns
    assert pre["practice_member"]["columns"][-1][0] == "access_records_retain_until"


@pytest.mark.parametrize("profile", ["pre", "post"])
def test_provider_mock_passes_with_exact_approved_state(
    module: ModuleType,
    profile: str,
) -> None:
    policy, policy_sha256 = _policy()
    provider = FakeProvider(module, policy, profile=profile)

    evidence = _verify(module, policy, policy_sha256, provider)

    assert evidence["pass"] is True
    assert evidence["errors"] == []
    assert evidence["counts"]["database.alembic_version"] == 1
    assert len(evidence["catalog_hmac"]) == 64
    assert set(evidence) == {
        "schema_version",
        "policy_id",
        "policy_sha256",
        "source_sha",
        "phase",
        "candidate_sha",
        "counts",
        "rule_violation_counts",
        "identity_hmacs",
        "catalog_hmac",
        "state_hmacs",
        "blob_state_hmacs",
        "redis_state_hmac",
        "errors",
        "pass",
    }


def test_evidence_contains_no_provider_values(module: ModuleType) -> None:
    policy, policy_sha256 = _policy()
    provider = FakeProvider(module, policy)
    evidence = _verify(module, policy, policy_sha256, provider)
    serialized = json.dumps(evidence)

    for forbidden in (
        "patient@example.invalid",
        "blob/private-object.wav",
        "celery-task-private",
        "database_rows",
        "redis_keys",
    ):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (
            {"catalog_extra": {"unexpected": ("v", False, [])}},
            "database.catalog.table_set",
        ),
        (
            {"column_mutation": ("doctor", 0, ("id", "text", True))},
            "database.catalog.doctor.columns",
        ),
        (
            {"catalog_extra": {"doctor": ("r", False, [])}},
            "database.catalog.doctor.columns",
        ),
        (
            {"relation_mutations": {"doctor": ("r", True)}},
            "database.catalog.doctor.columns",
        ),
        ({"revisions": ["wrong"]}, "database.alembic_version"),
        (
            {"table_counts": {"zoom_meeting": 1}},
            "database.table.zoom_meeting.required_empty",
        ),
        (
            {"rule_violations": {("doctor", "credentials_null"): 1}},
            "database.table.doctor.credentials_null",
        ),
        (
            {"rule_violations": {("patient", "synthetic_root_coverage"): 1}},
            "database.table.patient.synthetic_root_coverage",
        ),
    ],
)
def test_database_drift_fails_closed(
    module: ModuleType,
    mutation: dict[str, Any],
    error: str,
) -> None:
    policy, policy_sha256 = _policy()
    provider = FakeProvider(module, policy, **mutation)

    evidence = _verify(module, policy, policy_sha256, provider)

    assert evidence["pass"] is False
    assert error in evidence["errors"]


def test_approved_state_mismatch_fails_closed(module: ModuleType) -> None:
    policy, policy_sha256 = _policy()
    provider = FakeProvider(module, policy)
    approved = _approval(module, policy, policy_sha256, provider)
    approved["tables"]["doctor"]["state_hmac"] = "d" * 64

    evidence = _verify(
        module,
        policy,
        policy_sha256,
        provider,
        approved=approved,
    )

    assert evidence["pass"] is False
    assert "database.table.doctor.approved_state" in evidence["errors"]


def test_substituted_policy_is_rejected(module: ModuleType) -> None:
    policy, _ = _policy()
    policy["database"]["expected_host"] = "production.invalid"

    with pytest.raises(module.InventoryFailure, match="policy.invalid"):
        module.validate_policy(policy)


def test_blob_versions_deleted_objects_and_unknown_containers_fail_closed(
    module: ModuleType,
) -> None:
    policy, policy_sha256 = _policy()
    provider = FakeProvider(
        module,
        policy,
        containers=[*policy["blob"]["containers"], "unknown-container"],
        blob_counts={"uploads": 2},
    )

    evidence = _verify(module, policy, policy_sha256, provider)

    assert evidence["pass"] is False
    assert "blob.container_set" in evidence["errors"]
    assert "blob.container.uploads.required_empty" in evidence["errors"]
    assert policy["blob"]["enumerate"] == [
        "live",
        "versions",
        "snapshots",
        "deleted",
        "uncommitted",
    ]


def test_nontransactional_blob_and_redis_reads_must_be_stable(
    module: ModuleType,
) -> None:
    policy, policy_sha256 = _policy()
    provider = FakeProvider(
        module,
        policy,
        unstable_blob_container="test-uploads",
        unstable_redis=True,
    )

    evidence = _verify(module, policy, policy_sha256, provider)

    assert evidence["pass"] is False
    assert "blob.container.test-uploads.unstable" in evidence["errors"]
    assert "redis.unstable" in evidence["errors"]
    assert all(calls == 2 for calls in provider._blob_calls.values())
    assert provider._redis_calls == 2


def test_blob_adapter_hashes_streamed_content_metadata_tags_and_all_states(
    module: ModuleType,
) -> None:
    class Download:
        def __init__(self, content: bytes) -> None:
            self.content = content

        def chunks(self) -> list[bytes]:
            return [self.content[:2], self.content[2:]]

    class BlobClient:
        def __init__(
            self,
            content: bytes,
            metadata: dict[str, str],
            uncommitted: bool,
        ) -> None:
            self.content = content
            self.uncommitted = uncommitted
            self.download_calls = 0
            self.block_list_calls = 0
            self.properties = type(
                "Properties",
                (),
                {
                    "size": len(content),
                    "etag": "etag",
                    "last_modified": None,
                    "blob_type": "BlockBlob",
                    "metadata": metadata,
                    "content_settings": type(
                        "ContentSettings",
                        (),
                        {"content_md5": None, "content_type": "text/plain"},
                    )(),
                },
            )()

        def get_blob_properties(self) -> Any:
            return self.properties

        def download_blob(self) -> Download:
            self.download_calls += 1
            return Download(self.content)

        def get_blob_tags(self) -> dict[str, str]:
            return {"classification": "fixed"}

        def get_block_list(self, _: str) -> tuple[list[Any], list[Any]]:
            self.block_list_calls += 1
            return [], [object()] if self.uncommitted else []

    class Container:
        def __init__(
            self,
            content: bytes,
            metadata: dict[str, str],
            uncommitted: bool,
        ) -> None:
            self.client = BlobClient(content, metadata, uncommitted)
            self.include: list[str] = []

        def list_blobs(self, *, include: list[str]) -> list[Any]:
            self.include = include
            return [
                type(
                    "Blob",
                    (),
                    {
                        "name": "private-name",
                        "snapshot": None,
                        "version_id": "version",
                        "is_current_version": True,
                        "deleted": False,
                    },
                )()
            ]

        def get_blob_client(self, *_: Any, **__: Any) -> BlobClient:
            return self.client

    def state(
        content: bytes,
        metadata: dict[str, str],
        *,
        include_content: bool = True,
        uncommitted: bool = False,
    ) -> tuple[Any, Container]:
        container = Container(content, metadata, uncommitted)
        service = type(
            "Service",
            (),
            {"get_container_client": lambda _self, _name: container},
        )()
        provider = object.__new__(module.LiveInventoryProvider)
        provider._key = KEY
        provider._policy_id = "test-policy"
        provider._blob = service
        return (
            provider.blob_state(
                "test-uploads",
                include_content=include_content,
            ),
            container,
        )

    original, container = state(b"content-one", {"kind": "fixture"})
    changed_content, _ = state(b"content-two", {"kind": "fixture"})
    changed_metadata, _ = state(b"content-one", {"kind": "changed"})
    _, listing_only = state(
        b"must-not-be-downloaded",
        {"kind": "unexpected"},
        include_content=False,
    )

    assert original.count == 1
    assert original.state_hmac != changed_content.state_hmac
    assert original.state_hmac != changed_metadata.state_hmac
    assert listing_only.client.download_calls == 0
    assert container.client.block_list_calls == 1
    assert set(container.include) == {
        "deleted",
        "deletedwithversions",
        "metadata",
        "snapshots",
        "tags",
        "uncommittedblobs",
        "versions",
    }
    with pytest.raises(module.InventoryFailure, match="blob.uncommitted_blocks"):
        state(b"staged", {}, uncommitted=True)


@pytest.mark.parametrize(
    ("redis_size", "quiesced", "error"),
    [
        (1, True, "redis.required_empty"),
        (0, False, "redis.not_quiesced"),
    ],
)
def test_redis_database_zero_must_be_quiesced_and_empty(
    module: ModuleType,
    redis_size: int,
    quiesced: bool,
    error: str,
) -> None:
    policy, policy_sha256 = _policy()
    provider = FakeProvider(module, policy, redis_size=redis_size)

    evidence = _verify(
        module,
        policy,
        policy_sha256,
        provider,
        redis_quiesced=quiesced,
    )

    assert evidence["pass"] is False
    assert error in evidence["errors"]


def _write_pem(path: Path, marker: str, mode: int = 0o644) -> Path:
    path.write_text(f"-----BEGIN {marker}-----\ntest\n-----END {marker}-----\n")
    path.chmod(mode)
    return path


def test_database_and_redis_require_verified_tls_and_safe_keys(
    module: ModuleType,
    tmp_path: Path,
) -> None:
    policy, _ = _policy()
    ca = _write_pem(tmp_path / "ca.pem", "CERTIFICATE")
    cert = _write_pem(tmp_path / "client.pem", "CERTIFICATE")
    private_key = _write_pem(tmp_path / "client.key", "PRIVATE KEY", 0o600)
    database_query = urllib.parse.urlencode(
        {
            "sslmode": "verify-full",
            "sslrootcert": os.fspath(ca),
            "sslcert": os.fspath(cert),
            "sslkey": os.fspath(private_key),
        }
    )
    database_url = (
        "postgresql://staging:secret@"
        f"{policy['database']['expected_host']}:5432/staging?{database_query}"
    )
    redis_query = urllib.parse.urlencode(
        {
            "ssl_cert_reqs": "required",
            "ssl_check_hostname": "true",
            "ssl_ca_certs": os.fspath(ca),
            "ssl_certfile": os.fspath(cert),
            "ssl_keyfile": os.fspath(private_key),
        }
    )
    redis_url = (
        f"rediss://:secret@{policy['redis']['expected_host']}:6380/0?{redis_query}"
    )

    module.validate_database_url(database_url, policy["database"])
    module.validate_redis_url(redis_url, policy["redis"])

    with pytest.raises(module.InventoryFailure, match="database.target_invalid"):
        module.validate_database_url(
            database_url.replace("verify-full", "require"),
            policy["database"],
        )
    with pytest.raises(module.InventoryFailure, match="redis.target_invalid"):
        module.validate_redis_url(
            redis_url.replace("ssl_cert_reqs=required", "ssl_cert_reqs=optional"),
            policy["redis"],
        )
    private_key.chmod(0o644)
    with pytest.raises(module.InventoryFailure, match="tls.file_unsafe"):
        module.validate_database_url(database_url, policy["database"])


def test_hmac_key_must_be_owner_only_and_digest_is_keyed(
    module: ModuleType,
    tmp_path: Path,
) -> None:
    key_file = tmp_path / "inventory.key"
    key_file.write_bytes(KEY)
    key_file.chmod(0o600)
    assert module.read_hmac_key(key_file) == KEY
    first = module._digest(KEY, "test", ["private-value"])
    second = module._digest(b"x" * 32, "test", ["private-value"])
    assert first != second
    assert "private-value" not in first

    key_file.chmod(0o644)
    with pytest.raises(module.InventoryFailure, match="hmac_key.unsafe"):
        module.read_hmac_key(key_file)


def test_postgres_row_json_preserves_ordered_json() -> None:
    import psycopg

    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as connection:
        first_row = connection.execute(
            "SELECT row_to_json(row(%s::json))::text", ('{"a":1,"b":2}',)
        ).fetchone()
        second_row = connection.execute(
            "SELECT row_to_json(row(%s::json))::text", ('{"b":2,"a":1}',)
        ).fetchone()
    assert first_row and second_row and first_row[0] != second_row[0]


def test_policy_catalog_matches_migration_replay(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import psycopg
    from alembic import command
    from alembic.config import Config
    from psycopg import sql

    base_url = os.environ["TEST_DATABASE_URL"]
    parsed = urllib.parse.urlsplit(base_url)
    assert parsed.scheme == "postgresql"
    assert parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    assert parsed.port in {None, 5432} and parsed.path == "/test"
    assert not parsed.query and not parsed.fragment
    database_name = f"inventory_{secrets.token_hex(8)}"
    database_url = urllib.parse.urlunsplit(parsed._replace(path=f"/{database_name}"))
    created = False
    try:
        with psycopg.connect(base_url, autocommit=True) as admin:
            admin.execute(
                sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(
                    sql.Identifier(database_name)
                )
            )
        created = True
        monkeypatch.setenv("TEST_DATABASE_URL", database_url)
        config = Config(os.fspath(ROOT / "alembic.ini"))
        config.set_main_option("script_location", os.fspath(ROOT / "migrations"))
        policy, _ = _policy()
        for profile_name in ("pre", "post"):
            profile = policy["profiles"][profile_name]
            command.upgrade(config, profile["revision"])
            with psycopg.connect(database_url, autocommit=True) as connection:
                provider = object.__new__(module.LiveInventoryProvider)
                provider._connection = connection
                observed = provider.database_catalog()
                expected = {
                    name: ("r", False, [tuple(column) for column in spec["columns"]])
                    for name, spec in module._expected_tables(
                        policy, profile_name
                    ).items()
                }
                control = policy["database"]["control_table"]
                expected[control["name"]] = (
                    "r",
                    False,
                    [tuple(column) for column in control["columns"]],
                )
                assert observed == expected
                assert provider.alembic_revisions() == [profile["revision"]]
                assert len(observed) - 1 == profile["table_count"]
    finally:
        if created:
            with psycopg.connect(base_url, autocommit=True) as admin:
                admin.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (database_name,),
                )
                admin.execute(
                    sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                        sql.Identifier(database_name)
                    )
                )


def test_script_is_standalone_and_never_imports_app_or_alembic() -> None:
    source = SCRIPT.read_text()
    assert "from app" not in source
    assert "import app" not in source
    assert "alembic" not in {
        line.strip().split()[1]
        for line in source.splitlines()
        if line.strip().startswith("import ")
    }
    assert "REPEATABLE READ READ ONLY DEFERRABLE" in source
    assert "SET LOCAL row_security = off" in source
    assert "SET LOCAL lock_timeout" in source
    assert "LEFT JOIN pg_catalog.pg_attribute" in source
    assert "row_to_json(source)::text" in source
    assert "include_system=True" in source
