#!/usr/bin/env python3
"""Emit a deterministic, aggregate-only snapshot for the P0 staging rollout.

The snapshot is deliberately generated outside the application package so
importing this module cannot initialize the application or run migrations. Raw
rows are read only inside one repeatable-read transaction, reduced to counts
and keyed digests in memory, and never written to stdout or stderr.

Use the same high-entropy key file for the before and after snapshots. The key
file must be an owner-owned, non-symlink regular file with mode exactly 0600.
The database URL must use ``sslmode=verify-full`` and name an explicit,
non-symlink PEM trust-root file with ``sslrootcert``.
``--phase pre`` requires all P0 tables to be absent. The exceptional
``--phase pre-present-empty`` mode requires all of them to be present and empty,
for a migration that has already rolled forward without any P0 data writes.
``--phase post`` requires all of them to be present. Redirect stdout to an
owner-only evidence file rather than displaying or teeing the small-cohort
aggregate counts.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import ipaddress
import json
import os
import stat
import sys
import urllib.parse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, LiteralString, Protocol

import psycopg
from typeid import TypeID
from typeid.errors import TypeIDException
from uuid6 import UUID

CONNECT_TIMEOUT_SECONDS = 10
STATEMENT_TIMEOUT_MILLISECONDS = 30_000
MINIMUM_HMAC_KEY_BYTES = 32
MAXIMUM_HMAC_KEY_BYTES = 4096
MAXIMUM_CA_ROOT_BYTES = 8 * 1024 * 1024
SNAPSHOT_SCHEMA_VERSION = 2
SNAPSHOT_ENVIRONMENT = "staging"
EXPECTED_DATABASE_HOST = "cosmaneura-staging-postgres.postgres.database.azure.com"
EXPECTED_DATABASE_NAME = "staging"
EXPECTED_DATABASE_PORT = 5432
EXPECTED_SSLMODE = "verify-full"
HMAC_DOMAIN = b"cosmaneura:p0-staging-evidence:v1\x00"

EvidencePhase = Literal["pre", "pre-present-empty", "post"]


class EvidenceFailure(RuntimeError):
    """A failure whose details must not be emitted by the command."""


class QueryResult(Protocol):
    def fetchall(self) -> Sequence[tuple[Any, ...]]: ...


class ReadOnlyConnection(Protocol):
    def execute(self, query: LiteralString) -> QueryResult: ...


@dataclass(frozen=True, slots=True)
class TableSpec:
    name: str
    query: LiteralString


@dataclass(frozen=True, slots=True)
class SnapshotRows:
    tables: TableRows
    p0_schema_presence: dict[str, bool]


# Every query returns one JSON object per row. The command never returns these
# objects to its caller; it emits only counts and HMAC-SHA-256 digests. Doctor
# biography is excluded because it is free text and unnecessary for this
# authorization-state comparison. All identity and credential-like values in
# the remaining rows are protected by the operator-supplied HMAC key.
TABLE_SPECS: tuple[TableSpec, ...] = (
    TableSpec(
        "doctor",
        "SELECT to_jsonb(snapshot_row) FROM ("
        "SELECT id, clerk_id, email, role, last_active_at, api_key, "
        "has_natmed_subscription, terminology, onboarding_completed_at, "
        "deleted_at, created_at, updated_at FROM public.doctor"
        ") AS snapshot_row",
    ),
    TableSpec("baa_version", "SELECT to_jsonb(t) FROM public.baa_version AS t"),
    TableSpec(
        "baa_acceptance",
        "SELECT to_jsonb(t) FROM public.baa_acceptance AS t",
    ),
    TableSpec("practice", "SELECT to_jsonb(t) FROM public.practice AS t"),
    TableSpec(
        "practice_member",
        "SELECT to_jsonb(t) FROM public.practice_member AS t",
    ),
    TableSpec(
        "provider_organization",
        "SELECT to_jsonb(t) FROM public.provider_organization AS t",
    ),
    TableSpec(
        "organization_roster_approval",
        "SELECT to_jsonb(t) FROM public.organization_roster_approval AS t",
    ),
    TableSpec(
        "practice_access_audit",
        "SELECT to_jsonb(t) FROM public.practice_access_audit AS t",
    ),
    TableSpec(
        "doctor_identity_rebind_audit",
        "SELECT to_jsonb(t) FROM public.authority_audit_event AS t "
        "WHERE t.event_type = 'platform.doctor_identity.rebound'",
    ),
    TableSpec(
        "doctor_video_integration",
        "SELECT to_jsonb(t) FROM public.doctor_video_integration AS t",
    ),
    TableSpec(
        "authority_actor_external_identity",
        "SELECT to_jsonb(t) FROM public.authority_actor_external_identity AS t",
    ),
    TableSpec(
        "authority_person_binding",
        "SELECT to_jsonb(t) FROM public.authority_person_binding AS t",
    ),
    TableSpec(
        "authority_tenant_membership",
        "SELECT to_jsonb(t) FROM public.authority_tenant_membership AS t",
    ),
    TableSpec(
        "pilot_access_grandfather_grant",
        "SELECT to_jsonb(t) FROM public.pilot_access_grandfather_grant AS t",
    ),
    TableSpec(
        "pilot_access_continuity_amendment",
        "SELECT to_jsonb(t) FROM public.pilot_access_continuity_amendment AS t",
    ),
    TableSpec(
        "pilot_access_continuity_amendment_identity",
        "SELECT to_jsonb(t) "
        "FROM public.pilot_access_continuity_amendment_identity AS t",
    ),
    TableSpec(
        "superadmin_capability_grant",
        "SELECT to_jsonb(t) FROM public.superadmin_capability_grant AS t",
    ),
    TableSpec(
        "superadmin_capability_audit",
        "SELECT to_jsonb(t) FROM public.superadmin_capability_audit AS t",
    ),
    TableSpec(
        "view_as_session",
        "SELECT to_jsonb(t) FROM public.view_as_session AS t",
    ),
    TableSpec(
        "view_as_audit_event",
        "SELECT to_jsonb(t) FROM public.view_as_audit_event AS t",
    ),
)

TableRows = dict[str, list[dict[str, Any]]]

# These tables are created by the code under review. A useful pre-deploy
# snapshot must therefore tolerate their expected absence while still failing
# closed if any pre-existing business-state table is unavailable.
P0_SCHEMA_TABLES = frozenset(
    {
        "pilot_access_continuity_amendment",
        "pilot_access_continuity_amendment_identity",
        "superadmin_capability_grant",
        "superadmin_capability_audit",
        "view_as_session",
        "view_as_audit_event",
    }
)
P0_SCHEMA_PRESENCE_QUERY: LiteralString = (
    "SELECT expected.table_name, relation.relkind, "
    "CASE WHEN relation.oid IS NULL THEN NULL "
    "ELSE pg_catalog.has_table_privilege(relation.oid, 'SELECT') END "
    "FROM (VALUES "
    "('pilot_access_continuity_amendment'),"
    "('pilot_access_continuity_amendment_identity'),"
    "('superadmin_capability_grant'),"
    "('superadmin_capability_audit'),"
    "('view_as_session'),"
    "('view_as_audit_event')"
    ") AS expected(table_name) "
    "LEFT JOIN pg_catalog.pg_namespace AS namespace "
    "ON namespace.nspname = 'public' "
    "LEFT JOIN pg_catalog.pg_class AS relation "
    "ON relation.relnamespace = namespace.oid "
    "AND relation.relname = expected.table_name "
    "ORDER BY expected.table_name"
)
SERVER_IDENTITY_QUERY: LiteralString = (
    "SELECT current_database(), current_user, current_setting('server_version'), "
    "pg_catalog.inet_server_addr()::text, pg_catalog.inet_server_port(), "
    "COALESCE((SELECT ssl FROM pg_catalog.pg_stat_ssl "
    "WHERE pid = pg_catalog.pg_backend_pid()), FALSE)"
)


def _validate_trusted_ca_root(path: Path) -> None:
    """Require an explicit, stable, readable PEM trust-root file for libpq."""

    try:
        if not path.is_absolute() or any(
            ord(character) < 32 for character in str(path)
        ):
            raise EvidenceFailure("staging database trust root is invalid")
        resolved = path.resolve(strict=True)
        if resolved != path:
            raise EvidenceFailure("staging database trust root is unsafe")

        parent = path.parent.lstat()
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid not in {0, os.geteuid()}
            or parent.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            raise EvidenceFailure("staging database trust root is unsafe")

        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid not in {0, os.geteuid()}
            or before.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            or not 0 < before.st_size <= MAXIMUM_CA_ROOT_BYTES
        ):
            raise EvidenceFailure("staging database trust root is unsafe")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            after = os.fstat(descriptor)
            if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
                raise EvidenceFailure("staging database trust root changed")
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                contents = handle.read(MAXIMUM_CA_ROOT_BYTES + 1)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    except EvidenceFailure:
        raise
    except Exception:
        raise EvidenceFailure("staging database trust root could not be read") from None
    if (
        len(contents) > MAXIMUM_CA_ROOT_BYTES
        or b"-----BEGIN CERTIFICATE-----" not in contents
        or b"-----END CERTIFICATE-----" not in contents
    ):
        raise EvidenceFailure("staging database trust root is invalid")


def validate_staging_database_url(database_url: str) -> Path:
    """Require the one reviewed staging PostgreSQL endpoint and TLS policy."""

    try:
        parsed = urllib.parse.urlsplit(database_url)
        canonical_host = (parsed.hostname or "").rstrip(".").lower()
        port = parsed.port
        query = urllib.parse.parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
        )
    except (TypeError, ValueError):
        raise EvidenceFailure("staging database target is invalid") from None
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise EvidenceFailure("staging database target is invalid")
    if not parsed.username or not parsed.password or parsed.netloc.count("@") != 1:
        raise EvidenceFailure("staging database target is invalid")
    if len(query) != 2 or {key for key, _value in query} != {
        "sslmode",
        "sslrootcert",
    }:
        raise EvidenceFailure("staging database target is unexpected")
    query_values = dict(query)
    if (
        canonical_host != EXPECTED_DATABASE_HOST
        or port not in {None, EXPECTED_DATABASE_PORT}
        or parsed.path != f"/{EXPECTED_DATABASE_NAME}"
        or query_values["sslmode"] != EXPECTED_SSLMODE
        or parsed.fragment
    ):
        raise EvidenceFailure("staging database target is unexpected")
    ca_root = Path(query_values["sslrootcert"])
    _validate_trusted_ca_root(ca_root)
    return ca_root


def verify_staging_server_identity(connection: ReadOnlyConnection) -> None:
    """Verify the connected database and encrypted TCP server without emitting it."""

    returned = connection.execute(SERVER_IDENTITY_QUERY).fetchall()
    if len(returned) != 1 or len(returned[0]) != 6:
        raise EvidenceFailure("staging database identity is invalid")
    database, user, server_version, server_address, server_port, ssl_in_use = returned[
        0
    ]
    try:
        parsed_server_address = ipaddress.ip_interface(server_address)
        if (
            parsed_server_address.network.prefixlen
            != parsed_server_address.max_prefixlen
        ):
            raise ValueError
    except (TypeError, ValueError):
        raise EvidenceFailure("staging database identity is invalid") from None
    if (
        database != EXPECTED_DATABASE_NAME
        or not isinstance(user, str)
        or not user
        or not isinstance(server_version, str)
        or not server_version
        or server_port != EXPECTED_DATABASE_PORT
        or ssl_in_use is not True
    ):
        raise EvidenceFailure("staging database identity is unexpected")


def validate_capture_phase(
    p0_schema_presence: Mapping[str, bool],
    phase: EvidencePhase,
) -> None:
    """Require an unambiguous all-absent pre or all-present post schema."""

    if phase not in {"pre", "pre-present-empty", "post"}:
        raise EvidenceFailure("evidence capture phase is invalid")
    if set(p0_schema_presence) != P0_SCHEMA_TABLES or any(
        type(present) is not bool for present in p0_schema_presence.values()
    ):
        raise EvidenceFailure("database schema snapshot is incomplete")
    present_count = sum(p0_schema_presence.values())
    expected_count = 0 if phase == "pre" else len(P0_SCHEMA_TABLES)
    if present_count != expected_count:
        raise EvidenceFailure("database schema does not match the capture phase")


def validate_present_empty_pre_rows(
    rows: Mapping[str, Sequence[Mapping[str, Any]]],
    phase: EvidencePhase,
) -> None:
    """Require every P0 table to be empty for the rolled-forward pre phase."""

    if phase != "pre-present-empty":
        return
    if not P0_SCHEMA_TABLES.issubset(rows) or any(
        rows[table] for table in P0_SCHEMA_TABLES
    ):
        raise EvidenceFailure("pre-present-empty P0 state is not empty")


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise EvidenceFailure("database snapshot contained an invalid value") from None


def _hmac_sha256(key: bytes, *, purpose: str, value: bytes) -> str:
    message = HMAC_DOMAIN + purpose.encode("ascii") + b"\x00" + value
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def _table_digest(key: bytes, name: str, rows: Sequence[Mapping[str, Any]]) -> str:
    row_digests = sorted(
        _hmac_sha256(key, purpose=f"row:{name}", value=_canonical_json(row))
        for row in rows
    )
    return _hmac_sha256(
        key,
        purpose=f"table:{name}",
        value=_canonical_json(row_digests),
    )


def _projection_digest(
    key: bytes,
    name: str,
    rows: Sequence[Mapping[str, Any]],
    fields: Sequence[str],
) -> str:
    projected: list[dict[str, Any]] = []
    for row in rows:
        if any(field not in row for field in fields):
            raise EvidenceFailure("database snapshot columns are incomplete")
        projected.append({field: row[field] for field in fields})
    return _table_digest(key, name, projected)


def read_snapshot_rows(
    connection: ReadOnlyConnection,
    *,
    phase: EvidencePhase,
) -> SnapshotRows:
    """Read the fixed P0 evidence tables without returning raw values."""

    returned_presence = connection.execute(P0_SCHEMA_PRESENCE_QUERY).fetchall()
    schema_presence: dict[str, bool] = {}
    for returned in returned_presence:
        if len(returned) != 3:
            raise EvidenceFailure("database schema snapshot returned an invalid shape")
        table, relation_kind, can_select = returned
        if (
            not isinstance(table, str)
            or table not in P0_SCHEMA_TABLES
            or table in schema_presence
        ):
            raise EvidenceFailure("database schema snapshot returned an invalid shape")
        if relation_kind is None:
            if can_select is not None:
                raise EvidenceFailure(
                    "database schema snapshot returned an invalid shape"
                )
            schema_presence[table] = False
            continue
        if relation_kind not in {"r", "p"}:
            raise EvidenceFailure("database schema object has an unexpected type")
        if can_select is not True:
            raise EvidenceFailure("database schema object is not readable")
        schema_presence[table] = True
    if set(schema_presence) != P0_SCHEMA_TABLES:
        raise EvidenceFailure("database schema snapshot is incomplete")
    schema_presence = {
        table: schema_presence[table] for table in sorted(P0_SCHEMA_TABLES)
    }
    validate_capture_phase(schema_presence, phase)

    snapshots: TableRows = {}
    for spec in TABLE_SPECS:
        if spec.name in P0_SCHEMA_TABLES and not schema_presence[spec.name]:
            snapshots[spec.name] = []
            continue
        result = connection.execute(spec.query)
        rows: list[dict[str, Any]] = []
        for returned in result.fetchall():
            if len(returned) != 1 or not isinstance(returned[0], dict):
                raise EvidenceFailure("database snapshot returned an invalid shape")
            rows.append(returned[0])
        snapshots[spec.name] = rows
    validate_present_empty_pre_rows(snapshots, phase)
    return SnapshotRows(
        tables=snapshots,
        p0_schema_presence=schema_presence,
    )


def _count(rows: Sequence[Mapping[str, Any]], **matches: object) -> int:
    return sum(
        all(row.get(field) == expected for field, expected in matches.items())
        for row in rows
    )


def _not_null_count(rows: Sequence[Mapping[str, Any]], field: str) -> int:
    return sum(row.get(field) is not None for row in rows)


def _table_evidence(
    rows: TableRows,
    key: bytes,
    table: str,
) -> dict[str, object]:
    values = rows[table]
    return {
        "count": len(values),
        "state_hmac_sha256": _table_digest(key, table, values),
    }


def _continuity_grant_evidence(
    grants: Sequence[Mapping[str, Any]],
    key: bytes,
    *,
    digest_name: str,
) -> dict[str, object]:
    return {
        "count": len(grants),
        "state_hmac_sha256": _table_digest(key, digest_name, grants),
        "active_count": _count(grants, revoked_at=None),
        "doctor_snapshot_count": _not_null_count(grants, "doctor_id_snapshot"),
        "prelogin_snapshot_count": _count(grants, doctor_id_snapshot=None),
        "revoked_count": _not_null_count(grants, "revoked_at"),
    }


def _sha256_multiset_hmac(
    key: bytes,
    *,
    purpose: str,
    rows: Sequence[Mapping[str, Any]],
    field: str,
) -> str:
    values: list[str] = []
    for row in rows:
        value = row.get(field)
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise EvidenceFailure("reviewed plan evidence is invalid")
        values.append(value)
    return _hmac_sha256(
        key,
        purpose=purpose,
        value=_canonical_json(sorted(values)),
    )


def _plain_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _required_row_string(row: Mapping[str, Any], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value:
        raise EvidenceFailure("P0 row binding is invalid")
    return value


def _external_typeid(value: object, *, prefix: str) -> str:
    """Normalize PostgreSQL UUID JSON into the TypeID used by plan hashing."""

    if not isinstance(value, str) or not value:
        raise EvidenceFailure("P0 row binding is invalid")
    try:
        if value.startswith(f"{prefix}_"):
            parsed = TypeID.from_string(value)
            if parsed.prefix != prefix or str(parsed) != value:
                raise ValueError
        else:
            suffix = UUID(value)
            if str(suffix) != value:
                raise ValueError
            parsed = TypeID.from_uuid(suffix, prefix)
    except (TypeError, ValueError, TypeIDException):
        raise EvidenceFailure("P0 row binding is invalid") from None
    return str(parsed)


def _optional_external_typeid(value: object, *, prefix: str) -> str | None:
    if value is None:
        return None
    return _external_typeid(value, prefix=prefix)


def _normalized_email_sha256(value: object) -> str:
    if not isinstance(value, str):
        raise EvidenceFailure("superadmin identity binding is invalid")
    email = value.strip().lower()
    if (
        not email
        or len(email) > 320
        or email.count("@") != 1
        or not email.rsplit("@", 1)[0]
        or "." not in email.rsplit("@", 1)[1]
        or any(character.isspace() for character in email)
    ):
        raise EvidenceFailure("superadmin identity binding is invalid")
    return _plain_sha256(email)


def _validate_continuity_bindings(
    *,
    grants: Sequence[Mapping[str, Any]],
    amendments: Sequence[Mapping[str, Any]],
    identities: Sequence[Mapping[str, Any]],
) -> None:
    grant_by_id = {_required_row_string(row, "id"): row for row in grants}
    amendment_by_id = {_required_row_string(row, "id"): row for row in amendments}
    if len(grant_by_id) != len(grants) or len(amendment_by_id) != len(amendments):
        raise EvidenceFailure("P0 row binding is ambiguous")

    identities_by_amendment: dict[str, list[Mapping[str, Any]]] = {
        amendment_id: [] for amendment_id in amendment_by_id
    }
    for identity in identities:
        amendment_id = _required_row_string(identity, "amendment_id")
        grant_id = _required_row_string(identity, "grant_id")
        amendment = amendment_by_id.get(amendment_id)
        grant = grant_by_id.get(grant_id)
        if amendment is None or grant is None:
            raise EvidenceFailure("continuity grant linkage is invalid")
        identities_by_amendment[amendment_id].append(identity)
        email_sha256 = _required_row_string(identity, "email_sha256")
        if (
            identity.get("safe_prelogin") is not False
            or grant.get("clerk_id") != identity.get("clerk_subject")
            or grant.get("doctor_id_snapshot") != identity.get("doctor_id_snapshot")
            or grant.get("practice_id_snapshot") != identity.get("practice_id_snapshot")
            or grant.get("email_sha256") != email_sha256
            or _plain_sha256(_required_row_string(grant, "email_snapshot"))
            != email_sha256
            or grant.get("source_ref") != amendment.get("source_ref")
            or grant.get("source_sha256") != amendment.get("manifest_sha256")
            or grant.get("grant_plan_sha256") != amendment.get("plan_sha256")
            or grant.get("granted_by_ref") != amendment.get("applied_by_ref")
            or grant.get("grant_reason") != amendment.get("reason")
            or grant.get("granted_at") != amendment.get("applied_at")
        ):
            raise EvidenceFailure("continuity grant binding is invalid")

    for amendment_id, amendment in amendment_by_id.items():
        linked = identities_by_amendment[amendment_id]
        if amendment.get("account_count") != len(linked) or not linked:
            raise EvidenceFailure("continuity amendment count is invalid")
        clerk_issuer = _required_row_string(amendment, "clerk_issuer")
        identity_payloads = []
        for identity in sorted(
            linked,
            key=lambda row: (
                _required_row_string(row, "clerk_issuer"),
                _required_row_string(row, "clerk_subject"),
            ),
        ):
            issuer = _required_row_string(identity, "clerk_issuer")
            subject = _required_row_string(identity, "clerk_subject")
            if issuer != clerk_issuer:
                raise EvidenceFailure("continuity amendment issuer is invalid")
            identity_payloads.append(
                {
                    "continuity_basis": identity.get("continuity_basis"),
                    "doctor_id": _external_typeid(
                        identity.get("doctor_id_snapshot"),
                        prefix="doctor",
                    ),
                    "email_sha256": identity.get("email_sha256"),
                    "issuer_sha256": _plain_sha256(issuer),
                    "practice_id": _optional_external_typeid(
                        identity.get("practice_id_snapshot"),
                        prefix="practice",
                    ),
                    "subject_sha256": _plain_sha256(subject),
                }
            )
        reconstructed = _canonical_sha256(
            {
                "action": "pilot_access_continuity_amendment",
                "environment": amendment.get("environment"),
                "identities": identity_payloads,
                "manifest_sha256": amendment.get("manifest_sha256"),
                "operator_ref": amendment.get("applied_by_ref"),
                "reason": amendment.get("reason"),
                "schema_version": 1,
                "source_ref": amendment.get("source_ref"),
            }
        )
        if not hmac.compare_digest(
            reconstructed,
            _required_row_string(amendment, "plan_sha256"),
        ):
            raise EvidenceFailure("continuity amendment plan binding is invalid")


def _superadmin_identity_binding_sha256(grant: Mapping[str, Any]) -> str:
    return _canonical_sha256(
        {
            "capability_kind": grant.get("capability_kind"),
            "clerk_issuer_sha256": _plain_sha256(
                _required_row_string(grant, "clerk_issuer")
            ),
            "clerk_subject_sha256": _plain_sha256(
                _required_row_string(grant, "clerk_subject")
            ),
            "doctor_id_sha256": _plain_sha256(
                _external_typeid(grant.get("doctor_id"), prefix="doctor")
            ),
            "environment": grant.get("environment"),
        }
    )


def _validate_superadmin_bindings(
    *,
    doctors: Sequence[Mapping[str, Any]],
    grants: Sequence[Mapping[str, Any]],
    audits: Sequence[Mapping[str, Any]],
) -> None:
    doctor_by_id = {_required_row_string(row, "id"): row for row in doctors}
    grant_by_id = {_required_row_string(row, "id"): row for row in grants}
    if len(doctor_by_id) != len(doctors) or len(grant_by_id) != len(grants):
        raise EvidenceFailure("superadmin binding is ambiguous")

    grants_by_plan: dict[str, list[Mapping[str, Any]]] = {}
    for grant in grants:
        plan_sha256 = _required_row_string(grant, "grant_plan_sha256")
        doctor = doctor_by_id.get(_required_row_string(grant, "doctor_id"))
        issuer = _required_row_string(grant, "clerk_issuer")
        subject = _required_row_string(grant, "clerk_subject")
        if (
            doctor is None
            or doctor.get("clerk_id") != subject
            or _normalized_email_sha256(doctor.get("email"))
            != grant.get("verified_primary_email_sha256")
            or _plain_sha256(issuer) != grant.get("issuer_sha256")
            or _plain_sha256(subject) != grant.get("subject_sha256")
        ):
            raise EvidenceFailure("superadmin identity binding is invalid")
        grants_by_plan.setdefault(plan_sha256, []).append(grant)

    for plan_sha256, plan_grants in grants_by_plan.items():
        first = plan_grants[0]
        shared_fields = (
            "environment",
            "clerk_issuer",
            "source_ref",
            "source_sha256",
            "granted_by_ref",
            "grant_reason_code",
        )
        if any(
            any(grant.get(field) != first.get(field) for field in shared_fields)
            for grant in plan_grants
        ):
            raise EvidenceFailure("superadmin grant plan binding is invalid")
        targets = [
            {
                "binding_sha256": _superadmin_identity_binding_sha256(grant),
                "verified_primary_email_sha256": grant.get(
                    "verified_primary_email_sha256"
                ),
            }
            for grant in sorted(
                plan_grants,
                key=lambda row: (
                    _required_row_string(row, "clerk_subject"),
                    _required_row_string(row, "capability_kind"),
                ),
            )
        ]
        reconstructed = _canonical_sha256(
            {
                "action": "grant",
                "environment": first.get("environment"),
                "issuer_sha256": _plain_sha256(
                    _required_row_string(first, "clerk_issuer")
                ),
                "operator_ref": first.get("granted_by_ref"),
                "reason_code": first.get("grant_reason_code"),
                "schema_version": 1,
                "source_ref": first.get("source_ref"),
                "source_sha256": first.get("source_sha256"),
                "targets": targets,
            }
        )
        if not hmac.compare_digest(reconstructed, plan_sha256):
            raise EvidenceFailure("superadmin grant plan binding is invalid")

    audits_by_grant: dict[str, list[Mapping[str, Any]]] = {
        grant_id: [] for grant_id in grant_by_id
    }
    for audit in audits:
        grant_id = _required_row_string(audit, "grant_id")
        grant = grant_by_id.get(grant_id)
        if grant is None:
            raise EvidenceFailure("superadmin audit binding is invalid")
        audits_by_grant[grant_id].append(audit)
        action = audit.get("action")
        expected_evidence = (
            grant.get("grant_plan_sha256")
            if action == "granted"
            else grant.get("revocation_plan_sha256")
        )
        expected_operator = (
            grant.get("granted_by_ref")
            if action == "granted"
            else grant.get("revoked_by_ref")
        )
        expected_reason = (
            grant.get("grant_reason_code")
            if action == "granted"
            else grant.get("revocation_reason_code")
        )
        expected_occurred_at = (
            grant.get("granted_at") if action == "granted" else grant.get("revoked_at")
        )
        if (
            action not in {"granted", "revoked"}
            or audit.get("environment") != grant.get("environment")
            or audit.get("capability_kind") != grant.get("capability_kind")
            or audit.get("identity_binding_sha256")
            != _superadmin_identity_binding_sha256(grant)
            or audit.get("evidence_sha256") != expected_evidence
            or audit.get("operator_ref") != expected_operator
            or audit.get("reason_code") != expected_reason
            or audit.get("occurred_at") != expected_occurred_at
            or audit.get("recorded_at") != expected_occurred_at
        ):
            raise EvidenceFailure("superadmin audit binding is invalid")
    for grant_id, grant in grant_by_id.items():
        actions = [
            _required_row_string(audit, "action") for audit in audits_by_grant[grant_id]
        ]
        expected_actions = (
            ["granted", "revoked"]
            if grant.get("revoked_at") is not None
            else ["granted"]
        )
        if sorted(actions) != expected_actions:
            raise EvidenceFailure("superadmin audit history is incomplete")


def build_snapshot(
    rows: TableRows,
    key: bytes,
    *,
    phase: EvidencePhase,
    p0_schema_presence: Mapping[str, bool] | None = None,
) -> dict[str, object]:
    """Reduce raw rows to deterministic, non-identifying evidence."""

    expected_tables = {spec.name for spec in TABLE_SPECS}
    if set(rows) != expected_tables:
        raise EvidenceFailure("database snapshot tables are incomplete")
    validate_present_empty_pre_rows(rows, phase)
    if len(key) < MINIMUM_HMAC_KEY_BYTES:
        raise EvidenceFailure("evidence HMAC key is too short")
    if p0_schema_presence is None:
        normalized_schema_presence = {table: True for table in sorted(P0_SCHEMA_TABLES)}
    else:
        if set(p0_schema_presence) != P0_SCHEMA_TABLES or any(
            type(present) is not bool for present in p0_schema_presence.values()
        ):
            raise EvidenceFailure("database schema snapshot is incomplete")
        normalized_schema_presence = {
            table: p0_schema_presence[table] for table in sorted(P0_SCHEMA_TABLES)
        }
    validate_capture_phase(normalized_schema_presence, phase)

    doctors = rows["doctor"]
    practices = rows["practice"]
    members = rows["practice_member"]
    organizations = rows["provider_organization"]
    roster = rows["organization_roster_approval"]
    continuity_grants = rows["pilot_access_grandfather_grant"]
    amendments = rows["pilot_access_continuity_amendment"]
    amendment_identities = rows["pilot_access_continuity_amendment_identity"]
    capability_grants = rows["superadmin_capability_grant"]
    capability_audits = rows["superadmin_capability_audit"]
    view_as_sessions = rows["view_as_session"]
    view_as_audits = rows["view_as_audit_event"]

    active_capabilities = [
        row
        for row in capability_grants
        if row.get("environment") == SNAPSHOT_ENVIRONMENT
        and row.get("revoked_at") is None
    ]
    revoked_capabilities = [
        row
        for row in capability_grants
        if row.get("environment") == SNAPSHOT_ENVIRONMENT
        and row.get("revoked_at") is not None
    ]
    staging_sessions = [
        row
        for row in view_as_sessions
        if row.get("environment") == SNAPSHOT_ENVIRONMENT
    ]
    staging_view_as_audits = [
        row for row in view_as_audits if row.get("environment") == SNAPSHOT_ENVIRONMENT
    ]

    continuity_grant_ids = [row.get("id") for row in continuity_grants]
    amendment_grant_ids = [row.get("grant_id") for row in amendment_identities]
    if (
        any(grant_id is None for grant_id in continuity_grant_ids)
        or any(grant_id is None for grant_id in amendment_grant_ids)
        or len(set(continuity_grant_ids)) != len(continuity_grant_ids)
        or len(set(amendment_grant_ids)) != len(amendment_grant_ids)
        or not set(amendment_grant_ids).issubset(set(continuity_grant_ids))
    ):
        raise EvidenceFailure("continuity grant linkage is invalid")
    amendment_grant_id_set = set(amendment_grant_ids)
    base_continuity_grants = [
        row for row in continuity_grants if row["id"] not in amendment_grant_id_set
    ]
    amendment_continuity_grants = [
        row for row in continuity_grants if row["id"] in amendment_grant_id_set
    ]
    _validate_continuity_bindings(
        grants=continuity_grants,
        amendments=amendments,
        identities=amendment_identities,
    )
    _validate_superadmin_bindings(
        doctors=doctors,
        grants=[
            row
            for row in capability_grants
            if row.get("environment") == SNAPSHOT_ENVIRONMENT
        ],
        audits=[
            row
            for row in capability_audits
            if row.get("environment") == SNAPSHOT_ENVIRONMENT
        ],
    )

    sections: dict[str, object] = {
        "baa": {
            "acceptances": _table_evidence(
                rows,
                key,
                "baa_acceptance",
            ),
            "versions": _table_evidence(rows, key, "baa_version"),
        },
        "doctor_account_state": {
            **_table_evidence(rows, key, "doctor"),
            "active_count": _count(doctors, deleted_at=None),
            "deleted_count": _not_null_count(doctors, "deleted_at"),
            "legacy_admin_role_count": _count(doctors, role="admin"),
            "onboarding_state_hmac_sha256": _projection_digest(
                key,
                "doctor-onboarding-state",
                doctors,
                ("id", "role", "onboarding_completed_at", "deleted_at"),
            ),
            "identity_authority_state_hmac_sha256": _projection_digest(
                key,
                "doctor-identity-authority-state",
                doctors,
                (
                    "id",
                    "clerk_id",
                    "email",
                    "role",
                    "api_key",
                    "has_natmed_subscription",
                    "onboarding_completed_at",
                    "deleted_at",
                ),
            ),
            "onboarding_completed_count": _not_null_count(
                doctors,
                "onboarding_completed_at",
            ),
            "onboarding_pending_count": _count(
                doctors,
                onboarding_completed_at=None,
            ),
        },
        "practice_business_state": {
            "practices": {
                **_table_evidence(rows, key, "practice"),
                "authorization_billing_state_hmac_sha256": _projection_digest(
                    key,
                    "practice-authorization-billing-state",
                    practices,
                    (
                        "id",
                        "is_solo",
                        "join_code",
                        "email_domain",
                        "organization_id",
                        "baa_version",
                        "baa_accepted_at",
                        "baa_accepted_by_doctor_id",
                        "baa_signature_name",
                        "baa_signature_title",
                        "trial_ends_at",
                        "billing_exempt",
                        "billing_exempt_until",
                        "stripe_customer_id",
                        "stripe_subscription_id",
                        "subscription_status",
                        "stripe_last_event_created_at",
                        "stripe_last_event_id",
                        "stripe_state_version",
                        "stripe_checkout_attempt",
                        "stripe_checkout_state_version",
                        "stripe_checkout_session_id",
                        "stripe_checkout_session_url",
                        "stripe_checkout_session_expires_at",
                        "stripe_quantity_sync_required",
                        "stripe_quantity_sync_target",
                        "stripe_quantity_sync_version",
                        "stripe_quantity_sync_attempts",
                        "stripe_quantity_sync_error",
                        "stripe_quantity_synced_at",
                        "negotiated_baa_on_file",
                        "negotiated_baa_required",
                        "negotiated_baa_effective_date",
                        "negotiated_baa_reference",
                    ),
                ),
                "billing_exempt_count": _count(practices, billing_exempt=True),
                "clickwrap_baa_count": _not_null_count(
                    practices,
                    "baa_accepted_at",
                ),
                "negotiated_baa_on_file_count": _count(
                    practices,
                    negotiated_baa_on_file=True,
                ),
                "negotiated_baa_required_count": _count(
                    practices,
                    negotiated_baa_required=True,
                ),
                "organization_linked_count": _not_null_count(
                    practices,
                    "organization_id",
                ),
                "stripe_customer_count": _not_null_count(
                    practices,
                    "stripe_customer_id",
                ),
                "stripe_subscription_count": _not_null_count(
                    practices,
                    "stripe_subscription_id",
                ),
                "trial_count": _not_null_count(practices, "trial_ends_at"),
            },
            "memberships": {
                **_table_evidence(rows, key, "practice_member"),
                "active_count": _count(members, access_revoked_at=None),
                "attested_count": _not_null_count(members, "attested_at"),
                "authorization_state_hmac_sha256": _projection_digest(
                    key,
                    "practice-membership-authorization-state",
                    members,
                    (
                        "id",
                        "practice_id",
                        "doctor_id",
                        "role",
                        "attested_at",
                        "attestation_name",
                        "organization_roster_approval_id",
                        "organization_authorized_at",
                        "access_revoked_at",
                        "access_revoked_by_ref",
                        "access_revocation_reason",
                        "access_records_retain_until",
                    ),
                ),
                "organization_authorized_count": _not_null_count(
                    members,
                    "organization_authorized_at",
                ),
                "revoked_count": _not_null_count(members, "access_revoked_at"),
            },
        },
        "organization_access_state": {
            "organizations": {
                **_table_evidence(rows, key, "provider_organization"),
                "active_count": _count(organizations, status="active"),
                "suspended_count": _count(organizations, status="suspended"),
            },
            "roster": {
                **_table_evidence(rows, key, "organization_roster_approval"),
                "active_count": _count(roster, revoked_at=None),
                "claimed_count": _not_null_count(roster, "claimed_doctor_id"),
                "revoked_count": _not_null_count(roster, "revoked_at"),
            },
            "audit": _table_evidence(rows, key, "practice_access_audit"),
        },
        "identity_recovery": {
            "rebind_audit": _table_evidence(
                rows,
                key,
                "doctor_identity_rebind_audit",
            ),
            "authority_external_identities": _table_evidence(
                rows,
                key,
                "authority_actor_external_identity",
            ),
            "authority_person_bindings": _table_evidence(
                rows,
                key,
                "authority_person_binding",
            ),
            "authority_tenant_memberships": _table_evidence(
                rows,
                key,
                "authority_tenant_membership",
            ),
        },
        "provider_credentials": {
            "video_integrations": {
                **_table_evidence(
                    rows,
                    key,
                    "doctor_video_integration",
                ),
                "stable_binding_state_hmac_sha256": _projection_digest(
                    key,
                    "doctor-video-integration-stable-binding",
                    rows["doctor_video_integration"],
                    ("id", "doctor_id", "provider", "provider_user_id"),
                ),
            },
        },
        "continuity": {
            "grants": _continuity_grant_evidence(
                continuity_grants,
                key,
                digest_name="pilot_access_grandfather_grant",
            ),
            "base_grants": _continuity_grant_evidence(
                base_continuity_grants,
                key,
                digest_name="pilot-access-base-grants",
            ),
            "amendment_grants": _continuity_grant_evidence(
                amendment_continuity_grants,
                key,
                digest_name="pilot-access-amendment-grants",
            )
            | {
                "plan_multiset_hmac_sha256": _sha256_multiset_hmac(
                    key,
                    purpose="continuity-amendment-grant-plans",
                    rows=amendment_continuity_grants,
                    field="grant_plan_sha256",
                )
            },
            "amendments": {
                **_table_evidence(
                    rows,
                    key,
                    "pilot_access_continuity_amendment",
                ),
                "plan_multiset_hmac_sha256": _sha256_multiset_hmac(
                    key,
                    purpose="continuity-amendment-plans",
                    rows=amendments,
                    field="plan_sha256",
                ),
                "declared_account_count": sum(
                    int(row.get("account_count", 0)) for row in amendments
                ),
                "non_staging_count": sum(
                    row.get("environment") != SNAPSHOT_ENVIRONMENT for row in amendments
                ),
            },
            "amendment_identities": {
                **_table_evidence(
                    rows,
                    key,
                    "pilot_access_continuity_amendment_identity",
                ),
                "safe_prelogin_count": _count(
                    amendment_identities,
                    safe_prelogin=True,
                ),
            },
        },
        "superadmin_capabilities": {
            "grants": {
                **_table_evidence(rows, key, "superadmin_capability_grant"),
                "grant_plan_multiset_hmac_sha256": _sha256_multiset_hmac(
                    key,
                    purpose="superadmin-grant-plans",
                    rows=capability_grants,
                    field="grant_plan_sha256",
                ),
                "active_count": len(active_capabilities),
                "non_staging_count": sum(
                    row.get("environment") != SNAPSHOT_ENVIRONMENT
                    for row in capability_grants
                ),
                "platform_admin_active_count": _count(
                    active_capabilities,
                    capability_kind="platform_admin",
                ),
                "revoked_count": len(revoked_capabilities),
                "view_as_active_count": _count(
                    active_capabilities,
                    capability_kind="view_as",
                ),
            },
            "audit": {
                **_table_evidence(rows, key, "superadmin_capability_audit"),
                "evidence_multiset_hmac_sha256": _sha256_multiset_hmac(
                    key,
                    purpose="superadmin-audit-evidence",
                    rows=capability_audits,
                    field="evidence_sha256",
                ),
                "granted_count": _count(
                    capability_audits,
                    environment=SNAPSHOT_ENVIRONMENT,
                    action="granted",
                ),
                "non_staging_count": sum(
                    row.get("environment") != SNAPSHOT_ENVIRONMENT
                    for row in capability_audits
                ),
                "revoked_count": _count(
                    capability_audits,
                    environment=SNAPSHOT_ENVIRONMENT,
                    action="revoked",
                ),
            },
        },
        "view_as": {
            "sessions": {
                **_table_evidence(rows, key, "view_as_session"),
                "capability_revoked_count": _count(
                    staging_sessions,
                    end_reason="capability_revoked",
                ),
                "ended_count": _not_null_count(staging_sessions, "ended_at"),
                "non_staging_count": len(view_as_sessions) - len(staging_sessions),
                "unended_count": _count(staging_sessions, ended_at=None),
            },
            "audit": {
                **_table_evidence(rows, key, "view_as_audit_event"),
                "accepted_count": _count(staging_view_as_audits, outcome="accepted"),
                "began_count": _count(staging_view_as_audits, event_type="began"),
                "denied_count": _count(staging_view_as_audits, outcome="denied"),
                "ended_count": _count(staging_view_as_audits, event_type="ended"),
                "expired_count": _count(
                    staging_view_as_audits,
                    event_type="expired",
                ),
                "non_staging_count": len(view_as_audits) - len(staging_view_as_audits),
                "rejected_count": _count(
                    staging_view_as_audits,
                    event_type="rejected",
                ),
                "stale_cleared_count": _count(
                    staging_view_as_audits,
                    event_type="stale_cleared",
                ),
            },
        },
    }
    envelope: dict[str, object] = {
        "capture_phase": phase,
        "digest_scheme": "hmac-sha256/canonical-json/v1",
        "environment": SNAPSHOT_ENVIRONMENT,
        "key_fingerprint_hmac_sha256": _hmac_sha256(
            key,
            purpose="key-fingerprint",
            value=b"fixed-public-verifier",
        ),
        "p0_schema": {
            "expected_table_count": len(P0_SCHEMA_TABLES),
            "present_count": sum(normalized_schema_presence.values()),
            "presence": normalized_schema_presence,
        },
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "sections": sections,
    }
    envelope["snapshot_hmac_sha256"] = _hmac_sha256(
        key,
        purpose="snapshot",
        value=_canonical_json(envelope),
    )
    return envelope


def load_private_hmac_key(path: Path) -> bytes:
    """Read a stable snapshot key without following links or exposing it."""

    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_uid != os.geteuid()
        ):
            raise EvidenceFailure(
                "evidence key must be an owner-only mode-0600 regular file"
            )
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            after = os.fstat(descriptor)
            if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
                raise EvidenceFailure("evidence key changed while opening")
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                key = handle.read(MAXIMUM_HMAC_KEY_BYTES + 1)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    except EvidenceFailure:
        raise
    except Exception:
        raise EvidenceFailure("evidence key could not be read") from None
    if not MINIMUM_HMAC_KEY_BYTES <= len(key) <= MAXIMUM_HMAC_KEY_BYTES:
        raise EvidenceFailure("evidence key length is invalid")
    return key


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Emit aggregate-only P0 rollout evidence for staging.",
    )
    parser.add_argument(
        "--hmac-key-file",
        required=True,
        type=Path,
        help="Owner-only mode-0600 key file; the key is never printed.",
    )
    parser.add_argument(
        "--phase",
        choices=("pre", "pre-present-empty", "post"),
        required=True,
        help=(
            "Require an all-absent pre-deploy, all-present-but-empty "
            "rolled-forward pre-deploy, or all-present post-deploy P0 schema."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if os.environ.get("PYTHON_ENV") != SNAPSHOT_ENVIRONMENT:
        print(
            "P0 staging evidence collection refused outside staging.",
            file=sys.stderr,
        )
        return 2
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("P0 staging evidence collection failed.", file=sys.stderr)
        return 2

    try:
        validate_staging_database_url(database_url)
        key = load_private_hmac_key(args.hmac_key_file)
        with psycopg.connect(
            database_url,
            connect_timeout=CONNECT_TIMEOUT_SECONDS,
            application_name="cosmaneura_p0_staging_evidence",
            options=(
                "-c default_transaction_read_only=on "
                f"-c statement_timeout={STATEMENT_TIMEOUT_MILLISECONDS} "
                "-c timezone=UTC"
            ),
        ) as connection:
            connection.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
            )
            verify_staging_server_identity(connection)
            snapshot_rows = read_snapshot_rows(connection, phase=args.phase)
            snapshot = build_snapshot(
                snapshot_rows.tables,
                key,
                phase=args.phase,
                p0_schema_presence=snapshot_rows.p0_schema_presence,
            )
            connection.rollback()
    except Exception:
        # Driver errors can include a DSN, and validation errors can be caused
        # by private row contents. Operational output must remain sanitized.
        print("P0 staging evidence collection failed.", file=sys.stderr)
        return 2

    print(json.dumps(snapshot, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
