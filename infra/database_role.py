#!/usr/bin/env python3
"""Prepare a database-scoped application owner and login for production.

The command is intentionally dry-run by default.  It never accepts either a
database URL or password on the command line, so credentials do not appear in
shell history or process listings.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
from dataclasses import dataclass
from typing import Any, LiteralString, cast

import psycopg
from psycopg import sql

ROLE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,62}$")
SYSTEM_SCHEMAS = {"information_schema", "pg_catalog", "pg_toast"}
PRODUCTION_DATABASE_HOST = "cosmaneura-prod-postgres.postgres.database.azure.com"
PRODUCTION_DATABASE_NAME = "production"
PRODUCTION_SCHEMA_NAME = "public"
LINUX_CA_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"
APPLICATION_OWNER_ROLE = "cosmaneura_app_owner"
APPLICATION_LOGIN_ROLE = "cosmaneura_app"


class RolePreparationError(RuntimeError):
    """A sanitized, operator-actionable preparation failure."""


@dataclass(frozen=True)
class SchemaObject:
    kind: str
    name: str
    owner: str


@dataclass(frozen=True)
class SchemaRoutine:
    kind: str
    name: str
    identity_arguments: str
    owner: str


@dataclass(frozen=True)
class Inventory:
    database: str
    database_owner: str
    current_user: str
    schema: str
    schema_owner: str
    relations: tuple[SchemaObject, ...]
    types: tuple[SchemaObject, ...]
    routines: tuple[SchemaRoutine, ...]
    extension_relations: int
    extension_types: int
    extension_routines: int
    unsupported_routines: int
    owner_role_exists: bool
    login_role_exists: bool

    def summary(self) -> dict[str, Any]:
        relation_counts: dict[str, int] = {}
        for relation in self.relations:
            relation_counts[relation.kind] = relation_counts.get(relation.kind, 0) + 1
        return {
            "current_user": self.current_user,
            "database": self.database,
            "database_owner": self.database_owner,
            "extension_objects_left_unchanged": {
                "relations": self.extension_relations,
                "routines": self.extension_routines,
                "types": self.extension_types,
            },
            "unsupported_application_routines": self.unsupported_routines,
            "role_conflicts": {
                "login": self.login_role_exists,
                "owner": self.owner_role_exists,
            },
            "schema": self.schema,
            "schema_owner": self.schema_owner,
            "transfer": {
                "relation_kinds": relation_counts,
                "relations": len(self.relations),
                "routines": len(self.routines),
                "types": len(self.types),
            },
        }


RELATION_ALTER_KEYWORDS = {
    "foreign_table": sql.SQL("FOREIGN TABLE"),
    "materialized_view": sql.SQL("MATERIALIZED VIEW"),
    "partitioned_table": sql.SQL("TABLE"),
    "sequence": sql.SQL("SEQUENCE"),
    "table": sql.SQL("TABLE"),
    "view": sql.SQL("VIEW"),
}
ROUTINE_ALTER_KEYWORDS = {
    "function": sql.SQL("FUNCTION"),
    "procedure": sql.SQL("PROCEDURE"),
}


def validate_role_name(value: str) -> str:
    if not ROLE_NAME_RE.fullmatch(value):
        raise argparse.ArgumentTypeError("role names must match ^[a-z][a-z0-9_]{2,62}$")
    return value


def validate_schema_name(value: str) -> str:
    value = validate_role_name(value)
    if value in SYSTEM_SCHEMAS or value.startswith("pg_"):
        raise argparse.ArgumentTypeError("system schemas are not allowed")
    return value


def read_password(*, env_name: str, from_stdin: bool) -> str:
    env_value = os.environ.get(env_name)
    if env_value is not None and from_stdin:
        raise RolePreparationError(
            "provide the application password through either the selected "
            "environment variable or stdin, not both"
        )
    if env_value is not None:
        value = env_value
    elif from_stdin:
        value = sys.stdin.readline().removesuffix("\n")
    else:
        raise RolePreparationError(
            f"application password is missing; set {env_name} or use --password-stdin"
        )
    validate_application_password(value)
    return value


def validate_application_password(value: str) -> None:
    if len(value) < 32 or len(value) > 256:
        raise RolePreparationError(
            "application password must contain between 32 and 256 characters"
        )
    if any(ord(character) < 33 or ord(character) > 126 for character in value):
        raise RolePreparationError(
            "application password must use visible non-whitespace ASCII"
        )
    categories = (
        any(character.islower() for character in value),
        any(character.isupper() for character in value),
        any(character.isdigit() for character in value),
        any(not character.isalnum() for character in value),
    )
    if sum(categories) < 3:
        raise RolePreparationError(
            "application password must use at least three character classes"
        )


def validate_admin_database_url(database_url: str, *, expected_database: str) -> None:
    if expected_database != PRODUCTION_DATABASE_NAME:
        raise RolePreparationError("administrative database target is unexpected")
    try:
        parsed = urllib.parse.urlsplit(database_url)
    except ValueError as error:
        raise RolePreparationError("administrative database URL is invalid") from error
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise RolePreparationError("administrative database URL must use PostgreSQL")
    if not parsed.username or not parsed.password or parsed.netloc.count("@") != 1:
        raise RolePreparationError("administrative database identity is invalid")
    try:
        canonical_host = (parsed.hostname or "").rstrip(".").lower()
        port = parsed.port
    except ValueError as error:
        raise RolePreparationError("administrative database URL is invalid") from error
    if canonical_host != PRODUCTION_DATABASE_HOST:
        raise RolePreparationError("administrative database host is unexpected")
    if port not in {None, 5432} or parsed.path != f"/{expected_database}":
        raise RolePreparationError("administrative database target is unexpected")
    try:
        pairs = urllib.parse.parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
        )
    except ValueError as error:
        raise RolePreparationError(
            "administrative database URL options are invalid"
        ) from error
    query = dict(pairs)
    if (
        len(pairs) != len(query)
        or query
        != {
            "sslmode": "verify-full",
            "sslrootcert": LINUX_CA_BUNDLE,
        }
        or parsed.fragment
    ):
        raise RolePreparationError("administrative database URL options are unexpected")


def load_inventory(
    connection: psycopg.Connection[Any],
    *,
    schema: str,
    owner_role: str,
    login_role: str,
) -> Inventory:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT current_database(), current_user, owner.rolname
              FROM pg_database database
              JOIN pg_roles owner ON owner.oid = database.datdba
             WHERE database.datname = current_database()
            """
        )
        database_row = cursor.fetchone()
        if database_row is None:
            raise RolePreparationError("could not inspect the current database")
        database, current_user, database_owner = database_row

        cursor.execute(
            """
            SELECT owner.rolname
              FROM pg_namespace namespace
              JOIN pg_roles owner ON owner.oid = namespace.nspowner
             WHERE namespace.nspname = %s
            """,
            (schema,),
        )
        schema_row = cursor.fetchone()
        if schema_row is None:
            raise RolePreparationError("the selected application schema does not exist")
        schema_owner = schema_row[0]

        cursor.execute(
            """
            SELECT CASE object.relkind
                     WHEN 'r' THEN 'table'
                     WHEN 'p' THEN 'partitioned_table'
                     WHEN 'S' THEN 'sequence'
                     WHEN 'v' THEN 'view'
                     WHEN 'm' THEN 'materialized_view'
                     WHEN 'f' THEN 'foreign_table'
                   END AS kind,
                   object.relname,
                   owner.rolname,
                   EXISTS (
                     SELECT 1
                       FROM pg_depend dependency
                      WHERE dependency.classid = 'pg_class'::regclass
                        AND dependency.objid = object.oid
                        AND dependency.deptype = 'e'
                   ) AS extension_owned
              FROM pg_class object
              JOIN pg_namespace namespace ON namespace.oid = object.relnamespace
              JOIN pg_roles owner ON owner.oid = object.relowner
             WHERE namespace.nspname = %s
               AND object.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')
               AND NOT (
                 object.relkind = 'S'
                 AND EXISTS (
                   SELECT 1
                     FROM pg_depend ownership
                    WHERE ownership.classid = 'pg_class'::regclass
                      AND ownership.objid = object.oid
                      AND ownership.refclassid = 'pg_class'::regclass
                      AND ownership.deptype IN ('a', 'i')
                 )
               )
             ORDER BY kind, object.relname
            """,
            (schema,),
        )
        relation_rows = cursor.fetchall()

        cursor.execute(
            """
            SELECT CASE object.typtype
                     WHEN 'e' THEN 'enum'
                     WHEN 'd' THEN 'domain'
                   END AS kind,
                   object.typname,
                   owner.rolname,
                   EXISTS (
                     SELECT 1
                       FROM pg_depend dependency
                      WHERE dependency.classid = 'pg_type'::regclass
                        AND dependency.objid = object.oid
                        AND dependency.deptype = 'e'
                   ) AS extension_owned
              FROM pg_type object
              JOIN pg_namespace namespace ON namespace.oid = object.typnamespace
              JOIN pg_roles owner ON owner.oid = object.typowner
             WHERE namespace.nspname = %s
               AND object.typtype IN ('e', 'd')
               AND object.typrelid = 0
             ORDER BY kind, object.typname
            """,
            (schema,),
        )
        type_rows = cursor.fetchall()

        cursor.execute(
            """
            SELECT CASE object.prokind
                     WHEN 'p' THEN 'procedure'
                     WHEN 'f' THEN 'function'
                     WHEN 'w' THEN 'function'
                   END AS kind,
                   object.proname,
                   pg_get_function_identity_arguments(object.oid),
                   owner.rolname,
                   EXISTS (
                     SELECT 1
                       FROM pg_depend dependency
                      WHERE dependency.classid = 'pg_proc'::regclass
                        AND dependency.objid = object.oid
                        AND dependency.deptype = 'e'
                   ) AS extension_owned,
                   object.prokind
              FROM pg_proc object
              JOIN pg_namespace namespace ON namespace.oid = object.pronamespace
              JOIN pg_roles owner ON owner.oid = object.proowner
             WHERE namespace.nspname = %s
             ORDER BY kind, object.proname,
                      pg_get_function_identity_arguments(object.oid)
            """,
            (schema,),
        )
        routine_rows = cursor.fetchall()

        cursor.execute(
            "SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)",
            ([owner_role, login_role],),
        )
        existing_roles = {row[0] for row in cursor.fetchall()}

    relations = tuple(
        SchemaObject(kind=row[0], name=row[1], owner=row[2])
        for row in relation_rows
        if not row[3]
    )
    types = tuple(
        SchemaObject(kind=row[0], name=row[1], owner=row[2])
        for row in type_rows
        if not row[3]
    )
    routines = tuple(
        SchemaRoutine(
            kind=row[0],
            name=row[1],
            identity_arguments=row[2],
            owner=row[3],
        )
        for row in routine_rows
        if not row[4] and row[5] in {"f", "p", "w"}
    )
    return Inventory(
        database=database,
        database_owner=database_owner,
        current_user=current_user,
        schema=schema,
        schema_owner=schema_owner,
        relations=relations,
        types=types,
        routines=routines,
        extension_relations=sum(1 for row in relation_rows if row[3]),
        extension_types=sum(1 for row in type_rows if row[3]),
        extension_routines=sum(1 for row in routine_rows if row[4]),
        unsupported_routines=sum(
            1 for row in routine_rows if not row[4] and row[5] not in {"f", "p", "w"}
        ),
        owner_role_exists=owner_role in existing_roles,
        login_role_exists=login_role in existing_roles,
    )


