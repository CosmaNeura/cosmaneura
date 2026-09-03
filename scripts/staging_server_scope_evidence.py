#!/usr/bin/env python3
"""Read-only, closed-world PostgreSQL server-scope evidence collector.

This standalone command emits only counts, SHA-256 policy/schema fingerprints,
and HMAC-SHA-256 aggregates. Raw catalog identities and settings are never
written to stdout or evidence files. The committed policy is deliberately
unconfigured; activation requires a separately reviewed baseline capture.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import hmac
import json
import os
import re
import stat
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "contracts/staging-rehearsal/v1/server-scope-evidence-policy.json"
DATABASE_URL_ENVIRONMENT = "STAGING_SERVER_SCOPE_DATABASE_URL"
ALLOWED_INPUT_CONNECTION_PARAMETERS = frozenset(
    {"host", "port", "dbname", "user", "password", "sslmode", "sslrootcert"}
)
LOCAL_DATABASE_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
SYSTEM_SSL_ROOT_CERT = "system"
ADDITIONAL_CONNECTION_ENVIRONMENT_VARIABLES = frozenset(
    {
        "OPENSSL_CONF",
        "OPENSSL_ENGINES",
        "OPENSSL_MODULES",
        "PGSERVICEFILE",
        "PGSYSCONFDIR",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
    }
)
CANONICAL_POLICY_SHA256 = (
    "4f91a206c81cc2a0b239ef48d55e5b3249dff944a80b3df28128ef62c9e8adc5"
)
HMAC_DOMAIN = b"cosmaneura:staging-server-scope-evidence:v1\x00"
HEX_256_RE = re.compile(r"^[0-9a-f]{64}$")
REVISION_RE = re.compile(r"^[0-9a-f]{12,64}$")
KEY_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
AZURE_POSTGRES_HOST_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.postgres\.database\.azure\.com$"
)
MINIMUM_HMAC_KEY_BYTES = 32
MAXIMUM_HMAC_KEY_BYTES = 4096
MAXIMUM_POLICY_BYTES = 1024 * 1024
MAXIMUM_RECORDS_PER_SURFACE = 100_000
MAXIMUM_BYTES_PER_SURFACE = 16 * 1024 * 1024
MAXIMUM_ROW_BYTES = 1024 * 1024
MAXIMUM_JSON_ASCII_EXPANSION = 6
MAXIMUM_DATABASES = 64
MAXIMUM_TOTAL_RECORDS = 500_000
MAXIMUM_TOTAL_BYTES = 64 * 1024 * 1024
MAXIMUM_CAPTURE_SECONDS = 900
SERVER_SURFACES = tuple(
    "databases roles memberships role_settings database_privileges tablespaces replication_slots replication_origins prepared_transactions subscriptions parameter_privileges shared_security_labels shared_descriptions settings".split()
)
DATABASE_SURFACES = tuple(
    "schema user_relations shared_dependencies extensions foreign_data_wrappers foreign_servers foreign_tables foreign_privileges language_privileges user_mappings publications publication_memberships publication_namespaces subscription_state subscription_relations security_labels event_triggers large_objects".split()
)
NON_PRIMARY_EMPTY_SURFACES = ("user_relations", "large_objects")


class EvidenceFailure(RuntimeError):
    """A bounded failure code safe to show to an operator."""


class DatabaseUnavailable(RuntimeError):
    """Internal adapter signal; its underlying exception is never emitted."""


@dataclass(frozen=True, slots=True)
class SurfacePin:
    count: int
    hmac: str


@dataclass(frozen=True, slots=True)
class ServerSnapshot:
    version_major: int
    visibility: Mapping[str, bool]
    surfaces: Mapping[str, Sequence[Any]]


class EvidenceAdapter(Protocol):
    def start_capture(self, maximum_seconds: int) -> None: ...

    def server_snapshot(self, required_settings: Sequence[str]) -> ServerSnapshot: ...

    def database_snapshot(self, database: str) -> Mapping[str, Sequence[Any]]: ...


class SnapshotAdapter:
    """Pure in-memory adapter used for deterministic tests and policy drafting."""

    def __init__(
        self,
        server: ServerSnapshot,
        databases: Mapping[str, Mapping[str, Sequence[Any]] | DatabaseUnavailable],
    ) -> None:
        self._server = copy.deepcopy(server)
        self._databases = copy.deepcopy(databases)

    def start_capture(self, maximum_seconds: int) -> None:
        del maximum_seconds

    def server_snapshot(self, required_settings: Sequence[str]) -> ServerSnapshot:
        del required_settings
        return copy.deepcopy(self._server)

    def database_snapshot(self, database: str) -> Mapping[str, Sequence[Any]]:
        value = self._databases[database]
        if isinstance(value, DatabaseUnavailable):
            raise DatabaseUnavailable from None
        return copy.deepcopy(value)


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")
    except (TypeError, ValueError):
        raise EvidenceFailure("catalog.non_canonical_value") from None


def _update_mac(mac: hmac.HMAC, value: bytes) -> None:
    mac.update(len(value).to_bytes(8, "big"))
    mac.update(value)


def _identity_hmac(key: bytes, kind: str, value: str) -> str:
    mac = hmac.new(key, HMAC_DOMAIN + b"identity\x00", hashlib.sha256)
    _update_mac(mac, kind.encode("ascii"))
    _update_mac(mac, value.encode("utf-8"))
    return mac.hexdigest()


def _surface_pin(key: bytes, domain: str, records: Sequence[Any]) -> SurfacePin:
    if len(records) > MAXIMUM_RECORDS_PER_SURFACE:
        raise EvidenceFailure("catalog.surface_too_large")
    encoded: list[bytes] = []
    total_bytes = 0
    for record in records:
        item = _canonical(record)
        total_bytes += len(item)
        if total_bytes > MAXIMUM_BYTES_PER_SURFACE:
            raise EvidenceFailure("catalog.surface_too_large")
        encoded.append(item)
    encoded.sort()
    mac = hmac.new(key, HMAC_DOMAIN + b"surface\x00", hashlib.sha256)
    _update_mac(mac, domain.encode("ascii"))
    for record in encoded:
        _update_mac(mac, record)
    return SurfacePin(len(encoded), mac.hexdigest())


def _pin_json(pin: SurfacePin) -> dict[str, Any]:
    return {"count": pin.count, "hmac": pin.hmac}


def _expect_keys(value: Any, expected: set[str], code: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise EvidenceFailure(code)
    return value


def _validate_pin(value: Any, *, configured: bool) -> None:
    pin = _expect_keys(value, {"count", "hmac"}, "policy.invalid_pin")
    count = pin["count"]
    digest = pin["hmac"]
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        if configured:
            raise EvidenceFailure("policy.unconfigured")
        if count != -1:
            raise EvidenceFailure("policy.invalid_pin")
    if not isinstance(digest, str) or not HEX_256_RE.fullmatch(digest):
        if configured:
            raise EvidenceFailure("policy.unconfigured")
        if not isinstance(digest, str) or not digest.startswith("CONFIGURE_"):
            raise EvidenceFailure("policy.invalid_pin")


def _validate_source_host(value: Any, *, allow_local: bool = False) -> str:
    if not isinstance(value, str):
        raise EvidenceFailure("policy.invalid_source")
    if allow_local and value in LOCAL_DATABASE_HOSTS:
        return value
    if not AZURE_POSTGRES_HOST_RE.fullmatch(value):
        raise EvidenceFailure("policy.invalid_source")
    return value


def validate_policy(policy: Any) -> Mapping[str, Any]:
    policy = _expect_keys(
        policy,
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
        "policy.invalid",
    )
    configured = policy["activation_ready"] is True
    if (
        policy["schema_version"] != 1
        or policy["policy_id"] != "cosmaneura-staging-server-scope-evidence-v1"
        or not isinstance(policy["activation_ready"], bool)
    ):
        raise EvidenceFailure("policy.invalid")
    source = _expect_keys(
        policy["source"],
        {
            "resource_id",
            "revision",
            "host",
            "port",
            "maintenance_database",
            "primary_database",
            "postgres_major",
        },
        "policy.invalid_source",
    )
    for field in (
        "resource_id",
        "revision",
        "host",
        "maintenance_database",
        "primary_database",
    ):
        if not isinstance(source[field], str) or not source[field]:
            raise EvidenceFailure("policy.invalid_source")
        if configured and source[field].startswith("CONFIGURE_"):
            raise EvidenceFailure("policy.unconfigured")
    _validate_source_host(source["host"])
    if source["port"] != 5432 or source["postgres_major"] != 16:
        raise EvidenceFailure("policy.invalid_source")
    hmac_policy = _expect_keys(
        policy["hmac"], {"algorithm", "key_version"}, "policy.invalid_hmac"
    )
    if hmac_policy["algorithm"] != "HMAC-SHA-256" or not isinstance(
        hmac_policy["key_version"], str
    ):
        raise EvidenceFailure("policy.invalid_hmac")
    if configured and not KEY_VERSION_RE.fullmatch(hmac_policy["key_version"]):
        raise EvidenceFailure("policy.unconfigured")
    if configured and hmac_policy["key_version"].startswith("CONFIGURE_"):
        raise EvidenceFailure("policy.unconfigured")
    if configured and not REVISION_RE.fullmatch(source["revision"]):
        raise EvidenceFailure("policy.unconfigured")
    settings = policy["required_settings"]
    if (
        not isinstance(settings, list)
        or not settings
        or not all(isinstance(item, str) and item for item in settings)
        or settings != sorted(set(settings))
    ):
        raise EvidenceFailure("policy.invalid_settings")
    visibility = _expect_keys(
        policy["required_visibility"],
        {"pg_authid", "pg_monitor", "pg_read_all_settings"},
        "policy.invalid_visibility",
    )
    if not all(value is True for value in visibility.values()):
        raise EvidenceFailure("policy.invalid_visibility")
    server = _expect_keys(
        policy["expected_server_surfaces"],
        set(SERVER_SURFACES),
        "policy.invalid_server_surfaces",
    )
    for pin in server.values():
        _validate_pin(pin, configured=configured)
    databases = policy["expected_databases"]
    if (
        not isinstance(databases, list)
        or not databases
        or len(databases) > MAXIMUM_DATABASES
    ):
        raise EvidenceFailure("policy.invalid_databases")
    identities: set[str] = set()
    primary_entries = 0
    for database in databases:
        database = _expect_keys(
            database,
            {
                "identity_hmac",
                "is_template",
                "allow_connections",
                "status",
                "surfaces",
            },
            "policy.invalid_database",
        )
        identity = database["identity_hmac"]
        if not isinstance(identity, str) or identity in identities:
            raise EvidenceFailure("policy.invalid_database")
        identities.add(identity)
        if configured and not HEX_256_RE.fullmatch(identity):
            raise EvidenceFailure("policy.unconfigured")
        if not configured and not (
            HEX_256_RE.fullmatch(identity) or identity.startswith("CONFIGURE_")
        ):
            raise EvidenceFailure("policy.invalid_database")
        if not isinstance(database["is_template"], bool) or not isinstance(
            database["allow_connections"], bool
        ):
            raise EvidenceFailure("policy.invalid_database")
        if database["status"] == "connections_disabled":
            if (
                database["allow_connections"]
                or not database["is_template"]
                or database["surfaces"] is not None
            ):
                raise EvidenceFailure("policy.invalid_database")
        elif database["status"] in {"primary_inventory", "empty"}:
            if not database["allow_connections"]:
                raise EvidenceFailure("policy.invalid_database")
            if database["status"] == "primary_inventory":
                if database["is_template"]:
                    raise EvidenceFailure("policy.invalid_database")
                primary_entries += 1
            surfaces = _expect_keys(
                database["surfaces"],
                set(DATABASE_SURFACES),
                "policy.invalid_database_surfaces",
            )
            for pin in surfaces.values():
                _validate_pin(pin, configured=configured)
            if database["status"] == "empty" and any(
                surfaces[surface]["count"] != 0
                for surface in NON_PRIMARY_EMPTY_SURFACES
            ):
                raise EvidenceFailure("policy.invalid_database")
        else:
            raise EvidenceFailure("policy.invalid_database")
    if primary_entries != 1:
        raise EvidenceFailure("policy.invalid_databases")
    evidence = _expect_keys(
        policy["evidence"],
        {"aggregate_only", "forbidden_raw_values"},
        "policy.invalid_evidence",
    )
    if evidence["aggregate_only"] is not True or not isinstance(
        evidence["forbidden_raw_values"], list
    ):
        raise EvidenceFailure("policy.invalid_evidence")
    if not configured:
        raise EvidenceFailure("policy.unconfigured")
    return policy


def _read_regular_file(
    path: Path, maximum: int, code: str, *, exact_mode: int | None
) -> bytes:
    try:
        before = path.lstat()
        if (
            not path.is_absolute()
            or not stat.S_ISREG(before.st_mode)
            or (exact_mode is not None and stat.S_IMODE(before.st_mode) != exact_mode)
            or before.st_uid != os.getuid()
            or before.st_size > maximum
        ):
            raise EvidenceFailure(code)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            after = os.fstat(descriptor)
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise EvidenceFailure(code)
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                value = handle.read(maximum + 1)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    except EvidenceFailure:
        raise
    except OSError:
        raise EvidenceFailure(code) from None
    if len(value) > maximum:
        raise EvidenceFailure(code)
    return value


def read_hmac_key(path: Path) -> bytes:
    key = _read_regular_file(
        path, MAXIMUM_HMAC_KEY_BYTES, "hmac_key.unsafe", exact_mode=0o600
    )
    if not MINIMUM_HMAC_KEY_BYTES <= len(key) <= MAXIMUM_HMAC_KEY_BYTES:
        raise EvidenceFailure("hmac_key.invalid")
    return key


def database_url_from_environment() -> str:
    value = os.environ.get(DATABASE_URL_ENVIRONMENT)
    if not value or "\x00" in value or len(value) > 8192:
        raise EvidenceFailure("database_url.missing_or_invalid")
    return value


def read_policy(path: Path = POLICY_PATH) -> tuple[Mapping[str, Any], str]:
    raw = _read_regular_file(
        path, MAXIMUM_POLICY_BYTES, "policy.file_unsafe", exact_mode=None
    )
    digest = hashlib.sha256(raw).hexdigest()
    if digest != CANONICAL_POLICY_SHA256:
        raise EvidenceFailure("policy.hash_mismatch")
    try:
        policy = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise EvidenceFailure("policy.invalid_json") from None
    return validate_policy(policy), digest


def _compare_pin(observed: SurfacePin, expected: Any, code: str) -> None:
    if _pin_json(observed) != expected:
        raise EvidenceFailure(code)


def _database_metadata(record: Any) -> tuple[str, bool, bool]:
    if (
        not isinstance(record, dict)
        or set(record)
        != {
            "name",
            "is_template",
            "allow_connections",
            "owner",
            "encoding",
            "collation",
            "ctype",
            "locale_provider",
            "icu_locale",
            "icu_rules",
            "collation_version",
            "default_tablespace",
            "connection_limit",
        }
        or not isinstance(record["name"], str)
        or not isinstance(record["is_template"], bool)
        or not isinstance(record["allow_connections"], bool)
    ):
        raise EvidenceFailure("catalog.invalid_database_record")
    return record["name"], record["is_template"], record["allow_connections"]


def _database_scope_status(
    name: str,
    *,
    is_template: bool,
    allow_connections: bool,
    primary_database: str,
) -> str:
    if not allow_connections:
        if not is_template:
            raise EvidenceFailure("database.disabled_non_template")
        return "connections_disabled"
    if name == primary_database:
        return "primary_inventory"
    return "empty"


def _require_non_primary_empty(snapshot: Mapping[str, Sequence[Any]]) -> None:
    if any(snapshot[surface] for surface in NON_PRIMARY_EMPTY_SURFACES):
        raise EvidenceFailure("database.non_primary_not_empty")


def collect_evidence(
    policy: Mapping[str, Any],
    policy_sha256: str,
    adapter: EvidenceAdapter,
    key: bytes,
    *,
    source_resource_id: str,
    source_revision: str,
    key_version: str,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    policy = validate_policy(policy)
    if not HEX_256_RE.fullmatch(policy_sha256):
        raise EvidenceFailure("policy.hash_invalid")
    if source_resource_id != policy["source"]["resource_id"]:
        raise EvidenceFailure("source.resource_mismatch")
    if source_revision != policy["source"]["revision"] or not REVISION_RE.fullmatch(
        source_revision
    ):
        raise EvidenceFailure("source.revision_mismatch")
    if key_version != policy["hmac"]["key_version"]:
        raise EvidenceFailure("hmac_key.version_mismatch")
    if not MINIMUM_HMAC_KEY_BYTES <= len(key) <= MAXIMUM_HMAC_KEY_BYTES:
        raise EvidenceFailure("hmac_key.invalid")
    deadline = monotonic() + MAXIMUM_CAPTURE_SECONDS
    adapter.start_capture(MAXIMUM_CAPTURE_SECONDS)

    def check_deadline() -> None:
        if monotonic() >= deadline:
            raise EvidenceFailure("capture.deadline_exceeded")

    started = clock()
    if started.tzinfo is None:
        raise EvidenceFailure("capture.invalid_clock")
    settings = policy["required_settings"]
    check_deadline()
    before = adapter.server_snapshot(settings)
    if before.version_major != policy["source"]["postgres_major"]:
        raise EvidenceFailure("server.version_mismatch")
    if dict(before.visibility) != dict(policy["required_visibility"]):
        raise EvidenceFailure("server.incomplete_visibility")
    if set(before.surfaces) != set(SERVER_SURFACES):
        raise EvidenceFailure("server.incomplete_surface")
    observed_server: dict[str, dict[str, Any]] = {}
    for name in SERVER_SURFACES:
        pin = _surface_pin(key, f"server:{name}", before.surfaces[name])
        check_deadline()
        _compare_pin(
            pin,
            policy["expected_server_surfaces"][name],
            f"server.{name}_mismatch",
        )
        observed_server[name] = _pin_json(pin)
    database_records = list(before.surfaces["databases"])
    if len(database_records) > MAXIMUM_DATABASES:
        raise EvidenceFailure("database.count_exceeded")
    expected_databases = {
        item["identity_hmac"]: item for item in policy["expected_databases"]
    }
    primary_database = policy["source"]["primary_database"]
    primary_identity = _identity_hmac(key, "database", primary_database)
    primary_expected = expected_databases.get(primary_identity)
    if primary_expected is None or primary_expected["status"] != "primary_inventory":
        raise EvidenceFailure("database.primary_binding_mismatch")
    observed_databases: list[dict[str, Any]] = []
    captured: list[tuple[str, str, str, dict[str, dict[str, Any]]]] = []
    seen: set[str] = set()
    for raw_record in sorted(database_records, key=_canonical):
        check_deadline()
        name, is_template, allow_connections = _database_metadata(raw_record)
        status = _database_scope_status(
            name,
            is_template=is_template,
            allow_connections=allow_connections,
            primary_database=primary_database,
        )
        identity = _identity_hmac(key, "database", name)
        expected = expected_databases.get(identity)
        if expected is None:
            raise EvidenceFailure("database.unknown")
        if identity in seen:
            raise EvidenceFailure("database.duplicate")
        seen.add(identity)
        if (is_template, allow_connections) != (
            expected["is_template"],
            expected["allow_connections"],
        ):
            raise EvidenceFailure("database.metadata_mismatch")
        if not allow_connections:
            if expected["status"] != status:
                raise EvidenceFailure("database.unexpected_inaccessible")
            observed_databases.append(
                {"identity_hmac": identity, "status": "connections_disabled"}
            )
            continue
        if expected["status"] != status:
            raise EvidenceFailure("database.scope_mismatch")
        try:
            snapshot = adapter.database_snapshot(name)
        except DatabaseUnavailable:
            raise EvidenceFailure("database.read_blocked") from None
        if set(snapshot) != set(DATABASE_SURFACES):
            raise EvidenceFailure("database.incomplete_surface")
        if status == "empty":
            _require_non_primary_empty(snapshot)
        surface_evidence: dict[str, dict[str, Any]] = {}
        for surface in DATABASE_SURFACES:
            pin = _surface_pin(key, f"database:{identity}:{surface}", snapshot[surface])
            check_deadline()
            _compare_pin(
                pin,
                expected["surfaces"][surface],
                f"database.{surface}_mismatch",
            )
            surface_evidence[surface] = _pin_json(pin)
        observed_databases.append(
            {
                "identity_hmac": identity,
                "status": status,
                "surfaces": surface_evidence,
            }
        )
        captured.append((name, identity, status, surface_evidence))
    if seen != set(expected_databases):
        raise EvidenceFailure("database.missing")
    for name, identity, status, first_pins in captured:
        check_deadline()
        try:
            snapshot = adapter.database_snapshot(name)
        except DatabaseUnavailable:
            raise EvidenceFailure("database.read_blocked") from None
        if set(snapshot) != set(DATABASE_SURFACES):
            raise EvidenceFailure("database.incomplete_surface")
        if status == "empty":
            _require_non_primary_empty(snapshot)
        for surface in DATABASE_SURFACES:
            pin = _surface_pin(key, f"database:{identity}:{surface}", snapshot[surface])
            check_deadline()
            if _pin_json(pin) != first_pins[surface]:
                raise EvidenceFailure("database.changed_during_capture")
    check_deadline()
    after = adapter.server_snapshot(settings)
    if after != before:
        raise EvidenceFailure("server.changed_during_capture")
    completed = clock()
    check_deadline()
    duration = (completed.astimezone(UTC) - started.astimezone(UTC)).total_seconds()
    if duration < 0 or duration > MAXIMUM_CAPTURE_SECONDS:
        raise EvidenceFailure("capture.invalid_duration")
    body: dict[str, Any] = {
        "schema_version": 1,
        "policy_id": policy["policy_id"],
        "policy_sha256": policy_sha256,
        "source_revision": source_revision,
        "source_resource_hmac": _identity_hmac(
            key, "source-resource", source_resource_id
        ),
        "primary_database_identity_hmac": primary_identity,
        "key_version": key_version,
        "capture_started_at": started.astimezone(UTC)
        .isoformat()
        .replace("+00:00", "Z"),
        "capture_completed_at": completed.astimezone(UTC)
        .isoformat()
        .replace("+00:00", "Z"),
        "server_version_major": before.version_major,
        "server_surfaces": observed_server,
        "databases": sorted(observed_databases, key=lambda item: item["identity_hmac"]),
        "errors": [],
        "pass": True,
    }
    body["evidence_hmac"] = _surface_pin(
        key, "evidence", [_canonical(body).decode("ascii")]
    ).hmac
    return body


SCHEMA_QUERIES = (
    """SELECT jsonb_build_object('kind','namespace_object','object_type',i.type,
       'names',i.object_names,'arguments',i.object_args)::text
       FROM pg_catalog.pg_depend d JOIN pg_catalog.pg_namespace n ON n.oid=d.refobjid
       CROSS JOIN LATERAL pg_catalog.pg_identify_object_as_address(
       d.classid,d.objid,d.objsubid) i
       WHERE d.refclassid='pg_catalog.pg_namespace'::pg_catalog.regclass
       AND d.deptype='n' AND n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'""",
    """SELECT jsonb_build_object('kind','schema','schema',n.nspname,'owner',r.rolname)::text
       FROM pg_catalog.pg_namespace n JOIN pg_catalog.pg_roles r ON r.oid=n.nspowner
       WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'""",
    """SELECT jsonb_build_object('kind','relation','schema',n.nspname,'name',c.relname,
       'relkind',c.relkind,'persistence',c.relpersistence,'owner',r.rolname,
       'row_security',c.relrowsecurity,'force_row_security',c.relforcerowsecurity,
       'replica_identity',c.relreplident,'partition',c.relispartition,
       'access_method',am.amname,'partition_key',pg_catalog.pg_get_partkeydef(c.oid),
       'partition_bound',pg_catalog.pg_get_expr(c.relpartbound,c.oid,false),
       'tablespace',ts.spcname)::text
       FROM pg_catalog.pg_class c
       JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
       JOIN pg_catalog.pg_roles r ON r.oid=c.relowner
       LEFT JOIN pg_catalog.pg_am am ON am.oid=c.relam
       LEFT JOIN pg_catalog.pg_tablespace ts ON ts.oid=COALESCE(
       NULLIF(c.reltablespace,0),(SELECT d.dattablespace
       FROM pg_catalog.pg_database d
       WHERE d.datname=pg_catalog.current_database()))
       WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'""",
    """SELECT jsonb_build_object('kind','rewrite_rule','schema',n.nspname,
       'relation',c.relname,'name',rw.rulename,'enabled',rw.ev_enabled,
       'is_instead',rw.is_instead,'event',rw.ev_type,
       'definition',pg_catalog.pg_get_ruledef(rw.oid,false))::text
       FROM pg_catalog.pg_rewrite rw
       JOIN pg_catalog.pg_class c ON c.oid=rw.ev_class
       JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
       ORDER BY n.nspname,c.relname,rw.rulename""",
    """SELECT jsonb_build_object('kind','relation_option','schema',n.nspname,
       'relation',c.relname,'option',o.option)::text FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
       CROSS JOIN LATERAL unnest(c.reloptions) o(option)
       WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'""",
    """SELECT jsonb_build_object('kind','column','schema',n.nspname,'relation',c.relname,
       'name',a.attname,'position',a.attnum,'type',pg_catalog.format_type(a.atttypid,a.atttypmod),
       'not_null',a.attnotnull,'identity',a.attidentity,'generated',a.attgenerated,
       'default',pg_catalog.pg_get_expr(d.adbin,d.adrelid,false),
       'collation',co.collname)::text FROM pg_catalog.pg_attribute a
       JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
       JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
       LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
       LEFT JOIN pg_catalog.pg_collation co ON co.oid=a.attcollation
       WHERE a.attnum > 0 AND NOT a.attisdropped AND n.nspname !~ '^pg_'
       AND n.nspname <> 'information_schema'""",
    """SELECT jsonb_build_object('kind','column_fdw_option','schema',n.nspname,
       'relation',c.relname,'column',a.attname,'option',o.option)::text FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
       CROSS JOIN LATERAL unnest(a.attfdwoptions) o(option)
       WHERE a.attnum>0 AND NOT a.attisdropped AND n.nspname !~ '^pg_'
       AND n.nspname <> 'information_schema'""",
    """SELECT jsonb_build_object('kind','column_authorization','schema',n.nspname,
       'relation',c.relname,'column',x.attname,'grantee',CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE g.rolname END,
       'grantor',grantor.rolname,'privilege',a.privilege_type,'grantable',a.is_grantable)::text FROM pg_catalog.pg_attribute x
       JOIN pg_catalog.pg_class c ON c.oid=x.attrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace CROSS JOIN LATERAL pg_catalog.aclexplode(x.attacl) a
       LEFT JOIN pg_catalog.pg_roles g ON g.oid=a.grantee
       LEFT JOIN pg_catalog.pg_roles grantor ON grantor.oid=a.grantor
       WHERE x.attnum>0 AND NOT x.attisdropped AND n.nspname !~ '^pg_'
       AND n.nspname <> 'information_schema'""",
    """SELECT jsonb_build_object('kind','constraint','schema',n.nspname,'relation',c.relname,
       'name',x.conname,'type',x.contype,'definition',
       pg_catalog.pg_get_constraintdef(x.oid,false))::text FROM pg_catalog.pg_constraint x
       JOIN pg_catalog.pg_class c ON c.oid=x.conrelid
       JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'""",
    """SELECT jsonb_build_object('kind','domain_constraint','schema',n.nspname,
       'type',t.typname,'name',x.conname,'definition',
       pg_catalog.pg_get_constraintdef(x.oid,false))::text
       FROM pg_catalog.pg_constraint x JOIN pg_catalog.pg_type t ON t.oid=x.contypid
       JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace WHERE x.contypid <> 0
       AND n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'""",
    """SELECT jsonb_build_object('kind','index','schema',n.nspname,'relation',c.relname,
       'name',i.relname,'definition',pg_catalog.pg_get_indexdef(i.oid,0,false))::text
       FROM pg_catalog.pg_index x JOIN pg_catalog.pg_class i ON i.oid=x.indexrelid
       JOIN pg_catalog.pg_class c ON c.oid=x.indrelid
       JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'""",
    """SELECT jsonb_build_object('kind','sequence','schema',n.nspname,'name',c.relname,
       'type',pg_catalog.format_type(s.seqtypid,NULL),'start',s.seqstart,
       'increment',s.seqincrement,'max',s.seqmax,'min',s.seqmin,'cache',s.seqcache,
       'cycle',s.seqcycle)::text FROM pg_catalog.pg_sequence s
       JOIN pg_catalog.pg_class c ON c.oid=s.seqrelid
       JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'""",
    """SELECT jsonb_build_object('kind','view','schema',n.nspname,'name',c.relname,
       'definition',pg_catalog.pg_get_viewdef(c.oid,false))::text
       FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
       WHERE c.relkind IN ('v','m') AND n.nspname !~ '^pg_'
       AND n.nspname <> 'information_schema'""",
    """SELECT jsonb_build_object('kind','routine','schema',n.nspname,'name',p.proname,
       'identity_args',pg_catalog.pg_get_function_identity_arguments(p.oid),
       'kind_code',p.prokind,'arguments',pg_catalog.pg_get_function_arguments(p.oid),
       'result',pg_catalog.pg_get_function_result(p.oid),'language',l.lanname,
       'volatility',p.provolatile,'parallel',p.proparallel,'strict',p.proisstrict,
       'security_definer',p.prosecdef,'leakproof',p.proleakproof,'returns_set',p.proretset,
       'source',p.prosrc,'binary',p.probin,
       'definition',CASE WHEN p.prokind='a' THEN NULL ELSE pg_catalog.pg_get_functiondef(p.oid) END,
       'owner',r.rolname,'config',config.option)::text FROM pg_catalog.pg_proc p
       JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
       JOIN pg_catalog.pg_roles r ON r.oid=p.proowner
       JOIN pg_catalog.pg_language l ON l.oid=p.prolang
       LEFT JOIN LATERAL unnest(p.proconfig) config(option) ON true
       WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'""",
    """SELECT jsonb_build_object('kind','type','schema',n.nspname,'name',t.typname,
       'type_kind',t.typtype,'category',t.typcategory,'owner',r.rolname,
       'not_null',t.typnotnull,'default',t.typdefault,
       'base',pg_catalog.format_type(NULLIF(t.typbasetype,0),t.typtypmod))::text
       FROM pg_catalog.pg_type t JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace
       JOIN pg_catalog.pg_roles r ON r.oid=t.typowner
       WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'""",
    """SELECT jsonb_build_object('kind','collation','schema',n.nspname,
       'name',c.collname,'owner',r.rolname,'provider',c.collprovider,
       'deterministic',c.collisdeterministic,
       'encoding',pg_catalog.pg_encoding_to_char(c.collencoding),
       'collate',c.collcollate,'ctype',c.collctype,
       'icu_locale',c.colliculocale,'icu_rules',c.collicurules,
       'version',c.collversion)::text
       FROM pg_catalog.pg_collation c
       JOIN pg_catalog.pg_namespace n ON n.oid=c.collnamespace
       JOIN pg_catalog.pg_roles r ON r.oid=c.collowner
       WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'""",
    """SELECT jsonb_build_object('kind','enum_label','schema',n.nspname,
       'type',t.typname,'sort_order',e.enumsortorder::text,'label',e.enumlabel)::text
       FROM pg_catalog.pg_enum e JOIN pg_catalog.pg_type t ON t.oid=e.enumtypid
       JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace
       WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'""",
    """SELECT jsonb_build_object('kind','trigger','schema',n.nspname,'relation',c.relname,
       'name',t.tgname,'definition',pg_catalog.pg_get_triggerdef(t.oid,false),
       'enabled',t.tgenabled)::text FROM pg_catalog.pg_trigger t
       JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
       JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE NOT t.tgisinternal
       AND n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'""",
    """SELECT jsonb_build_object('kind','policy','schema',n.nspname,'relation',c.relname,
       'name',p.polname,'command',p.polcmd,'permissive',p.polpermissive,
       'role',CASE WHEN role_oid=0 THEN 'PUBLIC' ELSE r.rolname END,
       'using',pg_catalog.pg_get_expr(p.polqual,p.polrelid,false),
       'check',pg_catalog.pg_get_expr(p.polwithcheck,p.polrelid,false))::text
       FROM pg_catalog.pg_policy p JOIN pg_catalog.pg_class c ON c.oid=p.polrelid
       JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
       CROSS JOIN LATERAL unnest(p.polroles) role_oid
       LEFT JOIN pg_catalog.pg_roles r ON r.oid=role_oid
       WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'""",
    """SELECT jsonb_build_object('kind','authorization','object_kind',x.object_kind,
       'schema',x.schema_name,'object',x.object_name,'owner',x.owner_name,
       'grantee',CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE grantee.rolname END,
       'grantor',grantor.rolname,'privilege',a.privilege_type,
       'grantable',a.is_grantable)::text FROM (
       SELECT 'schema' object_kind,n.nspname schema_name,n.nspname object_name,
       owner.rolname owner_name,n.nspacl acl,n.nspowner owner_oid
       FROM pg_catalog.pg_namespace n JOIN pg_catalog.pg_roles owner ON owner.oid=n.nspowner
       WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
       UNION ALL SELECT CASE WHEN c.relkind='S' THEN 'sequence' ELSE 'relation' END,
       n.nspname,c.relname,owner.rolname,c.relacl,c.relowner
       FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
       JOIN pg_catalog.pg_roles owner ON owner.oid=c.relowner
       WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
       AND c.relkind IN ('r','p','v','m','S','f')
       UNION ALL SELECT 'routine',n.nspname,p.proname || '(' ||
       pg_catalog.pg_get_function_identity_arguments(p.oid) || ')',owner.rolname,
       p.proacl,p.proowner FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n
       ON n.oid=p.pronamespace JOIN pg_catalog.pg_roles owner ON owner.oid=p.proowner
       WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
       UNION ALL SELECT 'type',n.nspname,t.typname,owner.rolname,t.typacl,t.typowner
       FROM pg_catalog.pg_type t JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace
       JOIN pg_catalog.pg_roles owner ON owner.oid=t.typowner
       WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema') x
       LEFT JOIN LATERAL pg_catalog.aclexplode(COALESCE(x.acl,
       pg_catalog.acldefault(CASE x.object_kind WHEN 'schema' THEN 'n'::"char"
       WHEN 'relation' THEN 'r'::"char" WHEN 'sequence' THEN 'S'::"char"
       WHEN 'routine' THEN 'f'::"char"
       ELSE 'T'::"char" END,x.owner_oid))) a ON true
       LEFT JOIN pg_catalog.pg_roles grantee ON grantee.oid=a.grantee
       LEFT JOIN pg_catalog.pg_roles grantor ON grantor.oid=a.grantor""",
    """SELECT jsonb_build_object('kind','default_privilege','owner',r.rolname,
       'schema',n.nspname,'object_kind',d.defaclobjtype,
       'grantee',CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE g.rolname END,
       'grantor',grantor.rolname,'privilege',a.privilege_type,
       'grantable',a.is_grantable)::text FROM pg_catalog.pg_default_acl d
       JOIN pg_catalog.pg_roles r ON r.oid=d.defaclrole
       LEFT JOIN pg_catalog.pg_namespace n ON n.oid=d.defaclnamespace
       CROSS JOIN LATERAL pg_catalog.aclexplode(d.defaclacl) a
       LEFT JOIN pg_catalog.pg_roles g ON g.oid=a.grantee
       LEFT JOIN pg_catalog.pg_roles grantor ON grantor.oid=a.grantor""",
)


