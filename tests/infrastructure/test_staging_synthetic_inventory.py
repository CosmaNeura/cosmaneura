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
from typing import Any, LiteralString

import pytest

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "staging_synthetic_inventory.py"
POLICY = ROOT / "contracts" / "staging-rehearsal" / "v1" / "synthetic-data-policy.json"
KEY = b"inventory-test-key-that-is-at-least-32-bytes"
SOURCE_SHA = "1" * 40
CANDIDATE_SHA = "2" * 40
OBSERVED_AT = "2026-09-03T12:00:00Z"
EXPIRES_AT = "2026-09-03T12:15:00Z"


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
    non_system_schema_count: int = 1
    sequence_count: int = 0
    large_object_count: int = 0
    schema_descriptor_sha256: str | None = None
    schema_descriptor_counts: dict[str, int] | None = None
    column_mutation: tuple[str, int, tuple[str, str, bool]] | None = None
    relation_mutations: dict[str, tuple[str, bool]] = field(default_factory=dict)
    revisions: list[str] | None = None
    table_counts: dict[str, int] = field(default_factory=dict)
    rule_violations: dict[tuple[str, str], int] = field(default_factory=dict)
    containers: list[str] | None = None
    blob_counts: dict[str, int] = field(default_factory=dict)
    redis_logical_databases: int = 0
    redis_keys: int = 0
    unstable_blob_container: str | None = None
    unstable_redis: bool = False
    _blob_calls: dict[str, int] = field(default_factory=dict, init=False)
    _redis_calls: int = field(default=0, init=False)

    def _expected_tables(self) -> dict[str, dict[str, Any]]:
        return self.module._expected_tables(self.policy, self.profile)

    def database_global_counts(self) -> Any:
        return self.module.DatabaseGlobalCounts(
            self.non_system_schema_count,
            self.sequence_count,
            self.large_object_count,
        )

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

    def database_schema_descriptor(self) -> Any:
        expected = self.policy["profiles"][self.profile]["schema_descriptor"]
        return self.module.SchemaDescriptor(
            self.schema_descriptor_sha256 or expected["sha256"],
            self.schema_descriptor_counts or dict(expected["counts"]),
        )

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

    def redis_surface(self) -> Any:
        self._redis_calls += 1
        key_count = self.redis_keys + int(self.unstable_redis and self._redis_calls > 1)
        return self.module.RedisSurface(self.redis_logical_databases, key_count)


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
    provenance: dict[str, Any] | None = None,
    signer_claims: dict[str, Any] | None = None,
) -> dict[str, Any]:
    approved_value = (
        approved
        if approved is not None
        else _approval(module, policy, policy_sha256, provider)
    )
    trusted = policy["evidence"]["trusted_context"]
    provenance_value = provenance or {
        "repository": trusted["repository"],
        "protected_ref": trusted["protected_ref"],
        "workflow_path": trusted["workflow_path"],
        "workflow_sha": "3" * 40,
        "run_id": "901",
        "run_attempt": "1",
        "event_name": "workflow_dispatch",
    }
    claims_value = signer_claims or {
        "issuer": trusted["issuer"],
        "subject": trusted["subject"],
        "repository": provenance_value["repository"],
        "ref": provenance_value["protected_ref"],
        "sha": provenance_value["workflow_sha"],
        "workflow_ref": (
            f"{provenance_value['repository']}/"
            f"{provenance_value['workflow_path']}@"
            f"{provenance_value['protected_ref']}"
        ),
        "workflow_sha": provenance_value["workflow_sha"],
        "run_id": provenance_value["run_id"],
        "run_attempt": provenance_value["run_attempt"],
        "actor": trusted["founder_actor"],
        "triggering_actor": trusted["founder_actor"],
    }
    return module.verify_inventory(
        policy=policy,
        policy_sha256=policy_sha256,
        approved=approved_value,
        profile_name=provider.profile,
        source_sha=SOURCE_SHA,
        candidate_sha=CANDIDATE_SHA,
        approved_state_sha256=hashlib.sha256(
            module._canonical_json(approved_value)
        ).hexdigest(),
        hmac_key_version=module.SUPPORTED_HMAC_KEY_VERSION,
        observed_at=OBSERVED_AT,
        expires_at=EXPIRES_AT,
        provenance=provenance_value,
        signer_claims=claims_value,
        key=KEY,
        provider=provider,
    )