def validate_inventory(
    inventory: Inventory,
    *,
    expected_database: str,
    owner_role: str,
    login_role: str,
    required_table: str,
    for_apply: bool,
) -> None:
    if inventory.database != expected_database:
        raise RolePreparationError("connected to an unexpected database")
    if inventory.database_owner in {owner_role, login_role}:
        raise RolePreparationError(
            "database ownership is already assigned to a target application role"
        )
    if inventory.current_user in {owner_role, login_role}:
        raise RolePreparationError("the administrative connection uses a target role")
    if not any(
        item.kind in {"table", "partitioned_table"} and item.name == required_table
        for item in inventory.relations
    ):
        raise RolePreparationError("required application table was not found")
    if inventory.unsupported_routines:
        raise RolePreparationError(
            "application schema contains routines that require separate ownership review"
        )
    if for_apply and (inventory.owner_role_exists or inventory.login_role_exists):
        raise RolePreparationError(
            "one or both target roles already exist; use verification instead of "
            "re-running role creation"
        )


def prepare_roles(
    connection: psycopg.Connection[Any],
    *,
    inventory: Inventory,
    owner_role: str,
    login_role: str,
    password: str,
) -> None:
    original_database_owner = inventory.database_owner
    # Derive the PostgreSQL SCRAM verifier client-side.  The plaintext is never
    # submitted as a SQL bind value, where log_statement=all would record it.
    password_verifier = connection.pgconn.encrypt_password(
        password.encode(),
        login_role.encode(),
        algorithm=b"scram-sha-256",
    ).decode()
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '5s'")
            cursor.execute("SET LOCAL statement_timeout = '60s'")
            cursor.execute("SET LOCAL password_encryption = 'scram-sha-256'")
            cursor.execute(
                sql.SQL(
                    "CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOINHERIT NOREPLICATION NOBYPASSRLS"
                ).format(sql.Identifier(owner_role))
            )
            cursor.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "INHERIT NOREPLICATION NOBYPASSRLS"
                ).format(sql.Identifier(login_role))
            )
            # PostgreSQL role DDL does not accept a bind parameter in the
            # PASSWORD clause.  COPY keeps even the verifier out of statement
            # bind logs; the helper reads it from a temporary table.  The
            # plaintext application password never crosses the SQL protocol.
            cursor.execute(
                """
                CREATE TEMP TABLE pg_temp.cosmaneura_role_secret (
                    verifier text NOT NULL
                ) ON COMMIT DROP
                """
            )
            with cursor.copy(
                "COPY pg_temp.cosmaneura_role_secret (verifier) FROM STDIN"
            ) as copy:
                copy.write_row((password_verifier,))
            cursor.execute(
                """
                CREATE FUNCTION pg_temp.cosmaneura_set_role_password(
                    target_role name
                ) RETURNS void
                  LANGUAGE plpgsql
                  SECURITY INVOKER
                AS $function$
                DECLARE
                    target_verifier text;
                BEGIN
                    SELECT verifier
                      INTO STRICT target_verifier
                      FROM pg_temp.cosmaneura_role_secret;
                    EXECUTE format(
                        'ALTER ROLE %I PASSWORD %L',
                        target_role,
                        target_verifier
                    );
                EXCEPTION WHEN OTHERS THEN
                    RAISE EXCEPTION USING
                        MESSAGE = 'role password assignment failed',
                        ERRCODE = 'P0001';
                END
                $function$
                """
            )
            cursor.execute(
                "SELECT pg_temp.cosmaneura_set_role_password(%s)",
                (login_role,),
            )
            cursor.execute("DROP FUNCTION pg_temp.cosmaneura_set_role_password(name)")
            cursor.execute("DROP TABLE pg_temp.cosmaneura_role_secret")
            # PostgreSQL 16 records the grantor for role membership.  The
            # database administrator retains ADMIN OPTION on the no-login
            # owner so the login membership has a stable grantor and can be
            # maintained after the administrator password is rotated.
            cursor.execute(
                sql.SQL("GRANT {} TO {} WITH ADMIN OPTION").format(
                    sql.Identifier(owner_role),
                    sql.Identifier(inventory.current_user),
                )
            )
            cursor.execute(
                sql.SQL("GRANT {} TO {}").format(
                    sql.Identifier(owner_role), sql.Identifier(login_role)
                )
            )
            for object_kind in ("TABLES", "SEQUENCES", "FUNCTIONS", "TYPES"):
                cursor.execute(
                    sql.SQL(
                        "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} "
                        "REVOKE ALL ON {} FROM PUBLIC"
                    ).format(
                        sql.Identifier(owner_role),
                        sql.Identifier(inventory.schema),
                        sql.SQL(object_kind),
                    )
                )
            cursor.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(inventory.database), sql.Identifier(login_role)
                )
            )
            cursor.execute(
                sql.SQL("REVOKE CREATE ON DATABASE {} FROM {}, {}").format(
                    sql.Identifier(inventory.database),
                    sql.Identifier(owner_role),
                    sql.Identifier(login_role),
                )
            )
            cursor.execute(
                sql.SQL("ALTER SCHEMA {} OWNER TO {}").format(
                    sql.Identifier(inventory.schema), sql.Identifier(owner_role)
                )
            )
            cursor.execute(
                sql.SQL("REVOKE CREATE ON SCHEMA {} FROM PUBLIC").format(
                    sql.Identifier(inventory.schema)
                )
            )

            for relation in inventory.relations:
                cursor.execute(
                    sql.SQL("ALTER {} {}.{} OWNER TO {}").format(
                        RELATION_ALTER_KEYWORDS[relation.kind],
                        sql.Identifier(inventory.schema),
                        sql.Identifier(relation.name),
                        sql.Identifier(owner_role),
                    )
                )

            for item in inventory.types:
                cursor.execute(
                    sql.SQL("ALTER TYPE {}.{} OWNER TO {}").format(
                        sql.Identifier(inventory.schema),
                        sql.Identifier(item.name),
                        sql.Identifier(owner_role),
                    )
                )

            for routine in inventory.routines:
                cursor.execute(
                    sql.SQL("ALTER {} {}.{}({}) OWNER TO {}").format(
                        ROUTINE_ALTER_KEYWORDS[routine.kind],
                        sql.Identifier(inventory.schema),
                        sql.Identifier(routine.name),
                        # PostgreSQL emits this fragment from its own catalog,
                        # including any required identifier quoting.
                        sql.SQL(cast(LiteralString, routine.identity_arguments)),
                        sql.Identifier(owner_role),
                    )
                )

            # PostgreSQL 16 applies this setting at login.  session_user remains
            # the auditable login while current_user becomes the no-login owner,
            # so future Alembic objects have stable ownership.
            cursor.execute(
                sql.SQL("ALTER ROLE {} IN DATABASE {} SET ROLE TO {}").format(
                    sql.Identifier(login_role),
                    sql.Identifier(inventory.database),
                    sql.Identifier(owner_role),
                )
            )
            cursor.execute(
                """
                SELECT owner.rolname
                  FROM pg_database database
                  JOIN pg_roles owner ON owner.oid = database.datdba
                 WHERE database.datname = current_database()
                """
            )
            database_owner_after = cursor.fetchone()
            if (
                database_owner_after is None
                or database_owner_after[0] != original_database_owner
            ):
                raise RolePreparationError("database ownership changed unexpectedly")


