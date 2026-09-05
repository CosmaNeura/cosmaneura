#!/usr/bin/env python3
"""Validate a closed-world staging evidence bundle without provider access.

Success proves only local contract integrity and cross-binding.  The committed
server-scope policy remains unconfigured and cryptographic attestation
verification is deliberately unimplemented, so a valid bundle remains
ineligible to activate restore or sanitizer execution.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

POLICY_PATH = (
    Path(__file__).resolve().parents[1]
    / "contracts/staging-rehearsal/v1/evidence-manifest-policy.json"
)
SYNTHETIC_POLICY_PATH = (
    Path(__file__).resolve().parents[1]
    / "contracts/staging-rehearsal/v1/synthetic-data-policy.json"
)
SERVER_SCOPE_POLICY_PATH = (
    Path(__file__).resolve().parents[1]
    / "contracts/staging-rehearsal/v1/server-scope-evidence-policy.json"
)
POLICY_SHA256 = "8b1cd30a90782d5596b44415e0abb9e934c78aeaca9bc7960e226d1feee8df53"
POLICY_SEMANTIC_SHA256 = (
    "0ada65005a52e30cda252d2cd690c3980fa63973d8b8d97e9317194265215511"
)
MAXIMUM_FILE_BYTES = 8 * 1024 * 1024
MAXIMUM_SERVER_SCOPE_DATABASES = 64
MAXIMUM_SERVER_SCOPE_RECORDS_PER_SURFACE = 100_000
MAXIMUM_SERVER_SCOPE_TOTAL_RECORDS = 500_000
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
HEX_RE = re.compile(r"^[0-9a-f]{64}$")
RUN_RE = re.compile(r"^[1-9][0-9]{0,19}$")
REVISION_RE = re.compile(r"^[0-9a-f]{12}$")
KEY_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
AZURE_POSTGRES_HOST_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.postgres\.database\.azure\.com$"
)
MANIFEST_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{11,63}$")
UTC_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
SERVER_SCOPE_UTC_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$"
)
SERVER_SCOPE_SERVER_SURFACES = frozenset(
    "databases roles memberships role_settings database_privileges tablespaces "
    "replication_slots replication_origins prepared_transactions subscriptions "
    "parameter_privileges shared_security_labels shared_descriptions settings".split()
)
SERVER_SCOPE_DATABASE_SURFACES = frozenset(
    "schema user_relations shared_dependencies extensions foreign_data_wrappers "
    "foreign_servers foreign_tables foreign_privileges language_privileges "
    "user_mappings publications publication_memberships publication_namespaces "
    "subscription_state subscription_relations security_labels event_triggers "
    "large_objects".split()
)


class EvidenceFailure(RuntimeError):
    """Bounded contract failure safe to report without artifact contents."""


_VALIDATED_BUNDLE_SEAL = object()
_LAUNCH_DIGEST_VALIDATION_SEAL = object()


@dataclass(frozen=True, slots=True, init=False)
class _LaunchDigestValidation:
    """Opaque proof that one exact expected digest map matched one bundle read."""

    file_sha256s: tuple[tuple[str, str], ...]
    _seal: object = dataclass_field(repr=False, compare=False)

    def __init__(self) -> None:
        raise EvidenceFailure("bundle.launch_binding")

    def _matches(self, expected_file_sha256s: tuple[tuple[str, str], ...]) -> bool:
        try:
            return (
                type(self) is _LaunchDigestValidation
                and getattr(self, "_seal", None) is _LAUNCH_DIGEST_VALIDATION_SEAL
                and type(expected_file_sha256s) is tuple
                and self.file_sha256s == tuple(sorted(expected_file_sha256s))
            )
        except (AttributeError, TypeError):
            return False


@dataclass(frozen=True, slots=True, init=False)
class ValidatedEvidenceBundle:
    """Opaque immutable bytes minted only after complete bundle validation."""

    result_bytes: bytes
    server_scope_bytes: bytes
    manifest_bytes: bytes
    _validation_seal: object = dataclass_field(repr=False, compare=False)
    _manifest_validation_seal: object | None = dataclass_field(
        repr=False, compare=False
    )
    _launch_digest_validation: _LaunchDigestValidation | None = dataclass_field(
        repr=False, compare=False
    )

    def __init__(self) -> None:
        raise EvidenceFailure("validated_bundle.invalid")

    @classmethod
    def _from_validated_bytes(
        cls,
        *,
        result_bytes: bytes,
        server_scope_bytes: bytes,
    ) -> ValidatedEvidenceBundle:
        if (
            cls is not ValidatedEvidenceBundle
            or type(result_bytes) is not bytes
            or type(server_scope_bytes) is not bytes
        ):
            raise EvidenceFailure("validated_bundle.invalid")
        value = object.__new__(cls)
        object.__setattr__(value, "result_bytes", result_bytes)
        object.__setattr__(value, "server_scope_bytes", server_scope_bytes)
        object.__setattr__(value, "manifest_bytes", b"")
        object.__setattr__(value, "_validation_seal", _VALIDATED_BUNDLE_SEAL)
        object.__setattr__(value, "_manifest_validation_seal", None)
        object.__setattr__(value, "_launch_digest_validation", None)
        return value

    @classmethod
    def _from_validated_manifest_bytes(
        cls,
        *,
        result_bytes: bytes,
        server_scope_bytes: bytes,
        manifest_bytes: bytes,
    ) -> ValidatedEvidenceBundle:
        if cls is not ValidatedEvidenceBundle or type(manifest_bytes) is not bytes:
            raise EvidenceFailure("validated_bundle.invalid")
        value = cls._from_validated_bytes(
            result_bytes=result_bytes,
            server_scope_bytes=server_scope_bytes,
        )
        object.__setattr__(value, "manifest_bytes", manifest_bytes)
        object.__setattr__(value, "_manifest_validation_seal", _VALIDATED_BUNDLE_SEAL)
        return value

    def _is_validated_snapshot(self) -> bool:
        return (
            type(self) is ValidatedEvidenceBundle
            and getattr(self, "_validation_seal", None) is _VALIDATED_BUNDLE_SEAL
            and type(getattr(self, "result_bytes", None)) is bytes
            and type(getattr(self, "server_scope_bytes", None)) is bytes
            and type(getattr(self, "manifest_bytes", None)) is bytes
        )

    def _has_validated_manifest_snapshot(self) -> bool:
        return (
            self._is_validated_snapshot()
            and getattr(self, "_manifest_validation_seal", None)
            is _VALIDATED_BUNDLE_SEAL
            and 0 < len(self.manifest_bytes) <= MAXIMUM_FILE_BYTES
        )

    def _has_launch_digest_binding(
        self, expected_file_sha256s: tuple[tuple[str, str], ...]
    ) -> bool:
        proof = getattr(self, "_launch_digest_validation", None)
        return (
            self._has_validated_manifest_snapshot()
            and type(proof) is _LaunchDigestValidation
            and proof._matches(expected_file_sha256s)
        )

    def _parse_projection(self, raw: bytes) -> dict[str, Any]:
        if not self._is_validated_snapshot():
            raise EvidenceFailure("validated_bundle.invalid")
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise EvidenceFailure("validated_bundle.invalid") from None
        if not isinstance(value, dict):
            raise EvidenceFailure("validated_bundle.invalid")
        return value

    @property
    def result(self) -> dict[str, Any]:
        """Return a caller-owned copy of the validated public projection."""

        return self._parse_projection(self.result_bytes)

    @property
    def server_scope_receipt(self) -> dict[str, Any]:
        """Return a caller-owned copy parsed from the authoritative file bytes."""

        return self._parse_projection(self.server_scope_bytes)

    @property
    def manifest(self) -> dict[str, Any]:
        """Return a caller-owned projection of the exact validated manifest."""

        if not self._has_validated_manifest_snapshot():
            raise EvidenceFailure("validated_bundle.invalid")
        return self._parse_projection(self.manifest_bytes)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest(value: bytes) -> str:
    return f"sha256:{_sha256(value)}"


def _parse_utc(value: Any, code: str) -> datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise EvidenceFailure(code)
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        raise EvidenceFailure(code) from None
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise EvidenceFailure(code)
    return parsed


def _parse_server_scope_utc(value: Any, code: str) -> datetime:
    if not isinstance(value, str) or SERVER_SCOPE_UTC_RE.fullmatch(value) is None:
        raise EvidenceFailure(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except (ValueError, AttributeError):
        raise EvidenceFailure(code) from None
    if parsed.isoformat().replace("+00:00", "Z") != value:
        raise EvidenceFailure(code)
    return parsed


def _expect_keys(value: Any, expected: Collection[str], code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(expected):
        raise EvidenceFailure(code)
    return value


def _contains_boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return True
    if isinstance(value, Mapping):
        return any(_contains_boolean(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_boolean(item) for item in value)
    return False


def _read_regular(path: Path, code: str) -> bytes:
    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid not in {0, os.geteuid()}
            or before.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            or before.st_size <= 0
            or before.st_size > MAXIMUM_FILE_BYTES
        ):
            raise EvidenceFailure(code)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            after = os.fstat(descriptor)
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise EvidenceFailure(code)
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                contents = handle.read(MAXIMUM_FILE_BYTES + 1)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    except EvidenceFailure:
        raise
    except Exception:
        raise EvidenceFailure(code) from None
    if not contents or len(contents) > MAXIMUM_FILE_BYTES:
        raise EvidenceFailure(code)
    return contents


def _load_json(raw: bytes, code: str) -> Mapping[str, Any]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise EvidenceFailure(code) from None
    try:
        invalid = not isinstance(value, Mapping) or _contains_boolean(value)
    except RecursionError:
        raise EvidenceFailure(code) from None
    if invalid:
        raise EvidenceFailure(code)
    return value


def _load_server_scope_json(raw: bytes) -> Mapping[str, Any]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise EvidenceFailure("server_scope.invalid") from None
    if not isinstance(value, Mapping):
        raise EvidenceFailure("server_scope.invalid")
    without_pass = dict(value)
    passed = without_pass.pop("pass", None)
    try:
        invalid = passed is not True or _contains_boolean(without_pass)
    except RecursionError:
        raise EvidenceFailure("server_scope.invalid") from None
    if invalid:
        raise EvidenceFailure("server_scope.invalid")
    return value


def load_policy(path: Path) -> tuple[Mapping[str, Any], str]:
    try:
        if path.resolve(strict=True) != POLICY_PATH.resolve(strict=True):
            raise EvidenceFailure("policy.untrusted_path")
    except EvidenceFailure:
        raise
    except Exception:
        raise EvidenceFailure("policy.unreadable") from None
    raw = _read_regular(path, "policy.unreadable")
    raw_sha256 = _sha256(raw)
    try:
        policy = json.loads(raw)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise EvidenceFailure("policy.invalid") from None
    if (
        raw_sha256 != POLICY_SHA256
        or not isinstance(policy, Mapping)
        or _sha256(_canonical(policy)) != POLICY_SEMANTIC_SHA256
        or policy.get("schema_version") != 2
        or policy.get("policy_id") != "cosmaneura-staging-evidence-manifest-v2"
    ):
        raise EvidenceFailure("policy.invalid")
    expected_policy_fields = {
        "schema_version",
        "policy_id",
        "repository",
        "protected_ref",
        "workflow_path",
        "event_name",
        "founder_actor",
        "signer",
        "source_resource",
        "hmac_key_versions",
        "maximum_lifetime_seconds",
        "required_files",
        "artifact_contracts",
        "policy_digests",
        "schema_fields",
        "activation_blockers",
    }
    if (
        set(policy) != expected_policy_fields
        or policy["hmac_key_versions"] != ["staging-inventory-hmac-v1"]
        or policy["maximum_lifetime_seconds"] != 900
        or set(policy["artifact_contracts"])
        != {"candidate", "inventory", "quiescence", "server_scope"}
        or set(policy["schema_fields"])
        != {
            "manifest",
            "artifact",
            "candidate",
            "inventory",
            "quiescence",
            "server_scope",
            "server_scope_surface",
            "server_scope_database_connected",
            "server_scope_database_disabled",
            "provenance",
            "signer_claims",
            "policy_digests",
            "source_resource",
        }
    ):
        raise EvidenceFailure("policy.invalid")
    return policy, raw_sha256


def _load_synthetic_policy(policy: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = _read_regular(SYNTHETIC_POLICY_PATH, "synthetic_policy.unreadable")
    if _digest(raw) != policy["policy_digests"]["synthetic_data"]:
        raise EvidenceFailure("synthetic_policy.digest")
    try:
        synthetic = json.loads(raw)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise EvidenceFailure("synthetic_policy.invalid") from None
    if (
        not isinstance(synthetic, Mapping)
        or synthetic.get("schema_version") != 1
        or synthetic.get("policy_id") != "cosmaneura-staging-synthetic-inventory-v1"
    ):
        raise EvidenceFailure("synthetic_policy.invalid")
    return synthetic


def _server_scope_policy_pin_valid(value: Any, *, configured: bool) -> bool:
    if not isinstance(value, Mapping) or set(value) != {"count", "hmac"}:
        return False
    count = value["count"]
    digest = value["hmac"]
    if isinstance(count, bool) or not isinstance(count, int):
        return False
    if configured:
        return 0 <= count <= MAXIMUM_SERVER_SCOPE_RECORDS_PER_SURFACE and bool(
            isinstance(digest, str) and HEX_RE.fullmatch(digest)
        )
    return (
        -1 <= count <= MAXIMUM_SERVER_SCOPE_RECORDS_PER_SURFACE
        and isinstance(digest, str)
        and (bool(HEX_RE.fullmatch(digest)) or digest.startswith("CONFIGURE_"))
    )


def _load_server_scope_policy(policy: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = _read_regular(SERVER_SCOPE_POLICY_PATH, "server_scope_policy.unreadable")
    if _digest(raw) != policy["policy_digests"]["server_scope"]:
        raise EvidenceFailure("server_scope_policy.digest")
    try:
        server_policy = json.loads(raw)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise EvidenceFailure("server_scope_policy.invalid") from None
    server_policy = _expect_keys(
        server_policy,
        {
            "schema_version",
            "policy_id",
            "activation_ready",
            "source",
            "hmac",
            "required_settings",
            "required_visibility",
            "expected_server_surfaces",
            "expected_databases",
            "evidence",
        },
        "server_scope_policy.invalid",
    )
    configured = server_policy["activation_ready"] is True
    source = _expect_keys(
        server_policy["source"],
        {
            "resource_id",
            "revision",
            "host",
            "port",
            "maintenance_database",
            "primary_database",
            "postgres_major",
        },
        "server_scope_policy.invalid",
    )
    hmac_policy = _expect_keys(
        server_policy["hmac"],
        {"algorithm", "key_version"},
        "server_scope_policy.invalid",
    )
    server_surfaces = _expect_keys(
        server_policy["expected_server_surfaces"],
        SERVER_SCOPE_SERVER_SURFACES,
        "server_scope_policy.invalid",
    )
    visibility = _expect_keys(
        server_policy["required_visibility"],
        {"pg_authid", "pg_monitor", "pg_read_all_settings"},
        "server_scope_policy.invalid",
    )
    evidence = _expect_keys(
        server_policy["evidence"],
        {"aggregate_only", "forbidden_raw_values"},
        "server_scope_policy.invalid",
    )
    if (
        isinstance(server_policy["schema_version"], bool)
        or not isinstance(server_policy["schema_version"], int)
        or server_policy["schema_version"] != 1
        or server_policy["policy_id"] != "cosmaneura-staging-server-scope-evidence-v1"
        or not isinstance(server_policy["activation_ready"], bool)
        or any(
            not isinstance(source[field], str) or not source[field]
            for field in (
                "resource_id",
                "revision",
                "host",
                "maintenance_database",
                "primary_database",
            )
        )
        or AZURE_POSTGRES_HOST_RE.fullmatch(source["host"]) is None
        or source["primary_database"] != policy["source_resource"]["database_name"]
        or isinstance(source["port"], bool)
        or not isinstance(source["port"], int)
        or source["port"] != 5432
        or isinstance(source["postgres_major"], bool)
        or not isinstance(source["postgres_major"], int)
        or source["postgres_major"] != 16
        or hmac_policy["algorithm"] != "HMAC-SHA-256"
        or not isinstance(hmac_policy["key_version"], str)
        or not all(value is True for value in visibility.values())
        or evidence["aggregate_only"] is not True
        or not isinstance(evidence["forbidden_raw_values"], list)
        or not all(
            _server_scope_policy_pin_valid(pin, configured=configured)
            for pin in server_surfaces.values()
        )
    ):
        raise EvidenceFailure("server_scope_policy.invalid")
    if configured:
        if (
            source["resource_id"] != policy["source_resource"]["postgres_server_id"]
            or SHA_RE.fullmatch(source["revision"]) is None
            or KEY_VERSION_RE.fullmatch(hmac_policy["key_version"]) is None
            or hmac_policy["key_version"].startswith("CONFIGURE_")
        ):
            raise EvidenceFailure("server_scope_policy.invalid")
    elif (
        source["resource_id"] != "CONFIGURE_EXACT_STAGING_POSTGRES_RESOURCE_ID"
        or source["revision"] != "CONFIGURE_EXACT_SOURCE_REVISION"
        or hmac_policy["key_version"] != "CONFIGURE_OWNER_CONTROLLED_KEY_VERSION"
    ):
        raise EvidenceFailure("server_scope_policy.invalid")
    settings = server_policy["required_settings"]
    if (
        not isinstance(settings, list)
        or not settings
        or not all(isinstance(item, str) and item for item in settings)
        or settings != sorted(set(settings))
    ):
        raise EvidenceFailure("server_scope_policy.invalid")
    databases = server_policy["expected_databases"]
    if (
        not isinstance(databases, list)
        or not 1 <= len(databases) <= MAXIMUM_SERVER_SCOPE_DATABASES
    ):
        raise EvidenceFailure("server_scope_policy.invalid")
    identities: set[str] = set()
    primary_count = 0
    for database in databases:
        database = _expect_keys(
            database,
            {"identity_hmac", "is_template", "allow_connections", "status", "surfaces"},
            "server_scope_policy.invalid",
        )
        identity = database["identity_hmac"]
        if (
            not isinstance(identity, str)
            or identity in identities
            or not isinstance(database["is_template"], bool)
            or not isinstance(database["allow_connections"], bool)
        ):
            raise EvidenceFailure("server_scope_policy.invalid")
        identities.add(identity)
        if configured and HEX_RE.fullmatch(identity) is None:
            raise EvidenceFailure("server_scope_policy.invalid")
        if not configured and not (
            HEX_RE.fullmatch(identity) or identity.startswith("CONFIGURE_")
        ):
            raise EvidenceFailure("server_scope_policy.invalid")
        status = database["status"]
        if not isinstance(status, str):
            raise EvidenceFailure("server_scope_policy.invalid")
        if status == "connections_disabled":
            if (
                database["allow_connections"] is not False
                or database["is_template"] is not True
                or database["surfaces"] is not None
            ):
                raise EvidenceFailure("server_scope_policy.invalid")
            continue
        if status not in {"primary_inventory", "empty"}:
            raise EvidenceFailure("server_scope_policy.invalid")
        if database["allow_connections"] is not True:
            raise EvidenceFailure("server_scope_policy.invalid")
        if status == "primary_inventory":
            primary_count += 1
            if database["is_template"] is not False:
                raise EvidenceFailure("server_scope_policy.invalid")
        surfaces = _expect_keys(
            database["surfaces"],
            SERVER_SCOPE_DATABASE_SURFACES,
            "server_scope_policy.invalid",
        )
        if not all(
            _server_scope_policy_pin_valid(pin, configured=configured)
            for pin in surfaces.values()
        ):
            raise EvidenceFailure("server_scope_policy.invalid")
        if status == "empty" and any(
            surfaces[name]["count"] != 0 for name in ("user_relations", "large_objects")
        ):
            raise EvidenceFailure("server_scope_policy.invalid")
    if primary_count != 1:
        raise EvidenceFailure("server_scope_policy.invalid")
    return server_policy


def _inventory_key_sets(synthetic: Mapping[str, Any]) -> dict[str, set[str]]:
    try:
        profile = synthetic["profiles"]["pre"]
        tables = synthetic["database"]["tables"]
        expected_tables = set(tables) - set(profile["excluded_tables"])
        schema_sections = set(profile["schema_descriptor"]["counts"])
        containers = set(synthetic["blob"]["containers"])
    except (KeyError, TypeError):
        raise EvidenceFailure("synthetic_policy.invalid") from None
    if len(expected_tables) != profile["table_count"]:
        raise EvidenceFailure("synthetic_policy.invalid")
    rules = {
        f"database.{table_name}.{rule['id']}"
        for table_name in expected_tables
        for rule in tables[table_name].get("rules", [])
        if "pre" in rule.get("profiles", ["pre", "post"])
    }
    identities = {
        table_name
        for table_name in expected_tables
        if tables[table_name]["disposition"] != "required_empty"
        and tables[table_name].get("identity_columns")
    }
    counts = {
        "database.non_system_schemas",
        "database.sequences",
        "database.large_objects",
        "database.alembic_version",
        "redis.logical_databases_with_keys",
        "redis.keys",
        *(f"database.schema.{name}" for name in schema_sections),
        *(f"database.{name}" for name in expected_tables),
        *(f"blob.{name}" for name in containers),
    }
    return {
        "counts": counts,
        "rule_violation_counts": rules,
        "identity_hmacs": identities,
        "state_hmacs": expected_tables,
        "blob_state_hmacs": containers,
    }


def _read_bundle(
    bundle: Path,
    policy: Mapping[str, Any],
) -> dict[str, bytes]:
    try:
        if not bundle.is_absolute() or bundle.resolve(strict=True) != bundle:
            raise EvidenceFailure("bundle.unsafe")
        mode = bundle.lstat().st_mode
        if not stat.S_ISDIR(mode) or mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise EvidenceFailure("bundle.unsafe")
        children = list(bundle.iterdir())
    except EvidenceFailure:
        raise
    except Exception:
        raise EvidenceFailure("bundle.unsafe") from None
    required = set(policy["required_files"])
    if {child.name for child in children} != required:
        raise EvidenceFailure("bundle.file_set")
    return {
        name: _read_regular(bundle / name, f"bundle.file_unsafe.{name}")
        for name in sorted(required)
    }


def _snapshot_bundle_bytes(
    files: Mapping[str, bytes],
    policy: Mapping[str, Any],
) -> dict[str, bytes]:
    """Detach one exact immutable byte map before validating any contents."""

    try:
        required_names = tuple(policy["required_files"])
        if type(files) is not dict or len(files) != len(required_names):
            raise EvidenceFailure("bundle.byte_map")
        items = tuple(files.items())
        if (
            any(type(name) is not str for name, _value in items)
            or {name for name, _value in items} != set(required_names)
            or any(
                type(value) is not bytes or not 1 <= len(value) <= MAXIMUM_FILE_BYTES
                for _name, value in items
            )
        ):
            raise EvidenceFailure("bundle.byte_map")
        return {name: bytes(value) for name, value in sorted(items)}
    except EvidenceFailure:
        raise
    except Exception:
        raise EvidenceFailure("bundle.byte_map") from None


def _verify_checksums(files: Mapping[str, bytes]) -> None:
    expected_names = sorted(set(files) - {"SHA256SUMS"})
    expected = "".join(
        f"{_sha256(files[name])}  {name}\n" for name in expected_names
    ).encode()
    if files["SHA256SUMS"] != expected:
        raise EvidenceFailure("bundle.checksums")


def _verify_expected_file_sha256s(
    files: Mapping[str, bytes], expected_file_sha256s: Mapping[str, str]
) -> tuple[tuple[str, str], ...]:
    try:
        if type(expected_file_sha256s) is not dict or len(expected_file_sha256s) != len(
            files
        ):
            raise EvidenceFailure("bundle.launch_binding")
        items = tuple(expected_file_sha256s.items())
        expected = dict(items)
    except EvidenceFailure:
        raise
    except Exception:
        raise EvidenceFailure("bundle.launch_binding")
    if (
        any(type(name) is not str for name, _value in items)
        or set(expected) != set(files)
        or any(
            type(value) is not str or DIGEST_RE.fullmatch(value) is None
            for value in expected.values()
        )
        or any(expected[name] != _digest(raw) for name, raw in files.items())
    ):
        raise EvidenceFailure("bundle.launch_binding")
    return tuple((name, _digest(files[name])) for name in sorted(files))


def _validate_provenance(
    provenance: Any,
    claims: Any,
    policy: Mapping[str, Any],
) -> None:
    fields = policy["schema_fields"]
    provenance = _expect_keys(
        provenance,
        fields["provenance"],
        "provenance.schema",
    )
    claims = _expect_keys(
        claims,
        fields["signer_claims"],
        "signer_claims.schema",
    )
    if (
        any(not isinstance(value, str) for value in provenance.values())
        or any(not isinstance(value, str) for value in claims.values())
        or provenance["repository"] != policy["repository"]
        or provenance["protected_ref"] != policy["protected_ref"]
        or provenance["workflow_path"] != policy["workflow_path"]
        or provenance["event_name"] != policy["event_name"]
        or SHA_RE.fullmatch(provenance["workflow_sha"]) is None
        or RUN_RE.fullmatch(provenance["run_id"]) is None
        or RUN_RE.fullmatch(provenance["run_attempt"]) is None
        or claims["issuer"] != policy["signer"]["issuer"]
        or claims["subject"] != policy["signer"]["subject"]
        or claims["repository"] != provenance["repository"]
        or claims["ref"] != provenance["protected_ref"]
        or claims["sha"] != provenance["workflow_sha"]
        or claims["workflow_sha"] != provenance["workflow_sha"]
        or claims["workflow_ref"]
        != f"{policy['repository']}/{policy['workflow_path']}@{policy['protected_ref']}"
        or claims["run_id"] != provenance["run_id"]
        or claims["run_attempt"] != provenance["run_attempt"]
        or claims["actor"] != policy["founder_actor"]
        or claims["triggering_actor"] != policy["founder_actor"]
    ):
        raise EvidenceFailure("provenance.mismatch")


def _validate_time(
    approved_restore_time: Any,
    observed_at: Any,
    expires_at: Any,
    now: datetime,
    policy: Mapping[str, Any],
) -> None:
    approved_restore = _parse_utc(
        approved_restore_time,
        "evidence.approved_restore_time",
    )
    observed = _parse_utc(observed_at, "evidence.time")
    expires = _parse_utc(expires_at, "evidence.time")
    if approved_restore != observed:
        raise EvidenceFailure("evidence.approved_restore_time_mismatch")
    lifetime = int(policy["maximum_lifetime_seconds"])
    if (
        now.tzinfo is None
        or now.utcoffset() != UTC.utcoffset(now)
        or observed > now
        or now >= expires
        or (expires - observed).total_seconds() > lifetime
        or (now - observed).total_seconds() > lifetime
    ):
        raise EvidenceFailure("evidence.stale")


def _validate_receipt(value: Mapping[str, Any], code: str) -> str:
    supplied = value.get("receipt_sha256")
    if not isinstance(supplied, str) or HEX_RE.fullmatch(supplied) is None:
        raise EvidenceFailure(code)
    unsigned = dict(value)
    del unsigned["receipt_sha256"]
    if _sha256(_canonical(unsigned)) != supplied:
        raise EvidenceFailure(code)
    return f"sha256:{supplied}"


def _validate_common(
    artifact: Mapping[str, Any],
    manifest: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> None:
    for field in (
        "source_sha",
        "candidate_sha",
        "source_resource",
        "observed_at",
        "expires_at",
        "provenance",
        "signer_claims",
    ):
        if artifact[field] != manifest[field]:
            raise EvidenceFailure(f"artifact.binding.{field}")
    _validate_provenance(artifact["provenance"], artifact["signer_claims"], policy)


def _validate_inventory(
    inventory: Mapping[str, Any],
    manifest: Mapping[str, Any],
    policy: Mapping[str, Any],
    synthetic: Mapping[str, Any],
) -> str:
    _expect_keys(inventory, policy["schema_fields"]["inventory"], "inventory.schema")
    _validate_common(inventory, manifest, policy)
    digest_maps = (
        "identity_hmacs",
        "state_hmacs",
        "blob_state_hmacs",
    )
    count_maps = ("counts", "rule_violation_counts")
    key_sets = _inventory_key_sets(synthetic)
    profile = synthetic["profiles"]["pre"]
    if (
        inventory["schema_version"] != 2
        or inventory["policy_id"] != "cosmaneura-staging-synthetic-inventory-v1"
        or inventory["policy_sha256"]
        != policy["policy_digests"]["synthetic_data"].removeprefix("sha256:")
        or not isinstance(inventory["approved_state_sha256"], str)
        or HEX_RE.fullmatch(inventory["approved_state_sha256"]) is None
        or inventory["hmac_key_version"] != manifest["hmac_key_version"]
        or inventory["phase"] != "pre"
        or inventory["outcome"] != "pass"
        or inventory["errors"] != []
        or any(
            not isinstance(inventory[name], Mapping)
            or set(inventory[name]) != key_sets[name]
            for name in key_sets
        )
        or any(
            not isinstance(inventory[name], Mapping)
            or any(not isinstance(key, str) for key in inventory[name])
            or any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in inventory[name].values()
            )
            for name in count_maps
        )
        or any(
            not isinstance(inventory[name], Mapping)
            or any(not isinstance(key, str) for key in inventory[name])
            or any(
                not isinstance(value, str) or HEX_RE.fullmatch(value) is None
                for value in inventory[name].values()
            )
            for name in digest_maps
        )
        or any(
            not isinstance(inventory[name], str)
            or HEX_RE.fullmatch(inventory[name]) is None
            for name in (
                "catalog_hmac",
                "schema_descriptor_sha256",
                "redis_state_hmac",
            )
        )
        or inventory["schema_descriptor_sha256"]
        != profile["schema_descriptor"]["sha256"]
        or any(value != 0 for value in inventory["rule_violation_counts"].values())
        or inventory["counts"]["database.non_system_schemas"] != 1
        or inventory["counts"]["database.sequences"] != 0
        or inventory["counts"]["database.large_objects"] != 0
        or inventory["counts"]["database.alembic_version"] != 1
        or inventory["counts"]["redis.logical_databases_with_keys"] != 0
        or inventory["counts"]["redis.keys"] != 0
        or any(
            inventory["counts"][f"database.schema.{name}"] != value
            for name, value in profile["schema_descriptor"]["counts"].items()
        )
        or any(
            inventory["counts"][f"database.{name}"] != 0
            for name in key_sets["state_hmacs"]
            if synthetic["database"]["tables"][name]["disposition"] == "required_empty"
        )
        or any(
            inventory["counts"][f"blob.{name}"] != 0
            for name, disposition in synthetic["blob"]["containers"].items()
            if disposition == "required_empty"
        )
    ):
        raise EvidenceFailure("inventory.invalid")
    return _validate_receipt(inventory, "inventory.receipt")


def _validate_candidate(
    candidate: Mapping[str, Any],
    manifest: Mapping[str, Any],
    policy: Mapping[str, Any],
    expected_schema_revision: str,
) -> str:
    _expect_keys(candidate, policy["schema_fields"]["candidate"], "candidate.schema")
    _validate_common(candidate, manifest, policy)
    expected_digests = {
        "evidence_manifest": manifest["policy_digests"]["evidence_manifest"],
        **policy["policy_digests"],
    }
    if (
        candidate["schema_version"] != 1
        or candidate["evidence_type"] != "candidate"
        or candidate["state"] != "verified"
        or isinstance(candidate["approval_pr_number"], bool)
        or not isinstance(candidate["approval_pr_number"], int)
        or candidate["approval_pr_number"] <= 0
        or candidate["protected_master_sha"] != manifest["provenance"]["workflow_sha"]
        or not isinstance(candidate["schema_revision"], str)
        or REVISION_RE.fullmatch(candidate["schema_revision"]) is None
        or candidate["schema_revision"] != expected_schema_revision
        or candidate["policy_digests"] != expected_digests
    ):
        raise EvidenceFailure("candidate.invalid")
    return _validate_receipt(candidate, "candidate.receipt")


def _validate_server_scope_pin(value: Any, code: str) -> int:
    value = _expect_keys(value, {"count", "hmac"}, code)
    count = value["count"]
    if (
        isinstance(count, bool)
        or not isinstance(count, int)
        or not 0 <= count <= MAXIMUM_SERVER_SCOPE_RECORDS_PER_SURFACE
        or not isinstance(value["hmac"], str)
        or HEX_RE.fullmatch(value["hmac"]) is None
    ):
        raise EvidenceFailure(code)
    return count


def _validate_server_scope(
    artifact: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any],
    policy: Mapping[str, Any],
    server_policy: Mapping[str, Any],
) -> str:
    fields = policy["schema_fields"]
    _expect_keys(artifact, fields["server_scope"], "server_scope.schema")
    started = _parse_server_scope_utc(
        artifact["capture_started_at"], "server_scope.capture_time"
    )
    completed = _parse_server_scope_utc(
        artifact["capture_completed_at"], "server_scope.capture_time"
    )
    observed = _parse_utc(manifest["observed_at"], "server_scope.capture_time")
    if (
        started > completed
        or completed > observed
        or (completed - started).total_seconds() > policy["maximum_lifetime_seconds"]
        or (observed - started).total_seconds() > policy["maximum_lifetime_seconds"]
    ):
        raise EvidenceFailure("server_scope.capture_time")
    if (
        isinstance(artifact["schema_version"], bool)
        or not isinstance(artifact["schema_version"], int)
        or artifact["schema_version"] != 1
        or artifact["policy_id"] != server_policy["policy_id"]
        or artifact["policy_sha256"]
        != policy["policy_digests"]["server_scope"].removeprefix("sha256:")
        or artifact["source_revision"] != manifest["source_sha"]
        or not isinstance(artifact["source_resource_hmac"], str)
        or HEX_RE.fullmatch(artifact["source_resource_hmac"]) is None
        or not isinstance(artifact["primary_database_identity_hmac"], str)
        or HEX_RE.fullmatch(artifact["primary_database_identity_hmac"]) is None
        or not isinstance(artifact["key_version"], str)
        or KEY_VERSION_RE.fullmatch(artifact["key_version"]) is None
        or isinstance(artifact["server_version_major"], bool)
        or not isinstance(artifact["server_version_major"], int)
        or artifact["server_version_major"] != server_policy["source"]["postgres_major"]
        or artifact["errors"] != []
        or artifact["pass"] is not True
        or not isinstance(artifact["evidence_hmac"], str)
        or HEX_RE.fullmatch(artifact["evidence_hmac"]) is None
    ):
        raise EvidenceFailure("server_scope.invalid")

    server_surfaces = _expect_keys(
        artifact["server_surfaces"],
        SERVER_SCOPE_SERVER_SURFACES,
        "server_scope.server_surfaces",
    )
    total_records = sum(
        _validate_server_scope_pin(pin, "server_scope.server_surface")
        for pin in server_surfaces.values()
    )
    databases = artifact["databases"]
    if (
        not isinstance(databases, list)
        or not 1 <= len(databases) <= MAXIMUM_SERVER_SCOPE_DATABASES
        or server_surfaces["databases"]["count"] != len(databases)
        or server_surfaces["settings"]["count"]
        < len(server_policy["required_settings"])
    ):
        raise EvidenceFailure("server_scope.databases")
    observed_databases: dict[str, Mapping[str, Any]] = {}
    primary_count = 0
    for database in databases:
        if not isinstance(database, Mapping):
            raise EvidenceFailure("server_scope.database")
        status = database.get("status")
        expected_fields = (
            fields["server_scope_database_disabled"]
            if status == "connections_disabled"
            else fields["server_scope_database_connected"]
        )
        database = _expect_keys(database, expected_fields, "server_scope.database")
        if not isinstance(status, str):
            raise EvidenceFailure("server_scope.database")
        identity = database["identity_hmac"]
        if (
            not isinstance(identity, str)
            or HEX_RE.fullmatch(identity) is None
            or identity in observed_databases
        ):
            raise EvidenceFailure("server_scope.database")
        observed_databases[identity] = database
        if status == "connections_disabled":
            continue
        if status not in {"primary_inventory", "empty"}:
            raise EvidenceFailure("server_scope.database")
        if status == "primary_inventory":
            primary_count += 1
            if identity != artifact["primary_database_identity_hmac"]:
                raise EvidenceFailure("server_scope.primary_database")
        surfaces = _expect_keys(
            database["surfaces"],
            SERVER_SCOPE_DATABASE_SURFACES,
            "server_scope.database_surfaces",
        )
        surface_counts = {
            name: _validate_server_scope_pin(pin, "server_scope.database_surface")
            for name, pin in surfaces.items()
        }
        if status == "empty" and any(
            surface_counts[name] != 0 for name in ("user_relations", "large_objects")
        ):
            raise EvidenceFailure("server_scope.database")
        total_records += sum(surface_counts.values())
    if (
        list(observed_databases) != sorted(observed_databases)
        or primary_count != 1
        or total_records > MAXIMUM_SERVER_SCOPE_TOTAL_RECORDS
    ):
        raise EvidenceFailure("server_scope.databases")

    if server_policy["activation_ready"] is True:
        if (
            artifact["source_revision"] != server_policy["source"]["revision"]
            or artifact["key_version"] != server_policy["hmac"]["key_version"]
            or server_surfaces != server_policy["expected_server_surfaces"]
        ):
            raise EvidenceFailure("server_scope.policy_binding")
        expected_databases = {
            item["identity_hmac"]: item for item in server_policy["expected_databases"]
        }
        if set(observed_databases) != set(expected_databases):
            raise EvidenceFailure("server_scope.policy_binding")
        for identity, database in observed_databases.items():
            expected = expected_databases[identity]
            if database["status"] != expected["status"]:
                raise EvidenceFailure("server_scope.policy_binding")
            if database["status"] == "connections_disabled":
                if expected["surfaces"] is not None:
                    raise EvidenceFailure("server_scope.policy_binding")
            elif database["surfaces"] != expected["surfaces"]:
                raise EvidenceFailure("server_scope.policy_binding")

    # The collector has already domain-separated and HMAC-bound this receipt.
    # This local validator has no key material and intentionally proves only
    # exact structure, policy/file binding, and freshness. Attestation and the
    # authenticated source-resource observation remain activation blockers.
    return _digest(_canonical(artifact))


def _validate_claim_artifact(
    artifact: Mapping[str, Any],
    *,
    kind: str,
    manifest: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> str:
    fields = policy["schema_fields"][kind]
    _expect_keys(artifact, fields, f"{kind}.schema")
    _validate_common(artifact, manifest, policy)
    expected_digests = {
        "evidence_manifest": manifest["policy_digests"]["evidence_manifest"],
        **policy["policy_digests"],
    }
    if (
        artifact["schema_version"] != 1
        or artifact["evidence_type"] != kind
        or artifact["policy_digests"] != expected_digests
    ):
        raise EvidenceFailure(f"{kind}.invalid")
    if kind == "quiescence" and artifact["state"] != "quiesced":
        raise EvidenceFailure("quiescence.invalid")
    return _validate_receipt(artifact, f"{kind}.receipt")


def _validate_bundle_snapshot_files(
    *,
    policy: Mapping[str, Any],
    policy_sha256: str,
    files: dict[str, bytes],
    now: datetime,
    expected_file_sha256s: Mapping[str, str] | None = None,
) -> ValidatedEvidenceBundle:
    launch_file_sha256s = None
    if expected_file_sha256s is not None:
        launch_file_sha256s = _verify_expected_file_sha256s(
            files, expected_file_sha256s
        )
    _verify_checksums(files)
    synthetic = _load_synthetic_policy(policy)
    server_scope_policy = _load_server_scope_policy(policy)
    manifest = _load_json(files["evidence-manifest.json"], "manifest.invalid")
    _expect_keys(manifest, policy["schema_fields"]["manifest"], "manifest.schema")
    policy_digests = _expect_keys(
        manifest["policy_digests"],
        policy["schema_fields"]["policy_digests"],
        "manifest.policy_digests",
    )
    source_resource = _expect_keys(
        manifest["source_resource"],
        policy["schema_fields"]["source_resource"],
        "manifest.source_resource",
    )
    expected_policy_digests = {
        "evidence_manifest": f"sha256:{policy_sha256}",
        **policy["policy_digests"],
    }
    if (
        manifest["schema_version"] != 2
        or not isinstance(manifest["manifest_id"], str)
        or MANIFEST_ID_RE.fullmatch(manifest["manifest_id"]) is None
        or not isinstance(manifest["source_sha"], str)
        or SHA_RE.fullmatch(manifest["source_sha"]) is None
        or not isinstance(manifest["candidate_sha"], str)
        or SHA_RE.fullmatch(manifest["candidate_sha"]) is None
        or manifest["hmac_key_version"] not in policy["hmac_key_versions"]
        or source_resource != policy["source_resource"]
        or policy_digests != expected_policy_digests
    ):
        raise EvidenceFailure("manifest.invalid")
    _validate_time(
        manifest["approved_restore_time"],
        manifest["observed_at"],
        manifest["expires_at"],
        now,
        policy,
    )
    _validate_provenance(manifest["provenance"], manifest["signer_claims"], policy)

    artifact_contracts = policy["artifact_contracts"]
    artifacts = manifest["artifacts"]
    if not isinstance(artifacts, list) or len(artifacts) != len(artifact_contracts):
        raise EvidenceFailure("manifest.artifacts")
    entries: dict[str, Mapping[str, Any]] = {}
    for entry in artifacts:
        entry = _expect_keys(
            entry,
            policy["schema_fields"]["artifact"],
            "manifest.artifacts",
        )
        kind = entry["kind"]
        if (
            not isinstance(kind, str)
            or kind in entries
            or artifact_contracts.get(kind) != entry["path"]
            or not isinstance(entry["file_sha256"], str)
            or DIGEST_RE.fullmatch(entry["file_sha256"]) is None
            or not isinstance(entry["receipt_sha256"], str)
            or DIGEST_RE.fullmatch(entry["receipt_sha256"]) is None
        ):
            raise EvidenceFailure("manifest.artifacts")
        entries[kind] = entry
    if set(entries) != set(artifact_contracts):
        raise EvidenceFailure("manifest.artifacts")
    unsigned_manifest = dict(manifest)
    supplied_evidence_digest = unsigned_manifest.pop("evidence_set_sha256")
    if (
        not isinstance(supplied_evidence_digest, str)
        or DIGEST_RE.fullmatch(supplied_evidence_digest) is None
        or _digest(_canonical(unsigned_manifest)) != supplied_evidence_digest
    ):
        raise EvidenceFailure("manifest.evidence_set_digest")

    values = {
        kind: (
            _load_server_scope_json(files[path])
            if kind == "server_scope"
            else _load_json(files[path], f"{kind}.invalid")
        )
        for kind, path in artifact_contracts.items()
    }
    receipts = {
        "candidate": _validate_candidate(
            values["candidate"],
            manifest,
            policy,
            synthetic["profiles"]["pre"]["revision"],
        ),
        "inventory": _validate_inventory(
            values["inventory"],
            manifest,
            policy,
            synthetic,
        ),
        "quiescence": _validate_claim_artifact(
            values["quiescence"],
            kind="quiescence",
            manifest=manifest,
            policy=policy,
        ),
        "server_scope": _validate_server_scope(
            values["server_scope"],
            manifest=manifest,
            policy=policy,
            server_policy=server_scope_policy,
        ),
    }
    for kind, entry in entries.items():
        path = artifact_contracts[kind]
        if entry["file_sha256"] != _digest(files[path]):
            raise EvidenceFailure(f"artifact.file_digest.{kind}")
        if entry["receipt_sha256"] != receipts[kind]:
            raise EvidenceFailure(f"artifact.receipt_digest.{kind}")

    provenance = manifest["provenance"]
    signer_claims = manifest["signer_claims"]
    candidate = values["candidate"]
    evidence_binding = {
        "schema_version": 1,
        "binding_policy_id": "cosmaneura-staging-restore-evidence-binding-v1",
        "evidence_manifest_policy_id": policy["policy_id"],
        "evidence_manifest_policy_sha256": f"sha256:{policy_sha256}",
        "manifest_id": manifest["manifest_id"],
        "manifest_file_sha256": _digest(files["evidence-manifest.json"]),
        "bundle_index_sha256": _digest(files["SHA256SUMS"]),
        "evidence_set_sha256": supplied_evidence_digest,
        "source_sha": manifest["source_sha"],
        "candidate_sha": manifest["candidate_sha"],
        "source_server_resource_id": source_resource["postgres_server_id"],
        "source_database_name": source_resource["database_name"],
        "approved_restore_time": manifest["approved_restore_time"],
        "observed_at": manifest["observed_at"],
        "expires_at": manifest["expires_at"],
        "candidate_approval_pr_number": candidate["approval_pr_number"],
        "candidate_protected_master_sha": candidate["protected_master_sha"],
        "candidate_schema_revision": candidate["schema_revision"],
        "candidate_receipt_sha256": receipts["candidate"],
        "inventory_receipt_sha256": receipts["inventory"],
        "quiescence_receipt_sha256": receipts["quiescence"],
        "server_scope_receipt_sha256": receipts["server_scope"],
        "repository": provenance["repository"],
        "protected_ref": provenance["protected_ref"],
        "workflow_path": provenance["workflow_path"],
        "workflow_sha": provenance["workflow_sha"],
        "run_id": provenance["run_id"],
        "run_attempt": provenance["run_attempt"],
        "event_name": provenance["event_name"],
        "signer_issuer": signer_claims["issuer"],
        "signer_subject": signer_claims["subject"],
    }
    result = {
        "schema_version": 1,
        "policy_id": policy["policy_id"],
        "manifest_id": manifest["manifest_id"],
        "evidence_set_sha256": supplied_evidence_digest,
        "evidence_binding": evidence_binding,
        "outcome": "contract_valid",
        "activation_state": "blocked",
        "activation_blockers": policy["activation_blockers"],
    }
    snapshot = ValidatedEvidenceBundle._from_validated_manifest_bytes(
        result_bytes=_canonical(result),
        server_scope_bytes=files[artifact_contracts["server_scope"]],
        manifest_bytes=files["evidence-manifest.json"],
    )
    if launch_file_sha256s is not None:
        launch_digest_validation = object.__new__(_LaunchDigestValidation)
        object.__setattr__(
            launch_digest_validation,
            "file_sha256s",
            launch_file_sha256s,
        )
        object.__setattr__(
            launch_digest_validation,
            "_seal",
            _LAUNCH_DIGEST_VALIDATION_SEAL,
        )
        object.__setattr__(
            snapshot,
            "_launch_digest_validation",
            launch_digest_validation,
        )
    return snapshot


def validate_bundle_snapshot_bytes(
    *,
    policy: Mapping[str, Any],
    policy_sha256: str,
    files: Mapping[str, bytes],
    now: datetime,
    expected_file_sha256s: Mapping[str, str] | None = None,
) -> ValidatedEvidenceBundle:
    """Validate one caller-independent snapshot of an exact in-memory bundle."""

    snapshot = _snapshot_bundle_bytes(files, policy)
    return _validate_bundle_snapshot_files(
        policy=policy,
        policy_sha256=policy_sha256,
        files=snapshot,
        now=now,
        expected_file_sha256s=expected_file_sha256s,
    )


def validate_bundle_snapshot(
    *,
    policy: Mapping[str, Any],
    policy_sha256: str,
    bundle: Path,
    now: datetime,
    expected_file_sha256s: Mapping[str, str] | None = None,
) -> ValidatedEvidenceBundle:
    """Validate one safely read path bundle through the shared byte seam."""

    files = _read_bundle(bundle, policy)
    return _validate_bundle_snapshot_files(
        policy=policy,
        policy_sha256=policy_sha256,
        files=files,
        now=now,
        expected_file_sha256s=expected_file_sha256s,
    )


def validate_bundle(
    *,
    policy: Mapping[str, Any],
    policy_sha256: str,
    bundle: Path,
    now: datetime,
) -> dict[str, Any]:
    return validate_bundle_snapshot(
        policy=policy,
        policy_sha256=policy_sha256,
        bundle=bundle,
        now=now,
    ).result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=POLICY_PATH)
    parser.add_argument("--bundle", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        policy, policy_sha256 = load_policy(args.policy)
        result = validate_bundle(
            policy=policy,
            policy_sha256=policy_sha256,
            bundle=args.bundle,
            now=datetime.now(UTC),
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except EvidenceFailure as exc:
        print(f"staging evidence manifest failed: {exc}", file=sys.stderr)
        return 1
    except Exception:
        print("staging evidence manifest failed: internal_error", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
