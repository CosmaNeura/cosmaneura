#!/usr/bin/env python3
"""Value-free production readiness check for migration 91d2c4e5f6a7."""

from __future__ import annotations

import json
import os
import sys
from typing import Any, LiteralString

import psycopg
from psycopg import sql

if __package__:
    from .database_role import (
        PRODUCTION_DATABASE_NAME,
        RolePreparationError,
        validate_admin_database_url,
    )
else:  # Support the documented ``python infra/migration_..._preflight.py`` form.
    from database_role import (  # type: ignore[import-not-found]
        PRODUCTION_DATABASE_NAME,
        RolePreparationError,
        validate_admin_database_url,
    )

EXPECTED_CURRENT_REVISION = "7c1d5e9a2b40"
TARGET_REVISION = "91d2c4e5f6a7"
NEW_TABLES = (
    "ehr_sandbox_patient_designation",
    "ehr_sandbox_record_mapping",
)
NEW_OBJECT_NAMES = (
    "uq_patient_id_doctor_id_ehr_sandbox",
    "ehr_sandbox_patient_designation",
    "ehr_sandbox_record_mapping",
    "uq_ehr_sandbox_designation_active_patient",
    "uq_ehr_sandbox_mapping_active_patient",
    "uq_ehr_sandbox_mapping_record_history",
    "ehr_sandbox_record_mapping_patient_id_idx",
)


class MigrationPreflightError(RuntimeError):
    """A sanitized migration preflight failure."""


def _single(
    connection: psycopg.Connection[Any],
    statement: LiteralString,
    parameters: tuple[Any, ...] = (),
) -> Any:
    row = connection.execute(statement, parameters).fetchone()
    if row is None:
        raise MigrationPreflightError("preflight query returned no row")
    return row[0]


def inspect(connection: psycopg.Connection[Any]) -> dict[str, Any]:
    identity = connection.execute(
        "SELECT current_database(), current_user, current_setting('server_version')"
    ).fetchone()
    if identity is None or identity[0] != PRODUCTION_DATABASE_NAME:
        raise MigrationPreflightError("connected to an unexpected database")
    revision_rows = connection.execute(
        "SELECT version_num FROM alembic_version ORDER BY version_num"
    ).fetchall()
    revisions = [row[0] for row in revision_rows]

    table_presence = {
        table: bool(
            _single(
                connection,
                "SELECT to_regclass(%s) IS NOT NULL",
                (f"public.{table}",),
            )
        )
        for table in NEW_TABLES
    }
    new_table_rows: dict[str, int | None] = {}
    for table, present in table_presence.items():
        row = (
            connection.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
            ).fetchone()
            if present
            else None
        )
        if present and row is None:
            raise MigrationPreflightError("new table row count is unavailable")
        new_table_rows[table] = row[0] if row is not None else None

    object_conflicts = _single(
        connection,
        """
        SELECT count(*)
          FROM (
                SELECT relname AS name
                  FROM pg_class
                 WHERE relnamespace = 'public'::regnamespace
                UNION ALL
                SELECT conname AS name
                  FROM pg_constraint
                 WHERE connamespace = 'public'::regnamespace
               ) AS objects
         WHERE name = ANY(%s)
        """,
        (list(NEW_OBJECT_NAMES),),
    )
    duplicate_patient_scope_groups = _single(
        connection,
        """
        SELECT count(*)
          FROM (
                SELECT 1
                  FROM patient
                 GROUP BY id, doctor_id
                HAVING count(*) > 1
               ) AS duplicate_groups
        """,
    )
    patient_null_doctor_rows = _single(
        connection, "SELECT count(*) FROM patient WHERE doctor_id IS NULL"
    )
    patient_stats = connection.execute(
        """
        SELECT greatest(reltuples, 0)::bigint,
               pg_relation_size(oid),
               pg_total_relation_size(oid)
          FROM pg_class
         WHERE oid = 'public.patient'::regclass
        """
    ).fetchone()
    if patient_stats is None:
        raise MigrationPreflightError("patient table statistics are unavailable")
    locks = connection.execute(
        """
        SELECT mode, granted, count(*)
          FROM pg_locks
         WHERE relation = 'public.patient'::regclass
         GROUP BY mode, granted
         ORDER BY mode, granted
        """
    ).fetchall()
    long_transactions = _single(
        connection,
        """
        SELECT count(*)
          FROM pg_stat_activity
         WHERE pid <> pg_backend_pid()
           AND xact_start < clock_timestamp() - interval '5 minutes'
        """,
    )
    expected_current = revisions == [EXPECTED_CURRENT_REVISION]
    target_applied = TARGET_REVISION in revisions
    new_tables_absent = not any(table_presence.values())
    return {
        "alembic": {
            "current_revisions": revisions,
            "expected_current_revision": EXPECTED_CURRENT_REVISION,
            "target_revision": TARGET_REVISION,
            "target_already_applied": target_applied,
        },
        "database": identity[0],
        "new_object_conflict_count": object_conflicts,
        "new_table_presence": table_presence,
        "new_table_row_counts": new_table_rows,
        "patient": {
            "duplicate_id_doctor_group_count": duplicate_patient_scope_groups,
            "estimated_rows": patient_stats[0],
            "null_doctor_row_count": patient_null_doctor_rows,
            "relation_bytes": patient_stats[1],
            "total_relation_bytes": patient_stats[2],
        },
        "readiness": {
            "expected_current_revision": expected_current,
            "new_objects_absent": object_conflicts == 0 and new_tables_absent,
            "patient_scope_unique": duplicate_patient_scope_groups == 0,
            "ready": (
                expected_current
                and not target_applied
                and object_conflicts == 0
                and new_tables_absent
                and duplicate_patient_scope_groups == 0
            ),
        },
        "runtime": {
            "long_transaction_count": long_transactions,
            "patient_locks": [
                {"count": row[2], "granted": row[1], "mode": row[0]} for row in locks
            ],
            "required_lock": "ACCESS EXCLUSIVE on patient while adding the unique constraint",
        },
        "server_version": identity[2],
    }


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print(json.dumps({"code": "database_url_missing", "result": "failed"}))
        return 2
    try:
        validate_admin_database_url(
            database_url, expected_database=PRODUCTION_DATABASE_NAME
        )
        with psycopg.connect(database_url, connect_timeout=15) as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            connection.execute("SET LOCAL statement_timeout = '60s'")
            result = inspect(connection)
            connection.rollback()
    except (RolePreparationError, MigrationPreflightError, psycopg.Error):
        print(json.dumps({"code": "migration_preflight_failed", "result": "failed"}))
        return 1
    if result["readiness"]["ready"] is not True:
        print(
            json.dumps(
                {"code": "migration_not_ready", "result": "blocked", **result},
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps({"result": "ok", **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