def verify_roles(
    connection: psycopg.Connection[Any],
    *,
    schema: str,
    owner_role: str,
    login_role: str,
    expected_database: str,
) -> dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolcanlogin,
                   rolreplication, rolbypassrls, rolinherit
              FROM pg_roles
             WHERE rolname = ANY(%s)
             ORDER BY rolname
            """,
            ([owner_role, login_role],),
        )
        roles = {row[0]: row[1:] for row in cursor.fetchall()}
        expected_owner = (False, False, False, False, False, False, False)
        expected_login = (False, False, False, True, False, False, True)
        if roles.get(owner_role) != expected_owner:
            raise RolePreparationError("owner role attributes are not least-privilege")
        if roles.get(login_role) != expected_login:
            raise RolePreparationError("login role attributes are not least-privilege")

        cursor.execute(
            """
            SELECT membership.admin_option,
                   membership.inherit_option,
                   membership.set_option,
                   grantor.rolname,
                   current_user
              FROM pg_auth_members membership
              JOIN pg_roles granted_role
                ON granted_role.oid = membership.roleid
              JOIN pg_roles member_role
                ON member_role.oid = membership.member
              JOIN pg_roles grantor ON grantor.oid = membership.grantor
             WHERE granted_role.rolname = %s
               AND member_role.rolname = %s
            """,
            (owner_role, login_role),
        )
        membership = cursor.fetchone()
        if (
            membership is None
            or membership[:3] != (False, True, True)
            or membership[3] != membership[4]
        ):
            raise RolePreparationError("login membership options are unexpected")

        cursor.execute(
            """
            SELECT membership.admin_option
              FROM pg_auth_members membership
              JOIN pg_roles granted_role
                ON granted_role.oid = membership.roleid
              JOIN pg_roles member_role
                ON member_role.oid = membership.member
             WHERE granted_role.rolname = %s
               AND member_role.rolname = current_user
            """,
            (owner_role,),
        )
        admin_membership = cursor.fetchone()
        if admin_membership is None or admin_membership[0] is not True:
            raise RolePreparationError(
                "administrator lacks ADMIN OPTION on the application owner"
            )

        cursor.execute(
            """
            SELECT setconfig
              FROM pg_db_role_setting setting
              JOIN pg_roles role ON role.oid = setting.setrole
              JOIN pg_database database ON database.oid = setting.setdatabase
             WHERE role.rolname = %s AND database.datname = %s
            """,
            (login_role, expected_database),
        )
        setting = cursor.fetchone()
        if setting is None or f"role={owner_role}" not in setting[0]:
            raise RolePreparationError("login does not default to the owner role")

        cursor.execute(
            """
            SELECT schema_owner.rolname,
                   count(*) FILTER (WHERE object_owner.rolname <> %s)
              FROM pg_namespace namespace
              JOIN pg_roles schema_owner ON schema_owner.oid = namespace.nspowner
              LEFT JOIN pg_class object ON object.relnamespace = namespace.oid
                   AND object.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')
              LEFT JOIN pg_roles object_owner ON object_owner.oid = object.relowner
             WHERE namespace.nspname = %s
               AND NOT EXISTS (
                 SELECT 1
                   FROM pg_depend dependency
                  WHERE dependency.classid = 'pg_class'::regclass
                    AND dependency.objid = object.oid
                    AND dependency.deptype = 'e'
               )
             GROUP BY schema_owner.rolname
            """,
            (owner_role, schema),
        )
        ownership = cursor.fetchone()
        if ownership is None or ownership[0] != owner_role or ownership[1] != 0:
            raise RolePreparationError("application schema ownership is incomplete")

        cursor.execute(
            """
            SELECT count(*)
              FROM pg_type object
              JOIN pg_namespace namespace ON namespace.oid = object.typnamespace
              JOIN pg_roles owner ON owner.oid = object.typowner
             WHERE namespace.nspname = %s
               AND object.typtype IN ('e', 'd')
               AND object.typrelid = 0
               AND owner.rolname <> %s
               AND NOT EXISTS (
                 SELECT 1
                   FROM pg_depend dependency
                  WHERE dependency.classid = 'pg_type'::regclass
                    AND dependency.objid = object.oid
                    AND dependency.deptype = 'e'
               )
            """,
            (schema, owner_role),
        )
        type_ownership = cursor.fetchone()
        if type_ownership is None or type_ownership[0] != 0:
            raise RolePreparationError("application type ownership is incomplete")

        cursor.execute(
            """
            SELECT count(*)
              FROM pg_proc object
              JOIN pg_namespace namespace ON namespace.oid = object.pronamespace
              JOIN pg_roles owner ON owner.oid = object.proowner
             WHERE namespace.nspname = %s
               AND owner.rolname <> %s
               AND NOT EXISTS (
                 SELECT 1
                   FROM pg_depend dependency
                  WHERE dependency.classid = 'pg_proc'::regclass
                    AND dependency.objid = object.oid
                    AND dependency.deptype = 'e'
               )
            """,
            (schema, owner_role),
        )
        routine_ownership = cursor.fetchone()
        if routine_ownership is None or routine_ownership[0] != 0:
            raise RolePreparationError("application routine ownership is incomplete")

        cursor.execute(
            "SELECT has_schema_privilege('public', %s, 'CREATE')",
            (schema,),
        )
        public_create = cursor.fetchone()
        if public_create is None or public_create[0] is not False:
            raise RolePreparationError(
                "PUBLIC retains CREATE on the application schema"
            )

    return {
        "database": expected_database,
        "login_role": login_role,
        "owner_role": owner_role,
        "result": "verified",
        "schema": schema,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply",
        action="store_true",
        help="transactionally create roles and transfer schema ownership",
    )
    mode.add_argument(
        "--verify",
        action="store_true",
        help="verify an already-prepared administrative connection",
    )
    parser.add_argument(
        "--schema",
        default=PRODUCTION_SCHEMA_NAME,
        type=validate_schema_name,
        choices=(PRODUCTION_SCHEMA_NAME,),
    )
    parser.add_argument(
        "--owner-role",
        default=APPLICATION_OWNER_ROLE,
        type=validate_role_name,
        choices=(APPLICATION_OWNER_ROLE,),
    )
    parser.add_argument(
        "--login-role",
        default=APPLICATION_LOGIN_ROLE,
        type=validate_role_name,
        choices=(APPLICATION_LOGIN_ROLE,),
    )
    parser.add_argument(
        "--expected-database",
        default=PRODUCTION_DATABASE_NAME,
        choices=(PRODUCTION_DATABASE_NAME,),
    )
    parser.add_argument("--required-table", default="alembic_version")
    parser.add_argument("--admin-url-env", default="DATABASE_URL")
    parser.add_argument("--password-env", default="COSMANEURA_DB_APP_PASSWORD")
    parser.add_argument("--password-stdin", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.owner_role == args.login_role:
        print(json.dumps({"code": "role_names_not_distinct", "result": "failed"}))
        return 2
    admin_url = os.environ.get(args.admin_url_env)
    if not admin_url:
        print(json.dumps({"code": "admin_url_missing", "result": "failed"}))
        return 2

    try:
        validate_admin_database_url(admin_url, expected_database=args.expected_database)
        with psycopg.connect(admin_url) as connection:
            if args.verify:
                result = verify_roles(
                    connection,
                    schema=args.schema,
                    owner_role=args.owner_role,
                    login_role=args.login_role,
                    expected_database=args.expected_database,
                )
                print(json.dumps(result, sort_keys=True))
                return 0

            inventory = load_inventory(
                connection,
                schema=args.schema,
                owner_role=args.owner_role,
                login_role=args.login_role,
            )
            validate_inventory(
                inventory,
                expected_database=args.expected_database,
                owner_role=args.owner_role,
                login_role=args.login_role,
                required_table=args.required_table,
                for_apply=args.apply,
            )
            if not args.apply:
                print(
                    json.dumps(
                        {
                            "mode": "dry-run",
                            "result": "planned",
                            **inventory.summary(),
                        },
                        sort_keys=True,
                    )
                )
                return 0

            password = read_password(
                env_name=args.password_env, from_stdin=args.password_stdin
            )
            prepare_roles(
                connection,
                inventory=inventory,
                owner_role=args.owner_role,
                login_role=args.login_role,
                password=password,
            )
            result = verify_roles(
                connection,
                schema=args.schema,
                owner_role=args.owner_role,
                login_role=args.login_role,
                expected_database=args.expected_database,
            )
            print(json.dumps({"mode": "apply", **result}, sort_keys=True))
            return 0
    except (psycopg.Error, RolePreparationError):
        # psycopg diagnostics can include connection details or SQL values.
        # Keep terminal output deliberately coarse and inspect server-side logs
        # when deeper diagnosis is required.
        print(json.dumps({"code": "preparation_failed", "result": "failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
