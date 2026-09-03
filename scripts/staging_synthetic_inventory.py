#!/usr/bin/env python3
"""Fail-closed, aggregate-only staging synthetic inventory verification.

This command is deliberately independent of ``app`` and Alembic.  It opens a
read-only PostgreSQL transaction, enumerates blob state, and checks an already
quiesced Redis database.  Raw rows, blob names, and Redis keys are never
written to output; evidence contains only counts and keyed digests.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import stat
import sys
import urllib.parse
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

MINIMUM_HMAC_KEY_BYTES = 32
MAXIMUM_HMAC_KEY_BYTES = 4096
MAXIMUM_TRUST_FILE_BYTES = 8 * 1024 * 1024
MAXIMUM_ERRORS = 64
MAXIMUM_SCHEMA_DESCRIPTOR_RECORDS = 10_000
MAXIMUM_SCHEMA_DESCRIPTOR_BYTES = 8 * 1024 * 1024
CONNECT_TIMEOUT_SECONDS = 10
STATEMENT_TIMEOUT_MILLISECONDS = 30_000
LOCK_TIMEOUT_MILLISECONDS = 5_000
HMAC_DOMAIN = b"cosmaneura:staging-synthetic-inventory:v1\x00"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
HEX_256_RE = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]*$")
# fmt: off
CANONICAL_POLICY_SHA256 = "9bc83f96f82cbaaf3ec0b9acbde107733afebde1bf411d66f14e47405478c39e"
CANONICAL_POLICY_SEMANTIC_SHA256 = "70fc91062ee09086ec65585fd8b782999e46e3d0db5880cc982d12202693399c"
EXPECTED_PROFILES = {"pre": ("89c31f0ae275", 62), "post": ("9a2399212488", 64)}
EXPECTED_DATABASE_TARGET = ("cosmaneura-staging-postgres.postgres.database.azure.com", "staging", 5432)
EXPECTED_BLOB_TARGET = "cosmaneurastaginguploads.blob.core.windows.net"
EXPECTED_REDIS_TARGET = ("cosmaneura-staging-redis.redis.cache.windows.net", 6380, 0)
SCHEMA_DESCRIPTOR_FORMAT = "postgresql-16-public-v2"
SCHEMA_DESCRIPTOR_SECTIONS = (
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
    "default_privileges",
)
EXPECTED_DISPOSITIONS = {
    "synthetic_lineage": frozenset("appointment patient ehr_sandbox_patient_designation ehr_sandbox_record_mapping synthetic_preview_appointment_fixture".split()),
    "fixed_catalog": frozenset("baa_version drug_reference doctor_note_schema lab_range_set note_type_customization".split()),
    "workforce_allowlist": frozenset("authority_actor authority_actor_external_identity authority_person authority_person_binding authority_person_origin authority_tenant authority_tenant_membership baa_acceptance doctor organization_roster_approval pilot_access_continuity_amendment pilot_access_continuity_amendment_identity pilot_access_grandfather_grant practice practice_access_audit practice_member provider_organization public_legal_acceptance superadmin_capability_audit superadmin_capability_grant".split()),
    "required_empty": frozenset("agent_model_usage_event ai_visit_note ai_visit_note_version_audit ai_visit_transcript ai_visit_transcript_non_phi authority_audit_event authority_idempotency_record canonical_conversation_final_phase canonical_conversation_turn canonical_conversation_user_phase cps_self_report doctor_note_example doctor_note_file doctor_video_integration engagement_role external_webhook_delivery external_webhook_operation intake intake_summary interaction_lineage lab_panel lab_result llm_response mount_context_session mount_context_token patient_medication practice_invitation scribe_transcript_delivery stripe_event_inbox stripe_event_receipt view_as_audit_event view_as_session visit_audio_file zoom_meeting".split()),
}
# fmt: on

ColumnContract = tuple[str, str, bool]
RelationContract = tuple[str, bool, Sequence[ColumnContract]]


class InventoryFailure(RuntimeError):
    """A bounded failure whose message is safe to show to an operator."""


@dataclass(frozen=True, slots=True)
class StateDigest:
    count: int
    state_hmac: str
    identity_hmac: str | None = None


@dataclass(frozen=True, slots=True)
class DatabaseGlobalCounts:
    non_system_schema_count: int
    sequence_count: int
    large_object_count: int


@dataclass(frozen=True, slots=True)
class RedisSurface:
    logical_database_count: int
    key_count: int


@dataclass(frozen=True, slots=True)
class SchemaDescriptor:
    sha256: str
    counts: Mapping[str, int]


class InventoryProvider(Protocol):
    def database_global_counts(self) -> DatabaseGlobalCounts: ...

    def database_catalog(self) -> Mapping[str, RelationContract]: ...

    def database_schema_descriptor(self) -> SchemaDescriptor: ...

    def alembic_revisions(self) -> Sequence[str]: ...

    def table_count(self, table: str) -> int: ...

    def table_state(
        self, table: str, identity_columns: Sequence[str]
    ) -> StateDigest: ...

    def violation_count(self, table: str, rule: Mapping[str, Any]) -> int: ...

    def blob_container_names(self) -> Sequence[str]: ...

    def blob_state(self, container: str, *, include_content: bool) -> StateDigest: ...

    def redis_surface(self) -> RedisSurface: ...


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()


def _schema_descriptor(
    sections: Mapping[str, Sequence[Mapping[str, Any]]],
) -> SchemaDescriptor:
    if set(sections) != set(SCHEMA_DESCRIPTOR_SECTIONS):
        raise InventoryFailure("database.schema_descriptor_failed")
    canonical_sections: dict[str, list[Mapping[str, Any]]] = {}
    total_records = 0
    total_bytes = 0
    for section in SCHEMA_DESCRIPTOR_SECTIONS:
        records = sorted(sections[section], key=_canonical_json)
        total_records += len(records)
        if total_records > MAXIMUM_SCHEMA_DESCRIPTOR_RECORDS:
            raise InventoryFailure("database.schema_descriptor_failed")
        for record in records:
            encoded = _canonical_json(record)
            total_bytes += len(encoded)
            if total_bytes > MAXIMUM_SCHEMA_DESCRIPTOR_BYTES:
                raise InventoryFailure("database.schema_descriptor_failed")
        canonical_sections[section] = records
    encoded = _canonical_json(
        {"format": SCHEMA_DESCRIPTOR_FORMAT, "sections": canonical_sections}
    )
    if len(encoded) > MAXIMUM_SCHEMA_DESCRIPTOR_BYTES:
        raise InventoryFailure("database.schema_descriptor_failed")
    return SchemaDescriptor(
        sha256=hashlib.sha256(encoded).hexdigest(),
        counts={
            section: len(canonical_sections[section])
            for section in SCHEMA_DESCRIPTOR_SECTIONS
        },
    )


def _configure_catalog_session(connection: Any) -> None:
    """Make PostgreSQL catalog rendering deterministic for this transaction."""

    for statement in (
        "SET LOCAL search_path = pg_catalog, public",
        "SET LOCAL quote_all_identifiers = off",
        "SET LOCAL extra_float_digits = 3",
        "SET LOCAL DateStyle = 'ISO, YMD'",
        "SET LOCAL IntervalStyle = 'postgres'",
        "SET LOCAL bytea_output = 'hex'",
    ):
        connection.execute(statement)


def _digest(key: bytes, domain: str, values: Iterable[str | bytes]) -> str:
    mac = hmac.new(key, HMAC_DOMAIN + domain.encode() + b"\x00", hashlib.sha256)
    for value in values:
        _update_mac(mac, value)
    return mac.hexdigest()


def _update_mac(mac: hmac.HMAC, value: str | bytes) -> None:
    encoded = value.encode() if isinstance(value, str) else value
    mac.update(len(encoded).to_bytes(8, "big"))
    mac.update(encoded)


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not IDENTIFIER_RE.fullmatch(value):
        raise InventoryFailure("policy.invalid_identifier")
    return value


def _quoted(value: Any) -> str:
    return f'"{_identifier(value)}"'


def _read_regular_file(
    path: Path,
    *,
    exact_mode: int | None,
    maximum_bytes: int,
    code: str,
) -> bytes:
    """Read an owner-controlled regular file without following a symlink."""

    try:
        if not path.is_absolute() or any(ord(char) < 32 for char in str(path)):
            raise InventoryFailure(code)
        resolved = path.resolve(strict=True)
        if resolved != path:
            raise InventoryFailure(code)
        parent = path.parent.lstat()
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid not in {0, os.geteuid()}
            or parent.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            raise InventoryFailure(code)
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid not in {0, os.geteuid()}
            or before.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            or before.st_size <= 0
            or before.st_size > maximum_bytes
            or (exact_mode is not None and stat.S_IMODE(before.st_mode) != exact_mode)
        ):
            raise InventoryFailure(code)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            after = os.fstat(descriptor)
            if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
                raise InventoryFailure(code)
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                contents = handle.read(maximum_bytes + 1)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    except InventoryFailure:
        raise
    except Exception:
        raise InventoryFailure(code) from None
    if not contents or len(contents) > maximum_bytes:
        raise InventoryFailure(code)
    return contents


def read_hmac_key(path: Path) -> bytes:
    key = _read_regular_file(
        path,
        exact_mode=0o600,
        maximum_bytes=MAXIMUM_HMAC_KEY_BYTES,
        code="hmac_key.unsafe",
    ).rstrip(b"\r\n")
    if not MINIMUM_HMAC_KEY_BYTES <= len(key) <= MAXIMUM_HMAC_KEY_BYTES:
        raise InventoryFailure("hmac_key.invalid")
    return key


def _validate_pem(path: Path, *, private: bool) -> None:
    contents = _read_regular_file(
        path,
        exact_mode=0o600 if private else None,
        maximum_bytes=MAXIMUM_TRUST_FILE_BYTES,
        code="tls.file_unsafe",
    )
    marker = b"PRIVATE KEY" if private else b"CERTIFICATE"
    if marker not in contents:
        raise InventoryFailure("tls.file_invalid")


def _parse_query(parsed: urllib.parse.SplitResult) -> dict[str, str]:
    try:
        pairs = urllib.parse.parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
        )
    except ValueError:
        raise InventoryFailure("target.invalid") from None
    if len({key for key, _ in pairs}) != len(pairs):
        raise InventoryFailure("target.invalid")
    return dict(pairs)


def validate_database_url(database_url: str, policy: Mapping[str, Any]) -> None:
    try:
        parsed = urllib.parse.urlsplit(database_url)
        port = parsed.port
    except ValueError:
        raise InventoryFailure("database.target_invalid") from None
    query = _parse_query(parsed)
    allowed = {"sslmode", "sslrootcert", "sslcert", "sslkey"}
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or parsed.hostname != policy["expected_host"]
        or port != policy["expected_port"]
        or parsed.path != f"/{policy['expected_name']}"
        or not parsed.username
        or not parsed.password
        or parsed.fragment
        or set(query) - allowed
        or query.get("sslmode") != "verify-full"
        or "sslrootcert" not in query
        or ("sslcert" in query) != ("sslkey" in query)
    ):
        raise InventoryFailure("database.target_invalid")
    _validate_pem(Path(query["sslrootcert"]), private=False)
    if "sslcert" in query:
        _validate_pem(Path(query["sslcert"]), private=False)
        _validate_pem(Path(query["sslkey"]), private=True)


def validate_blob_url(account_url: str, policy: Mapping[str, Any]) -> None:
    try:
        parsed = urllib.parse.urlsplit(account_url)
        port = parsed.port
    except ValueError:
        raise InventoryFailure("blob.target_invalid") from None
    if (
        parsed.scheme != "https"
        or parsed.hostname != policy["expected_account_host"]
        or port not in {None, 443}
        or parsed.path not in {"", "/"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise InventoryFailure("blob.target_invalid")


def validate_redis_url(redis_url: str, policy: Mapping[str, Any]) -> None:
    try:
        parsed = urllib.parse.urlsplit(redis_url)
        port = parsed.port
    except ValueError:
        raise InventoryFailure("redis.target_invalid") from None
    query = _parse_query(parsed)
    allowed = {
        "ssl_cert_reqs",
        "ssl_check_hostname",
        "ssl_ca_certs",
        "ssl_certfile",
        "ssl_keyfile",
    }
    if (
        parsed.scheme != "rediss"
        or parsed.hostname != policy["expected_host"]
        or port != policy["expected_port"]
        or parsed.path != f"/{policy['database']}"
        or not parsed.password
        or parsed.fragment
        or set(query) - allowed
        or query.get("ssl_cert_reqs") != "required"
        or query.get("ssl_check_hostname") != "true"
        or "ssl_ca_certs" not in query
        or ("ssl_certfile" in query) != ("ssl_keyfile" in query)
    ):
        raise InventoryFailure("redis.target_invalid")
    _validate_pem(Path(query["ssl_ca_certs"]), private=False)
    if "ssl_certfile" in query:
        _validate_pem(Path(query["ssl_certfile"]), private=False)
        _validate_pem(Path(query["ssl_keyfile"]), private=True)


def _load_json(path: Path, code: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except Exception:
        raise InventoryFailure(code) from None
    if not isinstance(value, dict):
        raise InventoryFailure(code)
    return value, raw


def _expected_tables(
    policy: Mapping[str, Any], profile_name: str
) -> dict[str, dict[str, Any]]:
    try:
        profile = policy["profiles"][profile_name]
        excluded_tables = set(profile["excluded_tables"])
        exclusions = profile["column_exclusions"]
        all_tables = policy["database"]["tables"]
    except (KeyError, TypeError):
        raise InventoryFailure("policy.invalid") from None
    expected: dict[str, dict[str, Any]] = {}
    for table_name, table_spec in all_tables.items():
        _identifier(table_name)
        if table_name in excluded_tables:
            continue
        copy = dict(table_spec)
        removed = set(exclusions.get(table_name, []))
        copy["columns"] = [
            column for column in table_spec["columns"] if column[0] not in removed
        ]
        expected[table_name] = copy
    if len(expected) != profile["table_count"]:
        raise InventoryFailure("policy.invalid")
    if set(exclusions) - set(expected):
        raise InventoryFailure("policy.invalid")
    return expected


def validate_policy(policy: Mapping[str, Any]) -> None:
    if (
        hashlib.sha256(_canonical_json(policy)).hexdigest()
        != CANONICAL_POLICY_SEMANTIC_SHA256
        or policy.get("schema_version") != 1
        or policy.get("policy_id") != "cosmaneura-staging-synthetic-inventory-v1"
    ):
        raise InventoryFailure("policy.invalid")
    tables = policy.get("database", {}).get("tables", {})
    if (
        not isinstance(tables, dict)
        or len(tables) != 64
        or policy["database"].get("allowed_non_system_schemas") != ["public"]
        or policy["database"].get("sequences") != []
        or policy["database"].get("reject_large_objects") is not True
        or policy["database"].get("reject_unknown_tables") is not True
        or policy["database"].get("reject_unknown_columns") is not True
        or policy.get("blob", {}).get("enumerate")
        != ["live", "versions", "snapshots", "deleted", "uncommitted"]
        or policy["blob"].get("reject_unknown_containers") is not True
        or policy["blob"].get("reject_uncommitted_blocks") is not True
        or policy.get("redis", {}).get("database") != 0
        or policy["redis"].get("required_empty") is not True
        or policy["redis"].get("quiesced_required") is not True
        or policy["redis"].get("reject_any_key") is not True
        or policy["redis"].get("scan_all_logical_databases") is not True
        or (
            policy["database"].get("expected_host"),
            policy["database"].get("expected_name"),
            policy["database"].get("expected_port"),
        )
        != EXPECTED_DATABASE_TARGET
        or policy["blob"].get("expected_account_host") != EXPECTED_BLOB_TARGET
        or (
            policy["redis"].get("expected_host"),
            policy["redis"].get("expected_port"),
            policy["redis"].get("database"),
        )
        != EXPECTED_REDIS_TARGET
        or {
            name: (profile.get("revision"), profile.get("table_count"))
            for name, profile in policy.get("profiles", {}).items()
        }
        != EXPECTED_PROFILES
    ):
        raise InventoryFailure("policy.invalid")
    dispositions = {
        "required_empty",
        "synthetic_lineage",
        "workforce_allowlist",
        "fixed_catalog",
    }
    actual_dispositions = {
        disposition: frozenset(
            name
            for name, spec in tables.items()
            if spec.get("disposition") == disposition
        )
        for disposition in dispositions
    }
    if actual_dispositions != EXPECTED_DISPOSITIONS:
        raise InventoryFailure("policy.invalid")
    column_sets = {
        name: {column[0] for column in spec["columns"]} for name, spec in tables.items()
    }
    for name, spec in tables.items():
        _identifier(name)
        columns = spec.get("columns")
        if spec.get("disposition") not in dispositions or not isinstance(columns, list):
            raise InventoryFailure("policy.invalid")
        seen: set[str] = set()
        for column in columns:
            if (
                not isinstance(column, list)
                or len(column) != 3
                or not isinstance(column[1], str)
                or not isinstance(column[2], bool)
            ):
                raise InventoryFailure("policy.invalid")
            name_part = _identifier(column[0])
            if name_part in seen:
                raise InventoryFailure("policy.invalid")
            seen.add(name_part)
        identity_columns = spec.get("identity_columns", [])
        if len(identity_columns) != len(set(identity_columns)):
            raise InventoryFailure("policy.invalid")
        for identity in identity_columns:
            if _identifier(identity) not in seen:
                raise InventoryFailure("policy.invalid")
        rotation_columns = spec.get("rotation_columns", [])
        if len(rotation_columns) != len(set(rotation_columns)):
            raise InventoryFailure("policy.invalid")
        for rotated in rotation_columns:
            if (
                spec["disposition"] != "workforce_allowlist"
                or _identifier(rotated) not in seen
            ):
                raise InventoryFailure("policy.invalid")
        rule_ids: set[str] = set()
        for rule in spec.get("rules", []):
            if not isinstance(rule.get("id"), str) or rule.get("kind") not in {
                "required_null",
                "required_values",
                "references",
                "covered_by_any",
            }:
                raise InventoryFailure("policy.invalid")
            if rule["id"] in rule_ids:
                raise InventoryFailure("policy.invalid")
            rule_ids.add(rule["id"])
            profiles = rule.get("profiles", ["pre", "post"])
            if (
                not isinstance(profiles, list)
                or not profiles
                or len(profiles) != len(set(profiles))
                or not set(profiles) <= {"pre", "post"}
            ):
                raise InventoryFailure("policy.invalid")
            kind = rule["kind"]
            if kind == "required_null":
                if not rule.get("columns") or not set(rule["columns"]) <= seen:
                    raise InventoryFailure("policy.invalid")
            elif kind == "required_values":
                values = rule.get("values")
                if (
                    not isinstance(values, dict)
                    or not values
                    or not set(values) <= seen
                    or any(not choices for choices in values.values())
                ):
                    raise InventoryFailure("policy.invalid")
            elif kind == "references":
                target = rule.get("target_table")
                source_columns = rule.get("columns", [])
                target_columns = rule.get("target_columns", [])
                if (
                    target not in column_sets
                    or not source_columns
                    or len(source_columns) != len(target_columns)
                    or not set(source_columns) <= seen
                    or not set(target_columns) <= column_sets[target]
                ):
                    raise InventoryFailure("policy.invalid")
            else:
                coverages = rule.get("coverages")
                if not isinstance(coverages, list) or not coverages:
                    raise InventoryFailure("policy.invalid")
                for coverage in coverages:
                    target = coverage.get("table")
                    source_columns = coverage.get("columns", [])
                    target_columns = coverage.get("target_columns", [])
                    if (
                        target not in column_sets
                        or not source_columns
                        or len(source_columns) != len(target_columns)
                        or not set(source_columns) <= seen
                        or not set(target_columns) <= column_sets[target]
                    ):
                        raise InventoryFailure("policy.invalid")
    for profile in ("pre", "post"):
        _expected_tables(policy, profile)
        descriptor = policy["profiles"][profile].get("schema_descriptor", {})
        descriptor_counts = descriptor.get("counts", {})
        if (
            descriptor.get("format") != SCHEMA_DESCRIPTOR_FORMAT
            or not isinstance(descriptor.get("sha256"), str)
            or HEX_256_RE.fullmatch(descriptor["sha256"]) is None
            or not isinstance(descriptor_counts, dict)
            or set(descriptor_counts) != set(SCHEMA_DESCRIPTOR_SECTIONS)
            or any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in descriptor_counts.values()
            )
        ):
            raise InventoryFailure("policy.invalid")


def _validate_approved_state(
    approved: Mapping[str, Any],
    *,
    policy_sha256: str,
    profile: str,
) -> None:
    if (
        set(approved)
        != {
            "schema_version",
            "policy_sha256",
            "profile",
            "tables",
            "containers",
        }
        or approved.get("schema_version") != 1
        or approved.get("policy_sha256") != policy_sha256
        or approved.get("profile") != profile
        or not isinstance(approved.get("tables"), dict)
        or not isinstance(approved.get("containers"), dict)
    ):
        raise InventoryFailure("approved_state.invalid")
    for section in (approved["tables"], approved["containers"]):
        for entry in section.values():
            if (
                not isinstance(entry, dict)
                or not {"count", "state_hmac"} <= set(entry)
                or set(entry) - {"count", "state_hmac", "identity_hmac"}
                or not isinstance(entry.get("count"), int)
                or entry["count"] < 0
                or not isinstance(entry.get("state_hmac"), str)
                or not HEX_256_RE.fullmatch(entry["state_hmac"])
            ):
                raise InventoryFailure("approved_state.invalid")
            identity = entry.get("identity_hmac")
            if identity is not None and (
                not isinstance(identity, str) or not HEX_256_RE.fullmatch(identity)
            ):
                raise InventoryFailure("approved_state.invalid")


def _matches_approval(actual: StateDigest, expected: Mapping[str, Any]) -> bool:
    return (
        actual.count == expected.get("count")
        and hmac.compare_digest(actual.state_hmac, expected.get("state_hmac", ""))
        and (
            actual.identity_hmac is None
            or hmac.compare_digest(
                actual.identity_hmac,
                expected.get("identity_hmac", ""),
            )
        )
    )


def verify_inventory(
    *,
    policy: Mapping[str, Any],
    policy_sha256: str,
    approved: Mapping[str, Any],
    profile_name: str,
    source_sha: str,
    candidate_sha: str,
    key: bytes,
    provider: InventoryProvider,
    redis_quiesced: bool,
) -> dict[str, Any]:
    """Return PHI-free evidence; all failures are represented by stable codes."""

    validate_policy(policy)
    if policy_sha256 != CANONICAL_POLICY_SHA256:
        raise InventoryFailure("policy.invalid")
    _validate_approved_state(
        approved,
        policy_sha256=policy_sha256,
        profile=profile_name,
    )
    if not SHA_RE.fullmatch(source_sha) or not SHA_RE.fullmatch(candidate_sha):
        raise InventoryFailure("revision.invalid")
    expected_tables = _expected_tables(policy, profile_name)
    profile = policy["profiles"][profile_name]
    errors: list[str] = []
    counts: dict[str, int] = {}
    rule_counts: dict[str, int] = {}
    state_hmacs: dict[str, str] = {}
    identity_hmacs: dict[str, str] = {}

    def fail(code: str) -> None:
        if len(errors) < MAXIMUM_ERRORS and code not in errors:
            errors.append(code)

    database_counts = provider.database_global_counts()
    for value in (
        database_counts.non_system_schema_count,
        database_counts.sequence_count,
        database_counts.large_object_count,
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise InventoryFailure("database.enumeration_failed")
    counts["database.non_system_schemas"] = database_counts.non_system_schema_count
    counts["database.sequences"] = database_counts.sequence_count
    counts["database.large_objects"] = database_counts.large_object_count
    if database_counts.non_system_schema_count != 1:
        fail("database.schema_set")
    if database_counts.sequence_count:
        fail("database.sequence_set")
    if database_counts.large_object_count:
        fail("database.large_objects")
    observed_catalog = {
        name: {
            "kind": kind,
            "row_security": row_security,
            "columns": [list(column) for column in columns],
        }
        for name, (kind, row_security, columns) in provider.database_catalog().items()
    }
    expected_control = policy["database"]["control_table"]
    expected_catalog = {
        name: {"kind": "r", "row_security": False, "columns": spec["columns"]}
        for name, spec in expected_tables.items()
    } | {
        expected_control["name"]: {
            "kind": "r",
            "row_security": False,
            "columns": expected_control["columns"],
        }
    }
    catalog_valid = set(observed_catalog) == set(expected_catalog)
    if not catalog_valid:
        fail("database.catalog.table_set")
    for name in sorted(set(observed_catalog) & set(expected_catalog)):
        if observed_catalog[name] != expected_catalog[name]:
            catalog_valid = False
            fail(f"database.catalog.{name}.columns")
    catalog_hmac = _digest(
        key,
        "database-catalog",
        [_canonical_json(observed_catalog)],
    )
    schema_descriptor = provider.database_schema_descriptor()
    if (
        not isinstance(schema_descriptor.sha256, str)
        or HEX_256_RE.fullmatch(schema_descriptor.sha256) is None
        or set(schema_descriptor.counts) != set(SCHEMA_DESCRIPTOR_SECTIONS)
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in schema_descriptor.counts.values()
        )
    ):
        raise InventoryFailure("database.schema_descriptor_failed")
    for section, count in schema_descriptor.counts.items():
        counts[f"database.schema.{section}"] = count
    expected_descriptor = profile["schema_descriptor"]
    if schema_descriptor.counts != expected_descriptor[
        "counts"
    ] or not hmac.compare_digest(
        schema_descriptor.sha256,
        expected_descriptor["sha256"],
    ):
        fail("database.schema_descriptor")
    revisions = list(provider.alembic_revisions())
    counts["database.alembic_version"] = len(revisions)
    if revisions != [profile["revision"]]:
        fail("database.alembic_version")

    approved_tables = approved["tables"]
    retained_names = {
        name
        for name, spec in expected_tables.items()
        if spec["disposition"] != "required_empty"
    }
    if set(approved_tables) != retained_names:
        fail("approved_state.table_set")

    for table_name, spec in sorted(expected_tables.items()):
        if not catalog_valid:
            break
        disposition = spec["disposition"]
        if disposition == "required_empty":
            count = provider.table_count(table_name)
            counts[f"database.{table_name}"] = count
            state_hmacs[table_name] = _digest(
                key,
                f"database-table:{table_name}:empty-count",
                [str(count)],
            )
            if count:
                fail(f"database.table.{table_name}.required_empty")
            continue

        state = provider.table_state(table_name, spec.get("identity_columns", []))
        counts[f"database.{table_name}"] = state.count
        state_hmacs[table_name] = state.state_hmac
        if state.identity_hmac is not None:
            identity_hmacs[table_name] = state.identity_hmac
        expected_state = approved_tables.get(table_name)
        if expected_state is None or not _matches_approval(state, expected_state):
            fail(f"database.table.{table_name}.approved_state")
        for rule in spec.get("rules", []):
            if profile_name not in rule.get("profiles", ["pre", "post"]):
                continue
            count = provider.violation_count(table_name, rule)
            rule_key = f"database.{table_name}.{rule['id']}"
            rule_counts[rule_key] = count
            if count:
                fail(f"database.table.{table_name}.{rule['id']}")

    blob_policy = policy["blob"]
    observed_containers = sorted(provider.blob_container_names())
    repeated_containers = sorted(provider.blob_container_names())
    expected_containers = blob_policy["containers"]
    if (
        observed_containers != repeated_containers
        or len(observed_containers) != len(set(observed_containers))
        or set(observed_containers) != set(expected_containers)
    ):
        fail("blob.container_set")
    approved_containers = approved["containers"]
    fixed_containers = {
        name
        for name, disposition in expected_containers.items()
        if disposition == "fixed_catalog"
    }
    if set(approved_containers) != fixed_containers:
        fail("approved_state.container_set")
    blob_hmacs: dict[str, str] = {}
    for container, disposition in sorted(expected_containers.items()):
        if container not in observed_containers:
            continue
        include_content = disposition == "fixed_catalog"
        state = provider.blob_state(container, include_content=include_content)
        repeated_state = provider.blob_state(
            container,
            include_content=include_content,
        )
        counts[f"blob.{container}"] = state.count
        blob_hmacs[container] = state.state_hmac
        if state != repeated_state:
            fail(f"blob.container.{container}.unstable")
        if disposition == "required_empty":
            if state.count:
                fail(f"blob.container.{container}.required_empty")
        else:
            expected_state = approved_containers.get(container)
            if expected_state is None or not _matches_approval(state, expected_state):
                fail(f"blob.container.{container}.approved_state")

    redis_surface = provider.redis_surface()
    repeated_redis_surface = provider.redis_surface()
    counts["redis.logical_databases_with_keys"] = redis_surface.logical_database_count
    counts["redis.keys"] = redis_surface.key_count
    if not redis_quiesced:
        fail("redis.not_quiesced")
    if redis_surface.key_count != 0 or redis_surface.logical_database_count != 0:
        fail("redis.required_empty")
    if redis_surface != repeated_redis_surface:
        fail("redis.unstable")
    redis_hmac = _digest(
        key,
        "redis-state",
        [
            _canonical_json(
                {
                    "logical_databases_with_keys": (
                        redis_surface.logical_database_count
                    ),
                    "keys": redis_surface.key_count,
                }
            )
        ],
    )

    return {
        "schema_version": 1,
        "policy_id": policy["policy_id"],
        "policy_sha256": policy_sha256,
        "source_sha": source_sha,
        "phase": profile_name,
        "candidate_sha": candidate_sha,
        "counts": counts,
        "rule_violation_counts": rule_counts,
        "identity_hmacs": identity_hmacs,
        "catalog_hmac": catalog_hmac,
        "schema_descriptor_sha256": schema_descriptor.sha256,
        "state_hmacs": state_hmacs,
        "blob_state_hmacs": blob_hmacs,
        "redis_state_hmac": redis_hmac,
        "errors": errors,
        "pass": not errors,
    }


class LiveInventoryProvider:
    """Read-only production adapters, imported lazily to keep the module inert."""

    def __init__(
        self,
        *,
        database_url: str,
        blob_account_url: str,
        redis_url: str,
        key: bytes,
        policy_id: str,
    ) -> None:
        import psycopg
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient
        from redis import Redis

        self._key = key
        self._policy_id = policy_id
        try:
            self._connection: Any = psycopg.connect(
                database_url,
                connect_timeout=CONNECT_TIMEOUT_SECONDS,
                autocommit=True,
            )
            self._connection.execute(
                "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY DEFERRABLE"
            )
            self._connection.execute(
                f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MILLISECONDS}"
            )
            self._connection.execute(
                f"SET LOCAL lock_timeout = {LOCK_TIMEOUT_MILLISECONDS}"
            )
            self._connection.execute("SET LOCAL row_security = off")
            _configure_catalog_session(self._connection)
            database_name, server_version, tls_active = self._connection.execute(
                "SELECT current_database(), "
                "current_setting('server_version_num')::integer, "
                "COALESCE((SELECT ssl FROM pg_catalog.pg_stat_ssl "
                "WHERE pid = pg_catalog.pg_backend_pid()), FALSE)"
            ).fetchone()
            if (
                database_name != "staging"
                or server_version // 10_000 != 16
                or tls_active is not True
            ):
                raise InventoryFailure("database.server_identity")
            credential = DefaultAzureCredential(
                exclude_interactive_browser_credential=True
            )
            self._blob: Any = BlobServiceClient(
                account_url=blob_account_url,
                credential=credential,
            )
            self._redis: Any = Redis.from_url(
                redis_url,
                ssl_check_hostname=True,
                socket_connect_timeout=CONNECT_TIMEOUT_SECONDS,
                socket_timeout=CONNECT_TIMEOUT_SECONDS,
            )
        except InventoryFailure:
            raise
        except Exception:
            raise InventoryFailure("provider.connect_failed") from None

    def close(self) -> None:
        try:
            self._connection.execute("ROLLBACK")
        finally:
            self._connection.close()
            self._redis.close()

    def database_global_counts(self) -> DatabaseGlobalCounts:
        schema_count, sequence_count, large_object_count = self._connection.execute(
            "SELECT "
            "(SELECT count(*) FROM pg_catalog.pg_namespace "
            "WHERE nspname <> 'information_schema' AND nspname !~ '^pg_'), "
            "(SELECT count(*) FROM pg_catalog.pg_class AS relation "
            "WHERE relation.relkind = 'S'), "
            "(SELECT count(*) FROM pg_catalog.pg_largeobject_metadata)"
        ).fetchone()
        return DatabaseGlobalCounts(
            non_system_schema_count=int(schema_count),
            sequence_count=int(sequence_count),
            large_object_count=int(large_object_count),
        )

    def database_catalog(self) -> Mapping[str, RelationContract]:
        _configure_catalog_session(self._connection)
        rows = self._connection.execute(
            "SELECT relation.relname, relation.relkind, relation.relrowsecurity, "
            "attribute.attname, "
            "pg_catalog.format_type(attribute.atttypid, attribute.atttypmod), "
            "attribute.attnotnull "
            "FROM pg_catalog.pg_class AS relation "
            "JOIN pg_catalog.pg_namespace AS namespace "
            "ON namespace.oid = relation.relnamespace "
            "LEFT JOIN pg_catalog.pg_attribute AS attribute "
            "ON attribute.attrelid = relation.oid "
            "AND attribute.attnum > 0 AND NOT attribute.attisdropped "
            "WHERE namespace.nspname = 'public' "
            "AND relation.relkind IN ('r', 'p', 'f', 'm', 'v') "
            "ORDER BY relation.relname, attribute.attnum"
        ).fetchall()
        result: dict[str, tuple[str, bool, list[ColumnContract]]] = {}
        for table, kind, row_security, column, data_type, not_null in rows:
            relation = result.setdefault(table, (kind, row_security, []))
            if column is not None:
                relation[2].append((column, data_type, not_null))
        return result

    def _descriptor_records(
        self,
        query: str,
        fields: Sequence[str],
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        try:
            cursor = self._connection.execute(query)
            for row in cursor:
                if len(row) != len(fields):
                    raise InventoryFailure("database.schema_descriptor_failed")
                records.append(dict(zip(fields, row, strict=True)))
                if len(records) > MAXIMUM_SCHEMA_DESCRIPTOR_RECORDS:
                    raise InventoryFailure("database.schema_descriptor_failed")
        except InventoryFailure:
            raise
        except Exception:
            raise InventoryFailure("database.schema_descriptor_failed") from None
        return records

    def database_schema_descriptor(self) -> SchemaDescriptor:
        """Fingerprint all approved public-schema DDL from the open snapshot."""

        _configure_catalog_session(self._connection)
        sections: dict[str, list[dict[str, Any]]] = {}
        sections["namespace_objects"] = self._descriptor_records(
            "SELECT identified.type, identified.object_names, "
            "identified.object_args "
            "FROM pg_catalog.pg_depend AS dependency "
            "JOIN pg_catalog.pg_namespace AS namespace "
            "ON namespace.oid = dependency.refobjid "
            "CROSS JOIN LATERAL pg_catalog.pg_identify_object_as_address("
            "dependency.classid, dependency.objid, dependency.objsubid) "
            "AS identified "
            "WHERE dependency.refclassid = "
            "'pg_catalog.pg_namespace'::pg_catalog.regclass "
            "AND dependency.deptype = 'n' "
            "AND namespace.nspname = 'public' "
            "ORDER BY identified.type, identified.object_names, "
            "identified.object_args",
            ("kind", "names", "arguments"),
        )
        sections["relations"] = self._descriptor_records(
            "SELECT namespace.nspname, relation.relname, relation.relkind, "
            "relation.relpersistence, relation.relreplident, "
            "relation.relrowsecurity, relation.relforcerowsecurity, "
            "relation.relispartition, "
            "COALESCE(access_method.amname, ''), "
            "COALESCE(tablespace.spcname, ''), "
            "COALESCE((SELECT pg_catalog.string_agg(option, E'\\n' ORDER BY option) "
            "FROM pg_catalog.unnest(relation.reloptions) AS option), ''), "
            "COALESCE(pg_catalog.pg_get_expr(relation.relpartbound, "
            "relation.oid, FALSE), ''), "
            "COALESCE(pg_catalog.pg_get_partkeydef(relation.oid), '') "
            "FROM pg_catalog.pg_class AS relation "
            "JOIN pg_catalog.pg_namespace AS namespace "
            "ON namespace.oid = relation.relnamespace "
            "LEFT JOIN pg_catalog.pg_am AS access_method "
            "ON access_method.oid = relation.relam "
            "LEFT JOIN pg_catalog.pg_tablespace AS tablespace "
            "ON tablespace.oid = relation.reltablespace "
            "WHERE namespace.nspname = 'public' "
            "AND relation.relkind IN ('r', 'p', 'f', 'S', 'v', 'm', 'c') "
            "ORDER BY relation.relname, relation.relkind",
            (
                "schema",
                "name",
                "kind",
                "persistence",
                "replica_identity",
                "row_security",
                "force_row_security",
                "is_partition",
                "access_method",
                "tablespace",
                "options",
                "partition_bound",
                "partition_key",
            ),
        )
        sections["columns"] = self._descriptor_records(
            "SELECT namespace.nspname, relation.relname, relation.relkind, "
            "attribute.attnum, attribute.attname, "
            "pg_catalog.format_type(attribute.atttypid, attribute.atttypmod), "
            "attribute.attnotnull, attribute.attidentity, "
            "attribute.attgenerated, attribute.attislocal, "
            "attribute.attinhcount, attribute.attndims, "
            "attribute.attstorage, attribute.attcompression, "
            "attribute.attstattarget, "
            "COALESCE(collation_namespace.nspname, ''), "
            "COALESCE(collation_value.collname, ''), "
            "COALESCE(pg_catalog.pg_get_expr(default_value.adbin, "
            "default_value.adrelid, FALSE), '') "
            "FROM pg_catalog.pg_class AS relation "
            "JOIN pg_catalog.pg_namespace AS namespace "
            "ON namespace.oid = relation.relnamespace "
            "JOIN pg_catalog.pg_attribute AS attribute "
            "ON attribute.attrelid = relation.oid "
            "AND attribute.attnum > 0 AND NOT attribute.attisdropped "
            "LEFT JOIN pg_catalog.pg_attrdef AS default_value "
            "ON default_value.adrelid = relation.oid "
            "AND default_value.adnum = attribute.attnum "
            "LEFT JOIN pg_catalog.pg_collation AS collation_value "
            "ON collation_value.oid = attribute.attcollation "
            "AND attribute.attcollation <> 0 "
            "LEFT JOIN pg_catalog.pg_namespace AS collation_namespace "
            "ON collation_namespace.oid = collation_value.collnamespace "
            "WHERE namespace.nspname = 'public' "
            "AND relation.relkind IN ('r', 'p', 'f', 'S', 'v', 'm', 'c') "
            "ORDER BY relation.relname, attribute.attnum",
            (
                "schema",
                "relation",
                "relation_kind",
                "position",
                "name",
                "type",
                "not_null",
                "identity",
                "generated",
                "is_local",
                "inheritance_count",
                "dimensions",
                "storage",
                "compression",
                "statistics_target",
                "collation_schema",
                "collation",
                "default_or_generated_expression",
            ),
        )
        sections["constraints"] = self._descriptor_records(
            "SELECT namespace.nspname, constraint_value.conname, "
            "constraint_value.contype, constraint_value.condeferrable, "
            "constraint_value.condeferred, constraint_value.convalidated, "
            "constraint_value.conislocal, constraint_value.coninhcount, "
            "constraint_value.connoinherit, "
            "COALESCE(relation.relname, ''), "
            "COALESCE(relation.relkind, ''), "
            "COALESCE(domain_type.typname, ''), "
            "COALESCE(parent_constraint.conname, ''), "
            "COALESCE(parent_relation.relname, ''), "
            "pg_catalog.pg_get_constraintdef(constraint_value.oid, FALSE) "
            "FROM pg_catalog.pg_constraint AS constraint_value "
            "JOIN pg_catalog.pg_namespace AS namespace "
            "ON namespace.oid = constraint_value.connamespace "
            "LEFT JOIN pg_catalog.pg_class AS relation "
            "ON relation.oid = constraint_value.conrelid "
            "LEFT JOIN pg_catalog.pg_type AS domain_type "
            "ON domain_type.oid = constraint_value.contypid "
            "LEFT JOIN pg_catalog.pg_constraint AS parent_constraint "
            "ON parent_constraint.oid = constraint_value.conparentid "
            "LEFT JOIN pg_catalog.pg_class AS parent_relation "
            "ON parent_relation.oid = parent_constraint.conrelid "
            "WHERE namespace.nspname = 'public' "
            "ORDER BY COALESCE(relation.relname, domain_type.typname, ''), "
            "constraint_value.conname, constraint_value.contype",
            (
                "schema",
                "name",
                "type",
                "deferrable",
                "initially_deferred",
                "validated",
                "is_local",
                "inheritance_count",
                "no_inherit",
                "relation",
                "relation_kind",
                "domain",
                "parent_constraint",
                "parent_relation",
                "definition",
            ),
        )
        sections["indexes"] = self._descriptor_records(
            "SELECT table_namespace.nspname, table_relation.relname, "
            "index_namespace.nspname, index_relation.relname, "
            "access_method.amname, index_value.indisunique, "
            "index_value.indisprimary, index_value.indisexclusion, "
            "index_value.indimmediate, index_value.indisclustered, "
            "index_value.indisvalid, index_value.indcheckxmin, "
            "index_value.indisready, index_value.indislive, "
            "index_value.indisreplident, index_value.indnullsnotdistinct, "
            "COALESCE(pg_catalog.pg_get_expr(index_value.indexprs, "
            "index_value.indrelid, FALSE), ''), "
            "COALESCE(pg_catalog.pg_get_expr(index_value.indpred, "
            "index_value.indrelid, FALSE), ''), "
            "pg_catalog.pg_get_indexdef(index_relation.oid, 0, FALSE) "
            "FROM pg_catalog.pg_index AS index_value "
            "JOIN pg_catalog.pg_class AS table_relation "
            "ON table_relation.oid = index_value.indrelid "
            "JOIN pg_catalog.pg_namespace AS table_namespace "
            "ON table_namespace.oid = table_relation.relnamespace "
            "JOIN pg_catalog.pg_class AS index_relation "
            "ON index_relation.oid = index_value.indexrelid "
            "JOIN pg_catalog.pg_namespace AS index_namespace "
            "ON index_namespace.oid = index_relation.relnamespace "
            "JOIN pg_catalog.pg_am AS access_method "
            "ON access_method.oid = index_relation.relam "
            "WHERE table_namespace.nspname = 'public' "
            "ORDER BY table_relation.relname, index_relation.relname",
            (
                "table_schema",
                "table",
                "index_schema",
                "name",
                "access_method",
                "unique",
                "primary",
                "exclusion",
                "immediate",
                "clustered",
                "valid",
                "check_xmin",
                "ready",
                "live",
                "replica_identity",
                "nulls_not_distinct",
                "expressions",
                "predicate",
                "definition",
            ),
        )
        sections["triggers"] = self._descriptor_records(
            "SELECT table_namespace.nspname, table_relation.relname, "
            "trigger_value.tgname, trigger_value.tgenabled, "
            "pg_catalog.pg_get_triggerdef(trigger_value.oid, FALSE), "
            "function_namespace.nspname, function_value.proname, "
            "pg_catalog.pg_get_function_identity_arguments(function_value.oid) "
            "FROM pg_catalog.pg_trigger AS trigger_value "
            "JOIN pg_catalog.pg_class AS table_relation "
            "ON table_relation.oid = trigger_value.tgrelid "
            "JOIN pg_catalog.pg_namespace AS table_namespace "
            "ON table_namespace.oid = table_relation.relnamespace "
            "JOIN pg_catalog.pg_proc AS function_value "
            "ON function_value.oid = trigger_value.tgfoid "
            "JOIN pg_catalog.pg_namespace AS function_namespace "
            "ON function_namespace.oid = function_value.pronamespace "
            "WHERE table_namespace.nspname = 'public' "
            "AND NOT trigger_value.tgisinternal "
            "ORDER BY table_relation.relname, trigger_value.tgname",
            (
                "table_schema",
                "table",
                "name",
                "enabled",
                "definition",
                "function_schema",
                "function",
                "function_identity_arguments",
            ),
        )
        sections["trigger_functions"] = self._descriptor_records(
            "SELECT DISTINCT function_namespace.nspname, "
            "function_value.proname, "
            "pg_catalog.pg_get_function_identity_arguments(function_value.oid), "
            "pg_catalog.pg_get_functiondef(function_value.oid) "
            "FROM pg_catalog.pg_trigger AS trigger_value "
            "JOIN pg_catalog.pg_class AS table_relation "
            "ON table_relation.oid = trigger_value.tgrelid "
            "JOIN pg_catalog.pg_namespace AS table_namespace "
            "ON table_namespace.oid = table_relation.relnamespace "
            "JOIN pg_catalog.pg_proc AS function_value "
            "ON function_value.oid = trigger_value.tgfoid "
            "JOIN pg_catalog.pg_namespace AS function_namespace "
            "ON function_namespace.oid = function_value.pronamespace "
            "WHERE table_namespace.nspname = 'public' "
            "AND NOT trigger_value.tgisinternal "
            "ORDER BY function_namespace.nspname, function_value.proname, "
            "pg_catalog.pg_get_function_identity_arguments(function_value.oid)",
            ("schema", "name", "identity_arguments", "definition"),
        )
        sections["rls_policies"] = self._descriptor_records(
            "SELECT table_namespace.nspname, table_relation.relname, "
            "policy.polname, policy.polpermissive, policy.polcmd, "
            "COALESCE((SELECT pg_catalog.string_agg("
            "CASE WHEN role_oid = 0 THEN 'PUBLIC' "
            "ELSE pg_catalog.pg_get_userbyid(role_oid) END, E'\\n' "
            "ORDER BY CASE WHEN role_oid = 0 THEN 'PUBLIC' "
            "ELSE pg_catalog.pg_get_userbyid(role_oid) END) "
            "FROM pg_catalog.unnest(policy.polroles) AS role_oid), ''), "
            "COALESCE(pg_catalog.pg_get_expr(policy.polqual, "
            "policy.polrelid, FALSE), ''), "
            "COALESCE(pg_catalog.pg_get_expr(policy.polwithcheck, "
            "policy.polrelid, FALSE), '') "
            "FROM pg_catalog.pg_policy AS policy "
            "JOIN pg_catalog.pg_class AS table_relation "
            "ON table_relation.oid = policy.polrelid "
            "JOIN pg_catalog.pg_namespace AS table_namespace "
            "ON table_namespace.oid = table_relation.relnamespace "
            "WHERE table_namespace.nspname = 'public' "
            "ORDER BY table_relation.relname, policy.polname",
            (
                "table_schema",
                "table",
                "name",
                "permissive",
                "command",
                "roles",
                "using_expression",
                "check_expression",
            ),
        )
        sections["types"] = self._descriptor_records(
            "SELECT namespace.nspname, type_value.typname, "
            "type_value.typtype, type_value.typcategory, "
            "type_value.typispreferred, type_value.typisdefined, "
            "type_value.typlen, type_value.typbyval, type_value.typdelim, "
            "type_value.typalign, type_value.typstorage, "
            "type_value.typnotnull, type_value.typtypmod, type_value.typndims, "
            "COALESCE(pg_catalog.pg_get_expr(type_value.typdefaultbin, 0, FALSE), "
            "type_value.typdefault, ''), "
            "COALESCE(relation_namespace.nspname, ''), "
            "COALESCE(relation.relname, ''), "
            "COALESCE(element_namespace.nspname, ''), "
            "COALESCE(element_type.typname, ''), "
            "COALESCE(array_namespace.nspname, ''), "
            "COALESCE(array_type.typname, ''), "
            "COALESCE(base_namespace.nspname, ''), "
            "COALESCE(base_type.typname, ''), "
            "COALESCE(collation_namespace.nspname, ''), "
            "COALESCE(collation_value.collname, ''), "
            "type_value.typinput::pg_catalog.regprocedure::text, "
            "type_value.typoutput::pg_catalog.regprocedure::text, "
            "CASE WHEN type_value.typreceive = 0 THEN '' ELSE "
            "type_value.typreceive::pg_catalog.regprocedure::text END, "
            "CASE WHEN type_value.typsend = 0 THEN '' ELSE "
            "type_value.typsend::pg_catalog.regprocedure::text END, "
            "CASE WHEN type_value.typmodin = 0 THEN '' ELSE "
            "type_value.typmodin::pg_catalog.regprocedure::text END, "
            "CASE WHEN type_value.typmodout = 0 THEN '' ELSE "
            "type_value.typmodout::pg_catalog.regprocedure::text END, "
            "CASE WHEN type_value.typanalyze = 0 THEN '' ELSE "
            "type_value.typanalyze::pg_catalog.regprocedure::text END, "
            "CASE WHEN type_value.typsubscript = 0 THEN '' ELSE "
            "type_value.typsubscript::pg_catalog.regprocedure::text END, "
            "COALESCE(range_subtype_namespace.nspname, ''), "
            "COALESCE(range_subtype.typname, ''), "
            "COALESCE(multirange_namespace.nspname, ''), "
            "COALESCE(multirange_type.typname, ''), "
            "COALESCE(range_collation_namespace.nspname, ''), "
            "COALESCE(range_collation.collname, ''), "
            "COALESCE(opclass_namespace.nspname, ''), "
            "COALESCE(opclass.opcname, ''), "
            "CASE WHEN range_value.rngcanonical = 0 THEN '' ELSE "
            "range_value.rngcanonical::pg_catalog.regprocedure::text END, "
            "CASE WHEN range_value.rngsubdiff = 0 THEN '' ELSE "
            "range_value.rngsubdiff::pg_catalog.regprocedure::text END "
            "FROM pg_catalog.pg_type AS type_value "
            "JOIN pg_catalog.pg_namespace AS namespace "
            "ON namespace.oid = type_value.typnamespace "
            "LEFT JOIN pg_catalog.pg_class AS relation "
            "ON relation.oid = type_value.typrelid "
            "LEFT JOIN pg_catalog.pg_namespace AS relation_namespace "
            "ON relation_namespace.oid = relation.relnamespace "
            "LEFT JOIN pg_catalog.pg_type AS element_type "
            "ON element_type.oid = type_value.typelem "
            "LEFT JOIN pg_catalog.pg_namespace AS element_namespace "
            "ON element_namespace.oid = element_type.typnamespace "
            "LEFT JOIN pg_catalog.pg_type AS array_type "
            "ON array_type.oid = type_value.typarray "
            "LEFT JOIN pg_catalog.pg_namespace AS array_namespace "
            "ON array_namespace.oid = array_type.typnamespace "
            "LEFT JOIN pg_catalog.pg_type AS base_type "
            "ON base_type.oid = type_value.typbasetype "
            "LEFT JOIN pg_catalog.pg_namespace AS base_namespace "
            "ON base_namespace.oid = base_type.typnamespace "
            "LEFT JOIN pg_catalog.pg_collation AS collation_value "
            "ON collation_value.oid = type_value.typcollation "
            "LEFT JOIN pg_catalog.pg_namespace AS collation_namespace "
            "ON collation_namespace.oid = collation_value.collnamespace "
            "LEFT JOIN pg_catalog.pg_range AS range_value "
            "ON range_value.rngtypid = type_value.oid "
            "LEFT JOIN pg_catalog.pg_type AS range_subtype "
            "ON range_subtype.oid = range_value.rngsubtype "
            "LEFT JOIN pg_catalog.pg_namespace AS range_subtype_namespace "
            "ON range_subtype_namespace.oid = range_subtype.typnamespace "
            "LEFT JOIN pg_catalog.pg_type AS multirange_type "
            "ON multirange_type.oid = range_value.rngmultitypid "
            "LEFT JOIN pg_catalog.pg_namespace AS multirange_namespace "
            "ON multirange_namespace.oid = multirange_type.typnamespace "
            "LEFT JOIN pg_catalog.pg_collation AS range_collation "
            "ON range_collation.oid = range_value.rngcollation "
            "LEFT JOIN pg_catalog.pg_namespace AS range_collation_namespace "
            "ON range_collation_namespace.oid = range_collation.collnamespace "
            "LEFT JOIN pg_catalog.pg_opclass AS opclass "
            "ON opclass.oid = range_value.rngsubopc "
            "LEFT JOIN pg_catalog.pg_namespace AS opclass_namespace "
            "ON opclass_namespace.oid = opclass.opcnamespace "
            "WHERE namespace.nspname = 'public' "
            "ORDER BY type_value.typname",
            (
                "schema",
                "name",
                "kind",
                "category",
                "preferred",
                "defined",
                "length",
                "by_value",
                "delimiter",
                "alignment",
                "storage",
                "not_null",
                "type_modifier",
                "array_dimensions",
                "default_expression",
                "relation_schema",
                "relation",
                "element_schema",
                "element_type",
                "array_schema",
                "array_type",
                "base_schema",
                "base_type",
                "collation_schema",
                "collation",
                "input_function",
                "output_function",
                "receive_function",
                "send_function",
                "type_modifier_input_function",
                "type_modifier_output_function",
                "analyze_function",
                "subscript_function",
                "range_subtype_schema",
                "range_subtype",
                "multirange_schema",
                "multirange_type",
                "range_collation_schema",
                "range_collation",
                "range_operator_class_schema",
                "range_operator_class",
                "range_canonical_function",
                "range_subtype_diff_function",
            ),
        )
        sections["enum_labels"] = self._descriptor_records(
            "SELECT namespace.nspname, type_value.typname, "
            "enum_value.enumsortorder::text, enum_value.enumlabel "
            "FROM pg_catalog.pg_enum AS enum_value "
            "JOIN pg_catalog.pg_type AS type_value "
            "ON type_value.oid = enum_value.enumtypid "
            "JOIN pg_catalog.pg_namespace AS namespace "
            "ON namespace.oid = type_value.typnamespace "
            "WHERE namespace.nspname = 'public' "
            "ORDER BY type_value.typname, enum_value.enumsortorder",
            ("schema", "type", "sort_order", "label"),
        )
        sections["views"] = self._descriptor_records(
            "SELECT namespace.nspname, relation.relname, relation.relkind, "
            "COALESCE((SELECT pg_catalog.string_agg(option, E'\\n' ORDER BY option) "
            "FROM pg_catalog.unnest(relation.reloptions) AS option), ''), "
            "pg_catalog.pg_get_viewdef(relation.oid, FALSE) "
            "FROM pg_catalog.pg_class AS relation "
            "JOIN pg_catalog.pg_namespace AS namespace "
            "ON namespace.oid = relation.relnamespace "
            "WHERE namespace.nspname = 'public' "
            "AND relation.relkind IN ('v', 'm') "
            "ORDER BY relation.relname",
            ("schema", "name", "kind", "options", "definition"),
        )
        sections["routines"] = self._descriptor_records(
            "SELECT namespace.nspname, routine.proname, routine.prokind, "
            "pg_catalog.pg_get_function_identity_arguments(routine.oid), "
            "pg_catalog.pg_get_function_arguments(routine.oid), "
            "COALESCE(pg_catalog.pg_get_function_result(routine.oid), ''), "
            "language.lanname, routine.provolatile, routine.proparallel, "
            "routine.proisstrict, routine.prosecdef, routine.proleakproof, "
            "routine.proretset, routine.procost::text, routine.prorows::text, "
            "COALESCE((SELECT pg_catalog.string_agg(option, E'\\n' ORDER BY option) "
            "FROM pg_catalog.unnest(routine.proconfig) AS option), ''), "
            "routine.prosrc, COALESCE(routine.probin, ''), "
            "CASE WHEN routine.prokind = 'a' THEN '' "
            "ELSE pg_catalog.pg_get_functiondef(routine.oid) END, "
            "COALESCE(aggregate_value.aggkind, ''), "
            "COALESCE(aggregate_value.aggnumdirectargs, 0), "
            "CASE WHEN aggregate_value.aggtransfn = 0 THEN '' "
            "ELSE aggregate_value.aggtransfn::pg_catalog.regprocedure::text END, "
            "CASE WHEN aggregate_value.aggfinalfn = 0 THEN '' "
            "ELSE aggregate_value.aggfinalfn::pg_catalog.regprocedure::text END, "
            "CASE WHEN aggregate_value.aggcombinefn = 0 THEN '' "
            "ELSE aggregate_value.aggcombinefn::pg_catalog.regprocedure::text END, "
            "CASE WHEN aggregate_value.aggserialfn = 0 THEN '' "
            "ELSE aggregate_value.aggserialfn::pg_catalog.regprocedure::text END, "
            "CASE WHEN aggregate_value.aggdeserialfn = 0 THEN '' "
            "ELSE aggregate_value.aggdeserialfn::pg_catalog.regprocedure::text END, "
            "CASE WHEN aggregate_value.aggmtransfn = 0 THEN '' "
            "ELSE aggregate_value.aggmtransfn::pg_catalog.regprocedure::text END, "
            "CASE WHEN aggregate_value.aggminvtransfn = 0 THEN '' "
            "ELSE aggregate_value.aggminvtransfn::pg_catalog.regprocedure::text END, "
            "CASE WHEN aggregate_value.aggmfinalfn = 0 THEN '' "
            "ELSE aggregate_value.aggmfinalfn::pg_catalog.regprocedure::text END, "
            "COALESCE(aggregate_value.aggfinalextra, FALSE), "
            "COALESCE(aggregate_value.aggmfinalextra, FALSE), "
            "COALESCE(aggregate_value.aggfinalmodify, ''), "
            "COALESCE(aggregate_value.aggmfinalmodify, ''), "
            "CASE WHEN aggregate_value.aggsortop = 0 THEN '' "
            "ELSE aggregate_value.aggsortop::pg_catalog.regoperator::text END, "
            "COALESCE(pg_catalog.format_type(aggregate_value.aggtranstype, NULL), ''), "
            "COALESCE(aggregate_value.aggtransspace, 0), "
            "COALESCE(pg_catalog.format_type(aggregate_value.aggmtranstype, NULL), ''), "
            "COALESCE(aggregate_value.aggmtransspace, 0), "
            "COALESCE(aggregate_value.agginitval, ''), "
            "COALESCE(aggregate_value.aggminitval, '') "
            "FROM pg_catalog.pg_proc AS routine "
            "JOIN pg_catalog.pg_namespace AS namespace "
            "ON namespace.oid = routine.pronamespace "
            "JOIN pg_catalog.pg_language AS language "
            "ON language.oid = routine.prolang "
            "LEFT JOIN pg_catalog.pg_aggregate AS aggregate_value "
            "ON aggregate_value.aggfnoid = routine.oid "
            "WHERE namespace.nspname = 'public' "
            "ORDER BY routine.proname, "
            "pg_catalog.pg_get_function_identity_arguments(routine.oid)",
            (
                "schema",
                "name",
                "kind",
                "identity_arguments",
                "arguments",
                "result",
                "language",
                "volatility",
                "parallel",
                "strict",
                "security_definer",
                "leakproof",
                "returns_set",
                "cost",
                "rows",
                "configuration",
                "source",
                "binary",
                "definition",
                "aggregate_kind",
                "aggregate_direct_arguments",
                "aggregate_transition_function",
                "aggregate_final_function",
                "aggregate_combine_function",
                "aggregate_serial_function",
                "aggregate_deserial_function",
                "aggregate_moving_transition_function",
                "aggregate_moving_inverse_function",
                "aggregate_moving_final_function",
                "aggregate_final_extra",
                "aggregate_moving_final_extra",
                "aggregate_final_modify",
                "aggregate_moving_final_modify",
                "aggregate_sort_operator",
                "aggregate_transition_type",
                "aggregate_transition_space",
                "aggregate_moving_transition_type",
                "aggregate_moving_transition_space",
                "aggregate_initial_value",
                "aggregate_moving_initial_value",
            ),
        )
        sections["authorizations"] = self._descriptor_records(
            "WITH database_owner AS ("
            "SELECT datdba AS oid FROM pg_catalog.pg_database "
            "WHERE datname = pg_catalog.current_database()), "
            "objects AS ("
            "SELECT 'schema'::text AS object_kind, namespace.nspname AS schema, "
            "''::text AS name, ''::text AS identity_arguments, "
            "''::text AS subobject, namespace.nspowner AS owner_oid, "
            "COALESCE(namespace.nspacl, "
            "pg_catalog.acldefault('n', namespace.nspowner)) AS acl "
            "FROM pg_catalog.pg_namespace AS namespace "
            "WHERE namespace.nspname = 'public' "
            "UNION ALL "
            "SELECT 'relation:' || relation.relkind::text, namespace.nspname, "
            "relation.relname, '', '', relation.relowner, "
            "COALESCE(relation.relacl, CASE "
            "WHEN relation.relkind = 'S' THEN "
            "pg_catalog.acldefault('S', relation.relowner) "
            "WHEN relation.relkind IN ('r', 'p', 'v', 'm', 'f') THEN "
            "pg_catalog.acldefault('r', relation.relowner) "
            "ELSE NULL::pg_catalog.aclitem[] END) "
            "FROM pg_catalog.pg_class AS relation "
            "JOIN pg_catalog.pg_namespace AS namespace "
            "ON namespace.oid = relation.relnamespace "
            "WHERE namespace.nspname = 'public' "
            "UNION ALL "
            "SELECT 'column', namespace.nspname, relation.relname, '', "
            "attribute.attname, relation.relowner, attribute.attacl "
            "FROM pg_catalog.pg_attribute AS attribute "
            "JOIN pg_catalog.pg_class AS relation "
            "ON relation.oid = attribute.attrelid "
            "JOIN pg_catalog.pg_namespace AS namespace "
            "ON namespace.oid = relation.relnamespace "
            "WHERE namespace.nspname = 'public' "
            "AND attribute.attnum > 0 AND NOT attribute.attisdropped "
            "UNION ALL "
            "SELECT 'routine', namespace.nspname, routine.proname, "
            "pg_catalog.pg_get_function_identity_arguments(routine.oid), '', "
            "routine.proowner, COALESCE(routine.proacl, "
            "pg_catalog.acldefault('f', routine.proowner)) "
            "FROM pg_catalog.pg_proc AS routine "
            "JOIN pg_catalog.pg_namespace AS namespace "
            "ON namespace.oid = routine.pronamespace "
            "WHERE namespace.nspname = 'public' "
            "UNION ALL "
            "SELECT 'type', namespace.nspname, type_value.typname, '', '', "
            "type_value.typowner, COALESCE(type_value.typacl, "
            "pg_catalog.acldefault('T', type_value.typowner)) "
            "FROM pg_catalog.pg_type AS type_value "
            "JOIN pg_catalog.pg_namespace AS namespace "
            "ON namespace.oid = type_value.typnamespace "
            "WHERE namespace.nspname = 'public'), "
            "expanded AS ("
            "SELECT objects.*, privilege.grantor, privilege.grantee, "
            "privilege.privilege_type, privilege.is_grantable "
            "FROM objects LEFT JOIN LATERAL "
            "pg_catalog.aclexplode(objects.acl) AS privilege ON TRUE) "
            "SELECT expanded.object_kind, expanded.schema, expanded.name, "
            "expanded.identity_arguments, expanded.subobject, "
            "CASE WHEN expanded.owner_oid = database_owner.oid "
            "THEN 'database_owner' "
            "WHEN pg_catalog.pg_get_userbyid(expanded.owner_oid) = "
            "'pg_database_owner' THEN 'pg_database_owner' "
            "ELSE 'role:' || pg_catalog.pg_get_userbyid(expanded.owner_oid) END, "
            "CASE WHEN expanded.grantor IS NULL THEN '' "
            "WHEN expanded.grantor = database_owner.oid THEN 'database_owner' "
            "WHEN pg_catalog.pg_get_userbyid(expanded.grantor) = "
            "'pg_database_owner' THEN 'pg_database_owner' "
            "ELSE 'role:' || pg_catalog.pg_get_userbyid(expanded.grantor) END, "
            "CASE WHEN expanded.grantee IS NULL THEN '' "
            "WHEN expanded.grantee = 0 THEN 'PUBLIC' "
            "WHEN expanded.grantee = database_owner.oid THEN 'database_owner' "
            "WHEN pg_catalog.pg_get_userbyid(expanded.grantee) = "
            "'pg_database_owner' THEN 'pg_database_owner' "
            "ELSE 'role:' || pg_catalog.pg_get_userbyid(expanded.grantee) END, "
            "COALESCE(expanded.privilege_type, ''), "
            "COALESCE(expanded.is_grantable, FALSE) "
            "FROM expanded CROSS JOIN database_owner "
            "ORDER BY expanded.object_kind, expanded.schema, expanded.name, "
            "expanded.identity_arguments, expanded.subobject, 7, 8, 9, 10",
            (
                "object_kind",
                "schema",
                "name",
                "identity_arguments",
                "subobject",
                "owner",
                "grantor",
                "grantee",
                "privilege",
                "grantable",
            ),
        )
        sections["default_privileges"] = self._descriptor_records(
            "WITH database_owner AS ("
            "SELECT datdba AS oid FROM pg_catalog.pg_database "
            "WHERE datname = pg_catalog.current_database()) "
            "SELECT CASE WHEN defaults.defaclrole = database_owner.oid "
            "THEN 'database_owner' "
            "ELSE 'role:' || pg_catalog.pg_get_userbyid(defaults.defaclrole) END, "
            "COALESCE(namespace.nspname, ''), defaults.defaclobjtype, "
            "CASE WHEN privilege.grantor = database_owner.oid "
            "THEN 'database_owner' "
            "ELSE 'role:' || pg_catalog.pg_get_userbyid(privilege.grantor) END, "
            "CASE WHEN privilege.grantee = 0 THEN 'PUBLIC' "
            "WHEN privilege.grantee = database_owner.oid THEN 'database_owner' "
            "ELSE 'role:' || pg_catalog.pg_get_userbyid(privilege.grantee) END, "
            "privilege.privilege_type, privilege.is_grantable "
            "FROM pg_catalog.pg_default_acl AS defaults "
            "CROSS JOIN database_owner "
            "LEFT JOIN pg_catalog.pg_namespace AS namespace "
            "ON namespace.oid = defaults.defaclnamespace "
            "CROSS JOIN LATERAL pg_catalog.aclexplode(defaults.defaclacl) "
            "AS privilege "
            "WHERE defaults.defaclrole = database_owner.oid "
            "AND (defaults.defaclnamespace = 0 OR namespace.nspname = 'public') "
            "ORDER BY 1, 2, 3, 4, 5, 6, 7",
            (
                "owner",
                "schema",
                "object_kind",
                "grantor",
                "grantee",
                "privilege",
                "grantable",
            ),
        )
        return _schema_descriptor(sections)

    def alembic_revisions(self) -> Sequence[str]:
        return [
            row[0]
            for row in self._connection.execute(
                "SELECT version_num FROM public.alembic_version ORDER BY version_num"
            ).fetchall()
        ]

    def table_count(self, table: str) -> int:
        row = self._connection.execute(
            f"SELECT count(*) FROM public.{_quoted(table)}"
        ).fetchone()
        return int(row[0])

    def table_state(self, table: str, identity_columns: Sequence[str]) -> StateDigest:
        table_sql = _quoted(table)
        if identity_columns:
            identity_sql = ", ".join(
                f"source.{_quoted(column)}" for column in identity_columns
            )
            order_sql = ", ".join(
                f"source.{_quoted(column)}::text" for column in identity_columns
            )
            identity_expression = f"jsonb_build_array({identity_sql})::text"
        else:
            order_sql = "source.id::text"
            identity_expression = "NULL::text"
        cursor = self._connection.execute(
            f"SELECT {identity_expression}, row_to_json(source)::text "
            f"FROM public.{table_sql} AS source ORDER BY {order_sql}"
        )
        state_mac = hmac.new(
            self._key,
            HMAC_DOMAIN
            + f"{self._policy_id}:database-table:{table}:state".encode()
            + b"\x00",
            hashlib.sha256,
        )
        identity_mac = hmac.new(
            self._key,
            HMAC_DOMAIN
            + f"{self._policy_id}:database-table:{table}:identities".encode()
            + b"\x00",
            hashlib.sha256,
        )
        count = 0
        for identity, state in cursor:
            count += 1
            _update_mac(state_mac, state)
            if identity is not None:
                _update_mac(identity_mac, identity)
        return StateDigest(
            count=count,
            state_hmac=state_mac.hexdigest(),
            identity_hmac=identity_mac.hexdigest() if identity_columns else None,
        )

    def violation_count(self, table: str, rule: Mapping[str, Any]) -> int:
        table_sql = _quoted(table)
        kind = rule["kind"]
        parameters: list[Any] = []
        if kind == "required_null":
            predicate = " OR ".join(
                f"source.{_quoted(column)} IS NOT NULL" for column in rule["columns"]
            )
        elif kind == "required_values":
            clauses: list[str] = []
            for column, values in rule["values"].items():
                placeholders = ", ".join(["%s"] * len(values))
                clauses.append(
                    f"source.{_quoted(column)} IS NULL OR "
                    f"source.{_quoted(column)} NOT IN ({placeholders})"
                )
                parameters.extend(values)
            predicate = " OR ".join(clauses)
        elif kind == "references":
            target = _quoted(rule["target_table"])
            pairs = zip(rule["columns"], rule["target_columns"], strict=True)
            match = " AND ".join(
                f"target.{_quoted(target_column)} IS NOT DISTINCT FROM "
                f"source.{_quoted(source_column)}"
                for source_column, target_column in pairs
            )
            predicate = (
                f"NOT EXISTS (SELECT 1 FROM public.{target} AS target WHERE {match})"
            )
        elif kind == "covered_by_any":
            exists: list[str] = []
            for coverage in rule["coverages"]:
                target = _quoted(coverage["table"])
                pairs = zip(
                    coverage["columns"],
                    coverage["target_columns"],
                    strict=True,
                )
                match = " AND ".join(
                    f"target.{_quoted(target_column)} IS NOT DISTINCT FROM "
                    f"source.{_quoted(source_column)}"
                    for source_column, target_column in pairs
                )
                exists.append(
                    f"EXISTS (SELECT 1 FROM public.{target} AS target WHERE {match})"
                )
            predicate = "NOT (" + " OR ".join(exists) + ")"
        else:
            raise InventoryFailure("policy.invalid_rule")
        row = self._connection.execute(
            f"SELECT count(*) FROM public.{table_sql} AS source WHERE {predicate}",
            parameters,
        ).fetchone()
        return int(row[0])

    def blob_container_names(self) -> Sequence[str]:
        try:
            return [
                item["name"]
                for item in self._blob.list_containers(
                    include_deleted=True,
                    include_system=True,
                )
            ]
        except Exception:
            raise InventoryFailure("blob.enumeration_failed") from None

    def blob_state(self, container: str, *, include_content: bool) -> StateDigest:
        try:
            records: list[bytes] = []
            checked_block_blobs: set[str] = set()
            client = self._blob.get_container_client(container)
            for blob in client.list_blobs(
                include=[
                    "deleted",
                    "deletedwithversions",
                    "metadata",
                    "snapshots",
                    "tags",
                    "uncommittedblobs",
                    "versions",
                ]
            ):
                snapshot = getattr(blob, "snapshot", None)
                version_id = getattr(blob, "version_id", None)
                blob_client = client.get_blob_client(
                    blob.name,
                    snapshot=snapshot,
                    version_id=version_id,
                )
                properties = blob_client.get_blob_properties()
                blob_type = getattr(properties, "blob_type", "")
                is_live = (
                    snapshot is None
                    and not bool(getattr(blob, "deleted", False))
                    and (
                        version_id is None
                        or getattr(blob, "is_current_version", False) is True
                    )
                )
                if (
                    getattr(blob_type, "value", blob_type) == "BlockBlob"
                    and blob.name not in checked_block_blobs
                    and is_live
                ):
                    checked_block_blobs.add(blob.name)
                    _, uncommitted = client.get_blob_client(blob.name).get_block_list(
                        "uncommitted"
                    )
                    if uncommitted:
                        raise InventoryFailure("blob.uncommitted_blocks")
                content_sha256 = hashlib.sha256()
                if include_content:
                    for chunk in blob_client.download_blob().chunks():
                        content_sha256.update(chunk)
                tags = blob_client.get_blob_tags()
                content_settings = getattr(properties, "content_settings", None)
                content_md5 = getattr(
                    content_settings,
                    "content_md5",
                    None,
                )
                last_modified = getattr(properties, "last_modified", None)
                record = {
                    "container": container,
                    "name": blob.name,
                    "snapshot": snapshot,
                    "version_id": version_id,
                    "deleted": bool(getattr(blob, "deleted", False)),
                    "size": getattr(properties, "size", None),
                    "etag": str(getattr(properties, "etag", "")),
                    "last_modified": (
                        last_modified.isoformat() if last_modified else None
                    ),
                    "blob_type": str(blob_type),
                    "content_type": getattr(content_settings, "content_type", None),
                    "content_md5": content_md5.hex() if content_md5 else None,
                    "content_sha256": (
                        content_sha256.hexdigest() if include_content else None
                    ),
                    "metadata": dict(getattr(properties, "metadata", {}) or {}),
                    "tags": dict(tags or {}),
                }
                records.append(_canonical_json(record))
            records.sort()
            return StateDigest(
                count=len(records),
                state_hmac=_digest(
                    self._key,
                    f"{self._policy_id}:blob-container:{container}:state",
                    records,
                ),
            )
        except InventoryFailure:
            raise
        except Exception:
            raise InventoryFailure("blob.enumeration_failed") from None

    def redis_surface(self) -> RedisSurface:
        try:
            keyspace = self._redis.info(section="keyspace")
            if not isinstance(keyspace, Mapping):
                raise InventoryFailure("redis.enumeration_failed")
            logical_databases = 0
            key_count = 0
            for database, state in keyspace.items():
                if (
                    not isinstance(database, str)
                    or re.fullmatch(r"db[0-9]+", database) is None
                    or not isinstance(state, Mapping)
                ):
                    raise InventoryFailure("redis.enumeration_failed")
                keys = state.get("keys")
                if isinstance(keys, bool) or not isinstance(keys, int) or keys < 0:
                    raise InventoryFailure("redis.enumeration_failed")
                if keys:
                    logical_databases += 1
                    key_count += keys
            return RedisSurface(logical_databases, key_count)
        except InventoryFailure:
            raise
        except Exception:
            raise InventoryFailure("redis.enumeration_failed") from None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--approved-state", type=Path, required=True)
    parser.add_argument("--profile", choices=("pre", "post"), required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--hmac-key-file", type=Path, required=True)
    parser.add_argument("--blob-account-url", required=True)
    parser.add_argument("--database-url-env", default="STAGING_DATABASE_URL")
    parser.add_argument("--redis-url-env", default="STAGING_REDIS_URL")
    parser.add_argument("--redis-quiesced", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    provider: LiveInventoryProvider | None = None
    try:
        policy, policy_raw = _load_json(args.policy, "policy.unreadable")
        validate_policy(policy)
        policy_sha256 = hashlib.sha256(policy_raw).hexdigest()
        approved, _ = _load_json(args.approved_state, "approved_state.unreadable")
        key = read_hmac_key(args.hmac_key_file)
        database_url = os.environ.get(args.database_url_env, "")
        redis_url = os.environ.get(args.redis_url_env, "")
        if not database_url or not redis_url:
            raise InventoryFailure("target.environment_missing")
        validate_database_url(database_url, policy["database"])
        validate_blob_url(args.blob_account_url, policy["blob"])
        validate_redis_url(redis_url, policy["redis"])
        provider = LiveInventoryProvider(
            database_url=database_url,
            blob_account_url=args.blob_account_url,
            redis_url=redis_url,
            key=key,
            policy_id=policy["policy_id"],
        )
        evidence = verify_inventory(
            policy=policy,
            policy_sha256=policy_sha256,
            approved=approved,
            profile_name=args.profile,
            source_sha=args.source_sha,
            candidate_sha=args.candidate_sha,
            key=key,
            provider=provider,
            redis_quiesced=args.redis_quiesced,
        )
        print(json.dumps(evidence, sort_keys=True, separators=(",", ":")))
        return 0 if evidence["pass"] else 1
    except InventoryFailure as exc:
        print(f"staging synthetic inventory failed: {exc}", file=sys.stderr)
        return 2
    except Exception:
        print("staging synthetic inventory failed: internal_error", file=sys.stderr)
        return 2
    finally:
        if provider is not None:
            try:
                provider.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
