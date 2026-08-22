from __future__ import annotations

import io
import os
import uuid

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from infra import database_role


def test_password_requires_one_non_cli_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CUTOVER_PASSWORD", "not-a-real-secret")
    monkeypatch.setattr(database_role.sys, "stdin", io.StringIO("stdin-value\n"))

    with pytest.raises(database_role.RolePreparationError, match="not both"):
        database_role.read_password(env_name="CUTOVER_PASSWORD", from_stdin=True)


def test_password_rejects_multiline_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUTOVER_PASSWORD", "first\nsecond")

    with pytest.raises(database_role.RolePreparationError, match="password"):
        database_role.read_password(env_name="CUTOVER_PASSWORD", from_stdin=False)


def test_password_rejects_weak_or_unbounded_values() -> None:
    with pytest.raises(database_role.RolePreparationError, match="32 and 256"):
        database_role.validate_application_password("Aa1!too-short")
    with pytest.raises(database_role.RolePreparationError, match="character classes"):
        database_role.validate_application_password("a" * 40)


def test_admin_url_requires_exact_production_target() -> None:
    database_role.validate_admin_database_url(
        "postgresql://admin:opaque@"
        "cosmaneura-prod-postgres.postgres.database.azure.com/"
        "production?sslmode=require",
        expected_database="production",
    )
    with pytest.raises(database_role.RolePreparationError, match="host"):
        database_role.validate_admin_database_url(
            "postgresql://admin:opaque@"
            "different.postgres.database.azure.com/production?sslmode=require",
            expected_database="production",
        )
    with pytest.raises(database_role.RolePreparationError, match="invalid"):
        database_role.validate_admin_database_url(
            "postgresql://admin:opaque@"
            "cosmaneura-prod-postgres.postgres.database.azure.com:not-a-port/"
            "production?sslmode=require",
            expected_database="production",
        )


def test_target_names_are_restricted() -> None:
    assert database_role.validate_role_name("cosmaneura_app") == "cosmaneura_app"
    with pytest.raises(Exception, match="role names"):
        database_role.validate_role_name("admin; DROP ROLE admin")
    with pytest.raises(Exception, match="system schemas"):
        database_role.validate_schema_name("pg_catalog")


@pytest.mark.skipif(
    "COSMANEURA_DB_ROLE_INTEGRATION_ADMIN_URL" not in os.environ,
    reason="requires an explicitly selected disposable PostgreSQL database",
)
def test_fresh_login_can_run_alembic_style_ddl_without_client_set_role() -> None:
    """Prove startup Alembic/runtime access with a fresh login.

    This test is opt-in because PostgreSQL roles are cluster-global.  It uses
    unique names and cleans them up, but must only be pointed at a disposable
    database.
    """

    admin_url = os.environ["COSMANEURA_DB_ROLE_INTEGRATION_ADMIN_URL"]
    suffix = uuid.uuid4().hex[:10]
    schema = f"cutover_{suffix}"
    owner_role = f"cutover_owner_{suffix}"
    login_role = f"cutover_login_{suffix}"
    password = f"integration-{uuid.uuid4().hex}"

    with psycopg.connect(admin_url, autocommit=True) as admin:
        database = admin.execute("SELECT current_database()").fetchone()
        assert database is not None
        expected_database = database[0]
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        admin.execute(
            sql.SQL("CREATE TABLE {}.alembic_version (version_num text)").format(
                sql.Identifier(schema)
            )
        )
        admin.execute(
            sql.SQL(
                "CREATE FUNCTION {}.cutover_identity(value text) RETURNS text "
                "LANGUAGE sql IMMUTABLE AS 'SELECT value'"
            ).format(sql.Identifier(schema))
        )

    try:
        with psycopg.connect(admin_url) as admin:
            inventory = database_role.load_inventory(
                admin,
                schema=schema,
                owner_role=owner_role,
                login_role=login_role,
            )
            database_role.validate_inventory(
                inventory,
                expected_database=expected_database,
                owner_role=owner_role,
                login_role=login_role,
                required_table="alembic_version",
                for_apply=True,
            )
            database_role.prepare_roles(
                admin,
                inventory=inventory,
                owner_role=owner_role,
                login_role=login_role,
                password=password,
            )
            assert (
                database_role.verify_roles(
                    admin,
                    schema=schema,
                    owner_role=owner_role,
                    login_role=login_role,
                    expected_database=expected_database,
                )["result"]
                == "verified"
            )
            assert admin.execute(
                """
                SELECT owner.rolname
                  FROM pg_proc routine
                  JOIN pg_namespace namespace ON namespace.oid = routine.pronamespace
                  JOIN pg_roles owner ON owner.oid = routine.proowner
                 WHERE namespace.nspname = %s
                   AND routine.proname = 'cutover_identity'
                """,
                (schema,),
            ).fetchone() == (owner_role,)

        login_url = make_conninfo(admin_url, user=login_role, password=password)
        with psycopg.connect(login_url) as application:
            assert application.execute(
                "SELECT session_user, current_user"
            ).fetchone() == (login_role, owner_role)
            application.execute(
                sql.SQL("CREATE TABLE {}.migration_probe (id bigint)").format(
                    sql.Identifier(schema)
                )
            )
            application.execute(
                sql.SQL(
                    "CREATE OR REPLACE FUNCTION {}.cutover_identity(value text) "
                    "RETURNS text LANGUAGE sql IMMUTABLE AS 'SELECT value || ''-ok'''"
                ).format(sql.Identifier(schema))
            )
            assert application.execute(
                sql.SQL("SELECT {}.cutover_identity('routine')").format(
                    sql.Identifier(schema)
                )
            ).fetchone() == ("routine-ok",)
            application.execute(
                sql.SQL(
                    "ALTER TABLE {}.migration_probe ADD COLUMN payload text"
                ).format(sql.Identifier(schema))
            )
            application.execute(
                sql.SQL(
                    "INSERT INTO {}.migration_probe VALUES (1, 'runtime-ok')"
                ).format(sql.Identifier(schema))
            )
            assert application.execute(
                sql.SQL("SELECT payload FROM {}.migration_probe").format(
                    sql.Identifier(schema)
                )
            ).fetchone() == ("runtime-ok",)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                application.execute(
                    sql.SQL("CREATE SCHEMA {}").format(
                        sql.Identifier(f"forbidden_{suffix}")
                    )
                )
            application.rollback()
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(schema)
                )
            )
            roles = {
                row[0]
                for row in admin.execute(
                    "SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)",
                    ([login_role, owner_role],),
                ).fetchall()
            }
            if login_role in roles:
                admin.execute(
                    sql.SQL("DROP OWNED BY {} CASCADE").format(
                        sql.Identifier(login_role)
                    )
                )
            if owner_role in roles:
                admin.execute(
                    sql.SQL("DROP OWNED BY {} CASCADE").format(
                        sql.Identifier(owner_role)
                    )
                )
                admin.execute(
                    sql.SQL("REVOKE {} FROM current_user CASCADE").format(
                        sql.Identifier(owner_role)
                    )
                )
            admin.execute(
                sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(login_role))
            )
            admin.execute(
                sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(owner_role))
            )