class LivePostgresAdapter:
    """Psycopg adapter; all catalog reads run in read-only transactions."""

    def __init__(
        self, dsn: str, policy: Mapping[str, Any], *, allow_local: bool = False
    ):
        self._policy = policy
        self._deadline = 0.0
        self._query_sequence = 0
        self._total_records = 0
        self._total_bytes = 0
        self._connection_parameters = MappingProxyType(
            self._validate_dsn(dsn, allow_local=allow_local)
        )
        self.start_capture(MAXIMUM_CAPTURE_SECONDS)

    def start_capture(self, maximum_seconds: int) -> None:
        self._deadline = time.monotonic() + maximum_seconds
        self._query_sequence = 0
        self._total_records = 0
        self._total_bytes = 0

    def _remaining(self) -> float:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise EvidenceFailure("capture.deadline_exceeded")
        return remaining

    @staticmethod
    def _reject_libpq_environment(pq: Any) -> None:
        try:
            environment_names = {
                option.envvar.decode("ascii")
                for option in pq.Conninfo.get_defaults()
                if option.envvar
            }
        except Exception:
            raise EvidenceFailure("database_url.environment_invalid") from None
        if any(
            name in os.environ
            for name in environment_names | ADDITIONAL_CONNECTION_ENVIRONMENT_VARIABLES
        ):
            raise EvidenceFailure("database_url.environment_override")

    def _validate_dsn(self, dsn: str, *, allow_local: bool) -> dict[str, str | int]:
        try:
            from psycopg import pq
            from psycopg.conninfo import conninfo_to_dict

            values = conninfo_to_dict(dsn)
        except Exception:
            raise EvidenceFailure("database_url.invalid") from None
        self._reject_libpq_environment(pq)
        if set(values) - ALLOWED_INPUT_CONNECTION_PARAMETERS:
            raise EvidenceFailure("database_url.unsupported_parameter")
        source = self._policy["source"]
        expected_host = _validate_source_host(
            source.get("host"), allow_local=allow_local
        )
        host = values.get("host")
        raw_port = values.get("port")
        try:
            port = int(raw_port) if isinstance(raw_port, (str, int)) else 5432
        except ValueError:
            raise EvidenceFailure("database_url.invalid") from None
        database = values.get("dbname")
        user = values.get("user")
        password = values.get("password")
        if (
            not isinstance(user, str)
            or not user
            or not isinstance(password, str)
            or not password
        ):
            raise EvidenceFailure("database_url.credentials_required")
        if (host, port, database) != (
            expected_host,
            source["port"],
            source["maintenance_database"],
        ):
            raise EvidenceFailure("database_url.target_mismatch")
        sslmode = values.get("sslmode")
        sslrootcert = values.get("sslrootcert")
        if allow_local:
            if host not in LOCAL_DATABASE_HOSTS:
                raise EvidenceFailure("database_url.target_mismatch")
            if (sslmode, sslrootcert) not in {
                (None, None),
                ("disable", None),
                ("verify-full", SYSTEM_SSL_ROOT_CERT),
            }:
                raise EvidenceFailure("database_url.tls_invalid")
            normalized_sslmode = sslmode or "disable"
        elif (sslmode, sslrootcert) != ("verify-full", SYSTEM_SSL_ROOT_CERT):
            raise EvidenceFailure("database_url.tls_required")
        else:
            normalized_sslmode = "verify-full"

        parameters: dict[str, str | int] = {
            "host": str(host),
            "port": port,
            "user": user,
            "password": password,
            "sslmode": normalized_sslmode,
            "sslcertmode": "disable",
            "gssencmode": "disable",
        }
        if sslrootcert is not None:
            parameters["sslrootcert"] = sslrootcert
        return parameters

    def _connect(self, database: str) -> Any:
        try:
            import psycopg

            self._reject_libpq_environment(psycopg.pq)
            remaining = self._remaining()
            if remaining < 1:
                raise EvidenceFailure("capture.deadline_exceeded")
            timeout = min(10, int(remaining))
            parameters: dict[str, Any] = dict(self._connection_parameters)
            parameters.update(dbname=database, connect_timeout=timeout)
            connection = psycopg.connect(**parameters)
            self._remaining()
            return connection
        except EvidenceFailure:
            raise
        except Exception:
            raise DatabaseUnavailable from None

    def _configure(self, connection: Any) -> None:
        self._remaining()
        connection.execute(
            "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY DEFERRABLE"
        )
        timeout_ms = max(1, min(30_000, int(self._remaining() * 1000)))
        connection.execute(
            "SELECT pg_catalog.set_config('statement_timeout', %s, true)",
            (f"{timeout_ms}ms",),
        )
        lock_timeout_ms = max(1, min(5_000, int(self._remaining() * 1000)))
        connection.execute(
            "SELECT pg_catalog.set_config('lock_timeout', %s, true)",
            (f"{lock_timeout_ms}ms",),
        )
        for statement in (
            "SET LOCAL search_path = pg_catalog, public",
            "SET LOCAL quote_all_identifiers = off",
            "SET LOCAL extra_float_digits = 3",
            "SET LOCAL DateStyle = 'ISO, YMD'",
            "SET LOCAL TimeZone = 'UTC'",
            "SET LOCAL IntervalStyle = 'postgres'",
            "SET LOCAL bytea_output = 'hex'",
            "SET LOCAL row_security = off",
        ):
            connection.execute(statement)
            self._remaining()

    def _cancel_at_deadline(self, connection: Any) -> threading.Timer:
        def cancel() -> None:
            try:
                connection.cancel()
            except Exception:
                pass

        timer = threading.Timer(self._remaining(), cancel)
        timer.daemon = True
        timer.start()
        return timer

    def _rows(self, connection: Any, query: str, parameters: Any = None) -> list[Any]:
        try:
            remaining_ms = max(1, min(30_000, int(self._remaining() * 1000)))
            connection.execute(
                "SELECT pg_catalog.set_config('statement_timeout', %s, true)",
                (f"{remaining_ms}ms",),
            )
            bounds = connection.execute(
                "SELECT count(*)::bigint,"
                "COALESCE(sum(rendered.bytes),0)::bigint,"
                "COALESCE(max(rendered.bytes),0)::bigint FROM ("
                "SELECT pg_catalog.octet_length("
                "pg_catalog.row_to_json(q)::text)::bigint AS bytes "
                f"FROM ({query}) q) rendered",
                parameters,
            ).fetchone()
            self._remaining()
            rendered_bytes = bounds[1] * MAXIMUM_JSON_ASCII_EXPANSION if bounds else 0
            rendered_row_bytes = (
                bounds[2] * MAXIMUM_JSON_ASCII_EXPANSION if bounds else 0
            )
            if (
                not bounds
                or bounds[0] > MAXIMUM_RECORDS_PER_SURFACE
                or rendered_bytes > MAXIMUM_BYTES_PER_SURFACE
                or rendered_row_bytes > MAXIMUM_ROW_BYTES
                or self._total_records + bounds[0] > MAXIMUM_TOTAL_RECORDS
                or self._total_bytes + rendered_bytes > MAXIMUM_TOTAL_BYTES
            ):
                raise EvidenceFailure("catalog.resource_limit")
            self._query_sequence += 1
            records: list[Any] = []
            query_records = 0
            query_bytes = 0
            with connection.cursor(
                name=f"server_scope_{self._query_sequence}"
            ) as cursor:
                cursor.itersize = 1
                cursor.execute(query, parameters)
                for row in cursor:
                    self._remaining()
                    record = list(row)
                    size = len(_canonical(record))
                    if size > MAXIMUM_ROW_BYTES:
                        raise EvidenceFailure("catalog.resource_limit")
                    query_records += 1
                    query_bytes += size
                    self._total_records += 1
                    self._total_bytes += size
                    if (
                        query_records > MAXIMUM_RECORDS_PER_SURFACE
                        or query_bytes > MAXIMUM_BYTES_PER_SURFACE
                        or self._total_records > MAXIMUM_TOTAL_RECORDS
                        or self._total_bytes > MAXIMUM_TOTAL_BYTES
                    ):
                        raise EvidenceFailure("catalog.resource_limit")
                    records.append(record)
            self._remaining()
            return records
        except EvidenceFailure:
            raise
        except Exception:
            if time.monotonic() >= self._deadline:
                raise EvidenceFailure("capture.deadline_exceeded") from None
            raise EvidenceFailure("catalog.read_blocked") from None

    def server_snapshot(self, required_settings: Sequence[str]) -> ServerSnapshot:
        try:
            connection = self._connect(self._policy["source"]["maintenance_database"])
        except DatabaseUnavailable:
            raise EvidenceFailure("database.connection_blocked") from None
        timer = self._cancel_at_deadline(connection)
        try:
            self._configure(connection)
            version = int(
                self._rows(connection, "SELECT current_setting('server_version_num')")[
                    0
                ][0]
            )
            visibility_row = self._rows(
                connection,
                "SELECT pg_catalog.has_table_privilege(current_user,"
                "'pg_catalog.pg_authid','SELECT'),"
                "pg_catalog.pg_has_role(current_user,'pg_monitor','MEMBER'),"
                "pg_catalog.pg_has_role(current_user,'pg_read_all_settings','MEMBER')",
            )[0]
            visibility: dict[str, bool] = {
                "pg_authid": bool(visibility_row[0]),
                "pg_monitor": bool(visibility_row[1]),
                "pg_read_all_settings": bool(visibility_row[2]),
            }
            surfaces: dict[str, Sequence[Any]] = {
                "databases": self._rows(
                    connection,
                    """SELECT d.datname,d.datistemplate,d.datallowconn,r.rolname,
                    pg_catalog.pg_encoding_to_char(d.encoding),d.datcollate,d.datctype,
                    d.datlocprovider,d.daticulocale,d.daticurules,d.datcollversion,
                    t.spcname,d.datconnlimit FROM pg_catalog.pg_database d
                    JOIN pg_catalog.pg_roles r ON r.oid=d.datdba
                    JOIN pg_catalog.pg_tablespace t ON t.oid=d.dattablespace
                    ORDER BY d.datname""",
                ),
                "roles": self._rows(
                    connection,
                    """SELECT rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,
                    rolcanlogin,rolreplication,rolbypassrls,rolconnlimit,
                    rolvaliduntil::text,rolpassword IS NOT NULL,
                    CASE WHEN rolpassword IS NULL THEN 'absent'
                    WHEN rolpassword LIKE 'SCRAM-SHA-256$%' THEN 'scram-sha-256'
                    WHEN rolpassword LIKE 'md5%' THEN 'md5' ELSE 'other' END,
                    rolpassword FROM pg_catalog.pg_authid ORDER BY rolname""",
                ),
                "memberships": self._rows(
                    connection,
                    """SELECT role_role.rolname,member_role.rolname,grantor_role.rolname,
                    m.admin_option,m.inherit_option,m.set_option
                    FROM pg_catalog.pg_auth_members m
                    JOIN pg_catalog.pg_roles role_role ON role_role.oid=m.roleid
                    JOIN pg_catalog.pg_roles member_role ON member_role.oid=m.member
                    JOIN pg_catalog.pg_roles grantor_role ON grantor_role.oid=m.grantor
                    ORDER BY 1,2,3""",
                ),
                "role_settings": self._rows(
                    connection,
                    """SELECT COALESCE(d.datname,'*'),COALESCE(r.rolname,'*'),s.setting
                    FROM pg_catalog.pg_db_role_setting x
                    LEFT JOIN pg_catalog.pg_database d ON d.oid=x.setdatabase
                    LEFT JOIN pg_catalog.pg_roles r ON r.oid=x.setrole
                    CROSS JOIN LATERAL unnest(x.setconfig) s(setting) ORDER BY 1,2,3""",
                ),
                "database_privileges": self._rows(
                    connection,
                    """SELECT d.datname,owner.rolname,
                    CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE grantee.rolname END,
                    grantor.rolname,a.privilege_type,a.is_grantable
                    FROM pg_catalog.pg_database d
                    JOIN pg_catalog.pg_roles owner ON owner.oid=d.datdba
                    LEFT JOIN LATERAL pg_catalog.aclexplode(
                    COALESCE(d.datacl,pg_catalog.acldefault('d',d.datdba))) a ON true
                    LEFT JOIN pg_catalog.pg_roles grantee ON grantee.oid=a.grantee
                    LEFT JOIN pg_catalog.pg_roles grantor ON grantor.oid=a.grantor
                    ORDER BY 1,3,4,5""",
                ),
                "tablespaces": self._rows(
                    connection,
                    """SELECT t.spcname,r.rolname,pg_catalog.pg_tablespace_location(t.oid),option.option,
                    CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE g.rolname END,grantor.rolname,a.privilege_type,a.is_grantable
                    FROM pg_catalog.pg_tablespace t JOIN pg_catalog.pg_roles r ON r.oid=t.spcowner
                    LEFT JOIN LATERAL unnest(t.spcoptions) option(option) ON true
                    LEFT JOIN LATERAL pg_catalog.aclexplode(COALESCE(t.spcacl,
                    pg_catalog.acldefault('t',t.spcowner))) a ON true
                    LEFT JOIN pg_catalog.pg_roles g ON g.oid=a.grantee
                    LEFT JOIN pg_catalog.pg_roles grantor ON grantor.oid=a.grantor ORDER BY 1,3,4,5,6,7""",
                ),
                "replication_slots": self._rows(
                    connection,
                    """SELECT slot_name,plugin,slot_type,database,
                    temporary,active,two_phase,conflicting,wal_status,
                    restart_lsn::text,confirmed_flush_lsn::text,safe_wal_size
                    FROM pg_catalog.pg_replication_slots ORDER BY slot_name""",
                ),
                "replication_origins": self._rows(
                    connection,
                    """SELECT external_id,remote_lsn::text,local_lsn::text FROM pg_catalog.pg_replication_origin_status ORDER BY external_id""",
                ),
                "prepared_transactions": self._rows(
                    connection,
                    """SELECT transaction::text,gid,prepared::text,owner,database FROM pg_catalog.pg_prepared_xacts ORDER BY database,gid""",
                ),
                "subscriptions": self._rows(
                    connection,
                    """SELECT d.datname,s.subname,r.rolname,s.subenabled,s.subbinary,s.substream,s.subtwophasestate,s.subdisableonerr,
                    s.subpasswordrequired,s.subrunasowner,s.suborigin,s.subconninfo,s.subslotname,s.subsynccommit,
                    CASE WHEN s.subskiplsn='0/0'::pg_catalog.pg_lsn THEN NULL ELSE s.subskiplsn::text END,pub.publication
                    FROM pg_catalog.pg_subscription s JOIN pg_catalog.pg_database d ON d.oid=s.subdbid JOIN pg_catalog.pg_roles r ON r.oid=s.subowner
                    LEFT JOIN LATERAL unnest(s.subpublications) pub(publication) ON true
                    ORDER BY 1,2,16""",
                ),
                "parameter_privileges": self._rows(
                    connection,
                    """SELECT p.parname,CASE WHEN a.grantee=0 THEN 'PUBLIC'
                    ELSE g.rolname END,grantor.rolname,a.privilege_type,a.is_grantable
                    FROM pg_catalog.pg_parameter_acl p
                    CROSS JOIN LATERAL pg_catalog.aclexplode(p.paracl) a
                    LEFT JOIN pg_catalog.pg_roles g ON g.oid=a.grantee
                    LEFT JOIN pg_catalog.pg_roles grantor ON grantor.oid=a.grantor
                    ORDER BY 1,2,3,4""",
                ),
                "shared_security_labels": self._rows(
                    connection,
                    """SELECT i.type,i.object_names,i.object_args,s.provider,s.label
                    FROM pg_catalog.pg_shseclabel s CROSS JOIN LATERAL
                    pg_catalog.pg_identify_object_as_address(
                    s.classoid,s.objoid,0) i ORDER BY 1,2,3,4,5""",
                ),
                "shared_descriptions": self._rows(
                    connection,
                    """SELECT i.type,i.object_names,i.object_args,d.description FROM pg_catalog.pg_shdescription d CROSS JOIN LATERAL
                    pg_catalog.pg_identify_object_as_address(d.classoid,d.objoid,0) i ORDER BY 1,2,3,4""",
                ),
                "settings": self._rows(
                    connection,
                    """SELECT name,setting,unit,source,boot_val,reset_val,pending_restart
                    FROM pg_catalog.pg_settings ORDER BY name""",
                ),
            }
            if not set(required_settings) <= {row[0] for row in surfaces["settings"]}:
                raise EvidenceFailure("server.missing_setting")
            database_rows = []
            for row in surfaces["databases"]:
                database_rows.append(
                    {
                        "name": row[0],
                        "is_template": row[1],
                        "allow_connections": row[2],
                        "owner": row[3],
                        "encoding": row[4],
                        "collation": row[5],
                        "ctype": row[6],
                        "locale_provider": row[7],
                        "icu_locale": row[8],
                        "icu_rules": row[9],
                        "collation_version": row[10],
                        "default_tablespace": row[11],
                        "connection_limit": row[12],
                    }
                )
            surfaces["databases"] = database_rows
            return ServerSnapshot(version // 10000, visibility, surfaces)
        except EvidenceFailure:
            raise
        except Exception:
            raise EvidenceFailure("catalog.read_blocked") from None
        finally:
            timer.cancel()
            connection.close()

    def database_snapshot(self, database: str) -> Mapping[str, Sequence[Any]]:
        connection = self._connect(database)
        timer = self._cancel_at_deadline(connection)
        try:
            self._configure(connection)
            schema: list[Any] = []
            for query in SCHEMA_QUERIES:
                schema.extend(row[0] for row in self._rows(connection, query))
            return {
                "schema": schema,
                "user_relations": self._rows(
                    connection,
                    """SELECT n.nspname,c.relname,c.relkind
                    FROM pg_catalog.pg_class c
                    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname !~ '^pg_'
                    AND n.nspname <> 'information_schema'
                    AND c.relkind IN ('r','p','v','m','S','f')
                    ORDER BY n.nspname,c.relname,c.relkind""",
                ),
                "shared_dependencies": self._rows(
                    connection,
                    """SELECT jsonb_build_object(
                    'dependent_type',dependent_object.type,
                    'dependent_names',dependent_object.object_names,
                    'dependent_arguments',dependent_object.object_args,
                    'referenced_type',referenced_object.type,
                    'referenced_names',referenced_object.object_names,
                    'referenced_arguments',referenced_object.object_args,
                    'dependency_type',dependency.deptype)::text
                    FROM pg_catalog.pg_shdepend dependency
                    CROSS JOIN LATERAL pg_catalog.pg_identify_object_as_address(
                    dependency.classid,dependency.objid,
                    dependency.objsubid) dependent_object
                    CROSS JOIN LATERAL pg_catalog.pg_identify_object_as_address(
                    dependency.refclassid,dependency.refobjid,0) referenced_object
                    WHERE dependency.dbid=(SELECT oid FROM pg_catalog.pg_database
                    WHERE datname=pg_catalog.current_database())
                    ORDER BY dependent_object.type,dependent_object.object_names,
                    dependent_object.object_args,referenced_object.type,
                    referenced_object.object_names,referenced_object.object_args,
                    dependency.deptype""",
                ),
                "extensions": self._rows(
                    connection,
                    """SELECT e.extname,e.extversion,n.nspname,r.rolname,e.extrelocatable
                    FROM pg_catalog.pg_extension e JOIN pg_catalog.pg_namespace n
                    ON n.oid=e.extnamespace JOIN pg_catalog.pg_roles r
                    ON r.oid=e.extowner ORDER BY e.extname""",
                ),
                "foreign_data_wrappers": self._rows(
                    connection,
                    """SELECT f.fdwname,r.rolname,f.fdwhandler::regproc::text,
                    f.fdwvalidator::regproc::text,o.option
                    FROM pg_catalog.pg_foreign_data_wrapper f
                    JOIN pg_catalog.pg_roles r ON r.oid=f.fdwowner
                    LEFT JOIN LATERAL unnest(f.fdwoptions) o(option) ON true
                    ORDER BY f.fdwname,o.option""",
                ),
                "foreign_servers": self._rows(
                    connection,
                    """SELECT s.srvname,r.rolname,f.fdwname,s.srvtype,s.srvversion,
                    o.option
                    FROM pg_catalog.pg_foreign_server s JOIN pg_catalog.pg_roles r ON r.oid=s.srvowner
                    JOIN pg_catalog.pg_foreign_data_wrapper f ON f.oid=s.srvfdw
                    LEFT JOIN LATERAL unnest(s.srvoptions) o(option) ON true
                    ORDER BY s.srvname,o.option""",
                ),
                "foreign_tables": self._rows(
                    connection,
                    """SELECT n.nspname,c.relname,s.srvname,o.option
                    FROM pg_catalog.pg_foreign_table f
                    JOIN pg_catalog.pg_class c ON c.oid=f.ftrelid
                    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                    JOIN pg_catalog.pg_foreign_server s ON s.oid=f.ftserver
                    LEFT JOIN LATERAL unnest(f.ftoptions) o(option) ON true
                    ORDER BY 1,2,4""",
                ),
                "foreign_privileges": self._rows(
                    connection,
                    """SELECT x.kind,x.name,x.owner,x.acl IS NULL,CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE g.rolname END,
                    grantor.rolname,a.privilege_type,a.is_grantable FROM (
                    SELECT 'fdw' kind,f.fdwname name,r.rolname owner,f.fdwacl acl
                    FROM pg_catalog.pg_foreign_data_wrapper f JOIN pg_catalog.pg_roles r
                    ON r.oid=f.fdwowner UNION ALL
                    SELECT 'server',s.srvname,r.rolname,s.srvacl
                    FROM pg_catalog.pg_foreign_server s JOIN pg_catalog.pg_roles r
                    ON r.oid=s.srvowner) x
                    LEFT JOIN LATERAL pg_catalog.aclexplode(x.acl) a ON true
                    LEFT JOIN pg_catalog.pg_roles g ON g.oid=a.grantee LEFT JOIN pg_catalog.pg_roles grantor ON grantor.oid=a.grantor ORDER BY 1,2,5,6,7""",
                ),
                "language_privileges": self._rows(
                    connection,
                    """SELECT l.lanname,r.rolname,l.lanispl,l.lanpltrusted,l.lanacl IS NULL,
                    CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE g.rolname END,grantor.rolname,a.privilege_type,a.is_grantable
                    FROM pg_catalog.pg_language l LEFT JOIN pg_catalog.pg_roles r ON r.oid=l.lanowner
                    LEFT JOIN LATERAL pg_catalog.aclexplode(l.lanacl) a ON true LEFT JOIN pg_catalog.pg_roles g ON g.oid=a.grantee
                    LEFT JOIN pg_catalog.pg_roles grantor ON grantor.oid=a.grantor ORDER BY 1,6,7,8""",
                ),
                "user_mappings": self._rows(
                    connection,
                    """SELECT s.srvname,CASE WHEN m.umuser=0 THEN 'PUBLIC'
                    ELSE r.rolname END,o.option
                    FROM pg_catalog.pg_user_mapping m
                    JOIN pg_catalog.pg_foreign_server s ON s.oid=m.umserver
                    LEFT JOIN pg_catalog.pg_roles r ON r.oid=m.umuser
                    LEFT JOIN LATERAL unnest(m.umoptions) o(option) ON true
                    ORDER BY 1,2,3""",
                ),
                "publications": self._rows(
                    connection,
                    """SELECT p.pubname,r.rolname,p.puballtables,p.pubinsert,p.pubupdate,
                    p.pubdelete,p.pubtruncate,p.pubviaroot FROM pg_catalog.pg_publication p
                    JOIN pg_catalog.pg_roles r ON r.oid=p.pubowner ORDER BY p.pubname""",
                ),
                "publication_memberships": self._rows(
                    connection,
                    """SELECT p.pubname,n.nspname,c.relname,
                    pg_catalog.pg_get_expr(x.prqual,x.prrelid,false),
                    a.attname
                    FROM pg_catalog.pg_publication_rel x
                    JOIN pg_catalog.pg_publication p ON p.oid=x.prpubid
                    JOIN pg_catalog.pg_class c ON c.oid=x.prrelid
                    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                    LEFT JOIN LATERAL unnest(x.prattrs::smallint[]) number(attnum) ON true
                    LEFT JOIN pg_catalog.pg_attribute a ON a.attrelid=x.prrelid
                    AND a.attnum=number.attnum ORDER BY 1,2,3,5""",
                ),
                "publication_namespaces": self._rows(
                    connection,
                    """SELECT p.pubname,n.nspname FROM pg_catalog.pg_publication_namespace x JOIN pg_catalog.pg_publication p ON p.oid=x.pnpubid JOIN pg_catalog.pg_namespace n ON n.oid=x.pnnspid ORDER BY 1,2""",
                ),
                "subscription_state": self._rows(
                    connection,
                    """SELECT s.subname,n.nspname,c.relname,x.pid IS NOT NULL,x.received_lsn IS NOT NULL,x.latest_end_lsn IS NOT NULL
                    FROM pg_catalog.pg_subscription s LEFT JOIN pg_catalog.pg_stat_subscription x ON x.subid=s.oid
                    LEFT JOIN pg_catalog.pg_class c ON c.oid=x.relid LEFT JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                    WHERE s.subdbid=(SELECT oid FROM pg_catalog.pg_database WHERE datname=current_database()) ORDER BY 1,2,3""",
                ),
                "subscription_relations": self._rows(
                    connection,
                    """SELECT s.subname,n.nspname,c.relname,r.srsubstate,
                    CASE WHEN r.srsublsn='0/0'::pg_catalog.pg_lsn THEN NULL
                    ELSE r.srsublsn::text END FROM pg_catalog.pg_subscription_rel r
                    JOIN pg_catalog.pg_subscription s ON s.oid=r.srsubid
                    JOIN pg_catalog.pg_class c ON c.oid=r.srrelid
                    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                    ORDER BY 1,2,3""",
                ),
                "security_labels": self._rows(
                    connection,
                    """SELECT i.type,i.object_names,i.object_args,s.provider,s.label
                    FROM pg_catalog.pg_seclabel s CROSS JOIN LATERAL
                    pg_catalog.pg_identify_object_as_address(
                    s.classoid,s.objoid,s.objsubid) i ORDER BY 1,2,3,4,5""",
                ),
                "event_triggers": self._rows(
                    connection,
                    """SELECT e.evtname,r.rolname,e.evtevent,e.evtenabled,n.nspname,
                    p.proname,pg_catalog.pg_get_function_identity_arguments(p.oid),tag.tag
                    FROM pg_catalog.pg_event_trigger e JOIN pg_catalog.pg_roles r
                    ON r.oid=e.evtowner JOIN pg_catalog.pg_proc p ON p.oid=e.evtfoid
                    JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
                    LEFT JOIN LATERAL unnest(e.evttags) tag(tag) ON true
                    ORDER BY e.evtname,tag.tag""",
                ),
                "large_objects": self._rows(
                    connection,
                    """SELECT m.oid::text,r.rolname,
                    CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE g.rolname END,
                    grantor.rolname,a.privilege_type,a.is_grantable,
                    l.pageno,pg_catalog.encode(l.data,'base64')
                    FROM pg_catalog.pg_largeobject_metadata m
                    JOIN pg_catalog.pg_roles r ON r.oid=m.lomowner
                    LEFT JOIN LATERAL pg_catalog.aclexplode(COALESCE(m.lomacl,
                    pg_catalog.acldefault('L',m.lomowner))) a ON true
                    LEFT JOIN pg_catalog.pg_roles g ON g.oid=a.grantee
                    LEFT JOIN pg_catalog.pg_roles grantor ON grantor.oid=a.grantor
                    LEFT JOIN pg_catalog.pg_largeobject l ON l.loid=m.oid
                    ORDER BY m.oid,3,4,5,l.pageno""",
                ),
            }
        except EvidenceFailure:
            raise
        except Exception:
            raise DatabaseUnavailable from None
        finally:
            timer.cancel()
            connection.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hmac-key-file", required=True, type=Path)
    parser.add_argument("--source-resource-id", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--key-version", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _build_parser().parse_args(argv)
        policy, policy_sha256 = read_policy()
        key = read_hmac_key(args.hmac_key_file)
        adapter = LivePostgresAdapter(database_url_from_environment(), policy)
        evidence = collect_evidence(
            policy,
            policy_sha256,
            adapter,
            key,
            source_resource_id=args.source_resource_id,
            source_revision=args.source_revision,
            key_version=args.key_version,
        )
        print(json.dumps(evidence, sort_keys=True, separators=(",", ":")))
        return 0
    except EvidenceFailure as error:
        print(json.dumps({"errors": [str(error)], "pass": False}), file=sys.stderr)
        return 2
    except Exception:
        print(
            json.dumps({"errors": ["internal_error"], "pass": False}), file=sys.stderr
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