@pytest.fixture
def module() -> ModuleType:
    return _load_module()


def test_schema_descriptor_is_canonical_and_bounded(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sections = {name: [] for name in module.SCHEMA_DESCRIPTOR_SECTIONS}
    sections["relations"] = [{"name": "second"}, {"name": "first"}]
    forward = module._schema_descriptor(sections)
    sections["relations"].reverse()
    reverse = module._schema_descriptor(sections)

    assert forward == reverse
    assert forward.counts["relations"] == 2
    with pytest.raises(
        module.InventoryFailure, match="database.schema_descriptor_failed"
    ):
        module._schema_descriptor(sections | {"unexpected": []})
    monkeypatch.setattr(module, "MAXIMUM_SCHEMA_DESCRIPTOR_RECORDS", 1)
    with pytest.raises(
        module.InventoryFailure, match="database.schema_descriptor_failed"
    ):
        module._schema_descriptor(sections)


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
    assert policy["database"]["allowed_non_system_schemas"] == ["public"]
    assert policy["database"]["sequences"] == []
    assert policy["database"]["reject_large_objects"] is True
    assert policy["redis"]["scan_all_logical_databases"] is True
    assert policy["evidence"]["schema_descriptor_algorithm"] == "SHA-256"
    assert policy["profiles"]["pre"]["schema_descriptor"] == {
        "format": "postgresql-16-public-v2",
        "sha256": "b6dd67990533bec013f9db94c9c24940fabf9388ba97b585db45db24ca786849",
        "counts": {
            "namespace_objects": 88,
            "relations": 63,
            "columns": 744,
            "constraints": 346,
            "indexes": 186,
            "triggers": 19,
            "trigger_functions": 9,
            "rls_policies": 0,
            "types": 154,
            "enum_labels": 37,
            "views": 0,
            "routines": 11,
            "authorizations": 1944,
            "default_privileges": 0,
        },
    }
    assert policy["profiles"]["post"]["schema_descriptor"] == {
        "format": "postgresql-16-public-v2",
        "sha256": "3857d3b44c40f88cf2da464acbc8cd76068884979cc9b4bc6276c9ebb73c9295",
        "counts": {
            "namespace_objects": 92,
            "relations": 65,
            "columns": 784,
            "constraints": 372,
            "indexes": 195,
            "triggers": 22,
            "trigger_functions": 11,
            "rls_policies": 0,
            "types": 158,
            "enum_labels": 37,
            "views": 0,
            "routines": 13,
            "authorizations": 2035,
            "default_privileges": 0,
        },
    }

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

    assert evidence["outcome"] == "pass"
    assert evidence["errors"] == []
    assert evidence["counts"]["database.alembic_version"] == 1
    assert evidence["counts"]["database.non_system_schemas"] == 1
    assert evidence["counts"]["database.sequences"] == 0
    assert evidence["counts"]["database.large_objects"] == 0
    assert evidence["counts"]["redis.logical_databases_with_keys"] == 0
    assert evidence["counts"]["redis.keys"] == 0
    assert len(evidence["catalog_hmac"]) == 64
    assert len(evidence["schema_descriptor_sha256"]) == 64
    assert set(evidence) == {
        "schema_version",
        "policy_id",
        "policy_sha256",
        "approved_state_sha256",
        "hmac_key_version",
        "source_resource",
        "observed_at",
        "expires_at",
        "source_sha",
        "phase",
        "candidate_sha",
        "provenance",
        "signer_claims",
        "counts",
        "rule_violation_counts",
        "identity_hmacs",
        "catalog_hmac",
        "schema_descriptor_sha256",
        "state_hmacs",
        "blob_state_hmacs",
        "redis_state_hmac",
        "errors",
        "outcome",
        "receipt_sha256",
    }
    assert set(policy["evidence"]["allowed_fields"]) == set(evidence)
    unsigned = dict(evidence)
    receipt_sha256 = unsigned.pop("receipt_sha256")
    assert (
        receipt_sha256 == hashlib.sha256(module._canonical_json(unsigned)).hexdigest()
    )


def test_inventory_context_is_closed_world_and_cross_bound(module: ModuleType) -> None:
    policy, policy_sha256 = _policy()
    provider = FakeProvider(module, policy)
    trusted = policy["evidence"]["trusted_context"]
    provenance = {
        "repository": trusted["repository"],
        "protected_ref": trusted["protected_ref"],
        "workflow_path": trusted["workflow_path"],
        "workflow_sha": "3" * 40,
        "run_id": "901",
        "run_attempt": "1",
        "event_name": "workflow_dispatch",
    }

    for mutation in (
        {**provenance, "unexpected": "value"},
        {**provenance, "repository": "other/repository"},
        {**provenance, "workflow_sha": "not-a-sha"},
        {**provenance, "run_attempt": "0"},
    ):
        with pytest.raises(
            module.InventoryFailure, match="evidence.provenance_invalid"
        ):
            _verify(
                module,
                policy,
                policy_sha256,
                provider,
                provenance=mutation,
            )

    approved = _approval(module, policy, policy_sha256, provider)
    with pytest.raises(module.InventoryFailure, match="evidence.context_invalid"):
        module.verify_inventory(
            policy=policy,
            policy_sha256=policy_sha256,
            approved=approved,
            profile_name="post",
            source_sha=SOURCE_SHA,
            candidate_sha=CANDIDATE_SHA,
            approved_state_sha256="a" * 64,
            hmac_key_version="unsupported",
            observed_at=OBSERVED_AT,
            expires_at=EXPIRES_AT,
            provenance=provenance,
            signer_claims={},
            key=KEY,
            provider=provider,
        )

    with pytest.raises(module.InventoryFailure, match="evidence.time_invalid"):
        module._validate_evidence_context(
            policy=policy,
            approved_state_sha256="a" * 64,
            hmac_key_version=module.SUPPORTED_HMAC_KEY_VERSION,
            observed_at=OBSERVED_AT,
            expires_at="2026-09-03T12:15:01Z",
            provenance=provenance,
            signer_claims={},
        )


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
        ({"non_system_schema_count": 2}, "database.schema_set"),
        ({"sequence_count": 1}, "database.sequence_set"),
        ({"large_object_count": 1}, "database.large_objects"),
        (
            {"schema_descriptor_sha256": "f" * 64},
            "database.schema_descriptor",
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

    assert evidence["outcome"] == "fail"
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

    assert evidence["outcome"] == "fail"
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

    assert evidence["outcome"] == "fail"
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

    assert evidence["outcome"] == "fail"
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


def test_every_redis_database_must_be_empty(
    module: ModuleType,
) -> None:
    policy, policy_sha256 = _policy()
    provider = FakeProvider(
        module,
        policy,
        redis_logical_databases=1,
        redis_keys=1,
    )

    evidence = _verify(module, policy, policy_sha256, provider)

    assert evidence["outcome"] == "fail"
    assert "redis.required_empty" in evidence["errors"]


def test_redis_adapter_aggregates_info_keyspace_without_enumerating_keys(
    module: ModuleType,
) -> None:
    class Redis:
        def __init__(self, value: Any) -> None:
            self.value = value
            self.calls: list[str] = []

        def info(self, *, section: str) -> Any:
            self.calls.append(section)
            return self.value

    provider = object.__new__(module.LiveInventoryProvider)
    provider._redis = Redis(
        {
            "db0": {"keys": 2, "expires": 1, "avg_ttl": 10},
            "db7": {"keys": 3, "expires": 0, "avg_ttl": 0},
        }
    )

    assert provider.redis_surface() == module.RedisSurface(2, 5)
    assert provider._redis.calls == ["keyspace"]

    provider._redis = Redis({})
    assert provider.redis_surface() == module.RedisSurface(0, 0)

    for invalid in (
        {"private-key": {"keys": 1}},
        {"db1": {"keys": -1}},
        {"db1": {"keys": True}},
        {"db1": "keys=1"},
    ):
        provider._redis = Redis(invalid)
        with pytest.raises(module.InventoryFailure, match="redis.enumeration_failed"):
            provider.redis_surface()

    class DeniedRedis:
        def info(self, *, section: str) -> Any:
            raise RuntimeError("private-provider-detail")

    provider._redis = DeniedRedis()
    with pytest.raises(module.InventoryFailure) as exc_info:
        provider.redis_surface()
    assert str(exc_info.value) == "redis.enumeration_failed"


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
            with psycopg.connect(database_url) as connection:
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
                assert provider.database_global_counts() == module.DatabaseGlobalCounts(
                    1, 0, 0
                )
                descriptor_policy = profile["schema_descriptor"]
                observed_descriptor = provider.database_schema_descriptor()
                assert observed_descriptor == module.SchemaDescriptor(
                    descriptor_policy["sha256"], descriptor_policy["counts"]
                )
                assert len(observed) - 1 == profile["table_count"]
        with psycopg.connect(database_url) as connection:
            provider = object.__new__(module.LiveInventoryProvider)
            provider._connection = connection
            baseline = provider.database_schema_descriptor()

            connection.execute("SET LOCAL search_path = pg_catalog")
            assert provider.database_schema_descriptor() == baseline

            def isolated_change(
                statement: LiteralString,
                *,
                count_changes: tuple[str, ...] = (),
            ) -> None:
                before = provider.database_schema_descriptor()
                connection.execute("SAVEPOINT descriptor_isolated_change")
                connection.execute(statement)
                changed = provider.database_schema_descriptor()
                assert changed.sha256 != before.sha256
                for section in count_changes:
                    assert changed.counts[section] != before.counts[section]
                connection.execute("ROLLBACK TO SAVEPOINT descriptor_isolated_change")

            for statement in (
                "CREATE DOMAIN public.descriptor_probe_domain AS text",
                "CREATE TYPE public.descriptor_probe_range AS RANGE "
                "(subtype = integer)",
                "CREATE TYPE public.descriptor_probe_composite AS (value integer)",
                "CREATE TYPE public.descriptor_probe_shell",
            ):
                isolated_change(
                    statement,
                    count_changes=("namespace_objects", "types"),
                )
            isolated_change(
                "ALTER TABLE public.doctor OWNER TO pg_database_owner",
            )
            for statement in (
                "GRANT SELECT ON TABLE public.doctor TO PUBLIC",
                "GRANT SELECT (id) ON TABLE public.doctor TO PUBLIC",
                "REVOKE EXECUTE ON FUNCTION public.typeid_parse(text) FROM PUBLIC",
                "REVOKE USAGE ON TYPE public.zoommeetingstatus FROM PUBLIC",
                "REVOKE USAGE ON SCHEMA public FROM PUBLIC",
            ):
                isolated_change(statement)
            isolated_change(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                "GRANT SELECT ON TABLES TO PUBLIC",
                count_changes=("default_privileges",),
            )
            isolated_change(
                "CREATE ROLE descriptor_probe_default_owner; "
                "ALTER DEFAULT PRIVILEGES FOR ROLE "
                "descriptor_probe_default_owner IN SCHEMA public "
                "GRANT SELECT ON TABLES TO PUBLIC",
                count_changes=("default_privileges",),
            )

            connection.execute("SAVEPOINT descriptor_timezone_stability")
            connection.execute(
                "CREATE TABLE public.descriptor_timezone_probe ("
                "captured_at timestamptz NOT NULL DEFAULT "
                "'2026-01-02 03:04:05+00'::timestamptz)"
            )
            connection.execute("SET LOCAL TimeZone = 'America/New_York'")
            new_york = provider.database_schema_descriptor()
            connection.execute("SET LOCAL TimeZone = 'Pacific/Honolulu'")
            honolulu = provider.database_schema_descriptor()
            assert new_york == honolulu
            connection.execute("ROLLBACK TO SAVEPOINT descriptor_timezone_stability")

            connection.execute(
                "CREATE TYPE public.descriptor_probe_enum AS ENUM ('first')"
            )
            connection.execute(
                "CREATE TABLE public.descriptor_probe ("
                "id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY, "
                "base integer NOT NULL DEFAULT 7, "
                "generated integer GENERATED ALWAYS AS (base + 1) STORED, "
                "CONSTRAINT descriptor_probe_positive CHECK (base > 0))"
            )
            connection.execute(
                "CREATE INDEX descriptor_probe_generated_idx "
                "ON public.descriptor_probe (generated)"
            )
            connection.execute(
                "CREATE FUNCTION public.descriptor_probe_trigger() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN NEW.base := NEW.base; RETURN NEW; END $$"
            )
            connection.execute(
                "CREATE TRIGGER descriptor_probe_trigger BEFORE INSERT "
                "ON public.descriptor_probe FOR EACH ROW "
                "EXECUTE FUNCTION public.descriptor_probe_trigger()"
            )
            connection.execute(
                "ALTER TABLE public.descriptor_probe ENABLE ROW LEVEL SECURITY"
            )
            connection.execute(
                "CREATE POLICY descriptor_probe_policy ON public.descriptor_probe "
                "USING (base > 0)"
            )
            connection.execute(
                "CREATE VIEW public.descriptor_probe_view AS "
                "SELECT base, generated FROM public.descriptor_probe"
            )
            connection.execute(
                "CREATE MATERIALIZED VIEW public.descriptor_probe_materialized AS "
                "SELECT base FROM public.descriptor_probe WITH NO DATA"
            )
            connection.execute(
                "CREATE FUNCTION public.descriptor_probe_routine(value integer = 2) "
                "RETURNS integer LANGUAGE sql IMMUTABLE AS $$ SELECT value + 1 $$"
            )
            expanded = provider.database_schema_descriptor()
            assert expanded.sha256 != baseline.sha256
            assert all(
                expanded.counts[section] > baseline.counts[section]
                for section in (
                    "namespace_objects",
                    "relations",
                    "columns",
                    "constraints",
                    "indexes",
                    "triggers",
                    "trigger_functions",
                    "rls_policies",
                    "types",
                    "enum_labels",
                    "views",
                    "routines",
                    "authorizations",
                )
            )

            for statement in (
                "ALTER TABLE public.descriptor_probe ALTER COLUMN base SET DEFAULT 8",
                "ALTER TABLE public.descriptor_probe ALTER COLUMN id "
                "SET GENERATED BY DEFAULT",
                "ALTER TABLE public.descriptor_probe "
                "DROP CONSTRAINT descriptor_probe_positive; "
                "ALTER TABLE public.descriptor_probe ADD CONSTRAINT "
                "descriptor_probe_positive CHECK (base > 1)",
                "DROP INDEX public.descriptor_probe_generated_idx; "
                "CREATE INDEX descriptor_probe_generated_idx "
                "ON public.descriptor_probe (base)",
                "ALTER TABLE public.descriptor_probe FORCE ROW LEVEL SECURITY",
                "ALTER TABLE public.descriptor_probe "
                "DISABLE TRIGGER descriptor_probe_trigger",
                "ALTER POLICY descriptor_probe_policy ON public.descriptor_probe "
                "USING (base > 1)",
                "ALTER TYPE public.descriptor_probe_enum "
                "RENAME VALUE 'first' TO 'second'",
                "CREATE OR REPLACE FUNCTION public.descriptor_probe_trigger() "
                "RETURNS trigger LANGUAGE plpgsql AS "
                "$$ BEGIN NEW.base := NEW.base + 0; RETURN NEW; END $$",
                "CREATE OR REPLACE FUNCTION "
                "public.descriptor_probe_routine(value integer = 2) "
                "RETURNS integer LANGUAGE sql IMMUTABLE "
                "AS $$ SELECT value + 2 $$",
                "CREATE OR REPLACE VIEW public.descriptor_probe_view AS "
                "SELECT base, generated FROM public.descriptor_probe "
                "WHERE base > 0",
                "DROP MATERIALIZED VIEW public.descriptor_probe_materialized; "
                "CREATE MATERIALIZED VIEW public.descriptor_probe_materialized AS "
                "SELECT generated AS base FROM public.descriptor_probe WITH NO DATA",
                "DROP VIEW public.descriptor_probe_view; "
                "DROP INDEX public.descriptor_probe_generated_idx; "
                "ALTER TABLE public.descriptor_probe DROP COLUMN generated; "
                "ALTER TABLE public.descriptor_probe ADD COLUMN generated integer "
                "GENERATED ALWAYS AS (base + 2) STORED; "
                "CREATE INDEX descriptor_probe_generated_idx "
                "ON public.descriptor_probe (generated); "
                "CREATE VIEW public.descriptor_probe_view AS "
                "SELECT base, generated FROM public.descriptor_probe",
            ):
                isolated_change(statement)
            connection.rollback()
        with psycopg.connect(database_url, autocommit=True) as temporary_connection:
            temporary_connection.execute(
                "CREATE TEMP SEQUENCE unexpected_temp_sequence"
            )
            with psycopg.connect(database_url, autocommit=True) as connection:
                connection.execute("CREATE SCHEMA unexpected_inventory_surface")
                connection.execute(
                    "CREATE SEQUENCE public.unexpected_inventory_sequence"
                )
                connection.execute("SELECT pg_catalog.lo_create(0)")
                provider = object.__new__(module.LiveInventoryProvider)
                provider._connection = connection
                assert provider.database_global_counts() == module.DatabaseGlobalCounts(
                    2, 2, 1
                )

        repeat_name = f"inventory_repeat_{secrets.token_hex(8)}"
        repeat_url = urllib.parse.urlunsplit(parsed._replace(path=f"/{repeat_name}"))
        repeat_created = False
        try:
            with psycopg.connect(base_url, autocommit=True) as admin:
                admin.execute(
                    sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(
                        sql.Identifier(repeat_name)
                    )
                )
            repeat_created = True
            monkeypatch.setenv("TEST_DATABASE_URL", repeat_url)
            repeat_config = Config(os.fspath(ROOT / "alembic.ini"))
            repeat_config.set_main_option(
                "script_location", os.fspath(ROOT / "migrations")
            )
            for profile_name in ("pre", "post"):
                profile = policy["profiles"][profile_name]
                command.upgrade(repeat_config, profile["revision"])
                with psycopg.connect(repeat_url) as connection:
                    provider = object.__new__(module.LiveInventoryProvider)
                    provider._connection = connection
                    descriptor = profile["schema_descriptor"]
                    assert provider.database_schema_descriptor() == (
                        module.SchemaDescriptor(
                            descriptor["sha256"], descriptor["counts"]
                        )
                    )
        finally:
            if repeat_created:
                with psycopg.connect(base_url, autocommit=True) as admin:
                    admin.execute(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = %s AND pid <> pg_backend_pid()",
                        (repeat_name,),
                    )
                    admin.execute(
                        sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                            sql.Identifier(repeat_name)
                        )
                    )
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
    assert "SET LOCAL search_path = pg_catalog, public" in source
    assert "pg_catalog.pg_identify_object_as_address" in source
    assert "pg_catalog.aclexplode" in source
    assert "SET LOCAL lock_timeout" in source
    assert "current_setting('server_version_num')" in source
    assert "server_version // 10_000 != 16" in source
    assert "LEFT JOIN pg_catalog.pg_attribute" in source
    assert "pg_catalog.pg_get_constraintdef" in source
    assert "pg_catalog.pg_get_indexdef" in source
    assert "pg_catalog.pg_get_triggerdef" in source
    assert "pg_catalog.pg_get_functiondef" in source
    assert "pg_catalog.pg_get_viewdef" in source
    assert "row_to_json(source)::text" in source
    assert "include_system=True" in source
